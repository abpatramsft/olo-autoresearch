# Constructing an Olo benchmark

Build new measurement infrastructure only inside the prepared `exp_0000`
worktree.

## Define the score first

Before writing code, state:

- the single number being returned;
- whether higher or lower is better;
- theoretical range;
- useful improvement size;
- expected noise.

If these cannot be stated clearly, the benchmark design is not ready.

## Assemble cases

For deterministic task scoring, prefer at least 10-20 varied cases when the
project supports it. Include normal, edge, malformed, boundary, and large cases.

For fuzzy or LLM-judge scoring, include calibration examples that are obviously
good and obviously bad.

For performance, warm up first and aggregate multiple samples. Do not optimize
from one noisy timing.

## Emit Olo evidence

The harness must:

- write a finite `score`;
- include a per-task score map when tasks exist;
- write one diagnostic trace per item;
- exit nonzero on infrastructure failure;
- avoid swallowing partial failures as a zero score.

## Goodhart audit

Write down at least one way the metric could be gamed:

- special-case known inputs;
- return a constant;
- skip expensive correctness work;
- copy expected answers;
- optimize formatting rather than substance.

Mitigate each risk with a gate, held-out slice, or scoring assertion.

## Gate pairing

Every constructed benchmark needs a real gate:

| Benchmark | Minimum gate |
|---|---|
| Hand-written pass rate | Held-out score floor or independent correctness set |
| Performance | Correctness regression suite |
| LLM judge | Structural validity and degenerate-output checks |
| Output quality | Non-empty, schema, range, and invariants |

The gate must exit nonzero when it detects a regression.

## Baseline inheritance

When `python olo.py baseline` succeeds, all benchmark files in `exp_0000` are
committed to `olo/exp_0000`. Descendant experiment branches inherit the same
measurement system while `main` remains clean.
