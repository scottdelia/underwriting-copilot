"""Classify an incoming query and assemble the evidence needed to answer it.

WHY A ROUTER
------------
A build chart question and a policy question are different retrieval problems.
"What is the weight limit at 5'10\" for Standard Plus?" has an exact answer in
one row of one table; vector search over prose returns the chunk that talks
about build limits, which is not the same thing and is confidently wrong in a
way nothing downstream can detect. "What is the cigar exception?" has no row at
all and must come from prose.

So the router picks a strategy rather than running one pipeline over everything.
It is kept deliberately small: one model call that classifies and parses in a
single pass, then plain code that gathers evidence. Anything more elaborate
would be harder to explain than it is worth.

EVIDENCE ISOLATION
------------------
Evidence is gathered per carrier, and each carrier's synthesis sees only its own
guide. This is structural rather than instructed: there is no point in the
pipeline where one carrier's text is in scope for another carrier's verdict, so
no prompt has to ask the model not to mix them up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.ingest.build_index import CARRIER_NAMES
from app.models.profile import ProspectProfile, QueryPlan, resolve_build
from app.models.schemas import ConditionRule, SearchHit
from app.retrieval.semantic import get_index
from app.retrieval.structured import (
    BmiBuildVerdict,
    BuildVerdict,
    lookup_build_class,
    lookup_build_class_by_bmi,
    lookup_build_row,
    lookup_condition_rules,
    lookup_threshold_tables,
)
from app.security.sanitize import fence_retrieved_content
from app.synthesis.prompts import QUERY_PLAN_SYSTEM

logger = logging.getLogger(__name__)


async def plan_query(client: Any, settings: Settings, query: str) -> QueryPlan:
    """Classify a query and extract any prospect it describes.

    Args:
        client: An `anthropic.AsyncAnthropic` instance.
        settings: Application settings.
        query: The sanitized query text.

    Returns:
        The routing plan, with derivable build fields filled in.

    Raises:
        RuntimeError: If the model returns no structured output.
    """
    response = await client.messages.parse(
        model=settings.synthesis_model,
        max_tokens=4000,
        system=QUERY_PLAN_SYSTEM,
        output_config={"effort": settings.synthesis_effort},
        messages=[{"role": "user", "content": query}],
        output_format=QueryPlan,
    )
    plan: QueryPlan | None = response.parsed_output
    if plan is None:
        raise RuntimeError(
            f"query planning returned no structured output "
            f"(stop_reason={response.stop_reason})"
        )

    resolved = plan.model_copy(update={"profile": resolve_build(plan.profile)})
    logger.info(
        "routed as %s: %s", resolved.query_type, resolved.reasoning
    )
    return resolved


@dataclass
class CarrierEvidence:
    """Everything one carrier's guide offers about one prospect.

    `blocks` is what reaches the prompt; `sources` is the same content
    unfenced, used to verify that a quoted excerpt was actually supplied rather
    than composed.
    """

    carrier_id: str
    carrier_name: str
    build: BuildVerdict | None = None
    bmi_build: BmiBuildVerdict | None = None
    condition_rules: list[ConditionRule] = field(default_factory=list)
    threshold_tables: list[dict] = field(default_factory=list)
    prose: list[SearchHit] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when nothing was found, so synthesis has nothing to reason over."""
        return not (
            self.build
            or self.bmi_build
            or self.condition_rules
            or self.threshold_tables
            or self.prose
        )

    def evidence_text(self) -> str:
        """The fenced evidence block for the prompt."""
        return "\n\n".join(self.blocks)

    def source_text(self) -> str:
        """All supplied content, unfenced, for verifying quoted excerpts."""
        return "\n".join(self.sources)


def _profile_summary(profile: ProspectProfile) -> str:
    """Restate the parsed prospect in plain language for the prompt.

    Only stated facts appear. Anything the agent did not say is listed as not
    stated, so the model can see the gap and say what is missing rather than
    quietly assuming a value.
    """
    parts: list[str] = []
    if profile.age is not None:
        parts.append(f"age {profile.age}")
    if profile.gender:
        parts.append(profile.gender)
    if profile.height_inches:
        feet, inches = divmod(profile.height_inches, 12)
        parts.append(f"height {feet}'{inches}\"")
    if profile.weight_lbs:
        parts.append(f"weight {profile.weight_lbs} lb")
    if profile.bmi:
        parts.append(f"BMI {profile.bmi}")
    if profile.a1c is not None:
        parts.append(f"most recent A1c {profile.a1c}")
    if profile.conditions:
        parts.append("conditions: " + ", ".join(profile.conditions))
    if profile.medications:
        parts.append("medications: " + ", ".join(profile.medications))
    if profile.tobacco is True:
        parts.append("tobacco user")
    elif profile.tobacco is False:
        parts.append("non-tobacco")
    if profile.coverage_amount_usd:
        parts.append(f"coverage ${profile.coverage_amount_usd:,}")
    if profile.product_type:
        parts.append(profile.product_type)

    stated = "; ".join(parts) if parts else "no details stated"

    not_stated = [
        name
        for name, value in [
            ("height", profile.height_inches),
            ("weight", profile.weight_lbs),
            ("age", profile.age),
            ("sex", profile.gender),
            ("tobacco use", profile.tobacco),
        ]
        if value is None
    ]
    if not_stated:
        stated += f"\nNot stated by the agent: {', '.join(not_stated)}."
    return stated


def gather_carrier_evidence(
    settings: Settings,
    carrier_id: str,
    profile: ProspectProfile,
    query: str,
    top_k: int = 4,
) -> CarrierEvidence:
    """Collect every piece of evidence one carrier's guide offers.

    Structured lookups run first because they are exact. Semantic search runs
    afterwards to supply the surrounding prose a verdict needs to be explained,
    not to supply the numbers.

    Args:
        settings: Application settings.
        carrier_id: The carrier to gather for.
        profile: The parsed prospect.
        query: The original query, used as the semantic search text.
        top_k: How many prose chunks to retrieve.

    Returns:
        The assembled evidence, with fenced blocks ready for the prompt.
    """
    db = settings.sqlite_path
    evidence = CarrierEvidence(
        carrier_id=carrier_id,
        carrier_name=CARRIER_NAMES.get(carrier_id, carrier_id),
    )
    gender = profile.gender or "any"

    def add(block: str) -> None:
        """Fence a block for the prompt and keep the raw text for verification."""
        evidence.blocks.append(fence_retrieved_content(block))
        evidence.sources.append(block)

    # --- Build limits -----------------------------------------------------
    # A height and weight give an exact row. A BMI alone gives only a ceiling
    # implied by the chart, which is a weaker answer and is labelled as one.
    if profile.height_inches and profile.weight_lbs:
        verdict = lookup_build_class(
            db, carrier_id, profile.height_inches, profile.weight_lbs, gender
        )
        evidence.build = verdict
        row = lookup_build_row(db, carrier_id, profile.height_inches, gender)
        lines = [
            "[BUILD CHART LOOKUP - computed from the published chart]",
            verdict.explanation,
        ]
        if row:
            lines.append(
                f"Full published row at this height (page {row[0].page}):"
            )
            lines += [
                f"  {e.rate_class}: maximum {e.max_weight_lbs} lb" for e in row
            ]
        add("\n".join(lines))
    elif profile.bmi:
        bmi_verdict = lookup_build_class_by_bmi(db, carrier_id, profile.bmi, gender)
        evidence.bmi_build = bmi_verdict
        add(
            "[BUILD CHART LOOKUP - derived, no height was stated]\n"
            + bmi_verdict.derivation
            + (
                f"\nBest class the build allows: {bmi_verdict.carrier_label}"
                if bmi_verdict.qualifies
                else "\nThe applicant exceeds every published build limit."
            )
        )

    # --- Condition rules --------------------------------------------------
    conditions = [c for c in profile.conditions if c != "other"]
    rules = lookup_condition_rules(db, carrier_id, conditions)
    evidence.condition_rules = rules
    for rule in rules:
        add(
            f"[CONDITION RULE: {rule.condition} - "
            f"{rule.doc_id} page {rule.page}]\n"
            f"{rule.criteria}\n"
            f"Best available class stated: {rule.best_available_class}\n"
            f"Disqualifiers: {'; '.join(rule.disqualifiers) or 'none listed'}"
        )

    # --- Threshold tables -------------------------------------------------
    # Scoped to the pages the matched rules came from. A rule's headline class
    # is the general case; its table carries the refinement that actually
    # applies to this applicant.
    rule_pages = sorted({rule.page for rule in rules})
    tables = lookup_threshold_tables(db, carrier_id, rule_pages) if rule_pages else []
    evidence.threshold_tables = tables
    for table in tables:
        rendered = [
            f"[THRESHOLD TABLE: {table['title']} - "
            f"{table['doc_id']} page {table['page']}]",
            " | ".join(table["columns"]),
        ]
        rendered += [" | ".join(row) for row in table["rows"]]
        rendered += [f"Note: {note}" for note in table["footnotes"]]
        add("\n".join(rendered))

    # --- Prose ------------------------------------------------------------
    try:
        hits = get_index(settings).search(query, top_k=top_k, carrier_id=carrier_id)
    except Exception as exc:  # pragma: no cover - index optional at this layer
        logger.warning("semantic search unavailable for %s: %s", carrier_id, exc)
        hits = []
    evidence.prose = hits
    for hit in hits:
        add(
            f"[GUIDE PROSE: {hit.section} - {hit.doc_id} {hit.page_label}]\n"
            f"{hit.text}"
        )

    logger.info(
        "evidence for %s: build=%s rules=%d tables=%d prose=%d",
        carrier_id,
        bool(evidence.build or evidence.bmi_build),
        len(rules),
        len(tables),
        len(hits),
    )
    return evidence


def profile_summary(profile: ProspectProfile) -> str:
    """Public wrapper over the prompt-facing profile restatement."""
    return _profile_summary(profile)
