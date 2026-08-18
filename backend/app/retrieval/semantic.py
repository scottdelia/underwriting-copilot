"""Semantic search over the prose index.

This is the vector-search half of the retrieval strategy. Structured lookups
against build charts and condition rules land in `structured.py` in phase 2, and
the router that decides which to use lands in `router.py` in phase 3. Keeping
them separate is the point of section 4 of the brief: a build chart question and
a prose question are different retrieval problems, and answering the first with
vector search is how you get a confidently wrong weight limit.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import Settings, get_settings
from app.ingest.build_index import PROSE_COLLECTION
from app.ingest.embeddings import EmbeddingBackend, get_embedding_backend
from app.models.schemas import SearchHit

logger = logging.getLogger(__name__)


class IndexNotBuiltError(RuntimeError):
    """Raised when the prose index is missing or empty.

    Distinct from a generic error so the API can return a 503 with an
    actionable message instead of a 500. An unbuilt index is an operational
    state, not a bug.
    """


class SemanticIndex:
    """A loaded, queryable prose index.

    Held as a process-wide singleton because both the Chroma client and the
    embedding model are expensive to construct and safe to share. Construction
    is guarded by a lock so that concurrent first requests do not each build
    their own copy.
    """

    def __init__(self, settings: Settings) -> None:
        import chromadb

        self._settings = settings
        self._backend: EmbeddingBackend = get_embedding_backend(settings)

        if not settings.chroma_dir.exists():
            raise IndexNotBuiltError(
                f"no index at {settings.chroma_dir}. Build it with:\n"
                f"    python -m app.ingest.build_index"
            )

        client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        try:
            self._collection = client.get_collection(PROSE_COLLECTION)
        except Exception as exc:
            raise IndexNotBuiltError(
                f"collection {PROSE_COLLECTION!r} not found. Build it with:\n"
                f"    python -m app.ingest.build_index"
            ) from exc

        self._count = self._collection.count()
        if self._count == 0:
            raise IndexNotBuiltError(
                "the prose index is empty. Build it with:\n"
                "    python -m app.ingest.build_index"
            )
        logger.info("loaded prose index: %d chunks", self._count)

    @property
    def chunk_count(self) -> int:
        """Number of chunks in the index."""
        return self._count

    @property
    def backend_name(self) -> str:
        """Name of the embedding backend backing this index."""
        return self._backend.name

    def carriers(self) -> list[str]:
        """Distinct carrier ids present in the index."""
        result = self._collection.get(include=["metadatas"])
        return sorted({m["carrier_id"] for m in result["metadatas"] or []})

    def search(
        self,
        query: str,
        top_k: int | None = None,
        carrier_id: str | None = None,
    ) -> list[SearchHit]:
        """Retrieve the chunks most similar to a query.

        Args:
            query: Natural-language query text. Assumed already validated and
                length-capped by the API boundary.
            top_k: Number of hits to return. Defaults to the configured value.
            carrier_id: Restrict to a single carrier. The per-carrier retrieval
                in the synthesis path uses this so that one carrier's guide
                cannot supply evidence for another carrier's verdict.

        Returns:
            Hits ordered by descending similarity.
        """
        k = top_k or self._settings.semantic_top_k
        vector = self._backend.embed_query(query)

        where: dict[str, Any] | None = (
            {"carrier_id": carrier_id} if carrier_id else None
        )
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=min(k, self._count),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        hits: list[SearchHit] = []
        documents = result["documents"][0] if result["documents"] else []
        metadatas = result["metadatas"][0] if result["metadatas"] else []
        distances = result["distances"][0] if result["distances"] else []
        ids = result["ids"][0] if result["ids"] else []

        for chunk_id, text, meta, distance in zip(ids, documents, metadatas, distances):
            # Chroma returns cosine *distance*. Converting to similarity here
            # keeps "higher is better" true everywhere above this layer.
            hits.append(
                SearchHit(
                    chunk_id=chunk_id,
                    carrier_id=str(meta["carrier_id"]),
                    doc_id=str(meta["doc_id"]),
                    doc_title=str(meta["doc_title"]),
                    section=str(meta["section"]),
                    page_start=int(meta["page_start"]),
                    page_end=int(meta["page_end"]),
                    page_label=str(meta["page_label"]),
                    score=round(1.0 - float(distance), 4),
                    text=str(text),
                )
            )
        return hits


_index: SemanticIndex | None = None
_index_lock = threading.Lock()


def get_index(settings: Settings | None = None) -> SemanticIndex:
    """Return the process-wide index, constructing it on first use.

    Args:
        settings: Application settings. Defaults to the process settings.

    Returns:
        The loaded index.

    Raises:
        IndexNotBuiltError: If the index has not been built yet.
    """
    global _index
    if _index is None:
        with _index_lock:
            if _index is None:
                _index = SemanticIndex(settings or get_settings())
    return _index


def reset_index() -> None:
    """Drop the cached index. Used by tests and after a rebuild."""
    global _index
    with _index_lock:
        _index = None
