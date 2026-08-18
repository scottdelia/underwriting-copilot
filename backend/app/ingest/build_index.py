"""Build the Chroma prose index from the corpus.

Run from the backend directory:

    python -m app.ingest.build_index

Ingestion is deliberately a manual step. Section 2 of the brief puts automated
document ingestion out of scope, and there is a second reason to keep it manual:
re-indexing changes what every citation points at, so it should be an act
someone performs and records, not something that happens on a timer.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.ingest.embeddings import embedding_text, get_embedding_backend
from app.ingest.extract_text import chunk_document
from app.models.schemas import ProseChunk

logger = logging.getLogger(__name__)

PROSE_COLLECTION = "prose_chunks"

# Which documents make up the corpus, and what each one is called. Kept here
# rather than discovered by globbing so that adding a carrier is an explicit,
# reviewable change and a stray PDF dropped in the folder cannot silently
# become part of the index.
CORPUS_DOCUMENTS: list[dict[str, str]] = [
    {
        "carrier_id": "northstar",
        "carrier_name": "Northstar Mutual Life",
        "filename": "northstar_underwriting_guide.pdf",
        "doc_title": "Field Underwriting Guide",
    },
    {
        "carrier_id": "cardinal",
        "carrier_name": "Cardinal Assurance Company",
        "filename": "cardinal_underwriting_guide.pdf",
        "doc_title": "Underwriting Reference Manual",
    },
    {
        "carrier_id": "meridian",
        "carrier_name": "Meridian Life & Annuity",
        "filename": "meridian_underwriting_guide.pdf",
        "doc_title": "Agent Field Guide to Underwriting",
    },
    {
        "carrier_id": "granite",
        "carrier_name": "Granite Peak Financial Group",
        "filename": "granite_underwriting_guide.pdf",
        "doc_title": "Life Underwriting Guidelines",
    },
]

CARRIER_NAMES: dict[str, str] = {
    d["carrier_id"]: d["carrier_name"] for d in CORPUS_DOCUMENTS
}


def collect_chunks(settings: Settings) -> list[ProseChunk]:
    """Extract and chunk every document in the corpus.

    Args:
        settings: Application settings, for the corpus path and chunk sizes.

    Returns:
        Every prose chunk across all carriers.

    Raises:
        FileNotFoundError: If a document listed in CORPUS_DOCUMENTS is missing.
            Failing loudly is intentional: a silently short corpus would make
            the tool abstain on questions it should answer, which is a far more
            confusing failure than a missing file at ingest time.
    """
    chunks: list[ProseChunk] = []
    for doc in CORPUS_DOCUMENTS:
        path = settings.corpus_dir / doc["filename"]
        if not path.exists():
            raise FileNotFoundError(
                f"{path} is missing. Generate the corpus first:\n"
                f"    python tools/generate_corpus.py"
            )
        doc_chunks = chunk_document(
            pdf_path=path,
            carrier_id=doc["carrier_id"],
            doc_title=doc["doc_title"],
            target_tokens=settings.chunk_target_tokens,
            max_tokens=settings.chunk_max_tokens,
            min_tokens=settings.chunk_min_tokens,
        )
        chunks += doc_chunks
        logger.info("%s: %d chunks", doc["filename"], len(doc_chunks))
    return chunks


def _chunk_metadata(chunk: ProseChunk) -> dict[str, Any]:
    """Flatten a chunk into Chroma-compatible scalar metadata.

    Chroma metadata values must be scalars, so the citation fields are stored
    individually rather than as a nested object.
    """
    return {
        "carrier_id": chunk.carrier_id,
        "carrier_name": CARRIER_NAMES.get(chunk.carrier_id, chunk.carrier_id),
        "doc_id": chunk.doc_id,
        "doc_title": chunk.doc_title,
        "section": chunk.section,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "page_label": chunk.page_label,
    }


def build(settings: Settings | None = None, *, reset: bool = True) -> dict[str, Any]:
    """Build the prose index from scratch.

    Args:
        settings: Application settings. Defaults to the process settings.
        reset: Drop any existing collection first. On by default because a
            partial rebuild over a stale collection produces an index that
            matches no single version of the corpus, which is the kind of drift
            that silently degrades retrieval without failing anything.

    Returns:
        A summary dict with counts and timings, also written to disk as
        `data/index_manifest.json` so an eval run can record which index
        produced its numbers.
    """
    import chromadb

    settings = settings or get_settings()
    started = time.perf_counter()

    chunks = collect_chunks(settings)
    if not chunks:
        raise RuntimeError("no chunks produced; the corpus appears to be empty")

    backend = get_embedding_backend(settings)

    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))

    if reset:
        try:
            client.delete_collection(PROSE_COLLECTION)
            logger.info("dropped existing collection %s", PROSE_COLLECTION)
        except Exception:
            pass  # Collection did not exist; nothing to drop.

    # Cosine distance, not the L2 default. The embedding models here produce
    # vectors whose direction carries the meaning and whose magnitude does not,
    # so L2 would let chunk length influence ranking.
    collection = client.get_or_create_collection(
        name=PROSE_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    texts = [
        embedding_text(
            CARRIER_NAMES.get(c.carrier_id, c.carrier_id), c.section, c.text
        )
        for c in chunks
    ]
    embed_started = time.perf_counter()
    vectors = backend.embed_documents(texts)
    embed_seconds = time.perf_counter() - embed_started

    collection.add(
        ids=[c.chunk_id for c in chunks],
        embeddings=vectors,
        documents=[c.text for c in chunks],
        metadatas=[_chunk_metadata(c) for c in chunks],
    )

    manifest = {
        "chunk_count": len(chunks),
        "carriers": sorted({c.carrier_id for c in chunks}),
        "embeddings_backend": backend.name,
        "chunk_target_tokens": settings.chunk_target_tokens,
        "chunk_max_tokens": settings.chunk_max_tokens,
        "embed_seconds": round(embed_seconds, 2),
        "total_seconds": round(time.perf_counter() - started, 2),
        "documents": [d["filename"] for d in CORPUS_DOCUMENTS],
    }
    manifest_path = settings.data_dir / "index_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("index built: %s", manifest)
    return manifest


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Build the prose index.")
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Add to the existing collection instead of rebuilding it.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s"
    )
    manifest = build(reset=not args.no_reset)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
