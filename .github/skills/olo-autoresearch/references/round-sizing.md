# Sizing an Olo round

Width is live experiment concurrency. Budget is how many concrete iterations an
experimenter may attempt on its branch.

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

- Budget 1: cheap, obvious independent hypotheses.
- Budget 2-3: implementation may need one repair after trace feedback.
- Budget 4+: expensive model or systems experiments where preserving branch
  context is worth the extra attempts.

Use a lower budget when failures are deterministic and a sibling direction is
more valuable than another retry.

## Noisy benchmarks

If concurrency can bias the score, use width 1. If the benchmark is stochastic,
aggregate replicates inside one benchmark invocation and return the decision
statistic. Never promote the best lucky replicate.
