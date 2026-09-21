from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from olo.discovery import assess_baseline
from olo.measurement import measurement_snapshot
from olo.state import StateStore
from olo.utils import atomic_write_json


class DiscoveryAssessmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name))
        self.store.initialize_exploration(
            project_name="fixture", goal="Improve correctness", root_commit="root-commit"
        )
        worktree = self.store.worktrees_dir / "exp_0000"
        worktree.mkdir()
        (worktree / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
        graph = self.store.graph()
        graph["nodes"]["exp_0000"] = {
            "id": "exp_0000", "kind": "baseline", "status": "pending",
            "worktree": str(worktree), "attempts": 0,
        }
        atomic_write_json(self.store.graph_path, graph)
        config = self.store.config()
        config.update(
            target="target.py", benchmark="benchmark", metric="max",
            benchmark_determinism="deterministic", score_ceiling=1.0,
        )
        self.store.save_config(config)
        self.fingerprint = patch("olo.discovery.fingerprint", return_value="source-v1").start()
        self.measurement = patch("olo.discovery.measurement_snapshot", return_value={"digest": "harness-v1"}).start()
        self.addCleanup(patch.stopall)

    def check(
        self, number: int, *, score: float = 0.5, tasks: dict | None = None,
        status: str = "check-passed", fingerprint: str = "source-v1",
    ) -> None:
        result = {"score": score, "tasks": {"one": score} if tasks is None else tasks}
        atomic_write_json(
            self.store.experiment_dir("exp_0000") / "checks" / f"{number:03d}" / "check.json",
            {
                "status": status, "score": score, "benchmark": {"result": result},
                "fingerprint": fingerprint, "measurement": {"digest": "harness-v1"},
            },
        )

    def categories(self, assessment: dict) -> set[str]:
        return {item["category"] for item in assessment["findings"] if item["severity"] == "block"}

    def test_checks_are_required_and_assessment_is_persisted(self) -> None:
        self.assertIn("unchecked-baseline", self.categories(assess_baseline(self.store)))
        self.check(1)
        self.assertIn("repeatability", self.categories(assess_baseline(self.store)))
        self.check(2)
        result = assess_baseline(self.store, persist=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["remaining_headroom"], 0.5)
        self.assertEqual(result, self.store.discovery()["baseline_assessment"])
        self.assertEqual(self.store.graph()["nodes"]["exp_0000"]["attempts"], 0)

    def test_source_and_measurement_changes_invalidate_passing_checks(self) -> None:
        self.check(1)
        self.check(2)
        self.fingerprint.return_value = "source-v2"
        self.assertIn("stale-check", self.categories(assess_baseline(self.store)))
        self.fingerprint.return_value = "source-v1"
        self.measurement.return_value = {"digest": "harness-v2"}
        self.assertIn("stale-check", self.categories(assess_baseline(self.store)))

    def test_latest_failed_or_stale_check_cannot_be_hidden_by_old_passes(self) -> None:
        self.check(1)
        self.check(2)
        self.check(3, status="check-failed")
        self.assertIn("failed-check", self.categories(assess_baseline(self.store)))
        self.check(4, fingerprint="other-source")
        self.assertIn("stale-check", self.categories(assess_baseline(self.store)))

    def test_repaired_baseline_can_replace_old_failures(self) -> None:
        self.check(1, status="check-failed")
        self.check(2)
        self.check(3)
        self.assertTrue(assess_baseline(self.store)["passed"])

    def test_check_order_is_numeric_after_check_999(self) -> None:
        self.check(998)
        self.check(999)
        self.check(1000, status="check-failed")
        self.assertIn("failed-check", self.categories(assess_baseline(self.store)))

    def test_identical_aggregate_does_not_hide_unstable_task_scores(self) -> None:
        self.check(1, tasks={"one": 0, "two": 1})
        self.check(2, tasks={"one": 1, "two": 0})
        self.assertIn("unstable-results", self.categories(assess_baseline(self.store)))

    def test_noisy_scores_need_three_samples_and_a_gain_above_observed_noise(self) -> None:
        config = self.store.config()
        config["benchmark_determinism"] = "noisy"
        self.store.save_config(config)
        self.check(1, score=0.50)
        self.check(2, score=0.54)
        self.assertIn("repeatability", self.categories(assess_baseline(self.store)))
        self.check(3, score=0.52)
        self.assertIn("noise-floor", self.categories(assess_baseline(self.store)))
        config["min_improvement"] = 0.1
        self.store.save_config(config)
        self.assertTrue(assess_baseline(self.store)["passed"])

    def test_noisy_checks_still_require_a_fixed_task_set(self) -> None:
        config = self.store.config()
        config["benchmark_determinism"] = "noisy"
        self.store.save_config(config)
        self.check(1)
        self.check(2)
        self.check(3, tasks={"different-task": 0.5})
        self.assertIn("task-coverage", self.categories(assess_baseline(self.store)))

    def test_maximum_and_minimum_metrics_require_useful_headroom(self) -> None:
        for metric, score, ceiling in (("max", 1.0, 1.0), ("max", 0.995, 1.0), ("min", 0.005, 0.0)):
            with self.subTest(metric=metric, score=score):
                config = self.store.config()
                config.update(metric=metric, score_ceiling=ceiling)
                self.store.save_config(config)
                self.check(1, score=score)
                self.check(2, score=score)
                self.assertIn("insufficient-headroom", self.categories(assess_baseline(self.store)))

    def test_relative_gain_floor_is_included_in_headroom(self) -> None:
        config = self.store.config()
        config.update(score_ceiling=100, min_relative_improvement=0.1)
        self.store.save_config(config)
        self.check(1, score=95)
        self.check(2, score=95)
        result = assess_baseline(self.store)
        self.assertEqual(result["minimum_gain"], 9.5)
        self.assertIn("insufficient-headroom", self.categories(result))

    def test_empty_task_map_and_reversed_goal_are_not_ready(self) -> None:
        self.check(1, tasks={})
        self.check(2, tasks={})
        self.store.mutate_discovery(lambda value: value.update(
            dimensions=[{"name": "latency", "direction": "min"}], selected_dimension="latency"
        ))
        self.assertEqual(
            self.categories(assess_baseline(self.store)), {"task-coverage", "goal-direction"}
        )

    def test_missing_worktree_has_an_actionable_blocker(self) -> None:
        graph = self.store.graph()
        graph["nodes"]["exp_0000"]["worktree"] = None
        atomic_write_json(self.store.graph_path, graph)
        self.assertIn("baseline-worktree", self.categories(assess_baseline(self.store)))

    def test_readiness_policy_is_frozen_without_changing_legacy_manifest_shape(self) -> None:
        config = self.store.config()
        legacy = measurement_snapshot(config, self.store.root)
        self.assertNotIn("require_baseline_checks", legacy["settings"])
        self.assertNotIn("benchmark_determinism", legacy["settings"])
        config["require_baseline_checks"] = True
        strict = measurement_snapshot(config, self.store.root)
        self.assertTrue(strict["settings"]["require_baseline_checks"])
        config["benchmark_determinism"] = "noisy"
        self.assertNotEqual(strict["digest"], measurement_snapshot(config, self.store.root)["digest"])


if __name__ == "__main__":
    unittest.main()
