from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from olo.hooks import handle_hook
from olo.state import StateStore


class HookTests(unittest.TestCase):
    def make_store(self, root: Path) -> StateStore:
        (root / "agent.py").write_text("VALUE = 1\n", encoding="utf-8")
        store = StateStore(root)
        store.initialize(
            project_name="hooks",
            target="agent.py",
            benchmark="python benchmark.py",
            metric="max",
            gates=[],
            root_commit="deadbeef",
            editable_paths=["agent.py"],
            protected_paths=["benchmark.py"],
            timeout_seconds=30,
            max_attempts=3,
            stall_limit=3,
            frontier_strategy={"kind": "argmax", "k": 1},
            score_ceiling=1.0,
        )
        store.mode_start(autonomous=True, stall_limit=3)
        return store

    def test_blocks_main_target_edit_in_optimize_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(Path(temp))
            result = handle_hook(
                store,
                "pre-tool-use",
                {"toolName": "edit", "toolArgs": {"path": "agent.py"}},
            )
            self.assertEqual(result["permissionDecision"], "deny")

    def test_allows_worktree_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(Path(temp))
            result = handle_hook(
                store,
                "pre-tool-use",
                {
                    "toolName": "edit",
                    "toolArgs": {
                        "path": str(
                            Path(temp)
                            / ".olo"
                            / "worktrees"
                            / "exp_0001"
                            / "agent.py"
                        )
                    },
                },
            )
            self.assertEqual(result, {})

    def test_repairs_unstructured_experimenter_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(Path(temp))
            result = handle_hook(
                store,
                "subagent-stop",
                {
                    "agentName": "olo-experimenter",
                    "response": "I changed the router and it looks good.",
                },
            )
            self.assertEqual(result["decision"], "block")


if __name__ == "__main__":
    unittest.main()
