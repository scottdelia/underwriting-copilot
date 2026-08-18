"""Generate the 50-item evaluation dataset from the corpus source data.

Run from the repository root:

    python tools/build_eval_dataset.py

Writes `backend/eval/dataset.jsonl` and `backend/eval/REVIEW.md`.

Every expected answer comes from `eval_oracle.py`, which computes outcomes from
the published thresholds in `carrier_data.py`. No pipeline output is consulted
anywhere in this file. See the oracle's module docstring for why that matters
and what it still does not establish.

The composition follows section 6 of the brief exactly:

    build chart lookups     12
    single-condition rules  12
    multi-condition cases   10
    cross-carrier compares   8
    out-of-corpus            8
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from eval_oracle import (  # noqa: E402
    build_class,
    build_limit,
    build_page,
    condition_class,
    condition_page,
    doc_id,
    worse_of,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = REPO_ROOT / "backend" / "eval" / "dataset.jsonl"
REVIEW_PATH = REPO_ROOT / "backend" / "eval" / "REVIEW.md"

CARRIER_NAMES = {
    "northstar": "Northstar Mutual Life",
    "cardinal": "Cardinal Assurance",
    "meridian": "Meridian Life & Annuity",
    "granite": "Granite Peak Financial",
}


def height_label(inches: int) -> str:
    """Render a height the way an agent would type it."""
    return f"{inches // 12}'{inches % 12}\""


def _item(
    number: int,
    question: str,
    category: str,
    expected: dict[str, Any],
    notes: str,
) -> dict[str, Any]:
    """Assemble one dataset record."""
    return {
        "id": f"eval_{number:03d}",
        "question": question,
        "category": category,
        "expected": expected,
        "notes": notes,
    }


def build_chart_items(start: int) -> list[dict[str, Any]]:
    """12 items asking for a published weight limit.

    These test table extraction fidelity end to end: a wrong answer here means
    the vision extractor misread a cell, or the router sent the question to
    semantic search instead of to the row.
    """
    cases = [
        ("northstar", 70, "Preferred Elite", "male"),
        ("northstar", 70, "Standard Plus", "male"),
        ("northstar", 64, "Preferred", "female"),
        ("cardinal", 72, "Select NT", "any"),
        ("cardinal", 66, "Super Preferred NT", "any"),
        ("meridian", 70, "Standard Plus", "male"),
        ("meridian", 68, "Preferred Plus", "female"),
        ("granite", 74, "Standard", "any"),
        ("granite", 62, "Elite", "any"),
        # Chart edges. The first and last rows are where an extractor that
        # dropped a boundary row on a page split would show up.
        ("northstar", 56, "Standard", "male"),
        ("meridian", 80, "Preferred", "male"),
        ("cardinal", 80, "Standard NT", "any"),
    ]
    items = []
    for offset, (carrier, height, rate_class, gender) in enumerate(cases):
        limit = build_limit(carrier, height, rate_class, gender)
        page = build_page(carrier, height, gender)
        sex = "" if gender == "any" else f" {gender}"
        items.append(
            _item(
                start + offset,
                f"What is the maximum weight at {height_label(height)} for "
                f"{CARRIER_NAMES[carrier]} {rate_class}{sex}?",
                "build_chart",
                {
                    "query_type": "build_lookup",
                    "answerable": True,
                    "expected_values": {
                        "carrier_id": carrier,
                        "rate_class": rate_class,
                        "max_weight_lbs": limit,
                    },
                    "must_cite_pages": [
                        {"carrier": carrier, "doc": doc_id(carrier), "page": page}
                    ],
                },
                f"Edge case: {height_label(height)} is the "
                + (
                    "first row of the chart."
                    if height == 56
                    else "last row of the chart."
                    if height == 80
                    else "a mid-chart row."
                ),
            )
        )
    return items


def single_condition_items(start: int) -> list[dict[str, Any]]:
    """12 items with one carrier, one condition, and a stated build.

    A build is included because a rate class is not determinable without one.
    The first eval run labelled these with the condition class alone and then
    marked the pipeline wrong for saying it could not classify someone whose
    build was never stated -- which was the pipeline being right and the label
    being wrong. The builds here sit comfortably inside every class, so the
    condition rule is what moves the answer.
    """
    cases: list[tuple[str, str, int, int, str, dict[str, Any], str]] = [
        ("northstar", "type_2_diabetes", 70, 175, "male", {"a1c": 6.5}, "below the 7.0 threshold"),
        ("northstar", "type_2_diabetes", 70, 175, "male", {"a1c": 7.4}, "inside the 7.0-7.9 band"),
        ("northstar", "type_2_diabetes", 70, 175, "male", {"a1c": 8.5}, "inside the 8.0-8.9 band"),
        ("northstar", "type_2_diabetes", 70, 175, "male", {"a1c": 9.3}, "above the eligibility cut"),
        ("cardinal", "type_2_diabetes", 70, 180, "any", {"a1c": 6.4, "bmi": 25.8}, "best grid cell"),
        ("cardinal", "type_2_diabetes", 70, 190, "any", {"a1c": 7.2, "bmi": 27.3}, "middle grid cell"),
        ("cardinal", "type_2_diabetes", 70, 190, "any", {"a1c": 8.2, "bmi": 27.3}, "poor control"),
        ("meridian", "type_2_diabetes", 70, 180, "male", {"a1c": 6.8, "duration_years": 4}, "short duration"),
        ("meridian", "type_2_diabetes", 70, 180, "male", {"a1c": 6.8, "duration_years": 12}, "long duration"),
        ("granite", "type_2_diabetes", 70, 190, "any", {"a1c": 7.0, "bmi": 27.3}, "under the BMI cut"),
        ("northstar", "obstructive_sleep_apnea", 70, 175, "male", {}, "rule names a class outright"),
        ("meridian", "asthma", 70, 175, "male", {}, "rule names a class outright"),
    ]
    items = []
    for offset, case in enumerate(cases):
        carrier, condition, height, weight, gender, params, note = case
        build_canonical, _, _ = build_class(carrier, height, weight, gender)
        cond_canonical = condition_class(carrier, condition, **params)
        expected_class = worse_of(build_canonical, cond_canonical)

        described = _describe(condition, params)
        sex = "" if gender == "any" else " " + gender
        items.append(
            _item(
                start + offset,
                f"For {CARRIER_NAMES[carrier]}, how would a 50 year old{sex} at "
                f"{height_label(height)} and {weight} lb with {described} be "
                f"classified?",
                "single_condition",
                {
                    "query_type": "prospect_comparison",
                    "answerable": True,
                    "carrier_verdicts": {carrier: expected_class},
                    "must_cite_pages": [
                        {
                            "carrier": carrier,
                            "doc": doc_id(carrier),
                            "page": condition_page(carrier, condition),
                        }
                    ],
                },
                f"Build allows {build_canonical}; the condition allows "
                f"{cond_canonical}; {note}.",
            )
        )
    return items


def _describe(condition: str, params: dict[str, Any]) -> str:
    """Turn oracle parameters into the phrasing an agent would use."""
    if condition == "type_2_diabetes":
        bits = [f"type 2 diabetes, A1c {params['a1c']}"]
        if "bmi" in params:
            bits.append(f"BMI {params['bmi']}")
        if "duration_years" in params:
            bits.append(f"diagnosed {params['duration_years']} years ago")
        return ", ".join(bits)
    if condition == "obstructive_sleep_apnea":
        return "obstructive sleep apnea, compliant on CPAP"
    if condition == "asthma":
        return "mild intermittent asthma using a rescue inhaler twice a week"
    if condition == "hypertension":
        return "treated hypertension averaging 138/84, stable for two years"
    if condition == "hyperlipidemia":
        return "elevated cholesterol with a total-to-HDL ratio of 4.8 on a statin"
    if condition == "atrial_fibrillation":
        return (
            "isolated atrial fibrillation, rate controlled, stably "
            "anticoagulated for two years, with no structural heart disease"
        )
    if condition == "myocardial_infarction":
        return (
            "a single myocardial infarction eight years ago with a normal "
            "ejection fraction and a negative recent stress test"
        )
    if condition == "hepatitis_c":
        return (
            "hepatitis C treated to sustained virologic response three years "
            "ago, with normal liver enzymes and no fibrosis"
        )
    return condition.replace("_", " ")


def multi_condition_items(start: int) -> list[dict[str, Any]]:
    """10 items carrying two conditions at once, plus a build.

    This is the category the brief calls "synthesis under interacting rules".
    Three limits apply independently and the worst governs, so an
    implementation that reports any one of them alone reaches a plausible
    answer and the wrong one.
    """
    cases = [
        ("northstar", 70, 200, "male", "type_2_diabetes", {"a1c": 7.2}, "hypertension"),
        ("northstar", 70, 200, "male", "type_2_diabetes", {"a1c": 6.5}, "obstructive_sleep_apnea"),
        ("northstar", 74, 235, "male", "type_2_diabetes", {"a1c": 8.4}, "hypertension"),
        ("cardinal", 70, 190, "any", "type_2_diabetes", {"a1c": 7.1, "bmi": 27.3}, "hyperlipidemia"),
        ("cardinal", 70, 190, "any", "type_2_diabetes", {"a1c": 6.4, "bmi": 27.3}, "atrial_fibrillation"),
        ("cardinal", 66, 205, "any", "type_2_diabetes", {"a1c": 8.2, "bmi": 33.1}, "hyperlipidemia"),
        ("meridian", 70, 195, "male", "type_2_diabetes", {"a1c": 6.8, "duration_years": 3}, "asthma"),
        ("meridian", 70, 195, "male", "type_2_diabetes", {"a1c": 7.5, "duration_years": 3}, "myocardial_infarction"),
        ("granite", 70, 200, "any", "type_2_diabetes", {"a1c": 7.0, "bmi": 28.7}, "hepatitis_c"),
        ("granite", 70, 225, "any", "type_2_diabetes", {"a1c": 7.8, "bmi": 32.3}, "hepatitis_c"),
    ]
    items = []
    for offset, case in enumerate(cases):
        carrier, height, weight, gender, first, params, second = case
        build_canonical, _, _ = build_class(carrier, height, weight, gender)
        first_class = condition_class(carrier, first, **params)
        second_class = condition_class(carrier, second)
        expected_class = worse_of(build_canonical, first_class, second_class)

        sex = "" if gender == "any" else " " + gender
        items.append(
            _item(
                start + offset,
                f"For {CARRIER_NAMES[carrier]}: 50 year old{sex}, "
                f"{height_label(height)}, {weight} lb, with "
                f"{_describe(first, params)} and {_describe(second, {})}. "
                f"How would they be classified?",
                "multi_condition",
                {
                    "query_type": "prospect_comparison",
                    "answerable": True,
                    "carrier_verdicts": {carrier: expected_class},
                    "must_cite_pages": [
                        {
                            "carrier": carrier,
                            "doc": doc_id(carrier),
                            "page": condition_page(carrier, first),
                        },
                        {
                            "carrier": carrier,
                            "doc": doc_id(carrier),
                            "page": condition_page(carrier, second),
                        },
                    ],
                },
                f"Build allows {build_canonical}; {first} allows {first_class}; "
                f"{second} allows {second_class}; the worst of the three governs.",
            )
        )
    return items


def cross_carrier_items(start: int) -> list[dict[str, Any]]:
    """8 items comparing every carrier at once. The core use case."""
    cases = [
        (55, "male", 70, 216, 7.1, "the brief's demo scenario, stated by BMI"),
        (45, "female", 65, 150, 6.4, "well-controlled, favourable build"),
        (60, "male", 72, 260, 8.2, "poor control and a heavy build"),
        (38, "male", 68, 170, 6.8, "young, borderline on Meridian's cut"),
        (52, "female", 63, 190, 7.6, "crosses Cardinal's 7.5 boundary"),
        (58, "male", 74, 230, 6.9, "on Meridian's exact 6.9 threshold"),
        (49, "male", 69, 205, 9.5, "above every carrier's eligibility cut"),
        (41, "female", 67, 160, 7.0, "on Northstar's exact 7.0 boundary"),
    ]
    items = []
    for offset, (age, gender, height, weight, a1c, note) in enumerate(cases):
        bmi = round(703.0 * weight / (height**2), 1)
        verdicts: dict[str, str | None] = {}
        pages = []
        for carrier in CARRIER_NAMES:
            build_canonical, _, _ = build_class(carrier, height, weight, gender)
            cond = condition_class(
                carrier, "type_2_diabetes", a1c=a1c, bmi=bmi, duration_years=None
            )
            # None when the build exceeds every published limit, which the
            # guides describe as individual consideration rather than a
            # decline. worse_of ignores None, so the condition class governs
            # and the tool is expected to flag the build separately.
            verdicts[carrier] = worse_of(build_canonical, cond)
            pages.append(
                {
                    "carrier": carrier,
                    "doc": doc_id(carrier),
                    "page": condition_page(carrier, "type_2_diabetes"),
                }
            )
        items.append(
            _item(
                start + offset,
                f"{age} year old {gender}, {height_label(height)}, {weight} lb, "
                f"type 2 diabetes with an A1c of {a1c}, non-smoker, $500K "
                f"20-year term. Compare the carriers.",
                "cross_carrier",
                {
                    "query_type": "prospect_comparison",
                    "answerable": True,
                    "carrier_verdicts": verdicts,
                    "must_cite_pages": pages,
                },
                note,
            )
        )
    return items


def out_of_corpus_items(start: int) -> list[dict[str, Any]]:
    """8 items nothing in the corpus can answer.

    Abstention is the measured outcome. A tool that answers these confidently is
    worse than one that answers nothing, and the brief treats a high refusal
    rate here as a feature rather than a shortfall.
    """
    questions = [
        (
            "What is the average auto insurance premium in Ohio?",
            "A different line of insurance entirely.",
        ),
        (
            "How much would a $500,000 20-year term policy cost per month for a "
            "55 year old male?",
            "Premiums are out of scope; the guides carry no rates.",
        ),
        (
            "Should my client buy whole life or term insurance for retirement?",
            "Advice, not a published guideline.",
        ),
        (
            "How would Northstar classify an applicant with stage 3 chronic "
            "kidney disease?",
            "A plausible condition that no indexed guide addresses.",
        ),
        (
            "What is Meridian's underwriting position on scuba diving to 200 "
            "feet?",
            "An avocation none of the guides covers.",
        ),
        (
            "Which carrier has the best customer service ratings?",
            "Not an underwriting question.",
        ),
        (
            "What did Cardinal Assurance's 2025 annual report say about "
            "profitability?",
            "A document that is not in the corpus.",
        ),
        (
            "How would Granite Peak classify a 40 year old with a history of "
            "melanoma?",
            "A condition outside the indexed vocabulary and the guides.",
        ),
    ]
    return [
        _item(
            start + offset,
            question,
            "out_of_corpus",
            {"query_type": None, "answerable": False, "carrier_verdicts": {}, "must_cite_pages": []},
            note,
        )
        for offset, (question, note) in enumerate(questions)
    ]


def write_review(items: list[dict[str, Any]]) -> None:
    """Write the human review checklist.

    The labels in this dataset are generated, and a generated label that nobody
    read is an assumption wearing a number's clothing. This file exists so the
    review is a concrete task with a definite size rather than a good intention.
    """
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_category.setdefault(item["category"], []).append(item)

    lines = [
        "# Eval dataset review",
        "",
        "**These labels are generated and have not been verified by a human.**",
        "",
        "They come from `tools/eval_oracle.py`, which computes outcomes from the",
        "published thresholds in `tools/carrier_data.py`. No pipeline output was",
        "consulted, so the labels are independent of the system under test.",
        "",
        "They are not independent of their author. The oracle and the synthesis",
        "prompt were written by the same person from the same documents, so a",
        "rule misread in one may be misread in the other, and the two would",
        "agree while both were wrong. That is what this review is for.",
        "",
        "## How to review",
        "",
        "For each item, open the cited page of the carrier's PDF in `corpus/`",
        "and check that the expected class follows from what is printed. Tick",
        "the box when you have. The build chart items are the fastest to check",
        "and the cross-carrier ones carry the most weight.",
        "",
        f"**{len(items)} items total.**",
        "",
    ]

    for category, group in by_category.items():
        lines.append(f"## {category} ({len(group)})")
        lines.append("")
        for item in group:
            expected = item["expected"]
            if expected.get("expected_values"):
                summary = (
                    f"{expected['expected_values']['rate_class']} = "
                    f"{expected['expected_values']['max_weight_lbs']} lb"
                )
            elif expected.get("carrier_verdicts"):
                summary = ", ".join(
                    f"{k}={v}" for k, v in expected["carrier_verdicts"].items()
                )
            else:
                summary = "must abstain"
            pages = ", ".join(
                f"{p['carrier']} p{p['page']}"
                for p in expected.get("must_cite_pages", [])
            )
            lines.append(f"- [ ] **{item['id']}** {item['question']}")
            lines.append(f"      expected: `{summary}`")
            if pages:
                lines.append(f"      cite: {pages}")
            lines.append(f"      note: {item['notes']}")
        lines.append("")

    REVIEW_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Generate the dataset and the review checklist."""
    items = (
        build_chart_items(1)
        + single_condition_items(13)
        + multi_condition_items(25)
        + cross_carrier_items(35)
        + out_of_corpus_items(43)
    )

    expected_composition = {
        "build_chart": 12,
        "single_condition": 12,
        "multi_condition": 10,
        "cross_carrier": 8,
        "out_of_corpus": 8,
    }
    actual: dict[str, int] = {}
    for item in items:
        actual[item["category"]] = actual.get(item["category"], 0) + 1
    if actual != expected_composition:
        raise RuntimeError(
            f"composition does not match the brief: {actual} != "
            f"{expected_composition}"
        )

    # A label the oracle could not compute is a gap in the oracle, not a valid
    # expected value. Failing here beats scoring against a null.
    for item in items:
        if item["expected"]["answerable"]:
            values = item["expected"].get("expected_values", {})
            if "max_weight_lbs" in values and values["max_weight_lbs"] is None:
                raise RuntimeError(f"{item['id']}: oracle produced no weight limit")
            for carrier, verdict in item["expected"].get(
                "carrier_verdicts", {}
            ).items():
                if verdict is None:
                    raise RuntimeError(
                        f"{item['id']}: oracle produced no verdict for {carrier}"
                    )
            for page in item["expected"].get("must_cite_pages", []):
                if page["page"] is None:
                    raise RuntimeError(f"{item['id']}: no page for {page['carrier']}")

    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DATASET_PATH.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item) + "\n")

    write_review(items)

    print(f"wrote {len(items)} items to {DATASET_PATH}")
    for category, count in actual.items():
        print(f"  {category:<18} {count}")
    print(f"\nreview checklist: {REVIEW_PATH}")


if __name__ == "__main__":
    main()
