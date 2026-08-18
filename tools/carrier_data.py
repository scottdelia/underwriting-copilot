"""Structured source data for the four synthetic carrier underwriting guides.

WHY THIS FILE EXISTS
--------------------
No real carrier PDFs are used in this project (see docs/FINDINGS.md and the
README disclaimer). Instead, four fictional carriers are defined here as
structured data, rendered into realistic PDFs by generate_corpus.py, and then
ingested by the pipeline as if they were third-party documents.

This has one important consequence for the evaluation: because the build charts
and condition rules are generated FROM this data, the ground truth for table
extraction is known exactly, by construction. That makes extraction fidelity
measurable to the row rather than estimable by spot-check. The tradeoff -- these
PDFs are cleaner than real carrier documents, so the eval does not measure
robustness to real-world scan noise -- is stated plainly in the write-up.

The four carriers deliberately disagree with each other. They use different rate
class names, different A1c thresholds, and different build limits, so that
cross-carrier normalization is a real problem rather than a formality.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Heights covered by every build chart, in whole inches (4'8" through 6'8").
# The range is wide enough that a chart cannot fit on a single page, which
# forces a page split with a repeated header -- one of the three structures that
# breaks coordinate-based table parsers (see generate_corpus.py).
HEIGHT_RANGE_INCHES = list(range(56, 81))

# The canonical rate class ladder. Every carrier's own label maps onto exactly
# one of these tiers. Tier 1 is the best available class; higher is worse.
# Storing both the carrier's label and the canonical tier lets the UI show the
# carrier's own wording while the comparison logic sorts on the tier.
CANONICAL_LADDER: dict[str, int] = {
    "preferred_plus": 1,
    "preferred": 2,
    "standard_plus": 3,
    "standard": 4,
    "table_rated": 5,
    "decline": 6,
}


def max_weight_for(bmi_cap: float, height_inches: int) -> int:
    """Convert a BMI ceiling into a maximum weight in pounds for a given height.

    Real build charts are published as weight tables, not BMI limits, but they
    are almost always generated from an underlying BMI ceiling. Generating them
    the same way produces charts that are monotonic in height and internally
    consistent, which is what an underwriter would expect to see.

    Args:
        bmi_cap: The maximum BMI allowed for this rate class.
        height_inches: Applicant height in whole inches.

    Returns:
        The maximum weight in whole pounds, rounded to nearest.
    """
    return round(bmi_cap * (height_inches**2) / 703.0)


@dataclass(frozen=True)
class RateClass:
    """One rate class as a carrier names it, plus its canonical mapping."""

    label: str  # The carrier's own marketing label, shown verbatim in the UI.
    canonical: str  # Key into CANONICAL_LADDER.
    bmi_cap_male: float
    bmi_cap_female: float


@dataclass(frozen=True)
class ConditionRule:
    """One underwriting rule for one condition, as published by one carrier."""

    condition: str  # Normalized key, e.g. "type_2_diabetes".
    heading: str  # Section heading as printed in the guide.
    criteria: str  # Verbatim qualifying language printed in the guide.
    best_available: str  # Carrier's own label for the best class obtainable.
    disqualifiers: list[str]
    # Optional numeric threshold table printed alongside the prose. Rendered as
    # a real table in the PDF so the vision extractor has to recover it.
    threshold_table: dict | None = None


@dataclass(frozen=True)
class Carrier:
    """A complete fictional carrier guide."""

    carrier_id: str
    name: str
    doc_title: str
    doc_version: str
    # Gendered build charts are common but not universal. Carriers that publish
    # a single unisex chart set this to False, which the extractor must handle.
    gendered_build_chart: bool
    rate_classes: list[RateClass]
    conditions: list[ConditionRule] = field(default_factory=list)
    # Free-text sections with no numeric table, used to exercise prose retrieval.
    prose_sections: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared prose fragments. Slight wording differences between carriers are
# intentional: identical boilerplate would make semantic retrieval look better
# than it is.
# ---------------------------------------------------------------------------

TOBACCO_NORTHSTAR = (
    "An applicant is classified as Non-Tobacco when there has been no use of "
    "cigarettes, cigars, pipes, chewing tobacco, snuff, nicotine patches, "
    "nicotine gum, or electronic nicotine delivery systems within the 12 months "
    "preceding the application date, and the laboratory specimen is negative for "
    "cotinine. Occasional celebratory cigar use, defined as no more than 12 "
    "cigars per calendar year with a negative cotinine result, does not by "
    "itself require a Tobacco classification."
)

TOBACCO_CARDINAL = (
    "Non-Tobacco rates require complete abstinence from all forms of nicotine "
    "for the 24 months preceding the application, including electronic "
    "cigarettes and nicotine replacement therapy. Cardinal Assurance makes no "
    "exception for occasional cigar use. A positive cotinine result will result "
    "in Tobacco classification regardless of the answers given on the "
    "application."
)


# ---------------------------------------------------------------------------
# Carrier 1: Northstar Mutual Life
# ---------------------------------------------------------------------------

NORTHSTAR = Carrier(
    carrier_id="northstar",
    name="Northstar Mutual Life",
    doc_title="Field Underwriting Guide",
    doc_version="Edition 2026.1",
    gendered_build_chart=True,
    rate_classes=[
        RateClass("Preferred Elite", "preferred_plus", 27.1, 26.5),
        RateClass("Preferred", "preferred", 28.5, 27.9),
        RateClass("Standard Plus", "standard_plus", 30.5, 29.9),
        RateClass("Standard", "standard", 32.5, 31.9),
    ],
    conditions=[
        ConditionRule(
            condition="type_2_diabetes",
            heading="Diabetes Mellitus, Type 2",
            criteria=(
                "Applicants with Type 2 diabetes diagnosed at age 40 or later "
                "may be considered for Standard Plus provided the most recent "
                "hemoglobin A1c is below 7.0, treatment is limited to oral "
                "agents, and there is no evidence of retinopathy, nephropathy, "
                "neuropathy, or peripheral vascular disease. An A1c of 7.0 "
                "through 7.9 limits the best available class to Standard. An "
                "A1c of 8.0 through 8.9 will be rated Table B. An A1c of 9.0 "
                "or greater is not eligible."
            ),
            best_available="Standard Plus",
            disqualifiers=[
                "diagnosis before age 40",
                "insulin therapy",
                "documented retinopathy, nephropathy, or neuropathy",
                "A1c of 9.0 or greater",
            ],
            threshold_table={
                "title": "Table 4.2 - Type 2 Diabetes: Best Available Class by A1c",
                "columns": ["Most Recent A1c", "Best Available Class", "Notes"],
                "rows": [
                    ["Below 7.0", "Standard Plus", "Oral agents only †"],
                    ["7.0 - 7.9", "Standard", "Oral agents only †"],
                    ["8.0 - 8.9", "Table B", "Individual consideration"],
                    ["9.0 and above", "Not eligible", "—"],
                ],
                "footnotes": [
                    "† Applicants treated with insulin are not eligible "
                    "for any class better than Table B regardless of A1c."
                ],
            },
        ),
        ConditionRule(
            condition="hypertension",
            heading="Blood Pressure and Hypertension",
            criteria=(
                "Treated hypertension is acceptable at Preferred provided the "
                "average of the two most recent readings does not exceed 140/85 "
                "and treatment has been stable for at least six months. "
                "Readings above 150/90 limit the best available class to "
                "Standard."
            ),
            best_available="Preferred",
            disqualifiers=[
                "average reading above 160/95",
                "three or more antihypertensive medications",
            ],
            threshold_table={
                "title": "Table 3.1 - Blood Pressure Limits by Age and Class",
                "columns": [
                    "Age Band",
                    "Preferred Elite",
                    "Preferred",
                    "Standard Plus",
                    "Standard",
                ],
                "rows": [
                    ["Under 50", "135/85", "140/85", "145/90", "150/90"],
                    ["50 - 59", "138/85", "142/88", "148/90", "155/92"],
                    ["60 - 69", "140/85", "145/88", "150/90", "158/94"],
                    ["70 and over", "145/88", "150/90", "155/92", "160/95"],
                ],
                "footnotes": [
                    "Readings are the average of the two most recent recorded "
                    "measurements. Treated and untreated readings are evaluated "
                    "on the same basis."
                ],
            },
        ),
        ConditionRule(
            condition="obstructive_sleep_apnea",
            heading="Obstructive Sleep Apnea",
            criteria=(
                "Obstructive sleep apnea with documented CPAP compliance of at "
                "least four hours per night on 70 percent of nights may be "
                "considered for Standard Plus. Untreated sleep apnea, or "
                "documented non-compliance, limits the best available class to "
                "Standard."
            ),
            best_available="Standard Plus",
            disqualifiers=[
                "untreated moderate to severe apnea",
                "documented CPAP non-compliance",
                "co-existing pulmonary hypertension",
            ],
        ),
    ],
    prose_sections=[
        ("Tobacco and Nicotine Use", TOBACCO_NORTHSTAR),
        (
            "Family History",
            "A Preferred Elite classification requires that no biological "
            "parent or sibling was diagnosed with coronary artery disease or "
            "died of a cardiovascular cause before age 60. A single affected "
            "relative reduces the best available class to Preferred. Family "
            "history of cancer is not considered for classification purposes "
            "except in the case of two or more first-degree relatives with the "
            "same malignancy diagnosed before age 50.",
        ),
        (
            "Motor Vehicle Record",
            "A single moving violation in the past three years is acceptable at "
            "all classes. Two moving violations limit the best available class "
            "to Preferred. A conviction for driving under the influence within "
            "the past five years limits the best available class to Standard, "
            "and within the past two years is not eligible. A license "
            "suspension within the past three years requires individual "
            "consideration.",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Carrier 2: Cardinal Assurance
# ---------------------------------------------------------------------------

CARDINAL = Carrier(
    carrier_id="cardinal",
    name="Cardinal Assurance Company",
    doc_title="Underwriting Reference Manual",
    doc_version="Revision C, 2026",
    gendered_build_chart=False,
    rate_classes=[
        RateClass("Super Preferred NT", "preferred_plus", 27.5, 27.5),
        RateClass("Preferred NT", "preferred", 29.0, 29.0),
        RateClass("Select NT", "standard_plus", 31.6, 31.6),
        RateClass("Standard NT", "standard", 33.0, 33.0),
    ],
    conditions=[
        ConditionRule(
            condition="type_2_diabetes",
            heading="Diabetes (Type 2)",
            criteria=(
                "Cardinal Assurance considers well-controlled Type 2 diabetes "
                "favorably. An applicant whose most recent hemoglobin A1c is "
                "7.5 or below, whose body mass index does not exceed 32, and "
                "who has no diabetic complications of any kind may be offered "
                "Select NT. Applicants meeting these criteria who are also "
                "under age 50 at application may be considered for Preferred "
                "NT on an individual basis. An A1c above 7.5 but not exceeding "
                "8.5 is offered Standard NT."
            ),
            best_available="Select NT",
            disqualifiers=[
                "body mass index above 32",
                "any documented diabetic complication",
                "A1c above 8.5",
                "insulin use within the past 24 months",
            ],
            threshold_table={
                "title": "Exhibit 6 - Type 2 Diabetes Underwriting Grid",
                "columns": [
                    "A1c Range",
                    "BMI 30 or below",
                    "BMI 30.1 - 32.0",
                    "BMI above 32",
                ],
                "rows": [
                    ["6.5 or below", "Preferred NT", "Select NT", "Standard NT"],
                    ["6.6 - 7.5", "Select NT", "Select NT", "Standard NT"],
                    ["7.6 - 8.5", "Standard NT", "Standard NT", "Table 2"],
                    ["Above 8.5", "Table 2", "Table 4", "Decline"],
                ],
                "footnotes": [
                    "Grid assumes no diabetic complications and no insulin use "
                    "in the preceding 24 months. Any complication moves the "
                    "applicant to individual consideration."
                ],
            },
        ),
        ConditionRule(
            condition="hyperlipidemia",
            heading="Cholesterol and Lipids",
            criteria=(
                "A total cholesterol to HDL ratio of 5.0 or below is required "
                "for Super Preferred NT. A ratio of 5.1 to 6.0 is acceptable at "
                "Preferred NT. Total cholesterol above 300 requires individual "
                "consideration regardless of ratio. Treatment with a statin is "
                "not itself a debit provided the treated values meet the limits "
                "above."
            ),
            best_available="Super Preferred NT",
            disqualifiers=[
                "cholesterol to HDL ratio above 7.0",
                "total cholesterol above 300 without a favorable ratio",
            ],
            threshold_table={
                "title": "Exhibit 4 - Total Cholesterol / HDL Ratio Limits",
                "columns": ["Class", "Maximum Ratio", "Maximum Total Cholesterol"],
                "rows": [
                    ["Super Preferred NT", "5.0", "260"],
                    ["Preferred NT", "6.0", "280"],
                    ["Select NT", "7.0", "300"],
                    ["Standard NT", "8.0", "—"],
                ],
                "footnotes": [],
            },
        ),
        ConditionRule(
            condition="atrial_fibrillation",
            heading="Atrial Fibrillation",
            criteria=(
                "Isolated atrial fibrillation with no structural heart disease, "
                "controlled rate, and stable anticoagulation for at least 12 "
                "months may be considered for Standard NT. Atrial fibrillation "
                "accompanied by any valvular disease, cardiomyopathy, or a "
                "history of stroke or transient ischemic attack is rated Table 4 "
                "or higher."
            ),
            best_available="Standard NT",
            disqualifiers=[
                "structural heart disease",
                "history of stroke or transient ischemic attack",
                "unstable anticoagulation",
            ],
        ),
    ],
    prose_sections=[
        ("Nicotine Use", TOBACCO_CARDINAL),
        (
            "Coverage Amount and Age Limits",
            "Term coverage is available from age 18 through age 70 for 20-year "
            "level term and through age 60 for 30-year level term. The maximum "
            "face amount available without a financial questionnaire is "
            "$1,000,000 through age 55 and $500,000 from age 56 through age 65. "
            "Applications above these amounts require completion of Form CA-140 "
            "and, where the face amount exceeds $2,000,000, an inspection "
            "report.",
        ),
        (
            "Build and Body Composition",
            "Cardinal Assurance publishes a single build chart applicable to "
            "all applicants regardless of sex. Height is measured without shoes "
            "and weight is taken in ordinary indoor clothing. Where the "
            "paramedical measurement and the application differ by more than "
            "ten pounds, the paramedical measurement governs.",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Carrier 3: Meridian Life & Annuity
# ---------------------------------------------------------------------------

MERIDIAN = Carrier(
    carrier_id="meridian",
    name="Meridian Life & Annuity",
    doc_title="Agent Field Guide to Underwriting",
    doc_version="2026 Edition",
    gendered_build_chart=True,
    rate_classes=[
        RateClass("Preferred Plus", "preferred_plus", 26.8, 26.2),
        RateClass("Preferred", "preferred", 28.8, 28.2),
        RateClass("Standard Plus", "standard_plus", 30.9, 30.3),
        RateClass("Standard", "standard", 33.2, 32.6),
    ],
    conditions=[
        ConditionRule(
            condition="type_2_diabetes",
            heading="Type 2 Diabetes Mellitus",
            criteria=(
                "Meridian requires tight glycemic control for any class better "
                "than Standard. An applicant with Type 2 diabetes may be "
                "considered for Standard Plus only where the most recent "
                "hemoglobin A1c is 6.9 or below and at least two consecutive "
                "readings taken not less than three months apart are below 7.0. "
                "An A1c of 7.0 through 8.0 is offered Standard. An A1c above "
                "8.0 is rated Table 2 or higher based on duration and "
                "treatment."
            ),
            best_available="Standard Plus",
            disqualifiers=[
                "A1c above 8.0",
                "fewer than two documented A1c readings",
                "diabetic nephropathy at any stage",
            ],
            threshold_table={
                "title": "Figure 5-1. Glycemic Control Requirements",
                "columns": [
                    "Most Recent A1c",
                    "Duration under 10 Years",
                    "Duration 10 Years or More",
                ],
                "rows": [
                    ["6.9 or below", "Standard Plus", "Standard"],
                    ["7.0 - 8.0", "Standard", "Standard"],
                    ["8.1 - 9.0", "Table 2", "Table 4"],
                    ["Above 9.0", "Decline", "Decline"],
                ],
                "footnotes": [
                    "Duration is measured from the date of first diagnosis as "
                    "recorded in the attending physician statement."
                ],
            },
        ),
        ConditionRule(
            condition="myocardial_infarction",
            heading="History of Myocardial Infarction",
            criteria=(
                "A single myocardial infarction occurring more than five years "
                "prior to application, with normal ejection fraction, a "
                "negative stress test within the past 24 months, and no ongoing "
                "angina, may be considered at Table 2. Applicants within two "
                "years of the event are not eligible."
            ),
            best_available="Table 2",
            disqualifiers=[
                "event within the past two years",
                "ejection fraction below 50 percent",
                "ongoing angina",
                "more than one infarction",
            ],
        ),
        ConditionRule(
            condition="asthma",
            heading="Asthma",
            criteria=(
                "Mild intermittent asthma controlled with a rescue inhaler used "
                "no more than twice per week is acceptable at Preferred Plus. "
                "Asthma requiring daily controller medication is acceptable at "
                "Preferred. Any hospitalization for asthma within the past two "
                "years limits the best available class to Standard."
            ),
            best_available="Preferred Plus",
            disqualifiers=[
                "hospitalization within the past 12 months",
                "chronic oral corticosteroid therapy",
            ],
        ),
    ],
    prose_sections=[
        (
            "Tobacco Classification",
            "Meridian applies a 12-month look-back for cigarettes and a "
            "36-month look-back for all other tobacco products. Applicants who "
            "use nicotine replacement therapy as part of a documented cessation "
            "program may be considered for Non-Tobacco rates after six months "
            "provided the cotinine result is negative.",
        ),
        (
            "Foreign Travel and Residence",
            "Travel to countries under a current United States Department of "
            "State Level 3 or Level 4 advisory requires completion of the "
            "foreign travel questionnaire. Cumulative travel of fewer than 30 "
            "days per year to Level 3 countries does not affect "
            "classification.",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Carrier 4: Granite Peak Financial
# ---------------------------------------------------------------------------

GRANITE = Carrier(
    carrier_id="granite",
    name="Granite Peak Financial Group",
    doc_title="Life Underwriting Guidelines",
    doc_version="Effective January 2026",
    gendered_build_chart=False,
    rate_classes=[
        RateClass("Elite", "preferred_plus", 27.0, 27.0),
        RateClass("Preferred Best", "preferred", 28.2, 28.2),
        RateClass("Preferred", "preferred", 30.0, 30.0),
        RateClass("Standard Plus", "standard_plus", 31.8, 31.8),
        RateClass("Standard", "standard", 34.0, 34.0),
    ],
    conditions=[
        ConditionRule(
            condition="type_2_diabetes",
            heading="Diabetes",
            criteria=(
                "Granite Peak takes a conservative position on diabetes. No "
                "applicant with a diagnosis of Type 2 diabetes is eligible for "
                "any class better than Standard. Where the body mass index "
                "exceeds 30.0 in combination with a diabetes diagnosis, the "
                "minimum rating is Table 2. Where the body mass index exceeds "
                "35.0 in combination with a diabetes diagnosis, the application "
                "is declined."
            ),
            best_available="Standard",
            disqualifiers=[
                "body mass index above 35.0",
                "any insulin use",
                "diagnosis before age 30",
            ],
            threshold_table={
                "title": "Table 7 - Diabetes with Elevated Build",
                "columns": ["Body Mass Index", "Minimum Rating", "Additional Requirements"],
                "rows": [
                    ["30.0 or below", "Standard", "A1c below 7.5 ‡"],
                    ["30.1 - 35.0", "Table 2", "A1c below 7.5 ‡"],
                    ["Above 35.0", "Decline", "—"],
                ],
                "footnotes": [
                    "‡ An A1c of 7.5 or above adds one additional table "
                    "to the minimum rating shown."
                ],
            },
        ),
        ConditionRule(
            condition="hepatitis_c",
            heading="Hepatitis C",
            criteria=(
                "Applicants with a documented sustained virologic response "
                "following direct-acting antiviral therapy, normal liver "
                "enzymes, and no evidence of fibrosis may be considered for "
                "Standard Plus after 12 months. Untreated hepatitis C is not "
                "eligible."
            ),
            best_available="Standard Plus",
            disqualifiers=[
                "untreated infection",
                "any degree of documented cirrhosis",
                "elevated liver enzymes at application",
            ],
        ),
    ],
    prose_sections=[
        (
            "Nicotine and Tobacco",
            "Granite Peak classifies any nicotine use within 12 months as "
            "Tobacco. Electronic nicotine delivery systems are treated "
            "identically to cigarettes. There is no exception for cigars.",
        ),
        (
            "Product Availability",
            "Level term is offered in 10, 15, 20, and 30 year durations. The "
            "20-year product is available from issue age 18 through 65. The "
            "30-year product is available from issue age 18 through 50. "
            "Face amounts range from $100,000 to $5,000,000.",
        ),
        (
            "Underwriting Requirements by Age and Amount",
            "Applicants age 18 through 50 applying for $500,000 or less require "
            "a non-medical application and an electronic health record check. "
            "Applicants age 51 and above, or applying for more than $500,000, "
            "require a paramedical examination with blood and urine specimens. "
            "An electrocardiogram is required at age 61 and above for face "
            "amounts exceeding $1,000,000.",
        ),
    ],
)


ALL_CARRIERS: list[Carrier] = [NORTHSTAR, CARDINAL, MERIDIAN, GRANITE]
