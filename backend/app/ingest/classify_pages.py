"""Decide which pages contain tables and are worth sending to the vision model.

WHY CLASSIFY AT ALL
-------------------
Vision extraction costs money and time per page. Most pages of an underwriting
guide are prose. Classifying first means paying only for the pages that carry
tables.

The asymmetry matters when choosing how to combine signals. A false positive
costs one wasted API call. A false negative silently drops a build chart, and
the tool then abstains on questions it should be able to answer -- a failure
that looks like a retrieval problem and is really an ingestion problem. So the
signals are combined with OR, favouring recall, and disagreements are logged so
the balance can be re-examined rather than assumed.

TWO INDEPENDENT SIGNALS
-----------------------
1. `pdfplumber.find_tables()` -- ruling-line and alignment based. Strong on
   bordered tables, weaker on whitespace-aligned ones.
2. Digit density -- the fraction of visible characters that are digits. A build
   chart page is mostly numerals; a prose page is almost none. This catches
   tables with no ruling lines, which is exactly where the first signal is
   weakest.

They fail in different ways, which is the only reason having two is worth
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

_NON_SPACE = re.compile(r"\S")
_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class PageClassification:
    """The classification verdict and the evidence behind it."""

    page: int
    has_tables: bool
    table_count: int
    digit_density: float
    char_count: int
    signals: tuple[str, ...]

    @property
    def signal_disagreement(self) -> bool:
        """True when exactly one of the two signals fired.

        Not an error. It is the case worth looking at when tuning, because it
        marks the pages where the two detectors see different things.
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

            signals: list[str] = []
            if tables:
                signals.append("pdfplumber_tables")
            if (
                char_count >= MIN_CHARS_FOR_DENSITY
                and density >= DIGIT_DENSITY_THRESHOLD
            ):
                signals.append("digit_density")

            classification = PageClassification(
                page=page_no,
                has_tables=bool(signals),
                table_count=len(tables),
                digit_density=round(density, 4),
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

    table_pages = [c.page for c in results if c.has_tables]
    logger.info(
        "%s: %d/%d pages flagged as tabular %s",
        pdf_path.name,
        len(table_pages),
        len(results),
        table_pages,
    )
    return results


def table_pages(pdf_path: Path) -> list[int]:
    """Return just the 1-indexed page numbers that appear to contain tables."""
    return [c.page for c in classify_document(pdf_path) if c.has_tables]
