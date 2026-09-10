# Proposing optimization dimensions

Use this only when the user's goal or an existing benchmark does not make the
choice obvious.

## Repository evidence

Look for:

1. README claims such as fast, reliable, secure, small, or accurate.
2. Existing tests and evaluation scripts.
3. Profiling and metrics calls.
4. TODO, FIXME, HACK, and known limitation comments.
5. Error recovery and fallback paths.
6. Issue references or examples that reveal user pain.

## Evaluate each candidate

Use prose rather than invented numerical ratings:

- Signal: does this metric represent real product quality?
- Slack: is there believable room to improve?
- Cost: how expensive is each repeated run?
- Gaming risk: how could code raise the metric dishonestly?
- Gateability: can important regressions be detected cheaply?

## Common starting points

| Project | Candidate measurements |
|---|---|
| Agent/LLM | Task success, token cost per success, refusal calibration, tool-error recovery |
| Web/API | Contract success, p95/p99 latency, cold start, memory per request |
| Library | Corpus correctness, import time, allocation count, package size |
| Parser/compiler | Golden-output correctness, compile/parse time, output size |
| Data pipeline | Throughput, peak memory, replay idempotency, schema-drift handling |
| CLI | Exit-code correctness, cold start, output stability, task completion |
| Retrieval | Recall@K, grounding rate, query latency, indexing cost |

Prefer the highest-signal candidate whose benchmark is affordable enough to run
many times. Preserve non-selected candidates in discovery state and
`.olo/project.md`.

## Confirm the signal before freezing it

An all-green demonstration often makes a useful regression gate and a poor
optimization objective. Record concrete failing behaviors or counterexamples
for the chosen dimension, then use repeated `run exp_0000 --check` and
`explore assess` to establish actual headroom. Do not infer room to improve from
README promises alone or invent numerical candidate ratings.

Keep discovery proportional: start with the product README, entry points, and
existing evaluation, and stop widening the search once a defensible, affordable
goal is established. Exclude generated files and Olo's own kit/worktrees from
product-code discovery unless Olo itself is the target.
