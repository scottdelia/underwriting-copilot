"""Capture real /compare responses for the static demo build.

WHY A FIXTURE LAYER EXISTS
--------------------------
This is a portfolio piece. The link has to work when a stranger clicks it,
which rules out the two obvious hosting shapes: a free-tier backend that sleeps
and answers the first click in a minute, and a live endpoint holding a paid API
key behind nothing but a shared secret the reader does not have.

So the deployed build answers from recorded responses. These are not mocks --
every file here is a real response from the real pipeline, captured from a live
run, including its latency and its dropped-citation count. The live path is
what runs locally and what the eval measures; this is a recording of it.

The honest limitation, which the UI states rather than hides: a recorded answer
cannot respond to a query nobody ran. Anything outside the captured set tells
the reader so and points at the local instructions.

Run with the backend up:
    python tools/capture_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8000/compare"
OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "fixtures"

# Read from the file the UI also imports, so the two lists cannot drift. They
# did drift once: an example button read 5'6" while the captured query read
# 5'06", and because lookup is by hash of the query text, that button silently
# missed on the published build. One character, one dead example.
#
# The last entry is out of scope on purpose. A demo that only records queries
# the tool can answer hides the behaviour that matters most in a regulated
# context, which is what it does when it cannot answer.
QUERIES_FILE = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "src"
    / "api"
    / "exampleQueries.json"
)
QUERIES: list[str] = json.loads(QUERIES_FILE.read_text(encoding="utf-8"))


def key_for(query: str) -> str:
    """Return the lookup key for a query.

    Must match `fixtureKey` in frontend/src/api/client.ts exactly, or the
    static build looks up a file that was never written. Whitespace is
    collapsed and case folded so a reader retyping the query with a different
    space still lands on the recording.
    """
    normalized = re.sub(r"\s+", " ", query).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    """Capture each query and write the fixtures plus their index."""
    OUT.mkdir(parents=True, exist_ok=True)
    index = []

    for query in QUERIES:
        body = json.dumps({"query": query}).encode("utf-8")
        request = urllib.request.Request(
            API, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read())
        except urllib.error.URLError as exc:
            raise SystemExit(f"request failed for {query!r}: {exc}") from exc

        key = key_for(query)
        (OUT / f"{key}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        verdicts = payload.get("verdicts") or []
        classified = sum(1 for v in verdicts if v.get("determination") == "classified")
        index.append({"key": key, "query": query, "query_type": payload["query_type"]})
        print(
            f"{payload['query_type']:<21} {payload['latency_ms']:>6}ms  "
            f"{classified}/{len(verdicts)} classified  "
            f"dropped={payload.get('unverified_claims_dropped', 0)}  {key}"
        )

    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nwrote {len(index)} fixtures + index.json to {OUT}")


if __name__ == "__main__":
    sys.exit(main())
