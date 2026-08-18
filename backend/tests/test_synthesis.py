"""Tests for routing, citation verification, and enforced abstention.

The failures covered here are the ones that would make the tool untrustworthy
rather than broken: a claim that cites text nobody wrote, a determination whose
supporting evidence was all discarded but which still shows a rate class, and a
profile that acquired a fact the agent never stated.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.profile import ProspectProfile, QueryPlan, resolve_build
from app.models.verdict import CarrierVerdict, Citation, Claim
from app.retrieval.router import CarrierEvidence, profile_summary
from app.synthesis.answer import _empty_evidence_verdict, verify_verdict


# ---------------------------------------------------------------------------
# Profile parsing and build resolution
# ---------------------------------------------------------------------------


def test_height_and_weight_yield_bmi() -> None:
    """BMI is computed when both inputs are present."""
    resolved = resolve_build(ProspectProfile(height_inches=70, weight_lbs=216))
    assert resolved.bmi == 31.0


def test_height_and_bmi_yield_weight() -> None:
    """Weight is computed when height and BMI are present."""
    resolved = resolve_build(ProspectProfile(height_inches=70, bmi=31.0))
    assert resolved.weight_lbs == 216


def test_bmi_alone_stays_alone() -> None:
    """A BMI without a height implies neither a height nor a weight.

    The demo scenario is described this way. Inventing a height here would put
    a fabricated measurement into a build chart lookup and produce a cited
    answer about a person who was never described.
    """
    resolved = resolve_build(ProspectProfile(bmi=31.0))
    assert resolved.height_inches is None
    assert resolved.weight_lbs is None


def test_stated_values_are_never_overwritten() -> None:
    """Resolution fills gaps; it does not correct the agent."""
    profile = ProspectProfile(height_inches=70, weight_lbs=216, bmi=28.0)
    assert resolve_build(profile).bmi == 28.0


def test_profile_summary_names_what_was_not_stated() -> None:
    """The prompt is told what is missing, so it can say so rather than assume.

    An agent who did not mention tobacco has not said the prospect is a
    non-smoker, and the difference decides a rate class.
    """
    summary = profile_summary(ProspectProfile(age=55, gender="male", bmi=31.0))
    assert "Not stated by the agent" in summary
    assert "tobacco use" in summary
    assert "height" in summary


def test_profile_summary_distinguishes_non_tobacco_from_unstated() -> None:
    """False and None are different facts and must read differently."""
    stated = profile_summary(ProspectProfile(tobacco=False))
    assert "non-tobacco" in stated
    assert "tobacco use" not in stated.split("Not stated by the agent")[-1]


# ---------------------------------------------------------------------------
# The citation rule, encoded in the type
# ---------------------------------------------------------------------------


def test_a_claim_cannot_exist_without_a_citation() -> None:
    """The schema makes an uncited assertion unrepresentable."""
    with pytest.raises(ValidationError):
        Claim(statement="The applicant qualifies for Preferred.")  # type: ignore[call-arg]


def test_a_citation_cannot_have_an_empty_excerpt() -> None:
    """A citation with no quoted text cannot be verified, so it is invalid."""
    with pytest.raises(ValidationError):
        Citation(carrier_id="x", doc_id="x.pdf", page=1, excerpt="")


# ---------------------------------------------------------------------------
# Citation verification
# ---------------------------------------------------------------------------

SUPPLIED = (
    "An A1c of 7.0 through 7.9 limits the best available class to Standard. "
    "Applicants treated with insulin are not eligible for any class better "
    "than Table B regardless of A1c."
)


def _evidence() -> CarrierEvidence:
    evidence = CarrierEvidence(carrier_id="northstar", carrier_name="Northstar")
    evidence.sources.append(SUPPLIED)
    return evidence


def _claim(excerpt: str) -> Claim:
    return Claim(
        statement="A statement about the applicant.",
        citation=Citation(
            carrier_id="northstar",
            doc_id="northstar.pdf",
            page=4,
            excerpt=excerpt,
        ),
    )


def _verdict(**kwargs) -> CarrierVerdict:  # type: ignore[no-untyped-def]
    base = {
        "carrier_id": "northstar",
        "carrier_name": "Northstar",
        "determination": "classified",
        "carrier_label": "Standard",
        "canonical_class": "standard",
    }
    return CarrierVerdict(**{**base, **kwargs})


def test_a_genuine_quotation_is_kept() -> None:
    """An excerpt that appears in the supplied evidence survives."""
    verdict = _verdict(
        qualifying=[_claim("An A1c of 7.0 through 7.9 limits the best available class")]
    )
    verified, dropped = verify_verdict(verdict, _evidence())
    assert dropped == 0
    assert len(verified.qualifying) == 1
    assert verified.determination == "classified"


def test_whitespace_and_quote_differences_do_not_count_as_fabrication() -> None:
    """A quotation broken across lines is still a quotation."""
    verdict = _verdict(
        qualifying=[
            _claim("An A1c of 7.0   through\n7.9 limits the best available class")
        ]
    )
    _, dropped = verify_verdict(verdict, _evidence())
    assert dropped == 0


def test_an_invented_quotation_is_dropped() -> None:
    """Text that was never supplied is a fabricated citation.

    The statement here is plausible and might even be true of some carrier.
    That is exactly why it has to go: plausibility is not evidence.
    """
    verdict = _verdict(
        qualifying=[_claim("An A1c below 6.5 qualifies for Preferred Elite")]
    )
    verified, dropped = verify_verdict(verdict, _evidence())
    assert dropped == 1
    assert verified.qualifying == []


def test_an_excerpt_too_short_to_verify_is_dropped() -> None:
    """A three-word quote matches almost anything and supports nothing."""
    verdict = _verdict(qualifying=[_claim("Standard")])
    _, dropped = verify_verdict(verdict, _evidence())
    assert dropped == 1


def test_a_determination_loses_its_class_when_all_support_is_dropped() -> None:
    """A verdict whose every claim was fabricated is not a verdict.

    This is the case that matters most. Dropping the claims but keeping the
    rate class would leave a bare, uncited determination on screen -- which is
    precisely the output the citation rule exists to prevent.
    """
    verdict = _verdict(
        qualifying=[_claim("An A1c below 6.5 qualifies for Preferred Elite")],
        disqualifying=[_claim("The applicant has documented retinopathy")],
    )
    verified, dropped = verify_verdict(verdict, _evidence())
    assert dropped == 2
    assert verified.determination == "insufficient_information"
    assert verified.carrier_label is None
    assert verified.canonical_class is None
    assert "could not be found" in (verified.abstention_reason or "")


def test_a_partially_supported_determination_survives() -> None:
    """One surviving claim is enough to keep a determination standing."""
    verdict = _verdict(
        qualifying=[
            _claim("An A1c of 7.0 through 7.9 limits the best available class"),
            _claim("An A1c below 6.5 qualifies for Preferred Elite"),
        ]
    )
    verified, dropped = verify_verdict(verdict, _evidence())
    assert dropped == 1
    assert verified.determination == "classified"
    assert verified.carrier_label == "Standard"


def test_an_abstention_is_not_upgraded_by_verification() -> None:
    """Verification only ever removes support; it never adds a determination."""
    verdict = _verdict(
        determination="insufficient_information",
        carrier_label=None,
        canonical_class=None,
        abstention_reason="No diabetes rule is published.",
    )
    verified, _ = verify_verdict(verdict, _evidence())
    assert verified.determination == "insufficient_information"


# ---------------------------------------------------------------------------
# Abstention without a model call
# ---------------------------------------------------------------------------


def test_empty_evidence_abstains_without_calling_the_model() -> None:
    """No evidence means no verdict, decided before any spend.

    Handing a model an empty evidence block and asking for a rate class is an
    invitation to answer from general knowledge, which is the one source this
    tool is not allowed to use.
    """
    evidence = CarrierEvidence(carrier_id="granite", carrier_name="Granite Peak")
    assert evidence.is_empty

    verdict = _empty_evidence_verdict(evidence)
    assert verdict.determination == "insufficient_information"
    assert verdict.canonical_class is None
    assert "nothing relevant" in (verdict.abstention_reason or "")


def test_evidence_with_any_content_is_not_empty() -> None:
    """A single retrieved block is enough to justify a synthesis call."""
    evidence = CarrierEvidence(carrier_id="granite", carrier_name="Granite Peak")
    evidence.blocks.append("something")
    evidence.sources.append("something")
    evidence.prose = [object()]  # type: ignore[list-item]
    assert not evidence.is_empty


# ---------------------------------------------------------------------------
# Routing plan
# ---------------------------------------------------------------------------


def test_plan_defaults_to_all_carriers_when_none_named() -> None:
    """An unnamed carrier list means every carrier, not no carriers."""
    plan = QueryPlan(query_type="prospect_comparison", reasoning="describes a person")
    assert plan.carrier_ids == []


def test_out_of_scope_is_a_first_class_routing_outcome() -> None:
    """Refusing to engage is a routing decision, not an error path."""
    plan = QueryPlan(query_type="out_of_scope", reasoning="asks about auto insurance")
    assert plan.query_type == "out_of_scope"
    assert plan.profile == ProspectProfile()
