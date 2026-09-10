from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .measurement import measurement_snapshot
from .research import fingerprint
from .utils import read_json, utc_now


def same_result(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_tasks = left.get("tasks") or {}
    right_tasks = right.get("tasks") or {}
    return (
        math.isclose(left["score"], right["score"], rel_tol=1e-12, abs_tol=1e-12)
        and left_tasks.keys() == right_tasks.keys()
        and all(
            math.isclose(score, right_tasks[task], rel_tol=1e-12, abs_tol=1e-12)
            for task, score in left_tasks.items()
        )
    )


def assess_baseline(store, *, persist: bool = False) -> dict[str, Any]:
    config = store.config()
    discovery = store.discovery()
    node = store.graph()["nodes"].get("exp_0000") or {}
    required = 2 if config.get("benchmark_determinism") == "deterministic" else 3
    findings: list[dict[str, str]] = []
    assessment: dict[str, Any] = {
        "experiment_id": "exp_0000",
        "assessed_at": utc_now(),
        "required_checks": required,
        "matching_checks": 0,
        "check_records": [],
        "task_count": 0,
        "score": None,
        "observed_score_range": None,
        "remaining_headroom": None,
        "minimum_gain": None,
        "findings": findings,
    }

    def finding(category: str, what: str, fix: str, *, severity: str = "block") -> None:
        findings.append({"severity": severity, "category": category, "what": what, "fix": fix})

    worktree = Path(node["worktree"]) if node.get("worktree") else None
    if worktree is None or not worktree.is_dir():
        finding("baseline-worktree", "No baseline worktree is available.", "Run `baseline --prepare` first.")
    elif not config.get("target") or not config.get("benchmark"):
        finding("configuration", "The measurement system is not configured.", "Finish `explore configure`.")
    else:
        current = fingerprint(worktree)
        measurement = measurement_snapshot(config, worktree)["digest"]
        assessment.update(fingerprint=current, measurement_digest=measurement)
        paths = sorted(
            (path for path in (store.experiment_dir("exp_0000") / "checks").glob("*/check.json") if path.parent.name.isdigit()),
            key=lambda path: int(path.parent.name),
        )
        records = [(path, read_json(path, {})) for path in paths[-20:]]
        matching = [
            (path, record) for path, record in records
            if record.get("fingerprint") == current
            and (record.get("measurement") or {}).get("digest") == measurement
        ]
        assessment["matching_checks"] = len(matching)
        samples = matching[-required:]
        assessment["check_records"] = [
            path.relative_to(store.state_dir).as_posix() for path, _ in samples
        ]
        if not records:
            finding("unchecked-baseline", "No wiring checks have been recorded.", f"Run `run exp_0000 --check` {required} times.")
        elif not matching or matching[-1][0] != records[-1][0]:
            finding("stale-check", "The latest check does not match the current source and measurement settings.", "Recheck the current baseline; old passing results cannot approve changed files.")
        if len(samples) < required:
            finding("repeatability", f"Only {len(samples)} matching checks; {required} are required.", "Repeat the wiring check without changing source, fixtures, or configuration.")
        if any(record.get("status") != "check-passed" for _, record in samples):
            finding("failed-check", "A recent matching check failed.", "Repair the benchmark or gates and obtain consecutive passing checks.")
        successful = [
            record["benchmark"]["result"] for _, record in samples
            if record.get("status") == "check-passed"
            and (record.get("benchmark") or {}).get("result") is not None
        ]
        if successful:
            result = successful[-1]
            score = float(result["score"])
            tasks = result.get("tasks") or {}
            threshold = max(
                float(config.get("min_improvement", 0)),
                abs(score) * float(config.get("min_relative_improvement", 0)),
            )
            spread = max(item["score"] for item in successful) - min(item["score"] for item in successful)
            assessment.update(
                result=result, score=score, task_count=len(tasks),
                minimum_gain=threshold,
                observed_score_range=spread if len(successful) > 1 else None,
            )
            if not tasks:
                finding("task-coverage", "The benchmark has no explicit task scores.", "Emit a finite score and matching diagnostic trace for each benchmark unit.")
            elif len(tasks) < 10:
                finding("small-benchmark", f"Only {len(tasks)} benchmark tasks are represented.", "Add representative edge cases and counterexamples, or state why this small set is sufficient.", severity="warn")
            if any(set(other.get("tasks") or {}) != set(tasks) for other in successful):
                finding("task-coverage", "Repeated checks evaluate different task IDs.", "Keep the evaluated task set fixed even when scores are noisy.")
            if config.get("benchmark_determinism") == "deterministic":
                if any(not same_result(result, other) for other in successful):
                    finding("unstable-results", "Identical deterministic checks disagree on scores or task coverage.", "Remove nondeterminism or classify the benchmark as noisy and calibrate its gain floor.")
            elif spread > 1e-12 and spread + 1e-12 >= threshold:
                finding("noise-floor", f"Observed score range {spread:g} reaches the meaningful-gain floor {threshold:g}.", "Use more stable sampling or raise the gain floor above observed noise before freezing the benchmark.")
            ceiling = config.get("score_ceiling")
            if ceiling is not None:
                remaining = (float(ceiling) - score) * (-1 if config.get("metric") == "min" else 1)
                assessment["remaining_headroom"] = remaining
                if remaining <= 1e-12 or remaining + 1e-12 < threshold:
                    finding("insufficient-headroom", f"Remaining headroom {remaining:g} cannot support a meaningful gain of {threshold:g}.", "Choose a useful unsaturated dimension or construct harder representative cases before measuring the baseline.")

    selected = next(
        (item for item in discovery.get("dimensions", []) if item["name"] == discovery.get("selected_dimension")),
        None,
    )
    if selected and selected.get("direction") != config.get("metric"):
        finding("goal-direction", "The selected dimension and configured metric have different directions.", "Align the selected goal and benchmark direction.")
    if not config.get("final_test"):
        finding("no-final-test", "No untouched final-test command is configured.", "Configure a distinct final set, or report this run as development-only evidence.", severity="warn")
    assessment["passed"] = not any(item["severity"] == "block" for item in findings)
    assessment["status"] = "ready" if assessment["passed"] else "needs-work"
    if persist:
        store.mutate_discovery(lambda value: value.update(baseline_assessment=assessment))
        store.add_event("baseline_assessed", passed=assessment["passed"], matching_checks=assessment["matching_checks"])
    return assessment
