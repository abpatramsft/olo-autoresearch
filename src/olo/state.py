from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import uuid
from typing import Any, Callable

from .utils import (
    FileLock,
    append_jsonl,
    atomic_write_json,
    atomic_write_text,
    read_json,
    read_jsonl,
    utc_now,
)


STATE_DIR = ".olo"
CONFIG_FILE = "config.json"
GRAPH_FILE = "graph.json"
META_FILE = "meta.json"
DISCOVERY_FILE = "discovery.json"
PROJECT_FILE = "project.md"


class StateStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state_dir = self.root / STATE_DIR
        self.config_path = self.state_dir / CONFIG_FILE
        self.graph_path = self.state_dir / GRAPH_FILE
        self.meta_path = self.state_dir / META_FILE
        self.discovery_path = self.state_dir / DISCOVERY_FILE
        self.project_path = self.state_dir / PROJECT_FILE
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
        goal: str | None = None,
        phase: str = "ready-for-baseline",
    ) -> None:
        if self.is_initialized():
            raise RuntimeError(f"Olo is already initialized at {self.state_dir}")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.experiments_dir.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        config = {
            "schema_version": 2,
            "review_required": True,
            "min_improvement": 0.01,
            "min_relative_improvement": 0.0,
            "critical_tasks": [],
            "max_task_regression": 0.0,
            "evaluation_version": "v1",
            "project_name": project_name,
            "repo_root": str(self.root),
            "phase": phase,
            "goal": goal,
            "target": target,
            "editable_paths": editable_paths or ([target] if target else []),
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
        discovery = {
            "status": "exploring" if phase == "exploring" else phase,
            "goal": goal,
            "repo_summary": None,
            "dimensions": [],
            "selected_dimension": None,
            "benchmark_plan": None,
            "created_at": now,
            "updated_at": now,
        }
        atomic_write_json(self.config_path, config)
        atomic_write_json(self.graph_path, graph)
        atomic_write_json(self.meta_path, meta)
        atomic_write_json(self.discovery_path, discovery)
        self.add_event("workspace_initialized", project_name=project_name, target=target)

    def initialize_exploration(
        self,
        *,
        project_name: str,
        goal: str | None,
        root_commit: str,
        timeout_seconds: int = 300,
        max_attempts: int = 3,
        stall_limit: int = 3,
    ) -> None:
        self.initialize(
            project_name=project_name,
            target="",
            benchmark="",
            metric="max",
            gates=[],
            root_commit=root_commit,
            editable_paths=[],
            protected_paths=[],
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            stall_limit=stall_limit,
            frontier_strategy={
                "kind": "pareto-per-task",
                "k": 3,
                "epsilon": 0.1,
                "temperature": 0.5,
            },
            score_ceiling=None,
            goal=goal,
            phase="exploring",
        )
        self.write_project(
            "\n".join(
                [
                    f"# {project_name}",
                    "",
                    "## Goal",
                    "",
                    goal or "To be selected during Olo exploration.",
                    "",
                    "## Discovery status",
                    "",
                    "Exploration has started. Benchmark and gates are not configured yet.",
                    "",
                ]
            )
        )

    def config(self) -> dict[str, Any]:
        self.require_initialized()
        config = read_json(self.config_path, {})
        if "phase" not in config:
            graph = read_json(self.graph_path, {"nodes": {}})
            baseline = graph.get("nodes", {}).get("exp_0000")
            config["phase"] = (
                "ready-to-optimize"
                if baseline and baseline.get("status") == "committed"
                else "ready-for-baseline"
            )
        return config

    def graph(self) -> dict[str, Any]:
        self.require_initialized()
        return read_json(self.graph_path, {"root": "root", "nodes": {}})

    def meta(self) -> dict[str, Any]:
        self.require_initialized()
        return read_json(self.meta_path, {})

    def discovery(self) -> dict[str, Any]:
        self.require_initialized()
        return read_json(
            self.discovery_path,
            {
                "status": self.config().get("phase", "configured"),
                "dimensions": [],
            },
        )

    def save_config(self, config: dict[str, Any]) -> None:
        with FileLock(self.lock_path):
            atomic_write_json(self.config_path, config)

    def save_discovery(self, discovery: dict[str, Any]) -> None:
        discovery["updated_at"] = utc_now()
        with FileLock(self.lock_path):
            atomic_write_json(self.discovery_path, discovery)

    def mutate_discovery(self, callback: Callable[[dict[str, Any]], Any]) -> Any:
        with FileLock(self.lock_path):
            discovery = read_json(
                self.discovery_path,
                {"status": "exploring", "dimensions": []},
            )
            result = callback(discovery)
            discovery["updated_at"] = utc_now()
            atomic_write_json(self.discovery_path, discovery)
            return result

    def add_dimension(
        self,
        *,
        name: str,
        description: str,
        target: str,
        metric_name: str,
        direction: str,
        evidence: str,
        complexity: str,
        run_cost: str,
    ) -> dict[str, Any]:
        def mutate(discovery: dict[str, Any]) -> dict[str, Any]:
            dimensions = discovery.setdefault("dimensions", [])
            if any(item.get("name") == name for item in dimensions):
                raise RuntimeError(f"discovery dimension already exists: {name}")
            entry = {
                "name": name,
                "description": description,
                "target": target,
                "metric_name": metric_name,
                "direction": direction,
                "evidence": evidence,
                "complexity": complexity,
                "run_cost": run_cost,
                "created_at": utc_now(),
            }
            dimensions.append(entry)
            discovery["status"] = "dimensions-proposed"
            return dict(entry)

        result = self.mutate_discovery(mutate)
        self.add_event("discovery_dimension_added", name=name, target=target)
        return result

    def select_dimension(self, name: str) -> dict[str, Any]:
        def mutate(discovery: dict[str, Any]) -> dict[str, Any]:
            selected = next(
                (
                    item
                    for item in discovery.get("dimensions", [])
                    if item.get("name") == name
                ),
                None,
            )
            if selected is None:
                raise RuntimeError(f"unknown discovery dimension: {name}")
            discovery["selected_dimension"] = name
            discovery["status"] = "dimension-selected"
            return dict(selected)

        result = self.mutate_discovery(mutate)
        config = self.config()
        config["goal"] = result.get("description") or config.get("goal")
        self.save_config(config)
        self.add_event("discovery_dimension_selected", name=name)
        return result

    def write_project(self, content: str) -> None:
        from .utils import atomic_write_text

        atomic_write_text(self.project_path, content.rstrip() + "\n")

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

    def reserve_experiment(
        self,
        parent_id: str,
        hypothesis: str,
        *,
        kind: str = "experiment",
        donors: list[str] | None = None,
        contributions: dict[str, str] | None = None,
        proposal_id: str | None = None,
    ) -> dict[str, Any]:
        with FileLock(self.lock_path):
            graph = read_json(self.graph_path, {"nodes": {}})
            meta = read_json(self.meta_path, {"next_id": 0})
            config = self.config()
            if config.get("phase") == "finalized":
                raise RuntimeError("this evaluation version is finalized")
            current_round = meta.get("current_round") or {}
            if current_round and sum(node.get("round_number") == current_round["number"] for node in graph["nodes"].values()) >= current_round["width"] * current_round["budget"]:
                raise RuntimeError("round allocation budget exhausted")
            best = self.best_score()
            ceiling = config.get("score_ceiling")
            if kind != "baseline" and best is not None and ceiling is not None and ((config.get("metric") == "min" and best <= ceiling) or (config.get("metric") != "min" and best >= ceiling)):
                raise RuntimeError("score ceiling reached; start a harder evaluation version")
            parent = graph.get("nodes", {}).get(parent_id)
            if parent is None:
                raise RuntimeError(f"unknown parent experiment: {parent_id}")
            from .research import eligible_nodes

            if parent_id != "root" and parent_id not in {item["id"] for item in eligible_nodes(graph)}:
                raise RuntimeError(
                    f"parent {parent_id} is {parent.get('status')}; branch from an approved node"
                )
            donor_ids = list(dict.fromkeys(donors or []))
            approved = {item["id"]: item for item in eligible_nodes(graph)}
            for donor_id in donor_ids:
                if donor_id == parent_id or donor_id not in approved:
                    raise RuntimeError("donors must be distinct approved experiments, different from the base")
                if approved[donor_id].get("evaluation_version") != parent.get("evaluation_version"):
                    raise RuntimeError("base and donors must use the same evaluation version")
                if not (contributions or {}).get(donor_id, "").strip():
                    raise ValueError(f"describe the contribution from donor {donor_id}")
            next_id = int(meta.get("next_id", 0))
            exp_id = f"exp_{next_id:04d}"
            if proposal_id:
                self.update_proposal(proposal_id, status="claimed", experiment_id=exp_id)
            meta["next_id"] = next_id + 1
            now = utc_now()
            worktree = self.worktrees_dir / exp_id
            node = {
                "id": exp_id,
                "kind": kind,
                "parent": parent_id,
                "operator": "recombine" if donor_ids else "baseline" if kind == "baseline" else "mutation",
                "donors": donor_ids,
                "donor_commits": {donor_id: approved[donor_id]["commit"] for donor_id in donor_ids},
                "contributions": contributions or {},
                "proposal_id": proposal_id,
                "round_number": (meta.get("current_round") or {}).get("number"),
                "evaluation_version": self.config().get("evaluation_version", "legacy"),
                "children": [],
                "status": "pending",
                "hypothesis": hypothesis.strip(),
                "score": None,
                "tasks": {},
                "commit": None,
                "branch": f"{config.get('branch_prefix', 'olo')}/{exp_id}",
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

    def consume_evaluation(self, *, baseline: bool = False) -> int:
        config = self.config()

        def mutate(meta: dict[str, Any]) -> int:
            count = int(meta.get("evaluation_count", 0))
            if config.get("max_evaluations") is not None and count >= int(config["max_evaluations"]):
                raise RuntimeError("evaluation budget exhausted")
            current = meta.get("current_round")
            if current and not baseline:
                used = int(current.get("evaluations", 0))
                if used >= int(current["width"]) * int(current["budget"]):
                    raise RuntimeError("round evaluation budget exhausted (probes and checks count)")
                current["evaluations"] = used + 1
            meta["evaluation_count"] = count + 1
            return count + 1

        return self.mutate_meta(mutate)

    def experiment_dir(self, exp_id: str) -> Path:
        return self.experiments_dir / exp_id

    def attempt_dir(self, exp_id: str, attempt: int) -> Path:
        return self.experiment_dir(exp_id) / "attempts" / f"{attempt:03d}"

    def traces_dir(self, exp_id: str, attempt: int) -> Path:
        return self.attempt_dir(exp_id, attempt) / "traces"

    def latest_outcome(self, exp_id: str) -> dict[str, Any] | None:
        node = self.graph().get("nodes", {}).get(exp_id)
        if node and node.get("latest_record"):
            return read_json(self.state_dir / node["latest_record"], None)
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
            "id": "note_" + uuid.uuid4().hex[:12],
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

    def add_learning(self, exp_id: str, *, text: str, tags: list[str], supersedes: list[str], kind: str = "hypothesis") -> dict[str, Any]:
        node = self.graph().get("nodes", {}).get(exp_id)
        if not node or not self.latest_outcome(exp_id):
            raise RuntimeError("a learning must reference an experiment with saved evidence")
        known = {item.get("id") for item in self.annotations()}
        if any(note_id not in known for note_id in supersedes):
            raise RuntimeError("unknown superseded learning")
        if not text.strip():
            raise ValueError("learning text cannot be empty")
        return self.add_annotation(exp_id, text=text.strip(), annotation_type="learning", payload={
            "kind": kind, "tags": sorted(set(tags)), "supersedes": supersedes,
            "evidence": node.get("latest_record") or str((self.attempt_dir(exp_id, int(node["attempts"])) / "outcome.json").relative_to(self.state_dir)),
        })

    def learning_context(self, *, query: str = "", parent: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
        annotations = self.annotations()
        superseded = {note_id for item in annotations for note_id in (item.get("payload") or {}).get("supersedes", [])}
        material = [item for item in annotations if item.get("type") not in {"verification", "review"} and item.get("id") not in superseded]
        terms = set(re.findall(r"[a-z0-9]{3,}", query.lower()))
        nodes = self.graph().get("nodes", {})
        lineage: set[str] = set()
        pending = [parent] if parent else []
        while pending:
            exp_id = pending.pop()
            if not exp_id or exp_id in lineage:
                continue
            lineage.add(exp_id)
            node = nodes.get(exp_id, {})
            pending.extend([node.get("parent"), *(node.get("donors") or [])])

        def relevance(item: dict[str, Any]) -> tuple:
            payload = item.get("payload") or {}
            words = set(re.findall(r"[a-z0-9]{3,}", (item.get("text", "") + " " + " ".join(payload.get("tags") or [])).lower()))
            return (len(terms & words) * 2 + (4 if item.get("experiment_id") in lineage else 0), item.get("timestamp", ""), item.get("id", ""))

        return sorted(material, key=relevance, reverse=True)[:max(0, limit)]

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
            "id": "idea_" + uuid.uuid4().hex[:12],
            "status": "proposed",
            "timestamp": utc_now(),
            "source": source,
            "title": title,
            "hypothesis": hypothesis,
            "rationale": rationale,
            "parent": parent,
            "confidence": confidence,
            "consumed": False,
        }
        with FileLock(self.proposals_path.with_suffix(".jsonl.lock")):
            proposals = self.proposals()
            signature = " ".join(hypothesis.lower().split())
            for existing in proposals:
                if existing.get("parent") == parent and " ".join(existing.get("hypothesis", "").lower().split()) == signature and existing.get("status", "proposed") not in {"rejected", "superseded"}:
                    return existing
            atomic_write_text(self.proposals_path, "".join(json.dumps(item, sort_keys=True) + "\n" for item in [*proposals, entry]))
        self.add_event("proposal_added", title=title, source=source)
        return entry

    def proposals(self) -> list[dict[str, Any]]:
        return read_jsonl(self.proposals_path)

    def update_proposal(self, proposal_id: str, *, status: str, experiment_id: str | None = None) -> dict[str, Any]:
        transitions = {
            "proposed": {"claimed", "rejected", "superseded"},
            "claimed": {"tested", "proposed", "rejected", "superseded"},
            "tested": {"superseded"}, "rejected": set(), "superseded": set(),
        }
        with FileLock(self.proposals_path.with_suffix(".jsonl.lock")):
            proposals = self.proposals()
            item = next((value for value in proposals if value.get("id") == proposal_id), None)
            if item is None:
                raise RuntimeError(f"unknown proposal: {proposal_id}")
            if status not in transitions.get(item.get("status", "proposed"), set()):
                raise RuntimeError(f"cannot move proposal from {item.get('status')} to {status}")
            item.update(status=status, consumed=status != "proposed", updated_at=utc_now())
            if experiment_id:
                item["experiment_id"] = experiment_id
            atomic_write_text(self.proposals_path, "".join(json.dumps(value, sort_keys=True) + "\n" for value in proposals))
            result = dict(item)
        self.add_event("proposal_updated", proposal_id=proposal_id, status=status, experiment_id=experiment_id)
        return result

    def add_event(self, event: str, **details: Any) -> None:
        append_jsonl(
            self.events_path,
            {"timestamp": utc_now(), "event": event, "details": details},
        )

    def events(self) -> list[dict[str, Any]]:
        return read_jsonl(self.events_path)

    def best_node(self) -> dict[str, Any] | None:
        from .research import eligible_nodes

        config = self.config()
        graph = self.graph()
        committed = [
            node
            for node in eligible_nodes(graph)
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
            "phase": config.get("phase", "configured"),
            "goal": config.get("goal"),
            "discovery_status": self.discovery().get("status"),
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

    def scratchpad(self, *, query: str = "", parent: str | None = None, limit: int = 12) -> str:
        status = self.status_summary()
        graph = self.graph()
        annotations = self.learning_context(query=query or str(status.get("goal") or ""), parent=parent, limit=limit)
        proposals = [item for item in self.proposals() if item.get("status", "proposed") == "proposed"][-10:]
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
                f"- phase={status['phase']} metric={status['metric']} best={status['best_score']} "
                f"experiment={status['best_experiment']} total={status['experiments']}"
            ),
            f"- counts={status['counts']}",
            f"- mode={status['mode']}",
            "",
            "## Experiment Tree",
            "```text",
            *self.tree_lines()[:80],
            "```",
        ]
        discovery = self.discovery()
        if status["phase"] != "ready-to-optimize":
            lines.extend(
                [
                    "",
                    "## Discovery",
                    f"- status={discovery.get('status')}",
                    f"- goal={discovery.get('goal') or status.get('goal')}",
                    f"- selected={discovery.get('selected_dimension')}",
                ]
            )
            for dimension in discovery.get("dimensions", []):
                lines.append(
                    f"- {dimension.get('name')}: {dimension.get('description')} "
                    f"(target={dimension.get('target')}, "
                    f"metric={dimension.get('metric_name')} {dimension.get('direction')})"
                )
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
            lines.extend(["", "## Relevant Learnings"])
            for item in annotations:
                task = f" task={item['task_id']}" if item.get("task_id") else ""
                payload = item.get("payload") or {}
                category = payload.get("kind") or ("observation" if item.get("type") == "observation" else "interpretation")
                lines.append(
                    f"- [{category}] {item.get('experiment_id')}{task}: {item.get('text')}"
                )
                if payload.get("evidence"):
                    lines.append(f"  Evidence: {payload['evidence']}")
        return "\n".join(lines) + "\n"

    def mode_start(self, *, autonomous: bool, stall_limit: int | None) -> dict[str, Any]:
        if self.config().get("phase") == "finalized":
            raise RuntimeError("this evaluation version is finalized")
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
        from .reporting import save_report

        save_report(self)
        return result

    def round_start(self, *, width: int, budget: int) -> dict[str, Any]:
        if width < 1 or budget < 1:
            raise ValueError("round width and budget must be positive")
        if self.config().get("phase") == "finalized":
            raise RuntimeError("this evaluation version is finalized")
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
        if any(node.get("status") in {"active", "pending-review"} for node in self.graph()["nodes"].values()):
            raise RuntimeError("complete active evaluations and reviews before closing the round")
        best_after = self.best_score()
        metric = self.config().get("metric", "max")
        ceiling = self.config().get("score_ceiling")

        def mutate(meta: dict[str, Any]) -> dict[str, Any]:
            current = meta.get("current_round")
            if not current:
                raise RuntimeError("no Olo round is open")
            before = current.get("best_before")
            threshold = max(float(self.config().get("min_improvement", 0)), abs(float(before or 0)) * float(self.config().get("min_relative_improvement", 0)))
            improved = (
                best_after is not None
                and (
                    before is None
                    or (metric == "max" and best_after > before and best_after - before + 1e-12 >= threshold)
                    or (metric == "min" and best_after < before and before - best_after + 1e-12 >= threshold)
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
        from .reporting import save_report

        save_report(self)
        return result

    def update_dashboard(self, *, pid: int | None, port: int | None) -> None:
        def mutate(meta: dict[str, Any]) -> None:
            meta["dashboard"] = {"pid": pid, "port": port}

        self.mutate_meta(mutate)
