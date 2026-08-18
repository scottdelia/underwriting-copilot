"""Pydantic models for every boundary: API in and out, LLM output, stored rows.

Section 11 of the brief requires a Pydantic model at every boundary. The reason
is narrower than "type safety": anything crossing a boundary here is either
untrusted (a user query, text lifted out of a third-party PDF) or unreliable (a
model's structured output). Validating at the boundary means a malformed value
is rejected at a known place with a known error, instead of propagating into a
citation the UI renders as fact.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# The canonical rate class ladder. Carriers name their classes differently;
# every carrier label is mapped onto exactly one of these before comparison.
# Ordering matters: a lower index is a better offer for the applicant.
CanonicalClass = Literal[
    "preferred_plus",
    "preferred",
    "standard_plus",
    "standard",
    "table_rated",
    "decline",
]

CANONICAL_ORDER: dict[str, int] = {
    "preferred_plus": 1,
    "preferred": 2,
    "standard_plus": 3,
    "standard": 4,
    "table_rated": 5,
    "decline": 6,
}

Gender = Literal["male", "female", "any"]


# ---------------------------------------------------------------------------
# Ingestion: prose chunks
# ---------------------------------------------------------------------------


class ProseChunk(BaseModel):
    """One retrievable span of prose, with everything a citation needs.

    Page numbers are mandatory rather than optional. A chunk whose page is
    unknown cannot be cited, and a claim that cannot be cited does not get
    rendered, so a chunk without a page is useless by construction.
    """

    chunk_id: str
    carrier_id: str
    doc_id: str
    doc_title: str
    section: str = Field(description="Nearest preceding heading in the document.")
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    text: str = Field(min_length=1)

    @field_validator("page_end")
    @classmethod
    def _page_order(cls, value: int, info: object) -> int:
        """Reject a chunk whose page range runs backwards."""
        data = getattr(info, "data", {})
        start = data.get("page_start")
        if start is not None and value < start:
            raise ValueError(f"page_end {value} precedes page_start {start}")
        return value

    @property
    def page_label(self) -> str:
        """Human-readable page reference, e.g. 'p. 4' or 'pp. 4-5'."""
        if self.page_start == self.page_end:
            return f"p. {self.page_start}"
        return f"pp. {self.page_start}-{self.page_end}"


# ---------------------------------------------------------------------------
# Ingestion: structured rows extracted from tables (populated in phase 2)
# ---------------------------------------------------------------------------


class BuildChartEntry(BaseModel):
    """One cell of a build chart: the weight limit for a height and class."""

    carrier_id: str
    doc_id: str
    page: int = Field(ge=1)
    height_inches: int = Field(ge=48, le=96)
    rate_class: str = Field(description="The carrier's own label, verbatim.")
    canonical_class: CanonicalClass
    max_weight_lbs: int = Field(ge=50, le=600)
    gender: Gender
    notes: str | None = None


class ConditionRule(BaseModel):
    """One carrier's published rule for one underwriting condition."""

    carrier_id: str
    doc_id: str
    page: int = Field(ge=1)
    condition: str = Field(description="Normalized key, e.g. 'type_2_diabetes'.")
    criteria: str = Field(description="Verbatim qualifying language.")
    best_available_class: str = Field(description="The carrier's own label.")
    canonical_best_class: CanonicalClass
    disqualifiers: list[str] = Field(default_factory=list)
    source_excerpt: str = Field(
        description="Short verbatim snippet supporting the citation."
    )


# ---------------------------------------------------------------------------
# API: /search
# ---------------------------------------------------------------------------


class SearchHit(BaseModel):
    """One retrieved chunk with its similarity score and citation fields."""

    chunk_id: str
    carrier_id: str
    doc_id: str
    doc_title: str
    section: str
    page_start: int
    page_end: int
    page_label: str
    score: float = Field(description="Cosine similarity; higher is more similar.")
    text: str


class SearchResponse(BaseModel):
    """Response body for /search."""

    query: str
    hits: list[SearchHit]
    latency_ms: int
    embeddings_backend: str


class HealthResponse(BaseModel):
    """Response body for /health. Deliberately leaks nothing about config."""

    status: Literal["ok"]
    index_ready: bool
    chunk_count: int
    carriers: list[str]
