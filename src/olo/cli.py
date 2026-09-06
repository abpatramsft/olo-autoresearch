from __future__ import annotations

import argparse
import json
import math
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
from .utils import FileLock, atomic_write_json, is_pid_running, read_json, utc_now
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


def _normalize_relative_to(base: Path, raw: str) -> str:
    path = Path(raw)
    absolute = path.resolve() if path.is_absolute() else (base / path).resolve()
    try:
        relative = absolute.relative_to(base.resolve())
    except ValueError as exc:
        raise RuntimeError(f"path must be inside the experiment worktree: {raw}") from exc
    return relative.as_posix()


def _parse_gate(raw: str, index: int) -> dict[str, str]:
    if "::" in raw:
        name, command = raw.split("::", 1)
        return {"name": name.strip() or f"gate-{index}", "command": command.strip()}
    return {"name": f"gate-{index}", "command": raw.strip()}


def _policy_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings = {}
    for name in ("min_improvement", "min_relative_improvement", "max_task_regression", "max_evaluations", "evaluation_version", "final_test", "critical_tasks"):
        value = getattr(args, name, None)
        if value is None:
            continue
        if name in {"min_improvement", "min_relative_improvement", "max_task_regression"} and (not math.isfinite(value) or value < 0):
            raise ValueError(f"{name} must be finite and nonnegative")
        if name == "max_evaluations" and value < 1:
            raise ValueError("max_evaluations must be positive")
        settings[name] = value
    if getattr(args, "score_ceiling", None) is not None and not math.isfinite(args.score_ceiling):
        raise ValueError("score_ceiling must be finite")
    return settings


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


def _allocate(
    store: StateStore,
    parent_id: str,
    hypothesis: str,
    *,
    kind: str = "experiment",
    donors: list[str] | None = None,
    contributions: dict[str, str] | None = None,
    proposal_id: str | None = None,
) -> dict[str, Any]:
    graph = store.graph()
    commit = parent_commit(graph, parent_id)
    node = store.reserve_experiment(parent_id, hypothesis, kind=kind, donors=donors, contributions=contributions, proposal_id=proposal_id)
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
    atomic_write_json(store.experiment_dir(node["id"]) / "allocation.json", node)
    return node


def cmd_explore_init(args: argparse.Namespace, root: Path) -> int:
    ensure_repo_ready(root)
    if not args.allow_dirty:
        ensure_clean(root)
    store = StateStore(root)
    add_local_exclude(root)
    store.initialize_exploration(
        project_name=args.name or root.name,
        goal=args.goal,
        root_commit=head_commit(root),
        timeout_seconds=args.timeout,
        max_attempts=args.max_attempts,
        stall_limit=args.stall_limit,
    )
    result: dict[str, Any] = {
        "initialized": True,
        "phase": "exploring",
        "goal": args.goal,
        "state_dir": str(store.state_dir),
        "next": "Inspect the repository and record candidate dimensions.",
    }
    if not args.no_dashboard:
        result["dashboard"] = _start_dashboard_background(
            store,
            preferred_port=args.port,
        )
    _json(result)
    return 0


def _baseline_node(store: StateStore) -> dict[str, Any] | None:
    return store.graph().get("nodes", {}).get("exp_0000")


def _recreate_discarded_baseline(store: StateStore) -> dict[str, Any]:
    with FileLock(store.lock_path):
        graph = read_json(store.graph_path, {"root": "root", "nodes": {}})
        node = graph.get("nodes", {}).get("exp_0000")
        if node is None or node.get("status") != "discarded":
            raise RuntimeError("exp_0000 is not a discarded baseline")
        root_node = graph["nodes"]["root"]
        if "exp_0000" not in root_node.setdefault("children", []):
            root_node["children"].append("exp_0000")
        node.update(
            {
                "kind": "baseline",
                "status": "pending",
                "score": None,
                "tasks": {},
                "commit": None,
                "branch": "olo/exp_0000",
                "worktree": str(store.worktrees_dir / "exp_0000"),
                "attempts": 0,
                "gate_results": [],
                "changed_files": [],
                "error": None,
                "discard_reason": None,
                "failure_class": None,
                "updated_at": utc_now(),
            }
        )
        atomic_write_json(store.graph_path, graph)
    create_worktree(
        store.root,
        Path(node["worktree"]),
        str(node["branch"]),
        str(graph["nodes"]["root"]["commit"]),
    )
    store.add_event("baseline_recreated", experiment_id="exp_0000")
    return dict(node)


def cmd_explore(args: argparse.Namespace, store: StateStore) -> int:
    if args.explore_command == "status":
        _json(
            {
                "config": store.config(),
                "discovery": store.discovery(),
                "baseline": _baseline_node(store),
            }
        )
        return 0
    if args.explore_command == "add-dimension":
        target = args.target.replace("\\", "/")
        entry = store.add_dimension(
            name=args.name,
            description=args.description,
            target=target,
            metric_name=args.metric_name,
            direction=args.direction,
            evidence=args.evidence,
            complexity=args.complexity,
            run_cost=args.run_cost,
        )
        _json(entry)
        return 0
    if args.explore_command == "list-dimensions":
        discovery = store.discovery()
        _json(
            {
                "selected_dimension": discovery.get("selected_dimension"),
                "dimensions": discovery.get("dimensions", []),
            }
        )
        return 0
    if args.explore_command == "select":
        _json(store.select_dimension(args.name))
        return 0
    if args.explore_command != "configure":
        raise RuntimeError(f"unknown explore command: {args.explore_command}")

    baseline = _baseline_node(store)
    if baseline and baseline.get("status") in {"committed", "pending-review", "retained", "invalid"}:
        raise RuntimeError(
            "the baseline is already committed; start a new Olo workspace before "
            "changing the measurement system"
        )
    if baseline is None or not baseline.get("worktree"):
        raise RuntimeError(
            "prepare the baseline worktree first with `python olo.py baseline --prepare`"
        )
    worktree = Path(baseline["worktree"])
    if not worktree.exists():
        raise RuntimeError(f"baseline worktree is missing: {worktree}")
    target = _normalize_relative_to(worktree, args.target)
    if not (worktree / target).exists():
        raise RuntimeError(f"target does not exist in baseline worktree: {target}")
    editable = [
        _normalize_relative_to(worktree, item)
        for item in (args.editable or [target])
    ]
    protected = [
        _normalize_relative_to(worktree, item)
        for item in (args.protect or [])
    ]
    for path in protected:
        if not (worktree / path).exists():
            raise RuntimeError(f"protected path does not exist in baseline worktree: {path}")
    gates = [_parse_gate(raw, i + 1) for i, raw in enumerate(args.gate or [])]
    if args.benchmark_origin == "constructed" and not gates:
        raise RuntimeError("a constructed benchmark requires at least one real gate")

    config = store.config()
    config.update(
        {
            "phase": "ready-for-baseline",
            "target": target,
            "editable_paths": editable,
            "protected_paths": protected,
            "benchmark": args.benchmark,
            "benchmark_origin": args.benchmark_origin,
            "metric": args.metric,
            "gates": gates,
            "benchmark_unit": args.unit,
            "benchmark_determinism": args.determinism,
            "resource_profile": args.resource_profile,
            "meaningful_improvement": args.meaningful_improvement,
        }
    )
    config.update(_policy_settings(args))
    if args.timeout is not None:
        config["timeout_seconds"] = args.timeout
    if args.max_attempts is not None:
        config["max_attempts"] = args.max_attempts
    if args.stall_limit is not None:
        config["stall_limit"] = args.stall_limit
    if args.score_ceiling is not None:
        config["score_ceiling"] = args.score_ceiling
    if any(
        value is not None
        for value in (
            args.strategy,
            args.frontier_k,
            args.epsilon,
            args.temperature,
        )
    ):
        current_strategy = dict(config.get("frontier_strategy") or {})
        config["frontier_strategy"] = {
            "kind": args.strategy or current_strategy.get("kind", "pareto-per-task"),
            "k": (
                args.frontier_k
                if args.frontier_k is not None
                else current_strategy.get("k", 3)
            ),
            "epsilon": (
                args.epsilon
                if args.epsilon is not None
                else current_strategy.get("epsilon", 0.1)
            ),
            "temperature": (
                args.temperature
                if args.temperature is not None
                else current_strategy.get("temperature", 0.5)
            ),
        }
    store.save_config(config)

    def mark_configured(discovery: dict[str, Any]) -> None:
        discovery["status"] = "ready-for-baseline"
        discovery["repo_summary"] = args.repo_summary
        discovery["benchmark_plan"] = {
            "origin": args.benchmark_origin,
            "command": args.benchmark,
            "unit": args.unit,
            "metric": args.metric,
            "determinism": args.determinism,
            "resource_profile": args.resource_profile,
            "gaming_risks": args.gaming_risk or [],
        }

    store.mutate_discovery(mark_configured)
    selected = store.discovery().get("selected_dimension")
    project_lines = [
        f"# {config.get('project_name')}",
        "",
        "## Goal",
        "",
        str(config.get("goal") or "Selected during exploration."),
        "",
        "## Repository summary",
        "",
        args.repo_summary,
        "",
        "## Selected optimization dimension",
        "",
        str(selected or "Direct user goal"),
        "",
        "## Target and boundaries",
        "",
        f"- Target: `{target}`",
        f"- Editable: {', '.join(f'`{item}`' for item in editable)}",
        f"- Protected: {', '.join(f'`{item}`' for item in protected) or 'none'}",
        "",
        "## Benchmark",
        "",
        f"- Origin: {args.benchmark_origin}",
        f"- Command: `{args.benchmark}`",
        f"- Unit: {args.unit}",
        f"- Direction: {args.metric}",
        f"- Meaningful improvement: {args.meaningful_improvement}",
        f"- Determinism: {args.determinism}",
        "",
        "## Resource profile",
        "",
        args.resource_profile,
        "",
        "## Benchmark gaming risks",
        "",
    ]
    project_lines.extend(
        [f"- {item}" for item in (args.gaming_risk or ["No risks recorded."])]
    )
    project_lines.extend(["", "## Future experiment candidates", ""])
    project_lines.extend(
        [f"- {item}" for item in (args.future_dimension or ["None recorded."])]
    )
    store.write_project("\n".join(project_lines))
    store.add_event(
        "exploration_configured",
        target=target,
        benchmark_origin=args.benchmark_origin,
        metric=args.metric,
    )
    _json(
        {
            "phase": "ready-for-baseline",
            "target": target,
            "benchmark": args.benchmark,
            "gates": gates,
            "protected_paths": protected,
            "next": (
                "Run `python olo.py run exp_0000 --check`, audit with "
                "olo-benchmark-reviewer, then run `python olo.py baseline`."
            ),
        }
    )
    return 0


def cmd_init(args: argparse.Namespace, root: Path) -> int:
    policy = _policy_settings(args)
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
    config = store.config()
    config.update(policy)
    store.save_config(config)
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
    node = _baseline_node(store)
    if node is None:
        ensure_clean(store.root)
        if int(store.meta().get("next_id", 0)) != 0:
            raise RuntimeError("baseline must be the first Olo experiment")
        node = _allocate(
            store,
            "root",
            args.hypothesis
            or "Baseline: construct or instrument the benchmark and measure the unchanged target.",
            kind="baseline",
        )
    elif node.get("kind") != "baseline":
        raise RuntimeError("exp_0000 exists but is not an Olo baseline node")
    elif node.get("status") == "discarded":
        node = _recreate_discarded_baseline(store)
    if args.prepare:
        _json(
            {
                "experiment_id": node["id"],
                "branch": node["branch"],
                "worktree": node["worktree"],
                "status": node["status"],
                "next": "Create or instrument the benchmark and gates in this worktree.",
            }
        )
        return 0
    if node.get("status") == "committed":
        raise RuntimeError("the Olo baseline is already committed")
    if store.config().get("phase") not in {"configured", "ready-for-baseline"}:
        raise RuntimeError(
            "exploration is not configured; run `python olo.py explore configure ...`"
        )
    outcome = run_experiment(store, node["id"], timeout_override=args.timeout)
    _json(outcome)
    return 0 if outcome["status"] in {"committed", "pending-review"} else 1


def cmd_new(args: argparse.Namespace, store: StateStore) -> int:
    if store.config().get("phase", "configured") != "ready-to-optimize":
        raise RuntimeError(
            "Olo is not ready to optimize; complete exploration and commit the baseline first"
        )
    node = _allocate(store, args.parent, args.hypothesis, proposal_id=args.proposal)
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


def cmd_recombine(args: argparse.Namespace, store: StateStore) -> int:
    from .gitops import read_blob
    from .research import eligible_nodes
    from .verification import _covered

    config = store.config()
    if config.get("phase") != "ready-to-optimize":
        raise RuntimeError("approve the baseline before recombining experiments")
    approved = {node["id"]: node for node in eligible_nodes(store.graph())}
    if args.base not in approved or any(donor not in approved for donor in args.donor):
        raise RuntimeError("base and donors must be approved")
    contributions = {}
    for raw in args.contribution:
        donor_id, separator, description = raw.partition("::")
        if not separator or donor_id not in args.donor or not description.strip():
            raise ValueError("contributions must have the form DONOR::description")
        contributions[donor_id] = description.strip()
    transfers: dict[str, bytes] = {}
    for raw in args.take or []:
        donor_id, separator, raw_path = raw.partition(":")
        if not separator or donor_id not in args.donor:
            raise ValueError("file transfers must have the form DONOR:relative/path")
        path = _normalize_relative(store.root, raw_path)
        if _covered(path, config.get("protected_paths") or []) or not _covered(path, config.get("editable_paths") or []):
            raise RuntimeError(f"transfer is outside editable scope: {path}")
        if path in transfers:
            raise ValueError(f"multiple donors requested for the same path: {path}")
        transfers[path] = read_blob(store.root, approved[donor_id]["commit"], path)
    node = _allocate(store, args.base, args.hypothesis, donors=args.donor, contributions=contributions, proposal_id=args.proposal)
    for path, content in transfers.items():
        destination = Path(node["worktree"]) / path
        if not destination.resolve().is_relative_to(Path(node["worktree"]).resolve()):
            raise RuntimeError(f"transfer resolves outside candidate worktree: {path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    _json({**node, "experiment_id": node["id"], "transferred_files": sorted(transfers)})
    return 0


def cmd_run(args: argparse.Namespace, store: StateStore) -> int:
    outcome = run_experiment(
        store,
        args.experiment_id,
        timeout_override=args.timeout,
        check=args.check,
        probe=args.command == "probe",
    )
    _json(outcome)
    accepted = {"committed", "evaluated", "check-passed", "pending-review", "probed"}
    return 0 if outcome["status"] in accepted else 1


def cmd_review(args: argparse.Namespace, store: StateStore) -> int:
    from .research import review_experiment

    _json(review_experiment(store, args.experiment_id, verdict=args.verdict, reviewer=args.reviewer, reason=args.reason))
    return 0


def cmd_discard(args: argparse.Namespace, store: StateStore) -> int:
    from .gitops import capture_diff

    graph = store.graph()
    node = graph.get("nodes", {}).get(args.experiment_id)
    if node is None:
        raise RuntimeError(f"unknown experiment: {args.experiment_id}")
    if node.get("status") in {"committed", "retained"} and not args.force:
        raise RuntimeError("refusing to discard an approved node without --force")
    if not node.get("worktree"):
        raise RuntimeError("experiment has no worktree to discard")
    directory = store.experiment_dir(args.experiment_id) / "discard"
    directory.mkdir(parents=True, exist_ok=True)
    worktree = Path(node["worktree"])
    if worktree.exists():
        (directory / "diff.patch").write_text(capture_diff(worktree), encoding="utf-8")
    atomic_write_json(directory / "outcome.json", {
        "experiment_id": args.experiment_id, "status": "discarded", "reason": args.reason,
        "artifact_dir": str(directory.relative_to(store.state_dir)), "created_at": utc_now(),
        "latest_evidence": node.get("latest_record"), "score": node.get("score"),
    })
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
            f"{status['project_name']}: phase={status['phase']} best={status['best_score']} "
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


def cmd_scratchpad(store: StateStore, args: argparse.Namespace) -> int:
    print(store.scratchpad(query=args.query, parent=args.parent, limit=args.limit), end="")
    return 0


def cmd_learn(args: argparse.Namespace, store: StateStore) -> int:
    _json(store.add_learning(args.experiment_id, text=args.text, tags=args.tag or [], supersedes=args.supersedes or [], kind=args.kind))
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
    path = store.state_dir / (outcome.get("artifact_dir") or f"experiments/{args.experiment_id}/attempts/{int(outcome['attempt']):03d}") / "diff.patch"
    discarded = store.experiment_dir(args.experiment_id) / "discard/diff.patch"
    if discarded.exists():
        path = discarded
    if path.exists():
        print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_traces(args: argparse.Namespace, store: StateStore) -> int:
    outcome = store.latest_outcome(args.experiment_id)
    if not outcome:
        raise RuntimeError(f"no traces for {args.experiment_id}")
    traces = store.state_dir / (outcome.get("artifact_dir") or f"experiments/{args.experiment_id}/attempts/{int(outcome['attempt']):03d}") / "traces"
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
    if args.proposal_command == "update":
        _json(store.update_proposal(args.proposal_id, status=args.status, experiment_id=args.experiment))
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
        if store.config().get("phase") != "ready-to-optimize":
            raise RuntimeError(
                "Olo is not ready to optimize; finish `/olo-explore` and commit the baseline"
            )
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
    from .reporting import save_report

    report = save_report(store)
    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = store.root / path
        path.write_text(report, encoding="utf-8")
        print(path)
    else:
        print(report, end="")
    return 0


def cmd_finalize(args: argparse.Namespace, store: StateStore) -> int:
    from .measurement import finalize

    outcome = finalize(store, args.experiment)
    _json(outcome)
    return 0 if outcome["status"] == "completed" else 1


def cmd_doctor(store: StateStore) -> int:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    check("git", True, head_commit(store.root))
    check("initialized", store.is_initialized(), str(store.state_dir))
    if store.is_initialized():
        config = store.config()
        phase = config.get("phase", "configured")
        target = config.get("target", "")
        baseline = _baseline_node(store)
        baseline_target_exists = bool(
            target
            and baseline
            and baseline.get("worktree")
            and (Path(baseline["worktree"]) / target).exists()
        )
        check(
            "target",
            phase == "exploring"
            or (bool(target) and (store.root / target).exists())
            or baseline_target_exists,
            target or "pending discovery",
        )
    check(
        "skills",
        all(
            (store.root / ".github/skills" / name / "SKILL.md").exists()
            for name in ("olo-autoresearch", "olo-explore", "olo-optimize")
        ),
        ".github/skills/olo-autoresearch, olo-explore, and olo-optimize",
    )
    check(
        "agents",
        len(list((store.root / ".github/agents").glob("olo-*.agent.md"))) >= 6,
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

    explore = sub.add_parser("explore")
    explore_sub = explore.add_subparsers(dest="explore_command", required=True)
    explore_init = explore_sub.add_parser("init")
    explore_init.add_argument("--name")
    explore_init.add_argument("--goal")
    explore_init.add_argument("--timeout", type=int, default=300)
    explore_init.add_argument("--max-attempts", type=int, default=3)
    explore_init.add_argument("--stall-limit", type=int, default=3)
    explore_init.add_argument("--allow-dirty", action="store_true")
    explore_init.add_argument("--no-dashboard", action="store_true")
    explore_init.add_argument("--port", type=int, default=8765)
    explore_sub.add_parser("status")

    add_dimension = explore_sub.add_parser("add-dimension")
    add_dimension.add_argument("--name", required=True)
    add_dimension.add_argument("--description", required=True)
    add_dimension.add_argument("--target", required=True)
    add_dimension.add_argument("--metric-name", required=True)
    add_dimension.add_argument("--direction", choices=["max", "min"], required=True)
    add_dimension.add_argument("--evidence", required=True)
    add_dimension.add_argument(
        "--complexity",
        choices=["none", "minor", "substantial"],
        required=True,
    )
    add_dimension.add_argument(
        "--run-cost",
        choices=["small", "medium", "large"],
        required=True,
    )
    explore_sub.add_parser("list-dimensions")
    select_dimension = explore_sub.add_parser("select")
    select_dimension.add_argument("--name", required=True)

    configure = explore_sub.add_parser("configure")
    configure.add_argument("--target", required=True)
    configure.add_argument("--benchmark", required=True)
    configure.add_argument(
        "--benchmark-origin",
        choices=["existing", "wrapped", "constructed"],
        required=True,
    )
    configure.add_argument("--metric", choices=["max", "min"], default="max")
    configure.add_argument("--gate", action="append")
    configure.add_argument("--editable", action="append")
    configure.add_argument("--protect", action="append")
    configure.add_argument("--timeout", type=int)
    configure.add_argument("--max-attempts", type=int)
    configure.add_argument("--stall-limit", type=int)
    configure.add_argument(
        "--strategy",
        choices=["argmax", "top-k", "epsilon-greedy", "softmax", "pareto-per-task"],
    )
    configure.add_argument("--frontier-k", type=int)
    configure.add_argument("--epsilon", type=float)
    configure.add_argument("--temperature", type=float)
    configure.add_argument("--score-ceiling", type=float)
    configure.add_argument("--unit", required=True)
    configure.add_argument(
        "--determinism",
        choices=["deterministic", "temp-zero", "noisy"],
        required=True,
    )
    configure.add_argument("--resource-profile", required=True)
    configure.add_argument("--meaningful-improvement", required=True)
    configure.add_argument("--repo-summary", required=True)
    configure.add_argument("--gaming-risk", action="append")
    configure.add_argument("--future-dimension", action="append")

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

    for policy_parser in (init, configure):
        policy_parser.add_argument("--evaluation-version")
        policy_parser.add_argument("--min-improvement", type=float)
        policy_parser.add_argument("--min-relative-improvement", type=float)
        policy_parser.add_argument("--critical-task", dest="critical_tasks", action="append")
        policy_parser.add_argument("--max-task-regression", type=float)
        policy_parser.add_argument("--max-evaluations", type=int)
        policy_parser.add_argument("--final-test")

    evaluation = sub.add_parser("evaluation")
    evaluation_sub = evaluation.add_subparsers(dest="evaluation_command", required=True)
    evaluation_new = evaluation_sub.add_parser("new")
    evaluation_new.add_argument("--version", required=True)
    evaluation_new.add_argument("--from", dest="source", default="root")
    evaluation_new.add_argument("--goal")

    baseline = sub.add_parser("baseline")
    baseline.add_argument("--hypothesis")
    baseline.add_argument("--timeout", type=int)
    baseline.add_argument("--prepare", action="store_true")

    new = sub.add_parser("new")
    new.add_argument("--parent", required=True)
    new.add_argument("--hypothesis", required=True)
    new.add_argument("--proposal")

    recombine = sub.add_parser("recombine")
    recombine.add_argument("--base", required=True)
    recombine.add_argument("--donor", action="append", required=True)
    recombine.add_argument("--contribution", action="append", required=True)
    recombine.add_argument("--take", action="append")
    recombine.add_argument("--hypothesis", required=True)
    recombine.add_argument("--proposal")

    run = sub.add_parser("run")
    run.add_argument("experiment_id")
    run.add_argument("--timeout", type=int)
    run.add_argument("--check", action="store_true")

    probe = sub.add_parser("probe")
    probe.add_argument("experiment_id")
    probe.add_argument("--timeout", type=int)
    probe.set_defaults(check=False)

    review = sub.add_parser("review")
    review.add_argument("experiment_id")
    review.add_argument("--verdict", choices=["approve", "reject"], required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--reason", required=True)

    invalidate = sub.add_parser("invalidate")
    invalidate.add_argument("experiment_id")
    invalidate.add_argument("--reviewer", required=True)
    invalidate.add_argument("--reason", required=True)

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
    scratchpad = sub.add_parser("scratchpad")
    scratchpad.add_argument("--query", default="")
    scratchpad.add_argument("--parent")
    scratchpad.add_argument("--limit", type=int, default=12)

    learn = sub.add_parser("learn")
    learn.add_argument("experiment_id")
    learn.add_argument("--text", required=True)
    learn.add_argument("--tag", action="append")
    learn.add_argument("--supersedes", action="append")
    learn.add_argument("--kind", choices=["hypothesis", "observation"], default="hypothesis")

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
    proposal_update = proposal_sub.add_parser("update")
    proposal_update.add_argument("proposal_id")
    proposal_update.add_argument("--status", choices=["proposed", "claimed", "tested", "rejected", "superseded"], required=True)
    proposal_update.add_argument("--experiment")
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
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--experiment")
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
        if args.command == "explore" and args.explore_command == "init":
            return cmd_explore_init(args, root)
        store.require_initialized()
        if args.command == "evaluation":
            from .measurement import new_evaluation

            _json(new_evaluation(store, version=args.version, source=args.source, goal=args.goal))
            return 0
        if args.command == "invalidate":
            from .research import invalidate_experiment

            _json(invalidate_experiment(store, args.experiment_id, reviewer=args.reviewer, reason=args.reason))
            return 0
        commands = {
            "explore": lambda: cmd_explore(args, store),
            "baseline": lambda: cmd_baseline(args, store),
            "new": lambda: cmd_new(args, store),
            "recombine": lambda: cmd_recombine(args, store),
            "run": lambda: cmd_run(args, store),
            "probe": lambda: cmd_run(args, store),
            "review": lambda: cmd_review(args, store),
            "discard": lambda: cmd_discard(args, store),
            "status": lambda: cmd_status(args, store),
            "show": lambda: cmd_show(args, store),
            "tree": lambda: cmd_tree(store),
            "scratchpad": lambda: cmd_scratchpad(store, args),
            "learn": lambda: cmd_learn(args, store),
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
            "finalize": lambda: cmd_finalize(args, store),
            "doctor": lambda: cmd_doctor(store),
        }
        return commands[args.command]()
    except (RuntimeError, ValueError, KeyError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
