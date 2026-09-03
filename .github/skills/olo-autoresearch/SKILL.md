---
name: olo-autoresearch
description: Run project-local autoresearch with GitHub Copilot CLI. Use when asked to discover an optimization target, establish a benchmark and gates, run structured experiments, improve a score, compare variants, continue an Olo search, or report the best experiment.
---

# Olo Autoresearch

Olo is a Copilot-native experiment loop. The Python control plane records state,
creates isolated Git worktrees, executes benchmarks and gates, and serves the
dashboard. Copilot supplies the research judgment through the repository's
`olo-*` custom agents.

Resolve the Python launcher once:

- Windows: `python olo.py`
- Linux/macOS: `python3 olo.py`

The examples below use `python olo.py`; substitute the available launcher.

## Firm invariants

1. The orchestrator never edits a candidate in the main checkout. Candidate
   changes belong to `olo-experimenter` agents inside the worktree returned by
   `python olo.py new`.
2. Never modify benchmark, gate, scorer, fixture, or held-out data paths during
   an experiment. Olo's structural verifier blocks protected-path changes.
3. A higher score is not progress unless every gate passes and verification
   finds no blocking issue.
4. Read shared state before every round and every retry. Do not repeat a
   discarded hypothesis without new evidence.
5. One experiment represents one hypothesis. Replicates for a noisy benchmark
   belong inside that experiment's benchmark command, not in separate
   experiments.
6. Use committed frontier nodes as parents. Evaluated, failed, and discarded
   nodes are evidence, not branch points.

## Copilot agent map

Use the named repository agents, preferably through Copilot's custom-agent
subagent tool and `/fleet` for safe parallel work.

| Agent | Job |
|---|---|
| `olo-orchestrator` | Owns rounds, diversity, frontier selection, and stopping. Never edits candidates. |
| `olo-ideator` | Produces ranked proposals from failures, frontier gradients, or external research. |
| `olo-experimenter` | Allocates a worktree, forms the concrete edit, verifies, runs, and reports one branch. |
| `olo-verifier` | Read-only pre/post audit for leakage, scope escape, skipped work, and invalid results. |
| `olo-benchmark-reviewer` | Audits the harness before baseline and classifies per-task failures after kept experiments. |

If native custom-agent dispatch is unavailable, stop and say that the requested
Copilot-agent round cannot be run. Do not silently edit candidates in the main
agent as a substitute.

## Mode A: Discover and initialize

Use this when `.olo/config.json` does not exist.

1. Inspect the repository's README, entry points, tests, existing evaluation
   scripts, and target behavior.
2. Choose the optimization target. Prefer an existing runnable benchmark that
   directly measures the requested outcome.
3. Define at least one gate for behavior that must not regress. For a
   constructed benchmark, gates are mandatory.
4. Read `references/benchmark-contract.md`. Ensure the benchmark:
   - exits nonzero on execution failure;
   - emits `{"score": <finite number>, "tasks": {...}}`;
   - writes the same result to `OLO_RESULT_PATH` when set;
   - writes one trace per item under `OLO_TRACES_DIR`.
5. Protect the benchmark, gate, fixture, and held-out-data paths with repeated
   `--protect` arguments. Scope candidate edits with `--editable`.
6. Initialize. Example:

   ```text
   python olo.py init --name "policy router" \
     --target examples/tiny-policy-agent/agent.py \
     --editable examples/tiny-policy-agent/agent.py \
     --protect examples/tiny-policy-agent/benchmark.py \
     --protect examples/tiny-policy-agent/gate.py \
     --benchmark "python examples/tiny-policy-agent/benchmark.py --agent {target}" \
     --gate "policy::python examples/tiny-policy-agent/gate.py --agent {target}" \
     --metric max --score-ceiling 1.0
   ```

7. Dispatch `olo-benchmark-reviewer` in audit mode. Address every blocking
   finding before baseline.
8. Run the baseline:

   ```text
   python olo.py baseline
   ```

9. Read `python olo.py show exp_0000` and confirm:
   - status is `committed`;
   - score parses correctly;
   - gates passed;
   - per-task traces exist;
   - post-verification is pass or an understood warning.

Do not optimize against a broken or aggregate-only harness.

## Mode B: Optimize

### 1. Resolve run shape

Read `references/round-sizing.md` before selecting width and budget.

- An explicit user bound such as "one round" or "try two ideas" means bounded
  mode.
- Otherwise Olo defaults to autonomous mode and stops on score ceiling, stall
  limit, or user interruption.

Start the mode:

```text
python olo.py mode start --bounded --stall-limit 3
```

Omit `--bounded` for autonomous mode. State the chosen width and budget with a
one-line resource reason.

### 2. Start a round

```text
python olo.py round start --width N --budget M
python olo.py scratchpad
python olo.py frontier --limit N
```

Read the full scratchpad. The tree, rejected ideas, proposals, and annotations
are shared memory.

### 3. Generate or refresh ideas

Dispatch `olo-ideator` agents when:

- this is the first optimization round;
- the previous round did not improve;
- two or more experiments share a failure mode;
- five kept experiments have elapsed since the last ideation pass.

Use non-overlapping briefs:

- `failure-analysis`: cluster recent evaluated/failed nodes and propose fixes
  or clean alternatives;
- `frontier-extrapolation`: deepen the largest observed positive delta;
- `literature`: research actionable techniques not already represented in the
  graph.

Each ideator records proposals through `python olo.py proposal add`.

### 4. Write diverse experiment briefs

Write exactly one brief per experimenter. Each brief must contain:

```text
Objective:
Evidence:
Parent:
Boundaries:
Pointer traces:
Iteration budget:
Required final JSON:
```

The objective is strategic. The experimenter chooses the concrete edit after
reading the pointed traces and source. Briefs in the same round must differ by
failure cluster, mechanism, or parent; do not send cosmetic variants of one
idea to every lane.

### 5. Dispatch experimenters

Spawn `N` `olo-experimenter` agents, in parallel only when the benchmark and
machine are concurrency-safe. Each experimenter must follow
`references/experiment-protocol.md`.

Required final object:

```json
{
  "experiment_id": "exp_0001",
  "status": "committed",
  "score": 1.0,
  "parent": "exp_0000",
  "verification": "pass",
  "learnings": ["specific durable observation"]
}
```

Treat missing experiment IDs or prose-only handoffs as incomplete. The
`subagentStop` hook asks the agent to repair the response.

### 6. Reconcile outcomes

For each result:

- `committed`: inspect score, gates, post-verification, and task traces. Dispatch
  `olo-benchmark-reviewer` in `review-experiment` mode and record diagnostic
  annotations.
- `evaluated`: read the outcome and diff. Retry the same node only for a
  concrete implementation bug; otherwise discard it with a classified reason.
- `failed`: retry only when the failure is transient or clearly repaired.
- verification `fail`: do not count it as progress. Discard or repair before
  any further run.

Useful commands:

```text
python olo.py show exp_0001
python olo.py diff exp_0001
python olo.py traces exp_0001
python olo.py discard exp_0001 --failure-class hypothesis --reason "..."
python olo.py annotate exp_0001 "durable learning"
```

### 7. Close and continue

```text
python olo.py round close
python olo.py status --json
```

For a bounded request, report and stop after the requested rounds, then run:

```text
python olo.py mode stop --reason bounded-complete
```

For autonomous mode, continue while `mode.active` is true. The `agentStop` hook
nudges the orchestrator to continue and self-limits to avoid runaway turns.

## Stop conditions

Stop when any applies:

- configured score ceiling reached;
- consecutive no-improvement rounds equal the stall limit;
- user asks to stop;
- benchmark or gate integrity is uncertain;
- no safe experiment width is available;
- custom-agent dispatch is unavailable.

## Reporting

At the end:

```text
python olo.py status
python olo.py tree
python olo.py report --output OLO-REPORT.md
```

Report the best valid experiment, score delta from baseline, gates,
verification, meaningful failed directions, and the exact branch/worktree or
commit to inspect. Do not merge the best branch unless the user explicitly asks.
