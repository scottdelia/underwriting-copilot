"""FastAPI entrypoint.

Phase 1 exposes the ingestion spine: a health check and a raw `/search` endpoint
that returns retrieved chunks with their citations. The point of shipping this
before synthesis is that it makes retrieval inspectable on its own. If a
citation is wrong here, it is wrong because retrieval is wrong -- not because a
model paraphrased something. Later phases add the router, structured lookups,
and synthesis on top of this.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import Settings, get_settings
from app.models.schemas import HealthResponse, SearchHit, SearchResponse
from app.retrieval.semantic import IndexNotBuiltError, get_index
from app.security.auth import verify_shared_secret
from app.security.sanitize import (
    InputRejected,
    redact_query_for_logging,
    sanitize_query,
)

logging.basicConfig(
    level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Rate limited per client address. The brief asks for per-session limiting; with
# no sessions in scope, the address is the closest available proxy. It is a cost
# control on a demo, not a defence against a determined attacker, and the
# write-up says so rather than overstating it.
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate configuration at startup and warm the index.

    Two things happen here that are worth doing at boot rather than lazily.

    First, the shared secret is checked. A deployment that starts with no secret
    configured is wide open, and the failure mode of noticing that later is
    unbounded API spend. So it refuses to start instead.

    Second, the index is loaded eagerly. Not for speed -- because a missing
    index should surface as a failed deploy, not as a 503 to whoever happens to
    run the first query.
    """
    settings = get_settings()

    if not settings.app_shared_secret and not settings.dev_mode:
        raise RuntimeError(
            "APP_SHARED_SECRET is not set. Set it, or set DEV_MODE=true to run "
            "without an auth gate locally. Refusing to start an unauthenticated "
            "service that calls a paid API."
        )
    if settings.dev_mode:
        logger.warning("DEV_MODE is on: the auth gate is disabled")

    if not settings.cors_origins:
        logger.warning("CORS allowlist is empty; browser clients will be blocked")

    try:
        index = get_index(settings)
        logger.info(
            "index ready: %d chunks via %s", index.chunk_count, index.backend_name
        )
    except IndexNotBuiltError as exc:
        # Not fatal: /health should be able to report the problem.
        logger.error("index unavailable: %s", exc)

    yield


app = FastAPI(
    title="Underwriting Copilot",
    description=(
        "Cross-carrier life insurance underwriting lookup. "
        "Illustrative demonstration only; not affiliated with any carrier."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

_settings = get_settings()

app.state.limiter = limiter

# Strict allowlist, never a wildcard, and only the methods and header actually
# used. A wildcard here would let any page on the internet spend the API budget.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-App-Secret"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Attach hardening headers to every response.

    The CSP is restrictive because this API serves JSON and nothing else: it has
    no reason to permit scripts, styles, frames, or embedded objects at all.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a 429 with a plain message and no internal detail."""
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report whether the service can serve queries.

    Deliberately unauthenticated so a platform health check can reach it, and
    deliberately free of configuration detail: it reports what is ready, never
    which backend or which paths are in use.
    """
    try:
        index = get_index()
        return HealthResponse(
            status="ok",
            index_ready=True,
            chunk_count=index.chunk_count,
            carriers=index.carriers(),
        )
    except IndexNotBuiltError:
        return HealthResponse(
            status="ok", index_ready=False, chunk_count=0, carriers=[]
        )


@app.get(
    "/search",
    response_model=SearchResponse,
    dependencies=[Depends(verify_shared_secret)],
)
@limiter.limit(lambda: f"{get_settings().rate_limit_per_hour}/hour")
async def search(
    request: Request,
    q: str,
    top_k: int | None = None,
    carrier_id: str | None = None,
    settings: Settings = Depends(get_settings),
) -> SearchResponse:
    """Retrieve prose chunks matching a query, with citations.

    Args:
        request: The incoming request. Required by the rate limiter.
        q: The natural-language query.
        top_k: Number of chunks to return. Capped server-side.
        carrier_id: Restrict results to one carrier.
        settings: Application settings.

    Returns:
        Ranked chunks, each carrying the carrier, document, and page it came
        from.

    Raises:
        HTTPException: 400 on invalid input, 503 when the index is not built.
    """
    started = time.perf_counter()

    try:
        query = sanitize_query(q, settings.max_query_chars)
    except InputRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # Query text is never logged in the clear. See sanitize.redact_query_for_logging.
    logger.info("search %s", redact_query_for_logging(query))

    # Cap top_k regardless of what was asked for, so a single request cannot
    # pull the whole index into a response.
    effective_k = min(top_k or settings.semantic_top_k, 25)

    try:
        index = get_index(settings)
        hits: list[SearchHit] = index.search(
            query, top_k=effective_k, carrier_id=carrier_id
        )
    except IndexNotBuiltError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search index is not available.",
        ) from exc

    return SearchResponse(
        query=query,
        hits=hits,
        latency_ms=round((time.perf_counter() - started) * 1000),
        embeddings_backend=index.backend_name,
    )
