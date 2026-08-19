# System prompt token counts

Recorded run of `python -m eval.token_counts`, 2026-08-18.

Model: `claude-sonnet-5`. Minimum cacheable prefix: **1024 tokens**.

| Prompt | Tokens | Verdict |
|---|---:|---|
| `SYNTHESIS_SYSTEM` | 811 | Below minimum — a `cache_control` marker would be inert |
| `QUERY_PLAN_SYSTEM` (rendered) | 540 | Below minimum — a `cache_control` marker would be inert |

Counts include the one-character user turn and per-request overhead, so the
system prompts alone are slightly smaller than the figures above. Both are well
under the threshold either way.

## Why this was measured rather than assumed

Prompt caching looked like the obvious win: `SYNTHESIS_SYSTEM` is static, it is
the largest fixed block in the request, and it is sent on four parallel calls
per query. The measurement says it would do nothing, because a breakpoint below
the model's minimum prefix is accepted and silently ignored — no error, and
`cache_creation_input_tokens` stays at zero.

A second reason it would have underdelivered even above the threshold: a cache
entry only becomes readable once the first response has begun. The four carrier
calls are issued concurrently, so on any single query they would all miss and
all pay the write premium. The saving would come only from repeated queries
inside the cache TTL.

Padding the prompt to clear 1024 tokens would be writing text for the tokeniser
rather than for the model, and would make every clause in it less load-bearing.
It is not done.

**What would change this:** a larger corpus, if the per-carrier evidence block
moved into a shared prefix, or a model with a lower minimum. Re-run the script
before assuming the answer still holds.
