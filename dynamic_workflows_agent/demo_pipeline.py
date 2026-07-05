from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MAJOR_EVENT_KINDS = {
    "created",
    "planning",
    "planned",
    "resume",
    "round_start",
    "workers_start",
    "worker_done",
    "verify_start",
    "verify_done",
    "iterate",
    "max_rounds",
    "synthesizing",
    "complete",
    "cancelled",
    "llm_request_error",
}


GENERATED_MARKDOWN_FILES = {
    "README.md",
    "01-session-timeline.md",
    "02-planner-decisions.md",
    "03-worker-findings.md",
    "04-verifier-decisions.md",
    "05-convergence-and-resume.md",
    "final-report.md",
}


@dataclass(slots=True)
class RunRecord:
    run_id: str
    path: Path
    goal: str
    status: str
    created_at: str
    updated_at: str
    current_round: int | None
    completed_subtasks: int
    completed_verifications: int
    has_final: bool
    session_id: str
    session_title: str
    session_turn: int | None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a curated demo from checkpointed workflow runs.")
    parser.add_argument("--runs-dir", default="runs", help="Directory containing workflow run checkpoints.")
    parser.add_argument("--query", help="Select all runs whose goal/final report contains this text.")
    parser.add_argument("--session-id", help="Select runs with this explicit session_id in state.json.")
    parser.add_argument(
        "--output",
        help="Exact output directory. Defaults to <output-root>/<session_id-or-inferred-session-key>.",
    )
    parser.add_argument(
        "--output-root",
        default="docs/demos",
        help="Root directory used when --output is omitted.",
    )
    parser.add_argument("--title", default="Investment Opportunities Dynamic Workflow Demo", help="Demo title.")
    parser.add_argument("--list-sessions", action="store_true", help="List inferred sessions and exit.")
    args = parser.parse_args(argv)

    runs_dir = Path(args.runs_dir)
    records = load_run_records(runs_dir)
    if args.list_sessions:
        print(format_session_list(records))
        return 0

    selected = select_session_runs(records, query=args.query, session_id=args.session_id)
    if not selected:
        raise SystemExit("no matching runs found; pass --query, --session-id, or use --list-sessions")

    output_dir = resolve_output_dir(
        selected,
        output=args.output,
        output_root=Path(args.output_root),
        query=args.query or args.session_id or "",
    )
    generate_demo(selected, output_dir=output_dir, title=args.title, query=args.query or args.session_id or "")
    print(f"generated demo for {len(selected)} run(s): {output_dir}")
    return 0


def load_run_records(runs_dir: Path) -> list[RunRecord]:
    records: list[RunRecord] = []
    if not runs_dir.exists():
        return records
    for state_path in sorted(runs_dir.glob("*/state.json")):
        try:
            state = _read_json(state_path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        run_dir = state_path.parent
        run_id = str(state.get("run_id") or run_dir.name)
        completed_subtasks = state.get("completed_subtasks")
        completed_verifications = state.get("completed_verifications")
        session_turn = state.get("session_turn")
        records.append(
            RunRecord(
                run_id=run_id,
                path=run_dir,
                goal=str(state.get("goal") or ""),
                status=str(state.get("status") or ""),
                created_at=str(state.get("created_at") or ""),
                updated_at=str(state.get("updated_at") or ""),
                current_round=state.get("current_round") if isinstance(state.get("current_round"), int) else None,
                completed_subtasks=len(completed_subtasks) if isinstance(completed_subtasks, list) else 0,
                completed_verifications=(
                    len(completed_verifications) if isinstance(completed_verifications, list) else 0
                ),
                has_final=(run_dir / "final.md").exists(),
                session_id=str(state.get("session_id") or ""),
                session_title=str(state.get("session_title") or ""),
                session_turn=session_turn if isinstance(session_turn, int) else None,
            )
        )
    return records


def select_session_runs(records: list[RunRecord], *, query: str | None, session_id: str | None) -> list[RunRecord]:
    if session_id:
        selected = [record for record in records if record.session_id == session_id]
    elif query:
        needle = query.lower()
        selected = [record for record in records if _run_matches_query(record, needle)]
    else:
        return []
    return sorted(selected, key=lambda record: (record.created_at, record.run_id))


def resolve_output_dir(
    records: list[RunRecord],
    *,
    output: str | None,
    output_root: Path,
    query: str,
) -> Path:
    if output:
        return Path(output)
    session_key = _common_value(record.session_id for record in records)
    if not session_key:
        session_key = _common_value(record.session_title for record in records)
    if not session_key:
        primary = choose_primary_run(records)
        session_key = query or primary.goal or primary.run_id
    return output_root / _path_segment(session_key)


def generate_demo(records: list[RunRecord], *, output_dir: Path, title: str, query: str) -> None:
    _clean_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    primary = choose_primary_run(records)
    session = build_session_summary(records, primary=primary, query=query)
    run_details = build_run_details(records, primary=primary)
    events = load_major_events(records)

    _write_text(output_dir / "README.md", render_demo_readme(title, session, primary))
    _write_text(output_dir / "01-session-timeline.md", render_session_timeline(title, session, events))
    _write_text(output_dir / "02-planner-decisions.md", render_planner_decisions(title, primary, run_details))
    _write_text(output_dir / "03-worker-findings.md", render_worker_findings(title, primary, run_details))
    _write_text(output_dir / "04-verifier-decisions.md", render_verifier_decisions(title, primary, run_details))
    _write_text(
        output_dir / "05-convergence-and-resume.md",
        render_convergence_and_resume(title, primary, events, _primary_plans(run_details, primary)),
    )
    _write_text(output_dir / "final-report.md", render_final_report(primary))

    all_findings = [finding for detail in run_details for finding in detail["findings"]]
    all_verifications = [verification for detail in run_details for verification in detail["verifications"]]
    _write_json(artifacts_dir / "session.json", {"session": session, "runs": run_details})
    _write_json(artifacts_dir / "session-runs.json", {"runs": [run_record_to_dict(record) for record in records]})
    _write_json(artifacts_dir / "worker-findings-summary.json", {"findings": all_findings})
    _write_json(artifacts_dir / "verifier-decisions.json", {"verifications": all_verifications})
    _write_jsonl(artifacts_dir / "event-timeline.jsonl", events)
    for detail in run_details:
        run_id = str(detail["run"]["run_id"])
        run_path = Path(str(detail["run"]["path"]))
        for plan in detail["plans"]:
            source = run_path / "plans" / f"round-{plan['round_index']}.json"
            if source.exists():
                _write_json(artifacts_dir / f"plan-{run_id}-round-{plan['round_index']}.json", _read_json(source))
        for verification in detail["verifications"]:
            source = run_path / "verifications" / verification["file"]
            if source.exists():
                _write_json(artifacts_dir / f"verification-{run_id}-{verification['file']}", _read_json(source))


def _clean_output_dir(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    for name in GENERATED_MARKDOWN_FILES:
        path = output_dir / name
        if path.is_file():
            path.unlink()
    artifacts_dir = output_dir / "artifacts"
    if not artifacts_dir.exists():
        return
    for path in artifacts_dir.iterdir():
        if path.is_file() and path.suffix in {".json", ".jsonl"}:
            path.unlink()


def choose_primary_run(records: list[RunRecord]) -> RunRecord:
    return max(
        records,
        key=lambda record: (
            record.has_final,
            record.status == "done",
            record.completed_subtasks,
            record.completed_verifications,
            record.updated_at,
            record.run_id,
        ),
    )


def build_session_summary(records: list[RunRecord], *, primary: RunRecord, query: str) -> dict[str, Any]:
    return {
        "query": query,
        "session_id": _common_value(record.session_id for record in records),
        "session_title": _common_value(record.session_title for record in records),
        "primary_run_id": primary.run_id,
        "run_count": len(records),
        "started_at": min((record.created_at for record in records if record.created_at), default=""),
        "ended_at": max((record.updated_at for record in records if record.updated_at), default=""),
        "runs": [run_record_to_dict(record) for record in records],
    }


def build_run_details(records: list[RunRecord], *, primary: RunRecord) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for record in records:
        plans = load_plan_summaries(record.path)
        findings = load_finding_summaries(record.path)
        verifications = load_verification_summaries(record.path)
        for collection in (plans, findings, verifications):
            for item in collection:
                item["run_id"] = record.run_id
                item["is_primary"] = record.run_id == primary.run_id
        details.append(
            {
                "run": run_record_to_dict(record),
                "is_primary": record.run_id == primary.run_id,
                "plans": plans,
                "findings": findings,
                "verifications": verifications,
            }
        )
    return details


def run_record_to_dict(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "goal": record.goal,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "current_round": record.current_round,
        "completed_subtasks": record.completed_subtasks,
        "completed_verifications": record.completed_verifications,
        "has_final": record.has_final,
        "path": str(record.path),
        "session_id": record.session_id,
        "session_title": record.session_title,
        "session_turn": record.session_turn,
    }


def load_plan_summaries(run_dir: Path) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for path in sorted((run_dir / "plans").glob("round-*.json"), key=_round_sort_key):
        try:
            plan = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        round_index = _round_from_name(path.name) or len(plans) + 1
        plans.append(
            {
                "round_index": round_index,
                "goal": plan.get("goal", ""),
                "success_criteria": _string_list(plan.get("success_criteria")),
                "subtasks": [
                    {
                        "id": str(item.get("id") or ""),
                        "title": str(item.get("title") or ""),
                        "agent_role": str(item.get("agent_role") or ""),
                        "depends_on": item.get("depends_on") if isinstance(item.get("depends_on"), list) else [],
                        "prompt": str(item.get("prompt") or ""),
                    }
                    for item in _dict_list(plan.get("subtasks"))
                ],
                "parallel_groups": [
                    {
                        "id": str(item.get("id") or ""),
                        "subtask_ids": item.get("subtask_ids") if isinstance(item.get("subtask_ids"), list) else [],
                        "max_concurrency": item.get("max_concurrency"),
                    }
                    for item in _dict_list(plan.get("parallel_groups"))
                ],
                "verification_steps": [
                    {
                        "id": str(item.get("id") or ""),
                        "mode": str(item.get("mode") or ""),
                        "target_subtask_ids": (
                            item.get("target_subtask_ids") if isinstance(item.get("target_subtask_ids"), list) else []
                        ),
                        "prompt": str(item.get("prompt") or ""),
                    }
                    for item in _dict_list(plan.get("verification_steps"))
                ],
                "convergence_policy": plan.get("convergence_policy") if isinstance(plan.get("convergence_policy"), dict) else {},
            }
        )
    return plans


def load_finding_summaries(run_dir: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in sorted((run_dir / "findings").glob("*.json"), key=_round_sort_key):
        try:
            finding = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        tool_results = finding.get("tool_results") if isinstance(finding.get("tool_results"), list) else []
        findings.append(
            {
                "file": path.name,
                "round_index": _round_from_name(path.name),
                "subtask_id": str(finding.get("subtask_id") or ""),
                "agent_role": str(finding.get("agent_role") or ""),
                "claim": str(finding.get("claim") or ""),
                "confidence": finding.get("confidence"),
                "evidence": _string_list(finding.get("evidence"))[:3],
                "limitations": _string_list(finding.get("limitations"))[:3],
                "recommended_next_steps": _string_list(finding.get("recommended_next_steps"))[:3],
                "tool_count": len(tool_results),
                "tool_ok_count": sum(1 for item in tool_results if isinstance(item, dict) and item.get("ok")),
                "tool_error_count": sum(1 for item in tool_results if isinstance(item, dict) and not item.get("ok")),
            }
        )
    return findings


def load_verification_summaries(run_dir: Path) -> list[dict[str, Any]]:
    verifications: list[dict[str, Any]] = []
    for path in sorted((run_dir / "verifications").glob("*.json"), key=_round_sort_key):
        try:
            verification = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        verifications.append(
            {
                "file": path.name,
                "round_index": _round_from_name(path.name),
                "id": str(verification.get("id") or ""),
                "mode": str(verification.get("mode") or ""),
                "verdict": str(verification.get("verdict") or ""),
                "needs_followup": bool(verification.get("needs_followup")),
                "confidence": verification.get("confidence"),
                "target_subtask_ids": (
                    verification.get("target_subtask_ids")
                    if isinstance(verification.get("target_subtask_ids"), list)
                    else []
                ),
                "issues": _string_list(verification.get("issues"))[:8],
                "counterarguments": _string_list(verification.get("counterarguments"))[:5],
            }
        )
    return verifications


def load_major_events(records: list[RunRecord]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        path = record.path / "events.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("kind") not in MAJOR_EVENT_KINDS:
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            events.append(
                {
                    "run_id": record.run_id,
                    "timestamp": event.get("timestamp", ""),
                    "kind": event.get("kind", ""),
                    "message": event.get("message", ""),
                    "round_index": data.get("round_index"),
                    "group_id": data.get("group_id"),
                    "worker_count": data.get("worker_count"),
                    "step_id": data.get("step_id"),
                    "verdict": data.get("verdict"),
                    "needs_followup": data.get("needs_followup"),
                    "next_round_index": data.get("next_round_index"),
                    "error": data.get("error"),
                }
            )
    return sorted(events, key=lambda item: (str(item.get("timestamp") or ""), str(item.get("run_id") or "")))


def render_demo_readme(title: str, session: dict[str, Any], primary: RunRecord) -> str:
    return "\n".join(
        [
            f"# {title}",
            "",
            "> This demo is a curated case study generated from checkpointed workflow runs. It demonstrates",
            "> dynamic workflow orchestration, not investment advice.",
            "",
            "## Session",
            "",
            f"- Query/session selector: `{session.get('query') or '-'}`",
            f"- Session ID: `{session.get('session_id') or '-'}`",
            f"- Session title: {session.get('session_title') or '-'}",
            f"- Primary run: `{primary.run_id}`",
            f"- Runs included: {session.get('run_count')}",
            f"- Time window: {_short_time(session.get('started_at'))} to {_short_time(session.get('ended_at'))}",
            f"- Final report available: {'yes' if primary.has_final else 'no'}",
            "",
            "## Reading Order",
            "",
            "1. [Session timeline](01-session-timeline.md)",
            "2. [Planner decisions](02-planner-decisions.md)",
            "3. [Worker findings](03-worker-findings.md)",
            "4. [Verifier decisions](04-verifier-decisions.md)",
            "5. [Convergence and resume](05-convergence-and-resume.md)",
            "6. [Final report](final-report.md)",
            "",
            "## Why This Demo Is Useful",
            "",
            "- It shows multiple attempts grouped as one inferred session.",
            "- It shows the planner changing the workflow shape between rounds.",
            "- It shows independent worker roles and their intermediate claims.",
            "- It shows verifier/refuter decisions blocking clean convergence.",
            "- It shows final synthesis carrying unresolved evidence risks forward.",
            "",
        ]
    )


def render_session_timeline(title: str, session: dict[str, Any], events: list[dict[str, Any]]) -> str:
    lines = [
        f"# {title}: Session Timeline",
        "",
        "## Runs Included",
        "",
        "| Run | Status | Round | Findings | Verifications | Final | Goal |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for record in session.get("runs", []):
        run_label = f"`{record.get('run_id', '')}`"
        if record.get("session_turn"):
            run_label = f"{run_label}<br>turn {record.get('session_turn')}"
        lines.append(
            "| {run_id} | {status} | {round} | {findings} | {verifications} | {final} | {goal} |".format(
                run_id=run_label,
                status=record.get("status", ""),
                round=record.get("current_round") or "-",
                findings=record.get("completed_subtasks") or 0,
                verifications=record.get("completed_verifications") or 0,
                final="yes" if record.get("has_final") else "no",
                goal=_escape_table(str(record.get("goal") or "")),
            )
        )
    lines.extend(["", "## Major Events", "", "| Time | Run | Kind | Round | Message |", "|---|---|---|---:|---|"])
    for event in events:
        round_text = event.get("round_index") if event.get("round_index") is not None else "-"
        lines.append(
            f"| {_short_time(event.get('timestamp'))} | `{event.get('run_id')}` | "
            f"{event.get('kind')} | {round_text} | {_escape_table(str(event.get('message') or ''))} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_planner_decisions(title: str, primary: RunRecord, run_details: list[dict[str, Any]]) -> str:
    lines = [
        f"# {title}: Planner Decisions",
        "",
        f"Primary run: `{primary.run_id}`",
        "",
        "The application code does not contain an investment-analysis workflow. The planner generated the",
        "roles, groups, dependencies, verifier targets, and convergence policy at runtime.",
        "",
        "## Session Plan Coverage",
        "",
        "| Run | Primary | Status | Plan Rounds | Workers | Verifiers | Final |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for detail in run_details:
        run = detail["run"]
        lines.append(
            f"| `{run['run_id']}` | {'yes' if detail['is_primary'] else 'no'} | {run.get('status') or '-'} | "
            f"{len(detail['plans'])} | {sum(len(plan.get('subtasks', [])) for plan in detail['plans'])} | "
            f"{sum(len(plan.get('verification_steps', [])) for plan in detail['plans'])} | "
            f"{'yes' if run.get('has_final') else 'no'} |"
        )
    lines.append("")
    if not any(detail["plans"] for detail in run_details):
        lines.extend(["No round-specific plans were found in the selected session.", ""])
        return "\n".join(lines)

    for detail in run_details:
        run = detail["run"]
        plans = detail["plans"]
        if not plans:
            continue
        lines.extend(
            [
                f"## Run {run['run_id']}{' (primary)' if detail['is_primary'] else ''}",
                "",
                f"Goal: {run.get('goal') or '-'}",
                "",
            ]
        )
        for plan in plans:
            _append_plan_detail(lines, plan)
        if detail["is_primary"] and primary.current_round and primary.current_round > max(
            plan["round_index"] for plan in plans
        ):
            lines.extend(
                [
                    "### Missing Round Plan Note",
                    "",
                    f"The state reached round {primary.current_round}, but only round-specific plan files through "
                    f"round {max(plan['round_index'] for plan in plans)} were found. The demo keeps this visible "
                    "because it is relevant to resume behavior and checkpoint correctness.",
                    "",
                ]
            )
    return "\n".join(lines)


def _append_plan_detail(lines: list[str], plan: dict[str, Any]) -> None:
    lines.extend(
        [
            f"### Round {plan['round_index']}",
            "",
            f"Plan goal: {plan.get('goal') or '-'}",
            "",
            "#### Success Criteria",
            "",
        ]
    )
    lines.extend(_bullet(plan.get("success_criteria"), empty="- none"))
    lines.extend(["", "#### Parallel Groups", "", "| Group | Concurrency | Fan-out |", "|---|---:|---|"])
    for group in plan.get("parallel_groups", []):
        lines.append(
            f"| {group.get('id')} | {group.get('max_concurrency')} | "
            f"{', '.join(str(item) for item in group.get('subtask_ids', []))} |"
        )
    lines.extend(["", "#### Worker Roles", "", "| ID | Role | Title | Depends On |", "|---|---|---|---|"])
    for subtask in plan.get("subtasks", []):
        depends = ", ".join(str(item) for item in subtask.get("depends_on", [])) or "-"
        lines.append(
            f"| {subtask.get('id')} | {_escape_table(subtask.get('agent_role', ''))} | "
            f"{_escape_table(subtask.get('title', ''))} | {depends} |"
        )
    lines.extend(["", "#### Verification Steps", "", "| ID | Mode | Targets | Prompt |", "|---|---|---|---|"])
    for step in plan.get("verification_steps", []):
        lines.append(
            f"| {step.get('id')} | {step.get('mode')} | "
            f"{', '.join(str(item) for item in step.get('target_subtask_ids', []))} | "
            f"{_escape_table(_truncate(step.get('prompt', ''), 220))} |"
        )
    policy = plan.get("convergence_policy") or {}
    lines.extend(
        [
            "",
            "#### Convergence Policy",
            "",
            f"- `max_rounds`: {policy.get('max_rounds', '-')}",
            f"- `min_confidence`: {policy.get('min_confidence', '-')}",
            f"- `require_no_critical_disputes`: {policy.get('require_no_critical_disputes', '-')}",
            "",
        ]
    )


def _primary_plans(run_details: list[dict[str, Any]], primary: RunRecord) -> list[dict[str, Any]]:
    for detail in run_details:
        if detail["run"]["run_id"] == primary.run_id:
            return detail["plans"]
    return []


def _all_findings(run_details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [finding for detail in run_details for finding in detail["findings"]]


def _all_verifications(run_details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [verification for detail in run_details for verification in detail["verifications"]]



def render_worker_findings(title: str, primary: RunRecord, run_details: list[dict[str, Any]]) -> str:
    findings = _all_findings(run_details)
    lines = [f"# {title}: Worker Findings", "", f"Primary run: `{primary.run_id}`", ""]
    if not findings:
        lines.extend(["No findings were found in the selected session.", ""])
        return "\n".join(lines)
    lines.extend(["## Session Worker Coverage", "", "| Run | Primary | Findings | Average Confidence | Tool Calls |", "|---|---|---:|---:|---:|"])
    for detail in run_details:
        run_findings = detail["findings"]
        confidence_values = [item["confidence"] for item in run_findings if isinstance(item.get("confidence"), (int, float))]
        avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
        lines.append(
            f"| `{detail['run']['run_id']}` | {'yes' if detail['is_primary'] else 'no'} | {len(run_findings)} | "
            f"{avg_confidence:.2f} | {sum(int(item.get('tool_count') or 0) for item in run_findings)} |"
        )
    lines.append("")
    for detail in run_details:
        run_findings = detail["findings"]
        if not run_findings:
            continue
        lines.extend([f"## Run {detail['run']['run_id']}{' (primary)' if detail['is_primary'] else ''}", ""])
        for round_index in sorted({item.get("round_index") for item in run_findings if item.get("round_index") is not None}):
            round_findings = [item for item in run_findings if item.get("round_index") == round_index]
            lines.extend(
                [
                    f"### Round {round_index}",
                    "",
                    "| Worker | Role | Confidence | Tools | Claim | Key Limitation |",
                    "|---|---|---:|---:|---|---|",
                ]
            )
            for finding in round_findings:
                limitations = finding.get("limitations") or []
                lines.append(
                    f"| {finding.get('subtask_id')} | {_escape_table(finding.get('agent_role', ''))} | "
                    f"{finding.get('confidence', '-')} | {finding.get('tool_count', 0)} | "
                    f"{_escape_table(_truncate(finding.get('claim', ''), 220))} | "
                    f"{_escape_table(_truncate(limitations[0] if limitations else '-', 180))} |"
                )
            lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- The table is intentionally compressed. Full tool payloads are not copied into the demo.",
            "- `tool_count` shows how many local tools the worker used; see artifacts for summary JSON.",
            "- Low confidence values are part of the demo: verifier decisions use them to trigger follow-up.",
            "",
        ]
    )
    return "\n".join(lines)


def render_verifier_decisions(title: str, primary: RunRecord, run_details: list[dict[str, Any]]) -> str:
    verifications = _all_verifications(run_details)
    lines = [f"# {title}: Verifier Decisions", "", f"Primary run: `{primary.run_id}`", ""]
    if not verifications:
        lines.extend(["No verifier decisions were found in the selected session.", ""])
        return "\n".join(lines)
    lines.extend(
        [
            "## Session Verifier Coverage",
            "",
            "| Run | Primary | Round | Verdict | Follow-up | Confidence | Targets |",
            "|---|---|---:|---|---|---:|---:|",
        ]
    )
    for verification in verifications:
        lines.append(
            f"| `{verification.get('run_id')}` | {'yes' if verification.get('is_primary') else 'no'} | "
            f"{verification.get('round_index')} | {verification.get('verdict')} | "
            f"{verification.get('needs_followup')} | {verification.get('confidence')} | "
            f"{len(verification.get('target_subtask_ids', []))} |"
        )
    lines.append("")
    for detail in run_details:
        run_verifications = detail["verifications"]
        if not run_verifications:
            continue
        lines.extend([f"## Run {detail['run']['run_id']}{' (primary)' if detail['is_primary'] else ''}", ""])
        for verification in run_verifications:
            lines.extend(
                [
                    f"### Round {verification.get('round_index')}: {verification.get('verdict')}",
                    "",
                    "#### Issues",
                    "",
                ]
            )
            lines.extend(_bullet(verification.get("issues"), empty="- none"))
            lines.extend(["", "#### Counterarguments", ""])
            lines.extend(_bullet(verification.get("counterarguments"), empty="- none"))
            lines.append("")
    return "\n".join(lines)


def render_convergence_and_resume(
    title: str,
    primary: RunRecord,
    events: list[dict[str, Any]],
    plans: list[dict[str, Any]],
) -> str:
    primary_events = [event for event in events if event.get("run_id") == primary.run_id]
    convergence_events = [
        event
        for event in primary_events
        if event.get("kind") in {"iterate", "resume", "max_rounds", "synthesizing", "complete", "llm_request_error"}
    ]
    lines = [
        f"# {title}: Convergence and Resume",
        "",
        f"Primary run: `{primary.run_id}`",
        "",
        "## How Convergence Is Decided",
        "",
        "- Any finding below the plan's `min_confidence` contributes to unresolved risk.",
        "- Any verifier result with `needs_followup=true` contributes to unresolved risk.",
        "- Verifier verdicts `disputed`, `rejected`, or `insufficient_evidence` block clean convergence.",
        "- If unresolved risks remain and `max_rounds` has not been reached, the planner is asked for a follow-up plan.",
        "- If `max_rounds` is reached, synthesis proceeds with unresolved risks carried into the final report.",
        "",
        "## Session Events",
        "",
        "| Time | Kind | Round | Message |",
        "|---|---|---:|---|",
    ]
    for event in convergence_events:
        lines.append(
            f"| {_short_time(event.get('timestamp'))} | {event.get('kind')} | "
            f"{event.get('round_index') or '-'} | {_escape_table(str(event.get('message') or event.get('error') or ''))} |"
        )
    if not convergence_events:
        lines.append("| - | - | - | No convergence or resume events were found. |")
    lines.extend(["", "## Resume/Plan Integrity Note", ""])
    if primary.current_round and plans and primary.current_round > max(plan["round_index"] for plan in plans):
        lines.extend(
            [
                f"The run reached round {primary.current_round}, but only `{len(plans)}` round-specific plan files exist.",
                "This is preserved in the demo because it reveals a real checkpoint/resume edge case:",
                "resume can continue with the latest saved plan even when a new round-specific plan file is missing.",
                "",
            ]
        )
    else:
        lines.append("Round-specific plan files are present for the observed primary run rounds.")
        lines.append("")
    return "\n".join(lines)


def render_final_report(primary: RunRecord) -> str:
    final_path = primary.path / "final.md"
    if not final_path.exists():
        return f"# Final Report\n\nNo final report exists for `{primary.run_id}`.\n"
    content = final_path.read_text(encoding="utf-8", errors="replace").strip()
    return (
        "# Final Report\n\n"
        "> Demo note: this is generated output from the workflow and is not investment advice. "
        "The verifier explicitly found unresolved evidence quality issues.\n\n"
        f"Source run: `{primary.run_id}`\n\n"
        f"{content}\n"
    )


def format_session_list(records: list[RunRecord]) -> str:
    groups: dict[str, list[RunRecord]] = {}
    for record in records:
        key = record.session_id or _normalize_goal(record.goal)
        groups.setdefault(key, []).append(record)
    lines = ["Inferred sessions:", ""]
    for key, group in sorted(groups.items(), key=lambda item: (item[1][0].created_at, item[0])):
        primary = choose_primary_run(group)
        title = _common_value(record.session_title for record in group) or primary.goal
        lines.append(f"- {key or '(empty)'}")
        lines.append(f"  runs: {len(group)}; primary: {primary.run_id}; title: {title}")
    return "\n".join(lines)


def _run_matches_query(record: RunRecord, needle: str) -> bool:
    if needle in record.goal.lower():
        return True
    final_path = record.path / "final.md"
    if final_path.exists():
        return needle in final_path.read_text(encoding="utf-8", errors="replace").lower()
    return False


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    _write_text(path, "\n".join(json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values))


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bullet(values: Any, *, empty: str) -> list[str]:
    strings = _string_list(values)
    if not strings:
        return [empty]
    return [f"- {item}" for item in strings]


def _round_from_name(name: str) -> int | None:
    match = re.search(r"round-(\d+)", name)
    if not match:
        return None
    return int(match.group(1))


def _round_sort_key(path: Path) -> tuple[int, str]:
    return (_round_from_name(path.name) or 0, path.name)


def _normalize_goal(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def _path_segment(value: str) -> str:
    text = re.sub(r"\s+", "-", value.strip())
    text = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip(".-")
    return text[:120] or "session"


def _common_value(values: Any) -> str:
    strings = [str(value) for value in values if str(value or "").strip()]
    if not strings:
        return ""
    first = strings[0]
    if all(value == first for value in strings):
        return first
    return ""


def _short_time(value: Any) -> str:
    text = str(value or "")
    if not text:
        return "-"
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _truncate(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
