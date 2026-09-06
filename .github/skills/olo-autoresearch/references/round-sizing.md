# Sizing an Olo round

Width is live experiment concurrency. Budget is the planned evaluation allowance
per worker. Olo enforces a shared round cap of `width * budget`; the orchestrator
must keep each worker within its assigned allowance.

Every benchmark-bearing `run`, `probe`, and `run --check` consumes an evaluation.
A probe does not consume an experiment attempt, but it is not free evaluation.
Reserve room for the measured `run` required for independent approval. Read saved
outcomes to summarize results; never repeat a benchmark merely to retrieve output.

## Choose width from the binding resource

| Binding resource | Default width |
|---|---:|
| Exclusive GPU, device, port, database fixture, or mutable external system | 1 |
| Timing or latency benchmark sensitive to sibling load | 1, unless the harness proves stable under concurrency |
| External API with a strict rate limit | Rate-limit-safe concurrency |
| CPU-light, isolated unit benchmark | `min(available safe slots, 3)` |
| Unknown resource behavior | 1 |

Git worktrees isolate files, not hardware, ports, caches, databases, or external
quotas. Never increase width merely because worktrees are available.

## Choose budget from feedback quality

- Budget 1: one measured candidate, no exploratory probe.
- Budget 2: one recorded probe followed by one measured candidate.
- Budget 3: room for another recorded probe or a repaired failed execution.
- Budget 4+: use only when the expected information justifies the extra
  evaluations and the total study cap permits them.

Use a lower budget when failures are deterministic and a sibling direction is
more valuable than another retry. Setup/preflight blocks do not consume an
evaluation. Never increase or reset the budget to hide accidental duplicate runs.

## Noisy benchmarks

If concurrency can bias the score, use width 1. If the benchmark is stochastic,
aggregate replicates inside one benchmark invocation and return the decision
statistic. Never promote the best lucky replicate.
