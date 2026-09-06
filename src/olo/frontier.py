from __future__ import annotations

import math
import random
from typing import Any

from .research import eligible_nodes


def _direction(metric: str) -> float:
    return 1.0 if metric == "max" else -1.0


def _directional_score(node: dict[str, Any], metric: str) -> float:
    score = node.get("score")
    if score is None:
        return float("-inf")
    return float(score) * _direction(metric)


def frontier_candidates(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return eligible_nodes(graph)


def _summary(node: dict[str, Any], rank: int, reason: str) -> dict[str, Any]:
    return {
        "id": node["id"],
        "score": node.get("score"),
        "hypothesis": node.get("hypothesis"),
        "rank": rank,
        "reason": reason,
    }


def _score_order(nodes: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    return sorted(
        nodes,
        key=lambda node: (-_directional_score(node, metric), node["id"]),
    )


def _pareto_order(
    nodes: list[dict[str, Any]],
    metric: str,
) -> list[tuple[dict[str, Any], str]]:
    if not nodes:
        return []
    task_sets = [set((node.get("tasks") or {}).keys()) for node in nodes]
    common_tasks = set.intersection(*task_sets) if task_sets and all(task_sets) else set()
    if not common_tasks:
        return [(node, "aggregate score") for node in _score_order(nodes, metric)]

    wins: dict[str, int] = {node["id"]: 0 for node in nodes}
    sign = _direction(metric)
    for task_id in sorted(common_tasks):
        values = {
            node["id"]: float(node["tasks"][task_id]) * sign
            for node in nodes
            if task_id in (node.get("tasks") or {})
        }
        if not values:
            continue
        best = max(values.values())
        for exp_id, value in values.items():
            if value == best:
                wins[exp_id] += 1

    ordered = sorted(
        nodes,
        key=lambda node: (
            -wins[node["id"]],
            -_directional_score(node, metric),
            node["id"],
        ),
    )
    return [
        (node, f"wins {wins[node['id']]} per-task frontier(s)")
        for node in ordered
    ]


def _weighted_order(
    nodes: list[dict[str, Any]],
    metric: str,
    temperature: float,
    seed: int,
) -> list[dict[str, Any]]:
    remaining = list(nodes)
    ordered: list[dict[str, Any]] = []
    rng = random.Random(seed)
    while remaining:
        directional = [_directional_score(node, metric) for node in remaining]
        maximum = max(directional)
        weights = [
            math.exp((score - maximum) / max(0.01, temperature))
            for score in directional
        ]
        selected = rng.choices(range(len(remaining)), weights=weights, k=1)[0]
        ordered.append(remaining.pop(selected))
    return ordered


def rank_frontier(
    graph: dict[str, Any],
    config: dict[str, Any],
    *,
    limit: int | None = None,
    strategy_override: str | None = None,
) -> dict[str, Any]:
    nodes = frontier_candidates(graph)
    metric = str(config.get("metric", "max"))
    strategy = dict(config.get("frontier_strategy") or {})
    kind = (strategy_override or strategy.get("kind") or "pareto-per-task").replace(
        "_", "-"
    )
    k = int(strategy.get("k", 3))
    reason_by_id: dict[str, str] = {}

    if kind == "argmax":
        ordered = _score_order(nodes, metric)
        k = 1
        reason_by_id = {node["id"]: "current aggregate best" for node in ordered}
    elif kind == "top-k":
        ordered = _score_order(nodes, metric)
        reason_by_id = {node["id"]: "top-k aggregate score" for node in ordered}
    elif kind == "epsilon-greedy":
        ordered = _score_order(nodes, metric)
        if ordered:
            epsilon = float(strategy.get("epsilon", 0.1))
            rng = random.Random(len(graph.get("nodes", {})))
            if rng.random() < epsilon:
                chosen = rng.choice(ordered)
                ordered.remove(chosen)
                ordered.insert(0, chosen)
                reason_by_id[chosen["id"]] = "epsilon exploration pick"
            for node in ordered:
                reason_by_id.setdefault(node["id"], "epsilon-greedy score order")
        k = 1
    elif kind == "softmax":
        ordered = _weighted_order(
            nodes,
            metric,
            float(strategy.get("temperature", 0.5)),
            len(graph.get("nodes", {})),
        )
        reason_by_id = {node["id"]: "softmax sample order" for node in ordered}
    elif kind == "pareto-per-task":
        pairs = _pareto_order(nodes, metric)
        ordered = [node for node, _ in pairs]
        reason_by_id = {node["id"]: reason for node, reason in pairs}
    else:
        raise ValueError(f"unknown frontier strategy: {kind}")

    take = limit if limit is not None else k
    selected = ordered[: max(0, take)]
    return {
        "strategy": {
            **strategy,
            "kind": kind,
        },
        "all_ids": [node["id"] for node in nodes],
        "picks": [
            _summary(node, rank, reason_by_id.get(node["id"], kind))
            for rank, node in enumerate(selected, 1)
        ],
    }
