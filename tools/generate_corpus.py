"""Render the synthetic carrier guides in carrier_data.py into realistic PDFs.

Run from the repository root:

    python tools/generate_corpus.py

Writes:
    corpus/<carrier_id>_underwriting_guide.pdf   -- the documents the pipeline ingests
    corpus/MANIFEST.md                           -- document inventory, committed
    backend/eval/ground_truth/<carrier_id>.json  -- exact expected extraction

WHY THE PDFs ARE DELIBERATELY AWKWARD
-------------------------------------
Section 4 of the spec is explicit that naive chunk-and-embed destroys numeric
tables, and that fixing this is what separates a real tool from a toy. A corpus
of clean, single-header, one-page tables would make the table extractor look
good without testing it. So the generated build charts use a two-level merged
header, carry footnote markers in the header cells, and are long enough to split
across a page boundary with a repeated header row. Those are the same three
features that break coordinate-based table parsers on real carrier guides.

GROUND TRUTH
------------
Page numbers are recovered by re-reading the rendered PDF and locating the text
each item actually printed, rather than by asking the layout engine where it
intended to put things. A build chart row therefore carries the page a human
would find it on, which is what makes citation correctness checkable exactly
rather than by eye.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

sys.path.insert(0, str(Path(__file__).parent))
from carrier_data import (  # noqa: E402
    ALL_CARRIERS,
    HEIGHT_RANGE_INCHES,
    Carrier,
    ConditionRule,
    max_weight_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "corpus"
GROUND_TRUTH_DIR = REPO_ROOT / "backend" / "eval" / "ground_truth"

# Every generated document carries this line on its cover page. It is the
# in-document counterpart to the UI banner required by section 8 of the spec.
SYNTHETIC_NOTICE = (
    "This document is a synthetic sample created for a software demonstration. "
    "The carrier named here does not exist. Every figure, threshold, and rule "
    "in this document is fabricated and must not be relied upon for any "
    "underwriting, sales, or advisory purpose."
)


def _normalize(text: str) -> str:
    """Collapse all runs of whitespace so extracted text can be substring-matched.

    PDF text extraction inserts line breaks wherever the renderer wrapped a
    line, so a heading that reads as one phrase in the document may come back
    split across two lines. Collapsing whitespace makes those matches reliable.
    """
    return " ".join(text.split())


def _page_texts(pdf_path: Path) -> list[str]:
    """Extract normalized text for every page of a rendered PDF.

    Returns:
        A list where index i holds the text of page i+1.
    """
    from pypdf import PdfReader

    return [_normalize(p.extract_text() or "") for p in PdfReader(str(pdf_path)).pages]


def _find_page(page_texts: list[str], needle: str, start: int = 0) -> int | None:
    """Return the 1-indexed page on which `needle` first appears, or None.

    Args:
        page_texts: Output of _page_texts().
        needle: The phrase to locate. Normalized before matching.
        start: 0-indexed page to begin searching from.

    Returns:
        The 1-indexed page number, or None if the phrase does not appear.
    """
    target = _normalize(needle)
    for i in range(start, len(page_texts)):
        if target in page_texts[i]:
            return i + 1
    return None


def _styles() -> dict[str, ParagraphStyle]:
    """Build the paragraph styles used across every generated guide."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "GuideTitle", parent=base["Title"], fontSize=24, leading=29, spaceAfter=6
        ),
        "subtitle": ParagraphStyle(
            "GuideSubtitle",
            parent=base["Normal"],
            fontSize=13,
            leading=17,
            textColor=colors.HexColor("#444444"),
            spaceAfter=4,
        ),
        "h1": ParagraphStyle(
            "GuideH1",
            parent=base["Heading1"],
            fontSize=15,
            leading=19,
            spaceBefore=16,
            spaceAfter=7,
            textColor=colors.HexColor("#1a3a5c"),
        ),
        "h2": ParagraphStyle(
            "GuideH2",
            parent=base["Heading2"],
            fontSize=12,
            leading=15,
            spaceBefore=12,
            spaceAfter=5,
            textColor=colors.HexColor("#1a3a5c"),
        ),
        "body": ParagraphStyle(
            "GuideBody",
            parent=base["BodyText"],
            fontSize=9.5,
            leading=13.5,
            alignment=TA_JUSTIFY,
            spaceAfter=7,
        ),
        "caption": ParagraphStyle(
            "GuideCaption",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
            spaceBefore=8,
            spaceAfter=4,
            fontName="Helvetica-Bold",
        ),
        "footnote": ParagraphStyle(
            "GuideFootnote",
            parent=base["Normal"],
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#555555"),
            spaceBefore=3,
        ),
        "notice": ParagraphStyle(
            "GuideNotice",
            parent=base["Normal"],
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#8a1c1c"),
            borderPadding=6,
            alignment=TA_JUSTIFY,
        ),
    }


def format_height(inches: int) -> str:
    """Render a height in inches the way a printed build chart does."""
    return f"{inches // 12}' {inches % 12}\""


def _table_style(header_rows: int) -> TableStyle:
    """Standard table styling: banded body rows and a dark two-level header."""
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#1a3a5c")),
            ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.white),
            ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("LEADING", (0, 0), (-1, -1), 10),
            ("ALIGN", (1, header_rows), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (-1, header_rows - 1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aa5b1")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            (
                "ROWBACKGROUNDS",
                (0, header_rows),
                (-1, -1),
                [colors.white, colors.HexColor("#eef2f6")],
            ),
        ]
    )


GENDER_LABELS = {"male": "Male", "female": "Female", "any": "All"}


def build_row_key(height_inches: int, gender: str, gendered: bool) -> str:
    """The unique printed text identifying one build chart row.

    Used to recover the row's true page number from the rendered PDF. On a
    gendered chart the height alone is ambiguous because every height appears
    once per sex, so the sex label is part of the key.
    """
    height = format_height(height_inches)
    return f"{height} {GENDER_LABELS[gender]}" if gendered else height


def build_chart_flowables(
    carrier: Carrier,
    styles: dict[str, ParagraphStyle],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Render a carrier's build chart and return flowables plus ground truth rows.

    Three properties here exist specifically to stress the table extractor,
    because a corpus of clean one-page tables would not test it at all:

    1. A two-level header whose top row is a single cell merged across every
       rate class column. Coordinate-based parsers routinely mis-assign columns
       under a merge like this.
    2. A footnote marker on the last class label, so the header text does not
       cleanly equal the rate class name.
    3. Enough rows that the chart cannot fit on one page. It splits with the
       header repeated, so the extractor sees a partial table on each page and
       ingestion has to merge the fragments without inventing the missing rows.

    Carriers that publish gendered limits get one combined table with a Sex
    column rather than two separate tables, which is how several real guides
    present it and which is what makes the chart long enough to split.

    Args:
        carrier: The carrier whose chart is being rendered.
        styles: Paragraph styles from _styles().

    Returns:
        A (flowables, ground_truth_rows) pair. Each ground truth row is one
        BuildChartEntry-shaped dict whose `page` field is filled in later, after
        the document has been laid out.
    """
    classes = carrier.rate_classes
    gendered = carrier.gendered_build_chart
    genders = ["male", "female"] if gendered else ["any"]

    caption = "Build Chart - Maximum Weight by Height"
    lead_cols = ["Height", "Sex"] if gendered else ["Height"]

    # Two-level header. Row 0 merges the rate class columns under one label and
    # leaves the leading columns blank; row 1 carries the individual labels.
    header_top = (
        [""] * len(lead_cols)
        + ["Maximum Weight by Rate Class (pounds)"]
        + [""] * (len(classes) - 1)
    )
    header_bottom = list(lead_cols) + [rc.label for rc in classes]
    # A footnote marker on the final class label gives the extractor a real
    # footnote to either attach or discard, rather than a clean string.
    header_bottom[-1] = f"{header_bottom[-1]} *"

    data: list[list[str]] = [header_top, header_bottom]
    truth: list[dict[str, Any]] = []

    for h in HEIGHT_RANGE_INCHES:
        for gender in genders:
            caps = [
                rc.bmi_cap_female if gender == "female" else rc.bmi_cap_male
                for rc in classes
            ]
            row = [format_height(h)]
            if gendered:
                row.append(GENDER_LABELS[gender])
            for rc, cap in zip(classes, caps):
                w = max_weight_for(cap, h)
                row.append(str(w))
                truth.append(
                    {
                        "carrier_id": carrier.carrier_id,
                        "height_inches": h,
                        "rate_class": rc.label,
                        "canonical_class": rc.canonical,
                        "max_weight_lbs": w,
                        "gender": gender,
                        "row_key": build_row_key(h, gender, gendered),
                    }
                )
            data.append(row)

    lead_width = 0.85 * inch
    remaining = 6.5 * inch - lead_width * len(lead_cols)
    col_widths = [lead_width] * len(lead_cols) + [remaining / len(classes)] * len(
        classes
    )

    table = Table(data, colWidths=col_widths, repeatRows=2, hAlign="LEFT")
    style = _table_style(header_rows=2)
    style.add("SPAN", (0, 0), (len(lead_cols) - 1, 0))
    style.add("SPAN", (len(lead_cols), 0), (-1, 0))
    style.add("ALIGN", (0, 2), (len(lead_cols) - 1, -1), "LEFT")
    style.add("LEFTPADDING", (0, 2), (len(lead_cols) - 1, -1), 6)
    table.setStyle(style)

    footnote = (
        "* Weights shown are maximums inclusive. An applicant whose weight "
        "equals the figure shown qualifies for that class. Weights above the "
        "final column require individual consideration."
    )

    flowables = [
        Paragraph(caption, styles["caption"]),
        table,
        Paragraph(footnote, styles["footnote"]),
        Spacer(1, 10),
    ]
    return flowables, truth


def threshold_table_flowables(
    spec: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """Render a condition's numeric threshold table (single-level header)."""
    data = [list(spec["columns"])] + [list(r) for r in spec["rows"]]
    n_cols = len(spec["columns"])
    table = Table(
        data, colWidths=[6.5 * inch / n_cols] * n_cols, repeatRows=1, hAlign="LEFT"
    )
    table.setStyle(_table_style(header_rows=1))

    parts: list[Any] = [
        Paragraph(spec["title"], styles["caption"]),
        table,
    ]
    for note in spec.get("footnotes", []):
        parts.append(Paragraph(note, styles["footnote"]))
    parts.append(Spacer(1, 10))
    # KeepTogether stops a two-row table being orphaned from its caption, which
    # would make the page citation for the caption and the data disagree.
    return [KeepTogether(parts)]


def condition_flowables(
    rule: ConditionRule,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """Render one condition section: heading, criteria prose, disqualifiers, table."""
    parts: list[Any] = [
        Paragraph(rule.heading, styles["h2"]),
        Paragraph(rule.criteria, styles["body"]),
        Paragraph("Conditions that preclude the best available class:", styles["body"]),
        ListFlowable(
            [ListItem(Paragraph(d, styles["body"])) for d in rule.disqualifiers],
            bulletType="bullet",
            start="square",
            leftIndent=18,
        ),
        Spacer(1, 6),
    ]
    if rule.threshold_table:
        parts += threshold_table_flowables(rule.threshold_table, styles)
    return parts


def _make_doc(path: Path, carrier: Carrier) -> BaseDocTemplate:
    """Build a document template with a running header and page-number footer.

    Page numbers are printed on every page because every citation the tool
    renders points at one. A guide without printed page numbers would make the
    citations unverifiable by a human holding the document.
    """
    doc = BaseDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=1.0 * inch,
        rightMargin=1.0 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        title=f"{carrier.name} - {carrier.doc_title}",
        author=carrier.name,
        subject="Synthetic sample document for software demonstration",
    )

    def decorate(canvas: Any, _doc: Any) -> None:
        canvas.saveState()
        page = canvas.getPageNumber()
        if page > 1:
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(colors.HexColor("#666666"))
            canvas.drawString(
                1.0 * inch,
                LETTER[1] - 0.62 * inch,
                f"{carrier.name} - {carrier.doc_title} ({carrier.doc_version})",
            )
            canvas.drawRightString(
                LETTER[0] - 1.0 * inch,
                LETTER[1] - 0.62 * inch,
                "SYNTHETIC SAMPLE - NOT A REAL CARRIER DOCUMENT",
            )
            canvas.setLineWidth(0.4)
            canvas.line(
                1.0 * inch,
                LETTER[1] - 0.70 * inch,
                LETTER[0] - 1.0 * inch,
                LETTER[1] - 0.70 * inch,
            )
        canvas.setFont("Helvetica", 8.5)
        canvas.setFillColor(colors.HexColor("#333333"))
        canvas.drawCentredString(LETTER[0] / 2.0, 0.55 * inch, f"Page {page}")
        canvas.restoreState()

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="body",
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
    return doc


def attribute_build_chart_pages(
    page_texts: list[str],
    build_truth: list[dict[str, Any]],
) -> None:
    """Fill in the `page` field of every build chart ground truth row, in place.

    A build chart is too long for one page, so recording a single page number
    for the whole table would be wrong for every row after the break. Each row
    carries a `row_key` -- the unique text that row prints, such as
    `5' 10" Male` -- which is located directly in the rendered page text.

    Deriving ground truth from the rendered output rather than from the layout
    engine's intentions means a citation check compares against what a human
    would actually see holding the document.

    Args:
        page_texts: Normalized per-page text, from _page_texts().
        build_truth: Ground truth rows to annotate. Mutated in place.

    Raises:
        RuntimeError: If a row could not be located in the rendered PDF, which
            means the document and the ground truth have drifted apart.
    """
    # row_key -> 1-indexed page of its first (and only) occurrence.
    located: dict[str, int] = {}
    for key in {r["row_key"] for r in build_truth}:
        page = _find_page(page_texts, key)
        if page is not None:
            located[key] = page

    missing = sorted({r["row_key"] for r in build_truth} - located.keys())
    if missing:
        raise RuntimeError(f"build chart rows not found in rendered PDF: {missing[:5]}")

    for row in build_truth:
        row["page"] = located[row["row_key"]]


def generate_guide(carrier: Carrier) -> dict[str, Any]:
    """Render one carrier's PDF and return its ground truth record.

    Args:
        carrier: The carrier to render.

    Returns:
        A ground truth dict with build chart rows, condition rules, and the
        physical page number each was printed on.

    Raises:
        RuntimeError: If a ground truth item cannot be located in the rendered
            PDF, meaning the generator and the ground truth disagree.
    """
    styles = _styles()
    out_path = CORPUS_DIR / f"{carrier.carrier_id}_underwriting_guide.pdf"

    story: list[Any] = []

    # --- Cover page ---
    story += [
        Spacer(1, 1.4 * inch),
        Paragraph(carrier.name, styles["title"]),
        Paragraph(carrier.doc_title, styles["subtitle"]),
        Paragraph(carrier.doc_version, styles["subtitle"]),
        Spacer(1, 0.5 * inch),
        Paragraph(SYNTHETIC_NOTICE, styles["notice"]),
        Spacer(1, 0.4 * inch),
        Paragraph(
            "For agent use only. Not for distribution to the public.",
            styles["body"],
        ),
        PageBreak(),
    ]

    # --- Section 1: how to use ---
    # Kept deliberately short. A long preamble would push each build chart onto
    # its own fresh page; keeping it brief lets the second chart begin partway
    # down a page and split across the page break, which is the layout the table
    # extractor most needs to be tested against.
    story += [
        Paragraph("1. Using This Guide", styles["h1"]),
        Paragraph(
            "This guide states the classification an applicant may be "
            "considered for on a preliminary basis. It is not an offer and does "
            "not bind the underwriter. Where two sections of this guide would "
            "produce different classifications for the same applicant, the more "
            "restrictive classification applies. Build limits and condition "
            "limits are evaluated independently and the worse of the two "
            "governs. Rate class names used by this carrier are specific to "
            "this carrier and do not correspond directly to similarly named "
            "classes offered by other companies.",
            styles["body"],
        ),
    ]

    # --- Section 2: build charts ---
    story += [Paragraph("2. Build Charts", styles["h1"])]
    story.append(
        Paragraph(
            "Separate limits apply to male and female applicants. Use the row "
            "matching the sex recorded on the application."
            if carrier.gendered_build_chart
            else "A single set of limits applies to all applicants regardless "
            "of sex.",
            styles["body"],
        )
    )

    build_flows, build_truth = build_chart_flowables(carrier, styles)
    story += build_flows

    # --- Section 3: medical conditions ---
    story += [PageBreak(), Paragraph("3. Medical Conditions", styles["h1"])]
    for rule in carrier.conditions:
        story += condition_flowables(rule, styles)

    # --- Section 4: general underwriting ---
    story += [Paragraph("4. General Underwriting", styles["h1"])]
    for heading, text in carrier.prose_sections:
        story += [
            Paragraph(heading, styles["h2"]),
            Paragraph(text, styles["body"]),
        ]

    doc = _make_doc(out_path, carrier)
    doc.build(story)

    # --- Ground truth pages, recovered from the rendered document ---
    page_texts = _page_texts(out_path)
    attribute_build_chart_pages(page_texts, build_truth)
    for row in build_truth:
        row["doc_id"] = out_path.name

    def require(needle: str, what: str) -> int:
        page = _find_page(page_texts, needle)
        if page is None:
            raise RuntimeError(
                f"{carrier.carrier_id}: {what} not found in rendered PDF: {needle!r}"
            )
        return page

    condition_truth = [
        {
            "carrier_id": carrier.carrier_id,
            "doc_id": out_path.name,
            "condition": rule.condition,
            "heading": rule.heading,
            "best_available_class": rule.best_available,
            "disqualifiers": rule.disqualifiers,
            "prose_page": require(rule.heading, "condition heading"),
            "table_page": (
                require(rule.threshold_table["title"], "condition table title")
                if rule.threshold_table
                else None
            ),
            "table_title": (
                rule.threshold_table["title"] if rule.threshold_table else None
            ),
            "table_rows": (
                rule.threshold_table["rows"] if rule.threshold_table else None
            ),
        }
        for rule in carrier.conditions
    ]

    prose_truth = [
        {"heading": heading, "page": require(heading, "prose heading")}
        for heading, _ in carrier.prose_sections
    ]

    return {
        "carrier_id": carrier.carrier_id,
        "carrier_name": carrier.name,
        "doc_id": out_path.name,
        "doc_title": carrier.doc_title,
        "doc_version": carrier.doc_version,
        "gendered_build_chart": carrier.gendered_build_chart,
        "rate_class_map": {
            rc.label: rc.canonical for rc in carrier.rate_classes
        },
        "build_chart": build_truth,
        "conditions": condition_truth,
        "prose_sections": prose_truth,
    }


def write_manifest(records: list[dict[str, Any]]) -> None:
    """Write corpus/MANIFEST.md.

    Section 8 of the spec requires a manifest listing document titles, versions,
    and sources. For this synthetic corpus the "source" is the generator script,
    which is stated plainly rather than dressed up as a public URL.
    """
    lines = [
        "# Corpus Manifest",
        "",
        "**Every document listed here is synthetic.** These are not real carrier",
        "underwriting guides. The carriers named do not exist, and every",
        "threshold, weight limit, and rule is fabricated for demonstration",
        "purposes. Nothing in this corpus should be used for underwriting, sales,",
        "or advisory purposes.",
        "",
        "The PDFs themselves are gitignored. Regenerate them with:",
        "",
        "```bash",
        "python tools/generate_corpus.py",
        "```",
        "",
        "| Document | Carrier | Version | Pages | Source |",
        "|---|---|---|---|---|",
    ]
    for rec in records:
        lines.append(
            f"| `{rec['doc_id']}` | {rec['carrier_name']} | {rec['doc_version']} "
            f"| {rec['page_count']} | Generated by `tools/generate_corpus.py` |"
        )
    lines += [
        "",
        "## Why synthetic",
        "",
        "Real carrier field underwriting guides are third-party copyrighted",
        "material. Redistributing them, or serving them from a public demo, is",
        "not permissible without carrier agreement. Building the corpus from",
        "generated documents removes that problem entirely and has a second",
        "benefit: because the tables are generated from structured data, the",
        "ground truth for extraction is known exactly rather than estimated by",
        "hand-checking. See `docs/FINDINGS.md` for what this does and does not",
        "let the evaluation measure.",
        "",
    ]
    (CORPUS_DIR / "MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Generate all four guides, their ground truth, and the manifest."""
    from pypdf import PdfReader

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for carrier in ALL_CARRIERS:
        record = generate_guide(carrier)
        pdf_path = CORPUS_DIR / record["doc_id"]
        record["page_count"] = len(PdfReader(str(pdf_path)).pages)

        gt_path = GROUND_TRUTH_DIR / f"{carrier.carrier_id}.json"
        gt_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

        records.append(record)
        print(
            f"  {record['doc_id']:44} {record['page_count']:>3} pages  "
            f"{len(record['build_chart']):>4} build rows  "
            f"{len(record['conditions'])} conditions"
        )

    write_manifest(records)
    print(f"\nCorpus:       {CORPUS_DIR}")
    print(f"Ground truth: {GROUND_TRUTH_DIR}")


if __name__ == "__main__":
    main()
