---
name: olo-optimize
description: Run structured Olo autoresearch rounds after olo-explore has committed a valid baseline. Use when asked to improve the current best, try ideas or variants, compare candidates, continue an Olo search, or run bounded or autonomous optimization.
---

# Olo Optimize

This is phase two. Refuse to start unless:

```text
python olo.py status --json
```

reports `phase=ready-to-optimize` and `exp_0000` is approved and committed.

Use the experiment protocol at
`.github/skills/olo-autoresearch/references/experiment-protocol.md`.

## Invariants

1. The orchestrator never edits candidates.
2. Every candidate is owned by an `olo-experimenter` in its worktree.
3. Benchmark, gate, scorer, fixture, and held-out paths are immutable.
4. Gates, verification, complete task coverage, and independent binding review
  must pass before progress counts. Respect the configured numeric gain floor.
5. Read shared state before every round and retry.
6. Use approved frontier nodes as parents, including historical ancestors and
  retained task specialists. Never use a pending or invalidated source.
7. Respect the binding resource when choosing width.

## Start

Read `.olo/project.md`, then:

```text
python olo.py scratchpad
python olo.py status --json
```

Resolve mode:

- explicit bounded request -> `python olo.py mode start --bounded`;
- otherwise -> `python olo.py mode start`.

Read `.github/skills/olo-autoresearch/references/round-sizing.md` and state the
chosen width and budget with the binding-resource reason.

## Each round

Start:

```text
python olo.py round start --width N --budget M
python olo.py scratchpad
python olo.py frontier --limit N
```

Generate ideator proposals on the first round, after a non-improving round,
after clustered failures, or every five committed experiments.

Write one diverse brief per lane:

```text
Objective:
Evidence:
Parent:
Boundaries:
Pointer traces:
Iteration budget:
Required final JSON:
```

Dispatch `N` `olo-experimenter` agents. Parallelize only when the benchmark and
resource profile say concurrent runs are safe.

Each experimenter:

1. reads the scratchpad, parent, and pointer traces;
2. creates a concrete hypothesis;
3. allocates with `python olo.py new`;
4. edits only the returned worktree;
5. invokes `olo-verifier` pre;
6. runs `python olo.py verify <id> --phase pre`;
7. runs `python olo.py run <id>`;
8. reviews measured task changes with `olo-benchmark-reviewer`;
9. invokes independent `olo-verifier` post, which records a binding review;
10. returns structured JSON.

Required handoff:

```json
{
  "experiment_id": "exp_0001",
  "status": "committed",
  "score": 0.8,
  "parent": "exp_0000",
  "verification": "pass",
  "learnings": ["specific observation"]
}
```

## Reconcile

- `pending-review`: do not branch or count progress; complete independent review.
- `committed`: approved meaningful gain; inspect task regressions as well as aggregate.
- `retained`: valid specialist or nonmeaningful change, not aggregate progress.
- `probed`: saved exploratory evidence only; not a reviewed snapshot.
- `blocked`: repair setup; no evaluation attempt has been consumed.
- `invalid`: do not reuse this source or its dependent lineage.
- `evaluated`: retry only a concrete implementation bug; otherwise discard as
  a hypothesis failure.
- `failed`: retry only a repaired transient or implementation failure.
- verification `fail`: do not count as progress.

Useful commands:

```text
python olo.py show <id>
python olo.py diff <id>
python olo.py traces <id> [task]
python olo.py discard <id> --reason "<reason>" --failure-class hypothesis
python olo.py learn <id> --text "<evidence-backed lesson>" --tag <topic>
```

Close:

```text
python olo.py round close
python olo.py status --json
```

For a bounded run, stop at the requested boundary:

```text
python olo.py mode stop --reason bounded-complete
```

For autonomous mode, continue while `mode.active=true`. Stop on score ceiling,
stall limit, user interruption, measurement-integrity uncertainty, or resource
unsafety.

Probes and checks consume evaluation budget. Use `recombine` for cross-branch
ideas, recording donors and contributions before editing. Check proposal status
before retrying a direction; a changed condition should be stated explicitly.

Every round close or mode stop writes `.olo/report.md`. End with its path and
the dashboard URL. When a final test was configured, finish outstanding reviews
and call `python olo.py finalize` once on the selected approved winner. This
exposes final results and permanently closes optimization in this version.
Do not inspect final questions before selection or tune afterward.

At saturation, preserve evidence and create a harder measurement version with
`python olo.py evaluation new --version <new-label> --from <approved-id>`.
Stop the dashboard first. Rebuild and approve the baseline; never compare raw
scores across measurement versions. Prior state is retained under `.olo-history/`.

Never merge the winning branch unless the user explicitly asks.
