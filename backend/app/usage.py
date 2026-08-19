"""Per-request token accounting.

WHY THIS EXISTS, AND WHY IT SHOULD HAVE EXISTED FROM THE FIRST MODEL CALL
------------------------------------------------------------------------
This project instrumented spend on ingestion -- the path that runs once, when a
corpus is added -- and not on the query path, which is the one that would run
thousands of times a day and the one a business case actually turns on. That was
backwards, `docs/FINDINGS.md` said so, and the cost of the omission showed up in
the ordinary way: somebody asked what a month of usage came to and the honest
answer was that the repository could not say.

An eval that reports latency and accuracy and not a cent is answering two thirds
of the question. A 50-item sweep at three runs is roughly 370 model calls, and
nothing in the output said so.

WHAT IS MEASURED
----------------
Real usage from each response, not an estimate. Every call adds its own
`input_tokens` and `output_tokens` to a meter that lives for one request, so the
figure reported to the caller is what that request actually consumed rather than
a projection from an average.

Prices are per million tokens and are stated in one place. They are a published
list rate, not a contracted one, so the figure is an upper bound on what a
negotiated account pays -- which is the right direction for a number used to
size a business case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Published list prices, US dollars per million tokens. Sonnet 5 carries an
# introductory rate through 2026-08-31; the standard rate is used here so the
# reported figure never understates.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Used when a model id is not in the table, so an unknown model produces a
# visibly wrong zero rather than a plausible wrong number.
UNKNOWN_PRICE = (0.0, 0.0)


@dataclass
class UsageMeter:
    """Accumulates token usage across the calls that make up one request.

    Passed down rather than returned up: `plan_query` and `synthesize_carrier`
    each add to the same meter, so the total covers the routing call and all
    four carrier calls without every function in the chain having to widen its
    return type to carry a number none of them care about.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    # One entry per model call, so a request that made five is distinguishable
    # from one that made one. This is the number that surprises people.
    calls: list[dict[str, Any]] = field(default_factory=list)

    def record(self, label: str, model: str, usage: Any) -> None:
        """Add one response's usage.

        Args:
            label: What the call was for, e.g. "routing" or a carrier id.
            model: The model that served it.
            usage: The `usage` object off an Anthropic response.
        """
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_read_tokens += cache_read
        self.calls.append(
            {
                "label": label,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
        )

    @property
    def model_calls(self) -> int:
        """How many times a model was called for this request."""
        return len(self.calls)

    def cost_usd(self) -> float:
        """Spend for this request at list rates, summed per call.

        Summed per call rather than from the totals, because a request can mix
        models -- a cheap router in front of an expensive synthesiser is a
        reasonable future change, and a single blended rate would quietly stop
        being true the day it happened.
        """
        total = 0.0
        for call in self.calls:
            price_in, price_out = PRICES_PER_MTOK.get(call["model"], UNKNOWN_PRICE)
            total += call["input_tokens"] * price_in / 1e6
            total += call["output_tokens"] * price_out / 1e6
        return total

    def summary(self) -> dict[str, Any]:
        """The shape the API returns and the eval totals."""
        return {
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cost_usd": round(self.cost_usd(), 6),
        }
