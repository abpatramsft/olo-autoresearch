# Olo

Olo is a **project-local autoresearch prototype for GitHub Copilot CLI**. It
recreates the core idea behind Evo for a manual Copilot-first trial:

- a Copilot skill drives discovery and optimization;
- repository custom agents divide orchestration, ideation, execution,
  verification, and benchmark review;
- Copilot hooks protect the main checkout, inject run context, validate
  experimenter handoffs, and nudge autonomous continuation;
- a Python control plane creates Git worktrees, records a tree of experiments,
  runs benchmarks and gates, selects a frontier, and serves a live dashboard.

Nothing is installed globally. The implementation uses only the Python standard
library and lives entirely in this folder.

## Architecture

```text
GitHub Copilot CLI
  |
  +-- /olo-autoresearch skill
  |     +-- discover + baseline
  |     +-- size round
  |     +-- dispatch custom agents
  |     +-- reconcile + stop
  |
  +-- olo-orchestrator
  +-- olo-ideator
  +-- olo-experimenter
  +-- olo-verifier
  +-- olo-benchmark-reviewer
  |
  +-- .github/hooks/olo.json
          |
          v
      python olo.py
          +-- .olo/graph.json
          +-- .olo/worktrees/exp_NNNN
          +-- benchmark + gates + traces
          +-- frontier + annotations + proposals
          +-- local dashboard
```

The main checkout stays clean. A candidate is edited and measured in its own
`.olo/worktrees/exp_NNNN` checkout. A candidate is kept only when its score
improves in the configured direction and every gate passes.

## Prerequisites

- Git
- Python 3.10 or newer
- GitHub Copilot CLI
- PowerShell 7 or newer on Windows for repository hooks

No `pip install`, plugin install, or global configuration is required.

## Run the included manual trial

The included fixture starts at score `0.6` and has two discoverable policy
failures.

1. Make `olo` a Git repository with one clean commit if it is not already:

   ```powershell
   git init
   git config user.name "Your Name"
   git config user.email "you@example.com"
   git add .
   git commit -m "Initial Olo prototype"
   ```

2. Initialize the local Olo workspace:

   ```powershell
   python olo.py init --name "tiny-policy" --target examples/tiny-policy-agent/agent.py --editable examples/tiny-policy-agent/agent.py --protect examples/tiny-policy-agent/benchmark.py --protect examples/tiny-policy-agent/gate.py --benchmark "python examples/tiny-policy-agent/benchmark.py --agent {target}" --gate "policy::python examples/tiny-policy-agent/gate.py --agent {target}" --metric max --score-ceiling 1.0
   ```

   Initialization starts the dashboard and prints its URL. The default is
   `http://127.0.0.1:8765`; Olo increments the port if it is occupied.

3. Record the unchanged baseline:

   ```powershell
   python olo.py baseline
   ```

4. Start a new Copilot CLI session from this folder so it loads the repository
   skill, agents, and hooks:

   ```powershell
   copilot
   ```

5. If the session was already open while the files were created, run:

   ```text
   /skills reload
   ```

6. Select the orchestrator with `/agent olo-orchestrator`, or prompt directly:

   ```text
   Use the /olo-autoresearch skill to run exactly one bounded optimization
   round on the initialized tiny-policy workspace. Use width=1 and budget=1.
   Do not modify the benchmark or gate. Report the best valid experiment.
   ```

7. Inspect the result:

   ```powershell
   python olo.py status
   python olo.py tree
   python olo.py report
   ```

## Run the automated smoke test

The smoke suite creates a disposable Git repository, measures the `0.6`
baseline, applies a known candidate in an isolated worktree, verifies the score
reaches `1.0`, checks the gate, and fetches the dashboard API.

```powershell
python -m unittest discover -s tests -v
```

To exercise the rendered dashboard in headless Chromium while it is running:

```powershell
python tests/dashboard_browser_check.py --url http://127.0.0.1:8765 --screenshot .olo/dashboard-smoke.png
```

## Verified Copilot run

This prototype was exercised locally with GitHub Copilot CLI `1.0.83-3` on the
included fixture:

- Copilot discovered the `olo-autoresearch` project skill and all five `olo-*`
  custom agent profiles.
- `olo-orchestrator` ran one bounded round with width `1` and budget `1`.
- Candidate work was delegated to `olo-experimenter`; `olo-verifier` ran pre
  and post audits.
- The baseline `exp_0000` scored `0.6`.
- The kept branch `olo/exp_0001` scored `1.0` with all five task traces passing.
- The policy gate passed.
- The branch diff contains only
  `examples/tiny-policy-agent/agent.py`; the benchmark and gate are unchanged.
- The dashboard API and headless browser interaction both reflected the winning
  experiment.
- The winning branch was intentionally left unmerged.

## Use Olo in another codebase manually

For this prototype, copy these paths into the root of a clean Git repository:

```text
olo.py
src/olo/
.github/skills/olo-autoresearch/
.github/agents/olo-*.agent.md
.github/hooks/olo.json
.github/copilot-instructions.md
```

Then commit those project-local files, initialize Olo with the target benchmark
and gates, record a baseline, and start Copilot CLI from that repository.

The hook configuration is loaded only when Copilot CLI starts. Restart the CLI
after changing `.github/hooks/olo.json`. Skills can be refreshed with
`/skills reload`.

## Control-plane commands

| Command | Purpose |
|---|---|
| `python olo.py init ...` | Configure target, benchmark, gates, protected paths, metric, and dashboard |
| `python olo.py baseline` | Measure and commit the unchanged baseline node |
| `python olo.py new --parent ID --hypothesis TEXT` | Allocate an isolated experiment worktree |
| `python olo.py verify ID --phase pre\|post` | Run structural validity checks |
| `python olo.py run ID` | Execute benchmark and gates; keep only an improvement |
| `python olo.py discard ID --reason TEXT` | Reject and clean an experiment worktree |
| `python olo.py scratchpad` | Show bounded shared state for agents |
| `python olo.py frontier` | Rank branchable committed leaves |
| `python olo.py traces ID [TASK]` | Inspect per-task evidence |
| `python olo.py proposal add ...` | Add an ideator proposal |
| `python olo.py mode start [--bounded]` | Arm bounded or autonomous optimization |
| `python olo.py round start/close` | Track improvement and stall count per round |
| `python olo.py dashboard --background` | Start or reuse the dashboard |
| `python olo.py dashboard --stop` | Stop the recorded dashboard process |
| `python olo.py report --output FILE` | Generate a Markdown run report |
| `python olo.py doctor` | Check local integration files and workspace state |

Use `python olo.py COMMAND --help` for arguments.

## State and safety

Olo writes all runtime state to `.olo/`, which is added to the repository's
local `.git/info/exclude`.

Important safeguards:

- the pre-tool hook blocks direct edits to the configured main target while
  optimize mode is active;
- structural pre-verification blocks protected and out-of-scope changes;
- benchmark failures are never converted into a successful score;
- strict improvement and passing gates are required for a commit;
- the experimenter final response must include a structured experiment record;
- autonomous stop-hook continuations are capped to prevent runaway turns.

Hooks are workflow guardrails, not a security sandbox. Review any skill or hook
before granting broad shell permissions.

## Current prototype boundary

This is intentionally not a packaged root plugin yet. It supports local Git
worktrees and project-level Copilot configuration. It does not yet include
remote sandbox providers, pooled workspaces, cloud coordination, or automatic
plugin marketplace installation.

See [NOTICE.md](NOTICE.md) for design attribution.
