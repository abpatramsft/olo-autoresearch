# Olo

Olo is a project-local autoresearch system for GitHub Copilot CLI. It follows a
two-phase process inspired by Evo:

```text
/olo-explore
  understand the repository
  -> identify and rank goals
  -> prepare exp_0000
  -> construct or instrument benchmarks and gates
  -> audit and check the harness
  -> commit the measured baseline

/olo-optimize
  read baseline failures
  -> generate ideas
  -> create isolated candidate worktrees
  -> verify, benchmark, and gate each candidate
  -> keep improvements
  -> continue until ceiling or stall
```

Nothing is installed globally. The Python control plane, Copilot skills, custom
agents, hooks, dashboard, and runtime state are local to the repository.

## Why two phases?

Optimization is meaningless until "better" has a precise definition.

`/olo-explore` creates that definition. It decides:

- what behavior should improve;
- which numerical score represents improvement;
- whether higher or lower is better;
- what cases the benchmark evaluates;
- what behavior gates must protect;
- how the benchmark could be gamed;
- whether experiments can run concurrently.

`/olo-optimize` is then allowed to change product behavior, but it cannot change
the benchmark, gate, scorer, fixture, or held-out files established by
exploration.

## Architecture

```text
GitHub Copilot CLI
  |
  +-- /olo-autoresearch       phase router
  +-- /olo-explore            discovery + baseline
  +-- /olo-optimize           experiment loop
  |
  +-- olo-explorer            owns phase one
  +-- olo-orchestrator        owns phase two
  +-- olo-ideator             proposes experiments
  +-- olo-experimenter        edits candidate worktrees
  +-- olo-verifier            audits candidate validity
  +-- olo-benchmark-reviewer  audits measurement and task failures
  |
  +-- .github/hooks/olo.json
          |
          v
      python olo.py
          +-- .olo/discovery.json
          +-- .olo/project.md
          +-- .olo/graph.json
          +-- .olo/worktrees/exp_NNNN
          +-- benchmark + gates + traces
          +-- frontier + annotations + proposals
          +-- local dashboard
```

## Prerequisites

- Git
- Python 3.10 or newer
- GitHub Copilot CLI
- PowerShell 7 or newer on Windows for repository hooks

No `pip install`, plugin installation, or global configuration is required.

## Use Olo on a fresh repository

The repository should contain some working code and at least one Git commit. It
does not need an existing benchmark or test suite.

### 1. Copy the local Olo kit

Copy these paths into the repository root:

```text
olo.py
src/olo/
.github/skills/olo-autoresearch/
.github/skills/olo-explore/
.github/skills/olo-optimize/
.github/agents/olo-*.agent.md
.github/hooks/olo.json
.github/copilot-instructions.md
```

Commit the project and Olo files:

```powershell
git add .
git commit -m "Add project-local Olo autoresearch"
```

### 2. Start exploration

Start a fresh Copilot CLI session from the repository root:

```powershell
copilot
```

Select `/agent olo-explorer`, or launch directly:

```powershell
copilot --agent=olo-explorer
```

Prompt:

```text
Use /olo-explore to explore this repository and prepare a complete Olo
baseline. My goal is: <describe what should improve>.

If no benchmark exists, construct one with per-task traces and a real
regression or held-out gate inside exp_0000. Audit and check the harness, run
the baseline, and stop when Olo reports phase=ready-to-optimize.
```

The explorer:

1. initializes discovery state;
2. reads the repository;
3. records and ranks optimization dimensions;
4. selects the strongest measurable goal;
5. runs `python olo.py baseline --prepare`;
6. creates benchmark and gate files only in the returned worktree;
7. configures target, metric, editable scope, and protected paths;
8. invokes `olo-benchmark-reviewer`;
9. runs `python olo.py run exp_0000 --check`;
10. measures the baseline with `python olo.py baseline`;
11. obtains an independent binding approval with `python olo.py review`.

Confirm:

```powershell
python olo.py status --json
python olo.py explore status
python olo.py show exp_0000
```

The status must report:

```json
{
  "phase": "ready-to-optimize",
  "best_experiment": "exp_0000"
}
```

### 3. Start optimization

Select `/agent olo-orchestrator`, or launch:

```powershell
copilot --agent=olo-orchestrator
```

For a safe first trial:

```text
Use /olo-optimize to run exactly one bounded optimization round.
Use width=1 and budget=1. Do not merge the winning branch.
```

Inspect:

```powershell
python olo.py status
python olo.py tree
python olo.py report
```

## What happens to `main`?

The exploration harness is not added to `main`.

```text
main
  product code
  Olo skill/agent/control-plane files

olo/exp_0000
  product code
  benchmark
  gates
  fixtures
  instrumentation
  measured baseline

olo/exp_0001+
  everything inherited from exp_0000
  plus one candidate product change
```

`exp_0000` becomes the measurement foundation for every later experiment.

## Manual exploration commands

The Copilot skill normally drives these commands, but they can be run manually.

Initialize discovery:

```powershell
python olo.py explore init --name "my-project" --goal "Improve parser correctness"
```

Record dimensions:

```powershell
python olo.py explore add-dimension --name correctness --description "Increase valid parse rate" --target src/parser.py --metric-name "mean corpus pass rate" --direction max --evidence "README promises malformed-input support but no benchmark exists" --complexity minor --run-cost small
```

Select one:

```powershell
python olo.py explore select --name correctness
```

Prepare `exp_0000`:

```powershell
python olo.py baseline --prepare
```

Create or adapt the benchmark and gates in the returned worktree, then
configure:

```powershell
python olo.py explore configure --target src/parser.py --editable src/parser.py --protect benchmarks/parser_benchmark.py --protect benchmarks/parser_gate.py --benchmark "python benchmarks/parser_benchmark.py --target {target}" --benchmark-origin constructed --gate "held-out::python benchmarks/parser_gate.py --target {target}" --metric max --unit "parser corpus case" --determinism deterministic --resource-profile "CPU-light isolated process; start width 1" --meaningful-improvement "At least one additional case passing" --repo-summary "Parser library for structured input" --gaming-risk "The target could special-case visible benchmark strings"
```

Run a non-committing wiring check:

```powershell
python olo.py run exp_0000 --check
```

Measure the baseline, then have an independent reviewer approve the saved evidence:

```powershell
python olo.py baseline
python olo.py review exp_0000 --verdict approve --reviewer reviewer-name --reason "Checked scope, gates, task traces, and measurement integrity."
```

## Existing-benchmark shortcut

If the repository already has a suitable benchmark, `/olo-explore` still runs.
It records the goal, wraps or instruments the existing benchmark if necessary,
protects the measurement files, checks the wiring, and commits `exp_0000`.

Benchmark origins:

| Origin | Meaning |
|---|---|
| `existing` | Already emits the Olo score and trace contract |
| `wrapped` | Existing tests or eval are adapted to emit Olo evidence |
| `constructed` | New cases and scoring logic are created |

## Benchmark contract

Olo supplies:

| Variable | Meaning |
|---|---|
| `OLO_EXPERIMENT_ID` | Current experiment ID |
| `OLO_WORKTREE` | Absolute experiment worktree |
| `OLO_TARGET` | Absolute target path |
| `OLO_RESULT_PATH` | Required result-file destination |
| `OLO_TRACES_DIR` | Directory for per-task traces |

The benchmark produces:

```json
{
  "score": 0.75,
  "tasks": {
    "case-1": 1.0,
    "case-2": 0.5
  }
}
```

## Benchmark versus gate

| Benchmark | Gate |
|---|---|
| Measures what should improve | Protects what must not regress |
| Produces a number | Produces pass/fail through exit code |
| Example: task success rate | Example: existing behavior still passes |
| Example: latency | Example: outputs remain correct |

A constructed benchmark must have at least one real gate.

## Control-plane commands

| Command | Purpose |
|---|---|
| `python olo.py explore init` | Start discovery before target and benchmark are known |
| `python olo.py explore add-dimension` | Record a measurable candidate goal |
| `python olo.py explore select` | Choose the active optimization dimension |
| `python olo.py baseline --prepare` | Create the editable `exp_0000` worktree |
| `python olo.py explore configure` | Save target, benchmark, gates, metric, and project notes |
| `python olo.py run exp_0000 --check` | Validate real benchmark/gate wiring without committing or consuming an attempt |
| `python olo.py baseline` | Save the measured baseline as pending review |
| `python olo.py review ID --verdict approve --reviewer NAME --reason TEXT` | Bind approval to the measured source and configuration |
| `python olo.py invalidate ID --reviewer NAME --reason TEXT` | Invalidate a source and its dependent lineage |
| `python olo.py new --parent ID --hypothesis TEXT` | Allocate an optimization worktree |
| `python olo.py verify ID --phase pre\|post` | Run structural validity checks |
| `python olo.py run ID` | Benchmark and gate a candidate |
| `python olo.py probe ID` | Save an exploratory trial without promotion or attempt charge |
| `python olo.py recombine --base ID --donor ID --contribution "ID::idea" --hypothesis TEXT` | Allocate a combination with recorded donors |
| `python olo.py discard ID --reason TEXT` | Reject and clean a candidate |
| `python olo.py scratchpad` | Show shared research state |
| `python olo.py frontier` | Rank approved ancestors and retained task specialists |
| `python olo.py learn ID --text TEXT --tag TOPIC` | Save an evidence-linked hypothesis or observation |
| `python olo.py proposal update ID --status claimed` | Track proposal lifecycle |
| `python olo.py traces ID [TASK]` | Inspect per-task evidence |
| `python olo.py mode start [--bounded]` | Start bounded or autonomous optimization |
| `python olo.py round start/close` | Track round improvement and stalls |
| `python olo.py dashboard --background` | Start or reuse the dashboard |
| `python olo.py report --output FILE` | Generate a Markdown report |
| `python olo.py finalize` | Evaluate an approved winner once on the final set and close tuning |
| `python olo.py evaluation new --version LABEL --from ID` | Archive evidence and start a fresh measurement version |
| `python olo.py doctor` | Check the local setup |

## Evidence and Acceptance

Configure numeric rules before measuring the baseline with `init` or
`explore configure`: `--min-improvement` (default 0.01), optional
`--min-relative-improvement`, repeated `--critical-task`, and
`--max-task-regression` (default zero). A candidate must meet both gain floors
to count as progress; any protected task loss beyond tolerance blocks approval.
Task IDs must match the approved baseline. Gates and independent review remain
required. Valid ties, small gains, and trade-offs can be approved as `retained`
specialists without replacing the declared best.

Use `--score-ceiling` for a known maximum/minimum and `--max-evaluations` for a
run cap. Round `width * budget` bounds actual evaluations, including probes and
checks. Blocked setup checks cost no evaluation attempts. Generated Python
caches are excluded from scope checks and experiment snapshots.

The baseline freezes a named `--evaluation-version` and hashes of measurement
settings/protected files in `.olo/measurement.json`. A new harder benchmark
requires `evaluation new`; stop the dashboard first. Previous evidence remains
under `.olo-history/`. Old and new scores are not directly comparable.

Configure a distinct `--final-test "<command>"` before baseline measurement.
Routine runs never invoke it. `finalize` uses an approved, unchanged snapshot
once, saves final evidence, and closes optimization even if the command fails.
Reserve fresh final questions for the next version. This local workflow does
not enforce data-access isolation or prove that final questions were unread.

Use `recombine` to retain a Git base plus donor IDs, commit hashes, and explicit
contributions. Optional `--take "ID:relative/path"` copies a whole editable
file; it does not merge semantics. Outcomes compare per-task changes against
the base and donors. Record incompatibilities with `learn`; use `--supersedes`
to correct an older lesson. Verification chatter is excluded from research
context, and retrieval can be scoped with `scratchpad --parent ID --query TEXT`.

Every round close and mode stop writes `.olo/report.md`: goal, explored
directions, approvals, rejected ideas, task trade-offs, source contributions,
rounds, measured cost, final-test status, and evidence links. The dashboard's
**Run summary** button displays sanitized Markdown and offers a download.
Experiment details include every saved probe, check, evaluation, and discard.

## Automated tests

```powershell
python -m unittest discover -s tests -v
```

The suite covers:

- direct initialization with an existing benchmark;
- fresh exploration with no benchmark;
- benchmark and gate construction only in `exp_0000`;
- non-committing wiring checks;
- baseline and candidate commits;
- protected path enforcement;
- hooks;
- Pareto frontier selection;
- dashboard API behavior.
- recovery after discarded or repeatedly failed baselines;
- rejection of benchmark files modified at candidate runtime.

Browser check:

```powershell
python tests/dashboard_browser_check.py --url http://127.0.0.1:8765 --screenshot .olo/dashboard-smoke.png
```

## Verified fresh two-phase Copilot run

Olo `0.2.0` was tested on a fresh repository containing only `README.md` and a
flawed `slugify.py`; it had no tests, benchmark, fixtures, or gates.

`olo-explorer`:

- recorded correctness and throughput dimensions;
- selected exact-match slug correctness;
- created a deterministic 20-case benchmark only in `olo/exp_0000`;
- created a separate 12-case held-out set;
- created invariant and held-out gates;
- passed the benchmark-reviewer audit and non-committing wiring check;
- committed a baseline score of `0.25`;
- left the main checkout free of benchmark and gate files.

`olo-orchestrator` then ran one bounded `/olo-optimize` round:

- `olo-experimenter` changed only `slugify.py`;
- public benchmark score improved from `0.25` to `1.0`;
- all 20 public cases passed;
- all 12 held-out cases passed;
- both gates passed;
- pre and post verification passed;
- the winner remained on `olo/exp_0001` and was not merged.

The fresh-run dashboard remained live at `http://127.0.0.1:8766`.

## State and safety

Olo writes runtime state to `.olo\`, which is placed in local Git excludes.

Important safeguards:

- `/olo-optimize` is blocked until the baseline is committed;
- benchmark construction happens in `exp_0000`, not `main`;
- descendant experiments cannot edit protected measurement paths;
- benchmark failures never become successful scores;
- meaningful gain, passing gates, and binding review are required for promotion;
- measured snapshots and measurement settings are fingerprinted;
- invalidated parents and donors exclude dependent results;
- experimenter handoffs must contain structured experiment records;
- autonomous stop-hook continuations are capped.

Hooks are workflow guardrails, not a complete security sandbox.

Dashboard assets are bundled locally. Rebuild them only when changing the
renderer or icons: `npm ci --prefix src/olo/web/vendor` followed by
`npm run sync --prefix src/olo/web/vendor`. Runtime use requires no Node install.

## Prototype boundary

Olo remains a manually copied, project-local prototype. It currently supports
local Git worktrees and local benchmark execution. It does not yet include
plugin marketplace installation, remote sandbox providers, pooled remote
workers, cloud coordination, or automatic merging.

See [wokring.md](wokring.md) for the complete first-principles explanation,
[rough.md](rough.md) for a simpler guide to optimization and Pareto-style
frontier selection, and [NOTICE.md](NOTICE.md) for design attribution.
