"""Tests for per-request cost accounting.

These exist because the number they guard is one somebody will make a decision
with. A latency figure that is wrong is annoying; a cost figure that is wrong
gets a project funded or killed on a false premise, and it is exactly the kind
of arithmetic that looks obviously right and is quietly off by a factor of a
thousand.

Deliberately free of Chroma and the model client, so they run when neither is
available.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.usage import PRICES_PER_MTOK, UsageMeter


@dataclass
class FakeUsage:
    """The shape the meter reads off an Anthropic response."""

    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0


def test_meter_starts_empty() -> None:
    """A request that called no model reports zero, not None."""
    meter = UsageMeter()
    summary = meter.summary()
    assert summary["model_calls"] == 0
    assert summary["cost_usd"] == 0.0


def test_cost_is_computed_per_million_tokens() -> None:
    """The unit is dollars per million, and the arithmetic says so.

    A million input tokens on Sonnet 5 is exactly its input list price. If this
    is ever off by 1e3 or 1e6 the assertion catches it, which is the entire
    reason to state the expected value as a bare price rather than as a
    hand-computed decimal.
    """
    meter = UsageMeter()
    meter.record("routing", "claude-sonnet-5", FakeUsage(1_000_000, 0))
    price_in, _ = PRICES_PER_MTOK["claude-sonnet-5"]
    assert meter.cost_usd() == price_in


def test_output_tokens_cost_more_than_input() -> None:
    """Output is priced higher, and the meter has not swapped the two.

    Swapping input and output rates is a plausible transposition that produces
    a believable total, so it is asserted rather than assumed.
    """
    inputs = UsageMeter()
    inputs.record("a", "claude-sonnet-5", FakeUsage(100_000, 0))
    outputs = UsageMeter()
    outputs.record("a", "claude-sonnet-5", FakeUsage(0, 100_000))
    assert outputs.cost_usd() > inputs.cost_usd()


def test_a_comparison_counts_every_call() -> None:
    """One /compare is five model calls, and the meter reports five.

    This is the number the whole module exists to surface. A reader who assumes
    a query is one call underestimates a sweep by a factor of five, which is how
    a 50-item run at three runs turns out to be roughly 370 calls rather than
    150.
    """
    meter = UsageMeter()
    meter.record("routing", "claude-sonnet-5", FakeUsage(1_200, 300))
    for carrier in ("northstar", "cardinal", "meridian", "granite"):
        meter.record(carrier, "claude-sonnet-5", FakeUsage(4_000, 1_500))

    summary = meter.summary()
    assert summary["model_calls"] == 5
    assert summary["input_tokens"] == 1_200 + 4 * 4_000
    assert summary["output_tokens"] == 300 + 4 * 1_500
    assert summary["cost_usd"] > 0


def test_mixed_models_are_priced_separately() -> None:
    """A cheap router in front of an expensive synthesiser prices correctly.

    The meter sums per call rather than applying one blended rate to the
    totals, so this stays true if the router is ever moved to a smaller model.
    """
    meter = UsageMeter()
    meter.record("routing", "claude-haiku-4-5", FakeUsage(1_000_000, 0))
    meter.record("synthesis", "claude-opus-5", FakeUsage(1_000_000, 0))

    haiku_in, _ = PRICES_PER_MTOK["claude-haiku-4-5"]
    opus_in, _ = PRICES_PER_MTOK["claude-opus-5"]
    assert meter.cost_usd() == haiku_in + opus_in


def test_an_unknown_model_reports_zero_rather_than_guessing() -> None:
    """An unpriced model produces a visible zero, not a plausible number.

    A wrong cost that looks reasonable is worse than an obvious one: the first
    gets used, the second gets questioned.
    """
    meter = UsageMeter()
    meter.record("routing", "some-model-added-later", FakeUsage(1_000_000, 1_000_000))
    assert meter.cost_usd() == 0.0
    # The tokens are still counted, so the omission is discoverable from the
    # summary rather than invisible.
    assert meter.summary()["input_tokens"] == 1_000_000
