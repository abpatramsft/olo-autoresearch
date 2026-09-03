from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from olo.frontier import rank_frontier


class FrontierTests(unittest.TestCase):
    def test_pareto_keeps_task_specialists(self) -> None:
        graph = {
            "nodes": {
                "root": {"id": "root", "children": ["exp_0000"]},
                "exp_0000": {
                    "id": "exp_0000",
                    "status": "committed",
                    "score": 0.5,
                    "tasks": {"a": 1.0, "b": 0.0},
                    "children": ["exp_0001", "exp_0002"],
                },
                "exp_0001": {
                    "id": "exp_0001",
                    "status": "committed",
                    "score": 0.75,
                    "tasks": {"a": 1.0, "b": 0.5},
                    "children": [],
                    "hypothesis": "specialist a",
                },
                "exp_0002": {
                    "id": "exp_0002",
                    "status": "committed",
                    "score": 0.75,
                    "tasks": {"a": 0.5, "b": 1.0},
                    "children": [],
                    "hypothesis": "specialist b",
                },
            }
        }
        result = rank_frontier(
            graph,
            {
                "metric": "max",
                "frontier_strategy": {"kind": "pareto-per-task", "k": 2},
            },
        )
        self.assertEqual(
            {item["id"] for item in result["picks"]},
            {"exp_0001", "exp_0002"},
        )

    def test_argmax_honors_min_metric(self) -> None:
        graph = {
            "nodes": {
                "root": {"id": "root", "children": []},
                "exp_0001": {
                    "id": "exp_0001",
                    "status": "committed",
                    "score": 10,
                    "tasks": {},
                    "children": [],
                },
                "exp_0002": {
                    "id": "exp_0002",
                    "status": "committed",
                    "score": 5,
                    "tasks": {},
                    "children": [],
                },
            }
        }
        result = rank_frontier(
            graph,
            {"metric": "min", "frontier_strategy": {"kind": "argmax"}},
        )
        self.assertEqual(result["picks"][0]["id"], "exp_0002")


if __name__ == "__main__":
    unittest.main()
