# Olo

### Better code. Evidence first.

**A project-local autoresearch system built specifically for GitHub Copilot CLI.**
Olo helps Copilot define what "better" means, run isolated experiments, and keep
improvements only after measurement, regression gates, and independent review.
You decide what ships.

[Website](https://abpatramsft.github.io/olo-autoresearch/) ·
[Complete guide](docs/guide.md) ·
[Example](examples/tiny-policy-agent/README.md) ·
[Contributing](docs/contributing.md)

```text
Explore -> checked baseline -> isolated experiments -> independent review
                                  ^                         |
                                  +---- saved evidence -----+
```

## Inspired by Evo, built for Copilot

Olo is **loosely inspired by [Evo](https://github.com/evo-hq/evo)** and its
experiment-driven approach to improving code. It is an independently written,
Copilot-specific implementation, not an Evo plugin or a port.

Building on that inspiration, Olo puts particular emphasis on:

- **Copilot-native workflows:** local skills, six specialist agent roles, and
  lifecycle hooks coordinate exploration and optimization.
- **Evidence before promotion:** repeatable, source-bound baseline checks and an
  independent binding review before measured candidates can become parents.
- **Meaningful progress:** numeric gain floors, exact task coverage, critical-task
  regression limits, and retained specialists rather than aggregate scores alone.
- **Traceable research:** recorded probes, donor-tracked recombination,
  evidence-linked lessons, and a readable run summary.
- **A clean stopping boundary:** frozen measurement versions and a once-only
  final test that closes tuning for that version.

These describe Olo's design choices and improvements, not a claim that current
Evo lacks equivalent features. See [NOTICE.md](NOTICE.md) for attribution.

## Try it

**Requirements:** Git and Python 3.10+. Agent-driven experiments also require
[GitHub Copilot CLI](https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli)
with an account that can use it. Windows hooks require PowerShell 7+.
The Olo controller has no third-party Python runtime dependencies.
Copilot usage and your benchmark's own dependencies are separate.

```powershell
git clone https://github.com/abpatramsft/olo-autoresearch.git
cd olo-autoresearch
python olo.py --help
python examples\tiny-policy-agent\benchmark.py --agent examples\tiny-policy-agent\agent.py
python examples\tiny-policy-agent\gate.py --agent examples\tiny-policy-agent\agent.py
```

On macOS/Linux, use your Python 3 launcher and `/` path separators. The included
five-case example starts at **0.6** with its regression gate passing. It is a
small deterministic learning fixture, not a production benchmark.

### Explore first

From a **fresh clone**, start Copilot:

```powershell
copilot --agent=olo-explorer
```

Then send:

```text
Use /olo-explore on examples/tiny-policy-agent/agent.py.
Use the existing benchmark and gate, protect both measurement files, and
prepare a checked, independently approved baseline. Stop when the phase
is ready-to-optimize. Do not merge any experiment branch.
```

### Run one bounded round

Only after `python olo.py status --json` reports `phase=ready-to-optimize`,
start `copilot --agent=olo-orchestrator` and send:

```text
Use /olo-optimize. Read python olo.py scratchpad first.
Run exactly one bounded round with width=1 and budget=1.
Require independent review, stop the mode when done, and do not merge.
```

Inspect the result:

```powershell
python olo.py status --json
python olo.py tree
python olo.py report
python olo.py dashboard --background
```

Open the dashboard URL printed by Olo. Its **Run summary** viewer shows the
generated `.olo\report.md`. The public website is a product introduction;
your dashboard and experiment evidence stay local.

**Already initialized?** Read `python olo.py scratchpad` and
`python olo.py status --json` instead of starting another baseline. A saturated
or finalized run needs a fresh measurement version, not more tuning on the
same answers.

## How it works

| Phase | What happens | What you get |
|---|---|---|
| `/olo-explore` | Understand the repository, choose a measurable goal, build or wrap a benchmark and real gates in `exp_0000`, assess repeatability, obtain independent approval | A checked baseline and frozen measurement |
| `/olo-optimize` | Read failures, choose approved parents, edit isolated worktrees, measure, gate, and independently review candidates | Approved improvements, retained alternatives, and an evidence trail |

Benchmarks measure what should improve. Gates protect what must not break.
Failed gates cannot be offset by a higher score. Experiment branches are
**never automatically merged** into your working branch.

## Use it in your own repository

Olo is a manually copied local kit, not a published package or marketplace
plugin. Copy its entrypoint, `src\olo`, skills, agents, and hooks into a committed
Git repository. Merge the Copilot instructions and hooks with your existing
configuration instead of overwriting them.

The [installation guide](docs/guide.md#install-in-another-repository) lists the
exact paths and the full first-run workflow. Existing product dependencies must
also be available in experiment worktrees; use `{python}` in Python benchmark,
gate, and final-test commands to pin the configuring interpreter.

## Repository map

| Path | Purpose |
|---|---|
| `olo.py`, `src\olo\` | Python control plane and bundled local dashboard |
| `.github\skills\`, `.github\agents\`, `.github\hooks\` | Copilot workflows and guardrails |
| `examples\tiny-policy-agent\` | Small, runnable demonstration |
| `docs\guide.md` | Setup, lifecycle, evidence, configuration, and command reference |
| `docs\contributing.md` | Development checks and website deployment |
| `site\` | Standalone GitHub Pages landing page |
| `tests\` | Controller and browser checks |

`.olo\` and `.olo-history\` contain local research evidence and are not committed.

## Scope and limitations

Olo is a **local prototype** using Git worktrees and local command execution.
It does not provide remote sandboxes, cloud coordination, automatic deployment
of winning code, or operating-system isolation. Hooks are workflow guardrails,
not a security boundary. Reviewer separation is a workflow requirement, not
identity authentication.

A perfect score on a tiny or repeatedly observed dataset is not production
proof. Use representative cases, real regression gates, and fresh final
questions. See the [complete guide](docs/guide.md) for the measurement contract
and research boundaries.
