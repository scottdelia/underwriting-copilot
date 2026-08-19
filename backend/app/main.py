"""FastAPI entrypoint.

Phase 1 exposes the ingestion spine: a health check and a raw `/search` endpoint
that returns retrieved chunks with their citations. The point of shipping this
before synthesis is that it makes retrieval inspectable on its own. If a
citation is wrong here, it is wrong because retrieval is wrong -- not because a
model paraphrased something. Later phases add the router, structured lookups,
and synthesis on top of this.
"""

# NOTE: this module deliberately does not use `from __future__ import
# annotations`. The rate limiter decorator wraps each endpoint, and FastAPI
# resolves a deferred (string) annotation against the *wrapper's* globals, which
# belong to slowapi rather than to this module. `CompareRequest` is not
# importable there, so the request body silently degraded into a query
# parameter and every POST returned 422. Evaluating annotations eagerly keeps
# the types FastAPI sees the same as the ones written here.

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import Settings, get_settings
from app.models.schemas import HealthResponse, SearchHit, SearchResponse
from app.models.verdict import ComparisonResponse
from app.synthesis.answer import (
    answer_build_lookup,
    answer_prose_question,
    compare_carriers,
)
from app.retrieval.router import plan_query
from app.retrieval.semantic import IndexNotBuiltError, get_index
from app.security.auth import verify_shared_secret
from app.usage import UsageMeter
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

    # One client for the process, not one per request. Each AsyncAnthropic owns
    # an httpx connection pool; constructing it inside the handler threw the
    # pool away after every request and paid a fresh TLS handshake on the next
    # one -- four of them, since the carriers run concurrently.
    #
    # PROMPT CACHING IS DELIBERATELY NOT USED HERE, AND THE REASON IS MEASURED
    # ----------------------------------------------------------------------
    # The obvious optimisation is a cache_control breakpoint on the synthesis
    # system prompt, which is static and goes out on four parallel calls per
    # request. It would do nothing. The minimum cacheable prefix on
    # claude-sonnet-5 is 1024 tokens; SYNTHESIS_SYSTEM measures ~800 and the
    # router prompt ~540 (backend/eval/token_counts.md records the run). Below
    # the minimum a breakpoint is silently inert -- no error, and
    # cache_creation_input_tokens stays zero.
    #
    # Even above the threshold the win here would be smaller than it looks: a
    # cache entry is only readable once the first response has begun, and the
    # four carrier calls are issued concurrently, so they would all miss and
    # all pay the write premium. The saving would come only from repeat queries
    # inside the TTL.
    #
    # Padding the prompt to clear 1024 tokens would be writing text for the
    # tokenizer rather than for the model, so it is not done.
    app.state.anthropic = None
    if settings.anthropic_api_key:
        import anthropic

        app.state.anthropic = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key
        )

    yield

    if app.state.anthropic is not None:
        await app.state.anthropic.close()


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


class CompareRequest(BaseModel):
    """Request body for /compare.

    The query travels in a POST body rather than a query string. Section 7 of
    the brief forbids user input in URLs, and for good reason here: a real
    query names a person's medical conditions, and URLs land in access logs,
    proxy logs, and browser history.
    """

    query: str = Field(min_length=1, description="Natural-language description.")


@app.post(
    "/compare",
    response_model=ComparisonResponse,
    dependencies=[Depends(verify_shared_secret)],
)
@limiter.limit(lambda: f"{get_settings().rate_limit_per_hour}/hour")
async def compare(
    request: Request,
    body: CompareRequest,
    settings: Settings = Depends(get_settings),
) -> ComparisonResponse:
    """Compare how each carrier would likely classify a described prospect.

    Args:
        request: The incoming request. Required by the rate limiter.
        body: The query.
        settings: Application settings.

    Returns:
        One verdict per carrier, each either classified with cited evidence or
        explicitly abstaining.

    Raises:
        HTTPException: 400 on invalid input, 503 when the model or the index is
            unavailable.
    """
    try:
        query = sanitize_query(body.query, settings.max_query_chars)
    except InputRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Synthesis is not configured.",
        )

    logger.info("compare %s", redact_query_for_logging(query))

    # Started before routing rather than after it. Routing is a model call on
    # every path, so a clock started below it would report a number the caller
    # never experienced. latency_ms is what the request cost, end to end.
    started = time.perf_counter()

    # One meter per request. Every model call on every path adds to it, so the
    # figure returned is what this request actually consumed.
    meter = UsageMeter()

    client = request.app.state.anthropic
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Synthesis is not configured.",
        )

    try:
        plan = await plan_query(client, settings, query, meter)
    except Exception as exc:
        logger.exception("query planning failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not interpret the query.",
        ) from exc

    # The routing decision changes the path taken, which is the only reason to
    # have a router. Three of the four types never reach the synthesis model at
    # all: they are answered from the store, from the index, or not at all.
    #
    # "Not at all" is about composition, not about model calls. plan_query has
    # already run by this point, so every response below reports the routing
    # model rather than claiming no model was involved.
    routing_only = f"{settings.synthesis_model} (routing only)"

    def direct(answer_obj) -> ComparisonResponse:  # type: ignore[no-untyped-def]
        return ComparisonResponse(
            query=query,
            query_type=plan.query_type,
            routing_reason=plan.reasoning,
            profile=plan.profile.model_dump(exclude_none=True),
            answer=answer_obj,
            latency_ms=round((time.perf_counter() - started) * 1000),
            model=routing_only,
            usage=meter.summary(),
        )

    # An out-of-scope question is answered by saying so, not by running four
    # carriers over evidence that cannot address it. This is the abstention the
    # eval measures, and it costs nothing.
    if plan.query_type == "out_of_scope":
        return ComparisonResponse(
            query=query,
            query_type=plan.query_type,
            routing_reason=plan.reasoning,
            profile={},
            latency_ms=round((time.perf_counter() - started) * 1000),
            model=routing_only,
            usage=meter.summary(),
        )

    if plan.query_type == "build_lookup":
        return direct(answer_build_lookup(settings, plan))

    if plan.query_type == "prose_question":
        try:
            return direct(answer_prose_question(settings, plan, query))
        except IndexNotBuiltError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Search index is not available.",
            ) from exc

    return await compare_carriers(client, settings, query, plan, started, meter)
