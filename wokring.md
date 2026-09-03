# How Olo Works

Olo is a project-local autoresearch system for GitHub Copilot CLI.

Fundamentally, **Olo separates creativity from truth**:

- GitHub Copilot provides ideas and code changes.
- Olo provides isolation, measurement, safety checks, memory, and
  bookkeeping.

Copilot may believe a change is better. Olo accepts the change only when a
benchmark proves that the score improved and every safety gate still passes.

```text
You
 |
 v
Olo skill -> Orchestrator -> Experimenter
                              |
                              v
                        Isolated worktree
                              |
                    edit -> verify -> measure
                              |
                 benchmark score + safety gates
                              |
                     keep or reject branch
                              |
                    state files + dashboard
```

## A simple analogy

Think of Olo as a small research laboratory:

| Olo part | Laboratory equivalent |
|---|---|
| Skill | The laboratory manual |
| Orchestrator agent | The lab manager |
| Ideator agent | The scientist suggesting experiments |
| Experimenter agent | The engineer performing an experiment |
| Verifier agent | The safety inspector |
| Benchmark reviewer | The analyst explaining results |
| Git worktree | An isolated laboratory bench |
| Benchmark | The measuring instrument |
| Gate | The safety test |
| `.olo\` | The laboratory notebook |
| Dashboard | The observation window |

## Complete process

## 1. Initialize the research project

You first tell Olo what should be improved and how success should be measured.

Example:

```powershell
python olo.py init `
  --name "tiny-policy" `
  --target examples/tiny-policy-agent/agent.py `
  --editable examples/tiny-policy-agent/agent.py `
  --protect examples/tiny-policy-agent/benchmark.py `
  --protect examples/tiny-policy-agent/gate.py `
  --benchmark "python examples/tiny-policy-agent/benchmark.py --agent {target}" `
  --gate "policy::python examples/tiny-policy-agent/gate.py --agent {target}" `
  --metric max `
  --score-ceiling 1.0
```

### Inputs

| Input | Meaning |
|---|---|
| `--name` | Human-readable project name |
| `--target` | Main file Olo is trying to improve |
| `--editable` | Files or folders experiments may modify |
| `--protect` | Files experiments must never modify |
| `--benchmark` | Command that produces a numerical score |
| `--gate` | Command that protects required behavior |
| `--metric max` | Higher scores are better |
| `--metric min` | Lower scores are better |
| `--score-ceiling 1.0` | Stop when the score reaches `1.0` |
| `--timeout` | Maximum time allowed for one benchmark run |
| `--stall-limit` | Number of non-improving rounds allowed |

### What happens

Olo:

1. Confirms that the folder is a Git repository.
2. Confirms that the repository has a commit.
3. Normally requires the main working tree to be clean.
4. Checks that the configured target exists.
5. Creates the `.olo\` runtime directory.
6. Writes the static configuration.
7. Creates an empty experiment graph.
8. Records mode and round state.
9. Adds `.olo\` to the repository's local `.git\info\exclude`.
10. Starts the local dashboard unless `--no-dashboard` was supplied.

### Output

The main generated files are:

```text
.olo/
  config.json
  graph.json
  meta.json
  events.jsonl
  worktrees/
  experiments/
```

At this point Olo has not optimized anything. It only knows the experiment
rules.

## 2. Measure the unchanged baseline

Run:

```powershell
python olo.py baseline
```

The baseline answers:

> How well does the unchanged code work before Copilot modifies anything?

### Input

The baseline uses:

- the current committed source code;
- the configured target;
- the benchmark command;
- the gate commands;
- the metric direction;
- protected and editable paths.

### What happens

Olo:

1. Creates experiment `exp_0000`.
2. Creates branch `olo/exp_0000`.
3. Creates an isolated Git worktree under `.olo\worktrees\exp_0000`.
4. Runs the benchmark against the unchanged target.
5. Runs all configured gates.
6. collects per-task traces.
7. records stdout, stderr, duration, result JSON, and gate results.
8. stores `exp_0000` as the first committed experiment.

### Output from the included example

```json
{
  "experiment_id": "exp_0000",
  "score": 0.6,
  "status": "committed",
  "gates_passed": true,
  "trace_count": 5
}
```

The original router passed three of five tasks:

```text
status                  pass
simple refund           pass
cancel known order      pass
cancel missing order    fail
social engineering      fail
```

These failures give Copilot specific evidence to study.

## 3. Ask Copilot to optimize

Start GitHub Copilot CLI from the repository:

```powershell
copilot
```

Then use a prompt such as:

```text
Use the /olo-autoresearch skill to run one bounded optimization
round. Use width=1 and budget=1.
```

The skill is not executable Python code. It is a detailed laboratory manual
that GitHub Copilot loads into its context.

The skill tells Copilot:

- which Olo commands to run;
- which custom agents to use;
- how to form experiment briefs;
- how to choose experiment parents;
- what files are protected;
- how results must be verified;
- when a candidate may be accepted;
- when the research loop must stop.

The `olo-orchestrator` custom agent becomes the main manager.

## 4. Open an optimization mode

The orchestrator starts either bounded or autonomous mode.

Bounded mode:

```powershell
python olo.py mode start --bounded
```

Autonomous mode:

```powershell
python olo.py mode start
```

### Bounded mode

Bounded mode means:

> Run the requested number of rounds or ideas, report the result, and stop.

### Autonomous mode

Autonomous mode means:

> Continue running rounds until the score ceiling, stall limit, user stop, or
> safety failure is reached.

### Output

Mode state is stored in `.olo\meta.json`:

```json
{
  "active": true,
  "autonomous": false,
  "stall_count": 0,
  "stall_limit": 3,
  "status": "running"
}
```

## 5. Start a research round

The orchestrator runs:

```powershell
python olo.py round start --width 1 --budget 1
```

### Inputs

| Input | Meaning |
|---|---|
| `width` | Number of experiments allowed to run concurrently |
| `budget` | Number of candidate iterations each experimenter may attempt |

`width=1` means one experiment runs at a time.

`budget=1` means the experimenter gets one candidate attempt.

### Resource rule

Git worktrees isolate files, but they do not isolate:

- GPUs;
- CPUs;
- network ports;
- databases;
- shared caches;
- API quotas;
- external services.

Therefore, experiment width is selected from the actual binding resource, not
just the number of available worktrees.

### Output

Olo records:

```json
{
  "number": 1,
  "width": 1,
  "budget": 1,
  "best_before": 0.6
}
```

This lets Olo later determine whether the complete round improved the search.

## 6. Read shared research memory

The orchestrator reads:

```powershell
python olo.py scratchpad
python olo.py frontier
```

The scratchpad contains:

- current best score;
- experiment tree;
- active experiments;
- evaluated and discarded ideas;
- hypotheses that should not be repeated;
- recent verifier annotations;
- ideator proposals;
- current mode and stall state.

This is how fresh Copilot agents learn what earlier agents already discovered.

Without shared state, each new agent could repeat the same failed idea.

## 7. Generate ideas

The `olo-ideator` agent can operate in three modes.

| Ideator mode | Purpose |
|---|---|
| `failure-analysis` | Groups related failed experiments and finds common causes |
| `frontier-extrapolation` | Extends the most successful direction |
| `literature` | Searches for external techniques not already attempted |

### Ideator input

The ideator reads:

- Olo scratchpad;
- experiment outcomes;
- recorded diffs;
- per-task traces;
- annotations;
- current frontier;
- previous hypotheses.

The literature mode can additionally use web research.

### Ideator output

The ideator records proposals using:

```powershell
python olo.py proposal add `
  --source frontier-extrapolation `
  --title "Improve policy ordering" `
  --hypothesis "Check policy override language before refund intent" `
  --rationale "The social-engineering trace is intercepted by refund handling" `
  --parent exp_0000 `
  --confidence high
```

The proposal is written to:

```text
.olo\proposals.jsonl
```

The ideator does not edit source code or run experiments.

## 8. Select a parent from the frontier

Every experiment starts from a known parent.

Initially:

```text
exp_0000, score 0.6
```

Later there may be several valid branches.

The frontier contains committed experiments that do not yet have a newer
committed child.

Olo supports:

| Strategy | Behavior |
|---|---|
| `argmax` | Always select the best aggregate score |
| `top-k` | Select the best K aggregate scores |
| `epsilon-greedy` | Usually select the best, sometimes explore |
| `softmax` | Randomly weight selection toward stronger scores |
| `pareto-per-task` | Preserve branches that are best on individual tasks |

The default is `pareto-per-task`.

### Input

Olo reads:

- committed frontier nodes;
- aggregate scores;
- per-task scores;
- metric direction;
- configured frontier strategy.

### Output

```json
{
  "picks": [
    {
      "id": "exp_0000",
      "score": 0.6,
      "rank": 1
    }
  ]
}
```

Only committed experiments are valid parents.

Failed, evaluated, and discarded experiments remain useful evidence, but they
are not used as foundations for new branches.

## 9. Write an experiment brief

The orchestrator gives an experimenter a focused brief.

Example:

```text
Objective:
Fix the two remaining policy-routing failures.

Evidence:
cancel-missing-order currently returns cancel instead of clarify.
social-engineering currently returns refund instead of deny.

Parent:
exp_0000

Boundaries:
Only edit agent.py.
Do not edit benchmark.py or gate.py.

Pointer traces:
exp_0000 cancel-missing-order
exp_0000 social-engineering

Iteration budget:
1
```

### Input

The brief is created from:

- failed task traces;
- selected frontier parent;
- protected paths;
- editable paths;
- earlier annotations;
- ideator proposals;
- available iteration budget.

### Output

The brief becomes the prompt for `olo-experimenter`.

The orchestrator does not prescribe an exact code patch. The experimenter reads
the evidence and chooses the concrete edit.

## 10. Create an isolated experiment

The experimenter runs:

```powershell
python olo.py new `
  --parent exp_0000 `
  --hypothesis "Add policy-bypass denial and missing-order clarification before generic refund and cancellation routing."
```

### Input

- committed parent experiment;
- specific hypothesis.

The hypothesis should say:

- what file or function will change;
- what exact behavior will change;
- which task should improve;
- why the change should help.

### What happens

Olo creates:

```text
Experiment ID: exp_0001
Branch:        olo/exp_0001
Worktree:      .olo/worktrees/exp_0001/
```

### What a worktree means

A Git worktree is a second folder connected to the same Git repository but
checked out on another branch.

```text
Main checkout
olo/
  examples/tiny-policy-agent/agent.py

Experiment checkout
olo/.olo/worktrees/exp_0001/
  examples/tiny-policy-agent/agent.py
```

The experimenter edits the second copy.

The main checkout remains unchanged.

## 11. Edit the candidate

The experimenter reads the parent source and task traces.

A failed task trace looks like:

```json
{
  "task_id": "social-engineering",
  "status": "failed",
  "score": 0.0,
  "summary": "predicted=refund expected=deny",
  "failure_reason": "wrong_policy_decision"
}
```

The experimenter used the traces to change the routing order:

```text
1. Detect normal status requests.
2. Detect attempts to bypass policy and return deny.
3. Detect cancellation with missing order information and return clarify.
4. Handle normal refunds.
5. Handle normal cancellations.
6. Escalate everything else.
```

The ordering matters.

Previously, the word `refund` was handled before the social-engineering
meaning. The router therefore returned `refund` instead of `deny`.

### Input

- source code in the experiment worktree;
- task traces;
- experiment brief;
- hypothesis;
- editable and protected paths.

### Output

A modified candidate file inside:

```text
.olo\worktrees\exp_0001\examples\tiny-policy-agent\agent.py
```

No change is made to the main checkout.

## 12. Run pre-verification

Before spending benchmark time:

```powershell
python olo.py verify exp_0001 --phase pre
```

There are two verification layers.

### Structural verification

The deterministic Python verifier checks:

- does the worktree exist;
- did the experiment modify anything;
- did it modify a protected file;
- did it change a file outside `--editable`;
- is the hypothesis specific enough.

### Semantic verification

The `olo-verifier` agent checks:

- did the experiment hard-code benchmark answers;
- did it copy held-out test data;
- is it tricking the scorer instead of solving the task;
- did it stay inside the experiment brief;
- could concurrent execution corrupt the score;
- is the hypothesis meaningful and testable.

### Input

- Git diff;
- hypothesis;
- source code;
- protected paths;
- editable paths;
- benchmark design;
- current experiment state.

### Output

```json
{
  "phase": "pre",
  "experiment_id": "exp_0001",
  "passed": true,
  "verdict": "pass",
  "findings": []
}
```

Possible verdicts:

| Verdict | Meaning |
|---|---|
| `pass` | No important issue found |
| `warn` | Experiment may run, but a concern was recorded |
| `fail` | Blocking issue; benchmark must not run |

## 13. Run the benchmark

The experimenter runs:

```powershell
python olo.py run exp_0001
```

`runner.py` executes the configured benchmark inside the experiment worktree.

### Environment provided to the benchmark

| Environment variable | Meaning |
|---|---|
| `OLO_EXPERIMENT_ID` | Current experiment ID |
| `OLO_WORKTREE` | Absolute experiment worktree |
| `OLO_TARGET` | Absolute candidate target path |
| `OLO_RESULT_PATH` | File where final benchmark JSON should be written |
| `OLO_TRACES_DIR` | Directory for per-task trace JSON files |

### Benchmark output contract

The benchmark should produce:

```json
{
  "score": 1.0,
  "tasks": {
    "status": 1.0,
    "simple-refund": 1.0,
    "cancel-known-order": 1.0,
    "cancel-missing-order": 1.0,
    "social-engineering": 1.0
  }
}
```

The score must be a finite number.

If the benchmark crashes or exits nonzero, Olo records a failed experiment. It
does not convert an execution error into a successful score of zero.

### Per-task traces

The benchmark writes one trace for every evaluated item:

```text
.olo/experiments/exp_0001/attempts/001/traces/
  task_status.json
  task_simple-refund.json
  task_cancel-known-order.json
  task_cancel-missing-order.json
  task_social-engineering.json
```

These traces let Copilot understand why a score changed.

## 14. Run the gates

A benchmark asks:

> Did the candidate improve the target measurement?

A gate asks:

> Did the candidate break something we refuse to break?

Our example gate protects:

```text
Status request             must return status
Normal refund request      must return refund
Known-order cancellation   must return cancel
```

### Input

The gate receives the same candidate target.

### Output

```json
{
  "name": "policy",
  "passed": true,
  "returncode": 0
}
```

A gate passes when its command exits with code zero.

A candidate is not accepted if any gate fails, even when the benchmark score
improves.

## 15. Keep or reject the candidate

Olo uses a deterministic decision:

```text
Did the benchmark run successfully?
        |
        no -> failed
        |
        yes
        v
Did every gate pass?
        |
        no -> evaluated, not kept
        |
        yes
        v
Is the score strictly better than the parent?
        |
        no -> evaluated, not kept
        |
        yes -> commit the experiment branch
```

### Experiment statuses

| Status | Meaning |
|---|---|
| `pending` | Worktree exists but has not run |
| `active` | Benchmark is currently running |
| `committed` | Score improved and every gate passed |
| `evaluated` | Experiment ran, but it was not a valid improvement |
| `failed` | Verification, benchmark, or infrastructure failed |
| `discarded` | Experiment was deliberately rejected and cleaned up |

### Our successful decision

```text
Parent score:    0.6
Candidate score: 1.0
Gate:            pass
Decision:        committed
```

Olo committed the experiment to:

```text
Branch: olo/exp_0001
Commit: da21a1b...
```

The branch was not merged into `main`.

## 16. Run post-verification

After the benchmark:

```powershell
python olo.py verify exp_0001 --phase post
```

Post-verification checks:

- did every gate actually execute;
- are the expected traces present;
- do trace scores agree with the aggregate result;
- did the benchmark finish suspiciously quickly;
- was a cache used instead of real evaluation;
- did the candidate modify only expected files;
- did the code solve the task rather than exploit output formatting.

### Input

- benchmark result;
- benchmark logs;
- gate logs;
- task traces;
- duration;
- Git diff;
- committed candidate source.

### Output

```json
{
  "phase": "post",
  "experiment_id": "exp_0001",
  "passed": true,
  "verdict": "pass",
  "findings": []
}
```

Verifier results are also stored as annotations.

## 17. Return a structured experiment handoff

The experimenter must return:

```json
{
  "experiment_id": "exp_0001",
  "status": "committed",
  "score": 1.0,
  "parent": "exp_0000",
  "verification": "pass",
  "learnings": [
    "Policy-bypass signals must be checked before normal refund intent.",
    "Cancellation without identifiable order information requires clarification."
  ]
}
```

The `subagentStop` hook checks this response.

If the experimenter only says:

```text
I fixed the problem and everything looks good.
```

the hook asks the experimenter to return the required JSON object.

This prevents vague and uncheckable handoffs.

## 18. Close the round

The orchestrator runs:

```powershell
python olo.py round close
```

Olo compares the best score before and after:

```json
{
  "number": 1,
  "best_before": 0.6,
  "best_after": 1.0,
  "improved": true,
  "stall_count": 0,
  "reached_ceiling": true
}
```

For an improving round:

```text
stall_count = 0
```

For a non-improving round:

```text
stall_count = stall_count + 1
```

When the stall count reaches the configured limit, autonomous research stops.

Our example reached its score ceiling of `1.0`, so no further experiment was
needed.

## 19. Stop bounded mode

For a bounded request:

```powershell
python olo.py mode stop --reason bounded-complete
```

The final mode state becomes:

```json
{
  "active": false,
  "autonomous": false,
  "status": "bounded-complete"
}
```

## Copilot files

| File | Purpose |
|---|---|
| `.github\skills\olo-autoresearch\SKILL.md` | Main research procedure Copilot follows |
| `.github\skills\olo-autoresearch\references\benchmark-contract.md` | Defines benchmark result and trace formats |
| `.github\skills\olo-autoresearch\references\round-sizing.md` | Explains safe parallel experiment width |
| `.github\skills\olo-autoresearch\references\experiment-protocol.md` | Procedure every experimenter follows |
| `.github\agents\olo-orchestrator.agent.md` | Lab manager; cannot edit candidate code |
| `.github\agents\olo-ideator.agent.md` | Generates experiment proposals |
| `.github\agents\olo-experimenter.agent.md` | Owns worktree edits and experiment execution |
| `.github\agents\olo-verifier.agent.md` | Performs semantic pre/post verification |
| `.github\agents\olo-benchmark-reviewer.agent.md` | Audits benchmarks and explains task failures |
| `.github\hooks\olo.json` | Connects Copilot lifecycle events to Olo |
| `.github\copilot-instructions.md` | Always-on repository safety rules |

## Python files

| File | Responsibility |
|---|---|
| `olo.py` | Small launcher that loads the `src\olo` package |
| `src\olo\cli.py` | Defines commands such as `init`, `new`, `run`, and `status` |
| `src\olo\state.py` | Reads and writes graph, mode, rounds, annotations, and proposals |
| `src\olo\gitops.py` | Creates worktrees, captures diffs, commits winners, and removes rejected worktrees |
| `src\olo\runner.py` | Runs benchmarks and gates and makes the keep/reject decision |
| `src\olo\verification.py` | Performs deterministic structural verification |
| `src\olo\frontier.py` | Ranks successful branches for future experiments |
| `src\olo\hooks.py` | Processes Copilot hook input and returns decisions or context |
| `src\olo\dashboard.py` | Serves dashboard files and JSON APIs |
| `src\olo\utils.py` | Provides file locks, atomic JSON writes, timestamps, process checks, and shell quoting |
| `src\olo\web\index.html` | Dashboard page structure |
| `src\olo\web\style.css` | Dashboard visual design |
| `src\olo\web\app.js` | Polls state and draws the experiment graph |

## Runtime state files

Everything below is generated under `.olo\` and is not committed.

| Runtime path | Contents |
|---|---|
| `.olo\config.json` | Target, benchmark, gates, metric, protected paths, and limits |
| `.olo\graph.json` | Experiment nodes, parent-child relationships, scores, statuses, and commits |
| `.olo\meta.json` | Next ID, current mode, current round, stalls, and dashboard process |
| `.olo\annotations.jsonl` | Verifier and benchmark-reviewer findings |
| `.olo\proposals.jsonl` | Ideator proposals |
| `.olo\events.jsonl` | Hook, experiment, dashboard, and round events |
| `.olo\worktrees\exp_NNNN\` | Actual isolated candidate checkouts |
| `.olo\experiments\exp_NNNN\attempts\001\` | Complete evidence from one attempt |
| `benchmark-result.json` | Parsed numerical result |
| `benchmark.stdout.log` | Benchmark console output |
| `benchmark.stderr.log` | Benchmark error output |
| `gate-*.stdout.log` | Gate output |
| `gate-*.stderr.log` | Gate errors |
| `diff.patch` | Exact candidate changes |
| `outcome.json` | Final structured attempt decision |
| `traces\task_*.json` | One detailed record per evaluated task |

## Custom agent responsibilities

## `olo-orchestrator`

The orchestrator owns:

- mode and round control;
- reading shared state;
- selecting frontier parents;
- writing experiment briefs;
- dispatching agents;
- reconciling results;
- deciding whether another round is needed;
- producing the final report.

The orchestrator intentionally has no candidate-edit tool.

## `olo-ideator`

The ideator:

- studies failures and successful directions;
- optionally researches external ideas;
- records proposals;
- does not edit source;
- does not run experiments.

## `olo-experimenter`

The experimenter:

- reads the brief and traces;
- creates one experiment;
- edits only its isolated worktree;
- invokes verification;
- runs the benchmark and gates;
- interprets the outcome;
- retries only concrete implementation failures;
- returns structured JSON.

## `olo-verifier`

The verifier:

- remains read-only;
- checks scope and benchmark integrity;
- looks for data leakage and scorer tricks;
- checks whether the result is real;
- records pass, warning, or failure annotations.

## `olo-benchmark-reviewer`

The benchmark reviewer has two modes:

| Mode | Purpose |
|---|---|
| `audit` | Check benchmark and gate quality before baseline |
| `review-experiment` | Classify remaining per-task failures after an experiment |

It can classify failures such as:

- wrong answer;
- wrong format;
- policy ordering;
- prompt misunderstanding;
- truncation;
- refusal;
- evaluator error.

## Hook behavior

Hooks receive JSON from GitHub Copilot CLI and return JSON.

| Hook | When it runs | Olo behavior |
|---|---|---|
| `sessionStart` | Copilot CLI starts | Injects current Olo state and usage guidance |
| `preToolUse` | Before an edit tool | Blocks edits to the configured main target during optimization |
| `postToolUse` | After a shell tool | Reminds Copilot to inspect recorded outcomes |
| `subagentStart` | An Olo custom agent starts | Injects role-specific safety instructions |
| `subagentStop` | An Olo custom agent finishes | Requires a structured experiment handoff |
| `agentStop` | Main agent attempts to stop | Nudges autonomous research to continue while active |
| `errorOccurred` | Copilot reports an error | Records the event |

An edit-protection hook can return:

```json
{
  "permissionDecision": "deny",
  "permissionDecisionReason": "Candidate edits must occur in an Olo worktree."
}
```

The hooks are workflow guardrails. They are not a complete security sandbox.

## Dashboard operation

`dashboard.py` runs a local HTTP server.

The browser requests:

```text
GET /api/state
```

The response contains:

```json
{
  "status": {},
  "config": {},
  "graph": {},
  "frontier": {},
  "annotations": [],
  "proposals": [],
  "events": [],
  "round_history": []
}
```

`app.js` requests this state every two seconds and redraws:

- current best score;
- mode and stall status;
- experiment graph;
- parent-child branch lines;
- frontier candidates;
- experiment ledger;
- proposals;
- verifier annotations.

Clicking an experiment requests:

```text
GET /api/experiment/exp_0001
```

That response contains:

- experiment node;
- latest outcome;
- annotations;
- recorded diff;
- benchmark stdout;
- benchmark stderr.

The dashboard observes Olo state. It does not independently decide or execute
experiments.

## Benchmark versus gate

This distinction is critical.

| Benchmark | Gate |
|---|---|
| Measures what should improve | Protects what must not regress |
| Returns a numerical score | Returns pass or fail |
| Example: policy accuracy | Example: normal refunds still work |
| Example: lower latency | Example: answers remain correct |
| Example: search relevance | Example: authorization tests still pass |

Without a good gate, an optimizer can find dishonest shortcuts.

For example:

```python
def route(request):
    return "deny"
```

This might improve one safety task while breaking all normal functionality.

The gate prevents that candidate from being committed.

## Copilot decisions versus Olo decisions

| Decision | Owner |
|---|---|
| What idea should be tried | Copilot ideator and orchestrator |
| What exact code should change | Copilot experimenter |
| Whether the hypothesis looks suspicious | Copilot verifier |
| Which command measures quality | Olo configuration |
| What score was produced | Benchmark |
| Whether protected behavior survived | Gate |
| Whether the score strictly improved | Olo Python code |
| Whether an experiment branch is committed | Olo Python code |
| Whether the branch is merged into main | The user |

Olo may keep a successful experiment branch, but it does not automatically
merge that branch.

## Included example result

The real Copilot run produced:

```text
Baseline:
  exp_0000
  score 0.6

Candidate:
  exp_0001
  score 1.0

Gate:
  pass

Verification:
  pre pass
  post pass

Changed files:
  examples/tiny-policy-agent/agent.py

Protected files:
  benchmark.py unchanged
  gate.py unchanged

Winning branch:
  olo/exp_0001

Merged:
  no
```

The complete experiment tree is:

```text
root
  exp_0000 [committed] score=0.6000
    exp_0001 [committed] score=1.0000
```

## Important limitations of this prototype

This version intentionally remains a manual project-local prototype.

It currently supports:

- local Git worktrees;
- repository-level Copilot skills;
- repository-level custom agents;
- repository-level hooks;
- local benchmarks and gates;
- local state and dashboard.

It does not yet provide:

- a packaged Copilot plugin;
- automatic marketplace installation;
- remote sandbox providers;
- remote GPU orchestration;
- pooled remote workspaces;
- automatic merging;
- a complete security sandbox.

The quality of optimization also depends on the quality of the benchmark and
gates. A weak measurement system can reward the wrong behavior.

## One-sentence summary

Olo repeatedly gives Copilot an isolated copy of the code, asks it to try one
evidence-based idea, measures the result with a numerical benchmark, rejects
anything unsafe or worse, remembers every result, and continues from the
strongest valid branch.
