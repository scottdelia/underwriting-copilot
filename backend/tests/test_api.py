"""End-to-end tests for the HTTP surface.

These exercise the wiring rather than the retrieval quality: that the auth gate
is actually attached to the endpoint that costs money, that invalid input is
refused at the boundary, and that hardening headers reach the response. Search
relevance is measured by the eval harness, not here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def _require_index() -> None:
    """Skip the module when the index has not been built."""
    settings = get_settings()
    if not settings.chroma_dir.exists():
        pytest.skip(
            "index not built; run `python -m app.ingest.build_index` first"
        )


@pytest.fixture
def client() -> TestClient:
    """A client against the app with its real (dev-mode) settings."""
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def gated_client() -> TestClient:
    """A client against the app with the shared-secret gate switched on."""
    real = get_settings()
    gated = real.model_copy(update={"app_shared_secret": "test-secret", "dev_mode": False})
    app.dependency_overrides[get_settings] = lambda: gated
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_reports_a_ready_index(client: TestClient) -> None:
    """/health confirms the index loaded and lists the carriers present."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["index_ready"] is True
    assert body["chunk_count"] > 0
    assert set(body["carriers"]) == {"northstar", "cardinal", "meridian", "granite"}


def test_health_does_not_leak_configuration(client: TestClient) -> None:
    """/health is unauthenticated, so it must not disclose configuration."""
    body = client.get("/health").json()
    for leaky in ("api_key", "secret", "path", "dir", "backend"):
        assert not any(leaky in key.lower() for key in body)


def test_search_returns_hits_with_citations(client: TestClient) -> None:
    """Every hit carries the carrier, document, and page needed to cite it."""
    response = client.get(
        "/search", params={"q": "A1c threshold for standard plus", "top_k": 3}
    )
    assert response.status_code == 200
    body = response.json()
    assert 0 < len(body["hits"]) <= 3

    for hit in body["hits"]:
        assert hit["carrier_id"]
        assert hit["doc_id"].endswith(".pdf")
        assert hit["page_start"] >= 1
        assert hit["page_end"] >= hit["page_start"]
        assert hit["page_label"]
        assert hit["text"].strip()


def test_search_can_be_scoped_to_one_carrier(client: TestClient) -> None:
    """A carrier-scoped query returns only that carrier's chunks.

    Synthesis relies on this: evidence for one carrier's verdict must not be
    drawn from another carrier's guide.
    """
    response = client.get(
        "/search", params={"q": "diabetes", "carrier_id": "granite", "top_k": 5}
    )
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert hits, "expected at least one hit for the scoped query"
    assert {h["carrier_id"] for h in hits} == {"granite"}


def test_search_caps_top_k_server_side(client: TestClient) -> None:
    """A caller cannot pull the whole index into a single response."""
    response = client.get("/search", params={"q": "underwriting", "top_k": 10_000})
    assert response.status_code == 200
    assert len(response.json()["hits"]) <= 25


def test_search_rejects_empty_query(client: TestClient) -> None:
    """An empty query is a 400 at the boundary."""
    response = client.get("/search", params={"q": "   "})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_search_rejects_oversized_query(client: TestClient) -> None:
    """An over-length query is refused rather than truncated."""
    response = client.get("/search", params={"q": "x" * 5000})
    assert response.status_code == 400
    assert "limit" in response.json()["detail"]


def test_search_requires_the_shared_secret_when_configured(
    gated_client: TestClient,
) -> None:
    """With a secret configured, /search refuses unauthenticated callers.

    This is the control that stops an open endpoint spending the API budget, so
    it is checked against the real route rather than only against the
    dependency in isolation.
    """
    response = gated_client.get("/search", params={"q": "diabetes"})
    assert response.status_code == 401


def test_search_accepts_the_correct_shared_secret(gated_client: TestClient) -> None:
    """The gate lets a correctly authenticated caller through."""
    response = gated_client.get(
        "/search",
        params={"q": "diabetes"},
        headers={"X-App-Secret": "test-secret"},
    )
    assert response.status_code == 200


def test_health_stays_open_when_the_gate_is_on(gated_client: TestClient) -> None:
    """/health remains reachable so platform health checks keep working."""
    assert gated_client.get("/health").status_code == 200


def test_security_headers_are_present(client: TestClient) -> None:
    """Hardening headers are attached to responses."""
    headers = client.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "max-age=" in headers["strict-transport-security"]
    assert "default-src 'none'" in headers["content-security-policy"]


def test_cors_is_not_a_wildcard(client: TestClient) -> None:
    """The CORS allowlist must never answer with a wildcard origin."""
    response = client.get(
        "/health", headers={"Origin": "https://not-allowed.example"}
    )
    assert response.headers.get("access-control-allow-origin") != "*"
