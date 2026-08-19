# Underwriting Copilot

> **Illustrative demonstration only.** This tool is not affiliated with, endorsed
> by, or connected to any insurance carrier. **Every carrier in it is fictional
> and every guideline is fabricated** — see [Corpus](#corpus) below. Underwriting
> guidelines change frequently; nothing here should be used for underwriting,
> sales, or advisory purposes.

A cross-carrier life insurance underwriting lookup. An agent describes a
prospect in plain language and gets back a comparison of likely rate classes
across carriers, with the specific guideline text behind each answer and a
citation to the source page.

No single carrier's guide gives you the comparison. Agents currently guess, ask
a colleague, or submit and get declined.

---

## Status

Built in phases. This is what currently runs.

| Phase | Scope | State |
|---|---|---|
| 0 | Synthetic corpus generator + exact ground truth | Done |
| 1 | App skeleton, security, prose extraction, vector index, `/search` | Done |
| 2 | Vision-based table extraction, SQLite, rate class normalization | Done |
| 3 | Prospect parser, retrieval router, synthesis with citations | Done |
| 4 | 50-item eval dataset and scoring harness | Done |
| 5 | Frontend | Done |
| 6 | Deploy, write-up, demo video | In progress |

`POST /compare` answers the demo scenario end to end in **14.3s**, under the
15-second target. `/search` remains as a developer-facing retrieval probe.

### Phase 4 evaluation results

50 labelled items, three runs. Mean across runs, with spread:

| Metric | Mean | Min–Max | Stdev |
|---|---|---|---|
| Retrieval hit rate | 100% | 100–100 | 0.00 |
| Verdict accuracy | 91.4% | 89.4–93.9 | 2.32 |
| Citation correctness | 99.9% | 99.6–100 | 0.24 |
| Refusal on out-of-corpus | 83.3% | 75–87.5 | 7.22 |
| Over-abstention (answerable) | 2.4% | 2.4–2.4 | 0.00 |
| Hallucinated citations | 0.33 | 0–1 | 0.58 |
| Latency P50 | 10.1s | 9.8–10.4s | 301ms |
| Latency P95 | 17.8s | 16.2–19.4s | 1585ms |

Per category (last run): build chart 100%, single condition 100%,
multi-condition 90%, cross-carrier 90.6%.

**Labels are generated, not hand-written, and have not been reviewed.** They
come from `tools/eval_oracle.py`, which computes outcomes from the published
thresholds in `tools/carrier_data.py`. The pipeline never reads that file — it
reads rendered PDFs through classification, vision extraction, an index, and a
model — so the labels are independent of the system under test. They are *not*
independent of their author: oracle and prompt were written by the same person
from the same documents. `backend/eval/REVIEW.md` is the checklist for closing
that gap.

```bash
cd backend && python -m eval.run_eval --runs 3
```

### The demo scenario

> 55 year old male, A1c 7.1 controlled on metformin, BMI 31, non-smoker, $500K
> 20-year term

| Carrier | Likely class | Normalized | Driven by |
|---|---|---|---|
| Northstar Mutual Life | Standard | standard | A1c 7.1 falls in their 7.0–7.9 band (p4) |
| Cardinal Assurance | Select NT | standard_plus | A1c ≤7.5 and BMI ≤32 both pass (p4) |
| Meridian Life & Annuity | Standard | standard | A1c 7.1 misses their 6.9 cutoff (p4) |
| Granite Peak Financial | Table 2 | table_rated | Diabetes + BMI 30.1–35.0 (p4) |

Every claim carries a verbatim excerpt and a page. Zero claims were dropped in
citation verification.

### Phase 2 extraction accuracy

Measured against ground truth, all 625 build chart cells, not a sample:

| Metric | Result |
|---|---|
| Row coverage | 100% (625/625) |
| Value accuracy | 100% |
| Citation accuracy | 100% |
| Fabricated rows | 0 |
| Condition rules | 100% (11/11) |

Run cost: **$0.64**, 13 pages, ~3 minutes on Sonnet 5 at 150 DPI.

The scorer is verified by a negative control: planting a wrong weight, a wrong
page, a deleted row, a fabricated row, and a deleted condition rule into a copy
of the store drops every corresponding metric below 100%. A scorer that cannot
fail is not a measurement.

Reproduce with:

```bash
cd backend && python -m app.ingest.build_tables && python -m eval.extraction_report
```

---

## Quickstart

Requires Python 3.11+.

```bash
python -m venv .venv && ./.venv/Scripts/activate   # Windows
# source .venv/bin/activate                        # macOS / Linux
pip install -r backend/requirements.txt
```

Generate the corpus. The PDFs are not committed, so this step is required:

```bash
python tools/generate_corpus.py
```

Configure and build the index:

```bash
cp .env.example .env
printf 'DEV_MODE=true\n' >> .env
cd backend && python -m app.ingest.build_index
```

The first index build downloads a ~90MB embedding model and takes a couple of
minutes. Subsequent builds take seconds.

Run the backend:

```bash
cd backend && uvicorn app.main:app --reload
```

Run the frontend in a second terminal:

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the backend, so the browser
stays on one origin and CORS never enters the picture locally.

```bash
curl "http://127.0.0.1:8000/health"
```

```bash
curl "http://127.0.0.1:8000/search?q=A1c%20threshold%20for%20standard%20plus&top_k=3"
```

Run the tests:

```bash
cd backend && python -m pytest tests/ -q
```

---

## The published build

The deployed site is static and answers from recorded responses. That is a
choice, not a shortcut. A portfolio link has to work when a stranger opens it,
and the two live options both fail that test: a free-tier backend sleeps and
answers the first click a minute later, and an always-on one holds a paid API
key behind a shared secret the reader does not have.

So `npm run build:demo` produces a bundle that reads from
`frontend/public/fixtures/`. Every file there is a real response from this
pipeline, captured by `tools/capture_fixtures.py` from a live run, carrying that
run's own latency and its own dropped-citation count. Nothing is hand-written,
and the recorded set includes a query the tool correctly refuses.

What a recording cannot do is answer a query nobody ran. The UI says so, and
offers the queries it does have. To type your own, run the backend locally --
that is the same code path, and it is the one the eval measures.

```bash
cd frontend && npm run build:demo   # static bundle, no backend needed
npm run build                        # normal build, talks to the API
```

Regenerate the recordings with the backend running:

```bash
python tools/capture_fixtures.py
```

The queries live in `frontend/src/api/exampleQueries.json`, which the UI and the
capture script both read. They are matched by a hash of the query text, so a
one-character difference between the two lists silently breaks an example
button -- which it did, once, before they shared a file.

---

## Corpus

**There are no real carrier documents in this project.** The corpus is four
fictional carriers — Northstar Mutual Life, Cardinal Assurance, Meridian Life &
Annuity, and Granite Peak Financial — defined as structured data in
`tools/carrier_data.py` and rendered to PDFs by `tools/generate_corpus.py`.

Real field underwriting guides are third-party copyrighted material.
Redistributing them, or serving them from a public demo, is not permissible
without carrier agreement. Generating the corpus removes that problem entirely.

It also buys something the real documents could not: because the tables are
generated from structured data, **the ground truth for extraction is known
exactly**, down to the individual cell and the page it printed on. Extraction
fidelity and citation correctness become measurable rather than spot-checked.

The tradeoff is real and is not hidden: these PDFs are cleaner than scanned
carrier documents, so the evaluation measures extraction logic, not robustness
to real-world scan noise. `docs/FINDINGS.md` states this plainly.

The generated tables are deliberately awkward, because a corpus of clean
one-page tables would not test the extractor at all:

- a two-level header whose top row is merged across every rate class column
- a footnote marker attached to the last rate class label
- charts long enough that two of the four split across a page break with the
  header repeated

The four carriers disagree with each other on purpose — different rate class
names, different A1c thresholds, different build limits — so cross-carrier
normalization is a real problem rather than a formality.

---

## Architecture

```
corpus/*.pdf
    |
    +-- prose  --> font-size + table-geometry filtering --> section-aligned
    |              chunks with page numbers --> Chroma (cosine)
    |
    +-- pages  --> 3-signal classification --> vision extraction --> schema +
                   semantic validation --> SQLite (build charts, condition
                   rules, threshold tables, anomalies)
```

Prose and tables are two different extraction problems. Running a build chart
through a prose chunker destroys the row-to-column relationship and yields
confidently wrong weight limits, so table regions are located and excluded from
prose extraction and handled separately.

Prose is separated from tables and page furniture using two structural signals
that PyMuPDF exposes: table bounding boxes, and font size (headings render
larger than body text; running headers, footers, and footnotes render smaller).

### Routing

The router's decision changes the path taken. Three of the four query types
never reach the synthesis model at all:

| Query type | Path | Model calls |
|---|---|---|
| `prospect_comparison` | Per-carrier evidence → parallel synthesis | 1 + one per carrier |
| `build_lookup` | SQL row, returned verbatim with its page | 1 (routing only) |
| `prose_question` | Indexed passage, quoted verbatim | 1 (routing only) |
| `out_of_scope` | Abstains immediately | 1 (routing only) |

A build limit asked of a language model is a number that might be right; the
same limit asked of the table is the number. Routing it away from synthesis is
the entire justification for having a router.

### Citations

Enforced twice, structurally and by verification.

**Structurally:** a `Claim` is a statement *and* a citation. No shape in the
schema can represent an uncited assertion, so one cannot survive parsing.

**By verification:** after generation, every quoted excerpt is checked against
the evidence supplied to that carrier's call. An excerpt that cannot be found
was composed rather than copied, and the claim carrying it is discarded and
counted. If verification empties the support behind a classification, the
verdict is **downgraded to an abstention** — a determination whose every
support was discarded is not a determination.

Each carrier is synthesized in its own call, seeing only its own guide. A
verdict cannot be supported by another carrier's text because that text is
never in scope, rather than because a prompt asked the model not to do it.

### Layout

```
backend/app/
  config.py            settings, environment only
  main.py              FastAPI app, middleware, /health, /search, /compare
  ingest/
    extract_text.py    prose extraction and chunking
    embeddings.py      local (free, no key) or Voyage backends
    build_index.py     corpus -> Chroma
    classify_pages.py  which pages hold structured content
    extract_tables.py  vision extraction + 3 layers of validation
    normalize.py       carrier rate classes -> canonical ladder
    store.py           SQLite persistence
    build_tables.py    corpus -> SQLite
  retrieval/
    router.py          classifies the query, gathers per-carrier evidence
    semantic.py        vector search over prose
    structured.py      SQL lookups for build limits and condition rules
  synthesis/
    answer.py          per-carrier synthesis, citation verification, abstention
    prompts.py         every prompt, versioned
  security/
    auth.py            shared-secret gate
    sanitize.py        input validation, prompt-injection fencing
  models/
    profile.py         parsed prospect profile and query plan
    verdict.py         claims, citations, per-carrier verdicts, API response
    extraction.py      vision extraction schemas
    schemas.py         shared boundary models
backend/eval/
  run_eval.py                50-item pipeline eval, six metrics, N runs
  extraction_report.py       scores extraction against ground truth
  dataset.jsonl              the labelled set
  ground_truth/              exact expected extraction, per carrier
  results/                   timestamped run outputs
  REVIEW.md                  human-review checklist for the generated labels
tools/
  carrier_data.py            the four carriers as structured data
  generate_corpus.py         structured data -> rendered PDFs + ground truth
  eval_oracle.py             expected outcomes, independent of the pipeline
  build_eval_dataset.py      oracle -> dataset.jsonl
frontend/src/                React client; see frontend/README.md
```

---

## Embeddings

Two backends behind one interface, selected by `EMBEDDINGS_BACKEND`:

- **`local`** (default) — `all-MiniLM-L6-v2` via onnxruntime. No API key, no
  network call at query time, ~90MB. This is what makes a fresh clone runnable
  with an empty `.env`.
- **`voyage`** — `voyage-4-lite`. Better retrieval on domain text, and free at
  this corpus size against a 200M-token allowance.

The brief named `voyage-3`; it carries no free tier, while the
current-generation `voyage-4-lite` does, at better quality. Keeping both behind
one interface means the eval can report the difference rather than assert it.

---

## Security

Full detail is in the source comments; the short version:

- **Secrets** — environment only, via `pydantic-settings`. `.env` gitignored
  from the first commit. The Anthropic key is server-side only; the frontend
  calls this API and never the model provider.
- **Access control** — shared-secret gate on `/search`. The app **refuses to
  start** with no secret configured unless `DEV_MODE=true` is set explicitly, so
  a deploy cannot silently ship an open endpoint against a paid API. Rate
  limited per client, 20/hour by default.
- **Prompt injection** — retrieved carrier text is untrusted input. The
  strongest mitigation is architectural: there are **no write paths at all**, so
  there is nothing for an injection to accomplish beyond changing text on a
  screen. Beyond that, retrieved content is fenced with delimiters that are
  stripped from the content itself so it cannot close the fence early, and model
  output is validated against a Pydantic schema before anything renders it.
- **Input handling** — length-capped and validated at the boundary; over-length
  queries are **rejected, not truncated**, because a truncated query answers a
  question nobody asked. Strict CORS allowlist, never a wildcard. HSTS,
  `X-Content-Type-Options`, and a restrictive CSP on every response.
- **Secret scanning** — `detect-secrets` runs over every tracked file, wired as
  a pre-commit hook in `.pre-commit-config.yaml`. The current scan reports four
  hits, all in `backend/tests/`: the literal fixtures `test-secret`,
  `correct-horse`, `battery-staple`, and `nope`, which exist so the auth tests
  can assert that a wrong secret is rejected. They are recorded as reviewed in
  `.secrets.baseline`, so anything new fails the commit rather than blending in.
  Full history was checked separately: `.env` has never been tracked, no corpus
  PDF has ever been committed, and no key-shaped string appears in any diff.
- **Logging** — query text is logged as a hash plus a short prefix, never in the
  clear. The demo inputs are synthetic, but a real deployment would carry health
  details about named individuals in every query.

---

## Known deviations from the brief

- **Chunk size.** The brief specifies 400–800 token chunks. Chunks here are
  section-aligned and run 45–170 tokens, because a section in an underwriting
  guide is one condition. Padding to 600 tokens would merge two conditions into
  one chunk, which is how a carrier's diabetes rule ends up cited for a
  hypertension question. 800 is enforced as a ceiling; the floor is the section.
  Short chunks are compensated for by prefixing carrier and section heading to
  the embedded representation.
- **Embedding model.** `voyage-4-lite` rather than `voyage-3` — see above.
- **Corpus.** Synthetic rather than public carrier guides — see above.

---

## License and attribution

Carrier names, rate class names, and all guideline content in this repository
are fictional. Any resemblance to a real carrier's published guidelines is
coincidental and unintended.
