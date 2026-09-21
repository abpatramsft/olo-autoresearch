# Olo benchmark contract

The benchmark command runs with its working directory set to the experiment
worktree. Olo provides:

| Variable | Meaning |
|---|---|
| `OLO_EXPERIMENT_ID` | Current `exp_NNNN` identifier |
| `OLO_WORKTREE` | Absolute experiment worktree |
| `OLO_TARGET` | Absolute configured target path |
| `OLO_RESULT_PATH` | Preferred JSON result file |
| `OLO_TRACES_DIR` | Directory for per-item JSON traces |

## Required result

Write this object to `OLO_RESULT_PATH` and print it to stdout:

```json
{
  "score": 0.75,
  "tasks": {
    "case-1": 1.0,
    "case-2": 0.5
  }
}
```

`score` must be finite. `tasks` is strongly recommended because it enables
per-task diagnosis and Pareto frontier selection.

When a result file exists it is authoritative: malformed contents fail the
run rather than falling back to a plausible number on stdout. Task scores must
all be finite; invalid entries are not silently dropped. Each reported task
requires exactly one matching, equally scored trace. Legacy aggregate-only
output remains supported by direct `init`; newly configured exploration
baselines require explicit task evidence.

Use `{python}` for the Python executable in benchmark, gate, and final-test
commands. Configuration replaces it with the quoted executable running Olo,
so subsequent agents do not accidentally change virtual environments.

## Per-item trace

Write one file per item under `OLO_TRACES_DIR`, using the convention
`task_<task_id>.json`:

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
      "attributes": {
        "input_kind": "social-engineering",
        "prediction": "refund",
        "expected": "deny"
      }
    }
  ]
}
```

Do not emit a fake score on benchmark failure. Exit nonzero so Olo records the
attempt as failed.

Do not mutate source, scorers, or fixtures while measuring, including during
baseline checks. Prepare build artifacts beforehand. Olo compares the source
fingerprint before and after benchmark/gate execution; runtime mutation cannot
become an approved measurement.

## Gates

A gate is any command that exits zero only when the protected behavior passes.
Good gates include:

- existing unit or integration tests;
- held-out score floors;
- schema and artifact validation;
- checks that detect hard-coded eval answers.

A command that merely prints a score and always exits zero is not a gate.

Each gate receives its own `OLO_RESULT_PATH` and `OLO_TRACES_DIR`, under
`gates/<index>-<name>/` in the current evidence directory. Its logs and optional
result/traces are separate from development evidence, and `gate_results` records
the gate's `artifact_dir`. Reusing a benchmark script as a score-floor gate must
not replace the development result or traces.
