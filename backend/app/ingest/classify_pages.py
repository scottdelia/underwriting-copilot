"""Decide which pages hold structured underwriting content worth extracting.

WHY CLASSIFY AT ALL
-------------------
Vision extraction costs money and time per page. Most pages of an underwriting
guide are prose. Classifying first means paying only for the pages that carry
something worth extracting.

The asymmetry matters when choosing how to combine signals. A false positive
costs one wasted API call. A false negative silently drops data, and the tool
then abstains on questions it should be able to answer -- a failure that looks
like a retrieval problem and is really an ingestion problem. So the signals are
combined with OR, favouring recall.

WHY THERE ARE THREE SIGNALS AND NOT TWO
---------------------------------------
The first version of this module classified for *tables* and the extraction
pipeline rode on top of it. That silently lost two of eleven condition rules,
because a condition rule is prose: on the pages where a carrier states a rule
without printing a threshold table, there was no table to detect, so the page
was never sent and the rule was never extracted. Nothing failed; the data was
simply absent.

The lesson is that the classifier has to select for what is actually being
extracted, not for the most obvious instance of it. Hence the third signal.

1. `pdfplumber.find_tables()` -- ruling-line and alignment based. Strong on
   bordered tables, weaker on whitespace-aligned ones.
2. Digit density -- the fraction of visible characters that are digits. A build
   chart page is mostly numerals; a prose page is almost none. Catches tables
   with no ruling lines, which is where the first signal is weakest.
3. Underwriting rule language -- phrases that state an eligibility decision,
   such as "may be considered for" or "limits the best available class". These
   mark a page that carries a condition rule whether or not it prints a table.

They fail in different ways, which is the only reason having three is worth
anything.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# A page whose visible characters are at least this fraction digits is treated
# as tabular. Measured against the corpus: build chart pages run above 0.35,
# threshold table pages around 0.10-0.20, and prose pages below 0.03. The
# threshold sits in the gap, well clear of prose.
DIGIT_DENSITY_THRESHOLD = 0.06

# Below this many visible characters a page is too sparse to classify by
# density; a cover page with a single date would otherwise score high.
MIN_CHARS_FOR_DENSITY = 200

# Phrases that state an eligibility decision. These are the verbs of an
# underwriting guide: a page that states a rule almost always contains one,
# and a page of product descriptions or travel policy almost never does.
# Matched case-insensitively against the page's text layer.
RULE_LANGUAGE_RE = re.compile(
    r"(may be considered for"
    r"|may be offered"
    r"|best available class"
    r"|limits the best available"
    r"|is not eligible"
    r"|are not eligible"
    r"|will be rated"
    r"|rated Table"
    r"|acceptable at"
    r"|minimum rating is)",
    re.IGNORECASE,
)

# One stray phrase is not a rule section. Requiring two keeps a passing mention
# in an introduction from flagging a page of pure prose.
MIN_RULE_LANGUAGE_MATCHES = 2

_NON_SPACE = re.compile(r"\S")
_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class PageClassification:
    """The classification verdict and the evidence behind it."""

    page: int
    has_structured_content: bool
    table_count: int
    digit_density: float
    rule_language_matches: int
    char_count: int
    signals: tuple[str, ...]

    @property
    def signal_disagreement(self) -> bool:
        """True when exactly one signal fired.

        Not an error. It marks the pages where the detectors see different
        things, which is where the thresholds are worth re-examining.
        """
        return len(self.signals) == 1


def digit_density(text: str) -> float:
    """Fraction of a page's visible characters that are digits.

    Args:
        text: Extracted page text.

    Returns:
        A value in [0.0, 1.0]. Returns 0.0 for text with no visible characters.
    """
    visible = _NON_SPACE.findall(text)
    if not visible:
        return 0.0
    return len(_DIGIT.findall(text)) / len(visible)


def classify_document(pdf_path: Path) -> list[PageClassification]:
    """Classify every page of a document.

    Args:
        pdf_path: Path to the carrier guide.

    Returns:
        One classification per page, in page order.

    Raises:
        FileNotFoundError: If the document does not exist.
    """
    import pdfplumber

    if not pdf_path.exists():
        raise FileNotFoundError(f"corpus document not found: {pdf_path}")

    results: list[PageClassification] = []
    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages):
            page_no = index + 1

            try:
                tables = page.find_tables()
            except Exception:  # pragma: no cover - detector is best-effort
                logger.warning(
                    "pdfplumber table detection failed on %s p%d",
                    pdf_path.name,
                    page_no,
                )
                tables = []

            text = page.extract_text() or ""
            density = digit_density(text)
            char_count = len(_NON_SPACE.findall(text))
            rule_matches = len(RULE_LANGUAGE_RE.findall(text))

            signals: list[str] = []
            if tables:
                signals.append("pdfplumber_tables")
            if (
                char_count >= MIN_CHARS_FOR_DENSITY
                and density >= DIGIT_DENSITY_THRESHOLD
            ):
                signals.append("digit_density")
            if rule_matches >= MIN_RULE_LANGUAGE_MATCHES:
                signals.append("rule_language")

            classification = PageClassification(
                page=page_no,
                has_structured_content=bool(signals),
                table_count=len(tables),
                digit_density=round(density, 4),
                rule_language_matches=rule_matches,
                char_count=char_count,
                signals=tuple(signals),
            )
            if classification.signal_disagreement:
                logger.info(
                    "%s p%d: signals disagree (%s), density=%.3f, tables=%d",
                    pdf_path.name,
                    page_no,
                    classification.signals[0],
                    density,
                    len(tables),
                )
            results.append(classification)

    table_pages = [c.page for c in results if c.has_structured_content]
    logger.info(
        "%s: %d/%d pages flagged for extraction %s",
        pdf_path.name,
        len(table_pages),
        len(results),
        table_pages,
    )
    return results


def extraction_pages(pdf_path: Path) -> list[int]:
    """Return the 1-indexed pages that should be sent to the vision model."""
    return [c.page for c in classify_document(pdf_path) if c.has_structured_content]
