"""Tests for prose extraction and chunking.

These cover the failures that would corrupt results silently rather than raise.
A wrong page number does not crash anything -- it produces a citation that looks
authoritative and points at the wrong place, which is worse than an error. A
build chart weight leaking into a prose chunk does not crash anything either; it
produces a retrievable string that reads like a rule and is not one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingest.extract_text import (
    chunk_document,
    estimate_tokens,
    extract_lines,
    group_into_sections,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_DIR = REPO_ROOT / "corpus"
GROUND_TRUTH_DIR = REPO_ROOT / "backend" / "eval" / "ground_truth"

CARRIERS = [
    ("northstar", "Field Underwriting Guide"),
    ("cardinal", "Underwriting Reference Manual"),
    ("meridian", "Agent Field Guide to Underwriting"),
    ("granite", "Life Underwriting Guidelines"),
]


def _pdf(carrier_id: str) -> Path:
    path = CORPUS_DIR / f"{carrier_id}_underwriting_guide.pdf"
    if not path.exists():
        pytest.skip(f"corpus not generated; run tools/generate_corpus.py ({path})")
    return path


def _ground_truth(carrier_id: str) -> dict:
    path = GROUND_TRUTH_DIR / f"{carrier_id}.json"
    if not path.exists():
        pytest.skip("ground truth not generated; run tools/generate_corpus.py")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("carrier_id,doc_title", CARRIERS)
def test_chunks_are_produced(carrier_id: str, doc_title: str) -> None:
    """Every guide yields chunks, and every chunk has non-empty text."""
    chunks = chunk_document(_pdf(carrier_id), carrier_id, doc_title)
    assert chunks, f"{carrier_id} produced no chunks"
    assert all(c.text.strip() for c in chunks)
    assert all(c.carrier_id == carrier_id for c in chunks)


@pytest.mark.parametrize("carrier_id,doc_title", CARRIERS)
def test_condition_chunks_land_on_the_documented_page(
    carrier_id: str, doc_title: str
) -> None:
    """A condition's chunk must cite the page its heading actually printed on.

    This is the citation-correctness invariant, checked against page numbers
    recovered from the rendered PDF rather than against the generator's
    intentions. If chunking ever drifts, this fails instead of quietly emitting
    plausible-looking wrong citations.
    """
    truth = _ground_truth(carrier_id)
    chunks = chunk_document(_pdf(carrier_id), carrier_id, doc_title)
    by_section = {c.section: c for c in chunks}

    for condition in truth["conditions"]:
        heading = condition["heading"]
        assert heading in by_section, (
            f"{carrier_id}: no chunk for section {heading!r}; "
            f"got {sorted(by_section)}"
        )
        chunk = by_section[heading]
        expected_page = condition["prose_page"]
        assert chunk.page_start <= expected_page <= chunk.page_end, (
            f"{carrier_id}/{heading}: chunk cites {chunk.page_label} "
            f"but the heading is on page {expected_page}"
        )


@pytest.mark.parametrize("carrier_id,doc_title", CARRIERS)
def test_build_chart_values_do_not_leak_into_prose(
    carrier_id: str, doc_title: str
) -> None:
    """Prose chunks must contain no build chart rows.

    Table content in a prose chunk is the specific failure section 4 of the
    brief warns about: a chunked build chart loses the row-to-column
    relationship, so any weight retrieved from it is unmoored from the height
    and rate class it belonged to.

    The check looks for the printed height labels, which appear only inside the
    build chart. Weight figures alone would be a poor probe because a number
    like 140 also occurs legitimately in blood-pressure prose.
    """
    chunks = chunk_document(_pdf(carrier_id), carrier_id, doc_title)
    blob = " ".join(c.text for c in chunks)

    for height in (58, 66, 70, 78):
        label = f"{height // 12}' {height % 12}\""
        assert label not in blob, (
            f"{carrier_id}: build chart row label {label!r} leaked into prose"
        )


@pytest.mark.parametrize("carrier_id,doc_title", CARRIERS)
def test_list_bullets_are_not_treated_as_headings(
    carrier_id: str, doc_title: str
) -> None:
    """Symbol-font list bullets must not become section headings.

    List bullets render in ZapfDingbats at the same point size as a subheading.
    Classifying by size alone promoted every bullet to a heading, which split
    each condition's disqualifier list into single-line chunks and detached them
    from the rule they qualify. Section headings must carry real words.
    """
    lines = extract_lines(_pdf(carrier_id))
    sections = group_into_sections(lines)

    for section in sections:
        assert len(section.heading) > 2, f"suspicious heading {section.heading!r}"
        assert any(ch.isalpha() for ch in section.heading), (
            f"heading {section.heading!r} has no letters, likely a glyph"
        )


@pytest.mark.parametrize("carrier_id,doc_title", CARRIERS)
def test_disqualifiers_stay_with_their_condition(
    carrier_id: str, doc_title: str
) -> None:
    """A condition's disqualifiers belong in the same chunk as its criteria.

    Retrieval that returns the qualifying language without the disqualifiers
    would let synthesis state a best-available class while omitting the reason
    the applicant does not reach it.
    """
    truth = _ground_truth(carrier_id)
    chunks = chunk_document(_pdf(carrier_id), carrier_id, doc_title)
    by_section = {c.section: c for c in chunks}

    for condition in truth["conditions"]:
        chunk = by_section.get(condition["heading"])
        assert chunk is not None
        for disqualifier in condition["disqualifiers"]:
            assert disqualifier in chunk.text, (
                f"{carrier_id}/{condition['heading']}: disqualifier "
                f"{disqualifier!r} missing from its chunk"
            )


@pytest.mark.parametrize("carrier_id,doc_title", CARRIERS)
def test_page_range_is_ordered_and_within_the_document(
    carrier_id: str, doc_title: str
) -> None:
    """Page ranges must be ordered and must exist in the document."""
    import fitz

    with fitz.open(_pdf(carrier_id)) as doc:
        page_count = doc.page_count

    for chunk in chunk_document(_pdf(carrier_id), carrier_id, doc_title):
        assert 1 <= chunk.page_start <= chunk.page_end <= page_count


@pytest.mark.parametrize("carrier_id,doc_title", CARRIERS)
def test_chunks_respect_the_token_ceiling(carrier_id: str, doc_title: str) -> None:
    """No chunk may exceed the configured maximum.

    The floor is deliberately not asserted. Chunks here are aligned to section
    boundaries, and a section in an underwriting guide is one condition. Padding
    a short section up to a token target would mean merging two conditions into
    one chunk, which is exactly how a carrier's diabetes rule ends up cited for
    a hypertension question. See docs/FINDINGS.md.
    """
    for chunk in chunk_document(
        _pdf(carrier_id), carrier_id, doc_title, max_tokens=800
    ):
        assert estimate_tokens(chunk.text) <= 800 * 1.1


@pytest.mark.parametrize("carrier_id,doc_title", CARRIERS)
def test_chunk_ids_are_stable_across_runs(carrier_id: str, doc_title: str) -> None:
    """Re-ingesting the same document must produce the same chunk ids.

    Citations recorded by an eval run reference chunk ids. If a rebuild
    reshuffled them, historical results would silently stop being comparable.
    """
    first = chunk_document(_pdf(carrier_id), carrier_id, doc_title)
    second = chunk_document(_pdf(carrier_id), carrier_id, doc_title)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len(set(c.chunk_id for c in first)) == len(first), "duplicate chunk ids"


def test_running_header_is_not_chunked() -> None:
    """The repeated page header must not appear in any chunk.

    It prints on every page. Left in, it would appear in most chunks and pull
    them all toward each other in embedding space.
    """
    chunks = chunk_document(_pdf("northstar"), "northstar", "Field Underwriting Guide")
    for chunk in chunks:
        assert "SYNTHETIC SAMPLE - NOT A REAL CARRIER DOCUMENT" not in chunk.text
        assert "Edition 2026.1)" not in chunk.text
