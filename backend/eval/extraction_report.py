"""Score extracted build charts against the corpus ground truth.

Run from the backend directory, after `python -m app.ingest.build_tables`:

    python -m eval.extraction_report

The brief asks for ten rows spot-checked by hand and an error rate recorded.
Because the corpus is generated from structured data, every row can be checked
instead of ten, and the check is exact rather than a reading. That turns "we
looked at some of it and it seemed fine" into a number.

Three things are measured, and they fail independently:

* **Coverage** -- did extraction find the row at all? A missing row makes the
  tool abstain on a question it should answer.
* **Value accuracy** -- is the weight limit right? A wrong limit is the worst
  output in the system, because nothing downstream can detect it.
* **Citation accuracy** -- does the stored page match the page the row actually
  printed on? A right answer with a wrong page is an unverifiable answer, and
  the brief treats a bad citation as a blocking bug.

A run can score well on one and badly on another, which is why they are
reported separately rather than rolled into a single accuracy figure.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.ingest.store import connect

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH_DIR = BACKEND_ROOT / "eval" / "ground_truth"


@dataclass
class CarrierScore:
    """Extraction scores for one carrier."""

    carrier_id: str
    expected_rows: int = 0
    found_rows: int = 0
    correct_values: int = 0
    correct_pages: int = 0
    missing: list[str] = field(default_factory=list)
    wrong_value: list[str] = field(default_factory=list)
    wrong_page: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def as_dict(self, *, examples: int = 5) -> dict[str, Any]:
        """Render the score as JSON, with a bounded sample of each failure.

        Args:
            examples: How many example failures to include per category.

        Returns:
            A summary dict. Failure lists are truncated, and the full counts
            are reported alongside so a truncated sample cannot be mistaken for
            the whole picture.
        """

        def pct(numerator: int, denominator: int) -> float:
            return round(100.0 * numerator / denominator, 2) if denominator else 0.0

        return {
            "carrier_id": self.carrier_id,
            "expected_rows": self.expected_rows,
            "found_rows": self.found_rows,
            "coverage_pct": pct(self.found_rows, self.expected_rows),
            "value_accuracy_pct": pct(self.correct_values, self.expected_rows),
            "citation_accuracy_pct": pct(self.correct_pages, self.found_rows),
            "counts": {
                "missing": len(self.missing),
                "wrong_value": len(self.wrong_value),
                "wrong_page": len(self.wrong_page),
                "unexpected": len(self.unexpected),
            },
            "examples": {
                "missing": self.missing[:examples],
                "wrong_value": self.wrong_value[:examples],
                "wrong_page": self.wrong_page[:examples],
                "unexpected": self.unexpected[:examples],
            },
        }


def _ground_truth_rows(carrier_id: str) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Load a carrier's expected build chart rows, keyed for comparison.

    Args:
        carrier_id: The carrier to load.

    Returns:
        A mapping of (height, rate class, sex) to the expected row.

    Raises:
        FileNotFoundError: If the ground truth has not been generated.
    """
    path = GROUND_TRUTH_DIR / f"{carrier_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Generate it with:\n"
            f"    python tools/generate_corpus.py"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        (row["height_inches"], row["rate_class"], row["gender"]): row
        for row in data["build_chart"]
    }


def _extracted_rows(
    db_path: Path, carrier_id: str
) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Load a carrier's extracted build chart rows, keyed for comparison."""
    with connect(db_path, read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT height_inches, rate_class, gender, max_weight_lbs, page
            FROM build_chart_entries
            WHERE carrier_id = ?
            """,
            (carrier_id,),
        ).fetchall()
    return {
        (r["height_inches"], r["rate_class"], r["gender"]): dict(r) for r in rows
    }


def score_carrier(db_path: Path, carrier_id: str) -> CarrierScore:
    """Compare one carrier's extracted rows against ground truth.

    Rate class labels are compared after stripping footnote markers. The
    extractor is instructed to transcribe "Standard *" verbatim, so comparing
    raw strings would score a correct transcription as a miss.

    Args:
        db_path: Path to the structured store.
        carrier_id: The carrier to score.

    Returns:
        The carrier's score.
    """
    from app.ingest.normalize import _clean_label

    expected = {
        (h, _clean_label(rc), g): row
        for (h, rc, g), row in _ground_truth_rows(carrier_id).items()
    }
    extracted = {
        (h, _clean_label(rc), g): row
        for (h, rc, g), row in _extracted_rows(db_path, carrier_id).items()
    }

    score = CarrierScore(carrier_id=carrier_id, expected_rows=len(expected))

    for key, want in expected.items():
        height, rate_class, gender = key
        label = f"{height}in {rate_class} {gender}"
        got = extracted.get(key)

        if got is None:
            score.missing.append(label)
            continue

        score.found_rows += 1

        if got["max_weight_lbs"] == want["max_weight_lbs"]:
            score.correct_values += 1
        else:
            score.wrong_value.append(
                f"{label}: expected {want['max_weight_lbs']}lb, "
                f"got {got['max_weight_lbs']}lb"
            )

        if got["page"] == want["page"]:
            score.correct_pages += 1
        else:
            score.wrong_page.append(
                f"{label}: expected p{want['page']}, got p{got['page']}"
            )

    for key in extracted.keys() - expected.keys():
        height, rate_class, gender = key
        # Rows extracted that do not exist in the document. This is the
        # fabrication check: a model that completed a page-split table would
        # show up here and nowhere else.
        score.unexpected.append(f"{height}in {rate_class} {gender}")

    return score


def score_condition_rules(db_path: Path, carrier_ids: list[str]) -> dict[str, Any]:
    """Measure how many of the documented condition rules were extracted.

    This metric exists because its absence hid a bug. The first version of the
    report counted extracted condition rules without comparing them to the
    documented set, so an extraction that found nine of eleven reported "9" and
    looked fine. Two rules were missing because the pages stating them printed
    no table and were therefore never sent to the model.

    A count is not a score. Anything worth relying on needs a denominator.

    Args:
        db_path: Path to the structured store.
        carrier_ids: Carriers to score.

    Returns:
        Coverage totals plus the specific conditions missing per carrier.
    """
    with connect(db_path, read_only=True) as conn:
        rows = conn.execute(
            "SELECT carrier_id, condition FROM condition_rules"
        ).fetchall()

    found: dict[str, set[str]] = {}
    for row in rows:
        found.setdefault(row["carrier_id"], set()).add(row["condition"])

    per_carrier = []
    total_expected = total_found = 0
    for carrier_id in carrier_ids:
        path = GROUND_TRUTH_DIR / f"{carrier_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        expected = {c["condition"] for c in data["conditions"]}
        pages = {c["condition"]: c["prose_page"] for c in data["conditions"]}
        got = found.get(carrier_id, set())

        missing = sorted(expected - got)
        total_expected += len(expected)
        total_found += len(expected & got)

        per_carrier.append(
            {
                "carrier_id": carrier_id,
                "expected": len(expected),
                "found": len(expected & got),
                "missing": [f"{c} (p{pages[c]})" for c in missing],
                "unexpected": sorted(got - expected),
            }
        )

    return {
        "expected": total_expected,
        "found": total_found,
        "coverage_pct": (
            round(100.0 * total_found / total_expected, 2) if total_expected else 0.0
        ),
        "per_carrier": per_carrier,
    }


def build_report(db_path: Path, carrier_ids: list[str]) -> dict[str, Any]:
    """Score every carrier and summarise the run.

    Args:
        db_path: Path to the structured store.
        carrier_ids: Carriers to score.

    Returns:
        The full report.
    """
    scores = [score_carrier(db_path, carrier_id) for carrier_id in carrier_ids]

    expected = sum(s.expected_rows for s in scores)
    found = sum(s.found_rows for s in scores)
    correct_values = sum(s.correct_values for s in scores)
    correct_pages = sum(s.correct_pages for s in scores)
    unexpected = sum(len(s.unexpected) for s in scores)

    with connect(db_path, read_only=True) as conn:
        anomalies = [
            dict(row)
            for row in conn.execute(
                """
                SELECT severity, kind, COUNT(*) AS n
                FROM extraction_anomalies
                GROUP BY severity, kind
                ORDER BY n DESC
                """
            ).fetchall()
        ]
        condition_rows = conn.execute(
            "SELECT COUNT(*) FROM condition_rules"
        ).fetchone()[0]

    def pct(numerator: int, denominator: int) -> float:
        return round(100.0 * numerator / denominator, 2) if denominator else 0.0

    return {
        "totals": {
            "expected_rows": expected,
            "found_rows": found,
            "coverage_pct": pct(found, expected),
            "value_accuracy_pct": pct(correct_values, expected),
            "citation_accuracy_pct": pct(correct_pages, found),
            "fabricated_rows": unexpected,
            "condition_rules_extracted": condition_rows,
        },
        "per_carrier": [s.as_dict() for s in scores],
        "condition_rules": score_condition_rules(db_path, carrier_ids),
        "anomalies": anomalies,
    }


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Score extracted build charts against ground truth."
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Also write the report to this path.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()

    carrier_ids = sorted(p.stem for p in GROUND_TRUTH_DIR.glob("*.json"))
    report = build_report(settings.sqlite_path, carrier_ids)

    totals = report["totals"]
    print("\nExtraction accuracy vs ground truth")
    print("-" * 52)
    print(f"  rows expected        {totals['expected_rows']}")
    print(f"  rows found           {totals['found_rows']}")
    print(f"  coverage             {totals['coverage_pct']}%")
    print(f"  value accuracy       {totals['value_accuracy_pct']}%")
    print(f"  citation accuracy    {totals['citation_accuracy_pct']}%")
    print(f"  fabricated rows      {totals['fabricated_rows']}")

    conditions = report["condition_rules"]
    print(
        f"  condition rules      {conditions['found']}/{conditions['expected']} "
        f"({conditions['coverage_pct']}%)"
    )
    for carrier in conditions["per_carrier"]:
        for missing in carrier["missing"]:
            print(f"       MISSING  {carrier['carrier_id']}: {missing}")
    print()
    for carrier in report["per_carrier"]:
        print(
            f"  {carrier['carrier_id']:<10} "
            f"coverage {carrier['coverage_pct']:>6}%  "
            f"value {carrier['value_accuracy_pct']:>6}%  "
            f"citation {carrier['citation_accuracy_pct']:>6}%"
        )
        for kind in ("missing", "wrong_value", "wrong_page", "unexpected"):
            for example in carrier["examples"][kind]:
                print(f"       {kind}: {example}")

    if report["anomalies"]:
        print("\n  anomalies recorded during extraction:")
        for row in report["anomalies"]:
            print(f"       {row['severity']:<9} {row['kind']:<28} {row['n']}")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nreport written to {args.json}")


if __name__ == "__main__":
    main()
