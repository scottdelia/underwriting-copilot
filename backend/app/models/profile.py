"""Schemas for the parsed query: what the agent actually asked.

Everything here is a model output boundary, so every field is either something
the agent stated or an explicit null. There is no "reasonable default" anywhere
in this file, and that is deliberate: a default silently becomes a fact about a
real person's application. If an agent did not say whether the prospect smokes,
the answer is "not stated", never "non-smoker".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# What kind of question is being asked. The router picks a retrieval strategy
# from this, and each value maps to a genuinely different path through the
# system -- which is the only reason to have a router at all.
QueryType = Literal[
    # A described prospect to compare across carriers. The full pipeline.
    "prospect_comparison",
    # A specific numeric lookup, e.g. a weight limit at a given height. Answered
    # from the structured store; semantic search would return a plausible
    # neighbour rather than the row.
    "build_lookup",
    # A question about published policy prose, e.g. a tobacco look-back period.
    "prose_question",
    # Nothing in an underwriting guide could answer this.
    "out_of_scope",
]

# The condition vocabulary the structured store is keyed on. Shared with
# extraction so a rule stored under one key is retrievable by the same key.
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


class ProspectProfile(BaseModel):
    """A prospect as described by the agent, with nothing inferred.

    Height, weight, and BMI are all optional and all independent. Agents
    describe builds inconsistently -- sometimes height and weight, sometimes a
    BMI, occasionally both -- so the parser records whichever were stated and
    `resolve_build` derives the rest.
    """

    age: int | None = Field(
        default=None, ge=18, le=100, description="Age in years, if stated."
    )
    gender: Literal["male", "female"] | None = Field(
        default=None, description="Sex as stated. Null if not stated."
    )
    height_inches: int | None = Field(
        default=None,
        ge=48,
        le=96,
        description="Height in whole inches, if stated. Convert feet and "
        "inches, e.g. 5'10\" is 70.",
    )
    weight_lbs: int | None = Field(
        default=None, ge=50, le=600, description="Weight in pounds, if stated."
    )
    bmi: float | None = Field(
        default=None,
        ge=10.0,
        le=80.0,
        description="Body mass index, only if the agent stated one directly. "
        "Do not calculate it.",
    )
    conditions: list[ConditionKey] = Field(
        default_factory=list,
        description="Medical conditions mentioned, from the fixed list.",
    )
    a1c: float | None = Field(
        default=None,
        ge=3.0,
        le=20.0,
        description="Most recent hemoglobin A1c, if stated.",
    )
    medications: list[str] = Field(
        default_factory=list, description="Medications named, verbatim."
    )
    tobacco: bool | None = Field(
        default=None,
        description="True if described as a tobacco or nicotine user, False if "
        "explicitly described as a non-smoker, null if not mentioned.",
    )
    coverage_amount_usd: int | None = Field(
        default=None, description="Face amount in dollars, if stated."
    )
    product_type: str | None = Field(
        default=None, description="Product described, e.g. '20-year term'."
    )


class QueryPlan(BaseModel):
    """The router's decision: what was asked, and what is needed to answer it."""

    query_type: QueryType
    profile: ProspectProfile = Field(
        default_factory=ProspectProfile,
        description="Prospect details stated in the query. Empty when the "
        "query does not describe a prospect.",
    )
    carrier_ids: list[str] = Field(
        default_factory=list,
        description="Carriers named in the query, by id. Empty means all "
        "indexed carriers are in scope.",
    )
    topic: str | None = Field(
        default=None,
        description="For a prose question, a short phrase naming the topic to "
        "search for. Null otherwise.",
    )
    reasoning: str = Field(
        description="One sentence explaining the classification, so the "
        "routing decision can be inspected rather than guessed at."
    )


# BMI is defined as 703 * pounds / inches^2 for imperial units. Kept as a named
# constant so the two conversions below cannot drift apart.
_BMI_IMPERIAL_FACTOR = 703.0


def bmi_from(height_inches: int, weight_lbs: int) -> float:
    """Compute BMI from height and weight."""
    return _BMI_IMPERIAL_FACTOR * weight_lbs / (height_inches**2)


def weight_from(height_inches: int, bmi: float) -> int:
    """Compute the weight in pounds implied by a height and a BMI."""
    return round(bmi * (height_inches**2) / _BMI_IMPERIAL_FACTOR)


def resolve_build(profile: ProspectProfile) -> ProspectProfile:
    """Fill in whichever of height, weight, and BMI can be derived.

    Only arithmetic is applied, never an assumption. Given height and weight,
    BMI follows. Given height and BMI, weight follows. Given BMI alone -- which
    is how the demo scenario describes a prospect -- nothing follows, because a
    BMI without a height does not identify a row in any build chart. That case
    is handled by the build lookup, which falls back to comparing BMI against
    the ceiling implied by the chart rather than inventing a height.

    Args:
        profile: The parsed profile.

    Returns:
        A copy with derivable fields filled in.
    """
    updates: dict[str, object] = {}

    if profile.height_inches and profile.weight_lbs and profile.bmi is None:
        updates["bmi"] = round(
            bmi_from(profile.height_inches, profile.weight_lbs), 1
        )
    elif profile.height_inches and profile.bmi and profile.weight_lbs is None:
        updates["weight_lbs"] = weight_from(profile.height_inches, profile.bmi)

    return profile.model_copy(update=updates) if updates else profile
