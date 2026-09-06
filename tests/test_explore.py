from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "olo.py"

TARGET = '''def slugify(text: str) -> str:
    return text.lower().replace(" ", "-")
'''

BENCHMARK = '''from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path

CASES = {
    "simple": ("Hello World", "hello-world"),
    "spaces": ("Hello   World", "hello-world"),
    "punctuation": ("Hello, World!", "hello-world"),
}


def load_target(path: Path):
    spec = importlib.util.spec_from_file_location("slug_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load target")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    module = load_target(Path(args.target))
    traces = Path(os.environ["OLO_TRACES_DIR"])
    traces.mkdir(parents=True, exist_ok=True)
    scores = {}
    for task_id, (value, expected) in CASES.items():
        actual = module.slugify(value)
        score = 1.0 if actual == expected else 0.0
        scores[task_id] = score
        (traces / f"task_{task_id}.json").write_text(
            json.dumps({
                "task_id": task_id,
                "score": score,
                "status": "passed" if score else "failed",
                "summary": f"actual={actual} expected={expected}",
            }),
            encoding="utf-8",
        )
    result = {"score": sum(scores.values()) / len(scores), "tasks": scores}
    Path(os.environ["OLO_RESULT_PATH"]).write_text(
        json.dumps(result),
        encoding="utf-8",
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
'''

GATE = '''from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("slug_gate_target", Path(args.target))
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load target")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.slugify("Hello World") != "hello-world":
        sys.exit(1)


if __name__ == "__main__":
    main()
'''


def run(
    command: list[str],
    cwd: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and process.returncode != 0:
        raise AssertionError(
            f"command failed ({process.returncode}): {' '.join(command)}\n"
            f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return process


def cli(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(
        [sys.executable, str(CLI), "--repo", str(repo), *args],
        repo,
        check=check,
    )


class ExploreTests(unittest.TestCase):
    def test_constructs_benchmark_in_baseline_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            (repo / "slugify.py").write_text(TARGET, encoding="utf-8")
            (repo / "README.md").write_text(
                "# Slug helper\n\nTurn titles into URL-safe slugs.\n",
                encoding="utf-8",
            )
            run(["git", "init", "-q"], repo)
            run(["git", "config", "user.name", "Olo Test"], repo)
            run(["git", "config", "user.email", "olo-test@local.invalid"], repo)
            run(["git", "add", "."], repo)
            run(["git", "commit", "-q", "-m", "initial code"], repo)

            initialized = json.loads(
                cli(
                    repo,
                    "explore",
                    "init",
                    "--name",
                    "slug-helper",
                    "--goal",
                    "Improve slug correctness on spaces and punctuation.",
                    "--timeout",
                    "901",
                    "--max-attempts",
                    "7",
                    "--stall-limit",
                    "9",
                    "--no-dashboard",
                ).stdout
            )
            self.assertEqual(initialized["phase"], "exploring")

            cli(
                repo,
                "explore",
                "add-dimension",
                "--name",
                "slug-correctness",
                "--description",
                "Increase deterministic slug correctness over representative titles.",
                "--target",
                "slugify.py",
                "--metric-name",
                "mean case pass rate",
                "--direction",
                "max",
                "--evidence",
                "README promises URL-safe slugs but no tests or benchmark exist.",
                "--complexity",
                "minor",
                "--run-cost",
                "small",
            )
            cli(repo, "explore", "select", "--name", "slug-correctness")

            prepared = json.loads(cli(repo, "baseline", "--prepare").stdout)
            self.assertEqual(prepared["experiment_id"], "exp_0000")
            worktree = Path(prepared["worktree"])
            (worktree / "benchmark.py").write_text(BENCHMARK, encoding="utf-8")
            (worktree / "gate.py").write_text(GATE, encoding="utf-8")

            configure_args = (
                "explore",
                "configure",
                "--target",
                "slugify.py",
                "--editable",
                "slugify.py",
                "--protect",
                "benchmark.py",
                "--protect",
                "gate.py",
                "--benchmark",
                "python benchmark.py --target {target}",
                "--benchmark-origin",
                "constructed",
                "--gate",
                "baseline-behavior::python gate.py --target {target}",
                "--metric",
                "max",
                "--score-ceiling",
                "1.0",
                "--unit",
                "slug conversion case",
                "--determinism",
                "deterministic",
                "--resource-profile",
                "CPU-light isolated process; start with width 1.",
                "--meaningful-improvement",
                "At least one additional case passing.",
                "--repo-summary",
                "Small Python helper that converts titles into URL slugs.",
                "--gaming-risk",
                "The target could special-case the three visible inputs.",
            )
            configured = json.loads(cli(repo, *configure_args).stdout)
            self.assertEqual(configured["phase"], "ready-for-baseline")
            configured_state = json.loads(cli(repo, "explore", "status").stdout)
            self.assertEqual(configured_state["config"]["timeout_seconds"], 901)
            self.assertEqual(configured_state["config"]["max_attempts"], 7)
            self.assertEqual(configured_state["config"]["stall_limit"], 9)

            checked = json.loads(cli(repo, "run", "exp_0000", "--check").stdout)
            self.assertEqual(checked["status"], "check-passed")
            self.assertEqual(checked["trace_count"], 3)
            self.assertAlmostEqual(checked["score"], 1 / 3)
            node_after_check = json.loads(cli(repo, "show", "exp_0000").stdout)["node"]
            self.assertEqual(node_after_check["attempts"], 0)
            self.assertEqual(node_after_check["status"], "pending")

            baseline = json.loads(cli(repo, "baseline").stdout)
            self.assertEqual(baseline["status"], "pending-review")
            self.assertAlmostEqual(baseline["score"], 1 / 3)
            cli(repo, "review", "exp_0000", "--verdict", "approve", "--reviewer", "test", "--reason", "Baseline benchmark and gate checked.")
            status = json.loads(cli(repo, "status", "--json").stdout)
            self.assertEqual(status["phase"], "ready-to-optimize")
            self.assertEqual(status["best_experiment"], "exp_0000")

            self.assertFalse((repo / "benchmark.py").exists())
            self.assertFalse((repo / "gate.py").exists())
            branch_files = run(
                ["git", "ls-tree", "--name-only", "olo/exp_0000"],
                repo,
            ).stdout.splitlines()
            self.assertIn("benchmark.py", branch_files)
            self.assertIn("gate.py", branch_files)
            self.assertEqual(
                run(["git", "status", "--porcelain"], repo).stdout.strip(),
                "",
            )
            reconfigure = cli(repo, *configure_args, check=False)
            self.assertEqual(reconfigure.returncode, 1)
            self.assertIn("baseline is already committed", reconfigure.stderr)
            final_status = json.loads(cli(repo, "status", "--json").stdout)
            self.assertEqual(final_status["phase"], "ready-to-optimize")


if __name__ == "__main__":
    unittest.main()
