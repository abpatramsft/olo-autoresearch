from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "olo.py"


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


class BaselineRecoveryTests(unittest.TestCase):
    def test_discarded_and_repeatedly_failed_baseline_can_recover(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            (repo / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
            run(["git", "init", "-q"], repo)
            run(["git", "config", "user.name", "Olo Test"], repo)
            run(["git", "config", "user.email", "olo-test@local.invalid"], repo)
            run(["git", "add", "."], repo)
            run(["git", "commit", "-q", "-m", "initial"], repo)

            cli(
                repo,
                "explore",
                "init",
                "--goal",
                "Measure the target.",
                "--max-attempts",
                "1",
                "--no-dashboard",
            )
            first = json.loads(cli(repo, "baseline", "--prepare").stdout)
            self.assertTrue(Path(first["worktree"]).exists())
            cli(
                repo,
                "discard",
                "exp_0000",
                "--reason",
                "restart benchmark construction",
                "--failure-class",
                "build",
            )
            recreated = json.loads(cli(repo, "baseline", "--prepare").stdout)
            worktree = Path(recreated["worktree"])
            self.assertTrue(worktree.exists())
            self.assertEqual(recreated["status"], "pending")

            (worktree / "benchmark.py").write_text(
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            (worktree / "gate.py").write_text(
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            cli(
                repo,
                "explore",
                "configure",
                "--target",
                "target.py",
                "--editable",
                "target.py",
                "--protect",
                "benchmark.py",
                "--protect",
                "gate.py",
                "--benchmark",
                "python benchmark.py",
                "--benchmark-origin",
                "constructed",
                "--gate",
                "gate::python gate.py",
                "--metric",
                "max",
                "--unit",
                "one target check",
                "--determinism",
                "deterministic",
                "--resource-profile",
                "CPU-light isolated process; width 1.",
                "--meaningful-improvement",
                "Any strict increase.",
                "--repo-summary",
                "Minimal recovery fixture.",
                "--gaming-risk",
                "The target could return a constant.",
            )

            first_failure = cli(repo, "baseline", check=False)
            second_failure = cli(repo, "baseline", check=False)
            self.assertEqual(first_failure.returncode, 1)
            self.assertEqual(second_failure.returncode, 1)
            failed_node = json.loads(cli(repo, "show", "exp_0000").stdout)["node"]
            self.assertEqual(failed_node["attempts"], 2)

            (worktree / "benchmark.py").write_text(
                '''from __future__ import annotations

import json
import os
from pathlib import Path

traces = Path(os.environ["OLO_TRACES_DIR"])
traces.mkdir(parents=True, exist_ok=True)
(traces / "task_one.json").write_text(
    json.dumps({"task_id": "one", "score": 1.0}),
    encoding="utf-8",
)
result = {"score": 1.0, "tasks": {"one": 1.0}}
Path(os.environ["OLO_RESULT_PATH"]).write_text(
    json.dumps(result),
    encoding="utf-8",
)
print(json.dumps(result))
''',
                encoding="utf-8",
            )
            recovered = json.loads(cli(repo, "baseline").stdout)
            self.assertEqual(recovered["status"], "committed")
            self.assertEqual(recovered["attempt"], 3)
            self.assertEqual(
                json.loads(cli(repo, "status", "--json").stdout)["phase"],
                "ready-to-optimize",
            )


if __name__ == "__main__":
    unittest.main()
