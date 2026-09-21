from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .gitops import capture_diff, changed_files, commit_all
from .state import StateStore
from .research import decide, fingerprint, trace_facts
from .measurement import measurement_snapshot
from .utils import FileLock, atomic_write_json, finite_number, quote_shell, utc_now
from .verification import verify_experiment


SCORE_RE = re.compile(
    r"[\"']?score[\"']?\s*[:=]\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)


def bind_python(command: str) -> str:
    return command.replace("{python}", quote_shell(sys.executable))


def fill_command(command: str, worktree: Path, target: Path) -> str:
    values = {
        "{worktree}": quote_shell(worktree),
        "{repo}": quote_shell(worktree),
        "{target}": quote_shell(target),
    }
    rendered = bind_python(command)
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
    for launch_attempt in range(2):
        try:
            process = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
            # Windows occasionally returns HRESULT 0x8007001F from cmd.exe
            # during rapid check -> baseline process launches. It is a launcher
            # failure, not the benchmark's exit code, so retry it once.
            if (
                os.name == "nt"
                and process.returncode == 0x8007001F
                and launch_attempt == 0
            ):
                time.sleep(0.1)
                continue
            return {
                "returncode": process.returncode,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "timed_out": False,
                "duration_seconds": time.monotonic() - started,
            }
        except subprocess.TimeoutExpired as exc:
            stdout = (
                exc.stdout.decode()
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode()
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            return {
                "returncode": None,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": True,
                "duration_seconds": time.monotonic() - started,
            }
    raise RuntimeError("unreachable shell execution state")


def _validate_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or "score" not in value:
        raise ValueError("benchmark result must be a JSON object containing a finite `score`")
    try:
        score = finite_number(value["score"])
    except (TypeError, ValueError) as exc:
        raise ValueError("benchmark score must be finite") from exc
    raw_tasks = value.get("tasks", {})
    if not isinstance(raw_tasks, dict):
        raise ValueError("benchmark tasks must be an object of task IDs and finite scores")
    tasks: dict[str, float] = {}
    for task_id, task_score in raw_tasks.items():
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("benchmark task IDs must be nonempty strings")
        try:
            tasks[task_id] = finite_number(task_score)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"benchmark task {task_id!r} must have a finite score") from exc
    return {**value, "score": score, "tasks": tasks}


def parse_benchmark_result(result_path: Path, stdout: str) -> dict[str, Any]:
    if result_path.exists():
        return _validate_result(json.loads(result_path.read_text(encoding="utf-8")))
    stripped = stdout.strip()
    for text in [stripped, *reversed(stripped.splitlines())]:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "score" in value:
            return _validate_result(value)
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
    probe: bool = False,
) -> dict[str, Any]:
    with FileLock(store.experiment_dir(exp_id) / "run.lock", timeout=0.2, stale_after=86400):
        return _execute(store, exp_id, timeout_override=timeout_override, check=check, probe=probe)


def _execute(store: StateStore, exp_id: str, *, timeout_override: int | None, check: bool, probe: bool) -> dict[str, Any]:
    config = store.config()
    if config.get("phase") == "finalized":
        raise RuntimeError("this evaluation version is finalized; no further tuning is allowed")
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
    if node.get("status") in {"committed", "retained", "pending-review", "invalid"} and not check:
        raise RuntimeError(f"{exp_id} already has a measured snapshot; create a new experiment")
    attempts = int(node.get("attempts", 0))
    max_attempts = int(config.get("max_attempts", 3))
    if (
        not check
        and not probe
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
    baseline_assessment = None
    if node.get("kind") == "baseline" and config.get("require_baseline_checks") and not check and not probe:
        from .discovery import assess_baseline

        baseline_assessment = assess_baseline(store, persist=True)
        if not baseline_assessment["passed"]:
            blockers = "; ".join(
                item["what"] for item in baseline_assessment["findings"] if item["severity"] == "block"
            )
            raise RuntimeError(f"baseline is not ready: {blockers} Run `python olo.py explore assess` for remedies.")
    pre = verify_experiment(store, exp_id, phase="pre", persist=not check)
    if not pre["passed"]:
        preflight = int(node.get("preflight_count", 0)) + 1
        preflight_dir = store.experiment_dir(exp_id) / "preflight" / f"{preflight:03d}"
        preflight_dir.mkdir(parents=True, exist_ok=True)
        (preflight_dir / "diff.patch").write_text(capture_diff(worktree), encoding="utf-8")
        outcome = {
            "experiment_id": exp_id, "status": "check-failed" if check else "blocked",
            "score": None, "gates_passed": False, "gate_results": [],
            "error": "pre-verification failed", "verification": pre,
            "preflight": preflight, "duration_seconds": 0.0, "created_at": utc_now(),
            "artifact_dir": str(preflight_dir.relative_to(store.state_dir)),
        }
        record = preflight_dir / "outcome.json"
        atomic_write_json(record, outcome)
        store.update_node(exp_id, preflight_count=preflight, latest_record=str(record.relative_to(store.state_dir)))
        store.add_event("experiment_blocked", experiment_id=exp_id, preflight=preflight)
        return outcome
    store.consume_evaluation(baseline=node.get("kind") == "baseline")
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
    elif probe:
        attempt = int(node.get("probe_count", 0)) + 1
        attempt_dir = store.experiment_dir(exp_id) / "probes" / f"{attempt:03d}"
        traces_dir = attempt_dir / "traces"
    else:
        attempt = attempts + 1
        attempt_dir = store.attempt_dir(exp_id, attempt)
        traces_dir = store.traces_dir(exp_id, attempt)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)
    result_path = attempt_dir / "benchmark-result.json"
    timeout = int(timeout_override or config.get("timeout_seconds", 300))
    if probe:
        store.update_node(exp_id, probe_count=attempt)
        store.add_event("probe_started", experiment_id=exp_id, probe=attempt)
    elif not check:
        store.update_node(exp_id, status="active", attempts=attempt, current_attempt=attempt)
        store.add_event("experiment_started", experiment_id=exp_id, attempt=attempt)
    else:
        store.add_event("experiment_check_started", experiment_id=exp_id, check=attempt)

    benchmark_command = fill_command(str(config["benchmark"]), worktree, target)
    source_fingerprint = fingerprint(worktree)
    env = os.environ.copy()
    env.update(
        {
            "OLO_EXPERIMENT_ID": exp_id,
            "OLO_WORKTREE": str(worktree),
            "OLO_TARGET": str(target),
            "OLO_RESULT_PATH": str(result_path),
            "OLO_TRACES_DIR": str(traces_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
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

    benchmark_error: str | None = None
    result: dict[str, Any] | None = None
    if benchmark["timed_out"]:
        benchmark_error = f"benchmark timed out after {timeout}s"
    elif benchmark["returncode"] != 0:
        benchmark_error = f"benchmark exited with code {benchmark['returncode']}"
    else:
        try:
            result = parse_benchmark_result(result_path, benchmark["stdout"])
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            benchmark_error = str(exc)
    if baseline_assessment and result is not None and config.get("benchmark_determinism") == "deterministic":
        from .discovery import same_result

        if not same_result(baseline_assessment["result"], result):
            benchmark_error = "deterministic baseline differs from its checked result; repair repeatability and recheck before freezing"

    gate_results: list[dict[str, Any]] = []
    if benchmark_error is None:
        for index, gate in enumerate(config.get("gates") or [], 1):
            gate_name = str(gate.get("name") or "gate")
            gate_command = fill_command(str(gate["command"]), worktree, target)
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", gate_name).strip("-") or "gate"
            gate_dir = attempt_dir / "gates" / f"{index:03d}-{safe_name}"
            gate_traces = gate_dir / "traces"
            gate_traces.mkdir(parents=True, exist_ok=True)
            gate_env = {
                **env,
                "OLO_RESULT_PATH": str(gate_dir / "result.json"),
                "OLO_TRACES_DIR": str(gate_traces),
            }
            gate_result = _run_shell(
                gate_command,
                cwd=worktree,
                env=gate_env,
                timeout=timeout,
            )
            (gate_dir / "stdout.log").write_text(
                gate_result["stdout"], encoding="utf-8"
            )
            (gate_dir / "stderr.log").write_text(
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
                    "artifact_dir": str(gate_dir.relative_to(store.state_dir)),
                }
            )

    diff = capture_diff(worktree)
    (attempt_dir / "diff.patch").write_text(diff, encoding="utf-8")
    files = changed_files(worktree)
    gates_passed = benchmark_error is None and all(
        item["passed"] for item in gate_results
    )
    trace_count = len(list(traces_dir.glob("*.json")))
    measured_fingerprint = fingerprint(worktree)
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
            "source_fingerprint": source_fingerprint,
            "fingerprint": measured_fingerprint,
            "measurement": measurement_snapshot(config, worktree),
            "duration_seconds": float(benchmark["duration_seconds"]),
            "total_duration_seconds": float(benchmark["duration_seconds"]) + sum(item["duration_seconds"] for item in gate_results),
            "error": benchmark_error,
            "verification": pre,
            "artifact_dir": str(attempt_dir.relative_to(store.state_dir)),
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
    decision = decide(config, parent, result) if result is not None else {"meaningful": False, "task_changes": {}}
    improved = (
        score is not None
        and gates_passed
        and decision["meaningful"]
    )
    if benchmark_error is not None:
        status = "failed"
    elif gates_passed and config.get("review_required"):
        status = "pending-review"
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
        "decision": decision,
        "task_changes": decision["task_changes"],
        "source_comparison": {
            source_id: {
                "score": graph["nodes"][source_id].get("score"),
                "task_changes": decide(config, graph["nodes"][source_id], result)["task_changes"],
                "gain": decide(config, graph["nodes"][source_id], result)["gain"],
            }
            for source_id in [node["parent"], *(node.get("donors") or [])]
            if source_id != "root" and result is not None
        },
        "trace_facts": [],
        "evaluation_version": config.get("evaluation_version", "legacy"),
        "artifact_dir": str(attempt_dir.relative_to(store.state_dir)),
        "source_fingerprint": source_fingerprint,
        "fingerprint": measured_fingerprint,
        "measurement": measurement_snapshot(config, worktree),
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
        "total_duration_seconds": float(benchmark["duration_seconds"]) + sum(item["duration_seconds"] for item in gate_results),
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
    if post["passed"]:
        outcome["trace_facts"] = trace_facts(traces_dir)
    if not post["passed"]:
        status = "failed"
        improved = False
        outcome["error"] = "post-verification failed"
    commit: str | None = None
    if probe:
        status = "probed" if post["passed"] and benchmark_error is None and gates_passed else "probe-failed"
        improved = False
    elif status in {"committed", "pending-review"}:
        commit = commit_all(
            worktree,
            f"olo({exp_id}): {str(node.get('hypothesis') or '')[:100]}",
        )
        if node.get("kind") == "baseline":
            atomic_write_json(store.state_dir / "measurement.json", outcome["measurement"])
    outcome.update(
        {
            "status": status,
            "improved": improved,
            "commit": commit,
            "verification": post,
        }
    )
    atomic_write_json(attempt_dir / "outcome.json", outcome)
    if probe:
        store.update_node(exp_id, latest_record=str((attempt_dir / "outcome.json").relative_to(store.state_dir)))
        store.add_event("probe_finished", experiment_id=exp_id, probe=attempt, score=score, status=status)
        return outcome
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
        review_status="pending" if status == "pending-review" else "approved" if status == "committed" else "unapproved",
        latest_record=str((attempt_dir / "outcome.json").relative_to(store.state_dir)),
        task_changes=decision["task_changes"],
        decision=decision,
        evaluation_version=config.get("evaluation_version", "legacy"),
    )
    store.add_event(
        "experiment_finished",
        experiment_id=exp_id,
        attempt=attempt,
        status=status,
        score=score,
        improved=improved,
    )
    store.add_annotation(
        exp_id, annotation_type="observation",
        text=f"Measured score {score}; gain {decision.get('gain')}; improved tasks {decision['task_changes'].get('improved', [])}; regressed tasks {decision['task_changes'].get('regressed', [])}.",
        payload={"decision": decision, "trace_facts": outcome["trace_facts"], "evidence": str((attempt_dir / "outcome.json").relative_to(store.state_dir))},
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
