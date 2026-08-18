"""Structured lookups against build charts and condition rules.

This is the other half of the retrieval strategy. A build chart question --
"what is the weight limit for a 5'10\" male at Standard Plus?" -- has an exact
answer sitting in a row of a table. Answering it with vector similarity over
prose is how a tool returns a confidently wrong number, because the nearest
chunk in embedding space is not the right row and nothing in the pipeline
notices the difference.

So build limits are looked up by SQL, and the row that produced the answer is
returned with it. Every verdict carries the page it came from.

All queries here are parameterized. Carrier ids and condition keys are values
that originated in a model reading a third-party document.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.ingest.store import connect
from app.models.schemas import CANONICAL_ORDER, BuildChartEntry, ConditionRule

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildVerdict:
    """The best class a build alone allows, plus the evidence for it."""

    carrier_id: str
    qualifies: bool
    canonical_class: str | None
    carrier_label: str | None
    max_weight_lbs: int | None
    height_inches: int
    weight_lbs: int
    gender: str
    doc_id: str | None
    page: int | None

    @property
    def explanation(self) -> str:
        """A one-line statement of what the chart says, for the UI and prompt."""
        height = f"{self.height_inches // 12}'{self.height_inches % 12}\""
        if not self.qualifies:
            return (
                f"At {height} and {self.weight_lbs} lb the applicant exceeds "
                f"every published build limit."
            )
        return (
            f"At {height} the limit for {self.carrier_label} is "
            f"{self.max_weight_lbs} lb; the applicant is {self.weight_lbs} lb."
        )


def lookup_build_class(
    db_path: Path,
    carrier_id: str,
    height_inches: int,
    weight_lbs: int,
    gender: str,
) -> BuildVerdict:
    """Find the best rate class a carrier's build chart allows.

    The chart is keyed by sex, but not every carrier publishes gendered limits.
    Carriers with a single unisex chart store their rows under `any`, so the
    query accepts either the requested sex or `any` and lets the data decide.
    Asking the caller to know which convention a carrier uses would push a
    document detail into the calling code.

    Args:
        db_path: Path to the structured store.
        carrier_id: The carrier to look up.
        height_inches: Applicant height in whole inches.
        weight_lbs: Applicant weight in pounds.
        gender: "male", "female", or "any".

    Returns:
        The best class the applicant is inside the limit for, with the row that
        proves it. `qualifies` is False when the applicant exceeds every limit,
        which is a real answer rather than an error.
    """
    with connect(db_path, read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT rate_class, canonical_class, max_weight_lbs, doc_id, page
            FROM build_chart_entries
            WHERE carrier_id = ?
              AND height_inches = ?
              AND gender IN (?, 'any')
              AND max_weight_lbs >= ?
            """,
            (carrier_id, height_inches, gender, weight_lbs),
        ).fetchall()

    if not rows:
        return BuildVerdict(
            carrier_id=carrier_id,
            qualifies=False,
            canonical_class=None,
            carrier_label=None,
            max_weight_lbs=None,
            height_inches=height_inches,
            weight_lbs=weight_lbs,
            gender=gender,
            doc_id=None,
            page=None,
        )

    # Ranking happens here rather than in SQL because the ladder order is a
    # domain fact, not a lexical one: ORDER BY canonical_class would sort
    # alphabetically and rank "decline" above "preferred_plus".
    best = min(rows, key=lambda r: CANONICAL_ORDER[r["canonical_class"]])
    return BuildVerdict(
        carrier_id=carrier_id,
        qualifies=True,
        canonical_class=best["canonical_class"],
        carrier_label=best["rate_class"],
        max_weight_lbs=best["max_weight_lbs"],
        height_inches=height_inches,
        weight_lbs=weight_lbs,
        gender=gender,
        doc_id=best["doc_id"],
        page=best["page"],
    )


def lookup_build_row(
    db_path: Path,
    carrier_id: str,
    height_inches: int,
    gender: str,
) -> list[BuildChartEntry]:
    """Return every published limit at one height, best class first.

    Used to show an agent the whole row, so they can see how far an applicant
    is from the next class up. A one-pound miss is actionable information; a
    bare verdict is not.

    Args:
        db_path: Path to the structured store.
        carrier_id: The carrier to look up.
        height_inches: Applicant height in whole inches.
        gender: "male", "female", or "any".

    Returns:
        Entries ordered from best class to worst.
    """
    with connect(db_path, read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT carrier_id, doc_id, page, height_inches, rate_class,
                   canonical_class, max_weight_lbs, gender, notes
            FROM build_chart_entries
            WHERE carrier_id = ? AND height_inches = ? AND gender IN (?, 'any')
            """,
            (carrier_id, height_inches, gender),
        ).fetchall()

    entries = [BuildChartEntry(**dict(row)) for row in rows]
    return sorted(entries, key=lambda e: CANONICAL_ORDER[e.canonical_class])


def lookup_condition_rules(
    db_path: Path,
    carrier_id: str,
    conditions: list[str],
) -> list[ConditionRule]:
    """Return a carrier's published rules for the given conditions.

    Args:
        db_path: Path to the structured store.
        carrier_id: The carrier to look up.
        conditions: Normalized condition keys.

    Returns:
        Matching rules. An empty list means this carrier's guide says nothing
        about these conditions, which is the input the synthesis layer needs in
        order to abstain rather than guess.
    """
    if not conditions:
        return []

    # The IN list is built from a placeholder count, never from the values. The
    # values themselves always travel as bound parameters.
    placeholders = ",".join("?" for _ in conditions)
    with connect(db_path, read_only=True) as conn:
        rows = conn.execute(
            f"""
            SELECT carrier_id, doc_id, page, condition, criteria,
                   best_available_class, canonical_best_class,
                   disqualifiers_json, source_excerpt
            FROM condition_rules
            WHERE carrier_id = ? AND condition IN ({placeholders})
            """,  # noqa: S608 - placeholders only, values are bound
            (carrier_id, *conditions),
        ).fetchall()

    return [
        ConditionRule(
            carrier_id=row["carrier_id"],
            doc_id=row["doc_id"],
            page=row["page"],
            condition=row["condition"],
            criteria=row["criteria"],
            best_available_class=row["best_available_class"],
            canonical_best_class=row["canonical_best_class"],
            disqualifiers=json.loads(row["disqualifiers_json"]),
            source_excerpt=row["source_excerpt"],
        )
        for row in rows
    ]


def indexed_carriers(db_path: Path) -> list[str]:
    """Return the carriers that have build chart data."""
    with connect(db_path, read_only=True) as conn:
        rows = conn.execute(
            "SELECT DISTINCT carrier_id FROM build_chart_entries ORDER BY carrier_id"
        ).fetchall()
    return [row["carrier_id"] for row in rows]
