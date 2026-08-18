"""Application settings, loaded from the environment only.

Every secret this application uses arrives through the environment. Nothing is
defaulted to a real value, nothing is read from a checked-in file, and the
settings object is the single place any of it is read. That makes the blast
radius of a leaked value one rotation rather than a code change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration. See .env.example for the full list."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    # Optional so that ingestion and prose search can run with no key at all.
    # The endpoints that actually call the model check for it and fail loudly.
    anthropic_api_key: str = ""

    # Sonnet is the model named in the project brief. Opus is the stronger
    # default for this kind of extraction work; the brief chose Sonnet for cost
    # and latency, which the write-up records as a deliberate tradeoff.
    synthesis_model: str = "claude-sonnet-5"
    extraction_model: str = "claude-sonnet-5"

    # --- Embeddings ---
    # "local" needs no key and no network, which keeps a fresh clone runnable in
    # under ten minutes. "voyage" is the higher-quality option.
    embeddings_backend: Literal["local", "voyage"] = "local"
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    voyage_embedding_model: str = "voyage-4-lite"
    voyage_api_key: str = ""

    # --- Access control ---
    # When empty the auth gate is disabled, which is only ever appropriate for
    # local development. main.py refuses to start with an empty secret unless
    # dev_mode is explicitly set, so a deploy cannot silently ship wide open.
    app_shared_secret: str = ""
    dev_mode: bool = False

    # --- HTTP ---
    cors_allowed_origins: str = "http://localhost:5173"
    rate_limit_per_hour: int = 20
    max_query_chars: int = 2000

    # --- Paths ---
    corpus_dir: Path = BACKEND_ROOT.parent / "corpus"
    data_dir: Path = BACKEND_ROOT / "data"

    # --- Retrieval ---
    semantic_top_k: int = 6
    chunk_target_tokens: int = 600
    chunk_max_tokens: int = 800
    chunk_min_tokens: int = 120

    @field_validator("corpus_dir", "data_dir", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        """Resolve relative paths against the backend directory, not the cwd.

        Without this the app would look for the corpus in a different place
        depending on where uvicorn happened to be started from.
        """
        return value if value.is_absolute() else (BACKEND_ROOT / value).resolve()

    @property
    def cors_origins(self) -> list[str]:
        """The CORS allowlist as a list.

        A wildcard is never produced here. If the environment variable is empty
        the list is empty, which blocks cross-origin requests rather than
        allowing all of them.
        """
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def chroma_dir(self) -> Path:
        """Directory holding the persistent Chroma index."""
        return self.data_dir / "chroma"

    @property
    def sqlite_path(self) -> Path:
        """Path to the structured store for build charts and condition rules."""
        return self.data_dir / "underwriting.sqlite3"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that the environment is read once at startup. Tests clear the
    cache with `get_settings.cache_clear()` when they need to override values.
    """
    return Settings()
