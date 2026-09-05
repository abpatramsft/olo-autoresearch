# Olo Optimization, in Simple Terms

This document explains what Olo does after the baseline has been prepared. For
the complete end-to-end explanation, see [wokring.md](wokring.md).

## The starting point

`exp_0000` is the measured baseline. It contains the benchmark, gates,
fixtures, and the original product behavior that later experiments must beat.

Olo does not edit the main checkout while optimizing. Each candidate gets its
own Git branch and worktree:

```text
main                         normal project checkout
olo/exp_0000                 measured baseline
olo/exp_0001, exp_0002, ...  candidate improvements
```

The Copilot orchestrator decides what to investigate. The Python control plane
creates worktrees, runs measurements, records results, and enforces the rules.

## One optimization round

A round follows this shape:

```text
read current results
  -> choose promising parent experiments
  -> propose different hypotheses
  -> create isolated candidate worktrees
  -> edit each candidate
  -> run verification, benchmark, and gates
  -> commit valid improvements
  -> close the round
```

A candidate counts as an improvement only when it beats its parent in the
configured metric and passes all gates and verification. A higher score wins
when the metric is `max`; a lower score wins when it is `min`.

## Width and budget

**Width** is the number of candidate paths that may be explored in one round.
For example, width `3` allows three different hypotheses to be investigated.

**Budget** is the number of concrete attempts an experimenter may make on one
candidate path. For example, budget `2` allows an initial implementation and
one repair informed by benchmark feedback.

```text
width = breadth across ideas
budget = depth within one idea
```

The orchestrator chooses these values from the benchmark's resource profile.
CPU-light isolated work can often use width `3`. Timing-sensitive benchmarks,
exclusive GPUs, shared databases, ports, or rate-limited APIs normally require
width `1`.

You can request values explicitly:

```text
Use /olo-optimize in autonomous mode.
Use width=2 and budget=3 for every round. Do not merge.
```

## The best experiment

Every valid committed experiment has an aggregate benchmark score. Olo reports
the highest-scoring committed experiment for a `max` metric, or the
lowest-scoring one for a `min` metric, as `best_experiment`.

The winner can come from any surviving path:

```text
exp_0000  score 0.75
|-- exp_0001  score 0.78
|   `-- exp_0004  score 0.81  <- current best
|-- exp_0002  score 0.80
`-- exp_0003  score 0.77
    `-- exp_0006  score 0.83  <- becomes the new best
```

Passing gates is mandatory. A candidate with a better benchmark score but a
failed gate does not become a valid winner.

## The Pareto-style frontier

Looking only at the average score can hide useful experiments. One candidate
may be strongest on one benchmark task while another is strongest on a
different task.

Consider three retrieval experiments:

| Experiment | Query A | Query B | Query C | Overall |
|---|---:|---:|---:|---:|
| `exp_0001` | **0.90** | 0.60 | 0.60 | 0.70 |
| `exp_0002` | 0.70 | **0.90** | 0.70 | **0.77** |
| `exp_0003` | 0.60 | 0.70 | **0.90** | 0.73 |

`exp_0002` is best overall, but `exp_0001` and `exp_0003` contain useful
specialized behavior. Olo's default `pareto-per-task` strategy keeps such
directions available instead of immediately following only the aggregate
winner.

The implementation is a practical per-task ranking:

1. Consider committed branch leaves, meaning committed experiments without a
   committed child.
2. Count how many benchmark tasks each leaf wins.
3. Rank leaves by task wins.
4. Break ties using the aggregate score and then the experiment ID.
5. Return the requested number of frontier candidates.

This is called Pareto-style because it preserves different strengths. It is
not a complete mathematical Pareto-dominance calculation.

Frontier membership and overall winner mean different things:

```text
frontier candidate = useful path worth considering again
best experiment    = strongest valid aggregate score right now
```

Later rounds may create children from different frontier parents. If a child
from an initially weaker path eventually earns the best aggregate score, it
becomes the new `best_experiment`.

## Are successful paths combined?

Olo does not automatically combine sibling branches.

```text
exp_0002 -> exp_0004  improves query rewriting
exp_0003 -> exp_0006  improves ranking weights
```

It will not automatically create `exp_0004 + exp_0006`. The changes could
conflict or perform worse together even when each is useful alone.

Combination should be treated as another measured hypothesis. The
orchestrator can create a new child from one branch, deliberately apply the
compatible idea from the other branch, and then run the full benchmark and
gates. Ask for this explicitly when desired:

```text
Inspect strong experiments from different frontier branches. Create and test
combination experiments for compatible improvements. Do not merge to main.
```

## Autonomous mode and stopping

Autonomous optimization starts with:

```powershell
python olo.py mode start
```

Bounded optimization uses `--bounded` and stops at the requested boundary.
Autonomous mode continues through rounds until one of these conditions occurs:

- the configured score ceiling is reached;
- the stall limit is reached;
- the user stops it;
- measurement integrity is uncertain;
- continuing would be unsafe for the available resources.

The stall count increases when a completed round does not improve the global
best score. It resets to zero after an improving round. The default stall limit
is three unless exploration or the user configures another value.

## What the dashboard shows

The dashboard shows more than the winning path:

- the experiment graph shows all scored experiment branches;
- the current best path is highlighted;
- the frontier section shows the currently ranked frontier choices;
- the ledger includes pending, evaluated, committed, failed, and discarded
  experiments;
- experiment details show hypotheses, gates, recorded diffs, and benchmark
  output.

## What happens at the end

Olo leaves the winning experiment on its own branch. It does not merge that
branch into `main` automatically.

Inspect the result with:

```powershell
python olo.py status --json
python olo.py tree
python olo.py show <experiment-id>
python olo.py diff <experiment-id>
python olo.py report
```

The user reviews the winning branch and decides whether it should be merged.
If the winner is a descendant of earlier experiments, it already contains the
changes inherited from those ancestors.
