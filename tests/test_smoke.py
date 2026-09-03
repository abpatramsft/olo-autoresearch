from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "olo.py"
EXAMPLE = ROOT / "examples" / "tiny-policy-agent"

FIXED_AGENT = '''"""Improved tiny policy router."""

import re


def route(request: str) -> str:
    text = request.lower()

    if "manager already approved" in text or "skip the checks" in text:
        return "deny"

    if "status" in text or "where is" in text:
        return "status"

    if "refund" in text:
        return "refund"

    if "cancel" in text:
        return "cancel" if re.search(r"\\b\\d+\\b", text) else "clarify"

    return "escalate"
'''


def run(command: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


class SmokeTests(unittest.TestCase):
    def test_end_to_end_experiment_and_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            fixture = repo / "examples" / "tiny-policy-agent"
            fixture.parent.mkdir(parents=True)
            shutil.copytree(EXAMPLE, fixture)
            run(["git", "init", "-q"], repo)
            run(["git", "config", "user.name", "Olo Test"], repo)
            run(["git", "config", "user.email", "olo-test@local.invalid"], repo)
            run(["git", "add", "."], repo)
            run(["git", "commit", "-q", "-m", "fixture"], repo)

            cli(
                repo,
                "init",
                "--name",
                "tiny-policy",
                "--target",
                "examples/tiny-policy-agent/agent.py",
                "--editable",
                "examples/tiny-policy-agent/agent.py",
                "--protect",
                "examples/tiny-policy-agent/benchmark.py",
                "--protect",
                "examples/tiny-policy-agent/gate.py",
                "--benchmark",
                "python examples/tiny-policy-agent/benchmark.py --agent {target}",
                "--gate",
                "policy::python examples/tiny-policy-agent/gate.py --agent {target}",
                "--metric",
                "max",
                "--score-ceiling",
                "1.0",
                "--no-dashboard",
            )
            baseline = json.loads(cli(repo, "baseline").stdout)
            self.assertEqual(baseline["status"], "committed")
            self.assertAlmostEqual(baseline["score"], 0.6)
            self.assertEqual(baseline["trace_count"], 5)

            created = json.loads(
                cli(
                    repo,
                    "new",
                    "--parent",
                    "exp_0000",
                    "--hypothesis",
                    (
                        "In agent.py route, deny manager-approval bypasses before "
                        "refund handling and clarify cancellation without an order id."
                    ),
                ).stdout
            )
            worktree = Path(created["worktree"])
            (worktree / "examples" / "tiny-policy-agent" / "agent.py").write_text(
                FIXED_AGENT,
                encoding="utf-8",
            )
            improved = json.loads(cli(repo, "run", created["experiment_id"]).stdout)
            self.assertEqual(improved["status"], "committed")
            self.assertAlmostEqual(improved["score"], 1.0)
            self.assertTrue(improved["gates_passed"])

            status = json.loads(cli(repo, "status", "--json").stdout)
            self.assertEqual(status["best_experiment"], "exp_0001")
            self.assertAlmostEqual(status["best_score"], 1.0)

            port = free_port()
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(CLI),
                    "--repo",
                    str(repo),
                    "dashboard",
                    "--foreground",
                    "--port",
                    str(port),
                ],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 8
                state = None
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/state",
                            timeout=0.5,
                        ) as response:
                            state = json.loads(response.read().decode("utf-8"))
                            break
                    except Exception:
                        time.sleep(0.1)
                self.assertIsNotNone(state)
                self.assertEqual(state["status"]["best_experiment"], "exp_0001")
                self.assertEqual(len(state["graph"]["nodes"]), 3)
            finally:
                process.terminate()
                process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
