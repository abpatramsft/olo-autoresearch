# Olo guide

Olo is a repository-local research control plane for GitHub Copilot CLI.
Copilot proposes changes; benchmarks measure them; gates protect behavior;
independent review decides whether the measured source is valid. You decide
whether to merge it.

This is the maintained operating guide. Start with the [README](../README.md)
for the shortest demo, or [contributing](contributing.md) for development and
website publishing.

## Contents

- [Requirements and first run](#requirements-and-first-run)
- [Install in another repository](#install-in-another-repository)
- [The two-phase workflow](#the-two-phase-workflow)
- [Explore and establish a baseline](#explore-and-establish-a-baseline)
- [Benchmark and gate contract](#benchmark-and-gate-contract)
- [Optimize against frozen measurement](#optimize-against-frozen-measurement)
- [Acceptance and experiment states](#acceptance-and-experiment-states)
- [Frontier, memory, and recombination](#frontier-memory-and-recombination)
- [Budgets and stopping](#budgets-and-stopping)
- [Final testing and measurement versions](#final-testing-and-measurement-versions)
- [Dashboard and evidence](#dashboard-and-evidence)
- [Configuration reference](#configuration-reference)
- [Command reference](#command-reference)
- [Troubleshooting and boundaries](#troubleshooting-and-boundaries)

## Requirements and first run

| Requirement | Used for |
|---|---|
| Git with at least one repository commit | Branches and isolated worktrees |
| Python 3.10+ | Olo's dependency-free controller |
| GitHub Copilot CLI and access to Copilot | Agent-driven exploration and experiments |
| PowerShell 7+ on Windows | Repository lifecycle hooks |
| Your target's dependencies | Running its benchmark and gates |

Olo itself needs no `pip install`, Node.js, global plugin, or model API key.
Copilot has its own authentication, availability, and usage limits; benchmark
commands may need additional tools or credentials. Do not commit credentials.

Examples below use PowerShell and Windows paths. On macOS/Linux, use the
available Python 3 launcher and `/` separators. Run controller commands from
the repository root. The global `--repo` option can select another repository
when placed before a subcommand.

```powershell
git clone https://github.com/abpatramsft/olo-autoresearch.git
cd olo-autoresearch
python olo.py --help
python examples\tiny-policy-agent\benchmark.py --agent examples\tiny-policy-agent\agent.py
python examples\tiny-policy-agent\gate.py --agent examples\tiny-policy-agent\agent.py
```

The example benchmark scores five routing requests, with an initial aggregate
of `0.6`. The three-item gate protects working status, refund, and cancellation
behavior. The two deliberately failing benchmark cases concern missing order
information and social-engineering language.

These direct example commands do not initialize Olo or create an approved
experiment. Use the [README's Copilot prompts](../README.md#explore-first) to
run the complete workflow. There is no guaranteed optimization result.

## Install in another repository

Start with working code and a committed Git repository. Copy this kit from an
Olo checkout, preserving its directory structure:

```text
olo.py
src\olo\
.github\skills\olo-autoresearch\
.github\skills\olo-explore\
.github\skills\olo-optimize\
.github\agents\olo-*.agent.md
.github\hooks\olo.json
.github\copilot-instructions.md
```

Merge instructions and hook entries with any existing configuration; do not
replace your project's own instructions. Keep the dashboard's bundled vendor
license files with `src\olo\web\vendor`. You do not need this project's example,
tests, website, or Pages workflow to use the kit.

Add these entries to the target repository's ignore rules:

```gitignore
.olo/
.olo-history/
__pycache__/
*.pyc
```

Review and commit only the intended project and kit files. Initial baseline
allocation requires a clean checkout. Do not copy someone else's `.olo` state
or modify a previously measured experiment to update its controller.

Start a fresh Copilot session from the target repository:

```powershell
copilot --agent=olo-explorer
```

Give it a concrete goal, for example:

```text
Use /olo-explore to improve parser correctness.
Find representative failures, prepare exp_0000, and build or adapt a
benchmark with per-task traces and a real regression gate in that worktree.
Audit the harness, assess repeatability and useful headroom, and obtain
independent baseline approval. Stop at phase=ready-to-optimize.
```

## The two-phase workflow

```text
/olo-explore
  understand repository -> choose goal -> prepare exp_0000
  -> construct measurement -> audit -> repeat checks -> assess
  -> measure baseline -> independent approval

/olo-optimize
  read evidence -> choose approved parents -> propose hypotheses
  -> allocate worktrees -> edit -> verify -> measure + gates
  -> independent review -> keep, retain, or reject -> record lessons
```

The baseline is the measuring instrument and starting specimen together.
Candidate experiments inherit that instrument but cannot change it.

| Agent | Responsibility |
|---|---|
| `olo-explorer` | Understand the repository and establish measurement |
| `olo-orchestrator` | Coordinate research, budgets, reviews, and stopping |
| `olo-ideator` | Propose diverse, evidence-based hypotheses |
| `olo-experimenter` | Implement one hypothesis in its allocated worktree |
| `olo-verifier` | Audit validity and record independent binding review |
| `olo-benchmark-reviewer` | Audit the harness and interpret task-level results |

The orchestrator does not edit candidate code. An experimenter cannot approve
its own changes. The controller enforces source and measurement consistency,
state transitions, numerical rules, and resource counters; it does not prove
that a reviewer's explanation is correct.

## Explore and establish a baseline

Exploration defines one numerical meaning of "better": correctness, latency,
memory, throughput, retrieval quality, or another repeatable measurement.
Start at product entrypoints and existing tests, not generated worktrees.
Record observable weaknesses, positive controls, hard counterexamples, gaming
risks, and the resource that limits parallelism.

The skill normally drives these commands. This manual example uses the
included fixture; run it only in a fresh clone, not over an existing study.

```powershell
python olo.py explore init --name tiny-policy --goal "Improve policy routing correctness"
python olo.py explore add-dimension --name correctness --description "Route policy requests correctly" --target examples/tiny-policy-agent/agent.py --metric-name "mean task pass rate" --direction max --evidence "Two of five existing cases fail" --complexity none --run-cost small
python olo.py explore select --name correctness
python olo.py baseline --prepare
```

`baseline --prepare` returns the `exp_0000` worktree. Create or adapt benchmark,
gate, scorer, and fixture files **only there**. Product behavior should remain
unchanged except for necessary measurement instrumentation. Existing files in
this demo are inherited automatically.

Configure from the main repository root; configured paths refer to that
baseline worktree:

```powershell
python olo.py explore configure `
  --target examples/tiny-policy-agent/agent.py `
  --editable examples/tiny-policy-agent/agent.py `
  --protect examples/tiny-policy-agent/benchmark.py `
  --protect examples/tiny-policy-agent/gate.py `
  --benchmark "{python} examples/tiny-policy-agent/benchmark.py --agent {target}" `
  --benchmark-origin existing `
  --gate "regression::{python} examples/tiny-policy-agent/gate.py --agent {target}" `
  --metric max --unit "policy request" --determinism deterministic `
  --resource-profile "CPU-light isolated process; start width 1" `
  --meaningful-improvement "At least one more correctly routed request" `
  --min-improvement 0.2 --score-ceiling 1.0 `
  --repo-summary "Deterministic policy routing demonstration" `
  --gaming-risk "Visible requests can be hard-coded"
```

CLI configuration examples use portable, repository-relative Git-style paths;
PowerShell command continuation uses the backtick. For your own project,
replace all scope, commands, metric, and thresholds with evidence-backed values.

Use `{python}`, not an assumed `python` on a future worker's PATH. Olo replaces
it with the quoted configuring interpreter, including its environment.
A repository-local virtual environment is not copied into worktrees.

After an independent benchmark audit:

```powershell
python olo.py run exp_0000 --check
python olo.py run exp_0000 --check
python olo.py explore assess
python olo.py baseline
```

New exploration configurations need at least **two deterministic checks** or
**three noisy/temp-zero checks** on unchanged source and settings. Assessment
requires passing gates, consistent traces, adequate repeatability, and useful
headroom. A saturated demo belongs in a gate rather than the objective.
Source, fixture, command, or threshold changes require fresh checks.

The baseline is now measured, not yet approved. A separate reviewer inspects
its exact snapshot and evidence, then records:

```powershell
python olo.py review exp_0000 --verdict approve --reviewer independent-reviewer --reason "Checked scope, repeatability, task traces, gates, and measurement integrity."
```

Do not run this as the author's self-approval. Confirm
`python olo.py status --json` reports `phase=ready-to-optimize`, with an approved
`exp_0000`. Existing saved configurations and direct `init` compatibility
workflows are not silently migrated to the newer readiness policy.

## Benchmark and gate contract

The benchmark runs with the experiment worktree as its working directory.
Olo supplies:

| Environment variable | Meaning |
|---|---|
| `OLO_EXPERIMENT_ID` | Current experiment ID |
| `OLO_WORKTREE` | Absolute worktree path |
| `OLO_TARGET` | Absolute configured target path |
| `OLO_RESULT_PATH` | Result JSON destination for this invocation |
| `OLO_TRACES_DIR` | Per-task trace destination for this invocation |

Write a finite aggregate and finite per-task scores to `OLO_RESULT_PATH`,
and print the result:

```json
{
  "score": 0.5,
  "tasks": {
    "case-1": 1.0,
    "case-2": 0.0
  }
}
```

Every reported task needs exactly one JSON trace under `OLO_TRACES_DIR`,
with the same task ID and score:

```json
{
  "experiment_id": "exp_0001",
  "task_id": "case-2",
  "status": "failed",
  "score": 0.0,
  "summary": "predicted=refund expected=deny",
  "failure_reason": "wrong_policy_decision",
  "events": [
    {
      "name": "route",
      "attributes": {"prediction": "refund", "expected": "deny"}
    }
  ]
}
```

A present result file is authoritative. Malformed JSON, non-finite scores,
missing or inconsistent task traces, and execution errors must not turn into
favorable fallback scores. Legacy aggregate-only results remain supported
through direct initialization; new exploration requires explicit task evidence.

**A benchmark measures; a gate protects.** A gate exits zero only when its
assertions pass. Printing a score while always returning zero is not a gate.
Constructed benchmarks require at least one real gate. Check scorers against
obviously incorrect constant, empty, or all-answer outputs.

Each gate gets separate result, trace, and log destinations under its evidence
directory. A validation gate must not overwrite development results. Benchmark
and gate execution must not mutate measured source; prepare generated fixtures
and build artifacts before checks.

Keep three concepts separate:

| Measurement | Available during optimization? | Purpose |
|---|---|---|
| Development benchmark | Yes | Diagnose failures and guide candidates |
| Regression/validation gates | Yes, including their pass/fail signals | Protect behavior outside or alongside the objective |
| Final test | Once, after selecting a winner | Closing assessment, not tuning feedback |

Repeated validation is not an untouched final test. See the
[agent-facing contract](../.github/skills/olo-autoresearch/references/benchmark-contract.md)
for the canonical harness interface.

## Optimize against frozen measurement

Start `copilot --agent=olo-orchestrator` and request a bounded first round.
The orchestrator reads `.olo\project.md`, then:

```powershell
python olo.py scratchpad
python olo.py status --json
python olo.py mode start --bounded
python olo.py round start --width 1 --budget 1
python olo.py frontier --limit 1
```

Each experimenter receives an objective, parent, evidence pointers, editable
boundaries, resource constraints, and a concrete budget. The normal sequence is:

1. State a falsifiable hypothesis based on saved task failures.
2. Allocate with `new --parent ID --hypothesis TEXT`.
3. Edit only allowed paths in the **returned experiment worktree**.
4. Obtain pre-verification and run `verify ID --phase pre`.
5. Run `run ID`, which records benchmark and gate evidence.
6. Inspect task deltas with the benchmark reviewer.
7. Obtain independent post-verification and a binding `review`.
8. Record evidence-linked lessons and return a structured handoff.

Benchmarks, gates, scorers, fixtures, and held-out data remain immutable.
Never edit a measured snapshot and reuse its old score. Measure a new candidate
for a repair. Review must also preserve the product's intended contract outside
the narrow benchmark slice.

Noisy performance claims require paired or unchanged-control evidence.
An untouched control getting equally faster suggests machine-load drift,
not proof that the candidate caused the improvement.

## Acceptance and experiment states

For parent score `p` and candidate score `c`, directional gain is `c - p`
for `max`, or `p - c` for `min`. Meaningful gain must be positive and meet:

```text
max(min_improvement, abs(parent_score) * min_relative_improvement)
```

With an absolute floor of `0.01`, a change from `0.90` to `0.905` is not
meaningful progress. Describing a threshold in prose does not configure it.
Numeric thresholds, gates, task coverage, structural verification, source
integrity, and independent approval all matter.

| State | Meaning | Reusable as a parent? |
|---|---|---|
| `pending` / `active` | Allocated or being evaluated | No |
| `pending-review` | Measured snapshot awaiting binding review | No |
| `committed` | Approved baseline or meaningful gain against its parent | Yes, while dependencies remain valid |
| `retained` | Approved valid specialist, tie, or below-threshold alternative | Yes, while dependencies remain valid |
| `evaluated` / `failed` | Did not produce an accepted valid candidate | No |
| `discarded` | Closed with a recorded reason | No |
| `invalid` | Rejected or later-invalidated evidence | No |

A saved Git commit alone does not mean approval. A probe is evidence, not an
approved candidate. `best_experiment` selects an eligible committed result;
retained alternatives need not replace the declared winner.

Expected task IDs must match the approved baseline. Explicit critical tasks
have configurable regression limits. This is not an automatic prohibition on
every non-critical task trade-off: inspect the full task deltas.

## Frontier, memory, and recombination

The frontier ranks **approved, eligible ancestors and retained specialists**,
not just branch leaves. Parent and donor invalidation makes their dependent
lineage ineligible.

| Strategy | Behavior |
|---|---|
| `argmax` | Prefer the best directional aggregate |
| `top-k` | Keep several leading aggregate results available |
| `epsilon-greedy` | Usually prefer the leader, sometimes another eligible parent |
| `softmax` | Score-weighted ordering |
| `pareto-per-task` | Rank task wins, then aggregate score |

`pareto-per-task` is a practical task-specialist heuristic, not a full
multi-objective Pareto solver. A weaker average can hide a useful specialist.

Read relevant memory before rounds and retries:

```powershell
python olo.py scratchpad --parent exp_0000 --query "missing order"
python olo.py traces exp_0000
python olo.py frontier --limit 3
```

`learn` attaches observations or hypotheses to saved evidence. Tags and lineage
help retrieve relevant notes; `--supersedes` corrects older interpretations.
Verification chatter is separated from research lessons. A plausible note is
still not a proven causal explanation.

Proposals have stable IDs and a lifecycle: proposed, claimed, tested, rejected,
or superseded. Deduplication uses normalized hypothesis text and parent, not
general semantic understanding.

Use `probe` for exploratory measurements; do not leave trial scores only in
chat. Use `recombine` for implementation reuse across approved experiments:

```powershell
python olo.py recombine --base exp_0002 --donor exp_0003 --contribution "exp_0003::Reuse its input normalization" --hypothesis "Normalization complements the base routing change"
```

IDs above are illustrative. The controller records donors, commits, and
contributions. It does not merge semantics for you. Optional whole-file
`--take` can overwrite the base's changes; use it deliberately. A combination
must be measured and reviewed against its base and donors like any other
hypothesis. Two useful changes do not necessarily work better together.

## Budgets and stopping

**Width** is breadth across candidate lanes. **Budget** is the planned allowance
per lane. The controller enforces a shared round cap of `width * budget`;
the orchestrator also honors individual assignments.

Start at width 1 when resource behavior is unknown. Exclusive GPUs, shared
databases, ports, mutable fixtures, timing interference, and API quotas can
prevent safe concurrency even when worktrees are isolated.

| Action | Experiment attempt? | Evaluation budget? |
|---|---|---|
| Measured run after preflight | Yes | Yes |
| Recorded probe | No | Yes |
| Benchmark-bearing check | No | Yes |
| Preflight/setup block before execution | No | No |
| Read saved evidence | No | No |

Global limits also count baseline checks and measurement. Do not rerun an
evaluation merely to retrieve output; read its saved artifacts.

Resolve active evaluations, pending reviews, and unfinished allocations, then:

```powershell
python olo.py round close
python olo.py mode stop --reason bounded-complete
```

Autonomous mode uses `mode start` without `--bounded`. The agent still drives
execution; the flag is not a background scheduler. Stop at the declared
boundary, score ceiling, stall limit, user interruption, measurement-integrity
concern, or unsafe resource condition.

A round with meaningful global-best progress resets the stall count.
Retained alternatives alone do not count as global progress. The default stall
limit is three. Closing rounds and stopping mode regenerate the run report.

## Final testing and measurement versions

Configure a distinct `--final-test` command before freezing the baseline.
After selecting an approved, unchanged winner, run `python olo.py finalize`
**once**. It records exposure before execution and closes optimization for that
version, even if execution fails. Do not retry on exposed final answers.

Final execution is separate from the pre-final evaluation counter; reserve its
resource cost. Without a configured final test, explicitly report that no
untouched final assessment was run.

When the benchmark saturates or the measurement must change:

```powershell
python olo.py dashboard --stop
python olo.py evaluation new --version harder-v2 --from exp_0001
```

Use the current approved source ID, fresh measurement, and new final questions.
Old evidence remains under `.olo-history`. Establish and approve a new
baseline. Raw scores from different versions are not comparable gains.
Never silently modify a frozen harness in place.

## Dashboard and evidence

```powershell
python olo.py dashboard --background
python olo.py report
```

Use the printed local URL, not an assumed port. The dashboard shows discovery,
experiment lineage, frontier, ledger, task changes, review decisions, donor
contributions, logs, diffs, and saved evidence. **Run summary** opens the
generated Markdown report and supports downloading it.

The public GitHub Pages site does not host this service or receive run data.
The dashboard renderer and its vendor assets are bundled locally.

```text
.olo\
  config.json            executable measurement and policy
  discovery.json         goal selection and baseline assessment
  project.md             human-readable project context
  measurement.json       frozen settings and protected-file hashes
  graph.json             experiments, parents, donors, and reviews
  meta.json              mode, rounds, counters, and process state
  annotations.jsonl      evidence-linked notes and review records
  proposals.jsonl        hypothesis lifecycle
  events.jsonl           controller events
  report.md              generated run summary
  worktrees\exp_NNNN\    isolated Git checkout
  experiments\exp_NNNN\
    preflight\           blocked setup evidence
    checks\              wiring and repeatability checks
    probes\              exploratory trials
    attempts\            measured results, logs, traces, and diffs
    discard\             closing evidence
  final-test\            once-only closing assessment
.olo-history\            archived measurement versions
```

Artifacts appear as actions occur. Neither evidence directory belongs in
normal commits. Experiment branches preserve source snapshots; local state
preserves the wider research record. Exporting `report.md` alone does not
bundle its linked evidence.

## Configuration reference

Use `python olo.py explore configure --help` for exact syntax.

| Options | Purpose |
|---|---|
| `--target`, repeated `--editable` | Product scope |
| Repeated `--protect` | Immutable measurement files and data |
| `--benchmark`, `--benchmark-origin` | Command and existing/wrapped/constructed provenance |
| `--metric`, `--unit` | Direction (`max`/`min`) and scored item |
| Repeated `--gate "NAME::COMMAND"` | Named pass/fail protection |
| `--determinism`, `--resource-profile` | Variability and concurrency constraints |
| `--meaningful-improvement`, `--repo-summary`, `--gaming-risk` | Human-readable measurement intent and risks |
| `--min-improvement`, `--min-relative-improvement` | Numeric absolute/relative gain floors |
| `--critical-task`, `--max-task-regression` | Explicit task-level regression limits |
| `--strategy`, `--frontier-k`, `--epsilon`, `--temperature` | Parent ranking |
| `--timeout`, `--max-attempts`, `--max-evaluations` | Per-command timeout, candidate attempts, total evaluations |
| `--stall-limit`, `--score-ceiling` | Stop conditions |
| `--evaluation-version`, `--final-test` | Measurement identity and final assessment |
| `--future-dimension` | Deferred objective |

The measurement digest binds protected content and measurement settings,
including scope, commands, thresholds, final test, and version. Search choices
and counters are separate. Determinism metadata does not seed a model or create
statistical replicates automatically; design the harness accordingly.

## Command reference

Commands here describe roles, not a script to execute blindly. Replace IDs and
placeholders with values from the current run.

| Command after `python olo.py` | Use |
|---|---|
| `status --json`, `tree`, `show ID` | Inspect current state and a saved result |
| `diff ID`, `traces ID [TASK]` | Inspect source and task evidence |
| `scratchpad`, `frontier` | Read research context and approved parents |
| `explore init`, `explore add-dimension`, `explore select` | Establish a goal |
| `baseline --prepare`, `explore configure` | Construct measurement in isolation |
| `run exp_0000 --check`, `explore assess`, `baseline` | Check, assess, and measure baseline |
| `new --parent ID --hypothesis TEXT` | Allocate before candidate edits |
| `verify ID --phase pre`, `run ID`, `verify ID --phase post` | Structural checks and measurement |
| `review ID --verdict approve --reviewer NAME --reason TEXT` | Independent binding approval |
| `probe ID` | Record an exploratory trial |
| `recombine --base ID --donor ID --contribution "ID::idea" --hypothesis TEXT` | Record source-tracked combination |
| `learn ID --text TEXT --tag TOPIC` | Save an evidence-linked lesson |
| `proposal list`, `proposal update ID --status claimed` | Track ideas and assignments |
| `discard ID --reason TEXT --failure-class hypothesis` | Close an unsuccessful direction |
| `invalidate ID --reviewer NAME --reason TEXT` | Revoke invalid evidence and dependent eligibility |
| `mode start --bounded`, `round start --width 1 --budget 1` | Begin bounded research |
| `round close`, `mode stop --reason bounded-complete` | Reconcile and stop |
| `report`, `dashboard --background`, `dashboard --stop` | Review results locally |
| `finalize` | Once-only final exposure and closure |
| `evaluation new --version LABEL --from ID` | Archive and rebaseline |
| `doctor` | Check installation and initialized state |

## Troubleshooting and boundaries

| Situation | What to do |
|---|---|
| `doctor` reports uninitialized state in a fresh clone | Expected before exploration; use `--help` and start `/olo-explore` |
| Copilot does not see Olo agents | Check copied paths and start a fresh session at the repository root |
| Baseline allocation reports a dirty checkout | Review and commit intended changes; do not discard unrelated work |
| Already prepared baseline, unrelated main edits | Reuse the isolated baseline; do not erase main changes |
| Missing dependencies in a worktree | Install target dependencies in the intended environment and configure `{python}` |
| Assessment rejects repeatability or headroom | Repair measurement, rerun source-bound checks, or choose a more representative goal |
| Candidate changed protected or out-of-scope files | Read preflight evidence and repair scope before measuring |
| Measured source changed afterward | Allocate and measure a new candidate; old scores do not approve new bytes |
| Gate failed despite a higher score | Treat it as an unsuccessful candidate, not progress |
| Budget exhausted | Read saved artifacts; close or explicitly plan subsequent work |
| Dashboard port unavailable | Use the URL returned by Olo, which can select a nearby free port |
| Trusted evidence later proves invalid | Invalidate it and inspect parent/donor dependents |
| Final test fails after exposure | Preserve the failure and closed version; use fresh final questions next time |

Olo is a local prototype, not a sandbox, remote execution service, or automatic
release system. Hooks cannot guarantee that an agent never read a local file;
reviewer names do not authenticate independent identities. Worktrees isolate
files, not hardware, databases, or network access.

Repeatability diagnostics are not statistical confidence. A toy benchmark's
perfect score is not production reliability, and recorded command wall time
is not a complete monetary or agent-compute bill.

Olo is loosely inspired by [Evo](https://github.com/evo-hq/evo), independently
implemented for Copilot. See [NOTICE](../NOTICE.md). The original session notes
were consolidated into this guide; historical drafts remain in Git history.
Workspace-only case studies and stale test-count claims are not prerequisites
or reproducible results shipped with this repository.
