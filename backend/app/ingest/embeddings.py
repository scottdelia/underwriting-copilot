"""Embedding backends, selectable by configuration.

WHY TWO BACKENDS
----------------
The brief asks for one embedding model with a stated reason. There are two here
because they answer different questions:

* `local` runs all-MiniLM-L6-v2 through onnxruntime, on CPU, with no API key
  and no network call. It is what makes "a stranger can run this locally in
  under ten minutes" true -- a fresh clone with an empty .env still ingests and
  searches. It is the default for that reason.
* `voyage` calls voyage-4-lite. It retrieves better on domain text, and at this
  corpus size it costs nothing: the whole corpus is a few hundred thousand
  tokens against a 200M-token free allowance. It is the setting the deployed
  demo runs on.

The point of keeping both behind one interface is that the eval can be run
against each and the difference reported, rather than the choice being asserted.

The `voyage-3` model named in the brief was not used: it carries no free tier,
while the current-generation `voyage-4-lite` does, at better quality.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.config import Settings

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Anything that turns text into vectors.

    Documents and queries are embedded through separate methods because some
    providers, Voyage among them, apply a different input type to each and
    return measurably better results when told which is which.
    """

    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents for indexing."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query for search."""
        ...


class LocalEmbeddings:
    """all-MiniLM-L6-v2 via the onnxruntime model bundled with Chroma.

    Chosen over sentence-transformers deliberately: the ONNX path is a ~90MB
    download with no PyTorch dependency, where sentence-transformers pulls in
    roughly 2GB. For a demo that has to be runnable from a cold clone, that
    difference matters more than the marginal quality difference.
    """

    name = "local:all-MiniLM-L6-v2"

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        self._fn = ONNXMiniLM_L6_V2()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents.

        Args:
            texts: Document texts.

        Returns:
            One vector per input, in the same order.
        """
        return [list(map(float, v)) for v in self._fn(texts)]

    def embed_query(self, text: str) -> list[float]:
        """Embed one query string."""
        return self.embed_documents([text])[0]


class VoyageEmbeddings:
    """voyage-4-lite via the Voyage API."""

    def __init__(self, api_key: str, model: str) -> None:
        import voyageai

        if not api_key:
            raise ValueError(
                "VOYAGE_API_KEY is required when EMBEDDINGS_BACKEND=voyage"
            )
        self._client = voyageai.Client(api_key=api_key)
        self._model = model
        self.name = f"voyage:{model}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents with input_type='document'."""
        result = self._client.embed(texts, model=self._model, input_type="document")
        return [list(map(float, v)) for v in result.embeddings]

    def embed_query(self, text: str) -> list[float]:
        """Embed one query with input_type='query'."""
        result = self._client.embed([text], model=self._model, input_type="query")
        return list(map(float, result.embeddings[0]))


def get_embedding_backend(settings: Settings) -> EmbeddingBackend:
    """Construct the configured embedding backend.

    Args:
        settings: Application settings.

    Returns:
        A ready-to-use embedding backend.

    Raises:
        ValueError: If the Voyage backend is selected without an API key.
    """
    if settings.embeddings_backend == "voyage":
        backend: EmbeddingBackend = VoyageEmbeddings(
            api_key=settings.voyage_api_key,
            model=settings.voyage_embedding_model,
        )
    else:
        backend = LocalEmbeddings()
    logger.info("embedding backend: %s", backend.name)
    return backend


def embedding_text(carrier_name: str, section: str, text: str) -> str:
    """Build the string that actually gets embedded for a chunk.

    The stored chunk text is what gets shown and cited. The embedded text is
    something else: it prefixes the carrier and section heading.

    This matters because sections in an underwriting guide are short and highly
    similar across carriers. Four carriers all have a paragraph about diabetes
    that shares most of its vocabulary. Embedding the bare paragraph makes those
    four nearly indistinguishable, so a carrier-scoped query retrieves an
    arbitrary one of them. Prefixing the carrier and heading pushes them apart
    and gives a short chunk enough context to be found on topic alone.

    Args:
        carrier_name: The carrier's display name.
        section: The chunk's section heading.
        text: The chunk body.

    Returns:
        The string to embed.
    """
    return f"{carrier_name} | {section}\n\n{text}"
