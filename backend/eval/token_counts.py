"""Measure the system prompts against the tokenizer.

WHY THIS EXISTS
---------------
The obvious optimisation on this application is a prompt-caching breakpoint on
the synthesis system prompt: it is static, it is the largest fixed block in the
request, and it goes out on four parallel calls per query. It would do nothing.

The minimum cacheable prefix is model-dependent and is 1024 tokens on
claude-sonnet-5. Below that a `cache_control` marker is accepted and silently
ignored -- there is no error, and `cache_creation_input_tokens` stays at zero.
An optimisation that cannot be observed to fail is worth measuring before it is
worth shipping, so this script measures it.

Token counts come from the API's own tokeniser via `messages.count_tokens`, not
from a local approximation, because a local estimate is exactly what would get
this wrong.

Run with:
    cd backend && python -m eval.token_counts
"""

from __future__ import annotations

import anthropic

from app.config import get_settings
from app.ingest.build_index import CARRIER_NAMES
from app.synthesis.prompts import SYNTHESIS_SYSTEM, query_plan_system_prompt

# Minimum cacheable prefix for claude-sonnet-5. Other models differ, and the
# value is not monotonic across generations, so it is pinned to the model rather
# than assumed.
MIN_CACHEABLE_TOKENS = 1024


def main() -> None:
    """Print a token count and caching verdict for each system prompt."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set; cannot count tokens.")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    prompts = {
        "SYNTHESIS_SYSTEM": SYNTHESIS_SYSTEM,
        "QUERY_PLAN_SYSTEM": query_plan_system_prompt(CARRIER_NAMES),
    }

    print(f"model: {settings.synthesis_model}")
    print(f"minimum cacheable prefix: {MIN_CACHEABLE_TOKENS} tokens\n")
    print(f"{'prompt':<22}{'tokens':>8}  verdict")

    for name, text in prompts.items():
        counted = client.messages.count_tokens(
            model=settings.synthesis_model,
            system=text,
            # A one-character user turn: count_tokens requires a message, and
            # the goal is to measure the system prefix, not a real request.
            messages=[{"role": "user", "content": "x"}],
        ).input_tokens
        verdict = (
            "cacheable"
            if counted >= MIN_CACHEABLE_TOKENS
            else "below minimum -- a cache_control marker would be inert"
        )
        print(f"{name:<22}{counted:>8}  {verdict}")


if __name__ == "__main__":
    main()
