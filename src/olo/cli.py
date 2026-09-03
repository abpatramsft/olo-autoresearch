from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__
from .dashboard import find_free_port, serve_dashboard
from .frontier import rank_frontier
from .gitops import (
    add_local_exclude,
    create_worktree,
    diff_between,
    ensure_clean,
    ensure_repo_ready,
    head_commit,
    parent_commit,
    remove_worktree,
    repo_root,
)
from .hooks import handle_hook
from .runner import run_experiment
from .state import StateStore
from .utils import is_pid_running
from .verification import verify_experiment


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _normalize_relative(root: Path, raw: str) -> str:
    path = Path(raw)
    absolute = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"path must be inside the repository: {raw}") from exc
    return relative.as_posix()


def _parse_gate(raw: str, index: int) -> dict[str, str]:
    if "::" in raw:
        name, command = raw.split("::", 1)
        return {"name": name.strip() or f"gate-{index}", "command": command.strip()}
    return {"name": f"gate-{index}", "command": raw.strip()}


def _entry_command(root: Path) -> list[str]:
    local_script = root / "olo.py"
    if local_script.exists():
        return [sys.executable, str(local_script)]
    invoked = Path(sys.argv[0]).resolve()
    if invoked.name.lower() == "olo.py" and invoked.exists():
        return [sys.executable, str(invoked)]
    return [sys.executable, "-m", "olo"]


def _wait_for_health(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"dashboard did not become healthy at {url}")


def _start_dashboard_background(
    store: StateStore,
    *,
    preferred_port: int,
) -> dict[str, Any]:
    meta = store.meta()
    dashboard = meta.get("dashboard") or {}
    existing_pid = int(dashboard.get("pid") or 0)
    existing_port = int(dashboard.get("port") or 0)
    if existing_pid and existing_port and is_pid_running(existing_pid):
        return {
            "pid": existing_pid,
            "port": existing_port,
            "url": f"http://127.0.0.1:{existing_port}",
            "reused": True,
        }
    port = find_free_port(preferred_port)
    command = [
        *_entry_command(store.root),
        "--repo",
        str(store.root),
        "dashboard",
        "--foreground",
        "--port",
        str(port),
    ]
    log_path = store.state_dir / "dashboard.log"
    log_handle = log_path.open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": store.root,
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": log_handle,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            0x00000008 | 0x00000200 | 0x08000000
        )
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    log_handle.close()
    store.update_dashboard(pid=process.pid, port=port)
    try:
        _wait_for_health(port)
    except Exception:
        store.update_dashboard(pid=None, port=None)
        raise
    store.add_event("dashboard_started", pid=process.pid, port=port)
    return {
        "pid": process.pid,
        "port": port,
        "url": f"http://127.0.0.1:{port}",
        "reused": False,
    }


def _stop_dashboard(store: StateStore) -> dict[str, Any]:
    dashboard = store.meta().get("dashboard") or {}
    pid = int(dashboard.get("pid") or 0)
    port = dashboard.get("port")
    if pid and is_pid_running(pid):
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and is_pid_running(pid):
            time.sleep(0.1)
    store.update_dashboard(pid=None, port=None)
    store.add_event("dashboard_stopped", pid=pid or None, port=port)
    return {"stopped": bool(pid), "pid": pid or None, "port": port}


def _allocate(store: StateStore, parent_id: str, hypothesis: str) -> dict[str, Any]:
    graph = store.graph()
    commit = parent_commit(graph, parent_id)
    node = store.reserve_experiment(parent_id, hypothesis)
    try:
        create_worktree(
            store.root,
            Path(node["worktree"]),
            str(node["branch"]),
            commit,
        )
    except Exception as exc:
        store.update_node(node["id"], status="failed", error=str(exc))
        raise
    store.add_event(
        "experiment_allocated",
        experiment_id=node["id"],
        parent=parent_id,
        hypothesis=hypothesis,
    )
    return node


def cmd_init(args: argparse.Namespace, root: Path) -> int:
    ensure_repo_ready(root)
    if not args.allow_dirty:
        ensure_clean(root)
    target = _normalize_relative(root, args.target)
    if not (root / target).exists():
        raise RuntimeError(f"target does not exist: {target}")
    editable = [_normalize_relative(root, item) for item in (args.editable or [target])]
    protected = [_normalize_relative(root, item) for item in (args.protect or [])]
    gates = [_parse_gate(raw, i + 1) for i, raw in enumerate(args.gate or [])]
    strategy = {
        "kind": args.strategy,
        "k": args.frontier_k,
        "epsilon": args.epsilon,
        "temperature": args.temperature,
    }
    store = StateStore(root)
    add_local_exclude(root)
    store.initialize(
        project_name=args.name or root.name,
        target=target,
        benchmark=args.benchmark,
        metric=args.metric,
        gates=gates,
        root_commit=head_commit(root),
        editable_paths=editable,
        protected_paths=protected,
        timeout_seconds=args.timeout,
        max_attempts=args.max_attempts,
        stall_limit=args.stall_limit,
        frontier_strategy=strategy,
        score_ceiling=args.score_ceiling,
    )
    result: dict[str, Any] = {
        "initialized": True,
        "state_dir": str(store.state_dir),
        "target": target,
    }
    if not args.no_dashboard:
        result["dashboard"] = _start_dashboard_background(
            store, preferred_port=args.port
        )
    _json(result)
    return 0


def cmd_baseline(args: argparse.Namespace, store: StateStore) -> int:
    if int(store.meta().get("next_id", 0)) != 0:
        raise RuntimeError("baseline must be the first Olo experiment")
    node = _allocate(
        store,
        "root",
        args.hypothesis or "Measure the unchanged repository baseline.",
    )
    outcome = run_experiment(store, node["id"], timeout_override=args.timeout)
    _json(outcome)
    return 0 if outcome["status"] == "committed" else 1


def cmd_new(args: argparse.Namespace, store: StateStore) -> int:
    node = _allocate(store, args.parent, args.hypothesis)
    _json(
        {
            "experiment_id": node["id"],
            "parent": node["parent"],
            "branch": node["branch"],
            "worktree": node["worktree"],
            "hypothesis": node["hypothesis"],
        }
    )
    return 0


def cmd_run(args: argparse.Namespace, store: StateStore) -> int:
    outcome = run_experiment(store, args.experiment_id, timeout_override=args.timeout)
    _json(outcome)
    return 0 if outcome["status"] in {"committed", "evaluated"} else 1


def cmd_discard(args: argparse.Namespace, store: StateStore) -> int:
    graph = store.graph()
    node = graph.get("nodes", {}).get(args.experiment_id)
    if node is None:
        raise RuntimeError(f"unknown experiment: {args.experiment_id}")
    if node.get("status") == "committed" and not args.force:
        raise RuntimeError("refusing to discard a committed node without --force")
    remove_worktree(
        store.root,
        Path(node["worktree"]),
        node.get("branch"),
    )
    updated = store.update_node(
        args.experiment_id,
        status="discarded",
        discard_reason=args.reason,
        failure_class=args.failure_class,
        worktree=None,
    )
    store.add_event(
        "experiment_discarded",
        experiment_id=args.experiment_id,
        reason=args.reason,
        failure_class=args.failure_class,
    )
    _json(updated)
    return 0


def cmd_status(args: argparse.Namespace, store: StateStore) -> int:
    status = store.status_summary()
    if args.json:
        _json(status)
    else:
        print(
            f"{status['project_name']}: best={status['best_score']} "
            f"({status['best_experiment']}) metric={status['metric']} "
            f"experiments={status['experiments']} counts={status['counts']}"
        )
        if status.get("dashboard_url"):
            print(f"Dashboard: {status['dashboard_url']}")
    return 0


def cmd_show(args: argparse.Namespace, store: StateStore) -> int:
    graph = store.graph()
    node = graph.get("nodes", {}).get(args.experiment_id)
    if node is None:
        raise RuntimeError(f"unknown experiment: {args.experiment_id}")
    _json(
        {
            "node": node,
            "outcome": store.latest_outcome(args.experiment_id),
            "annotations": [
                item
                for item in store.annotations()
                if item.get("experiment_id") == args.experiment_id
            ],
        }
    )
    return 0


def cmd_tree(store: StateStore) -> int:
    print("\n".join(store.tree_lines()))
    return 0


def cmd_scratchpad(store: StateStore) -> int:
    print(store.scratchpad(), end="")
    return 0


def cmd_frontier(args: argparse.Namespace, store: StateStore) -> int:
    value = rank_frontier(
        store.graph(),
        store.config(),
        limit=args.limit,
        strategy_override=args.strategy,
    )
    _json(value)
    return 0


def cmd_diff(args: argparse.Namespace, store: StateStore) -> int:
    graph = store.graph()
    node = graph.get("nodes", {}).get(args.experiment_id)
    if node is None:
        raise RuntimeError(f"unknown experiment: {args.experiment_id}")
    if args.other:
        other = graph.get("nodes", {}).get(args.other)
        if other is None or not other.get("commit") or not node.get("commit"):
            raise RuntimeError("both experiments need committed revisions")
        print(diff_between(store.root, other["commit"], node["commit"]), end="")
        return 0
    outcome = store.latest_outcome(args.experiment_id)
    if not outcome:
        return 0
    path = store.attempt_dir(args.experiment_id, int(outcome["attempt"])) / "diff.patch"
    if path.exists():
        print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_traces(args: argparse.Namespace, store: StateStore) -> int:
    node = store.graph().get("nodes", {}).get(args.experiment_id)
    if not node or not node.get("attempts"):
        raise RuntimeError(f"no traces for {args.experiment_id}")
    traces = store.traces_dir(args.experiment_id, int(node["attempts"]))
    if args.task_id is None:
        _json({"files": [str(path) for path in sorted(traces.glob("*.json"))]})
        return 0
    exact = traces / f"task_{args.task_id}.json"
    matches = [exact] if exact.exists() else list(traces.glob(f"*{args.task_id}*.json"))
    if not matches:
        raise RuntimeError(f"trace task {args.task_id} not found")
    print(matches[0].read_text(encoding="utf-8"))
    return 0


def cmd_verify(args: argparse.Namespace, store: StateStore) -> int:
    report = verify_experiment(
        store,
        args.experiment_id,
        phase=args.phase,
        persist=True,
    )
    _json(report)
    return 0 if report["passed"] else 1


def cmd_annotate(args: argparse.Namespace, store: StateStore) -> int:
    entry = store.add_annotation(
        args.experiment_id,
        text=args.text,
        task_id=args.task,
        annotation_type=args.type,
    )
    _json(entry)
    return 0


def cmd_proposal(args: argparse.Namespace, store: StateStore) -> int:
    if args.proposal_command == "list":
        _json({"proposals": store.proposals()})
        return 0
    entry = store.add_proposal(
        source=args.source,
        title=args.title,
        hypothesis=args.hypothesis,
        rationale=args.rationale,
        parent=args.parent,
        confidence=args.confidence,
    )
    _json(entry)
    return 0


def cmd_strategy(args: argparse.Namespace, store: StateStore) -> int:
    config = store.config()
    config["frontier_strategy"] = {
        "kind": args.kind,
        "k": args.k,
        "epsilon": args.epsilon,
        "temperature": args.temperature,
    }
    store.save_config(config)
    _json(config["frontier_strategy"])
    return 0


def cmd_mode(args: argparse.Namespace, store: StateStore) -> int:
    if args.mode_command == "start":
        _json(
            store.mode_start(
                autonomous=not args.bounded,
                stall_limit=args.stall_limit,
            )
        )
    elif args.mode_command == "stop":
        _json(store.mode_stop(args.reason))
    else:
        _json(store.meta().get("mode") or {})
    return 0


def cmd_round(args: argparse.Namespace, store: StateStore) -> int:
    if args.round_command == "start":
        _json(store.round_start(width=args.width, budget=args.budget))
    else:
        _json(store.round_close())
    return 0


def cmd_dashboard(args: argparse.Namespace, store: StateStore) -> int:
    if args.stop:
        _json(_stop_dashboard(store))
        return 0
    if args.background:
        _json(_start_dashboard_background(store, preferred_port=args.port))
        return 0
    port = args.port
    store.update_dashboard(pid=os.getpid(), port=port)
    print(f"Olo dashboard: http://{args.host}:{port}", flush=True)
    try:
        serve_dashboard(store, args.host, port)
    finally:
        current = store.meta().get("dashboard") or {}
        if int(current.get("pid") or 0) == os.getpid():
            store.update_dashboard(pid=None, port=None)
    return 0


def _build_report(store: StateStore) -> str:
    status = store.status_summary()
    graph = store.graph()
    lines = [
        f"# Olo report: {status['project_name']}",
        "",
        f"- Target: `{status['target']}`",
        f"- Metric: `{status['metric']}`",
        f"- Best: `{status['best_score']}` ({status['best_experiment']})",
        f"- Counts: `{status['counts']}`",
        "",
        "## Experiment tree",
        "",
        "```text",
        *store.tree_lines(),
        "```",
        "",
        "## Experiments",
        "",
        "| Experiment | Parent | Status | Score | Hypothesis |",
        "|---|---|---:|---:|---|",
    ]
    for exp_id, node in sorted(graph["nodes"].items()):
        if exp_id == "root":
            continue
        hypothesis = str(node.get("hypothesis") or "").replace("|", "\\|")
        lines.append(
            f"| {exp_id} | {node.get('parent')} | {node.get('status')} | "
            f"{node.get('score')} | {hypothesis} |"
        )
    annotations = store.annotations()
    if annotations:
        lines.extend(["", "## Learnings", ""])
        for item in annotations[-30:]:
            lines.append(
                f"- {item.get('experiment_id')}: {item.get('text')}"
            )
    return "\n".join(lines) + "\n"


def cmd_report(args: argparse.Namespace, store: StateStore) -> int:
    report = _build_report(store)
    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = store.root / path
        path.write_text(report, encoding="utf-8")
        print(path)
    else:
        print(report, end="")
    return 0


def cmd_doctor(store: StateStore) -> int:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    check("git", True, head_commit(store.root))
    check("initialized", store.is_initialized(), str(store.state_dir))
    if store.is_initialized():
        config = store.config()
        check(
            "target",
            (store.root / config["target"]).exists(),
            config["target"],
        )
    check(
        "skill",
        (store.root / ".github/skills/olo-autoresearch/SKILL.md").exists(),
        ".github/skills/olo-autoresearch/SKILL.md",
    )
    check(
        "agents",
        len(list((store.root / ".github/agents").glob("olo-*.agent.md"))) >= 4,
        ".github/agents/olo-*.agent.md",
    )
    check(
        "hooks",
        (store.root / ".github/hooks/olo.json").exists(),
        ".github/hooks/olo.json",
    )
    result = {"passed": all(item["passed"] for item in checks), "checks": checks}
    _json(result)
    return 0 if result["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="olo",
        description="Project-local autoresearch control plane for GitHub Copilot CLI.",
    )
    parser.add_argument("--repo", default=".", help="target Git repository")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version")

    init = sub.add_parser("init")
    init.add_argument("--name")
    init.add_argument("--target", required=True)
    init.add_argument("--benchmark", required=True)
    init.add_argument("--metric", choices=["max", "min"], default="max")
    init.add_argument("--gate", action="append")
    init.add_argument("--editable", action="append")
    init.add_argument("--protect", action="append")
    init.add_argument("--timeout", type=int, default=300)
    init.add_argument("--max-attempts", type=int, default=3)
    init.add_argument("--stall-limit", type=int, default=3)
    init.add_argument(
        "--strategy",
        choices=["argmax", "top-k", "epsilon-greedy", "softmax", "pareto-per-task"],
        default="pareto-per-task",
    )
    init.add_argument("--frontier-k", type=int, default=3)
    init.add_argument("--epsilon", type=float, default=0.1)
    init.add_argument("--temperature", type=float, default=0.5)
    init.add_argument("--score-ceiling", type=float)
    init.add_argument("--allow-dirty", action="store_true")
    init.add_argument("--no-dashboard", action="store_true")
    init.add_argument("--port", type=int, default=8765)

    baseline = sub.add_parser("baseline")
    baseline.add_argument("--hypothesis")
    baseline.add_argument("--timeout", type=int)

    new = sub.add_parser("new")
    new.add_argument("--parent", required=True)
    new.add_argument("--hypothesis", required=True)

    run = sub.add_parser("run")
    run.add_argument("experiment_id")
    run.add_argument("--timeout", type=int)

    discard = sub.add_parser("discard")
    discard.add_argument("experiment_id")
    discard.add_argument("--reason", required=True)
    discard.add_argument(
        "--failure-class",
        choices=["build", "eval", "hypothesis", "verification"],
        default="hypothesis",
    )
    discard.add_argument("--force", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")

    show = sub.add_parser("show")
    show.add_argument("experiment_id")
    sub.add_parser("tree")
    sub.add_parser("scratchpad")

    frontier = sub.add_parser("frontier")
    frontier.add_argument("--limit", type=int)
    frontier.add_argument(
        "--strategy",
        choices=["argmax", "top-k", "epsilon-greedy", "softmax", "pareto-per-task"],
    )

    diff = sub.add_parser("diff")
    diff.add_argument("experiment_id")
    diff.add_argument("other", nargs="?")

    traces = sub.add_parser("traces")
    traces.add_argument("experiment_id")
    traces.add_argument("task_id", nargs="?")

    verify = sub.add_parser("verify")
    verify.add_argument("experiment_id")
    verify.add_argument("--phase", choices=["pre", "post"], required=True)

    annotate = sub.add_parser("annotate")
    annotate.add_argument("experiment_id")
    annotate.add_argument("text")
    annotate.add_argument("--task")
    annotate.add_argument("--type", default="note")

    proposal = sub.add_parser("proposal")
    proposal_sub = proposal.add_subparsers(dest="proposal_command", required=True)
    proposal_sub.add_parser("list")
    proposal_add = proposal_sub.add_parser("add")
    proposal_add.add_argument("--source", required=True)
    proposal_add.add_argument("--title", required=True)
    proposal_add.add_argument("--hypothesis", required=True)
    proposal_add.add_argument("--rationale", required=True)
    proposal_add.add_argument("--parent")
    proposal_add.add_argument(
        "--confidence", choices=["low", "medium", "high"], default="medium"
    )

    strategy = sub.add_parser("strategy")
    strategy.add_argument(
        "kind",
        choices=["argmax", "top-k", "epsilon-greedy", "softmax", "pareto-per-task"],
    )
    strategy.add_argument("--k", type=int, default=3)
    strategy.add_argument("--epsilon", type=float, default=0.1)
    strategy.add_argument("--temperature", type=float, default=0.5)

    mode = sub.add_parser("mode")
    mode_sub = mode.add_subparsers(dest="mode_command", required=True)
    mode_start = mode_sub.add_parser("start")
    mode_start.add_argument("--bounded", action="store_true")
    mode_start.add_argument("--stall-limit", type=int)
    mode_stop = mode_sub.add_parser("stop")
    mode_stop.add_argument("--reason", default="manual")
    mode_sub.add_parser("status")

    round_parser = sub.add_parser("round")
    round_sub = round_parser.add_subparsers(dest="round_command", required=True)
    round_start = round_sub.add_parser("start")
    round_start.add_argument("--width", type=int, required=True)
    round_start.add_argument("--budget", type=int, required=True)
    round_sub.add_parser("close")

    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--background", action="store_true")
    dashboard.add_argument("--foreground", action="store_true")
    dashboard.add_argument("--stop", action="store_true")

    report = sub.add_parser("report")
    report.add_argument("--output")
    sub.add_parser("doctor")

    hook = sub.add_parser("hook", help=argparse.SUPPRESS)
    hook.add_argument(
        "event",
        choices=[
            "session-start",
            "pre-tool-use",
            "post-tool-use",
            "agent-stop",
            "subagent-start",
            "subagent-stop",
            "error-occurred",
        ],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"olo-autoresearch {__version__}")
        return 0

    raw_root = Path(args.repo).resolve()
    if args.command == "hook":
        try:
            payload_text = sys.stdin.read()
            payload = json.loads(payload_text) if payload_text.strip() else {}
            result = handle_hook(StateStore(raw_root), args.event, payload)
            print(json.dumps(result, separators=(",", ":")))
        except Exception:
            print("{}")
        return 0

    try:
        root = repo_root(raw_root)
        store = StateStore(root)
        if args.command == "init":
            return cmd_init(args, root)
        store.require_initialized()
        commands = {
            "baseline": lambda: cmd_baseline(args, store),
            "new": lambda: cmd_new(args, store),
            "run": lambda: cmd_run(args, store),
            "discard": lambda: cmd_discard(args, store),
            "status": lambda: cmd_status(args, store),
            "show": lambda: cmd_show(args, store),
            "tree": lambda: cmd_tree(store),
            "scratchpad": lambda: cmd_scratchpad(store),
            "frontier": lambda: cmd_frontier(args, store),
            "diff": lambda: cmd_diff(args, store),
            "traces": lambda: cmd_traces(args, store),
            "verify": lambda: cmd_verify(args, store),
            "annotate": lambda: cmd_annotate(args, store),
            "proposal": lambda: cmd_proposal(args, store),
            "strategy": lambda: cmd_strategy(args, store),
            "mode": lambda: cmd_mode(args, store),
            "round": lambda: cmd_round(args, store),
            "dashboard": lambda: cmd_dashboard(args, store),
            "report": lambda: cmd_report(args, store),
            "doctor": lambda: cmd_doctor(store),
        }
        return commands[args.command]()
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
