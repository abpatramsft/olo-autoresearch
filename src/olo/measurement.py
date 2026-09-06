from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from .utils import FileLock, atomic_write_json, generated_artifact, read_json, utc_now


def new_evaluation(store, *, version: str, source: str = "root", goal: str | None = None) -> dict[str, Any]:
    from .gitops import add_local_exclude, git
    from .reporting import save_report
    from .research import eligible_nodes
    from .utils import is_pid_running

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", version):
        raise ValueError("evaluation version must be a short alphanumeric label")
    history = store.root / ".olo-history"
    with FileLock(history / "transition.lock", timeout=0.2):
        previous = store.config()
        if version == previous.get("evaluation_version") or any(read_json(path, {}).get("evaluation_version") == version for path in history.glob("*/config.json")):
            raise RuntimeError("use a new evaluation version label")
        if is_pid_running(int((store.meta().get("dashboard") or {}).get("pid") or 0)):
            raise RuntimeError("stop this run's dashboard before starting a new evaluation")
        nodes = store.graph()["nodes"]
        if any(node.get("status") == "active" for node in nodes.values()):
            raise RuntimeError("finish active evaluations before archiving this run")
        approved = {node["id"] for node in eligible_nodes(store.graph())} | {"root"}
        if source not in approved:
            raise RuntimeError("the source must be root or an approved experiment")
        commit = nodes[source]["commit"]
        git(store.root, "cat-file", "-e", f"{commit}^{{commit}}")
        registered = [Path(line[9:]) for line in git(store.root, "worktree", "list", "--porcelain").stdout.splitlines() if line.startswith("worktree ")]
        run_id = version + "-" + uuid.uuid4().hex[:8]
        archive = history / (str(previous.get("evaluation_version", "legacy")) + "-" + uuid.uuid4().hex[:8])
        add_local_exclude(store.root)
        save_report(store)
        store.state_dir.rename(archive)
        for worktree in registered:
            if worktree.resolve().is_relative_to(store.state_dir):
                moved = archive / worktree.resolve().relative_to(store.state_dir)
                if moved.exists():
                    git(store.root, "worktree", "repair", str(moved))
        store.initialize_exploration(
            project_name=previous.get("project_name", store.root.name), goal=goal or previous.get("goal"),
            root_commit=commit, timeout_seconds=int(previous.get("timeout_seconds", 300)),
            max_attempts=int(previous.get("max_attempts", 3)), stall_limit=int(previous.get("stall_limit", 3)),
        )
        config = store.config()
        config.update(evaluation_version=version, branch_prefix=f"olo/{run_id}", previous_evaluation={
            "version": previous.get("evaluation_version", "legacy"), "archive": archive.relative_to(store.root).as_posix(),
            "source_experiment": source, "source_commit": commit,
        })
        store.save_config(config)
        store.add_event("evaluation_started", version=version, source=source, archive=str(archive))
        return {"evaluation_version": version, "phase": "exploring", "archive": str(archive), "source_commit": commit}


def measurement_snapshot(config: dict[str, Any], worktree: Path) -> dict[str, Any]:
    fields = (
        "evaluation_version", "benchmark", "gates", "metric", "target",
        "protected_paths", "editable_paths", "final_test", "min_improvement",
        "min_relative_improvement", "critical_tasks", "max_task_regression", "score_ceiling",
    )
    settings = {name: config.get(name) for name in fields}
    files = {}
    for name in config.get("protected_paths") or []:
        path = worktree / name
        if not path.resolve().is_relative_to(worktree.resolve()):
            raise RuntimeError(f"protected path is outside worktree: {name}")
        paths = sorted(path.rglob("*")) if path.is_dir() else [path]
        for protected in paths:
            relative = protected.relative_to(worktree).as_posix()
            if generated_artifact(relative) or protected.is_dir():
                continue
            files[relative] = hashlib.sha256(protected.read_bytes()).hexdigest() if protected.is_file() else "missing"
    content = {"settings": settings, "files": files}
    return {
        **content,
        "evaluation_version": config.get("evaluation_version", "legacy"),
        "digest": hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest(),
    }


def finalize(store, exp_id: str | None = None) -> dict[str, Any]:
    from .gitops import head_commit
    from .reporting import save_report
    from .research import eligible_nodes, fingerprint
    from .runner import _run_shell, fill_command, parse_benchmark_result

    directory = store.state_dir / "final-test"
    with FileLock(store.state_dir / "final-test.lock", timeout=0.2, stale_after=86400):
        config = store.config()
        if (directory / "outcome.json").exists() or config.get("phase") == "finalized":
            raise RuntimeError("the final test has already been exposed; start a new evaluation version")
        if not config.get("final_test"):
            raise RuntimeError("no final-test command was configured before baseline measurement")
        if any(node.get("status") in {"active", "pending-review"} for node in store.graph()["nodes"].values()):
            raise RuntimeError("finish all active evaluations and reviews before finalizing")
        approved = {node["id"]: node for node in eligible_nodes(store.graph())}
        node = approved.get(exp_id) if exp_id else store.best_node()
        if not node:
            raise RuntimeError("final testing requires an approved experiment")
        outcome = store.latest_outcome(node["id"])
        worktree = Path(node["worktree"])
        before = fingerprint(worktree)
        if not outcome or before != outcome.get("fingerprint") or head_commit(worktree) != node["commit"]:
            raise RuntimeError("approved candidate changed after measurement")
        manifest = read_json(store.state_dir / "measurement.json", {})
        if measurement_snapshot(config, worktree)["digest"] != manifest.get("digest"):
            raise RuntimeError("measurement configuration changed after baseline approval")
        directory.mkdir(parents=True, exist_ok=True)
        traces = directory / "traces"
        traces.mkdir(exist_ok=True)
        record = {
            "status": "started", "experiment_id": node["id"], "commit": node["commit"],
            "evaluation_version": config.get("evaluation_version"), "created_at": utc_now(),
            "measurement_digest": manifest["digest"], "fingerprint": before,
            "artifact_dir": "final-test",
        }
        atomic_write_json(directory / "outcome.json", record)
        config["phase"] = "finalized"
        store.save_config(config)
        store.mode_stop("final-test-exposed")
        env = os.environ.copy()
        result_path = directory / "result.json"
        env.update(
            OLO_RESULT_PATH=str(result_path), OLO_TRACES_DIR=str(traces),
            OLO_TARGET=str(worktree / config["target"]), OLO_WORKTREE=str(worktree),
            OLO_EXPERIMENT_ID=node["id"], PYTHONDONTWRITEBYTECODE="1",
            PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
        )
        command = fill_command(config["final_test"], worktree, worktree / config["target"])
        execution = _run_shell(command, cwd=worktree, env=env, timeout=int(config.get("timeout_seconds", 300)))
        for stream in ("stdout", "stderr"):
            (directory / f"final.{stream}.log").write_text(execution[stream], encoding="utf-8")
        try:
            if execution["timed_out"] or execution["returncode"] != 0:
                raise RuntimeError("final-test command failed or timed out; see saved logs")
            if fingerprint(worktree) != before:
                raise RuntimeError("final-test command changed the approved snapshot")
            result = parse_benchmark_result(result_path, execution["stdout"])
            record.update(status="completed", score=result["score"], tasks=result.get("tasks"), result=result)
        except (RuntimeError, ValueError) as exc:
            record.update(status="failed", error=str(exc))
        record.update(command=command, duration_seconds=execution["duration_seconds"], finished_at=utc_now())
        atomic_write_json(directory / "outcome.json", record)
        store.add_event("final_test_finished", **record)
        save_report(store)
        return record