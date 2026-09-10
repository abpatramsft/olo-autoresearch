from __future__ import annotations

from collections import Counter
from typing import Any

from .research import eligible_nodes
from .utils import atomic_write_text, read_json, utc_now


def _text(value: Any) -> str:
    return str(value if value is not None else "not available").replace("|", "\\|").replace("\n", " ")


def _score(value: Any) -> str:
    return "not measured" if value is None else f"{float(value):.6f}"


def build_report(store) -> str:
    config = store.config()
    graph = store.graph()
    meta = store.meta()
    nodes = [node for exp_id, node in sorted(graph["nodes"].items()) if exp_id != "root"]
    trusted = {node["id"] for node in eligible_nodes(graph)}
    best = store.best_node()
    baseline = graph["nodes"].get("exp_0000") or {}
    final = read_json(store.state_dir / "final-test/outcome.json", {})
    records = []
    for path in sorted([*store.experiments_dir.glob("*/*/*/*.json"), *store.experiments_dir.glob("*/discard/outcome.json")]):
        if path.name not in {"outcome.json", "check.json"}:
            continue
        value = read_json(path, {})
        records.append((path.relative_to(store.state_dir).as_posix(), value))
    counts = Counter(path.split("/")[2] for path, _ in records)
    seconds = sum(float(value.get("total_duration_seconds", value.get("duration_seconds", 0)) or 0) for _, value in records)
    reason = (meta.get("mode") or {}).get("status", "in progress")
    lines = [
        f"# Olo Research Summary: {_text(config.get('project_name'))}", "",
        f"Generated: {utc_now()}. Evaluation version: **{_text(config.get('evaluation_version', 'legacy'))}**.", "",
        "## Outcome", "",
        f"The run is **{_text(config.get('phase'))}**; controller status: **{_text(reason)}**.",
        f"The approved best is **{best['id']}**, scoring **{_score(best.get('score'))}**." if best else "No candidate is currently approved as a winner.",
        f"Baseline: {_score(baseline.get('score'))}. Direction: {_text(config.get('metric'))}. Only approved, meaningful gains count as progress.", "",
        "## Goal and Measurement", "",
        _text(config.get("goal") or store.discovery().get("goal") or config.get("target")), "",
        f"- Target: `{_text(config.get('target'))}`",
        f"- Benchmark: `{_text(config.get('benchmark'))}`",
        f"- Meaningful gain: at least {config.get('min_improvement', 0)} absolute and {config.get('min_relative_improvement', 0)} relative to the parent.",
        f"- Critical tasks: {_text(', '.join(config.get('critical_tasks') or []) or 'none configured')}; maximum regression {config.get('max_task_regression', 0)}.",
        f"- Score ceiling: {_text(config.get('score_ceiling'))}.",
        "- Measurement manifest: [measurement.json](measurement.json). Hashes bind protected files and measurement settings to this version.",
    ]
    for gate in config.get("gates") or []:
        lines.append(f"- Gate {_text(gate.get('name'))}: `{_text(gate.get('command'))}`")
    assessment = store.discovery().get("baseline_assessment")
    if assessment:
        lines.extend([
            "", "## Exploration Readiness", "",
            f"Last assessment: **{_text(assessment.get('status'))}** at {_text(assessment.get('assessed_at'))}.",
            f"Matching checks: {assessment.get('matching_checks', 0)}; required: {assessment.get('required_checks')}. "
            f"Task count: {assessment.get('task_count', 0)}. Observed score range: {_text(assessment.get('observed_score_range'))}.",
            f"Remaining headroom: {_text(assessment.get('remaining_headroom'))}; minimum useful gain: {_text(assessment.get('minimum_gain'))}.",
            "Checks are bound to the source and measurement settings. Repeatability samples are diagnostic, not statistical or production proof.",
        ])
        for finding in assessment.get("findings") or []:
            lines.append(f"- **{_text(finding.get('severity'))}: {_text(finding.get('category'))}**. {_text(finding.get('what'))} {_text(finding.get('fix'))}")
        for path in assessment.get("check_records") or []:
            lines.append(f"- [Readiness evidence]({path})")
    lines.extend(["", "## What Was Explored", ""])
    for dimension in store.discovery().get("dimensions") or []:
        lines.append(f"- Dimension **{_text(dimension.get('name'))}**: {_text(dimension.get('description'))}")
    lines.extend(["", "| Experiment | Sources | Operator | Status | Score | Hypothesis |", "|---|---|---|---|---:|---|"])
    for node in nodes:
        sources = [node.get("parent", "root"), *(node.get("donors") or [])]
        lines.append(f"| {node['id']} | {_text(', '.join(sources))} | {_text(node.get('operator', 'mutation'))} | {_text(node.get('status'))} | {_score(node.get('score'))} | {_text(node.get('hypothesis'))} |")
    lines.extend(["", "## What Worked", ""])
    winners = [node for node in nodes if node["id"] in trusted and node.get("status") == "committed" and node.get("parent") != "root"]
    if not winners:
        lines.append("No approved meaningful improvement beyond the baseline has been established.")
    for node in winners:
        decision = node.get("decision") or {}
        lines.append(f"- **{node['id']}**: {_text(node.get('hypothesis'))} Gain against parent: {_score(decision.get('gain'))}; improved tasks: {_text(', '.join((node.get('task_changes') or {}).get('improved') or []) or 'none recorded')}.")
    if best:
        lineage = []
        cursor = best
        while cursor and cursor.get("id") != "root":
            lineage.append(cursor["id"])
            cursor = graph["nodes"].get(cursor.get("parent"))
        lines.extend(["", "Winning base lineage: " + " -> ".join(reversed(lineage)) + ". Donor contributions are recorded separately below."])
    lines.extend(["", "## What Did Not Work", ""])
    other = [node for node in nodes if node.get("status") in {"retained", "discarded", "failed", "invalid", "evaluated", "pending-review"}]
    if not other:
        lines.append("No unsuccessful or unapproved candidates are recorded yet.")
    for node in other:
        detail = node.get("discard_reason") or node.get("error") or (node.get("review") or {}).get("reason") or "No approved meaningful gain established."
        lines.append(f"- **{node['id']} ({_text(node.get('status'))})**: {_text(node.get('hypothesis'))} {_text(detail)}")
        if node.get("status") == "retained":
            lines.append("  Retained as a selectable specialist, not declared a new aggregate winner.")
    lines.extend(["", "## Trade-offs and Combinations", ""])
    tradeoffs = False
    for node in nodes:
        changes = node.get("task_changes") or {}
        if changes.get("regressed") or changes.get("missing"):
            tradeoffs = True
            lines.append(f"- {node['id']}: regressed tasks {_text(', '.join(changes.get('regressed') or []) or 'none')}; missing tasks {_text(', '.join(changes.get('missing') or []) or 'none')}. Directional deltas: `{_text(changes.get('deltas') or {})}`.")
        if node.get("donors"):
            tradeoffs = True
            outcome = store.latest_outcome(node["id"]) or {}
            for donor in node["donors"]:
                comparison = (outcome.get("source_comparison") or {}).get(donor) or {}
                lines.append(f"- {node['id']} used **{donor}** at `{_text((node.get('donor_commits') or {}).get(donor))}`: {_text((node.get('contributions') or {}).get(donor))} Gain versus donor: {_score(comparison.get('gain'))}.")
    if not tradeoffs:
        lines.append("No task regressions or combinations are recorded.")
    lines.extend(["", "## Research Lessons", "", "The following are evidence-linked observations or interpretations, not proof of causality.", ""])
    for item in store.learning_context(limit=40):
        payload = item.get("payload") or {}
        kind = payload.get("kind") or ("observation" if item.get("type") == "observation" else "interpretation")
        lines.append(f"- **{kind}**, {item.get('experiment_id')}: {_text(item.get('text'))}")
        if payload.get("evidence"):
            lines.append(f"  [Evidence]({str(payload['evidence']).replace(chr(92), '/')})")
    lines.extend(["", "## Rounds and Cost", "", f"Recorded {len(nodes)} experiments, {counts['attempts']} evaluation attempts, {counts['probes']} probes, {counts['checks']} checks, and {counts['preflight']} blocked setup checks.", f"Recorded benchmark and gate wall time: {seconds:.2f} seconds. This excludes agent reasoning and unrecorded external work.", ""])
    for current in meta.get("round_history") or []:
        lines.append(f"- Round {current['number']}: {_score(current.get('best_before'))} -> {_score(current.get('best_after'))}; meaningful progress={current.get('improved')}; width={current.get('width')}, per-worker budget={current.get('budget')}.")
    lines.extend(["", "## Final Test", ""])
    if final:
        lines.extend([f"Status: **{_text(final.get('status'))}**. Frozen candidate: **{_text(final.get('experiment_id'))}**. Final-test score: **{_score(final.get('score'))}**.", "[Final-test evidence](final-test/outcome.json). This result is not used for parent selection or further tuning in this run."])
        if final.get("error"):
            lines.append(_text(final["error"]))
    else:
        lines.append("Not run. Development and repeatedly used validation scores do not establish performance on unseen data.")
    lines.extend(["", "## Limits and Next Steps", "", "- Scores are comparable only within the same frozen evaluation version. A saturated benchmark calls for a new, harder version, not claims of general reliability.", "- Review records identify who approved the evidence; approval is not an independent proof of correctness.", "- Keep final questions outside optimization feedback. The local workflow is not an access-control sandbox and cannot prove that a user or agent never read them.", "- Inspect per-task losses and partial relevance coverage before reusing a combination. A trace labeled passed can still omit relevant results.", "- Nothing is merged into the main branch automatically.", "", "## Evidence Index", ""])
    for path, value in records:
        lines.append(f"- [{path}]({path}): {_text(value.get('status'))}, score {_score(value.get('score'))}.")
    return "\n".join(lines) + "\n"


def save_report(store) -> str:
    report = build_report(store)
    atomic_write_text(store.state_dir / "report.md", report)
    return report