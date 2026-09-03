from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .utils import (
    FileLock,
    append_jsonl,
    atomic_write_json,
    read_json,
    read_jsonl,
    utc_now,
)


STATE_DIR = ".olo"
CONFIG_FILE = "config.json"
GRAPH_FILE = "graph.json"
META_FILE = "meta.json"


class StateStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state_dir = self.root / STATE_DIR
        self.config_path = self.state_dir / CONFIG_FILE
        self.graph_path = self.state_dir / GRAPH_FILE
        self.meta_path = self.state_dir / META_FILE
        self.lock_path = self.state_dir / "state.lock"
        self.annotations_path = self.state_dir / "annotations.jsonl"
        self.proposals_path = self.state_dir / "proposals.jsonl"
        self.events_path = self.state_dir / "events.jsonl"
        self.worktrees_dir = self.state_dir / "worktrees"
        self.experiments_dir = self.state_dir / "experiments"

    def is_initialized(self) -> bool:
        return self.config_path.exists() and self.graph_path.exists()

    def require_initialized(self) -> None:
        if not self.is_initialized():
            raise RuntimeError(
                f"{self.root} is not initialized for Olo; run `python olo.py init ...`"
            )

    def initialize(
        self,
        *,
        project_name: str,
        target: str,
        benchmark: str,
        metric: str,
        gates: list[dict[str, str]],
        root_commit: str,
        editable_paths: list[str],
        protected_paths: list[str],
        timeout_seconds: int,
        max_attempts: int,
        stall_limit: int,
        frontier_strategy: dict[str, Any],
        score_ceiling: float | None,
    ) -> None:
        if self.is_initialized():
            raise RuntimeError(f"Olo is already initialized at {self.state_dir}")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.experiments_dir.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        config = {
            "schema_version": 1,
            "project_name": project_name,
            "repo_root": str(self.root),
            "target": target,
            "editable_paths": editable_paths or [target],
            "protected_paths": protected_paths,
            "benchmark": benchmark,
            "gates": gates,
            "metric": metric,
            "timeout_seconds": timeout_seconds,
            "max_attempts": max_attempts,
            "stall_limit": stall_limit,
            "frontier_strategy": frontier_strategy,
            "score_ceiling": score_ceiling,
            "created_at": now,
        }
        graph = {
            "root": "root",
            "nodes": {
                "root": {
                    "id": "root",
                    "parent": None,
                    "children": [],
                    "status": "root",
                    "hypothesis": "repository baseline root",
                    "score": None,
                    "tasks": {},
                    "commit": root_commit,
                    "branch": None,
                    "worktree": None,
                    "attempts": 0,
                    "created_at": now,
                    "updated_at": now,
                }
            },
        }
        meta = {
            "next_id": 0,
            "mode": {
                "active": False,
                "autonomous": False,
                "stall_count": 0,
                "stall_limit": stall_limit,
                "continuation_count": 0,
                "status": "idle",
                "started_at": None,
                "stopped_at": None,
            },
            "current_round": None,
            "round_history": [],
            "dashboard": {"pid": None, "port": None},
            "created_at": now,
        }
        atomic_write_json(self.config_path, config)
        atomic_write_json(self.graph_path, graph)
        atomic_write_json(self.meta_path, meta)
        self.add_event("workspace_initialized", project_name=project_name, target=target)

    def config(self) -> dict[str, Any]:
        self.require_initialized()
        return read_json(self.config_path, {})

    def graph(self) -> dict[str, Any]:
        self.require_initialized()
        return read_json(self.graph_path, {"root": "root", "nodes": {}})

    def meta(self) -> dict[str, Any]:
        self.require_initialized()
        return read_json(self.meta_path, {})

    def save_config(self, config: dict[str, Any]) -> None:
        with FileLock(self.lock_path):
            atomic_write_json(self.config_path, config)

    def update_node(self, exp_id: str, **changes: Any) -> dict[str, Any]:
        with FileLock(self.lock_path):
            graph = read_json(self.graph_path, {"nodes": {}})
            node = graph.get("nodes", {}).get(exp_id)
            if node is None:
                raise RuntimeError(f"unknown experiment: {exp_id}")
            node.update(changes)
            node["updated_at"] = utc_now()
            atomic_write_json(self.graph_path, graph)
            return dict(node)

    def mutate_meta(self, callback: Callable[[dict[str, Any]], Any]) -> Any:
        with FileLock(self.lock_path):
            meta = read_json(self.meta_path, {})
            result = callback(meta)
            atomic_write_json(self.meta_path, meta)
            return result

    def reserve_experiment(self, parent_id: str, hypothesis: str) -> dict[str, Any]:
        with FileLock(self.lock_path):
            graph = read_json(self.graph_path, {"nodes": {}})
            meta = read_json(self.meta_path, {"next_id": 0})
            parent = graph.get("nodes", {}).get(parent_id)
            if parent is None:
                raise RuntimeError(f"unknown parent experiment: {parent_id}")
            if parent_id != "root" and parent.get("status") != "committed":
                raise RuntimeError(
                    f"parent {parent_id} is {parent.get('status')}; branch from a committed node"
                )
            next_id = int(meta.get("next_id", 0))
            exp_id = f"exp_{next_id:04d}"
            meta["next_id"] = next_id + 1
            now = utc_now()
            worktree = self.worktrees_dir / exp_id
            node = {
                "id": exp_id,
                "parent": parent_id,
                "children": [],
                "status": "pending",
                "hypothesis": hypothesis.strip(),
                "score": None,
                "tasks": {},
                "commit": None,
                "branch": f"olo/{exp_id}",
                "worktree": str(worktree),
                "attempts": 0,
                "gate_results": [],
                "created_at": now,
                "updated_at": now,
            }
            graph["nodes"][exp_id] = node
            parent.setdefault("children", []).append(exp_id)
            parent["updated_at"] = now
            atomic_write_json(self.graph_path, graph)
            atomic_write_json(self.meta_path, meta)
            return dict(node)

    def experiment_dir(self, exp_id: str) -> Path:
        return self.experiments_dir / exp_id

    def attempt_dir(self, exp_id: str, attempt: int) -> Path:
        return self.experiment_dir(exp_id) / "attempts" / f"{attempt:03d}"

    def traces_dir(self, exp_id: str, attempt: int) -> Path:
        return self.attempt_dir(exp_id, attempt) / "traces"

    def latest_outcome(self, exp_id: str) -> dict[str, Any] | None:
        node = self.graph().get("nodes", {}).get(exp_id)
        if not node or not node.get("attempts"):
            return None
        path = self.attempt_dir(exp_id, int(node["attempts"])) / "outcome.json"
        return read_json(path, None)

    def add_annotation(
        self,
        exp_id: str,
        *,
        text: str,
        annotation_type: str = "note",
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "timestamp": utc_now(),
            "experiment_id": exp_id,
            "task_id": task_id,
            "type": annotation_type,
            "text": text,
            "payload": payload or {},
        }
        append_jsonl(self.annotations_path, entry)
        return entry

    def annotations(self) -> list[dict[str, Any]]:
        return read_jsonl(self.annotations_path)

    def add_proposal(
        self,
        *,
        source: str,
        title: str,
        hypothesis: str,
        rationale: str,
        parent: str | None,
        confidence: str,
    ) -> dict[str, Any]:
        entry = {
            "timestamp": utc_now(),
            "source": source,
            "title": title,
            "hypothesis": hypothesis,
            "rationale": rationale,
            "parent": parent,
            "confidence": confidence,
            "consumed": False,
        }
        append_jsonl(self.proposals_path, entry)
        self.add_event("proposal_added", title=title, source=source)
        return entry

    def proposals(self) -> list[dict[str, Any]]:
        return read_jsonl(self.proposals_path)

    def add_event(self, event: str, **details: Any) -> None:
        append_jsonl(
            self.events_path,
            {"timestamp": utc_now(), "event": event, "details": details},
        )

    def events(self) -> list[dict[str, Any]]:
        return read_jsonl(self.events_path)

    def best_node(self) -> dict[str, Any] | None:
        config = self.config()
        graph = self.graph()
        committed = [
            node
            for node in graph["nodes"].values()
            if node.get("status") == "committed" and node.get("score") is not None
        ]
        if not committed:
            return None
        reverse = config.get("metric", "max") == "max"
        return sorted(
            committed,
            key=lambda node: (float(node["score"]), node["id"]),
            reverse=reverse,
        )[0]

    def best_score(self) -> float | None:
        best = self.best_node()
        return None if best is None else float(best["score"])

    def status_summary(self) -> dict[str, Any]:
        graph = self.graph()
        config = self.config()
        meta = self.meta()
        nodes = [n for key, n in graph["nodes"].items() if key != "root"]
        counts = Counter(str(node.get("status", "unknown")) for node in nodes)
        best = self.best_node()
        dashboard = meta.get("dashboard") or {}
        port = dashboard.get("port")
        return {
            "project_name": config.get("project_name"),
            "target": config.get("target"),
            "metric": config.get("metric"),
            "best_score": None if best is None else best.get("score"),
            "best_experiment": None if best is None else best.get("id"),
            "experiments": len(nodes),
            "counts": dict(sorted(counts.items())),
            "mode": meta.get("mode") or {},
            "current_round": meta.get("current_round"),
            "dashboard_url": f"http://127.0.0.1:{port}" if port else None,
        }

    def tree_lines(self) -> list[str]:
        graph = self.graph()
        best = self.best_node()
        best_path: set[str] = set()
        cursor = best
        while cursor and cursor.get("id") != "root":
            best_path.add(cursor["id"])
            cursor = graph["nodes"].get(cursor.get("parent"))

        lines: list[str] = []

        def walk(node_id: str, depth: int) -> None:
            node = graph["nodes"][node_id]
            marker = "*" if node_id in best_path else " "
            score = "-" if node.get("score") is None else f"{node['score']:.4f}"
            label = (
                f"{marker} {node_id} [{node.get('status')}] score={score}"
                if node_id != "root"
                else "  root"
            )
            if node_id != "root":
                label += f" - {node.get('hypothesis', '')}"
            lines.append("  " * depth + label)
            for child_id in sorted(node.get("children", [])):
                if child_id in graph["nodes"]:
                    walk(child_id, depth + 1)

        walk("root", 0)
        return lines

    def scratchpad(self) -> str:
        status = self.status_summary()
        graph = self.graph()
        annotations = self.annotations()[-12:]
        proposals = self.proposals()[-10:]
        discarded = [
            node
            for node in graph["nodes"].values()
            if node.get("status") in {"discarded", "evaluated", "failed"}
        ]
        lines = [
            "# Olo Scratchpad",
            "",
            "## Status",
            (
                f"- metric={status['metric']} best={status['best_score']} "
                f"experiment={status['best_experiment']} total={status['experiments']}"
            ),
            f"- counts={status['counts']}",
            f"- mode={status['mode']}",
            "",
            "## Experiment Tree",
            "```text",
            *self.tree_lines(),
            "```",
        ]
        if discarded:
            lines.extend(["", "## What Not To Repeat"])
            for node in discarded[-10:]:
                reason = node.get("discard_reason") or node.get("error") or node.get("status")
                lines.append(f"- {node['id']}: {node.get('hypothesis')} ({reason})")
        if proposals:
            lines.extend(["", "## Proposals"])
            for item in proposals:
                lines.append(
                    f"- [{item.get('source')}] {item.get('title')}: "
                    f"{item.get('hypothesis')}"
                )
        if annotations:
            lines.extend(["", "## Recent Learnings"])
            for item in annotations:
                task = f" task={item['task_id']}" if item.get("task_id") else ""
                lines.append(
                    f"- {item.get('experiment_id')}{task}: {item.get('text')}"
                )
        return "\n".join(lines) + "\n"

    def mode_start(self, *, autonomous: bool, stall_limit: int | None) -> dict[str, Any]:
        def mutate(meta: dict[str, Any]) -> dict[str, Any]:
            mode = meta.setdefault("mode", {})
            mode.update(
                {
                    "active": True,
                    "autonomous": autonomous,
                    "stall_count": 0,
                    "stall_limit": int(stall_limit or self.config().get("stall_limit", 3)),
                    "continuation_count": 0,
                    "status": "running",
                    "started_at": utc_now(),
                    "stopped_at": None,
                }
            )
            return dict(mode)

        result = self.mutate_meta(mutate)
        self.add_event("mode_started", autonomous=autonomous, stall_limit=result["stall_limit"])
        return result

    def mode_stop(self, reason: str = "manual") -> dict[str, Any]:
        def mutate(meta: dict[str, Any]) -> dict[str, Any]:
            mode = meta.setdefault("mode", {})
            mode.update(
                {
                    "active": False,
                    "status": reason,
                    "stopped_at": utc_now(),
                }
            )
            meta["current_round"] = None
            return dict(mode)

        result = self.mutate_meta(mutate)
        self.add_event("mode_stopped", reason=reason)
        return result

    def round_start(self, *, width: int, budget: int) -> dict[str, Any]:
        best_before = self.best_score()

        def mutate(meta: dict[str, Any]) -> dict[str, Any]:
            if meta.get("current_round"):
                raise RuntimeError("an Olo round is already open")
            history = meta.setdefault("round_history", [])
            round_number = len(history) + 1
            current = {
                "number": round_number,
                "width": int(width),
                "budget": int(budget),
                "best_before": best_before,
                "started_at": utc_now(),
            }
            meta["current_round"] = current
            return dict(current)

        result = self.mutate_meta(mutate)
        self.add_event("round_started", **result)
        return result

    def round_close(self) -> dict[str, Any]:
        best_after = self.best_score()
        metric = self.config().get("metric", "max")
        ceiling = self.config().get("score_ceiling")

        def mutate(meta: dict[str, Any]) -> dict[str, Any]:
            current = meta.get("current_round")
            if not current:
                raise RuntimeError("no Olo round is open")
            before = current.get("best_before")
            improved = (
                best_after is not None
                and (
                    before is None
                    or (metric == "max" and best_after > before)
                    or (metric == "min" and best_after < before)
                )
            )
            mode = meta.setdefault("mode", {})
            mode["stall_count"] = 0 if improved else int(mode.get("stall_count", 0)) + 1
            mode["continuation_count"] = 0
            reached_ceiling = (
                ceiling is not None
                and best_after is not None
                and (
                    (metric == "max" and best_after >= float(ceiling))
                    or (metric == "min" and best_after <= float(ceiling))
                )
            )
            reached_stall = int(mode.get("stall_count", 0)) >= int(
                mode.get("stall_limit", 3)
            )
            closed = {
                **current,
                "best_after": best_after,
                "improved": improved,
                "closed_at": utc_now(),
                "stall_count": mode.get("stall_count"),
                "reached_ceiling": reached_ceiling,
                "reached_stall": reached_stall,
            }
            meta.setdefault("round_history", []).append(closed)
            meta["round_history"] = meta["round_history"][-100:]
            meta["current_round"] = None
            if reached_ceiling or reached_stall:
                mode["active"] = False
                mode["status"] = "ceiling" if reached_ceiling else "stalled"
                mode["stopped_at"] = utc_now()
            return closed

        result = self.mutate_meta(mutate)
        self.add_event("round_closed", **result)
        return result

    def update_dashboard(self, *, pid: int | None, port: int | None) -> None:
        def mutate(meta: dict[str, Any]) -> None:
            meta["dashboard"] = {"pid": pid, "port": port}

        self.mutate_meta(mutate)
