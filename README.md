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
| 2 | Vision-based table extraction, SQLite, rate class normalization | Not started |
| 3 | Prospect parser, retrieval router, synthesis with citations | Not started |
| 4 | 50-item eval dataset and scoring harness | Not started |
| 5 | Frontend | Not started |
| 6 | Deploy, write-up, demo video | Not started |

`/search` returns retrieved prose chunks with citations. It does not yet produce
carrier verdicts — that is phase 3.

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

Run it:

```bash
cd backend && uvicorn app.main:app --reload
```

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
    +-- tables --> [phase 2] page classification --> vision extraction -->
                   Pydantic validation --> SQLite
```

Prose and tables are two different extraction problems. Running a build chart
through a prose chunker destroys the row-to-column relationship and yields
confidently wrong weight limits, so table regions are located and excluded from
prose extraction and handled separately.

Prose is separated from tables and page furniture using two structural signals
that PyMuPDF exposes: table bounding boxes, and font size (headings render
larger than body text; running headers, footers, and footnotes render smaller).

### Layout

```
backend/app/
  config.py           settings, environment only
  main.py             FastAPI app, middleware, /health and /search
  ingest/
    extract_text.py   prose extraction and chunking
    embeddings.py     local (free, no key) or Voyage backends
    build_index.py    corpus -> Chroma
  retrieval/
    semantic.py       vector search over prose
  security/
    auth.py           shared-secret gate
    sanitize.py       input validation, prompt-injection fencing
  models/schemas.py   Pydantic models for every boundary
backend/eval/ground_truth/   exact expected extraction, per carrier
tools/                       corpus generator
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
