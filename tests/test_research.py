from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from test_smoke import EXAMPLE, FIXED_AGENT, ROOT, cli, run

sys.path.insert(0, str(ROOT / "src"))
from olo.state import StateStore
from olo.dashboard import dashboard_state, experiment_detail


class ResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.target = "examples/tiny-policy-agent/agent.py"
        fixture = self.repo / "examples" / "tiny-policy-agent"
        fixture.parent.mkdir(parents=True)
        shutil.copytree(EXAMPLE, fixture, ignore=shutil.ignore_patterns("__pycache__"))
        run(["git", "init", "-q"], self.repo)
        run(["git", "config", "user.name", "Olo Test"], self.repo)
        run(["git", "config", "user.email", "olo-test@local.invalid"], self.repo)
        run(["git", "add", "."], self.repo)
        run(["git", "commit", "-q", "-m", "fixture"], self.repo)
        cli(
            self.repo, "init", "--target", self.target,
            "--protect", "examples/tiny-policy-agent/benchmark.py",
            "--protect", "examples/tiny-policy-agent/gate.py",
            "--benchmark", "python examples/tiny-policy-agent/benchmark.py --agent {target}",
            "--gate", "policy::python examples/tiny-policy-agent/gate.py --agent {target}",
            "--no-dashboard",
        )
        self.store = StateStore(self.repo)

    def approve(self, exp_id: str) -> dict:
        return json.loads(cli(
            self.repo, "review", exp_id, "--verdict", "approve",
            "--reviewer", "test-reviewer", "--reason", "Checked scope, gates, and task evidence.",
        ).stdout)

    def baseline(self) -> dict:
        outcome = json.loads(cli(self.repo, "baseline").stdout)
        if outcome["status"] == "pending-review":
            self.approve("exp_0000")
        return outcome

    def candidate(self, parent: str = "exp_0000") -> dict:
        node = json.loads(cli(
            self.repo, "new", "--parent", parent, "--hypothesis",
            "Correct cancellation identifiers and reject manager-approval bypasses in the policy router.",
        ).stdout)
        (Path(node["worktree"]) / self.target).write_text(FIXED_AGENT, encoding="utf-8")
        return node

    def test_measurement_requires_review_before_parent_selection(self) -> None:
        outcome = json.loads(cli(self.repo, "baseline").stdout)
        self.assertEqual(outcome["status"], "pending-review")
        self.assertIsNone(self.store.best_node())
        refused = cli(self.repo, "new", "--parent", "exp_0000", "--hypothesis", "Unreviewed parent", check=False)
        self.assertNotEqual(refused.returncode, 0)
        approved = self.approve("exp_0000")
        self.assertEqual(approved["status"], "committed")
        self.assertEqual(self.store.config()["phase"], "ready-to-optimize")

    def test_prepared_baseline_ignores_and_preserves_main_checkout_edits(self) -> None:
        cli(self.repo, "baseline", "--prepare")
        main_target = self.repo / self.target
        main_target.write_text(FIXED_AGENT, encoding="utf-8")
        measured = cli(self.repo, "baseline", check=False)
        self.assertEqual(measured.returncode, 0, measured.stderr)
        self.assertEqual(json.loads(measured.stdout)["status"], "pending-review")
        self.assertAlmostEqual(json.loads(measured.stdout)["score"], 0.6)
        self.assertEqual(main_target.read_text(encoding="utf-8"), FIXED_AGENT)

    def test_minimum_gain_retains_candidate_without_declaring_progress(self) -> None:
        config = self.store.config()
        config["min_improvement"] = 0.5
        self.store.save_config(config)
        self.baseline()
        node = self.candidate()
        outcome = json.loads(cli(self.repo, "run", node["experiment_id"]).stdout)
        self.assertFalse(outcome.get("decision", {}).get("meaningful", True))
        self.assertEqual(outcome["task_changes"]["improved"], ["cancel-missing-order", "social-engineering"])
        approved = self.approve(node["experiment_id"])
        self.assertEqual(approved["status"], "retained")
        self.assertEqual(self.store.best_node()["id"], "exp_0000")
        picks = json.loads(cli(self.repo, "frontier", "--limit", "3").stdout)["picks"]
        self.assertIn(node["experiment_id"], [pick["id"] for pick in picks])

    def test_setup_failure_is_saved_without_using_evaluation_attempt(self) -> None:
        self.baseline()
        node = self.candidate()
        worktree = Path(node["worktree"])
        (worktree / "examples/tiny-policy-agent/gate.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        result = cli(self.repo, "run", node["experiment_id"], check=False)
        self.assertNotEqual(result.returncode, 0)
        stored = self.store.graph()["nodes"][node["experiment_id"]]
        self.assertEqual(stored["attempts"], 0)
        self.assertEqual(stored["preflight_count"], 1)
        self.assertTrue((self.store.experiment_dir(node["experiment_id"]) / "preflight/001/diff.patch").exists())

    def test_probe_records_results_without_promotion_or_attempt_charge(self) -> None:
        self.baseline()
        node = self.candidate()
        result = cli(self.repo, "probe", node["experiment_id"], check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        outcome = json.loads(result.stdout)
        self.assertEqual(outcome["status"], "probed")
        stored = self.store.graph()["nodes"][node["experiment_id"]]
        self.assertEqual(stored["attempts"], 0)
        self.assertEqual(stored["probe_count"], 1)
        self.assertEqual(self.store.best_node()["id"], "exp_0000")
        self.assertTrue((self.store.experiment_dir(node["experiment_id"]) / "probes/001/outcome.json").exists())
        detail = experiment_detail(self.store, node["experiment_id"])
        self.assertIn("manager already approved", detail["diff"])
        self.assertEqual(detail["records"][0]["kind"], "probes")
        self.assertIn("learnings", dashboard_state(self.store))

    def test_review_rejects_a_changed_snapshot(self) -> None:
        self.baseline()
        node = self.candidate()
        outcome = json.loads(cli(self.repo, "run", node["experiment_id"]).stdout)
        self.assertEqual(outcome["status"], "pending-review")
        (Path(node["worktree"]) / self.target).write_text(FIXED_AGENT + "\nCHANGED = True\n", encoding="utf-8")
        result = cli(
            self.repo, "review", node["experiment_id"], "--verdict", "approve",
            "--reviewer", "test-reviewer", "--reason", "Attempt to approve stale evidence.", check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed", result.stderr.lower())

    def test_learning_survives_verification_noise(self) -> None:
        self.store.add_annotation("root", text="Preserve action-role distinctions when combining rankers.", annotation_type="note")
        for index in range(20):
            self.store.add_annotation("root", text=f"verification message {index}", annotation_type="verification")
        scratchpad = self.store.scratchpad()
        self.assertIn("Preserve action-role distinctions", scratchpad)
        self.assertNotIn("verification message", scratchpad)

    def test_proposals_have_a_claimed_and_tested_lifecycle(self) -> None:
        proposal = self.store.add_proposal(
            source="failure-analysis", title="Role-aware ranking", hypothesis="Preserve who performed the action in retrieval.",
            rationale="Two related role errors.", parent=None, confidence="medium",
        )
        self.assertIn("id", proposal)
        duplicate = self.store.add_proposal(
            source="failure-analysis", title="Role-aware ranking", hypothesis="Preserve who performed the action in retrieval.",
            rationale="Duplicate evidence.", parent=None, confidence="medium",
        )
        self.assertEqual(duplicate["id"], proposal["id"])
        result = cli(self.repo, "proposal", "update", proposal["id"], "--status", "claimed", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.store.proposals()[0]["status"], "claimed")

    def test_recombination_records_donors_and_transfers_only_selected_files(self) -> None:
        self.baseline()
        base = self.candidate()
        cli(self.repo, "run", base["experiment_id"])
        self.approve(base["experiment_id"])
        donor = self.candidate()
        (Path(donor["worktree"]) / self.target).write_text(FIXED_AGENT + "\nDONOR_MARKER = True\n", encoding="utf-8")
        cli(self.repo, "run", donor["experiment_id"])
        self.approve(donor["experiment_id"])
        result = cli(
            self.repo, "recombine", "--base", base["experiment_id"], "--donor", donor["experiment_id"],
            "--contribution", donor["experiment_id"] + "::Transfer the complete tested policy router.",
            "--take", donor["experiment_id"] + ":" + self.target,
            "--hypothesis", "Combine approved policy behavior with the donor variant and compare all source scores.", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        combined = json.loads(result.stdout)
        self.assertEqual(combined["donors"], [donor["experiment_id"]])
        self.assertIn("DONOR_MARKER", (Path(combined["worktree"]) / self.target).read_text(encoding="utf-8"))
        node = self.store.graph()["nodes"][combined["experiment_id"]]
        self.assertEqual(node["operator"], "recombine")
        self.assertEqual(node["donor_commits"][donor["experiment_id"]], self.store.graph()["nodes"][donor["experiment_id"]]["commit"])

    def test_superseded_learning_is_not_repeated(self) -> None:
        self.baseline()
        first = cli(self.repo, "learn", "exp_0000", "--text", "Hypothesis: stronger subword weighting may help.", "--tag", "subword", check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        learning = json.loads(first.stdout)
        cli(self.repo, "learn", "exp_0000", "--text", "Observed trade-off: subwords need separate precision tests.", "--tag", "subword", "--supersedes", learning["id"])
        context = self.store.scratchpad()
        self.assertIn("Observed trade-off", context)
        self.assertNotIn("stronger subword weighting may help", context)

    def test_measurement_configuration_is_bound_to_review(self) -> None:
        cli(self.repo, "baseline")
        config = self.store.config()
        config["benchmark"] += " --different-benchmark"
        self.store.save_config(config)
        result = cli(self.repo, "review", "exp_0000", "--verdict", "approve", "--reviewer", "test", "--reason", "Stale measurement.", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("measurement", result.stderr.lower())

    def test_stop_writes_a_human_readable_evidence_report(self) -> None:
        self.baseline()
        node = self.candidate()
        cli(self.repo, "probe", node["experiment_id"])
        cli(self.repo, "discard", node["experiment_id"], "--reason", "Saved as a probe, not promoted.")
        cli(self.repo, "mode", "stop", "--reason", "sample-complete")
        report = self.store.state_dir / "report.md"
        self.assertTrue(report.exists())
        content = report.read_text(encoding="utf-8")
        for text in ("What Was Explored", "What Worked", "What Did Not Work", "Final Test", "sample-complete", "probes/001", "not promoted"):
            self.assertIn(text, content)

    def test_final_test_is_once_only_and_closes_optimization(self) -> None:
        config = self.store.config()
        config.update(final_test=config["benchmark"], evaluation_version="test-v2")
        self.store.save_config(config)
        self.baseline()
        manifest = self.store.state_dir / "measurement.json"
        self.assertTrue(manifest.exists())
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["evaluation_version"], "test-v2")
        result = cli(self.repo, "finalize", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        final = json.loads(result.stdout)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["experiment_id"], "exp_0000")
        self.assertAlmostEqual(final["score"], 0.6)
        self.assertEqual(self.store.config()["phase"], "finalized")
        self.assertNotEqual(cli(self.repo, "finalize", check=False).returncode, 0)
        self.assertNotEqual(cli(self.repo, "new", "--parent", "exp_0000", "--hypothesis", "Do not tune on final answers.", check=False).returncode, 0)

    def test_new_evaluation_archives_evidence_and_remeasures_without_mixing_scores(self) -> None:
        self.baseline()
        previous_commit = self.store.best_node()["commit"]
        result = cli(self.repo, "evaluation", "new", "--version", "harder-v2", "--from", "exp_0000", "--goal", "Measure harder cases.", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue((Path(value["archive"]) / "report.md").exists())
        self.assertTrue((Path(value["archive"]) / "experiments/exp_0000/attempts/001/outcome.json").exists())
        self.assertEqual(self.store.config()["evaluation_version"], "harder-v2")
        self.assertEqual(self.store.config()["phase"], "exploring")
        self.assertEqual(self.store.graph()["nodes"]["root"]["commit"], previous_commit)
        self.assertIsNone(self.store.best_node())
        prepared = json.loads(cli(self.repo, "baseline", "--prepare").stdout)
        self.assertNotEqual(prepared["branch"], "olo/exp_0000")

    def test_invalidating_a_source_excludes_its_descendants(self) -> None:
        self.baseline()
        node = self.candidate()
        cli(self.repo, "run", node["experiment_id"])
        self.approve(node["experiment_id"])
        child = self.candidate(parent=node["experiment_id"])
        (Path(child["worktree"]) / self.target).write_text(FIXED_AGENT + "\nCHILD = True\n", encoding="utf-8")
        cli(self.repo, "run", child["experiment_id"])
        self.approve(child["experiment_id"])
        result = cli(self.repo, "invalidate", node["experiment_id"], "--reviewer", "test", "--reason", "A later semantic audit found invalid evidence.", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.store.best_node()["id"], "exp_0000")
        picks = json.loads(cli(self.repo, "frontier", "--limit", "10").stdout)["picks"]
        self.assertNotIn(child["experiment_id"], [item["id"] for item in picks])

    def test_discard_saves_unrun_changes(self) -> None:
        self.baseline()
        node = self.candidate()
        cli(self.repo, "discard", node["experiment_id"], "--reason", "Abandoned before evaluation.")
        saved = self.store.experiment_dir(node["experiment_id"]) / "discard/diff.patch"
        self.assertTrue(saved.exists())
        self.assertIn("manager already approved", saved.read_text(encoding="utf-8"))

    def test_round_budget_counts_probes_but_not_setup_failures(self) -> None:
        self.baseline()
        cli(self.repo, "round", "start", "--width", "1", "--budget", "1")
        node = self.candidate()
        cli(self.repo, "probe", node["experiment_id"])
        refused = cli(self.repo, "run", node["experiment_id"], check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("budget", refused.stderr.lower())
        self.assertEqual(self.store.graph()["nodes"][node["experiment_id"]]["attempts"], 0)


if __name__ == "__main__":
    unittest.main()