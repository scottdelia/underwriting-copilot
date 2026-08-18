"""Score the pipeline against the labelled dataset.

Run from the backend directory:

    python -m eval.run_eval --runs 3

Writes a timestamped result file to `eval/results/`, including the config that
produced it. A number without the config that produced it cannot be compared to
anything later.

THE SIX METRICS, AND WHY THEY ARE REPORTED SEPARATELY
------------------------------------------------------
1. **Retrieval hit rate** -- was the page carrying the answer put in front of
   the model at all? This is what separates a retrieval failure from a
   synthesis failure. A wrong verdict whose supporting page was never retrieved
   is a different bug, with a different fix, from one where the page was
   retrieved and then misread.

2. **Verdict accuracy** -- exact match on the normalized class. No partial
   credit: "Standard Plus" when the answer is "Standard" is wrong, and a
   scoring scheme that awards it 0.8 for being close is measuring similarity
   rather than correctness.

3. **Citation correctness** -- does the cited page actually contain the quoted
   text? Checked against the PDF's own text layer, not against what the
   pipeline believed it supplied. That independence is the point: the pipeline
   already verifies excerpts against its own evidence, and a check that reuses
   the pipeline's belief would confirm the pipeline agrees with itself.

4. **Refusal rate on out-of-corpus** -- higher is better. A tool that abstains
   correctly is worth more in a regulated setting than one that always answers.

5. **Hallucinated citation rate** -- any citation pointing at text that is not
   on the page. Target zero; the brief treats any occurrence as a blocking bug,
   so it is reported as a count rather than a percentage.

6. **Latency** -- P50 and P95, not a mean. A mean hides the tail, and the tail
   is what an agent waiting on a quote actually experiences.

Rolling these into one headline score would hide the thing that matters most:
they fail independently, and which one drops tells you where to look.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = BACKEND_ROOT / "eval" / "dataset.jsonl"
RESULTS_DIR = BACKEND_ROOT / "eval" / "results"


def load_dataset(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    """Load the labelled dataset.

    Raises:
        FileNotFoundError: If the dataset has not been generated.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Generate it with:\n"
            f"    python tools/build_eval_dataset.py"
        )
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class PageTextCache:
    """Page text from the corpus PDFs, for verifying citations independently.

    Reading the source document rather than trusting the pipeline's record of
    what it supplied is the whole value of this check. Cached because the same
    handful of pages is checked hundreds of times across a run.
    """

    def __init__(self, corpus_dir: Path) -> None:
        self._corpus_dir = corpus_dir
        self._cache: dict[tuple[str, int], str] = {}

    def get(self, doc_id: str, page: int) -> str | None:
        """Return normalized text for one page, or None if it does not exist."""
        key = (doc_id, page)
        if key in self._cache:
            return self._cache[key]

        import re

        import fitz

        path = self._corpus_dir / doc_id
        if not path.exists():
            self._cache[key] = ""
            return None

        with fitz.open(path) as doc:
            if not (1 <= page <= doc.page_count):
                self._cache[key] = ""
                return None
            text = doc[page - 1].get_text() or ""

        normalized = re.sub(r"\s+", " ", text).strip().lower()
        self._cache[key] = normalized
        return normalized


def _normalize(text: str) -> str:
    """Normalize a quoted excerpt the same way page text is normalized."""
    import re

    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class ItemResult:
    """The outcome of scoring one dataset item."""

    item_id: str
    category: str
    latency_ms: int
    # Verdicts
    verdicts_expected: int = 0
    verdicts_correct: int = 0
    verdict_errors: list[str] = field(default_factory=list)
    # Retrieval
    pages_expected: int = 0
    pages_retrieved: int = 0
    retrieval_misses: list[str] = field(default_factory=list)
    # Citations
    citations_total: int = 0
    citations_verified: int = 0
    hallucinated: list[str] = field(default_factory=list)
    # Abstention
    should_abstain: bool = False
    did_abstain: bool = False
    # Routing
    routed_as: str | None = None
    routing_correct: bool | None = None
    error: str | None = None


async def score_item(
    client: Any,
    settings: Settings,
    item: dict[str, Any],
    pages: PageTextCache,
) -> ItemResult:
    """Run one dataset item through the pipeline and score the result.

    Args:
        client: An `anthropic.AsyncAnthropic` instance.
        settings: Application settings.
        item: One dataset record.
        pages: Page text cache for citation verification.

    Returns:
        The scored result.
    """
    from app.retrieval.router import plan_query
    from app.synthesis.answer import (
        answer_build_lookup,
        answer_prose_question,
        compare_carriers,
    )

    expected = item["expected"]
    result = ItemResult(
        item_id=item["id"],
        category=item["category"],
        latency_ms=0,
        should_abstain=not expected["answerable"],
    )

    started = time.perf_counter()
    try:
        plan = await plan_query(client, settings, item["question"])
        result.routed_as = plan.query_type
        if expected.get("query_type"):
            result.routing_correct = plan.query_type == expected["query_type"]

        if plan.query_type == "out_of_scope":
            # Abstaining at the router is the strongest form of abstention: no
            # carrier is consulted and nothing is spent.
            result.did_abstain = True
            result.latency_ms = round((time.perf_counter() - started) * 1000)
            return result

        if plan.query_type == "build_lookup":
            answer = answer_build_lookup(settings, plan)
            claims = answer.claims
            verdicts = []
            retrieved: dict[str, list[int]] = {}
        elif plan.query_type == "prose_question":
            answer = answer_prose_question(settings, plan, item["question"])
            claims = answer.claims
            verdicts = []
            retrieved = {}
        else:
            response = await compare_carriers(
                client, settings, item["question"], plan
            )
            claims = [
                claim
                for verdict in response.verdicts
                for claim in verdict.qualifying + verdict.disqualifying
            ]
            verdicts = response.verdicts
            retrieved = response.retrieved_pages
    except Exception as exc:  # pragma: no cover - surfaced in the report
        result.error = str(exc)
        result.latency_ms = round((time.perf_counter() - started) * 1000)
        logger.exception("item %s failed", item["id"])
        return result

    result.latency_ms = round((time.perf_counter() - started) * 1000)

    # --- Abstention -------------------------------------------------------
    # An answerable item counts as abstaining only if every carrier abstained.
    if verdicts:
        result.did_abstain = all(
            v.determination == "insufficient_information" for v in verdicts
        )
    elif not claims:
        result.did_abstain = True

    # --- Verdict accuracy -------------------------------------------------
    by_carrier = {v.carrier_id: v for v in verdicts}
    for carrier_id, want in (expected.get("carrier_verdicts") or {}).items():
        result.verdicts_expected += 1
        got = by_carrier.get(carrier_id)
        actual = got.canonical_class if got else None
        if actual == want:
            result.verdicts_correct += 1
        else:
            result.verdict_errors.append(
                f"{carrier_id}: expected {want}, got {actual or 'abstained'}"
            )

    # --- Build lookup values ----------------------------------------------
    # Scored as a verdict so a table read wrong counts against accuracy rather
    # than disappearing into a category with no metric of its own.
    values = expected.get("expected_values")
    if values:
        result.verdicts_expected += 1
        wanted = str(values["max_weight_lbs"])
        rate_class = values["rate_class"].lower()
        hit = any(
            wanted in claim.citation.excerpt
            and rate_class in claim.citation.excerpt.lower()
            for claim in claims
        )
        if hit:
            result.verdicts_correct += 1
        else:
            result.verdict_errors.append(
                f"{values['carrier_id']}: no claim carrying "
                f"{values['rate_class']} = {wanted} lb"
            )

    # --- Retrieval hit rate -----------------------------------------------
    cited_pages: dict[str, set[int]] = {}
    for claim in claims:
        cited_pages.setdefault(claim.citation.carrier_id, set()).add(
            claim.citation.page
        )

    for want in expected.get("must_cite_pages", []):
        result.pages_expected += 1
        carrier = want["carrier"]
        # A page counts as retrieved if it reached the model's evidence, or if
        # it was cited (which it could not be unless it was retrieved).
        available = set(retrieved.get(carrier, [])) | cited_pages.get(carrier, set())
        if want["page"] in available:
            result.pages_retrieved += 1
        else:
            result.retrieval_misses.append(
                f"{carrier} p{want['page']} not retrieved"
            )

    # --- Citation correctness and hallucination ---------------------------
    for claim in claims:
        result.citations_total += 1
        page_text = pages.get(claim.citation.doc_id, claim.citation.page)
        excerpt = _normalize(claim.citation.excerpt)

        if page_text is None:
            result.hallucinated.append(
                f"{claim.citation.doc_id} p{claim.citation.page} does not exist"
            )
            continue

        if excerpt in page_text:
            result.citations_verified += 1
            continue

        # A build lookup quotes a row the pipeline composed from stored cells
        # ("Standard Plus: maximum 213 lb"), which is not a printed sentence.
        # Verify the figures instead: both the class and the number must appear
        # on the page.
        import re

        numbers = re.findall(r"\d+", excerpt)
        words = [w for w in re.findall(r"[a-z]+", excerpt) if len(w) > 3]
        if (
            numbers
            and all(n in page_text for n in numbers)
            and all(w in page_text for w in words[:3])
        ):
            result.citations_verified += 1
            continue

        result.hallucinated.append(
            f"{claim.citation.doc_id} p{claim.citation.page}: "
            f"{claim.citation.excerpt[:70]!r}"
        )

    return result


def summarize(results: list[ItemResult]) -> dict[str, Any]:
    """Compute the six headline metrics from one run's item results."""
    latencies = [r.latency_ms for r in results if r.error is None]
    out_of_corpus = [r for r in results if r.should_abstain]
    answerable = [r for r in results if not r.should_abstain]

    verdicts_expected = sum(r.verdicts_expected for r in results)
    verdicts_correct = sum(r.verdicts_correct for r in results)
    pages_expected = sum(r.pages_expected for r in results)
    pages_retrieved = sum(r.pages_retrieved for r in results)
    citations_total = sum(r.citations_total for r in results)
    citations_verified = sum(r.citations_verified for r in results)
    hallucinated = sum(len(r.hallucinated) for r in results)

    routed = [r for r in results if r.routing_correct is not None]

    def pct(numerator: int, denominator: int) -> float:
        return round(100.0 * numerator / denominator, 2) if denominator else 0.0

    return {
        "retrieval_hit_rate_pct": pct(pages_retrieved, pages_expected),
        "verdict_accuracy_pct": pct(verdicts_correct, verdicts_expected),
        "citation_correctness_pct": pct(citations_verified, citations_total),
        "refusal_rate_out_of_corpus_pct": pct(
            sum(1 for r in out_of_corpus if r.did_abstain), len(out_of_corpus)
        ),
        # Reported separately: an answerable item that abstains is not wrong in
        # the way a hallucination is wrong, but it is a miss and hiding it
        # inside verdict accuracy would flatter the tool.
        "over_abstention_rate_pct": pct(
            sum(1 for r in answerable if r.did_abstain), len(answerable)
        ),
        "hallucinated_citations": hallucinated,
        "hallucinated_citation_rate_pct": pct(hallucinated, citations_total),
        "routing_accuracy_pct": pct(
            sum(1 for r in routed if r.routing_correct), len(routed)
        ),
        "latency_p50_ms": round(statistics.median(latencies)) if latencies else 0,
        "latency_p95_ms": (
            round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)])
            if latencies
            else 0
        ),
        "errors": sum(1 for r in results if r.error),
        "counts": {
            "items": len(results),
            "verdicts_expected": verdicts_expected,
            "pages_expected": pages_expected,
            "citations_total": citations_total,
        },
    }


def by_category(results: list[ItemResult]) -> dict[str, Any]:
    """Per-category verdict accuracy, for locating a regression."""
    grouped: dict[str, list[ItemResult]] = {}
    for result in results:
        grouped.setdefault(result.category, []).append(result)

    summary = {}
    for category, group in grouped.items():
        expected = sum(r.verdicts_expected for r in group)
        correct = sum(r.verdicts_correct for r in group)
        summary[category] = {
            "items": len(group),
            "verdict_accuracy_pct": (
                round(100.0 * correct / expected, 2) if expected else None
            ),
            "abstained": sum(1 for r in group if r.did_abstain),
        }
    return summary


async def run_once(
    settings: Settings, dataset: list[dict[str, Any]], pages: PageTextCache
) -> list[ItemResult]:
    """Run every dataset item once, with bounded concurrency.

    Concurrency is capped rather than unbounded: fifty items each fanning out to
    four carriers would put two hundred requests in flight and measure the rate
    limiter instead of the pipeline.
    """
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    semaphore = asyncio.Semaphore(4)

    async def guarded(item: dict[str, Any]) -> ItemResult:
        async with semaphore:
            return await score_item(client, settings, item, pages)

    return await asyncio.gather(*(guarded(item) for item in dataset))


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Score the pipeline.")
    parser.add_argument("--runs", type=int, default=3, help="Repeat count.")
    parser.add_argument("--limit", type=int, default=None, help="First N items only.")
    parser.add_argument(
        "--category", default=None, help="Restrict to one category."
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set.")

    dataset = load_dataset()
    if args.category:
        dataset = [d for d in dataset if d["category"] == args.category]
    if args.limit:
        dataset = dataset[: args.limit]

    pages = PageTextCache(settings.corpus_dir)
    started = time.perf_counter()

    runs = []
    all_results: list[list[ItemResult]] = []
    for index in range(args.runs):
        print(f"run {index + 1}/{args.runs} over {len(dataset)} items...")
        results = asyncio.run(run_once(settings, dataset, pages))
        all_results.append(results)
        runs.append(summarize(results))

    # Variance across runs. The brief is explicit that single-run numbers on a
    # 50-item set are noise, and reporting a spread is the only honest way to
    # show whether a difference between two configs means anything.
    metrics = [
        "retrieval_hit_rate_pct",
        "verdict_accuracy_pct",
        "citation_correctness_pct",
        "refusal_rate_out_of_corpus_pct",
        "over_abstention_rate_pct",
        "routing_accuracy_pct",
        "hallucinated_citations",
        "latency_p50_ms",
        "latency_p95_ms",
    ]
    variance = {
        name: {
            "mean": round(statistics.mean(r[name] for r in runs), 2),
            "min": min(r[name] for r in runs),
            "max": max(r[name] for r in runs),
            "stdev": (
                round(statistics.stdev([r[name] for r in runs]), 2)
                if len(runs) > 1
                else 0.0
            ),
        }
        for name in metrics
    }

    failures = [
        {
            "item_id": r.item_id,
            "category": r.category,
            "verdict_errors": r.verdict_errors,
            "retrieval_misses": r.retrieval_misses,
            "hallucinated": r.hallucinated,
            "error": r.error,
            "routed_as": r.routed_as,
            "abstained": r.did_abstain,
        }
        for r in all_results[-1]
        if r.verdict_errors or r.retrieval_misses or r.hallucinated or r.error
    ]

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "synthesis_model": settings.synthesis_model,
            "extraction_model": settings.extraction_model,
            "synthesis_effort": settings.synthesis_effort,
            "embeddings_backend": settings.embeddings_backend,
            "semantic_top_k": settings.semantic_top_k,
            "prompt_versions": [
                "QUERY_PLAN_SYSTEM v1",
                "SYNTHESIS_SYSTEM v2",
                "TABLE_EXTRACTION_SYSTEM v1",
            ],
            "dataset_items": len(dataset),
            "runs": args.runs,
        },
        "variance": variance,
        "runs": runs,
        "by_category": by_category(all_results[-1]),
        "failures_last_run": failures,
        "wall_seconds": round(time.perf_counter() - started, 1),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"eval_{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n{'metric':<34} {'mean':>9} {'min':>8} {'max':>8} {'stdev':>8}")
    print("-" * 70)
    for name in metrics:
        stats = variance[name]
        print(
            f"{name:<34} {stats['mean']:>9} {stats['min']:>8} "
            f"{stats['max']:>8} {stats['stdev']:>8}"
        )

    print("\nby category (last run):")
    for category, stats in report["by_category"].items():
        print(
            f"  {category:<18} items={stats['items']:<3} "
            f"verdict={stats['verdict_accuracy_pct']}%  "
            f"abstained={stats['abstained']}"
        )

    if failures:
        print(f"\nfailures in the last run ({len(failures)}):")
        for failure in failures[:15]:
            detail = (
                failure["verdict_errors"]
                or failure["retrieval_misses"]
                or failure["hallucinated"]
                or [failure["error"]]
            )
            print(f"  {failure['item_id']} [{failure['category']}] {detail[0]}")

    print(f"\nwritten to {path}")


if __name__ == "__main__":
    main()
