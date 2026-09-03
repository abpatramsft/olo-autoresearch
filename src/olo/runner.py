from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .gitops import capture_diff, changed_files, commit_all
from .state import StateStore
from .utils import atomic_write_json, finite_number, quote_shell, utc_now
from .verification import verify_experiment


SCORE_RE = re.compile(
    r"[\"']?score[\"']?\s*[:=]\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)


def fill_command(command: str, worktree: Path, target: Path) -> str:
    values = {
        "{worktree}": quote_shell(worktree),
        "{repo}": quote_shell(worktree),
        "{target}": quote_shell(target),
    }
    rendered = command
    for marker, value in values.items():
        rendered = rendered.replace(marker, value)
    return rendered


def _run_shell(
    command: str,
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return {
            "returncode": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "timed_out": False,
            "duration_seconds": time.monotonic() - started,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return {
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": True,
            "duration_seconds": time.monotonic() - started,
        }


def parse_benchmark_result(result_path: Path, stdout: str) -> dict[str, Any]:
    candidates: list[Any] = []
    if result_path.exists():
        candidates.append(json.loads(result_path.read_text(encoding="utf-8")))
    stripped = stdout.strip()
    if stripped:
        try:
            candidates.append(json.loads(stripped))
        except json.JSONDecodeError:
            pass
        for line in reversed(stripped.splitlines()):
            try:
                candidates.append(json.loads(line))
                break
            except json.JSONDecodeError:
                continue
    for value in candidates:
        if isinstance(value, dict) and "score" in value:
            score = finite_number(value["score"])
            tasks: dict[str, float] = {}
            for task_id, task_score in (value.get("tasks") or {}).items():
                try:
                    tasks[str(task_id)] = finite_number(task_score)
                except (TypeError, ValueError):
                    continue
            return {**value, "score": score, "tasks": tasks}
    match = SCORE_RE.search(stdout)
    if match:
        return {"score": finite_number(match.group(1)), "tasks": {}}
    raise RuntimeError(
        "benchmark did not emit a JSON object containing a finite `score`"
    )


def _is_improvement(metric: str, candidate: float, parent: float | None) -> bool:
    if parent is None:
        return True
    return candidate > parent if metric == "max" else candidate < parent


def run_experiment(
    store: StateStore,
    exp_id: str,
    *,
    timeout_override: int | None = None,
    check: bool = False,
) -> dict[str, Any]:
    config = store.config()
    graph = store.graph()
    node = graph.get("nodes", {}).get(exp_id)
    if node is None:
        raise RuntimeError(f"unknown experiment: {exp_id}")
    if not config.get("target") or not config.get("benchmark"):
        raise RuntimeError(
            "benchmark configuration is incomplete; finish `python olo.py explore configure` first"
        )
    if node.get("status") == "discarded":
        raise RuntimeError(
            f"{exp_id} is discarded; restore the baseline with "
            "`python olo.py baseline --prepare` or create a new experiment"
        )
    if node.get("status") == "committed" and not check:
        raise RuntimeError(f"{exp_id} is already committed")
    attempts = int(node.get("attempts", 0))
    max_attempts = int(config.get("max_attempts", 3))
    if (
        not check
        and node.get("kind") != "baseline"
        and attempts >= max_attempts
    ):
        raise RuntimeError(
            f"{exp_id} reached max_attempts={max_attempts}; discard it or create a sibling"
        )

    worktree_value = node.get("worktree")
    if not worktree_value:
        raise RuntimeError(
            f"{exp_id} has no worktree; recreate it before running"
        )
    worktree = Path(worktree_value)
    if not worktree.exists():
        raise RuntimeError(f"experiment worktree is missing: {worktree}")
    target = worktree / str(config["target"])
    if not target.exists():
        raise RuntimeError(f"configured target does not exist in worktree: {target}")
    if check:
        checks_root = store.experiment_dir(exp_id) / "checks"
        existing = [
            int(path.name)
            for path in checks_root.iterdir()
            if path.is_dir() and path.name.isdigit()
        ] if checks_root.exists() else []
        attempt = max(existing, default=0) + 1
        attempt_dir = checks_root / f"{attempt:03d}"
        traces_dir = attempt_dir / "traces"
    else:
        attempt = attempts + 1
        attempt_dir = store.attempt_dir(exp_id, attempt)
        traces_dir = store.traces_dir(exp_id, attempt)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)
    result_path = attempt_dir / "benchmark-result.json"
    timeout = int(timeout_override or config.get("timeout_seconds", 300))
    if not check:
        store.update_node(exp_id, status="active", attempts=attempt, current_attempt=attempt)
        store.add_event("experiment_started", experiment_id=exp_id, attempt=attempt)
    else:
        store.add_event("experiment_check_started", experiment_id=exp_id, check=attempt)

    pre = verify_experiment(store, exp_id, phase="pre", persist=not check)
    if not pre["passed"]:
        outcome = {
            "experiment_id": exp_id,
            "check" if check else "attempt": attempt,
            "status": "check-failed" if check else "failed",
            "score": None,
            "parent_score": None,
            "gates_passed": False,
            "gate_results": [],
            "trace_count": 0,
            "duration_seconds": 0.0,
            "error": "pre-verification failed",
            "verification": pre,
            "created_at": utc_now(),
        }
        atomic_write_json(
            attempt_dir / ("check.json" if check else "outcome.json"),
            outcome,
        )
        if not check:
            store.update_node(exp_id, status="failed", error=outcome["error"])
        return outcome

    benchmark_command = fill_command(str(config["benchmark"]), worktree, target)
    env = os.environ.copy()
    env.update(
        {
            "OLO_EXPERIMENT_ID": exp_id,
            "OLO_WORKTREE": str(worktree),
            "OLO_TARGET": str(target),
            "OLO_RESULT_PATH": str(result_path),
            "OLO_TRACES_DIR": str(traces_dir),
        }
    )
    benchmark = _run_shell(
        benchmark_command,
        cwd=worktree,
        env=env,
        timeout=timeout,
    )
    (attempt_dir / "benchmark.stdout.log").write_text(
        benchmark["stdout"], encoding="utf-8"
    )
    (attempt_dir / "benchmark.stderr.log").write_text(
        benchmark["stderr"], encoding="utf-8"
    )

    diff = capture_diff(worktree)
    (attempt_dir / "diff.patch").write_text(diff, encoding="utf-8")
    files = changed_files(worktree)
    benchmark_error: str | None = None
    result: dict[str, Any] | None = None
    if benchmark["timed_out"]:
        benchmark_error = f"benchmark timed out after {timeout}s"
    elif benchmark["returncode"] != 0:
        benchmark_error = f"benchmark exited with code {benchmark['returncode']}"
    else:
        try:
            result = parse_benchmark_result(result_path, benchmark["stdout"])
        except Exception as exc:
            benchmark_error = str(exc)

    gate_results: list[dict[str, Any]] = []
    if benchmark_error is None:
        for gate in config.get("gates") or []:
            gate_name = str(gate.get("name") or "gate")
            gate_command = fill_command(str(gate["command"]), worktree, target)
            gate_result = _run_shell(
                gate_command,
                cwd=worktree,
                env=env,
                timeout=timeout,
            )
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", gate_name).strip("-")
            (attempt_dir / f"gate-{safe_name}.stdout.log").write_text(
                gate_result["stdout"], encoding="utf-8"
            )
            (attempt_dir / f"gate-{safe_name}.stderr.log").write_text(
                gate_result["stderr"], encoding="utf-8"
            )
            gate_results.append(
                {
                    "name": gate_name,
                    "command": gate_command,
                    "passed": not gate_result["timed_out"]
                    and gate_result["returncode"] == 0,
                    "returncode": gate_result["returncode"],
                    "timed_out": gate_result["timed_out"],
                    "duration_seconds": gate_result["duration_seconds"],
                }
            )

    gates_passed = benchmark_error is None and all(
        item["passed"] for item in gate_results
    )
    trace_count = len(list(traces_dir.glob("*.json")))
    score = None if result is None else float(result["score"])
    if check:
        status = (
            "check-passed"
            if benchmark_error is None and gates_passed
            else "check-failed"
        )
        outcome = {
            "experiment_id": exp_id,
            "check": attempt,
            "status": status,
            "score": score,
            "gates_passed": gates_passed,
            "gate_results": gate_results,
            "benchmark": {
                "command": benchmark_command,
                "returncode": benchmark["returncode"],
                "timed_out": benchmark["timed_out"],
                "result": result,
            },
            "changed_files": files,
            "trace_count": trace_count,
            "duration_seconds": float(benchmark["duration_seconds"]),
            "error": benchmark_error,
            "verification": pre,
            "created_at": utc_now(),
        }
        post = verify_experiment(
            store,
            exp_id,
            phase="post",
            persist=False,
            outcome_override=outcome,
        )
        outcome["verification"] = post
        if not post["passed"]:
            outcome["status"] = "check-failed"
            outcome["error"] = "post-verification failed"
        atomic_write_json(attempt_dir / "check.json", outcome)
        store.add_event(
            "experiment_check_finished",
            experiment_id=exp_id,
            check=attempt,
            status=outcome["status"],
            score=score,
        )
        return outcome

    parent = graph["nodes"][node["parent"]]
    parent_score = (
        None if parent.get("score") is None else float(parent.get("score"))
    )
    improved = (
        score is not None
        and gates_passed
        and _is_improvement(str(config.get("metric", "max")), score, parent_score)
    )
    if benchmark_error is not None:
        status = "failed"
    elif improved:
        status = "committed"
    else:
        status = "evaluated"

    outcome = {
        "experiment_id": exp_id,
        "attempt": attempt,
        "status": status,
        "score": score,
        "parent_score": parent_score,
        "improved": improved,
        "gates_passed": gates_passed,
        "gate_results": gate_results,
        "benchmark": {
            "command": benchmark_command,
            "returncode": benchmark["returncode"],
            "timed_out": benchmark["timed_out"],
            "result": result,
        },
        "changed_files": files,
        "trace_count": trace_count,
        "duration_seconds": float(benchmark["duration_seconds"]),
        "commit": None,
        "error": benchmark_error,
        "created_at": utc_now(),
    }
    post = verify_experiment(
        store,
        exp_id,
        phase="post",
        persist=True,
        outcome_override=outcome,
    )
    if not post["passed"]:
        status = "failed"
        improved = False
        outcome["error"] = "post-verification failed"
    commit: str | None = None
    if status == "committed":
        commit = commit_all(
            worktree,
            f"olo({exp_id}): {str(node.get('hypothesis') or '')[:100]}",
        )
    outcome.update(
        {
            "status": status,
            "improved": improved,
            "commit": commit,
            "verification": post,
        }
    )
    atomic_write_json(attempt_dir / "outcome.json", outcome)
    store.update_node(
        exp_id,
        status=status,
        score=score,
        tasks=(result or {}).get("tasks") or {},
        commit=commit,
        gate_results=gate_results,
        duration_seconds=float(benchmark["duration_seconds"]),
        changed_files=files,
        error=outcome.get("error"),
    )
    store.add_event(
        "experiment_finished",
        experiment_id=exp_id,
        attempt=attempt,
        status=status,
        score=score,
        improved=improved,
    )
    if node.get("kind") == "baseline" and status == "committed":
        latest_config = store.config()
        latest_config["phase"] = "ready-to-optimize"
        store.save_config(latest_config)

        def mark_ready(discovery: dict[str, Any]) -> None:
            discovery["status"] = "ready-to-optimize"
            discovery["baseline_experiment"] = exp_id
            discovery["baseline_score"] = score

        store.mutate_discovery(mark_ready)
        store.add_event(
            "exploration_completed",
            baseline_experiment=exp_id,
            baseline_score=score,
        )
    return outcome
