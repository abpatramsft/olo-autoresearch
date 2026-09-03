# How Olo Works, From First Principles

Olo is a project-local autoresearch system for GitHub Copilot CLI.

Its job is not simply to ask Copilot to "make the code better." That request is
too vague. Olo first creates a trustworthy way to measure "better," and only
then allows Copilot to run optimization experiments.

Olo therefore has two separate phases:

```text
PHASE 1: /olo-explore

Understand repository
  -> identify possible goals
  -> choose one measurable goal
  -> create exp_0000
  -> build benchmark and gates
  -> check measurement wiring
  -> measure and commit baseline


PHASE 2: /olo-optimize

Read baseline failures
  -> generate candidate ideas
  -> create exp_0001, exp_0002, ...
  -> edit isolated worktrees
  -> verify each candidate
  -> run benchmark and gates
  -> keep improvements
  -> reject regressions
  -> continue until ceiling or stall
```

## The fundamental idea

Olo separates **creativity** from **truth**.

| Responsibility | Owner |
|---|---|
| Suggest a possible improvement | Copilot |
| Write the candidate change | Copilot |
| Decide what score was actually produced | Benchmark |
| Decide whether important behavior broke | Gates |
| Compare candidate score against parent | Olo Python code |
| Keep or reject the candidate branch | Olo Python code |
| Decide whether to merge into main | User |

Copilot is useful for generating hypotheses and editing code, but Copilot is not
allowed to declare its own work successful.

Olo accepts a candidate only when:

```text
benchmark completed successfully
AND every gate passed
AND candidate score is strictly better than parent score
AND verification has no blocking finding
```

## Laboratory analogy

| Olo component | Laboratory equivalent |
|---|---|
| `/olo-explore` | Designing the experiment and measurement instruments |
| `/olo-optimize` | Running the actual research experiments |
| `olo-explorer` | Lead scientist defining what will be measured |
| `olo-orchestrator` | Lab manager scheduling experiments |
| `olo-ideator` | Researcher proposing new directions |
| `olo-experimenter` | Engineer implementing one experiment |
| `olo-verifier` | Safety and validity inspector |
| `olo-benchmark-reviewer` | Measurement and failure analyst |
| Git worktree | Isolated laboratory bench |
| Benchmark | Measuring instrument |
| Gate | Safety test |
| `.olo\` | Laboratory notebook |
| Dashboard | Observation window |

# Phase 1: Explore

## Why exploration must happen first

Imagine telling an optimizer:

> Make this web API better.

"Better" could mean:

- lower latency;
- fewer errors;
- lower memory use;
- higher test pass rate;
- stronger security;
- more stable output;
- cheaper external API usage.

These are different goals. A change that improves one may damage another.

Before optimization, Olo needs a precise sentence such as:

> Return the mean correctness rate across 20 malformed-input parser cases.
> Higher is better. Existing parser regression tests must continue to pass.

That sentence gives Olo:

- one numerical measurement;
- one direction;
- one set of protected behavior;
- one repeatable command.

## Step 1: Start exploration

The explorer runs:

```powershell
python olo.py explore init --name "my-project" --goal "Improve parser correctness"
```

The goal is optional. If the user does not provide one, the explorer must infer
candidate goals from repository evidence.

### Input

```text
project name
optional user goal
current committed repository
```

### What Olo creates

```text
.olo/
  config.json
  discovery.json
  graph.json
  meta.json
  project.md
  events.jsonl
  worktrees/
  experiments/
```

At this point:

```json
{
  "phase": "exploring",
  "target": "",
  "benchmark": "",
  "gates": []
}
```

This is intentional. Olo exists, but the measurement system has not been
selected yet.

### Output

The command returns:

```json
{
  "initialized": true,
  "phase": "exploring",
  "next": "Inspect the repository and record candidate dimensions."
}
```

The dashboard starts, but it initially shows discovery state rather than
optimization results.

## Step 2: Explore the repository

The `olo-explorer` agent reads:

- README files;
- entry points;
- package manifests;
- source modules;
- existing tests;
- examples;
- benchmarks and profilers;
- TODO and FIXME comments;
- error handling and fallback paths;
- configuration;
- documented product promises.

The explorer is trying to answer:

```text
What does this project do?
What behavior matters?
What is already measured?
What important behavior is not measured?
Where is there believable room for improvement?
How expensive would repeated measurement be?
How could each metric be cheated?
```

## Step 3: Identify optimization dimensions

An optimization dimension is one measurable meaning of "better."

Examples:

| Project | Possible dimension |
|---|---|
| Parser | Valid corpus pass rate |
| API | p99 request latency |
| CLI | Correct exit-code rate |
| Agent | Task success rate |
| Retrieval system | Recall@K |
| Data pipeline | Rows processed per second |
| Library | Cold import time |

The explorer records dimensions:

```powershell
python olo.py explore add-dimension `
  --name correctness `
  --description "Increase representative parser case pass rate" `
  --target src/parser.py `
  --metric-name "mean corpus pass rate" `
  --direction max `
  --evidence "README promises malformed-input support but no benchmark exists" `
  --complexity minor `
  --run-cost small
```

### Inputs

| Input | Meaning |
|---|---|
| `name` | Short stable identifier |
| `description` | Plain-language definition of improvement |
| `target` | Likely code area |
| `metric-name` | Meaning of the single score |
| `direction` | `max` or `min` |
| `evidence` | Why this goal is grounded in this repository |
| `complexity` | Cost to construct measurement |
| `run-cost` | Cost of every repeated experiment |

### Output

Dimensions are written to:

```text
.olo\discovery.json
```

Example:

```json
{
  "dimensions": [
    {
      "name": "correctness",
      "target": "src/parser.py",
      "metric_name": "mean corpus pass rate",
      "direction": "max",
      "complexity": "minor",
      "run_cost": "small"
    }
  ]
}
```

## Step 4: Select one goal

The explorer ranks candidates using:

- signal: does this score represent real product value;
- slack: is there believable room to improve;
- repeated run cost;
- metric-gaming risk;
- ability to build useful gates.

It selects one:

```powershell
python olo.py explore select --name correctness
```

Non-selected dimensions remain in discovery state for later searches.

## Step 5: Prepare the baseline worktree

The explorer runs:

```powershell
python olo.py baseline --prepare
```

This creates:

```text
Experiment: exp_0000
Branch:     olo/exp_0000
Worktree:   .olo/worktrees/exp_0000/
Status:     pending
```

No benchmark runs yet.

### Why this is important

Benchmark infrastructure should not pollute `main`.

```text
main
  product code
  Olo framework files

olo/exp_0000
  product code
  benchmark
  gates
  fixtures
  instrumentation
```

All benchmark construction happens in the `exp_0000` worktree.

Later experiments branch from `exp_0000`, so they inherit the same measurement
system.

## Step 6: Find, wrap, or construct the benchmark

The explorer classifies the benchmark.

| Origin | Meaning |
|---|---|
| `existing` | The repository already has a runnable benchmark that emits Olo evidence |
| `wrapped` | Existing tests or evaluation exist but need an Olo wrapper |
| `constructed` | New cases and scoring logic must be created |

If no benchmark exists, the explorer creates it inside:

```text
.olo\worktrees\exp_0000\
```

It does not create the benchmark in the main checkout.

## Step 7: Design the score

Before writing benchmark code, the explorer defines:

```text
What single number is produced?
Is higher or lower better?
What is the score range?
What improvement is meaningful?
How noisy is the measurement?
```

Examples:

```text
Mean pass rate over 20 tasks, range 0.0 to 1.0, higher is better.
```

```text
Median processing latency in milliseconds, lower is better.
```

```text
Recall@10 across 100 queries, range 0.0 to 1.0, higher is better.
```

`--metric max|min` tells Olo the direction. It does not define the score itself.

## Step 8: Build benchmark cases

For deterministic correctness benchmarks, the explorer should normally include:

- normal cases;
- edge cases;
- malformed cases;
- boundary values;
- empty input;
- larger inputs where relevant.

For performance benchmarks:

- warm up first;
- use realistic workloads;
- collect several samples;
- return an aggregate rather than one noisy timing.

For fuzzy or LLM-judge benchmarks:

- include clearly good calibration examples;
- include clearly bad calibration examples;
- document expected variance and cost.

## Step 9: Emit the Olo benchmark contract

Olo supplies these environment variables:

| Variable | Meaning |
|---|---|
| `OLO_EXPERIMENT_ID` | Current experiment ID |
| `OLO_WORKTREE` | Absolute experiment worktree |
| `OLO_TARGET` | Absolute configured target |
| `OLO_RESULT_PATH` | File where final result JSON must be written |
| `OLO_TRACES_DIR` | Directory for per-task trace files |

The benchmark writes:

```json
{
  "score": 0.75,
  "tasks": {
    "case-1": 1.0,
    "case-2": 0.5
  }
}
```

Each independent item should also produce a trace:

```json
{
  "experiment_id": "exp_0000",
  "task_id": "case-2",
  "status": "failed",
  "score": 0.0,
  "summary": "actual=x expected=y",
  "failure_reason": "wrong-output"
}
```

Per-task traces are important because future agents need to understand which
cases fail and why.

## Step 10: Perform the Goodhart check

Goodhart's law means:

> When a measurement becomes a target, the optimizer may learn to improve the
> measurement without improving the real product.

The explorer asks:

```text
Could the target special-case visible benchmark inputs?
Could it return a constant that scores well?
Could it skip expensive correctness work?
Could it copy expected answers?
Could it satisfy output formatting without solving the task?
```

Each realistic gaming strategy is recorded in `.olo\project.md`.

## Step 11: Create gates

A benchmark asks:

> Did the target metric improve?

A gate asks:

> Did something important regress or become dishonest?

Examples:

| Benchmark | Gate |
|---|---|
| Lower latency | Existing correctness tests |
| Higher task pass rate | Held-out independent task slice |
| Better LLM-judge quality | Non-empty and schema-valid output |
| Smaller output | Exact behavior equivalence |

A constructed benchmark must have at least one gate.

The gate must exit:

```text
0     pass
nonzero     fail
```

A command that merely prints a low score and exits zero is not a real gate.

## Step 12: Configure the explored workspace

After benchmark and gate files exist in `exp_0000`, the explorer runs:

```powershell
python olo.py explore configure `
  --target src/parser.py `
  --editable src/parser.py `
  --protect benchmarks/parser_benchmark.py `
  --protect benchmarks/parser_gate.py `
  --benchmark "python benchmarks/parser_benchmark.py --target {target}" `
  --benchmark-origin constructed `
  --gate "held-out::python benchmarks/parser_gate.py --target {target}" `
  --metric max `
  --unit "parser corpus case" `
  --determinism deterministic `
  --resource-profile "CPU-light isolated process; start width 1" `
  --meaningful-improvement "At least one additional case passing" `
  --repo-summary "Parser library for structured input" `
  --gaming-risk "The target could special-case visible cases"
```

### Input

This command receives the final measurement design:

- target;
- editable paths;
- protected paths;
- benchmark command;
- benchmark origin;
- gates;
- metric direction;
- benchmark unit;
- determinism;
- resource profile;
- useful score delta;
- repository summary;
- gaming risks.

### Output

Olo changes phase:

```text
exploring -> ready-for-baseline
```

It writes a human-readable summary to:

```text
.olo\project.md
```

## Step 13: Audit the benchmark

The explorer invokes `olo-benchmark-reviewer` in audit mode.

The reviewer checks:

- numeric score exists;
- result contract is valid;
- per-task traces are emitted;
- benchmark errors remain nonzero;
- a constructed benchmark has a real gate;
- held-out data is not used by the target;
- protected paths are correct;
- obvious metric-gaming paths are mitigated.

The reviewer is read-only.

Blocking findings must be fixed before baseline.

## Step 14: Run a non-committing wiring check

The explorer runs:

```powershell
python olo.py run exp_0000 --check
```

This performs the real benchmark and gate commands but:

- does not consume an experiment attempt;
- does not change experiment status;
- does not commit the branch;
- does not compare the score against a parent.

Artifacts are stored under:

```text
.olo\experiments\exp_0000\checks\001\
```

Successful output:

```json
{
  "experiment_id": "exp_0000",
  "check": 1,
  "status": "check-passed",
  "score": 0.4,
  "gates_passed": true,
  "trace_count": 10
}
```

This proves the measurement machinery is wired correctly before committing the
baseline.

## Step 15: Run and commit the baseline

The explorer runs:

```powershell
python olo.py baseline
```

Olo:

1. runs the benchmark;
2. runs every gate;
3. captures logs and traces;
4. captures the complete baseline worktree diff;
5. commits benchmark, fixture, gate, and instrumentation files to
   `olo/exp_0000`;
6. records the baseline score;
7. changes phase to `ready-to-optimize`.

Example:

```json
{
  "experiment_id": "exp_0000",
  "status": "committed",
  "score": 0.4,
  "gates_passed": true,
  "trace_count": 10
}
```

At this point:

```text
/olo-explore is complete
/olo-optimize may begin
```

# Phase 2: Optimize

## Step 16: Verify readiness

The orchestrator runs:

```powershell
python olo.py status --json
```

It refuses to optimize unless:

```json
{
  "phase": "ready-to-optimize",
  "best_experiment": "exp_0000"
}
```

This prevents optimization against an undefined or uncommitted benchmark.

## Step 17: Start optimization mode

Bounded:

```powershell
python olo.py mode start --bounded
```

Autonomous:

```powershell
python olo.py mode start
```

Bounded means run the requested amount and stop.

Autonomous means continue until:

- score ceiling;
- stall limit;
- user stop;
- measurement-integrity concern;
- unsafe resource condition.

## Step 18: Start a round

```powershell
python olo.py round start --width 1 --budget 1
```

| Value | Meaning |
|---|---|
| Width | Number of simultaneous experiment lanes |
| Budget | Candidate iterations allowed per lane |

Worktrees isolate files but do not isolate hardware, databases, ports, or API
quotas. Width must follow `.olo\project.md`'s resource profile.

## Step 19: Read shared state

```powershell
python olo.py scratchpad
python olo.py frontier
```

The scratchpad contains:

- best score;
- experiment tree;
- baseline and candidate failures;
- discarded hypotheses;
- proposals;
- annotations;
- active mode;
- stall count;
- discovery context.

## Step 20: Generate experiment ideas

`olo-ideator` can perform:

| Brief | Purpose |
|---|---|
| `failure-analysis` | Cluster repeated failure modes |
| `frontier-extrapolation` | Deepen the strongest observed direction |
| `literature` | Find external ideas not already tried |

The ideator records proposals but does not edit or run candidates.

## Step 21: Select frontier parents

The frontier contains committed leaves that can safely be extended.

Strategies:

| Strategy | Behavior |
|---|---|
| `argmax` | Current aggregate best |
| `top-k` | Highest K aggregate scores |
| `epsilon-greedy` | Mostly best, occasionally exploratory |
| `softmax` | Score-weighted sampling |
| `pareto-per-task` | Preserve task specialists hidden by aggregate score |

Failed, evaluated, and discarded experiments remain evidence but are not valid
parents.

## Step 22: Write experiment briefs

Each experimenter receives:

```text
Objective:
Evidence:
Parent:
Boundaries:
Pointer traces:
Iteration budget:
Required final JSON:
```

The objective explains where improvement may exist. The experimenter decides
the exact code change.

## Step 23: Create a candidate worktree

```powershell
python olo.py new `
  --parent exp_0000 `
  --hypothesis "Change function X in file Y so task Z improves because..."
```

Olo creates:

```text
exp_0001
olo/exp_0001
.olo/worktrees/exp_0001/
```

The candidate inherits:

- product code;
- benchmark;
- gates;
- fixtures;
- baseline instrumentation.

## Step 24: Edit only the candidate

The `olo-experimenter` edits only:

```text
.olo\worktrees\exp_0001\
```

It may edit configured editable paths.

It may not edit:

- benchmark;
- gates;
- scorer;
- fixtures;
- held-out data;
- unrelated paths outside the configured scope.

## Step 25: Pre-verify

```powershell
python olo.py verify exp_0001 --phase pre
```

Structural checks:

- worktree exists;
- candidate has changes;
- protected paths unchanged;
- changes stay inside editable scope;
- hypothesis is specific.

Semantic `olo-verifier` checks:

- no benchmark-answer hard-coding;
- no held-out leakage;
- no scorer manipulation;
- no fake or no-op solution;
- no resource conflict.

## Step 26: Run benchmark and gates

```powershell
python olo.py run exp_0001
```

The benchmark measures the candidate.

The gates protect required behavior.

Olo records:

```text
benchmark-result.json
benchmark.stdout.log
benchmark.stderr.log
gate-*.stdout.log
gate-*.stderr.log
diff.patch
outcome.json
traces/task_*.json
```

## Step 27: Keep or reject

```text
benchmark error
  -> failed

benchmark works, gate fails
  -> evaluated

benchmark works, gates pass, score not better
  -> evaluated

benchmark works, gates pass, score strictly better
  -> committed
```

Possible statuses:

| Status | Meaning |
|---|---|
| `pending` | Worktree exists but has not run |
| `active` | Benchmark currently running |
| `committed` | Valid strict improvement |
| `evaluated` | Measured but not accepted |
| `failed` | Could not produce a valid measurement |
| `discarded` | Deliberately rejected and cleaned |

## Step 28: Post-verify

Post-verification checks:

- all gates really ran;
- traces match aggregate score;
- duration is plausible;
- evaluation was not skipped;
- cache did not fake progress;
- only allowed files changed;
- the candidate solved substance, not just format.

## Step 29: Return structured handoff

The experimenter returns:

```json
{
  "experiment_id": "exp_0001",
  "status": "committed",
  "score": 0.8,
  "parent": "exp_0000",
  "verification": "pass",
  "learnings": [
    "Specific observation supported by traces"
  ]
}
```

The `subagentStop` hook asks the experimenter to repair a prose-only handoff.

## Step 30: Close the round

```powershell
python olo.py round close
```

Olo compares:

```text
best_before
best_after
```

Improvement resets stall count to zero.

No improvement increments stall count.

Score ceiling or stall limit stops autonomous mode.

# Main checkout and branches

The resulting Git shape is:

```text
main
  product source
  project-local Olo framework

olo/exp_0000
  measurement foundation
  benchmark
  gates
  fixtures
  baseline score

olo/exp_0001
  exp_0000 contents
  candidate change A

olo/exp_0002
  exp_0000 contents
  candidate change B
```

Nothing is merged automatically.

# Copilot files

| Path | Responsibility |
|---|---|
| `.github\skills\olo-autoresearch\SKILL.md` | Routes requests to explore or optimize |
| `.github\skills\olo-explore\SKILL.md` | Canonical discovery and baseline procedure |
| `.github\skills\olo-optimize\SKILL.md` | Canonical optimization loop |
| `.github\skills\olo-explore\references\proposing-dimensions.md` | Goal-identification method |
| `.github\skills\olo-explore\references\constructing-benchmark.md` | Benchmark construction method |
| `.github\skills\olo-autoresearch\references\benchmark-contract.md` | Result and trace contract |
| `.github\skills\olo-autoresearch\references\round-sizing.md` | Safe width and budget guidance |
| `.github\skills\olo-autoresearch\references\experiment-protocol.md` | Candidate-agent protocol |
| `.github\agents\olo-explorer.agent.md` | Phase-one orchestrator |
| `.github\agents\olo-orchestrator.agent.md` | Phase-two orchestrator |
| `.github\agents\olo-ideator.agent.md` | Proposal generation |
| `.github\agents\olo-experimenter.agent.md` | Candidate implementation and execution |
| `.github\agents\olo-verifier.agent.md` | Candidate validity audit |
| `.github\agents\olo-benchmark-reviewer.agent.md` | Measurement and trace audit |
| `.github\hooks\olo.json` | Copilot lifecycle integration |
| `.github\copilot-instructions.md` | Always-on repository safety rules |

# Python files

| Path | Responsibility |
|---|---|
| `olo.py` | Local launcher |
| `src\olo\cli.py` | Commands and argument parsing |
| `src\olo\state.py` | Config, discovery, graph, mode, rounds, notes |
| `src\olo\gitops.py` | Worktrees, branches, diffs, commits, cleanup |
| `src\olo\runner.py` | Checks, benchmarks, gates, acceptance decisions |
| `src\olo\verification.py` | Deterministic structural verification |
| `src\olo\frontier.py` | Parent ranking strategies |
| `src\olo\hooks.py` | Hook decisions and injected context |
| `src\olo\dashboard.py` | Local HTTP server and state APIs |
| `src\olo\utils.py` | Locks, atomic writes, timestamps, process helpers |
| `src\olo\web\index.html` | Dashboard structure |
| `src\olo\web\style.css` | Dashboard appearance |
| `src\olo\web\app.js` | Dashboard polling and graph rendering |

# Runtime state

| Path | Contents |
|---|---|
| `.olo\config.json` | Active phase, target, benchmark, gates, metric, boundaries |
| `.olo\discovery.json` | Candidate dimensions, selected goal, benchmark plan |
| `.olo\project.md` | Human-readable goal, measurement, risks, resources |
| `.olo\graph.json` | Experiment tree and scores |
| `.olo\meta.json` | Next ID, mode, round, stalls, dashboard process |
| `.olo\annotations.jsonl` | Verifier and reviewer findings |
| `.olo\proposals.jsonl` | Ideator proposals |
| `.olo\events.jsonl` | Hook and control-plane event history |
| `.olo\worktrees\exp_NNNN\` | Isolated Git checkouts |
| `.olo\experiments\exp_NNNN\checks\` | Non-committing wiring checks |
| `.olo\experiments\exp_NNNN\attempts\` | Real benchmark attempt evidence |

# Hook behavior

| Hook | Behavior |
|---|---|
| `sessionStart` | Directs fresh or incomplete work to `/olo-explore`, ready work to `/olo-optimize` |
| `preToolUse` | Blocks direct main-target edits during optimization |
| `postToolUse` | Reminds agents to trust recorded outcomes |
| `subagentStart` | Injects role-specific rules |
| `subagentStop` | Requires structured experimenter results |
| `agentStop` | Nudges autonomous optimization to continue |
| `errorOccurred` | Records the event |

# Existing benchmark versus no benchmark

If a benchmark already exists:

```text
/olo-explore
  -> inspect it
  -> instrument or wrap if necessary
  -> protect it
  -> audit it
  -> check it
  -> commit exp_0000
```

If no benchmark exists:

```text
/olo-explore
  -> infer candidate goals
  -> select one
  -> create exp_0000
  -> construct cases, scoring, traces, and gates in exp_0000
  -> audit and check
  -> commit baseline
```

In both cases, `/olo-optimize` sees the same final contract:

```text
committed exp_0000
finite baseline score
per-task evidence
real gates
protected measurement files
documented resource profile
```

# Included original policy example

The original validated example produced:

```text
exp_0000 baseline score: 0.6
exp_0001 candidate score: 1.0
gate: pass
pre-verification: pass
post-verification: pass
changed product file: agent.py
benchmark and gate changed: no
winning branch merged: no
```

# Verified benchmark-free repository example

The complete two-phase workflow was also run with GitHub Copilot CLI on a fresh
repository containing only:

```text
README.md
slugify.py
project-local Olo files
```

There were no tests, benchmark, fixtures, or gates.

## Exploration result

`olo-explorer` discovered and recorded:

```text
Dimension 1: slug correctness, maximize exact-match pass rate
Dimension 2: throughput, minimize per-slug latency
Selected: slug correctness
```

Inside `olo/exp_0000`, it created:

```text
benchmark/run_benchmark.py
benchmark/cases.json
benchmark/heldout.json
benchmark/gate_invariants.py
benchmark/gate_heldout.py
benchmark/.gitignore
```

The public benchmark contained 20 cases. The disjoint held-out gate contained
12 additional cases.

The measured baseline was:

```text
exp_0000
score: 0.25
public cases: 5 / 20
invariant gate: pass
held-out gate: pass at its baseline floor
phase: ready-to-optimize
```

The main checkout still contained no benchmark or gate files.

## Optimization result

`olo-orchestrator` then ran one bounded round with width 1 and budget 1.

`olo-experimenter` created `exp_0001` and replaced the trivial implementation
with a general slug algorithm that:

- lowercases input;
- removes punctuation and symbols;
- preserves letters and digits;
- collapses whitespace to one hyphen;
- removes leading and trailing hyphens.

Final result:

```text
exp_0001
score: 1.0
public cases: 20 / 20
held-out cases: 12 / 12
invariant gate: pass
held-out gate: pass
pre-verification: pass
post-verification: pass
changed files: slugify.py only
merged into main: no
```

This proves the complete handoff:

```text
no benchmark
  -> /olo-explore constructs and commits measurement in exp_0000
  -> /olo-optimize reads its traces
  -> candidate improves from 0.25 to 1.0 in exp_0001
```

# Important limitations

Olo remains a local prototype.

It currently provides:

- project-level Copilot skills;
- project-level custom agents;
- project-level hooks;
- local Git worktrees;
- local benchmark and gate execution;
- local dashboard and state.

It does not yet provide:

- marketplace plugin installation;
- remote sandbox providers;
- remote GPU or VM orchestration;
- pooled remote workers;
- automatic branch merging;
- a complete security sandbox.

The benchmark and gates determine the quality of optimization. A weak
measurement system can reward the wrong behavior, which is why `/olo-explore`
is now a mandatory first-class phase.

# One-sentence summary

`/olo-explore` decides what improvement means and commits a trustworthy
measurement foundation in `exp_0000`; `/olo-optimize` then lets Copilot try
isolated code changes and keeps only candidates that measurably improve without
breaking protected behavior.
