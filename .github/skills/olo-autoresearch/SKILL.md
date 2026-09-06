---
name: olo-autoresearch
description: Route Olo work to the correct phase. Use for general Olo requests, fresh-repository setup, benchmark discovery, or optimization when the user does not explicitly name olo-explore or olo-optimize.
---

# Olo Autoresearch Router

Olo has two distinct phases:

```text
/olo-explore
  repository understanding
  -> goal selection
  -> benchmark and gate construction in exp_0000
  -> checked, independently approved baseline

/olo-optimize
  frontier selection
  -> isolated candidate experiments
  -> verification, benchmark, and gates
  -> review meaningful gains, retain specialists, record trade-offs
  -> once-only final test and human-readable summary
```

Determine the current phase:

```text
python olo.py status --json
```

If Olo is not initialized, or the reported phase is `exploring`,
`ready-for-baseline`, or `configured`, load and follow the `olo-explore` skill.

If the reported phase is `ready-to-optimize`, load and follow the
`olo-optimize` skill.

If the phase is `finalized`, read `.olo/report.md` and stop. Further research
requires `evaluation new` with a fresh version and new final questions.

Never bypass exploration by inventing an optimization score ad hoc. Never start
optimization until `exp_0000` is approved with a checked benchmark and gates.

Shared references used by the optimize phase remain in this skill directory:

- `references/benchmark-contract.md`
- `references/round-sizing.md`
- `references/experiment-protocol.md`
