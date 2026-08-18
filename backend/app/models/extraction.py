"""Schemas for what the vision model reads off a page.

WHY THESE ARE NARROWER THAN THE STORED ROW SCHEMAS
--------------------------------------------------
`schemas.BuildChartEntry` carries carrier_id, doc_id, and page. The model is
never asked for any of those. It is looking at one page image and it has no
reliable way to know which file that page came from, so asking would invite it
to invent an answer that then travels into a citation.

Everything the caller already knows is supplied by the caller. The model is
asked only for what is visibly printed on the page. That is both better prompt
design and a smaller surface for a hallucination to enter through.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WeightCell(BaseModel):
    """One cell of a build chart: the weight limit for one rate class."""

    rate_class: str = Field(
        description="The rate class column header exactly as printed, "
        "including any footnote marker."
    )
    max_weight_lbs: int | None = Field(
        description="The weight in pounds printed in this cell. Null if the "
        "cell is blank, illegible, or contains something other than a number."
    )


class BuildChartRow(BaseModel):
    """One row of a build chart: a height, a sex, and one cell per class."""

    height_label: str = Field(
        description="The height exactly as printed in the row, for example "
        "5' 10\"."
    )
    sex: Literal["male", "female", "any"] = Field(
        description="The sex this row applies to. Use 'any' when the chart "
        "has no sex column."
    )
    cells: list[WeightCell]


class BuildChart(BaseModel):
    """A build chart, or the portion of one visible on a single page."""

    caption: str | None = Field(
        description="The caption printed above the table, or null if the "
        "table begins above the top of this page."
    )
    rate_class_labels: list[str] = Field(
        description="The rate class column headers, left to right, exactly as "
        "printed."
    )
    continued_from_previous_page: bool = Field(
        description="True when this page shows a continuation of a table that "
        "started earlier, recognisable by a repeated header row with no caption."
    )
    rows: list[BuildChartRow]


class ThresholdTable(BaseModel):
    """Any non-build-chart table, transcribed as printed."""

    title: str | None = Field(
        description="The table title or caption, or null if there is none."
    )
    columns: list[str] = Field(description="Column headers, left to right.")
    rows: list[list[str]] = Field(
        description="Body rows. Each row has one string per column, in the "
        "same order as `columns`. Transcribe cell text verbatim."
    )
    footnotes: list[str] = Field(
        default_factory=list,
        description="Footnote lines printed beneath the table.",
    )


# The closed vocabulary of underwriting conditions this tool understands.
#
# This is the same principle as the rate class mapping table: a controlled
# vocabulary the model must choose from, rather than a free-text label it
# invents per page. Without it, the same rule extracted from two carriers comes
# back as "type 2 diabetes" and "Diabetes Mellitus (Type II)", and no structured
# lookup can join them.
#
# `other` is the escape hatch. A rule the vocabulary does not cover is recorded
# honestly as unclassified rather than forced into the nearest neighbour.
ConditionKey = Literal[
    "type_2_diabetes",
    "hypertension",
    "hyperlipidemia",
    "obstructive_sleep_apnea",
    "atrial_fibrillation",
    "myocardial_infarction",
    "asthma",
    "hepatitis_c",
    "tobacco_use",
    "other",
]


class ConditionRuleExtract(BaseModel):
    """One carrier's published rule for one condition, as read off a page."""

    condition: ConditionKey = Field(
        description="Which condition this rule governs, from the fixed list. "
        "Use 'other' if none of the listed conditions fits."
    )
    heading: str = Field(description="The section heading exactly as printed.")
    criteria: str = Field(
        description="The qualifying language, transcribed verbatim from the "
        "page. Do not summarise or paraphrase."
    )
    best_available_class: str = Field(
        description="The best rate class this rule allows, using the "
        "carrier's own label exactly as printed."
    )
    disqualifiers: list[str] = Field(
        default_factory=list,
        description="Conditions the page states will preclude the best "
        "available class, each transcribed verbatim.",
    )
    source_excerpt: str = Field(
        description="A short verbatim sentence from the page that supports "
        "the best_available_class value. Must appear on the page as printed."
    )


class PageTables(BaseModel):
    """Everything structured the model found on one page image.

    A page may legitimately hold zero, one, or several of each. Returning empty
    lists is the correct answer for a prose-only page, and the prompt says so
    explicitly so that a page classifier false positive costs one wasted call
    rather than a fabricated table.
    """

    build_charts: list[BuildChart] = Field(default_factory=list)
    threshold_tables: list[ThresholdTable] = Field(default_factory=list)
    condition_rules: list[ConditionRuleExtract] = Field(default_factory=list)


class ExtractionAnomaly(BaseModel):
    """A validation finding recorded against an extracted page.

    Anomalies are kept rather than raised because they are evidence about
    extraction quality. A run that silently dropped its problems would report a
    better error rate than it earned.
    """

    carrier_id: str
    doc_id: str
    page: int
    severity: Literal["rejected", "warning"]
    kind: str
    detail: str
