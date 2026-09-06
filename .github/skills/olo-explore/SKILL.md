---
name: olo-explore
description: Explore a repository, identify and rank optimization goals, construct or instrument a benchmark and real regression gates inside an isolated baseline worktree, audit the harness, and commit the measured Olo baseline. Use before olo-optimize on a fresh repository or whenever the measurement system must change.
---

# Olo Explore

This is phase one of Olo:

```text
repository
  -> understand goals
  -> choose a measurable dimension
  -> prepare exp_0000
  -> build benchmark and gates in exp_0000
  -> audit and check
  -> commit baseline
  -> ready for /olo-optimize
```

The benchmark, fixtures, and Olo-specific gates belong in the baseline worktree,
not on `main`. Every optimization descendant inherits them from `exp_0000`.

Use `python olo.py` on Windows and the available Python 3 launcher elsewhere.

## Firm rules

1. Do not create benchmark infrastructure on `main`.
2. Do not start optimization before the measured baseline is independently
  reviewed and its status becomes `committed`.
3. Constructed benchmarks require at least one real pass/fail gate.
4. Emit one trace per independently evaluated item whenever possible.
5. A benchmark command measures only. Training/build steps that produce an
   artifact happen before the benchmark.
6. Benchmark errors exit nonzero; never convert an execution failure into a
   successful score of zero.
7. Document metric meaning, direction, determinism, gaming risks, and binding
   resource before the baseline run.
8. Version the benchmark. Include hard counterexamples and distractors, not
  just paraphrases of visible cases. Keep development, reusable validation,
  and an untouched final test distinct. A perfect tiny benchmark is a ceiling,
  not evidence of production reliability.

## 1. Initialize exploration

If `.olo/config.json` does not exist:

```text
python olo.py explore init --name "<project>" --goal "<user goal>"
```

If the user supplied no goal, omit `--goal`; infer candidates from repository
evidence.

Initialization creates Olo state and starts the dashboard, but leaves target,
benchmark, and gates unconfigured.

If Olo already reports `phase=ready-for-baseline` and `config.json` already has
a target and benchmark, it came from the direct `olo init` compatibility path.
Skip goal construction and `explore configure`; prepare/check/audit the baseline
and run `python olo.py baseline`.

To replace a frozen measurement system, stop its dashboard and run
`python olo.py evaluation new --version <new-label> --from <approved-id-or-root>`.
This archives the prior state and resets scores. Never edit a measured harness
in place or mix results from different versions.

## 2. Explore the repository

Read the README, entry points, package manifests, tests, examples, profiling
code, TODO/FIXME comments, and existing evaluation scripts.

Identify:

- what the product actually does;
- behavior that appears important;
- existing measurements;
- unmeasured areas with plausible slack;
- behaviors that must never regress;
- benchmark runtime and resource constraints.

Read `references/proposing-dimensions.md`.

If one target is obvious from the user goal and repository, record it directly.
Otherwise propose a small ranked set. For every candidate, record it:

```text
python olo.py explore add-dimension \
  --name "<short-id>" \
  --description "<what better means>" \
  --target "<likely target path>" \
  --metric-name "<single score meaning>" \
  --direction <max|min> \
  --evidence "<repo-specific evidence>" \
  --complexity <none|minor|substantial> \
  --run-cost <small|medium|large>
```

Select the strongest signal with reasonable run cost:

```text
python olo.py explore select --name "<short-id>"
```

In interactive use, present genuinely ambiguous dimensions to the user once.
In unattended use, select the recommended dimension and preserve alternatives
as future candidates.

## 3. Prepare the baseline worktree

Run:

```text
python olo.py baseline --prepare
```

This creates `exp_0000` and returns its worktree. All benchmark construction and
instrumentation now happens at that exact path.

The explorer may edit files only inside this baseline worktree. Existing product
behavior should remain unchanged except for minimal instrumentation that is
strictly necessary to measure it.

## 4. Choose benchmark provenance

Classify the benchmark:

- `existing`: already runnable and already emits the Olo contract;
- `wrapped`: an existing test/eval is adapted to emit Olo results and traces;
- `constructed`: new cases and scoring logic are created.

Read `references/constructing-benchmark.md` for wrapped or constructed cases and
`.github/skills/olo-autoresearch/references/benchmark-contract.md` for the file
and environment contract.

The benchmark must produce:

```json
{
  "score": 0.75,
  "tasks": {
    "case-1": 1.0,
    "case-2": 0.5
  }
}
```

Write the result to `OLO_RESULT_PATH` when set and one JSON trace per item under
`OLO_TRACES_DIR`.

## 5. Build real gates

Constructed benchmarks require at least one gate. Prefer:

- existing unit/integration tests;
- held-out score-floor checks;
- correctness checks for performance benchmarks;
- structural checks against constant or empty output;
- deterministic checks for copied validation strings when leakage is possible.

A gate passes only through exit code zero. A command that prints a low score and
still exits zero is not a gate.

Protect the benchmark, gates, scorer, fixtures, and held-out data from descendant
experiments.

Configure `--critical-task <id>` for behavior that must not regress. Choose a
validation floor relative to the measured baseline and include relevant
invariants. Reusing validation makes it development feedback, not a final test.

## 6. Configure the discovered workspace

From the main repository root, configure Olo. Paths refer to files in the
baseline worktree:

```text
python olo.py explore configure \
  --target "<target>" \
  --editable "<target-or-source-root>" \
  --protect "<benchmark-file>" \
  --protect "<gate-or-held-out-file>" \
  --benchmark "<benchmark command>" \
  --benchmark-origin <existing|wrapped|constructed> \
  --gate "<name>::<real pass/fail command>" \
  --metric <max|min> \
  --unit "<one benchmark item>" \
  --determinism <deterministic|temp-zero|noisy> \
  --resource-profile "<binding resource and concurrency safety>" \
  --meaningful-improvement "<noise floor or useful delta>" \
  --min-improvement 0.01 \
  --evaluation-version "<version>" \
  --score-ceiling 1.0 \
  --final-test "<separate final-test command>" \
  --repo-summary "<what the repository does>" \
  --gaming-risk "<specific metric-gaming risk>"
```

Add repeated `--gaming-risk` and `--future-dimension` values as needed. This
creates `.olo/project.md` and changes the phase to `ready-for-baseline`.
The numeric `--min-improvement`, optional `--min-relative-improvement`,
`--critical-task`, and `--max-task-regression` are enforced, not inferred from
prose. Set a ceiling only when the metric has a known limit. Set
`--max-evaluations` for a run-wide cap. Omit `--final-test` only when no genuinely
separate final set is available, and state that limitation in the report.

## 7. Audit the harness

Invoke `olo-benchmark-reviewer` in `audit` mode with:

- main repository path;
- baseline worktree path;
- literal benchmark command;
- one-line benchmark unit;
- benchmark origin.

It must check:

- numeric score contract;
- per-item traces;
- protected paths;
- real gates;
- held-out leakage;
- benchmark failures remaining nonzero;
- metric gaming strategies.

Address every blocking finding and re-run the audit.

## 8. Run a non-committing wiring check

```text
python olo.py run exp_0000 --check
```

This runs the real benchmark and gates, writes evidence under
`.olo/experiments/exp_0000/checks/`, and does not consume an attempt or commit
the worktree.

Require:

- status `check-passed`;
- finite score;
- all gates pass;
- expected task traces exist.

## 9. Measure and approve the baseline

Run:

```text
python olo.py baseline
```

Olo captures the benchmark, fixtures, instrumentation, and gates in the
baseline branch, records its score and measurement fingerprint, and returns
`pending-review`. Have an independent verifier review the saved evidence and
record `python olo.py review exp_0000 --verdict approve --reviewer <name>
--reason "<scope, gates, traces, and measurement audit>"`. Only approval changes
the phase to `ready-to-optimize`. Do not read or run the final-test cases during
optimization; finalization executes that command once after winner selection.

Confirm:

```text
python olo.py explore status
python olo.py show exp_0000
python olo.py doctor
```

## 10. Handoff

Report:

- selected optimization goal;
- target and editable scope;
- benchmark score meaning and direction;
- baseline score;
- gate names;
- determinism and resource profile;
- dashboard URL;
- preserved future dimensions.

Finish with:

```text
Exploration complete. Use /olo-optimize to start experiment rounds.
```
