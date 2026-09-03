from __future__ import annotations

import json
import re
from typing import Any

from .state import StateStore


def _tool_name(payload: dict[str, Any]) -> str:
    return str(payload.get("toolName") or payload.get("tool_name") or "")


def _tool_args(payload: dict[str, Any]) -> Any:
    return payload.get("toolArgs", payload.get("tool_input", {}))


def _normalized_text(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True)
    return re.sub(r"/+", "/", text.replace("\\", "/").lower())


def _session_start(store: StateStore) -> dict[str, Any]:
    if not store.is_initialized():
        return {
            "additionalContext": (
                "This repository contains the project-local Olo autoresearch kit. "
                "Use /olo-explore to identify a goal and build the baseline, then "
                "use /olo-optimize for experiment rounds."
            )
        }
    status = store.status_summary()
    if status.get("phase") != "ready-to-optimize":
        return {
            "additionalContext": (
                f"Olo is in exploration phase `{status.get('phase')}` with discovery "
                f"status `{status.get('discovery_status')}`. Use /olo-explore and "
                "`python olo.py explore status`; do not start optimization until "
                "exp_0000 is committed."
            )
        }
    return {
        "additionalContext": (
            "Olo autoresearch is initialized. "
            f"Target: {status['target']}; best: {status['best_score']} "
            f"({status['best_experiment']}); mode: {status['mode'].get('status')}. "
            "Use /olo-optimize and `python olo.py scratchpad` before proposing "
            "or running candidate changes."
        )
    }


def _pre_tool_use(store: StateStore, payload: dict[str, Any]) -> dict[str, Any]:
    if not store.is_initialized():
        return {}
    tool = _tool_name(payload).lower()
    if tool not in {"edit", "create", "apply_patch", "write"}:
        return {}
    args_text = _normalized_text(_tool_args(payload))
    if ".olo/worktrees/" in args_text:
        return {}
    config = store.config()
    phase = config.get("phase")
    if phase != "ready-to-optimize":
        store.add_event("exploration_main_edit_blocked", tool=tool, phase=phase)
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Olo exploration keeps benchmark, gate, fixture, instrumentation, "
                "and scorer edits off main. Run `python olo.py baseline --prepare` "
                "and edit only the returned exp_0000 worktree."
            ),
        }
    mode = store.meta().get("mode") or {}
    if not mode.get("active"):
        return {}
    target = str(config.get("target") or "").replace("\\", "/").lower()
    absolute = str(store.root / target).replace("\\", "/").lower()
    protected = [
        str(path).replace("\\", "/").lower()
        for path in config.get("protected_paths") or []
    ]
    matched_protected = any(path and path in args_text for path in protected)
    if target and (target in args_text or absolute in args_text) or matched_protected:
        store.add_event("main_target_edit_blocked", tool=tool)
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Olo optimize mode keeps candidate edits out of the main checkout. "
                "Delegate the change to olo-experimenter and edit the path returned "
                "by `python olo.py new` under .olo/worktrees/."
            ),
        }
    return {}


def _post_tool_use(store: StateStore, payload: dict[str, Any]) -> dict[str, Any]:
    if not store.is_initialized():
        return {}
    tool = _tool_name(payload).lower()
    if tool not in {"bash", "powershell"}:
        return {}
    command = _normalized_text(_tool_args(payload))
    if "olo.py run" in command or "benchmark" in command:
        return {
            "additionalContext": (
                "After an Olo run, read `python olo.py show <exp_id>` and "
                "`python olo.py scratchpad`; decide from the recorded score, gates, "
                "traces, and verification rather than from console output alone."
            )
        }
    return {}


def _agent_stop(store: StateStore, payload: dict[str, Any]) -> dict[str, Any]:
    if not store.is_initialized():
        return {}
    meta = store.meta()
    mode = meta.get("mode") or {}
    if not mode.get("active") or not mode.get("autonomous"):
        return {}
    count = int(mode.get("continuation_count", 0))
    if count >= 6:
        return {}

    def mutate(current: dict[str, Any]) -> None:
        current.setdefault("mode", {})["continuation_count"] = count + 1

    store.mutate_meta(mutate)
    return {
        "decision": "block",
        "reason": (
            "Olo autonomous mode is still active. Continue the autoresearch loop: "
            "read the scratchpad, close any completed round, choose diverse frontier "
            "parents, dispatch the next bounded experiment round, and stop only when "
            "Olo reports ceiling/stalled or the user requested a stop."
        ),
    }


def _subagent_start(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("agentName") or payload.get("agent_name") or "")
    if name == "olo-explorer":
        return {
            "additionalContext": (
                "Keep main clean. Prepare exp_0000 first, then create benchmark, "
                "fixtures, instrumentation, and gates only inside that baseline "
                "worktree. Finish only after the checked baseline is committed."
            )
        }
    if name == "olo-experimenter":
        return {
            "additionalContext": (
                "You own candidate work only. Allocate an experiment, edit only its "
                ".olo/worktrees/exp_* checkout, preserve benchmark/gates, run pre and "
                "post verification, and finish with the required JSON result."
            )
        }
    if name == "olo-verifier":
        return {
            "additionalContext": (
                "Remain read-only. Treat benchmark or gate modification, test leakage, "
                "scope escape, and skipped evaluation as blocking findings."
            )
        }
    return {}


def _subagent_stop(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("agentName") or payload.get("agent_name") or "")
    if name != "olo-experimenter":
        return {}
    response = str(
        payload.get("response") or payload.get("last_assistant_message") or ""
    )
    has_exp = re.search(r"\bexp_\d{4,}\b", response) is not None
    has_status = re.search(
        r'"status"\s*:\s*"(committed|evaluated|failed|discarded)"',
        response,
    )
    if has_exp and has_status:
        return {}
    return {
        "decision": "block",
        "reason": (
            "Return the experiment handoff as one JSON object containing at least "
            "`experiment_id`, `status`, `score`, `parent`, `verification`, and "
            "`learnings`. Do not finish with prose only."
        ),
    }


def handle_hook(
    store: StateStore,
    event: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    event = event.replace("_", "-").lower()
    if store.is_initialized():
        store.add_event(
            "hook",
            hook_event=event,
            tool=_tool_name(payload) or None,
            agent=payload.get("agentName") or payload.get("agent_name"),
        )
    if event == "session-start":
        return _session_start(store)
    if event == "pre-tool-use":
        return _pre_tool_use(store, payload)
    if event == "post-tool-use":
        return _post_tool_use(store, payload)
    if event == "agent-stop":
        return _agent_stop(store, payload)
    if event == "subagent-start":
        return _subagent_start(payload)
    if event == "subagent-stop":
        return _subagent_stop(payload)
    return {}
