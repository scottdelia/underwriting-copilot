"""An independent oracle for expected underwriting outcomes.

WHY THIS FILE IS THE ANSWER TO THE CIRCULARITY PROBLEM
-------------------------------------------------------
The obvious way to label an eval set for a RAG system is to run the system and
check whether the answers look right. That measures nothing. The labels and the
predictions come from the same pipeline, so the score reports self-consistency
and reads as accuracy.

The labels here are derived from `carrier_data.py` -- the structured source the
corpus PDFs were *rendered from* -- by rules written out by hand from each
carrier's published thresholds. The pipeline never reads `carrier_data.py`. It
reads rendered PDFs, through page classification, vision extraction, an
embedding index, and a synthesis model. So an agreement between this oracle and
the pipeline means the pipeline recovered the source data through that whole
chain, which is the thing worth measuring.

WHAT THIS DOES NOT ESTABLISH, STATED PLAINLY
--------------------------------------------
This oracle and the synthesis prompt were written by the same author, from the
same documents. If a carrier's rule was misread here, it may be misread there
too, and the two would agree while both being wrong. That is a real and
unmeasured risk, and it is the specific reason a human should spot-check the
dataset rather than trust the generated labels. `docs/FINDINGS.md` says so
without hedging.

The mitigation available is structural: this file encodes rules as arithmetic
over published thresholds, while the pipeline reads prose and tables out of a
rendered document. The two disagree in most interesting ways.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from carrier_data import ALL_CARRIERS, Carrier, max_weight_for  # noqa: E402

CARRIERS: dict[str, Carrier] = {c.carrier_id: c for c in ALL_CARRIERS}

# Ladder ranks, duplicated here on purpose. Importing the application's copy
# would couple the oracle to the code under test; a label that changes because
# the application changed is not a label.
LADDER: dict[str, int] = {
    "preferred_plus": 1,
    "preferred": 2,
    "standard_plus": 3,
    "standard": 4,
    "table_rated": 5,
    "decline": 6,
}


def worse_of(*classes: str | None) -> str | None:
    """Return the least favourable class supplied, ignoring None."""
    known = [c for c in classes if c is not None]
    return max(known, key=lambda c: LADDER[c]) if known else None


def build_class(
    carrier_id: str, height_inches: int, weight_lbs: int, gender: str
) -> tuple[str | None, str | None, int | None]:
    """Best class a carrier's build chart allows, computed from its BMI caps.

    A build above every published limit returns None, not "decline". The guides
    do not say decline: Northstar's chart footnote says such weights "require
    individual consideration", and the others simply stop. Labelling that a
    decline asserts an outcome no document states, and the first eval run
    marked the pipeline wrong for abstaining where abstaining was correct.

    Args:
        carrier_id: Carrier to evaluate.
        height_inches: Applicant height.
        weight_lbs: Applicant weight.
        gender: "male" or "female".

    Returns:
        (canonical class, carrier label, the published limit) or (None, None,
        None) when the applicant exceeds every limit.
    """
    carrier = CARRIERS[carrier_id]
    for rate_class in carrier.rate_classes:
        cap = (
            rate_class.bmi_cap_female
            if gender == "female" and carrier.gendered_build_chart
            else rate_class.bmi_cap_male
        )
        limit = max_weight_for(cap, height_inches)
        if weight_lbs <= limit:
            return rate_class.canonical, rate_class.label, limit
    return None, None, None


def build_limit(
    carrier_id: str, height_inches: int, rate_class_label: str, gender: str
) -> int | None:
    """The published weight limit for one class at one height."""
    carrier = CARRIERS[carrier_id]
    for rate_class in carrier.rate_classes:
        if rate_class.label.lower() == rate_class_label.lower():
            cap = (
                rate_class.bmi_cap_female
                if gender == "female" and carrier.gendered_build_chart
                else rate_class.bmi_cap_male
            )
            return max_weight_for(cap, height_inches)
    return None


def diabetes_class(
    carrier_id: str,
    a1c: float,
    bmi: float | None = None,
    duration_years: int | None = None,
    insulin: bool = False,
) -> str | None:
    """Best class a carrier's diabetes rule allows.

    Transcribed by hand from each carrier's published thresholds. The four
    disagree deliberately, which is what makes the comparison worth running.

    Args:
        carrier_id: Carrier to evaluate.
        a1c: Most recent hemoglobin A1c.
        bmi: Body mass index, where the carrier's rule uses it.
        duration_years: Years since diagnosis, where the rule uses it.
        insulin: Whether the applicant uses insulin.

    Returns:
        The canonical class, or None if the carrier publishes no rule.
    """
    if carrier_id == "northstar":
        # Table 4.2. Insulin caps at Table B regardless of A1c.
        if insulin:
            return "table_rated"
        if a1c < 7.0:
            return "standard_plus"
        if a1c <= 7.9:
            return "standard"
        if a1c <= 8.9:
            return "table_rated"
        return "decline"

    if carrier_id == "cardinal":
        # Exhibit 6, an A1c by BMI grid.
        band = 0 if (bmi or 0) <= 30.0 else (1 if (bmi or 0) <= 32.0 else 2)
        if a1c <= 6.5:
            return ["preferred", "standard_plus", "standard"][band]
        if a1c <= 7.5:
            return ["standard_plus", "standard_plus", "standard"][band]
        if a1c <= 8.5:
            return ["standard", "standard", "table_rated"][band]
        return ["table_rated", "table_rated", "decline"][band]

    if carrier_id == "meridian":
        # Figure 5-1, an A1c by duration grid.
        long_duration = (duration_years or 0) >= 10
        if a1c <= 6.9:
            return "standard" if long_duration else "standard_plus"
        if a1c <= 8.0:
            return "standard"
        if a1c <= 9.0:
            return "table_rated"
        return "decline"

    if carrier_id == "granite":
        # Table 7 keys on BMI, and no diabetic beats Standard here. An A1c of
        # 7.5 or above adds one table, which cannot improve an outcome and only
        # matters where the base is already rated.
        value = bmi or 0.0
        if value > 35.0:
            return "decline"
        base = "table_rated" if value > 30.0 else "standard"
        if a1c >= 7.5 and base == "standard":
            base = "table_rated"
        return base

    return None


# Conditions whose published rule names a best available class outright, with no
# threshold table refining it. Transcribed from carrier_data.py.
SIMPLE_CONDITION_CLASS: dict[tuple[str, str], str] = {
    ("northstar", "hypertension"): "preferred",
    ("northstar", "obstructive_sleep_apnea"): "standard_plus",
    ("cardinal", "hyperlipidemia"): "preferred_plus",
    ("cardinal", "atrial_fibrillation"): "standard",
    ("meridian", "myocardial_infarction"): "table_rated",
    ("meridian", "asthma"): "preferred_plus",
    ("granite", "hepatitis_c"): "standard_plus",
}


def condition_class(
    carrier_id: str,
    condition: str,
    a1c: float | None = None,
    bmi: float | None = None,
    duration_years: int | None = None,
    insulin: bool = False,
) -> str | None:
    """Best class a carrier allows for one condition.

    Returns:
        The canonical class, or None when the carrier's guide is silent on the
        condition. None is the signal that the tool should abstain.
    """
    if condition == "type_2_diabetes":
        return diabetes_class(carrier_id, a1c or 0.0, bmi, duration_years, insulin)
    return SIMPLE_CONDITION_CLASS.get((carrier_id, condition))


def condition_page(carrier_id: str, condition: str) -> int | None:
    """The page a carrier's rule for a condition is printed on.

    Read from the generated ground truth rather than assumed, so a corpus
    regenerated with different pagination does not silently invalidate every
    citation label.
    """
    import json

    path = (
        Path(__file__).resolve().parent.parent
        / "backend"
        / "eval"
        / "ground_truth"
        / f"{carrier_id}.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    for rule in data["conditions"]:
        if rule["condition"] == condition:
            return rule["prose_page"]
    return None


def build_page(carrier_id: str, height_inches: int, gender: str) -> int | None:
    """The page a build chart row is printed on, from the generated ground truth."""
    import json

    path = (
        Path(__file__).resolve().parent.parent
        / "backend"
        / "eval"
        / "ground_truth"
        / f"{carrier_id}.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    wanted = gender if CARRIERS[carrier_id].gendered_build_chart else "any"
    for row in data["build_chart"]:
        if row["height_inches"] == height_inches and row["gender"] == wanted:
            return row["page"]
    return None


def doc_id(carrier_id: str) -> str:
    """The filename of a carrier's guide."""
    return f"{carrier_id}_underwriting_guide.pdf"
