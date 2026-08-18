"""Vision-based table extraction, with validation that can reject a result.

WHY VISION AND NOT A TEXT PARSER
--------------------------------
Coordinate-based table parsers reconstruct a grid from ruling lines and text
positions. They break on the three things real underwriting guides do
constantly: merged header cells spanning several columns, footnote markers
attached to header labels, and tables split across a page break with the header
repeated. All three are present in this corpus by design. A vision model reads
the page the way a person does, so the merge is just a merge.

The cost of that is a model in the extraction path, which means the output is
unreliable in a way a parser's is not. That is what the validation below is
for.

VALIDATION IS THE POINT
-----------------------
The brief says to reject malformed output rather than silently accept it. Schema
validation catches shape errors, but the dangerous failure here is not
malformed -- it is well-formed and wrong: a shifted column produces a perfectly
valid row where every weight belongs to the class next door.

So there are three layers:

1. Schema, enforced by the API through structured outputs.
2. Per-row semantic checks: the height must parse, the class labels must be
   classes this carrier actually publishes, and weight limits must not improve
   as the rate class gets stricter.
3. Cross-page checks at document level: within one class and sex, the weight
   limit must increase with height.

Layer 3 is the one that catches a shifted column, because a shift usually
survives layers 1 and 2 intact.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.ingest.normalize import known_labels, normalize_rate_class
from app.models.extraction import ExtractionAnomaly, PageTables
from app.models.schemas import BuildChartEntry, ConditionRule
from app.synthesis.prompts import (
    TABLE_EXTRACTION_SYSTEM,
    table_extraction_user_prompt,
)

logger = logging.getLogger(__name__)

# Heights print in several conventions. Accepts 5' 10", 5'10", 5 ft 10 in,
# and a bare inch count such as 70".
_HEIGHT_FT_IN = re.compile(r"(\d+)\s*(?:'|ft|feet)\s*(\d+)?\s*(?:\"|in|inches)?")
_HEIGHT_INCHES_ONLY = re.compile(r"^\s*(\d{2,3})\s*(?:\"|in|inches)?\s*$")

# Plausible adult heights. A value outside this is a misread, not a person.
MIN_HEIGHT_INCHES = 48
MAX_HEIGHT_INCHES = 96


def parse_height_label(label: str) -> int | None:
    """Convert a printed height label into whole inches.

    Args:
        label: The height exactly as printed, e.g. "5' 10\"".

    Returns:
        The height in inches, or None if the label cannot be parsed or falls
        outside a plausible adult range. None is deliberate: a row whose height
        cannot be read has no usable key and is dropped rather than guessed at.
    """
    text = label.strip()

    match = _HEIGHT_FT_IN.search(text)
    if match:
        feet = int(match.group(1))
        inches = int(match.group(2) or 0)
        total = feet * 12 + inches
    else:
        match = _HEIGHT_INCHES_ONLY.match(text)
        if not match:
            return None
        total = int(match.group(1))

    if not (MIN_HEIGHT_INCHES <= total <= MAX_HEIGHT_INCHES):
        return None
    return total


@dataclass
class PageExtraction:
    """The result of extracting one page, including anything rejected."""

    carrier_id: str
    doc_id: str
    page: int
    build_entries: list[BuildChartEntry] = field(default_factory=list)
    condition_rules: list[ConditionRule] = field(default_factory=list)
    threshold_tables: list[dict] = field(default_factory=list)
    anomalies: list[ExtractionAnomaly] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def render_page_png(pdf_path: Path, page: int, dpi: int) -> bytes:
    """Rasterize one page of a PDF to PNG bytes.

    Args:
        pdf_path: The source document.
        page: 1-indexed page number.
        dpi: Render resolution. 150 keeps a Letter page inside the model's
            high-resolution band while staying legible for 8pt table type;
            higher resolutions cost proportionally more image tokens for no
            measured gain on this corpus.

    Returns:
        PNG-encoded image bytes.

    Raises:
        IndexError: If the page does not exist in the document.
    """
    import fitz

    with fitz.open(pdf_path) as doc:
        if not (1 <= page <= doc.page_count):
            raise IndexError(f"{pdf_path.name} has no page {page}")
        return doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")


def extract_page_text(pdf_path: Path, page: int) -> str:
    """Return the text layer of one page, for verifying quoted excerpts.

    Args:
        pdf_path: The source document.
        page: 1-indexed page number.

    Returns:
        The page's extracted text, or an empty string if it has none.
    """
    import fitz

    with fitz.open(pdf_path) as doc:
        return doc[page - 1].get_text() or ""


def extract_page(
    client: object,
    settings: Settings,
    pdf_path: Path,
    carrier_id: str,
    carrier_name: str,
    page: int,
) -> PageExtraction:
    """Send one page image to the model and validate what comes back.

    Args:
        client: An `anthropic.Anthropic` instance.
        settings: Application settings.
        pdf_path: The source document.
        carrier_id: Owning carrier.
        carrier_name: Display name, used only to orient the model.
        page: 1-indexed page number.

    Returns:
        The validated extraction for this page. A page the model reports as
        having no tables returns an empty result, which is the correct outcome
        for a page-classifier false positive.
    """
    image_b64 = base64.standard_b64encode(
        render_page_png(pdf_path, page, settings.extraction_dpi)
    ).decode("ascii")

    # The page's own text layer, used only to verify that a quoted excerpt
    # actually appears on the page. It is never fed to the model: the model
    # must read the image, so that a citation is anchored to what a person
    # would see rather than to a text layer that may not match the rendering.
    page_text = extract_page_text(pdf_path, page)

    response = client.messages.parse(  # type: ignore[attr-defined]
        model=settings.extraction_model,
        max_tokens=settings.extraction_max_tokens,
        system=TABLE_EXTRACTION_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": table_extraction_user_prompt(carrier_name, page),
                    },
                ],
            }
        ],
        output_format=PageTables,
    )

    result = PageExtraction(
        carrier_id=carrier_id,
        doc_id=pdf_path.name,
        page=page,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    parsed: PageTables | None = response.parsed_output
    if parsed is None:
        # Structured outputs guarantee shape, so a null parse means the turn
        # ended without a usable object -- a refusal or a token cutoff. Recorded
        # rather than retried silently, so the report shows a real gap.
        result.anomalies.append(
            ExtractionAnomaly(
                carrier_id=carrier_id,
                doc_id=pdf_path.name,
                page=page,
                severity="rejected",
                kind="no_parsed_output",
                detail=f"model returned no structured output "
                f"(stop_reason={response.stop_reason})",
            )
        )
        return result

    for chart in parsed.build_charts:
        result.build_entries += _validate_build_chart(chart, result)

    for table in parsed.threshold_tables:
        result.threshold_tables.append(table.model_dump())

    result.condition_rules += _validate_condition_rules(parsed, result, page_text)

    return result


def _validate_condition_rules(
    parsed: PageTables,
    result: PageExtraction,
    page_text: str,
) -> list[ConditionRule]:
    """Validate extracted condition rules and convert them to stored rows.

    Two checks matter here, and they guard different things.

    The rate class check guards the verdict: `best_available_class` is what a
    carrier comparison is built from, so a label that does not map onto the
    ladder makes the rule unusable and it is rejected.

    The excerpt check guards the citation. `source_excerpt` is rendered to the
    user as the evidence behind a claim, so it is verified to actually occur in
    the page's own text. An excerpt the model composed rather than copied is a
    fabricated citation, which the brief treats as a blocking bug -- so a rule
    whose excerpt cannot be found is rejected outright rather than shown with a
    caveat.

    Args:
        parsed: The model's structured output for this page.
        result: The page result, mutated to collect anomalies.
        page_text: The page's own extracted text, used to verify excerpts.

    Returns:
        Rules that passed validation.
    """
    rules: list[ConditionRule] = []
    normalized_page = _normalize_whitespace(page_text)

    def anomaly(severity: str, kind: str, detail: str) -> None:
        result.anomalies.append(
            ExtractionAnomaly(
                carrier_id=result.carrier_id,
                doc_id=result.doc_id,
                page=result.page,
                severity=severity,  # type: ignore[arg-type]
                kind=kind,
                detail=detail,
            )
        )

    for extracted in parsed.condition_rules:
        if extracted.condition == "other":
            anomaly(
                "warning",
                "unclassified_condition",
                f"rule under heading {extracted.heading!r} did not match the "
                f"known condition vocabulary",
            )
            continue

        canonical = normalize_rate_class(
            result.carrier_id, extracted.best_available_class
        )
        if canonical is None:
            anomaly(
                "rejected",
                "unmapped_best_class",
                f"{extracted.condition}: cannot map best available class "
                f"{extracted.best_available_class!r} onto the canonical ladder",
            )
            continue

        excerpt = _normalize_whitespace(extracted.source_excerpt)
        if excerpt and excerpt not in normalized_page:
            anomaly(
                "rejected",
                "excerpt_not_on_page",
                f"{extracted.condition}: source_excerpt does not appear in the "
                f"page text, so the citation cannot be verified: {excerpt[:120]!r}",
            )
            continue

        try:
            rules.append(
                ConditionRule(
                    carrier_id=result.carrier_id,
                    doc_id=result.doc_id,
                    page=result.page,
                    condition=extracted.condition,
                    criteria=extracted.criteria,
                    best_available_class=extracted.best_available_class.strip(),
                    canonical_best_class=canonical,  # type: ignore[arg-type]
                    disqualifiers=extracted.disqualifiers,
                    source_excerpt=extracted.source_excerpt.strip(),
                )
            )
        except Exception as exc:
            anomaly(
                "rejected",
                "schema_validation_failed",
                f"{extracted.condition}: {exc}",
            )

    return rules


def _normalize_whitespace(text: str) -> str:
    """Collapse whitespace so extracted text can be substring-matched.

    PDF text extraction breaks lines wherever the renderer wrapped them, so a
    sentence that reads as one phrase on the page comes back split. Without
    this, every excerpt check would fail on a line break.
    """
    return re.sub(r"\s+", " ", text).strip()


def _validate_build_chart(chart: object, result: PageExtraction) -> list[BuildChartEntry]:
    """Apply per-row semantic checks and convert surviving rows to entries.

    Args:
        chart: A `BuildChart` from the model.
        result: The page result, mutated to collect anomalies.

    Returns:
        Entries for the rows that passed validation.
    """
    entries: list[BuildChartEntry] = []
    carrier_labels = known_labels(result.carrier_id)

    def anomaly(severity: str, kind: str, detail: str) -> None:
        result.anomalies.append(
            ExtractionAnomaly(
                carrier_id=result.carrier_id,
                doc_id=result.doc_id,
                page=result.page,
                severity=severity,  # type: ignore[arg-type]
                kind=kind,
                detail=detail,
            )
        )

    # A header carrying labels this carrier does not publish is the signature of
    # a misread header row. Warned rather than rejected, because the individual
    # cells are still checked against the mapping table below.
    for label in chart.rate_class_labels:  # type: ignore[attr-defined]
        if normalize_rate_class(result.carrier_id, label) is None:
            anomaly(
                "warning",
                "unknown_rate_class_header",
                f"header label {label!r} is not a published class for this "
                f"carrier (known: {sorted(carrier_labels)})",
            )

    for row in chart.rows:  # type: ignore[attr-defined]
        height = parse_height_label(row.height_label)
        if height is None:
            anomaly(
                "rejected",
                "unparseable_height",
                f"could not read a plausible height from {row.height_label!r}",
            )
            continue

        # Weight limits must not improve as the rate class gets stricter.
        # Columns print best-to-worst, so the numbers must be non-decreasing
        # left to right. A violation means the columns are not what they claim.
        weights = [c.max_weight_lbs for c in row.cells if c.max_weight_lbs is not None]
        if weights != sorted(weights):
            anomaly(
                "warning",
                "non_monotonic_row",
                f"{row.height_label} {row.sex}: weight limits {weights} do not "
                f"increase left to right; columns may be shifted",
            )

        for cell in row.cells:
            if cell.max_weight_lbs is None:
                anomaly(
                    "warning",
                    "null_cell",
                    f"{row.height_label} {row.sex} / {cell.rate_class}: "
                    f"cell reported as unreadable",
                )
                continue

            canonical = normalize_rate_class(result.carrier_id, cell.rate_class)
            if canonical is None:
                anomaly(
                    "rejected",
                    "unmapped_rate_class",
                    f"{row.height_label} {row.sex}: cannot map rate class "
                    f"{cell.rate_class!r} onto the canonical ladder",
                )
                continue

            try:
                entries.append(
                    BuildChartEntry(
                        carrier_id=result.carrier_id,
                        doc_id=result.doc_id,
                        page=result.page,
                        height_inches=height,
                        rate_class=cell.rate_class.strip(),
                        canonical_class=canonical,  # type: ignore[arg-type]
                        max_weight_lbs=cell.max_weight_lbs,
                        gender=row.sex,
                    )
                )
            except Exception as exc:
                # Reached when a value passes the model's schema but fails the
                # storage schema's tighter bounds, e.g. a 900lb limit.
                anomaly(
                    "rejected",
                    "schema_validation_failed",
                    f"{row.height_label} {row.sex} / {cell.rate_class}: {exc}",
                )

    return entries


def check_monotonic_by_height(
    entries: list[BuildChartEntry],
) -> list[ExtractionAnomaly]:
    """Verify weight limits rise with height within each class and sex.

    This is the cross-page check, and the one most likely to catch a shifted
    column. A shift keeps every row internally plausible, so it survives the
    per-row checks; what it does not survive is comparison against the rows
    above and below it, which came from a different page or a different part of
    the chart.

    Args:
        entries: Validated entries for one document.

    Returns:
        One warning per violation found.
    """
    anomalies: list[ExtractionAnomaly] = []
    grouped: dict[tuple[str, str, str], list[BuildChartEntry]] = {}
    for entry in entries:
        grouped.setdefault(
            (entry.carrier_id, entry.rate_class, entry.gender), []
        ).append(entry)

    for (carrier_id, rate_class, gender), group in grouped.items():
        ordered = sorted(group, key=lambda e: e.height_inches)
        for previous, current in zip(ordered, ordered[1:]):
            if current.max_weight_lbs < previous.max_weight_lbs:
                anomalies.append(
                    ExtractionAnomaly(
                        carrier_id=carrier_id,
                        doc_id=current.doc_id,
                        page=current.page,
                        severity="warning",
                        kind="non_monotonic_by_height",
                        detail=(
                            f"{rate_class} ({gender}): limit falls from "
                            f"{previous.max_weight_lbs}lb at "
                            f"{previous.height_inches}in to "
                            f"{current.max_weight_lbs}lb at "
                            f"{current.height_inches}in"
                        ),
                    )
                )
    return anomalies


def deduplicate_entries(entries: list[BuildChartEntry]) -> list[BuildChartEntry]:
    """Collapse duplicate rows arising from repeated headers across a split.

    When a chart splits across pages the header row repeats, and a model may
    transcribe a boundary row on both pages. Keeping the first occurrence
    preserves the page citation of the page the row actually printed on.

    Args:
        entries: Entries for one document, in page order.

    Returns:
        Entries with duplicates removed, order preserved.
    """
    seen: set[tuple[int, str, str]] = set()
    unique: list[BuildChartEntry] = []
    for entry in entries:
        key = (entry.height_inches, entry.rate_class, entry.gender)
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique
