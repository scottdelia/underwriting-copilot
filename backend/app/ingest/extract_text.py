"""Extract and chunk prose from carrier PDFs, preserving page numbers.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not extract tables. Section 4 of the brief is explicit that running a
build chart through a prose chunker destroys the row-to-column relationship and
produces confidently wrong weight limits. So table regions are located and
excluded here, and handled separately by the vision extractor. Prose and tables
are two different extraction problems and mixing them is the failure mode.

HOW PROSE IS SEPARATED FROM EVERYTHING ELSE
-------------------------------------------
Rather than guess from the text alone, this uses two structural signals that
PyMuPDF exposes and that survive on real carrier documents as well as generated
ones:

1. Table geometry. `page.find_tables()` returns bounding boxes. Any text line
   substantially inside one is table content, not prose, and is dropped.
2. Font size. Guides render headings larger than body text and render running
   headers, footers, and footnotes smaller. Thresholds on span size separate
   headings from body and drop page furniture, which would otherwise pollute
   every chunk with the same repeated header string.

Both are heuristics. Where they fail is recorded in docs/FINDINGS.md rather
than papered over.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from app.models.schemas import ProseChunk

logger = logging.getLogger(__name__)

# --- Font size thresholds, in points -------------------------------------
# Calibrated against the corpus: headings render at 12pt and 15pt, body at
# 9.5pt, table cells at 8pt, and running headers, footers, and footnotes at
# 7.5-8.5pt. The gaps between those bands are wide, so the exact cut points are
# not delicate. They are named constants rather than literals so that pointing
# this at a differently-styled document set is a config change, not a hunt.
HEADING_MIN_SIZE = 11.5
BODY_MIN_SIZE = 8.8

# --- Page furniture margins, in points from the page edge -----------------
# Running headers and page numbers sit in the margins. Excluding by geometry as
# well as by size is belt and braces: a guide that sets its running header in
# body-size type would otherwise contribute the same line to every chunk.
HEADER_MARGIN_PT = 58.0
FOOTER_MARGIN_PT = 58.0

# A line is treated as table content when this much of its area falls inside a
# detected table. Well below 1.0 because detected table boxes are slightly
# tighter than the text they contain.
TABLE_OVERLAP_THRESHOLD = 0.5

# Symbol fonts carry no readable text. List bullets in particular are drawn as
# glyphs in these faces at heading point sizes, so without this filter every
# bullet in a disqualifier list is classified as a heading and shatters the
# section it belongs to. Matched as substrings because embedded fonts are
# commonly subset with a prefix, e.g. "ABCDEF+ZapfDingbats".
SYMBOL_FONT_MARKERS = ("zapfdingbats", "dingbat", "symbol", "wingding")

# A heading has to contain real words. Combined with the symbol-font filter this
# is belt and braces against single-glyph or numeric lines being promoted.
_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def _is_symbol_font(font_name: str) -> bool:
    """True when a font carries glyphs rather than readable text."""
    lowered = font_name.lower()
    return any(marker in lowered for marker in SYMBOL_FONT_MARKERS)

# Rough token estimate. The brief specifies chunk sizes in tokens, but calling a
# tokenizer API for every candidate boundary during ingestion would be slow and
# would require a key for a step that otherwise needs none. English prose runs
# close to four characters per token, which is accurate enough to hold chunks
# inside the intended band. The write-up records this as an approximation.
CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    """Estimate the token count of a string.

    Args:
        text: The text to measure.

    Returns:
        An approximate token count. Intended for chunk sizing only, never for
        billing or context-limit decisions.
    """
    return max(1, round(len(text) / CHARS_PER_TOKEN))


@dataclass
class TextLine:
    """One rendered line of prose, with the page it appeared on."""

    text: str
    page: int
    size: float
    is_heading: bool


def _rect_overlap_ratio(inner: fitz.Rect, outer: fitz.Rect) -> float:
    """Fraction of `inner`'s area that lies inside `outer`.

    Args:
        inner: The candidate rectangle, typically a text line.
        outer: The containing rectangle, typically a detected table.

    Returns:
        A value in [0.0, 1.0]. Returns 0.0 for a degenerate `inner`.
    """
    if inner.is_empty or inner.get_area() <= 0:
        return 0.0
    intersection = inner & outer
    if intersection.is_empty:
        return 0.0
    return intersection.get_area() / inner.get_area()


def extract_lines(pdf_path: Path) -> list[TextLine]:
    """Extract prose lines from a PDF, excluding tables and page furniture.

    Args:
        pdf_path: Path to the carrier guide.

    Returns:
        Prose lines in reading order, each tagged with its 1-indexed page and
        whether it is a heading.

    Raises:
        FileNotFoundError: If the PDF does not exist.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"corpus document not found: {pdf_path}")

    lines: list[TextLine] = []
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            page_no = page_index + 1

            # Locate tables first so their text can be excluded wholesale.
            try:
                table_rects = [fitz.Rect(t.bbox) for t in page.find_tables()]
            except Exception:  # pragma: no cover - detector is best-effort
                logger.warning("table detection failed on %s p%d", pdf_path.name, page_no)
                table_rects = []

            content_top = HEADER_MARGIN_PT
            content_bottom = page.rect.height - FOOTER_MARGIN_PT

            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    # Drop symbol-font spans before anything else. They render
                    # as list bullets at heading point sizes and would
                    # otherwise be both promoted to headings and concatenated
                    # into the body text as junk characters.
                    spans = [
                        s
                        for s in line.get("spans", [])
                        if s["text"].strip() and not _is_symbol_font(s["font"])
                    ]
                    if not spans:
                        continue

                    rect = fitz.Rect(line["bbox"])

                    # Drop running headers and page numbers by geometry.
                    if rect.y1 < content_top or rect.y0 > content_bottom:
                        continue

                    # Drop anything sitting inside a detected table.
                    if any(
                        _rect_overlap_ratio(rect, t) >= TABLE_OVERLAP_THRESHOLD
                        for t in table_rects
                    ):
                        continue

                    # The largest span drives classification; a line with an
                    # inline bold run should not be mistaken for a heading.
                    size = max(s["size"] for s in spans)
                    if size < BODY_MIN_SIZE:
                        continue  # footnote, caption, or stray furniture

                    text = " ".join(s["text"] for s in spans)
                    text = re.sub(r"\s+", " ", text).strip()
                    if not text:
                        continue

                    lines.append(
                        TextLine(
                            text=text,
                            page=page_no,
                            size=size,
                            is_heading=(
                                size >= HEADING_MIN_SIZE
                                and _WORD_RE.search(text) is not None
                            ),
                        )
                    )
    return lines


@dataclass
class Section:
    """A run of body lines under a single heading."""

    heading: str
    lines: list[TextLine]


def group_into_sections(lines: list[TextLine]) -> list[Section]:
    """Group prose lines into sections delimited by headings.

    Section headings are attached to every chunk as metadata because they carry
    most of the topical signal in an underwriting guide. A chunk reading "an
    A1c of 7.0 through 7.9 limits the best available class to Standard" is far
    easier to retrieve correctly when it also carries "Diabetes Mellitus,
    Type 2".

    Args:
        lines: Output of extract_lines().

    Returns:
        Sections in document order. Body text appearing before the first
        heading is collected under "Front Matter".
    """
    sections: list[Section] = []
    current = Section(heading="Front Matter", lines=[])

    for line in lines:
        if line.is_heading:
            if current.lines:
                sections.append(current)
            current = Section(heading=line.text, lines=[])
        else:
            current.lines.append(line)

    if current.lines:
        sections.append(current)
    return sections


def _chunk_id(carrier_id: str, doc_id: str, section: str, ordinal: int) -> str:
    """Build a stable, collision-resistant chunk identifier.

    Deriving the id from content coordinates rather than a counter means
    re-running ingestion produces the same ids, so an index rebuild does not
    invalidate every citation recorded in a previous eval run.
    """
    raw = f"{carrier_id}|{doc_id}|{section}|{ordinal}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def chunk_section(
    section: Section,
    carrier_id: str,
    doc_id: str,
    doc_title: str,
    target_tokens: int,
    max_tokens: int,
    min_tokens: int,
    start_ordinal: int,
) -> list[ProseChunk]:
    """Split one section into chunks that respect line boundaries.

    Chunks are accumulated line by line and closed once they reach the target
    size, so a chunk never splits mid-line and the set of pages a chunk covers
    is exactly the set of pages its lines came from. That is what makes the
    page citation on a chunk true rather than approximate.

    A trailing chunk below `min_tokens` is merged back into its predecessor
    rather than emitted, because a two-sentence fragment retrieves poorly and
    tends to win on similarity for the wrong reasons.

    Args:
        section: The section to split.
        carrier_id: Owning carrier.
        doc_id: Source document filename.
        doc_title: Human-readable document title.
        target_tokens: Preferred chunk size.
        max_tokens: Hard ceiling; a single line longer than this is emitted alone.
        min_tokens: Below this a trailing chunk is merged backwards.
        start_ordinal: Running chunk counter, for stable ids.

    Returns:
        The chunks produced for this section, in document order.
    """
    chunks: list[ProseChunk] = []
    buffer: list[TextLine] = []
    ordinal = start_ordinal

    def flush() -> None:
        """Emit the buffered lines as one chunk and reset the buffer."""
        nonlocal buffer, ordinal
        if not buffer:
            return
        text = " ".join(line.text for line in buffer)
        chunks.append(
            ProseChunk(
                chunk_id=_chunk_id(carrier_id, doc_id, section.heading, ordinal),
                carrier_id=carrier_id,
                doc_id=doc_id,
                doc_title=doc_title,
                section=section.heading,
                page_start=min(line.page for line in buffer),
                page_end=max(line.page for line in buffer),
                text=text,
            )
        )
        ordinal += 1
        buffer = []

    for line in section.lines:
        prospective = estimate_tokens(
            " ".join(item.text for item in [*buffer, line])
        )
        # Close the current chunk before adding a line that would overshoot the
        # ceiling, unless the buffer is empty and this single line is oversized.
        if buffer and prospective > max_tokens:
            flush()
        buffer.append(line)
        if estimate_tokens(" ".join(item.text for item in buffer)) >= target_tokens:
            flush()

    flush()

    # Merge an undersized tail back into the previous chunk.
    if len(chunks) > 1 and estimate_tokens(chunks[-1].text) < min_tokens:
        tail = chunks.pop()
        prev = chunks[-1]
        chunks[-1] = prev.model_copy(
            update={
                "text": f"{prev.text} {tail.text}",
                "page_end": max(prev.page_end, tail.page_end),
            }
        )

    return chunks


def chunk_document(
    pdf_path: Path,
    carrier_id: str,
    doc_title: str,
    target_tokens: int = 600,
    max_tokens: int = 800,
    min_tokens: int = 120,
) -> list[ProseChunk]:
    """Extract and chunk one carrier guide end to end.

    Args:
        pdf_path: Path to the carrier guide.
        carrier_id: Owning carrier.
        doc_title: Human-readable document title.
        target_tokens: Preferred chunk size.
        max_tokens: Hard ceiling per chunk.
        min_tokens: Minimum size for a standalone trailing chunk.

    Returns:
        Every prose chunk in the document, in reading order.
    """
    lines = extract_lines(pdf_path)
    sections = group_into_sections(lines)

    chunks: list[ProseChunk] = []
    for section in sections:
        chunks += chunk_section(
            section=section,
            carrier_id=carrier_id,
            doc_id=pdf_path.name,
            doc_title=doc_title,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
            min_tokens=min_tokens,
            start_ordinal=len(chunks),
        )

    logger.info(
        "chunked %s: %d lines, %d sections, %d chunks",
        pdf_path.name,
        len(lines),
        len(sections),
        len(chunks),
    )
    return chunks
