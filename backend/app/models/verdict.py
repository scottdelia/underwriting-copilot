"""Schemas for a synthesized carrier verdict.

THE CITATION RULE, ENCODED IN THE TYPES
---------------------------------------
The brief says every claim must carry a citation, and that a claim without one
does not get rendered. That is easy to write in a prompt and easy for a model to
drift away from, so it is encoded structurally instead: a claim *is* a statement
plus a citation. There is no shape in this module that can represent an
uncited assertion, so an uncited claim cannot survive parsing, let alone reach
a screen.

Verification happens after parsing. A citation that names a page is not yet a
correct citation -- the excerpt has to actually appear on that page. Synthesis
checks each one against the evidence it supplied and drops the ones that fail.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.schemas import CanonicalClass


class Citation(BaseModel):
    """A pointer to the exact place a claim came from."""

    carrier_id: str
    doc_id: str
    page: int = Field(ge=1)
    excerpt: str = Field(
        min_length=1,
        description="A short verbatim quotation from the cited page that "
        "supports the claim. Must be copied, never paraphrased.",
    )


class Claim(BaseModel):
    """One statement about a carrier's treatment of this prospect.

    A claim cannot exist without a citation. That is the point of the type.
    """

    statement: str = Field(
        min_length=1,
        description="One sentence stating what the guide says and what it "
        "means for this prospect.",
    )
    citation: Citation


class CarrierVerdict(BaseModel):
    """What one carrier's guide says about one prospect.

    `determination` has two values and no third. Either the evidence supports a
    class, or it does not and the tool says so. There is no "probably" state,
    because a hedged verdict in a regulated context is read as a verdict.
    """

    carrier_id: str
    carrier_name: str
    determination: Literal["classified", "insufficient_information"]

    carrier_label: str | None = Field(
        default=None,
        description="The carrier's own name for the likely class, verbatim. "
        "Null when abstaining.",
    )
    canonical_class: CanonicalClass | None = Field(
        default=None,
        description="The normalized tier, for cross-carrier comparison. Null "
        "when abstaining.",
    )

    qualifying: list[Claim] = Field(
        default_factory=list,
        description="Cited criteria the prospect meets.",
    )
    disqualifying: list[Claim] = Field(
        default_factory=list,
        description="Cited criteria that hold the prospect back from a better "
        "class, or that rule them out.",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Facts the guide requires that the agent did not supply. "
        "Populated whether or not the verdict abstains, because a class "
        "offered on incomplete information should say what is missing.",
    )
    abstention_reason: str | None = Field(
        default=None,
        description="Why no determination could be made. Required when "
        "determination is insufficient_information.",
    )


class DirectAnswer(BaseModel):
    """A cited answer to a question that is not a prospect classification.

    Build lookups and policy questions are answered from the store and the
    index directly, with no model in the path. There is nothing for a model to
    add to "the published limit at 6'0\" is 220 lb": the number is in a row,
    and passing it through a paraphrase step only creates an opportunity to
    change it. Zero hallucination risk here is structural, not measured.
    """

    kind: Literal["build_lookup", "prose_question"]
    claims: list[Claim] = Field(
        default_factory=list,
        description="Each finding, with the page it was read from.",
    )
    note: str | None = Field(
        default=None,
        description="Explains what was answered and how, including when "
        "nothing was found.",
    )


class ComparisonResponse(BaseModel):
    """The full cross-carrier answer returned to the client."""

    query: str
    query_type: str
    routing_reason: str
    profile: dict = Field(
        description="The parsed prospect, echoed back so the agent can see "
        "what the tool understood before trusting what it concluded."
    )
    verdicts: list[CarrierVerdict] = Field(
        default_factory=list,
        description="Per-carrier verdicts. Empty for query types that are not "
        "prospect classifications.",
    )
    answer: DirectAnswer | None = Field(
        default=None,
        description="A directly-answered result, for build lookups and policy "
        "questions. Null for prospect comparisons.",
    )
    retrieved_pages: dict[str, list[int]] = Field(
        default_factory=dict,
        description="Pages placed in front of the model, per carrier. Exposed "
        "so retrieval can be scored separately from synthesis: a wrong verdict "
        "whose supporting page was never retrieved is a retrieval failure, and "
        "one where it was retrieved and ignored is a synthesis failure.",
    )
    unverified_claims_dropped: int = Field(
        default=0,
        description="Claims discarded because their quoted excerpt could not "
        "be found on the page cited. Surfaced rather than hidden: a non-zero "
        "value here is a fabricated-citation attempt that was caught.",
    )
    latency_ms: int = Field(
        description="Wall-clock time for the whole request, measured from "
        "before the routing call. Routing is a model call on every path, so a "
        "figure that excluded it would understate what the caller waited for.",
    )
    model: str = Field(
        description="The model that produced this response. A value suffixed "
        "'(routing only)' means one call was made to classify the query and "
        "none to compose the answer: the answer itself was read from the "
        "structured store or the index and is quoted verbatim.",
    )
