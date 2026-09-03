from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from .gitops import changed_files
from .state import StateStore


def _normalized(path: str) -> str:
    value = path.replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    value = value.rstrip("/")
    return value or "."


def _covered(path: str, roots: list[str]) -> bool:
    candidate = _normalized(path)
    for root in roots:
        allowed = _normalized(root).rstrip("/")
        if allowed in {"", "."}:
            return True
        if candidate == allowed or candidate.startswith(allowed + "/"):
            return True
    return False


def verify_experiment(
    store: StateStore,
    exp_id: str,
    *,
    phase: str,
    persist: bool = True,
) -> dict[str, Any]:
    graph = store.graph()
    config = store.config()
    node = graph.get("nodes", {}).get(exp_id)
    if node is None:
        raise RuntimeError(f"unknown experiment: {exp_id}")
    if phase not in {"pre", "post"}:
        raise ValueError("verification phase must be pre or post")

    findings: list[dict[str, str]] = []
    if phase == "pre":
        worktree = Path(node["worktree"])
        files = changed_files(worktree) if worktree.exists() else []
        protected = list(config.get("protected_paths") or [])
        editable = list(config.get("editable_paths") or [config.get("target")])
        parent_is_root = node.get("parent") == "root"
        baseline_setup = parent_is_root and node.get("kind") == "baseline"

        if not worktree.exists():
            findings.append(
                {
                    "severity": "block",
                    "category": "workspace",
                    "what": "experiment worktree is missing",
                    "where": str(worktree),
                    "fix": "recreate the experiment with `python olo.py new`",
                }
            )
        if not files and not parent_is_root:
            findings.append(
                {
                    "severity": "block",
                    "category": "changes",
                    "what": "candidate has no source changes",
                    "where": str(worktree),
                    "fix": "make the concrete candidate edit before running the benchmark",
                }
            )
        for path in files:
            if not baseline_setup and _covered(path, protected):
                findings.append(
                    {
                        "severity": "block",
                        "category": "benchmark-integrity",
                        "what": f"candidate modified protected path {path}",
                        "where": path,
                        "fix": "revert benchmark, gate, and fixture changes",
                    }
                )
            elif not baseline_setup and editable and not _covered(path, editable):
                findings.append(
                    {
                        "severity": "block",
                        "category": "scope",
                        "what": f"candidate changed out-of-scope path {path}",
                        "where": path,
                        "fix": f"limit changes to: {', '.join(editable)}",
                    }
                )
        hypothesis = str(node.get("hypothesis") or "").strip()
        if not baseline_setup and len(hypothesis) < 35:
            findings.append(
                {
                    "severity": "warn",
                    "category": "hypothesis",
                    "what": "hypothesis is too generic to evaluate cleanly",
                    "where": exp_id,
                    "fix": "name the file/function, concrete edit, and predicted behavior",
                }
            )
    else:
        outcome = store.latest_outcome(exp_id)
        if not outcome:
            findings.append(
                {
                    "severity": "block",
                    "category": "result",
                    "what": "no completed attempt outcome exists",
                    "where": exp_id,
                    "fix": "run the experiment before post-verification",
                }
            )
        else:
            if outcome.get("status") == "committed" and not outcome.get("gates_passed"):
                findings.append(
                    {
                        "severity": "block",
                        "category": "gates",
                        "what": "committed experiment did not pass every gate",
                        "where": str(store.attempt_dir(exp_id, int(outcome["attempt"]))),
                        "fix": "discard the experiment and repair gate execution",
                    }
                )
            if int(outcome.get("trace_count") or 0) == 0:
                findings.append(
                    {
                        "severity": "warn",
                        "category": "instrumentation",
                        "what": "benchmark emitted no per-task traces",
                        "where": str(store.traces_dir(exp_id, int(outcome["attempt"]))),
                        "fix": "write one JSON trace per evaluated item to OLO_TRACES_DIR",
                    }
                )
            durations: list[float] = []
            for other in graph.get("nodes", {}).values():
                if other.get("id") in {"root", exp_id}:
                    continue
                if other.get("status") != "committed":
                    continue
                duration = other.get("duration_seconds")
                if duration is not None:
                    durations.append(float(duration))
            current_duration = float(outcome.get("duration_seconds") or 0.0)
            if durations:
                median = statistics.median(durations)
                if median >= 1.0 and current_duration < median * 0.2:
                    findings.append(
                        {
                            "severity": "block",
                            "category": "duration",
                            "what": "runtime is under 20% of the committed cohort median",
                            "where": exp_id,
                            "fix": "check for cached or skipped evaluation paths",
                        }
                    )

    has_block = any(item["severity"] == "block" for item in findings)
    has_warn = any(item["severity"] == "warn" for item in findings)
    verdict = "fail" if has_block else "warn" if has_warn else "pass"
    report = {
        "phase": phase,
        "experiment_id": exp_id,
        "passed": not has_block,
        "verdict": verdict,
        "findings": findings,
    }
    if persist:
        store.add_annotation(
            exp_id,
            annotation_type="verification",
            text=f"{phase} verification: {verdict}",
            payload=report,
        )
    return report
