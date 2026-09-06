from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .gitops import git
from .utils import atomic_write_json, generated_artifact, read_json, utc_now


def eligible_nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = graph.get("nodes", {})

    def eligible(exp_id: str, visiting: frozenset[str] = frozenset()) -> bool:
        if exp_id == "root":
            return True
        node = nodes.get(exp_id, {})
        if exp_id in visiting or node.get("status") not in {"committed", "retained"}:
            return False
        if node.get("review_status", "approved") != "approved" or node.get("retired"):
            return False
        dependencies = [node.get("parent"), *(node.get("donors") or [])]
        return all(eligible(parent, visiting | {exp_id}) for parent in dependencies if parent)

    return [node for exp_id, node in nodes.items() if exp_id != "root" and eligible(exp_id)]


def fingerprint(worktree: Path) -> str:
    paths = git(worktree, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    digest = hashlib.sha256()
    for name in sorted(set(paths.split("\0")) - {""}):
        if generated_artifact(name):
            continue
        path = worktree / name
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def task_changes(parent: dict[str, Any], tasks: dict[str, float], metric: str) -> dict[str, Any]:
    previous = parent.get("tasks") or {}
    sign = -1 if metric == "min" else 1
    deltas = {
        task: round(sign * (float(tasks[task]) - float(previous[task])), 10)
        for task in sorted(set(previous) & set(tasks))
    }
    return {
        "improved": [task for task, delta in deltas.items() if delta > 1e-12],
        "regressed": [task for task, delta in deltas.items() if delta < -1e-12],
        "unchanged": [task for task, delta in deltas.items() if abs(delta) <= 1e-12],
        "missing": sorted(set(previous) - set(tasks)),
        "added": sorted(set(tasks) - set(previous)),
        "deltas": deltas,
    }


def decide(config: dict[str, Any], parent: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    metric = config.get("metric", "max")
    changes = task_changes(parent, result.get("tasks") or {}, metric)
    before = parent.get("score")
    gain = None if before is None else (float(result["score"]) - float(before)) * (-1 if metric == "min" else 1)
    threshold = max(
        float(config.get("min_improvement", 0)),
        abs(float(before or 0)) * float(config.get("min_relative_improvement", 0)),
    )
    critical = set(config.get("critical_tasks") or [])
    blocked = [
        task for task in changes["regressed"]
        if task in critical and -changes["deltas"][task] > float(config.get("max_task_regression", 0)) + 1e-12
    ]
    expected = set(config.get("expected_tasks") or [])
    coverage_ok = not expected or expected == set(result.get("tasks") or {})
    meaningful = gain is None or (gain > 1e-12 and gain + 1e-12 >= threshold)
    return {
        "meaningful": meaningful and coverage_ok and not blocked,
        "gain": gain,
        "minimum_gain": threshold,
        "critical_regressions": blocked,
        "task_coverage_valid": coverage_ok,
        "task_changes": changes,
    }


def trace_facts(directory: Path) -> list[dict[str, Any]]:
    facts = []
    for path in sorted(directory.glob("*.json")):
        trace = read_json(path, {})
        for event in trace.get("events") or []:
            attributes = event.get("attributes") or {}
            if "gold" not in attributes or "ranked_top" not in attributes:
                continue
            gold = set(attributes["gold"])
            found = gold & set(attributes["ranked_top"])
            facts.append({
                "task_id": trace.get("task_id", path.stem),
                "coverage": "complete" if found == gold else "partial" if found else "missing",
                "found": len(found), "expected": len(gold),
                "missing": sorted(gold - found),
                "reported_status": trace.get("status"),
                "evidence": path.name,
            })
    return facts


def review_experiment(store, exp_id: str, *, verdict: str, reviewer: str, reason: str) -> dict[str, Any]:
    from .measurement import measurement_snapshot

    graph = store.graph()
    node = graph.get("nodes", {}).get(exp_id)
    if not node or node.get("status") != "pending-review":
        raise RuntimeError("only a measured pending-review snapshot can be reviewed")
    outcome = store.latest_outcome(exp_id)
    if not outcome or not node.get("commit"):
        raise RuntimeError("the measured snapshot is missing")
    worktree = Path(node["worktree"])
    if fingerprint(worktree) != outcome.get("fingerprint") or git(worktree, "rev-parse", "HEAD").stdout.strip() != node["commit"]:
        raise RuntimeError("candidate changed after measurement; create and measure a new experiment")
    if measurement_snapshot(store.config(), worktree)["digest"] != (outcome.get("measurement") or {}).get("digest"):
        raise RuntimeError("measurement configuration changed after evaluation")
    approved_ids = {item["id"] for item in eligible_nodes(graph)} | {"root"}
    if any(parent not in approved_ids for parent in [node["parent"], *(node.get("donors") or [])]):
        raise RuntimeError("a parent or donor is no longer approved")
    if verdict == "approve" and (not outcome.get("gates_passed") or not outcome.get("verification", {}).get("passed")):
        raise RuntimeError("cannot approve a candidate with failed gates or verification")
    decision = outcome.get("decision") or {}
    if verdict == "approve" and (decision.get("critical_regressions") or not decision.get("task_coverage_valid", True)):
        raise RuntimeError("cannot approve a critical regression or incomplete task coverage")
    status = "invalid" if verdict == "reject" else "committed" if decision.get("meaningful") else "retained"
    review = {
        "verdict": verdict, "reviewer": reviewer, "reason": reason,
        "fingerprint": outcome["fingerprint"], "commit": node["commit"], "created_at": utc_now(),
    }
    outcome.update(status=status, review=review)
    atomic_write_json(store.state_dir / node["latest_record"], outcome)
    updated = store.update_node(exp_id, status=status, review_status="rejected" if verdict == "reject" else "approved", review=review)
    store.add_annotation(exp_id, text=reason, annotation_type="review", payload=review)
    store.add_event("experiment_reviewed", experiment_id=exp_id, **review)
    if node.get("proposal_id"):
        store.update_proposal(node["proposal_id"], status="tested", experiment_id=exp_id)
    if node.get("kind") == "baseline" and verdict == "approve":
        config = store.config()
        config.update(phase="ready-to-optimize", expected_tasks=sorted(node.get("tasks") or {}))
        store.save_config(config)
        store.mutate_discovery(lambda discovery: discovery.update(
            status="ready-to-optimize", baseline_experiment=exp_id, baseline_score=node.get("score"),
        ))
    return updated


def invalidate_experiment(store, exp_id: str, *, reviewer: str, reason: str) -> dict[str, Any]:
    from .reporting import save_report
    from .utils import FileLock

    if not reviewer.strip() or not reason.strip():
        raise ValueError("reviewer and reason are required")
    with FileLock(store.lock_path):
        graph = store.graph()
        if exp_id == "root" or exp_id not in graph["nodes"]:
            raise RuntimeError("choose an experiment to invalidate")
        affected = {exp_id}
        while True:
            dependents = {node["id"] for node in graph["nodes"].values() if set([node.get("parent"), *(node.get("donors") or [])]) & affected}
            if dependents <= affected:
                break
            affected.update(dependents)
        review = {"reviewer": reviewer, "reason": reason, "verdict": "invalidate", "created_at": utc_now(), "source": exp_id}
        for affected_id in affected:
            node = graph["nodes"][affected_id]
            node.update(previous_status=node.get("status"), status="invalid", review_status="invalidated", invalidation=review)
        atomic_write_json(store.graph_path, graph)
    store.add_annotation(exp_id, text=reason, annotation_type="review", payload=review)
    store.add_event("experiment_invalidated", experiment_id=exp_id, affected=sorted(affected), **review)
    save_report(store)
    return {"experiment_id": exp_id, "affected": sorted(affected), "review": review}