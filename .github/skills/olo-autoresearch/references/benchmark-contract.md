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

## Per-item trace

Write one file per item under `OLO_TRACES_DIR`:

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

## Gates

A gate is any command that exits zero only when the protected behavior passes.
Good gates include:

- existing unit or integration tests;
- held-out score floors;
- schema and artifact validation;
- checks that detect hard-coded eval answers.

A command that merely prints a score and always exits zero is not a gate.
