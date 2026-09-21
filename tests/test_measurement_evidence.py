from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_explore import cli, run

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from olo.runner import fill_command, parse_benchmark_result
from olo.utils import quote_shell
from olo.verification import task_trace_errors


class BenchmarkResultTests(unittest.TestCase):
    def test_invalid_task_scores_are_not_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result_path = Path(temp) / "result.json"
            for tasks in ({"broken": "NaN"}, {"broken": None}, ["not-a-map"]):
                with self.subTest(tasks=tasks):
                    with self.assertRaisesRegex((ValueError, RuntimeError), "tasks|task"):
                        parse_benchmark_result(
                            result_path, json.dumps({"score": 0.5, "tasks": tasks})
                        )

    def test_invalid_result_file_does_not_fall_back_to_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result_path = Path(temp) / "result.json"
            result_path.write_text('{"tasks": {"missing-score": 1}}', encoding="utf-8")
            with self.assertRaisesRegex((ValueError, RuntimeError), "score"):
                parse_benchmark_result(result_path, '{"score": 1}')

    def test_aggregate_only_legacy_output_is_still_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = parse_benchmark_result(Path(temp) / "result.json", "score=0.75")
            self.assertEqual(result, {"score": 0.75, "tasks": {}})

    def test_python_placeholder_uses_the_current_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            worktree = Path(temp)
            rendered = fill_command(
                "{python} benchmark.py --target {target}", worktree, worktree / "target.py"
            )
            self.assertEqual(
                rendered,
                f"{quote_shell(sys.executable)} benchmark.py --target "
                f"{quote_shell(worktree / 'target.py')}",
            )


class GateEvidenceTests(unittest.TestCase):
    def test_gates_cannot_overwrite_benchmark_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            (repo / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
            for filename, task, score in (
                ("benchmark.py", "development", 0.5),
                ("gate.py", "validation", 0.9),
            ):
                (repo / filename).write_text(
                    "import json, os\n"
                    "from pathlib import Path\n"
                    f"task, score = {task!r}, {score!r}\n"
                    "Path(os.environ['OLO_RESULT_PATH']).write_text("
                    "json.dumps({'score': score, 'tasks': {task: score}}), encoding='utf-8')\n"
                    "Path(os.environ['OLO_TRACES_DIR'], 'task.json').write_text("
                    "json.dumps({'task_id': task, 'score': score}), encoding='utf-8')\n",
                    encoding="utf-8",
                )
            run(["git", "init", "-q"], repo)
            run(["git", "config", "user.name", "Olo Test"], repo)
            run(["git", "config", "user.email", "olo-test@local.invalid"], repo)
            run(["git", "add", "."], repo)
            run(["git", "commit", "-q", "-m", "fixture"], repo)
            cli(
                repo, "init", "--target", "target.py",
                "--protect", "benchmark.py", "--protect", "gate.py",
                "--benchmark", "{python} benchmark.py",
                "--gate", "validation::{python} gate.py",
                "--gate", "validation::{python} gate.py",
                "--final-test", "{python} gate.py",
                "--no-dashboard",
            )
            config = json.loads((repo / ".olo" / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["benchmark"], f"{quote_shell(sys.executable)} benchmark.py")
            self.assertEqual(config["gates"][0]["command"], f"{quote_shell(sys.executable)} gate.py")
            self.assertEqual(config["final_test"], f"{quote_shell(sys.executable)} gate.py")
            cli(repo, "baseline", "--prepare")
            outcome = json.loads(cli(repo, "run", "exp_0000", "--check").stdout)
            directory = repo / ".olo" / outcome["artifact_dir"]
            saved_result = json.loads(
                (directory / "benchmark-result.json").read_text(encoding="utf-8")
            )
            saved_trace = json.loads(
                (directory / "traces" / "task.json").read_text(encoding="utf-8")
            )
            self.assertEqual(outcome["score"], 0.5)
            self.assertEqual(saved_result, outcome["benchmark"]["result"])
            self.assertEqual(saved_trace["task_id"], "development")
            self.assertEqual(saved_trace["score"], 0.5)
            self.assertEqual(outcome["trace_count"], 1)
            gate = outcome["gate_results"][0]
            gate_directory = repo / ".olo" / gate["artifact_dir"]
            gate_trace = json.loads(
                (gate_directory / "traces" / "task.json").read_text(encoding="utf-8")
            )
            self.assertEqual(gate_trace["task_id"], "validation")
            self.assertEqual(len(outcome["gate_results"]), 2)
            self.assertNotEqual(
                gate["artifact_dir"], outcome["gate_results"][1]["artifact_dir"]
            )


class TaskTraceTests(unittest.TestCase):
    def test_trace_contract_rejects_incomplete_or_inconsistent_evidence(self) -> None:
        cases = (
            ({}, "missing"),
            ({"one": {"task_id": "one", "score": 0.2}}, "disagrees"),
            ({"one": {"task_id": "one", "score": "NaN"}}, "finite"),
            ({"one": {"score": 0.5}}, "identify"),
            ({"one": {"task_id": "other", "score": 0.5}}, "unexpected"),
            ({"one": [], "two": "not-json"}, "object"),
            (
                {
                    "one": {"task_id": "one", "score": 0.5},
                    "duplicate": {"task_id": "one", "score": 0.5},
                },
                "duplicate",
            ),
        )
        for traces, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                for name, value in traces.items():
                    (directory / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
                self.assertIn(expected, "; ".join(task_trace_errors(directory, {"one": 0.5})))

    def test_matching_scores_and_legacy_aggregate_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "one.json").write_text('{"task_id": "one", "score": 0.5}', encoding="utf-8")
            self.assertEqual(task_trace_errors(directory, {"one": 0.5}), [])
            self.assertEqual(task_trace_errors(directory, {}), [])


if __name__ == "__main__":
    unittest.main()
