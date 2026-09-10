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

For noisy performance goals, include an unchanged control path or paired,
interleaved parent/candidate samples in the measurement design. An equal speedup
on untouched work suggests ambient drift, not an attributable candidate gain.
Record the control results and the noise floor; minimum-of-many timing alone
does not remove this confounder.

Include both positive controls and hard negative cases. Exercise the scorer and
gates with obviously wrong outputs, such as empty results or every possible
answer, and require rejection. Keep these calibration checks in harness tests,
not product edits. Distinct wording alone does not make a held-out set
independent: separate identities, examples, and relevant failure cases.

Document existing input and API support separately from the chosen objective.
A benchmark deliberately limited to one language, input class, or workload is
not permission to remove behavior outside that slice. Preserve cheap existing
compatibility checks as gates, and have independent review look for collateral
restrictions that a perfect narrow score would miss.

## Emit Olo evidence

The harness must:

- write a finite `score`;
- include a per-task score map when tasks exist;
- write one diagnostic trace per item;
- exit nonzero on infrastructure failure;
- avoid swallowing partial failures as a zero score.

Malformed task scores are errors, not entries to omit. Every task score must
have exactly one corresponding JSON trace with the same score. Gate commands
receive separate result and trace paths; do not mix validation traces into the
development result. Use `{python}` in Python commands to bind the interpreter
and its installed dependencies when configuring the harness.

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

Before freezing, run two unchanged deterministic checks (three for noisy or
temp-zero runs) and `python olo.py explore assess`. A changed source or harness
invalidates earlier checks. An already-saturated benchmark, unstable task
scores, or an improvement threshold smaller than observed noise needs repair,
not an optimization round. A deterministic baseline measurement must also
match its checked result.
