# Underwriting Copilot POC: Project Brief

> **What this is, and what it is not.**
>
> This was the working brief, written before any code existed. It is kept in the
> repository as a record of how the project was planned, not as a description of
> what it became. Several of its decisions were wrong and were overridden during
> implementation.
>
> **Where the brief and the code disagree, the code is the decision.** The
> reasoning for each departure is in
> [Known deviations from the brief](../README.md#known-deviations-from-the-brief).
>
> The largest reversal is the corpus. The brief called for ingesting real
> published carrier guides, with a note that production use would require carrier
> permission. The implementation generates a fictional corpus instead. That
> removed the redistribution problem entirely and, more importantly, made
> extraction ground truth exact at the level of the individual table cell, which
> turned extraction fidelity from something spot-checked into something measured
> across all 625 cells. The brief did not anticipate that trade.
>
> Chunk sizing was also overridden. The brief specifies 400 to 800 token chunks;
> the implementation aligns chunks to sections, which run 45 to 170 tokens,
> because a section in an underwriting guide is a single condition and padding to
> hit a token target merges two conditions into one chunk. That is how a
> carrier's diabetes rule ends up cited for a hypertension question.

**Author:** Scott Delia
**Status:** Superseded by the implementation. Retained for provenance.

---

## 1. What we are building and why

A cross-carrier life insurance underwriting lookup tool. An agent describes a prospect in plain language and gets back a comparison of likely rate classes across multiple carriers, with the specific guideline text that drove each answer and a citation back to the source page.

**The demo scenario (this is the money shot, optimize everything for it):**

> Input: "55 year old male, A1c 7.1 controlled on metformin, BMI 31, non-smoker, $500K 20-year term"
>
> Output: a table of 4 carriers, each showing likely rate class, the qualifying and disqualifying criteria that applied, the exact guideline language, and a deep link to the source document and page.

**Why this problem:** no single carrier's guide gives you the comparison. Agents currently guess, ask a colleague, or submit and get declined. The tool improves quote accuracy (close rate), reduces declines (placement rate), and shortens the ramp for new agents who have not yet absorbed tribal knowledge.

**Secondary deliverable, equally important:** an evaluation harness with published accuracy numbers and a written failure analysis. Most portfolio RAG demos show only the happy path. This one shows measured performance including where it breaks. Do not treat the eval as optional polish.

---

## 2. Non-goals

Scope discipline matters more than feature count. Explicitly out of scope:

- Multi-user accounts, roles, or an admin panel
- Chat history or conversation memory
- Any write path into a CRM or agency management system
- Quoting, premium calculation, or illustrations
- Automated ingestion of new carrier documents (manual ingestion script is fine)
- Mobile-responsive polish beyond "does not look broken on a phone"
- Anything resembling a production data pipeline

If a feature does not make the demo scenario better or the eval numbers more credible, do not build it.

---

## 3. Tech stack

Chosen to match my existing fluency so build speed is maximized.

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI | Async, good typing, fast to stand up |
| Frontend | React + TypeScript + Vite | Single page, no router needed |
| Styling | Tailwind | No component library, keep it lean |
| Vector store | ChromaDB (local persistent) | Zero infra. Swap to pgvector only if needed |
| Structured store | SQLite | Build charts and condition rules live here |
| LLM | Anthropic API, Claude Sonnet | Synthesis and structured extraction |
| Embeddings | `voyage-3` or `text-embedding-3-large` | Either is fine, pick one and note why |
| PDF parsing | `pypdf` for text, `pdfplumber` for tables, `pymupdf` for page rasterization | |
| Deploy | Railway or Fly.io | Fastest path to a public URL. Azure only if a container is already trivial for you |

**Repo layout:**

```
underwriting-copilot/
  backend/
    app/
      main.py              # FastAPI entrypoint
      config.py            # Settings via pydantic-settings, env only
      ingest/
        extract_text.py    # Prose extraction and chunking
        extract_tables.py  # Vision-based table extraction
        build_index.py     # Orchestrates full ingestion
      retrieval/
        router.py          # Classifies query, picks retrieval strategy
        semantic.py        # Vector search over prose
        structured.py      # SQL lookups against build charts and rules
      synthesis/
        answer.py          # Assembles carrier comparison with citations
        prompts.py         # All prompt templates, versioned
      security/
        auth.py            # Shared-secret gate
        sanitize.py        # Input validation, retrieved-content fencing
      models/              # Pydantic schemas
    eval/
      dataset.jsonl        # 50 labeled question/answer pairs
      run_eval.py          # Scoring harness
      results/             # Timestamped run outputs
    tests/
  frontend/
    src/
      App.tsx
      components/
      api/
  corpus/                  # Source PDFs, NOT committed. See section 8
  docs/
    FINDINGS.md            # The write-up. See section 9
  .env.example
  README.md
```

---

## 4. The hard part: structured data trapped in prose documents

This is the technical crux and where most implementations fail. Read this section carefully before writing ingestion code.

Carrier field underwriting guides contain two fundamentally different kinds of content:

**(a) Prose rules.** "Applicants with well-controlled Type 2 diabetes diagnosed after age 50 may be considered for Standard Plus provided A1c is below 7.5 and there is no evidence of complications." Semantic search handles this well.

**(b) Numeric tables.** Build charts (height by maximum weight by rate class), A1c thresholds by age band, blood pressure limits, coverage-by-age grids. **Naive chunk-and-embed destroys these.** A 40-row build chart chunked into 500-character segments loses the row-to-column relationship, and the model will confidently return a wrong weight limit. This is the single most common failure mode in this problem domain and fixing it is the thing that separates a real tool from a toy.

### Required approach

**Step 1: Classify pages.** Rasterize each page with pymupdf. Use pdfplumber's table detection plus a heuristic (high digit density, consistent column alignment) to flag pages containing tables.

**Step 2: Extract tables with vision, not text parsing.** For flagged pages, send the page image to Claude with a strict schema and ask for structured JSON. Vision extraction handles merged cells, multi-level headers, and footnote markers far better than coordinate-based parsers. Validate the returned JSON against a Pydantic model and reject anything malformed rather than silently accepting it.

Target schemas:

```python
# Build chart entry: one row per height, per rate class, per carrier
class BuildChartEntry(BaseModel):
    carrier_id: str
    doc_id: str
    page: int
    height_inches: int
    rate_class: str          # normalized, see below
    max_weight_lbs: int
    gender: Literal["male", "female", "any"]
    notes: str | None

# Condition rule: one row per underwriting condition per carrier
class ConditionRule(BaseModel):
    carrier_id: str
    doc_id: str
    page: int
    condition: str           # normalized, e.g. "type_2_diabetes"
    criteria: str            # verbatim qualifying language
    best_available_class: str
    disqualifiers: list[str]
    source_excerpt: str      # short verbatim snippet for citation
```

**Step 3: Normalize rate classes across carriers.** Every carrier names them differently (Preferred Plus, Preferred Best, Super Preferred, Elite). Build an explicit mapping table to a canonical ladder. Do not let the LLM improvise this at query time. Store both the carrier's original label and the normalized tier, and display the carrier's own label in the UI.

**Step 4: Chunk prose separately.** Semantic chunking on section boundaries, roughly 400 to 800 tokens, with the carrier, document title, section heading, and page number attached as metadata to every chunk. Page number is mandatory because citations depend on it.

---

## 5. Retrieval

A router classifies the incoming query and picks a strategy. Keep the router simple and legible, because you will need to explain it.

```
Query arrives
  |
  +-- Parse into a structured prospect profile (LLM, strict schema)
  |     age, gender, height, weight, conditions[], medications[],
  |     tobacco, coverage_amount, product_type
  |
  +-- For each carrier in scope, run in parallel:
  |     - Structured lookup: build chart by height and gender
  |     - Structured lookup: condition rules matching parsed conditions
  |     - Semantic search: top-k prose chunks scoped to that carrier
  |
  +-- Synthesis: assemble per-carrier verdict from retrieved evidence
```

**Critical rule for synthesis: the model may only reason over retrieved evidence.** If the evidence does not support a determination for a given carrier, the correct output is "insufficient information in the indexed guide," not a guess. Build this into the prompt and measure it in the eval as refusal rate. A tool that abstains correctly is worth vastly more in a regulated context than one that always answers.

**Every claim in the output must carry a citation** (carrier, document, page, and the verbatim excerpt it rests on). No citation means the claim does not get rendered.

---

## 6. Evaluation harness

This is the differentiator. Build it early, not at the end, so it can guide development.

**Dataset:** 50 question/answer pairs in `eval/dataset.jsonl`, hand-labeled by reading the guides. Composition:

| Category | Count | Purpose |
|---|---|---|
| Build chart lookups | 12 | Tests table extraction fidelity |
| Single-condition rules | 12 | Tests prose retrieval |
| Multi-condition cases | 10 | Tests synthesis under interacting rules |
| Cross-carrier comparisons | 8 | Tests the core use case |
| Out-of-corpus questions | 8 | Tests refusal behavior |

Each record:

```json
{
  "id": "eval_017",
  "question": "...",
  "category": "build_chart",
  "expected": {
    "carrier_verdicts": {"carrier_a": "standard_plus"},
    "must_cite_pages": [{"carrier": "carrier_a", "doc": "...", "page": 14}],
    "answerable": true
  },
  "notes": "Edge case: height falls exactly on a chart boundary"
}
```

**Metrics to report:**

1. **Retrieval hit rate @k** (did the correct source page appear in retrieved context)
2. **Verdict accuracy** (did the rate class match the label)
3. **Citation correctness** (does every cited page actually contain the supporting text)
4. **Refusal rate on out-of-corpus** (target: high, this is a feature)
5. **Hallucinated citation rate** (target: zero, treat any occurrence as a blocking bug)
6. **P50 and P95 latency**

Grading: exact match for verdicts and citations. Use an LLM judge only for prose-quality assessment, and if you do, note in the write-up that the judge is itself unvalidated. Run each config three times and report variance, because single-run numbers on a 50-item set are noise.

**Write results to `eval/results/` with a timestamp and the config that produced them.** Being able to show the improvement curve across runs is itself a strong signal.

---

## 7. Security requirements

Treat these as hard requirements, not suggestions. Half the point of the artifact is demonstrating that I ship responsibly in a regulated domain.

**Secrets**
- All keys via environment variables loaded through `pydantic-settings`. Never hardcoded, never in the repo, never in client-shipped code.
- `.env` in `.gitignore` from the first commit. Provide `.env.example` with empty values.
- The Anthropic API key lives server-side only. The React app calls our FastAPI backend, never the Anthropic API directly.
- Add a pre-commit hook running `detect-secrets` or `gitleaks`.

**Access control**
- Gate the deployed app behind a shared secret or single-password login. This is a portfolio piece on the public internet calling a paid API. An open endpoint is both a cost risk and a bad look.
- Rate limit per session (for example 20 queries per hour) with `slowapi`.
- Set a hard monthly spend cap in the Anthropic console.

**Prompt injection (call this out explicitly in the write-up)**
- Retrieved PDF content is untrusted input. Carrier documents are third-party artifacts I do not control.
- Fence all retrieved content in the prompt with clear delimiters, and instruct the model that content inside the fence is reference data only and never instructions.
- Validate model output against a Pydantic schema before it reaches the UI. Reject and log anything that does not conform rather than passing it through.
- Never let retrieved content influence tool selection or trigger any side effect. In this POC there are no write paths at all, which is itself the strongest mitigation. Say so.

**Input handling**
- Validate and length-cap all user input at the API boundary.
- Parameterized queries only for SQLite. No string-built SQL anywhere.
- Strict CORS allowlist, not a wildcard.
- Security headers via middleware: HSTS, `X-Content-Type-Options`, a restrictive CSP.

**Data handling**
- Log query text only in a redacted or hashed form. The demo inputs are synthetic, but the discipline should be visible in the code.
- No user input in URL query strings.
- Structured logging with no secrets and no full prompt dumps.

**Dependencies**
- Pin all versions. Commit the lockfile.
- Run `pip-audit` and `npm audit` before the final commit and note the result.

---

## 8. Legal and IP guardrails

Non-negotiable. Getting this wrong turns a good artifact into a liability.

- **Do not commit carrier PDFs to the repo.** `corpus/` goes in `.gitignore`. Provide a manifest listing document titles, versions, and public source URLs, plus a fetch script.
- **Do not serve or redistribute the source PDFs from the app.** Cite the document, page, and a short excerpt, and deep link to the carrier's own published URL.
- **Keep excerpts short.** Enough to justify the verdict, not enough to substitute for the document.
- Prominent banner in the UI and at the top of the README: illustrative demonstration only, not affiliated with or endorsed by any carrier, guidelines change frequently, always verify against the current official document.
- State plainly in the write-up that a production version would require carrier permission and a versioned document feed. Raising this unprompted is a strong signal.
- Use only publicly published field underwriting guides. Nothing behind an agent portal login.

---

## 9. The write-up (`docs/FINDINGS.md`)

Roughly 1,200 to 1,800 words. This is the highest-signal artifact in the package. Structure:

1. **The problem**, in an agent's words, with the cost of getting it wrong
2. **Approach**, including why hybrid retrieval and why vision-based table extraction
3. **Results table**, all six metrics, with variance across runs
4. **What broke**, the honest section. Specific failure taxonomy with real examples:
   - Which query types failed and why
   - Where table extraction lost fidelity
   - Any cases where confident wrong answers were produced, and what surfaced them
   - Where carrier terminology normalization is still lossy
5. **What I would need before this touches a producing agent**: carrier permission and document feed, human review of every extracted table, versioning and change detection on source documents, a compliance review of anything approaching a suitability recommendation, and monitoring for silent retrieval degradation
6. **What this cost**: build hours and API spend. Concrete numbers signal someone who thinks about unit economics, which their job posting explicitly asks for

Tone: measured, specific, no overclaiming. The failures section is what makes the successes credible.

---

## 10. Build sequence

Work in this order. Each phase should end with something runnable.

**Phase 1 (4 to 5 hrs): Skeleton and ingestion spine**
FastAPI app, config, security middleware, auth gate. Text extraction and chunking for one carrier. Chroma index populated. A `/search` endpoint returning raw chunks. Verify citations carry correct page numbers.

**Phase 2 (5 to 6 hrs): Table extraction**
Page classification, vision extraction to structured JSON, Pydantic validation, SQLite persistence. Rate class normalization mapping. Spot-check 10 extracted build chart rows against the PDFs by hand and record the error rate. Do not proceed until this is solid, because everything downstream depends on it.

**Phase 3 (4 to 5 hrs): Retrieval and synthesis**
Prospect profile parser, router, parallel per-carrier retrieval, synthesis with mandatory citations and explicit abstention. Working end to end via API for the demo scenario.

**Phase 4 (5 to 6 hrs): Eval harness**
Write the 50-item dataset (budget real time for this, it is the slowest part and cannot be shortcut). Scoring script, three runs, results committed. Fix what the numbers expose, then rerun.

**Phase 5 (4 to 5 hrs): Frontend**
Single page. Natural language input, parsed profile shown back for confirmation, carrier comparison table, expandable evidence per carrier with excerpt and source link. Loading and error states. Disclaimer banner.

**Phase 6 (3 to 4 hrs): Deploy and document**
Deploy behind auth. README with setup and architecture. Write `FINDINGS.md`. Record a 2 to 3 minute demo video, unedited, showing a real query including one where the tool correctly abstains.

---

## 11. Code conventions

- **Comment heavily.** Every non-obvious block gets a comment explaining the reasoning, not restating the code. Prompt templates get comments explaining why they are phrased that way. Retrieval logic gets comments on the trade-offs chosen. Assume a reviewer is reading this to judge my engineering thinking, because one will be.
- Type hints throughout. `mypy` clean.
- Pydantic models for every boundary: API in and out, LLM structured output, database rows.
- Docstrings on all public functions, with the format `Args / Returns / Raises`.
- All prompts in `synthesis/prompts.py` as named constants with a version comment. No inline prompt strings scattered through the codebase.
- `pytest` for the ingestion and retrieval logic. Unit tests for chunking, normalization, and schema validation. Do not chase coverage, just cover the parts where a silent bug would corrupt results.
- Conventional commits. Commit history should read as a coherent narrative of the build.

---

## 12. Definition of done

- [ ] Demo scenario returns a correct 4-carrier comparison in under 15 seconds
- [ ] Every displayed claim carries a verifiable page citation
- [ ] Tool correctly abstains on out-of-corpus questions
- [ ] Eval run committed with all six metrics and variance across three runs
- [ ] Zero hallucinated citations in the final eval run
- [ ] Deployed at a public URL behind an auth gate
- [ ] No secrets in git history (verified with a scanning tool, not by eye)
- [ ] No carrier PDFs in the repo
- [ ] `FINDINGS.md` complete, including the failures section
- [ ] Demo video recorded
- [ ] README lets a stranger run it locally in under 10 minutes
