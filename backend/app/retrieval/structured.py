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
    # An unspecified sex must not silently match nothing. A carrier that
    # publishes separate male and female charts stores no rows under "any", so
    # a query for "any" against a gendered carrier returned an empty row and
    # the question went unanswered with no indication why. When the sex is
    # unknown, every published row at that height is returned and labelled, so
    # the agent sees what the guide actually offers and picks.
    if gender == "any":
        wanted = ("male", "female", "any")
    else:
        wanted = (gender, "any")

    placeholders = ",".join("?" for _ in wanted)
    with connect(db_path, read_only=True) as conn:
        rows = conn.execute(
            f"""
            SELECT carrier_id, doc_id, page, height_inches, rate_class,
                   canonical_class, max_weight_lbs, gender, notes
            FROM build_chart_entries
            WHERE carrier_id = ? AND height_inches = ?
              AND gender IN ({placeholders})
            """,  # noqa: S608 - placeholders only, values are bound
            (carrier_id, height_inches, *wanted),
        ).fetchall()

    entries = [BuildChartEntry(**dict(row)) for row in rows]
    # Sorted by ladder rank first so the best class leads, then by sex so a
    # gendered chart reads as paired rows rather than an interleaved jumble.
    return sorted(
        entries, key=lambda e: (CANONICAL_ORDER[e.canonical_class], e.gender)
    )


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


def lookup_threshold_tables(
    db_path: Path,
    carrier_id: str,
    pages: list[int] | None = None,
) -> list[dict]:
    """Return a carrier's transcribed threshold tables.

    These carry the refinements the headline condition rule leaves out. Granite
    Peak's diabetes rule states a best available class of Standard; its table is
    what says that a BMI above 30 forces a minimum of Table 2. A verdict built
    from the rule alone is too generous, so synthesis needs both.

    Args:
        db_path: Path to the structured store.
        carrier_id: The carrier to look up.
        pages: Restrict to these pages. Defaults to every page.

    Returns:
        Tables with their columns, rows, and footnotes decoded.
    """
    with connect(db_path, read_only=True) as conn:
        if pages:
            placeholders = ",".join("?" for _ in pages)
            rows = conn.execute(
                f"""
                SELECT doc_id, page, title, columns_json, rows_json,
                       footnotes_json
                FROM threshold_tables
                WHERE carrier_id = ? AND page IN ({placeholders})
                ORDER BY page
                """,  # noqa: S608 - placeholders only, values are bound
                (carrier_id, *pages),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT doc_id, page, title, columns_json, rows_json,
                       footnotes_json
                FROM threshold_tables
                WHERE carrier_id = ?
                ORDER BY page
                """,
                (carrier_id,),
            ).fetchall()

    return [
        {
            "doc_id": row["doc_id"],
            "page": row["page"],
            "title": row["title"],
            "columns": json.loads(row["columns_json"]),
            "rows": json.loads(row["rows_json"]),
            "footnotes": json.loads(row["footnotes_json"]),
        }
        for row in rows
    ]


def indexed_carriers(db_path: Path) -> list[str]:
    """Return the carriers that have build chart data."""
    with connect(db_path, read_only=True) as conn:
        rows = conn.execute(
            "SELECT DISTINCT carrier_id FROM build_chart_entries ORDER BY carrier_id"
        ).fetchall()
    return [row["carrier_id"] for row in rows]


@dataclass(frozen=True)
class BmiBuildVerdict:
    """The best class a BMI alone allows, with the rows the ceiling came from.

    Separate from BuildVerdict because it is a weaker kind of answer and should
    not be mistaken for the same thing. A weight lookup reads a published cell.
    This reads a ceiling *implied* by published cells.
    """

    carrier_id: str
    qualifies: bool
    canonical_class: str | None
    carrier_label: str | None
    implied_bmi_limit: float | None
    applicant_bmi: float
    gender: str
    doc_id: str | None
    page: int | None
    derivation: str


def lookup_build_class_by_bmi(
    db_path: Path,
    carrier_id: str,
    bmi: float,
    gender: str,
) -> BmiBuildVerdict:
    """Find the best class a carrier allows, given a BMI and no height.

    WHY THIS EXISTS AND WHAT IT COSTS
    ---------------------------------
    Agents routinely describe a prospect by BMI without giving a height. A build
    chart is keyed by height, so there is no row to read.

    A build chart is a statement about weight at a height, and BMI is a function
    of exactly those two quantities, so a BMI ceiling is recoverable from the
    published cells: each cell implies one, and for a chart built on a BMI rule
    they agree across heights. The median across heights is taken rather than a
    single row so that one rounded cell cannot move the answer.

    The honest caveat, and the reason this returns its own type: a chart that was
    *not* built from an underlying BMI rule will imply different ceilings at
    different heights, and the median will be a summary of a chart rather than a
    rule the carrier published. The spread is reported in `derivation` so that a
    reader can see whether the ceiling is a real limit or an average of an
    uneven chart. When height is known, `lookup_build_class` is strictly better
    and should be used instead.

    Args:
        db_path: Path to the structured store.
        carrier_id: The carrier to look up.
        bmi: The applicant's stated BMI.
        gender: "male", "female", or "any".

    Returns:
        The best class whose implied BMI ceiling the applicant is inside.
    """
    from statistics import median

    with connect(db_path, read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT rate_class, canonical_class, max_weight_lbs, height_inches,
                   doc_id, page
            FROM build_chart_entries
            WHERE carrier_id = ? AND gender IN (?, 'any')
            """,
            (carrier_id, gender),
        ).fetchall()

    if not rows:
        return BmiBuildVerdict(
            carrier_id=carrier_id,
            qualifies=False,
            canonical_class=None,
            carrier_label=None,
            implied_bmi_limit=None,
            applicant_bmi=bmi,
            gender=gender,
            doc_id=None,
            page=None,
            derivation="No build chart is indexed for this carrier.",
        )

    # Group the implied ceilings by the class they belong to.
    by_class: dict[str, list[float]] = {}
    meta: dict[str, tuple[str, str, int]] = {}
    for row in rows:
        implied = 703.0 * row["max_weight_lbs"] / (row["height_inches"] ** 2)
        by_class.setdefault(row["rate_class"], []).append(implied)
        meta[row["rate_class"]] = (
            row["canonical_class"],
            row["doc_id"],
            row["page"],
        )

    qualifying = [
        (label, median(values))
        for label, values in by_class.items()
        if bmi <= median(values)
    ]
    if not qualifying:
        return BmiBuildVerdict(
            carrier_id=carrier_id,
            qualifies=False,
            canonical_class=None,
            carrier_label=None,
            implied_bmi_limit=None,
            applicant_bmi=bmi,
            gender=gender,
            doc_id=None,
            page=None,
            derivation=(
                f"A BMI of {bmi} exceeds the ceiling implied by every "
                f"published rate class."
            ),
        )

    label, limit = min(qualifying, key=lambda item: CANONICAL_ORDER[meta[item[0]][0]])
    canonical, doc_id, page = meta[label]

    spread = max(by_class[label]) - min(by_class[label])
    return BmiBuildVerdict(
        carrier_id=carrier_id,
        qualifies=True,
        canonical_class=canonical,
        carrier_label=label,
        implied_bmi_limit=round(limit, 1),
        applicant_bmi=bmi,
        gender=gender,
        doc_id=doc_id,
        page=page,
        derivation=(
            f"No height was given, so the limit for {label} was derived from "
            f"the published weight limits across all {len(by_class[label])} "
            f"heights in the chart, which imply a BMI ceiling of "
            f"{limit:.1f} (spread {spread:.2f} across heights). The "
            f"applicant's stated BMI is {bmi}."
        ),
    )
