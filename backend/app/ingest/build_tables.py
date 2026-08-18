"""Run the vision extraction pipeline over the corpus and persist the results.

Run from the backend directory:

    python -m app.ingest.build_tables

This is the step that costs money. It classifies pages, sends only the ones
that appear to hold tables to the model, validates what comes back, and writes
the survivors plus every rejection to SQLite.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.ingest.build_index import CORPUS_DOCUMENTS
from app.ingest.classify_pages import classify_document
from app.ingest.extract_tables import (
    PageExtraction,
    check_monotonic_by_height,
    deduplicate_entries,
    extract_page,
)
from app.ingest.store import (
    counts,
    initialize,
    insert_anomalies,
    insert_build_entries,
    insert_condition_rules,
    insert_threshold_tables,
)
from app.models.extraction import ExtractionAnomaly

logger = logging.getLogger(__name__)

# Published Sonnet rates, used only to report what a run cost. Reporting real
# spend is a requirement of the write-up, and a number computed from token
# counts beats a number remembered afterwards.
USD_PER_INPUT_TOKEN = 3.00 / 1_000_000
USD_PER_OUTPUT_TOKEN = 15.00 / 1_000_000


def extract_document(
    client: Any,
    settings: Settings,
    document: dict[str, str],
) -> list[PageExtraction]:
    """Classify and extract every table-bearing page of one document.

    Args:
        client: An `anthropic.Anthropic` instance.
        settings: Application settings.
        document: An entry from CORPUS_DOCUMENTS.

    Returns:
        One result per page sent to the model.

    Raises:
        FileNotFoundError: If the document is missing from the corpus.
    """
    path = settings.corpus_dir / document["filename"]
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Generate the corpus first:\n"
            f"    python tools/generate_corpus.py"
        )

    pages = [c.page for c in classify_document(path) if c.has_structured_content]
    logger.info("%s: extracting pages %s", document["filename"], pages)

    results: list[PageExtraction] = []
    for page in pages:
        started = time.perf_counter()
        result = extract_page(
            client=client,
            settings=settings,
            pdf_path=path,
            carrier_id=document["carrier_id"],
            carrier_name=document["carrier_name"],
            page=page,
        )
        elapsed = time.perf_counter() - started
        logger.info(
            "  p%d: %d build rows, %d condition rules, %d anomalies (%.1fs)",
            page,
            len(result.build_entries),
            len(result.condition_rules),
            len(result.anomalies),
            elapsed,
        )
        results.append(result)
    return results


def build(settings: Settings | None = None, *, reset: bool = True) -> dict[str, Any]:
    """Extract every document's tables and write them to the structured store.

    Args:
        settings: Application settings. Defaults to the process settings.
        reset: Clear the store before writing. On by default; a partial
            re-extraction layered over a previous run produces a store that
            matches no single version of the corpus.

    Returns:
        A run manifest, also written to `data/extraction_manifest.json` so an
        eval result can name the extraction run that produced its data.

    Raises:
        RuntimeError: If no Anthropic API key is configured.
    """
    import anthropic

    settings = settings or get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Table extraction needs it. Add it to "
            "backend/.env and re-run."
        )

    started = time.perf_counter()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    initialize(settings.sqlite_path, reset=reset)

    per_carrier: dict[str, dict[str, int]] = {}
    total_in = total_out = 0
    all_anomalies: list[ExtractionAnomaly] = []

    for document in CORPUS_DOCUMENTS:
        results = extract_document(client, settings, document)

        # A chart split across pages repeats its header, so a boundary row can
        # arrive twice. Deduplicate per document, before the store sees it.
        entries = deduplicate_entries(
            [e for r in results for e in r.build_entries]
        )
        rules = [rule for r in results for rule in r.condition_rules]
        anomalies = [a for r in results for a in r.anomalies]

        # The cross-page check runs on the assembled document, because that is
        # the only level at which a shifted column becomes visible.
        anomalies += check_monotonic_by_height(entries)

        thresholds = [(r.page, t) for r in results for t in r.threshold_tables]

        inserted_entries = insert_build_entries(settings.sqlite_path, entries)
        inserted_rules = insert_condition_rules(settings.sqlite_path, rules)
        inserted_tables = insert_threshold_tables(
            settings.sqlite_path,
            document["carrier_id"],
            document["filename"],
            thresholds,
        )
        insert_anomalies(settings.sqlite_path, anomalies)
        all_anomalies += anomalies

        doc_in = sum(r.input_tokens for r in results)
        doc_out = sum(r.output_tokens for r in results)
        total_in += doc_in
        total_out += doc_out

        per_carrier[document["carrier_id"]] = {
            "pages_extracted": len(results),
            "build_entries": inserted_entries,
            "condition_rules": inserted_rules,
            "threshold_tables": inserted_tables,
            "anomalies": len(anomalies),
            "rejected": sum(1 for a in anomalies if a.severity == "rejected"),
            "input_tokens": doc_in,
            "output_tokens": doc_out,
        }
        logger.info("%s: %s", document["carrier_id"], per_carrier[document["carrier_id"]])

    cost = total_in * USD_PER_INPUT_TOKEN + total_out * USD_PER_OUTPUT_TOKEN
    manifest = {
        "model": settings.extraction_model,
        "dpi": settings.extraction_dpi,
        "prompt_version": "TABLE_EXTRACTION_SYSTEM v1",
        "per_carrier": per_carrier,
        "totals": {
            **counts(settings.sqlite_path),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "estimated_cost_usd": round(cost, 4),
            "seconds": round(time.perf_counter() - started, 1),
        },
        "anomaly_kinds": _anomaly_summary(all_anomalies),
    }

    path = settings.data_dir / "extraction_manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("extraction manifest written to %s", path)
    return manifest


def _anomaly_summary(anomalies: list[ExtractionAnomaly]) -> dict[str, int]:
    """Count anomalies by severity and kind, for the run manifest."""
    summary: dict[str, int] = {}
    for anomaly in anomalies:
        key = f"{anomaly.severity}:{anomaly.kind}"
        summary[key] = summary.get(key, 0) + 1
    return dict(sorted(summary.items()))


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Extract carrier tables with vision and persist them."
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Add to the existing store instead of clearing it first.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s"
    )
    manifest = build(reset=not args.no_reset)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
