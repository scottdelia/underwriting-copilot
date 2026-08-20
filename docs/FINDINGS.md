# Findings

What this system does, what it measures, and where it breaks.

> Every carrier and every guideline in this project is fictional. See
> [Corpus](../README.md#corpus).

---

## The problem

An agent has a 55-year-old on the phone with an A1c of 7.1, controlled on
metformin, and a BMI of 31. The question is which carrier to write him with.

There is no document that answers it. Each carrier publishes its own field
underwriting guide, each one names its rate classes differently, and each sets
its A1c and build thresholds in a different place. Answering the question means
reading four documents and holding four different vocabularies in your head at
once. In practice agents guess, ask a colleague who has written the case
before, or submit and find out.

Guessing has a price on both sides. Quote high and the agent loses the sale to
someone who quoted the class the carrier would actually have offered. Quote low
and the case comes back rated or declined, the client has been told a number
that is no longer true, and weeks of cycle time are gone. New agents pay this
tax hardest, because the knowledge that avoids it is tribal and takes months to
absorb.

So the tool takes a plain-language description and returns one row per carrier:
the class that carrier would likely offer, the guideline text behind it, and the
page it came from.

---

## Approach

Three decisions shaped the build. Each one was made because the obvious
alternative fails in a specific way.

**Prose and tables are separate extraction problems.** A field underwriting
guide contains two kinds of content that look alike to a chunker and behave
nothing alike. Prose rules ("well-controlled Type 2 diabetes may be considered
for Standard Plus provided A1c is below 7.5") survive semantic chunking. Build
charts do not. Run a height-by-weight-by-class table through a 500-character
chunker and the row-to-column relationship is destroyed; what comes back is a
confidently wrong weight limit, which is the worst output this system can
produce. So table regions are located with PyMuPDF's own geometry, excluded from
prose extraction, and handled separately, rasterised at 150 DPI and read by a
vision model against a strict schema.

**The router exists so that most questions never reach a language model.** A
published build limit asked of a model is a number that might be right. The same
limit read out of the table is the number. Of four query types, three are
answered without a synthesis call at all: build lookups come from SQLite, prose
questions are quoted verbatim from the index, out-of-scope questions are
declined. Only a full prospect comparison runs the model, and then once per
carrier.

**Citations are enforced twice, structurally and by verification.** A `Claim` in
the schema is a statement *and* a citation, so no parseable shape can carry an
uncited assertion. After generation, every quoted excerpt is checked against the
evidence that was actually supplied to that carrier's call; anything that cannot
be found there was composed rather than copied, and the claim is discarded and
counted. If verification empties the support behind a classification, the
verdict is downgraded to an abstention. A determination whose every support was
thrown away is not a determination. Each carrier is synthesised in its own call
seeing only its own guide, so a verdict cannot rest on another carrier's text
because that text is never in scope, rather than because a prompt asked nicely.

---

## Results

**Extraction**, scored against ground truth on all 625 build-chart cells rather
than a sample:

| Metric | Result |
|---|---|
| Row coverage | 100% (625/625) |
| Value accuracy | 100% |
| Citation accuracy | 100% |
| Fabricated rows | 0 |
| Condition rules | 100% (11/11) |

The scorer is verified by a negative control: planting a wrong weight, a wrong
page, a deleted row, a fabricated row, and a deleted condition rule into a copy
of the store drops every corresponding metric below 100%. A scorer that cannot
fail is not a measurement.

**Pipeline**, 50 labelled items, three runs:

| Metric | Mean | Min–Max | Stdev |
|---|---|---|---|
| Retrieval hit rate | 100% | 100–100 | 0.00 |
| Verdict accuracy | 91.4% | 89.4–93.9 | 2.32 |
| Citation correctness | 99.9% | 99.6–100 | 0.24 |
| Refusal on out-of-corpus | 83.3% | 75–87.5 | 7.22 |
| Over-abstention (answerable) | 2.4% | 2.4–2.4 | 0.00 |
| Routing accuracy | 100% | 100–100 | 0.00 |
| Hallucinated citations | 0.33 | 0–1 | 0.58 |
| Latency P50 | 10.1s | – | 301ms |
| Latency P95 | 17.8s | – | 1585ms |

Per category on the last run: build chart 100%, single condition 100%,
multi-condition 90%, cross-carrier 90.6%.

---

## What broke

**Refusal is the weakest number and the widest.** 83.3% on out-of-corpus
questions, ranging 75–87.5% across three runs. A 12-point spread on eight
items, which means one or two items flipping. Of the six metrics this is the one
I would not ship on. In a regulated context, a tool that answers a question it
has no basis for is worse than one that answers nothing, and the variance says
the boundary is not reliably drawn.

**The remaining verdict failures are all one shape.** Four items, reproducibly,
across runs: Meridian over-abstains on `standard_plus` three times, Cardinal on
`table_rated` once. The tool is not producing confident wrong answers here; it is
declining cases it has the evidence to decide. That is the failure I would rather
have, but it is still a failure, and it clusters on one carrier, which points at
Meridian's guide wording rather than at the synthesis prompt.

**A fabricated citation is caught roughly once every three runs.** The
`hallucinated_citations` mean of 0.33 is not zero, and the number is not the
interesting part. The mechanism is. One fabrication appears in the recorded demo
response shipped with the published build: the model produced a claim whose
quoted text was not in the guide, verification found it, and the claim never
reached the screen. The count is surfaced in the API and rendered in the UI
rather than swallowed, because a reader should be told when the tool caught
itself.

**Chunks are far shorter than the brief specified.** 45–170 tokens against a
specified 400–800. This is deliberate: a section in an underwriting guide is one
condition, and padding to 600 tokens merges two conditions into one chunk, which
is exactly how a carrier's diabetes rule ends up cited for a hypertension
question. Short chunks are compensated for by prefixing carrier and section
heading to the embedded text. The brief's number was wrong for this document
shape; the deviation is recorded rather than quietly taken.

**The eval labels have not been reviewed by a human.** They are computed by an
oracle that reads the structured source the PDFs were rendered from, using rules
written out by hand. The pipeline never reads that source. It reads rendered
PDFs through classification, vision extraction, an index, and a model. So
agreement means the pipeline recovered the source through that whole chain. What
it does not establish is that the oracle is right. Oracle and synthesis prompt
were written by the same person from the same documents, and a rule misread in
one place can be misread identically in the other. `backend/eval/REVIEW.md` is
the checklist for closing that gap and every box in it is still unticked.

**The corpus is cleaner than reality.** Because the PDFs are generated, ground
truth is exact down to the cell and the page, which is what makes extraction
fidelity measurable at all. The cost is that the evaluation measures extraction
*logic*, not robustness to scan noise, skew, or the artefacts of a document that
has been photocopied and re-PDF'd twice. The generated tables are deliberately
awkward, merged multi-level headers, a footnote marker inside a header cell,
charts long enough to split across a page break, but awkward is not the same as
degraded.

**One optimisation was measured and correctly killed.** The obvious cost win is
a prompt-caching breakpoint on the synthesis system prompt: it is static and goes
out on four parallel calls per query. It would do nothing. The minimum cacheable
prefix on this model is 1024 tokens; the synthesis prompt measures 811 and the
router prompt 540, so a `cache_control` marker would be accepted and silently
ignored. Even above the threshold the win would be smaller than it looks, because
a cache entry only becomes readable once the first response has begun and the
four carrier calls are issued concurrently. They would all miss and all pay the
write premium. Padding the prompt to clear the threshold would be writing text
for the tokeniser rather than the model. `backend/eval/token_counts.py`
reproduces the measurement.

---

## What I would need before this touches a producing agent

- **Carrier permission and a document feed.** Real guides are third-party
  copyrighted material, and a demo that serves them is a legal problem, not a
  technical one. A production version needs an agreement and a versioned feed,
  because a guide that changed last week and an index built last month produce a
  confidently wrong answer with a correct-looking citation.
- **Human review of every extracted table.** Extraction scored 100% here against
  ground truth I generated. On real documents there is no ground truth, and the
  only honest substitute is a person checking the rows before anyone quotes off
  them.
- **Change detection on source documents**, with the index rebuild gated on it.
  Silent retrieval degradation is the failure mode nobody notices, because the
  output keeps looking exactly as authoritative.
- **A compliance review of anything approaching a recommendation.** This tool
  reports what guides say. The moment it ranks carriers for a specific person it
  is adjacent to suitability, and that is a different regulatory object.
- **Refusal behaviour fixed first.** 83% with a 12-point spread is not a number
  I would put in front of a producing agent.

---

## What this cost

**API spend is instrumented on ingestion and not on queries, which is
backwards.** The extraction run is measured exactly: **$0.6373**, 13 pages,
77,840 input and 26,918 output tokens, 187 seconds. The query path. The one
whose per-request cost determines whether this is viable at agent-network scale, records no usage at all. The eval reports latency and accuracy and not a cent.

That is the sharpest process criticism I have of my own build. Per-query cost is
the number a business case turns on, and I measured the number that runs once
instead of the number that runs thousands of times a day. The fix is small, capture `usage` off each response and total it per request. And it should have
been in from the first synthesis call rather than noticed at write-up.

Build hours are not instrumented either and are not reconstructed here, because
a figure recalled after the fact is not a measurement and this document should
not contain one.
