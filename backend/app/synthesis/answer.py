"""Per-carrier synthesis, with citation verification and enforced abstention.

TWO GUARANTEES, ONE STRUCTURAL AND ONE CHECKED
----------------------------------------------
The brief requires that every claim carries a citation and that a claim without
one is not rendered. That is implemented twice, on purpose.

Structurally: `Claim` in models/verdict.py is a statement *and* a citation.
There is no shape that can represent an uncited assertion, so one cannot survive
parsing.

By verification: a citation naming a page is not yet a correct citation. After
generation, every excerpt is checked against the evidence that was supplied to
that carrier's call. An excerpt that cannot be found is a fabricated citation,
and the claim carrying it is discarded and counted. The count is reported to the
caller rather than swallowed, because a non-zero value is a caught fabrication
and the reader should know it happened.

WHY ONE CALL PER CARRIER
------------------------
Four carriers means four parallel calls, each seeing only its own guide. A
single call over all four would be cheaper and would create the one failure this
tool cannot tolerate: a verdict for one carrier supported by another carrier's
text. Isolation by construction beats an instruction not to mix them up.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from app.config import Settings
from app.ingest.build_index import CARRIER_NAMES
from app.models.profile import ProspectProfile, QueryPlan
from app.models.verdict import (
    CarrierVerdict,
    Citation,
    Claim,
    ComparisonResponse,
    DirectAnswer,
)
from app.retrieval.router import (
    CarrierEvidence,
    gather_carrier_evidence,
    profile_summary,
)
from app.synthesis.prompts import SYNTHESIS_SYSTEM, synthesis_user_prompt

logger = logging.getLogger(__name__)

# How much of a quoted excerpt must match the supplied evidence. Not 1.0,
# because a model quoting across a line break may normalise a hyphen or a
# quotation mark and that is not fabrication. Well above chance, because the
# point is to catch invented text rather than to be lenient about it.
EXCERPT_MATCH_MIN_LENGTH = 12


def _normalize(text: str) -> str:
    """Collapse whitespace and unify quote characters for excerpt comparison."""
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_verdict(
    verdict: CarrierVerdict, evidence: CarrierEvidence
) -> tuple[CarrierVerdict, int]:
    """Drop any claim whose excerpt cannot be found in the supplied evidence.

    A claim is kept only when its quoted excerpt appears in the text that was
    actually given to the model for this carrier. Anything else was composed
    rather than copied, which is a fabricated citation regardless of whether the
    underlying statement happens to be true.

    If verification empties the evidence behind a classification, the verdict is
    downgraded to an abstention. A determination whose every support was
    discarded is not a determination.

    Args:
        verdict: The model's parsed verdict.
        evidence: The evidence supplied to that carrier's call.

    Returns:
        The verified verdict and the number of claims dropped.
    """
    source = _normalize(evidence.source_text())
    dropped = 0

    def keep(claims: list) -> list:
        nonlocal dropped
        kept = []
        for claim in claims:
            excerpt = _normalize(claim.citation.excerpt)
            # Too short to verify meaningfully; a three-word quote matches
            # almost anything, so it is not accepted as support.
            if len(excerpt) < EXCERPT_MATCH_MIN_LENGTH:
                dropped += 1
                logger.warning(
                    "%s: dropped claim with unverifiably short excerpt: %r",
                    evidence.carrier_id,
                    claim.citation.excerpt[:60],
                )
                continue
            if excerpt not in source:
                dropped += 1
                logger.warning(
                    "%s: dropped claim citing p%d, excerpt not in supplied "
                    "evidence: %r",
                    evidence.carrier_id,
                    claim.citation.page,
                    claim.citation.excerpt[:80],
                )
                continue
            kept.append(claim)
        return kept

    qualifying = keep(verdict.qualifying)
    disqualifying = keep(verdict.disqualifying)

    updates: dict[str, Any] = {
        "qualifying": qualifying,
        "disqualifying": disqualifying,
    }

    if (
        verdict.determination == "classified"
        and not qualifying
        and not disqualifying
    ):
        updates.update(
            {
                "determination": "insufficient_information",
                "carrier_label": None,
                "canonical_class": None,
                "abstention_reason": (
                    "Every claim supporting this determination cited text that "
                    "could not be found in the guide, so the determination was "
                    "withdrawn."
                ),
            }
        )
        logger.warning(
            "%s: downgraded to abstention after citation verification",
            evidence.carrier_id,
        )

    return verdict.model_copy(update=updates), dropped


def _empty_evidence_verdict(evidence: CarrierEvidence) -> CarrierVerdict:
    """Abstain without calling the model when there is nothing to reason over.

    Saves a round trip, and more importantly removes the opportunity: a model
    handed an empty evidence block and asked for a verdict is being invited to
    fall back on general knowledge.
    """
    return CarrierVerdict(
        carrier_id=evidence.carrier_id,
        carrier_name=evidence.carrier_name,
        determination="insufficient_information",
        abstention_reason=(
            f"The indexed guide for {evidence.carrier_name} contains nothing "
            f"relevant to this question."
        ),
    )


async def synthesize_carrier(
    client: Any,
    settings: Settings,
    evidence: CarrierEvidence,
    profile: ProspectProfile,
    query: str = "",
) -> tuple[CarrierVerdict, int]:
    """Produce one carrier's verdict from its own evidence alone.

    Args:
        client: An `anthropic.AsyncAnthropic` instance.
        settings: Application settings.
        evidence: Evidence gathered for this carrier only.
        profile: The parsed prospect.

    Returns:
        The verified verdict and the number of claims dropped in verification.
    """
    if evidence.is_empty:
        return _empty_evidence_verdict(evidence), 0

    try:
        response = await client.messages.parse(
            model=settings.synthesis_model,
            max_tokens=settings.synthesis_max_tokens,
            system=SYNTHESIS_SYSTEM,
            output_config={"effort": settings.synthesis_effort},
            messages=[
                {
                    "role": "user",
                    "content": synthesis_user_prompt(
                        carrier_name=evidence.carrier_name,
                        profile_summary=profile_summary(profile),
                        evidence_block=evidence.evidence_text(),
                        agent_description=query,
                    ),
                }
            ],
            output_format=CarrierVerdict,
        )
    except Exception as exc:
        # A carrier that errors abstains rather than failing the whole
        # comparison. Three verdicts and one honest gap beats no answer.
        logger.exception("synthesis failed for %s", evidence.carrier_id)
        return (
            CarrierVerdict(
                carrier_id=evidence.carrier_id,
                carrier_name=evidence.carrier_name,
                determination="insufficient_information",
                abstention_reason=f"Synthesis failed for this carrier: {exc}",
            ),
            0,
        )

    verdict: CarrierVerdict | None = response.parsed_output
    if verdict is None:
        return (
            CarrierVerdict(
                carrier_id=evidence.carrier_id,
                carrier_name=evidence.carrier_name,
                determination="insufficient_information",
                abstention_reason=(
                    f"The model returned no structured verdict "
                    f"(stop_reason={response.stop_reason})."
                ),
            ),
            0,
        )

    # The model is told which carrier it is assessing, but the identifying
    # fields are overwritten with the caller's values regardless. They are known
    # facts, and a verdict mislabelled with another carrier's id would attach
    # correct evidence to the wrong company.
    verdict = verdict.model_copy(
        update={
            "carrier_id": evidence.carrier_id,
            "carrier_name": evidence.carrier_name,
        }
    )
    return verify_verdict(verdict, evidence)


def _evidence_pages(evidence: CarrierEvidence) -> set[int]:
    """Every page number represented in one carrier's assembled evidence."""
    pages: set[int] = set()
    if evidence.build and evidence.build.page:
        pages.add(evidence.build.page)
    if evidence.bmi_build and evidence.bmi_build.page:
        pages.add(evidence.bmi_build.page)
    pages.update(rule.page for rule in evidence.condition_rules)
    pages.update(table["page"] for table in evidence.threshold_tables)
    for hit in evidence.prose:
        pages.update(range(hit.page_start, hit.page_end + 1))
    return pages


async def compare_carriers(
    client: Any,
    settings: Settings,
    query: str,
    plan: QueryPlan,
) -> ComparisonResponse:
    """Run the full per-carrier comparison for a parsed query.

    Evidence gathering is synchronous and fast (SQLite plus a local vector
    search). Synthesis is the slow part, so the carriers run concurrently and
    total latency is roughly one call rather than four.

    Args:
        client: An `anthropic.AsyncAnthropic` instance.
        settings: Application settings.
        query: The sanitized query text.
        plan: The routing plan.

    Returns:
        The full comparison, with one verdict per carrier in scope.
    """
    started = time.perf_counter()

    carrier_ids = plan.carrier_ids or list(CARRIER_NAMES)
    unknown = [c for c in carrier_ids if c not in CARRIER_NAMES]
    if unknown:
        logger.warning("ignoring unknown carriers in plan: %s", unknown)
    carrier_ids = [c for c in carrier_ids if c in CARRIER_NAMES]

    evidence = [
        gather_carrier_evidence(settings, carrier_id, plan.profile, query)
        for carrier_id in carrier_ids
    ]

    results = await asyncio.gather(
        *(
            synthesize_carrier(client, settings, item, plan.profile, query)
            for item in evidence
        )
    )

    verdicts = [verdict for verdict, _ in results]
    dropped = sum(count for _, count in results)

    retrieved_pages = {
        item.carrier_id: sorted(
            {p for p in _evidence_pages(item)}
        )
        for item in evidence
    }

    return ComparisonResponse(
        query=query,
        query_type=plan.query_type,
        routing_reason=plan.reasoning,
        profile=plan.profile.model_dump(exclude_none=True),
        verdicts=verdicts,
        retrieved_pages=retrieved_pages,
        unverified_claims_dropped=dropped,
        latency_ms=round((time.perf_counter() - started) * 1000),
        model=settings.synthesis_model,
    )


def answer_build_lookup(settings: Settings, plan: QueryPlan) -> DirectAnswer:
    """Answer a published-figure question straight from the structured store.

    No model is involved. The answer is a row, the citation is the page that
    row printed on, and the excerpt is the row itself. Routing this away from
    synthesis is the entire justification for having a router: a build limit
    asked of a language model is a number that might be right, and the same
    limit asked of the table is the number.

    Args:
        settings: Application settings.
        plan: The routing plan, whose profile carries the height.

    Returns:
        The published row for each carrier in scope, cited.
    """
    from app.retrieval.structured import lookup_build_row

    height = plan.profile.height_inches
    if not height:
        return DirectAnswer(
            kind="build_lookup",
            note=(
                "This looks like a build chart question, but no height was "
                "stated. Build charts are keyed by height, so there is no row "
                "to read. State a height and ask again."
            ),
        )

    carrier_ids = [c for c in (plan.carrier_ids or list(CARRIER_NAMES)) if c in CARRIER_NAMES]
    gender = plan.profile.gender or "any"
    claims: list[Claim] = []

    for carrier_id in carrier_ids:
        for entry in lookup_build_row(settings.sqlite_path, carrier_id, height, gender):
            feet, inches = divmod(entry.height_inches, 12)
            claims.append(
                Claim(
                    statement=(
                        f"{CARRIER_NAMES[carrier_id]} publishes a maximum of "
                        f"{entry.max_weight_lbs} lb at {feet}'{inches}\" for "
                        f"{entry.rate_class}"
                        + (
                            f" ({entry.gender})."
                            if entry.gender != "any"
                            else " (all applicants)."
                        )
                    ),
                    citation=Citation(
                        carrier_id=carrier_id,
                        doc_id=entry.doc_id,
                        page=entry.page,
                        excerpt=(
                            f"{entry.rate_class}: maximum "
                            f"{entry.max_weight_lbs} lb"
                        ),
                    ),
                )
            )

    feet, inches = divmod(height, 12)
    note = (
        f"Read directly from the published build charts at {feet}'{inches}\". "
        f"No model was used to produce these figures."
        if claims
        else f"No indexed build chart has a row at {feet}'{inches}\"."
    )
    return DirectAnswer(kind="build_lookup", claims=claims, note=note)


def answer_prose_question(
    settings: Settings, plan: QueryPlan, query: str
) -> DirectAnswer:
    """Answer a policy question with the guide's own words.

    The retrieved passage is returned rather than a summary of it. A summary
    would be shorter and would introduce the one thing this tool cannot afford:
    a sentence about a carrier's policy that the carrier did not write.

    Args:
        settings: Application settings.
        plan: The routing plan.
        query: The sanitized query, used as the search text.

    Returns:
        The matching passages, cited.
    """
    from app.retrieval.semantic import get_index

    carrier_ids = [c for c in plan.carrier_ids if c in CARRIER_NAMES]
    index = get_index(settings)

    hits = []
    if carrier_ids:
        for carrier_id in carrier_ids:
            hits += index.search(query, top_k=3, carrier_id=carrier_id)
    else:
        hits = index.search(query, top_k=settings.semantic_top_k)

    claims = [
        Claim(
            statement=(
                f"{CARRIER_NAMES.get(hit.carrier_id, hit.carrier_id)}, "
                f"section \"{hit.section}\":"
            ),
            citation=Citation(
                carrier_id=hit.carrier_id,
                doc_id=hit.doc_id,
                page=hit.page_start,
                excerpt=hit.text,
            ),
        )
        for hit in hits
    ]
    return DirectAnswer(
        kind="prose_question",
        claims=claims,
        note=(
            "Passages quoted verbatim from the indexed guides. No model was "
            "used to summarise them."
            if claims
            else "Nothing in the indexed guides addresses this question."
        ),
    )
