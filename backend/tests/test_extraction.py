"""Tests for normalization, extraction validation, and the structured store.

The theme is the same as the chunking tests: cover the failures that produce a
plausible wrong answer rather than an error. A shifted column, a rate class
silently mapped to the wrong tier, or a build lookup that ranks "decline" above
"preferred" all yield output that looks entirely reasonable and is wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest.extract_tables import (
    PageExtraction,
    _validate_build_chart,
    check_monotonic_by_height,
    deduplicate_entries,
    parse_height_label,
)
from app.ingest.normalize import (
    canonical_rank,
    normalize_rate_class,
    worst_of,
)
from app.ingest.store import (
    counts,
    initialize,
    insert_anomalies,
    insert_build_entries,
    insert_condition_rules,
)
from app.models.extraction import BuildChart, BuildChartRow, ExtractionAnomaly, WeightCell
from app.models.schemas import BuildChartEntry, ConditionRule
from app.retrieval.structured import (
    lookup_build_class,
    lookup_build_row,
    lookup_condition_rules,
)


# ---------------------------------------------------------------------------
# Height parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,expected",
    [
        ("5' 10\"", 70),
        ("5'10\"", 70),
        ("5 ft 10 in", 70),
        ("70\"", 70),
        ("4' 8\"", 56),
        ("6' 8\"", 80),
    ],
)
def test_height_labels_parse(label: str, expected: int) -> None:
    """The printed height conventions all resolve to inches."""
    assert parse_height_label(label) == expected


@pytest.mark.parametrize("label", ["", "garbage", "12' 0\"", "2' 0\"", "999"])
def test_implausible_heights_are_rejected(label: str) -> None:
    """A height outside adult range is a misread and returns None, not a guess."""
    assert parse_height_label(label) is None


# ---------------------------------------------------------------------------
# Rate class normalization
# ---------------------------------------------------------------------------


def test_footnote_markers_are_stripped_from_labels() -> None:
    """"Standard *" is the class "Standard" plus a footnote, not a new class."""
    assert normalize_rate_class("northstar", "Standard *") == "standard"
    assert normalize_rate_class("northstar", "Standard †") == "standard"


def test_each_carrier_uses_its_own_mapping() -> None:
    """The same tier is reached through different labels per carrier."""
    assert normalize_rate_class("northstar", "Preferred Elite") == "preferred_plus"
    assert normalize_rate_class("cardinal", "Super Preferred NT") == "preferred_plus"
    assert normalize_rate_class("granite", "Elite") == "preferred_plus"
    assert normalize_rate_class("meridian", "Preferred Plus") == "preferred_plus"


def test_carrier_mapping_wins_over_generic_reading() -> None:
    """A carrier's own table takes precedence over a generic interpretation.

    Cardinal's "Select NT" is its third tier. Read generically, "Select" sounds
    like a preferred-tier marketing word. The carrier table is authoritative.
    """
    assert normalize_rate_class("cardinal", "Select NT") == "standard_plus"


def test_table_ratings_and_declines_map_generically() -> None:
    """Ratings and declines are carrier-independent conventions."""
    assert normalize_rate_class("granite", "Table 2") == "table_rated"
    assert normalize_rate_class("northstar", "Table B") == "table_rated"
    assert normalize_rate_class("meridian", "Decline") == "decline"
    assert normalize_rate_class("northstar", "Not eligible") == "decline"


def test_unmappable_labels_return_none_rather_than_guessing() -> None:
    """An unknown label is an absence of information, not a nearest match."""
    assert normalize_rate_class("northstar", "Ultra Preferred Diamond") is None
    assert normalize_rate_class("cardinal", "Individual consideration") is None
    assert normalize_rate_class("northstar", "—") is None


def test_granite_collapses_two_labels_onto_one_tier() -> None:
    """The documented lossy case is asserted so it cannot regress unnoticed.

    Granite Peak publishes five non-rated classes against a four-tier ladder.
    This is a known limitation recorded in normalize.py and FINDINGS; the test
    exists so the collapse stays deliberate rather than becoming a surprise.
    """
    assert normalize_rate_class("granite", "Preferred Best") == "preferred"
    assert normalize_rate_class("granite", "Preferred") == "preferred"


def test_worst_of_governs_when_limits_combine() -> None:
    """Build and condition limits combine to the worse of the two."""
    assert worst_of("standard_plus", "table_rated") == "table_rated"
    assert worst_of("preferred_plus", "standard") == "standard"
    assert worst_of("standard", None) == "standard"
    assert worst_of(None, None) is None


def test_ladder_ranks_better_classes_lower() -> None:
    """Rank order is the domain order, not alphabetical."""
    assert canonical_rank("preferred_plus") < canonical_rank("standard")
    assert canonical_rank("standard") < canonical_rank("decline")


# ---------------------------------------------------------------------------
# Extraction validation
# ---------------------------------------------------------------------------


def _page(carrier_id: str = "northstar") -> PageExtraction:
    return PageExtraction(carrier_id=carrier_id, doc_id="x.pdf", page=2)


def _chart(rows: list[BuildChartRow], labels: list[str]) -> BuildChart:
    return BuildChart(
        caption="Build Chart",
        rate_class_labels=labels,
        continued_from_previous_page=False,
        rows=rows,
    )


def test_valid_rows_convert_to_entries() -> None:
    """A clean row produces one entry per cell, with the ladder mapping applied."""
    page = _page()
    chart = _chart(
        [
            BuildChartRow(
                height_label="5' 10\"",
                sex="male",
                cells=[
                    WeightCell(rate_class="Preferred Elite", max_weight_lbs=189),
                    WeightCell(rate_class="Standard *", max_weight_lbs=227),
                ],
            )
        ],
        ["Preferred Elite", "Standard *"],
    )
    entries = _validate_build_chart(chart, page)
    assert len(entries) == 2
    assert entries[0].height_inches == 70
    assert entries[0].canonical_class == "preferred_plus"
    assert entries[1].canonical_class == "standard"
    assert not [a for a in page.anomalies if a.severity == "rejected"]


def test_unparseable_height_rejects_the_row() -> None:
    """A row whose height cannot be read is dropped and recorded."""
    page = _page()
    chart = _chart(
        [
            BuildChartRow(
                height_label="tall",
                sex="male",
                cells=[WeightCell(rate_class="Standard", max_weight_lbs=227)],
            )
        ],
        ["Standard"],
    )
    assert _validate_build_chart(chart, page) == []
    assert any(a.kind == "unparseable_height" for a in page.anomalies)


def test_unmappable_rate_class_rejects_the_cell() -> None:
    """A cell whose class cannot be mapped is dropped rather than guessed."""
    page = _page()
    chart = _chart(
        [
            BuildChartRow(
                height_label="5' 10\"",
                sex="male",
                cells=[WeightCell(rate_class="Mystery Tier", max_weight_lbs=227)],
            )
        ],
        ["Mystery Tier"],
    )
    assert _validate_build_chart(chart, page) == []
    assert any(a.kind == "unmapped_rate_class" for a in page.anomalies)


def test_null_cell_is_recorded_not_invented() -> None:
    """An unreadable cell yields no entry and one warning."""
    page = _page()
    chart = _chart(
        [
            BuildChartRow(
                height_label="5' 10\"",
                sex="male",
                cells=[WeightCell(rate_class="Standard", max_weight_lbs=None)],
            )
        ],
        ["Standard"],
    )
    assert _validate_build_chart(chart, page) == []
    assert any(a.kind == "null_cell" for a in page.anomalies)


def test_weights_that_improve_as_the_class_tightens_are_flagged() -> None:
    """Non-monotonic rows are the signature of a shifted column."""
    page = _page()
    chart = _chart(
        [
            BuildChartRow(
                height_label="5' 10\"",
                sex="male",
                cells=[
                    WeightCell(rate_class="Preferred Elite", max_weight_lbs=227),
                    WeightCell(rate_class="Standard", max_weight_lbs=189),
                ],
            )
        ],
        ["Preferred Elite", "Standard"],
    )
    _validate_build_chart(chart, page)
    assert any(a.kind == "non_monotonic_row" for a in page.anomalies)


def test_out_of_range_weight_is_rejected_by_the_storage_schema() -> None:
    """A weight the model schema allows but the storage schema does not."""
    page = _page()
    chart = _chart(
        [
            BuildChartRow(
                height_label="5' 10\"",
                sex="male",
                cells=[WeightCell(rate_class="Standard", max_weight_lbs=9000)],
            )
        ],
        ["Standard"],
    )
    assert _validate_build_chart(chart, page) == []
    assert any(a.kind == "schema_validation_failed" for a in page.anomalies)


def _entry(height: int, weight: int, page: int = 2) -> BuildChartEntry:
    return BuildChartEntry(
        carrier_id="northstar",
        doc_id="x.pdf",
        page=page,
        height_inches=height,
        rate_class="Standard",
        canonical_class="standard",
        max_weight_lbs=weight,
        gender="male",
    )


def test_monotonic_by_height_accepts_a_well_formed_chart() -> None:
    """Weight limits rising with height produce no findings."""
    entries = [_entry(68, 210), _entry(69, 216), _entry(70, 222)]
    assert check_monotonic_by_height(entries) == []


def test_monotonic_by_height_catches_a_cross_page_shift() -> None:
    """A limit that falls as height rises is caught at document level.

    This is the check that finds a column shifted on one page of a split table.
    Each page looks internally consistent; only the join across the page break
    exposes it.
    """
    entries = [_entry(68, 210, page=2), _entry(69, 216, page=2), _entry(70, 189, page=3)]
    anomalies = check_monotonic_by_height(entries)
    assert len(anomalies) == 1
    assert anomalies[0].kind == "non_monotonic_by_height"
    assert anomalies[0].page == 3


def test_duplicate_boundary_rows_are_collapsed() -> None:
    """A row transcribed on both sides of a page split is stored once.

    The first occurrence wins, which preserves the page the row printed on.
    """
    entries = [_entry(70, 222, page=2), _entry(70, 222, page=3), _entry(71, 229, page=3)]
    unique = deduplicate_entries(entries)
    assert len(unique) == 2
    assert unique[0].page == 2


# ---------------------------------------------------------------------------
# Structured store and lookups
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A store seeded with one carrier's row at a single height."""
    path = tmp_path / "test.sqlite3"
    initialize(path, reset=True)
    insert_build_entries(
        path,
        [
            BuildChartEntry(
                carrier_id="northstar",
                doc_id="northstar.pdf",
                page=3,
                height_inches=70,
                rate_class=label,
                canonical_class=canonical,
                max_weight_lbs=weight,
                gender="male",
            )
            for label, canonical, weight in [
                ("Preferred Elite", "preferred_plus", 189),
                ("Preferred", "preferred", 199),
                ("Standard Plus", "standard_plus", 213),
                ("Standard", "standard", 227),
            ]
        ],
    )
    # A carrier publishing a single unisex chart, stored under "any".
    insert_build_entries(
        path,
        [
            BuildChartEntry(
                carrier_id="cardinal",
                doc_id="cardinal.pdf",
                page=2,
                height_inches=70,
                rate_class="Select NT",
                canonical_class="standard_plus",
                max_weight_lbs=220,
                gender="any",
            )
        ],
    )
    return path


def test_build_lookup_returns_the_best_qualifying_class(db: Path) -> None:
    """216 lb clears Standard but not Standard Plus."""
    verdict = lookup_build_class(db, "northstar", 70, 216, "male")
    assert verdict.qualifies
    assert verdict.canonical_class == "standard"
    assert verdict.carrier_label == "Standard"
    assert verdict.max_weight_lbs == 227
    assert verdict.page == 3


def test_build_lookup_ranks_by_the_ladder_not_alphabetically(db: Path) -> None:
    """A light applicant gets the best tier, not the alphabetically first."""
    verdict = lookup_build_class(db, "northstar", 70, 150, "male")
    assert verdict.canonical_class == "preferred_plus"


def test_build_lookup_reports_exceeding_every_limit(db: Path) -> None:
    """Over every limit is a real answer, not an error or a silent default."""
    verdict = lookup_build_class(db, "northstar", 70, 400, "male")
    assert verdict.qualifies is False
    assert verdict.canonical_class is None
    assert verdict.page is None
    assert "exceeds every published build limit" in verdict.explanation


def test_unisex_charts_answer_a_gendered_query(db: Path) -> None:
    """A carrier with one chart for everyone still answers a male query."""
    verdict = lookup_build_class(db, "cardinal", 70, 216, "male")
    assert verdict.qualifies
    assert verdict.carrier_label == "Select NT"


def test_build_row_returns_the_whole_row_best_first(db: Path) -> None:
    """The full row lets an agent see the distance to the next class up."""
    row = lookup_build_row(db, "northstar", 70, "male")
    assert [e.rate_class for e in row] == [
        "Preferred Elite",
        "Preferred",
        "Standard Plus",
        "Standard",
    ]


def test_unknown_carrier_returns_no_verdict(db: Path) -> None:
    """An unindexed carrier yields no data rather than an exception."""
    assert lookup_build_class(db, "nonexistent", 70, 200, "male").qualifies is False


def test_queries_are_parameterized_against_injection(db: Path) -> None:
    """A hostile carrier id is treated as a value, never as SQL.

    Carrier ids reach this layer from extraction output, which originates in a
    model reading a third-party document. If the parameterization were wrong,
    this input would drop the table and the following assertion would fail.
    """
    hostile = "northstar'; DROP TABLE build_chart_entries; --"
    assert lookup_build_class(db, hostile, 70, 216, "male").qualifies is False
    assert counts(db)["build_chart_entries"] == 5


def test_condition_rules_round_trip(db: Path) -> None:
    """Rules survive storage with their disqualifiers and citation intact."""
    insert_condition_rules(
        db,
        [
            ConditionRule(
                carrier_id="northstar",
                doc_id="northstar.pdf",
                page=4,
                condition="type_2_diabetes",
                criteria="An A1c of 7.0 through 7.9 limits the class to Standard.",
                best_available_class="Standard Plus",
                canonical_best_class="standard_plus",
                disqualifiers=["insulin therapy", "A1c of 9.0 or greater"],
                source_excerpt="An A1c of 7.0 through 7.9 limits the class to Standard.",
            )
        ],
    )
    rules = lookup_condition_rules(db, "northstar", ["type_2_diabetes"])
    assert len(rules) == 1
    assert rules[0].disqualifiers == ["insulin therapy", "A1c of 9.0 or greater"]
    assert rules[0].page == 4


def test_condition_lookup_with_no_matches_returns_empty(db: Path) -> None:
    """Silence from a guide is reported as silence.

    This is what lets synthesis abstain. A carrier whose guide says nothing
    about a condition must produce no rows, not a default.
    """
    assert lookup_condition_rules(db, "northstar", ["hepatitis_c"]) == []
    assert lookup_condition_rules(db, "northstar", []) == []


def test_duplicate_inserts_are_idempotent(db: Path) -> None:
    """Re-running extraction does not double the rows."""
    before = counts(db)["build_chart_entries"]
    insert_build_entries(
        db,
        [
            BuildChartEntry(
                carrier_id="northstar",
                doc_id="northstar.pdf",
                page=3,
                height_inches=70,
                rate_class="Standard",
                canonical_class="standard",
                max_weight_lbs=227,
                gender="male",
            )
        ],
    )
    assert counts(db)["build_chart_entries"] == before


def test_anomalies_are_persisted_with_the_data(db: Path) -> None:
    """Rejections are stored, so the error rate is computed from what happened."""
    insert_anomalies(
        db,
        [
            ExtractionAnomaly(
                carrier_id="northstar",
                doc_id="northstar.pdf",
                page=3,
                severity="rejected",
                kind="unparseable_height",
                detail="could not read a height",
            )
        ],
    )
    assert counts(db)["extraction_anomalies"] == 1
