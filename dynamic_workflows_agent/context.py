from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFAULT_TEXT_LIMIT = 600
DEFAULT_LIST_LIMIT = 5
ROLLING_SUMMARY_LIMIT = 12000


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_conversation(
    conversation: list[dict[str, str]] | None,
    *,
    max_messages: int = 8,
    max_chars: int = 5000,
) -> list[dict[str, str]]:
    if not conversation:
        return []
    compacted: list[dict[str, str]] = []
    for item in conversation[-max_messages:]:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if not role or not content:
            continue
        compacted.append({"role": role, "content": truncate_text(content, max_chars // max_messages)})
    return compacted


def compact_session_for_prompt(session: dict[str, Any] | None, *, max_turns: int = 5) -> dict[str, Any]:
    if not session:
        return {}
    turns = session.get("turns") if isinstance(session.get("turns"), list) else []
    return {
        "session_id": session.get("session_id") or "",
        "session_title": session.get("session_title") or "",
        "rolling_summary": truncate_text(session.get("rolling_summary") or "", ROLLING_SUMMARY_LIMIT),
        "recent_turns": [
            {
                "run_id": item.get("run_id") or "",
                "session_turn": item.get("session_turn"),
                "goal": truncate_text(item.get("goal") or "", 500),
                "status": item.get("status") or "",
                "final_summary": truncate_text(item.get("final_summary") or "", 2200),
            }
            for item in turns[-max_turns:]
            if isinstance(item, dict)
        ],
    }


def make_session_turn_summary(
    *,
    state: dict[str, Any],
    report_markdown: str,
    max_report_chars: int = 3200,
) -> dict[str, Any]:
    return {
        "run_id": state.get("run_id") or "",
        "session_id": state.get("session_id") or "",
        "session_turn": state.get("session_turn"),
        "goal": state.get("goal") or "",
        "status": state.get("status") or "",
        "created_at": state.get("created_at") or "",
        "updated_at": state.get("updated_at") or "",
        "final_summary": summarize_markdown(report_markdown, max_chars=max_report_chars),
    }


def merge_session_turn(session: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    now = utc_now_iso()
    run_id = str(turn.get("run_id") or "")
    turns = [item for item in session.get("turns", []) if isinstance(item, dict)]
    if run_id:
        turns = [item for item in turns if item.get("run_id") != run_id]
    turns.append(turn)
    turns.sort(key=lambda item: (item.get("session_turn") or 0, item.get("created_at") or "", item.get("run_id") or ""))

    session["session_id"] = session.get("session_id") or turn.get("session_id") or ""
    session["session_title"] = session.get("session_title") or turn.get("goal") or ""
    session["updated_at"] = now
    session.setdefault("created_at", now)
    session["latest_run_id"] = run_id or session.get("latest_run_id") or ""
    session["turns"] = turns
    session["rolling_summary"] = _build_rolling_summary(turns)
    return session


def compact_findings(findings: list[Any], *, max_items: int = 80) -> list[dict[str, Any]]:
    return [compact_finding(item) for item in findings[:max_items]]


def compact_finding(value: Any) -> dict[str, Any]:
    data = value.to_dict() if hasattr(value, "to_dict") else value
    if not isinstance(data, dict):
        return {}
    tool_summary = compact_tool_results(data.get("tool_results"))
    return {
        "id": data.get("id") or "",
        "subtask_id": data.get("subtask_id") or "",
        "agent_role": truncate_text(data.get("agent_role") or "", 160),
        "claim": truncate_text(data.get("claim") or "", 900),
        "evidence": truncate_list(data.get("evidence"), item_limit=420, max_items=4),
        "confidence": data.get("confidence"),
        "limitations": truncate_list(data.get("limitations"), item_limit=360, max_items=3),
        "recommended_next_steps": truncate_list(data.get("recommended_next_steps"), item_limit=300, max_items=3),
        "tool_summary": tool_summary,
    }


def compact_verifications(verifications: list[Any], *, max_items: int = 40) -> list[dict[str, Any]]:
    return [compact_verification(item) for item in verifications[:max_items]]


def compact_verification(value: Any) -> dict[str, Any]:
    data = value.to_dict() if hasattr(value, "to_dict") else value
    if not isinstance(data, dict):
        return {}
    return {
        "id": data.get("id") or "",
        "target_subtask_ids": data.get("target_subtask_ids") if isinstance(data.get("target_subtask_ids"), list) else [],
        "mode": data.get("mode") or "",
        "verdict": data.get("verdict") or "",
        "issues": truncate_list(data.get("issues"), item_limit=420, max_items=6),
        "counterarguments": truncate_list(data.get("counterarguments"), item_limit=420, max_items=5),
        "needs_followup": bool(data.get("needs_followup")),
        "confidence": data.get("confidence"),
    }


def compact_tool_results(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {"count": 0, "ok": 0, "errors": 0, "tools": [], "samples": []}
    tools: list[str] = []
    samples: list[str] = []
    ok_count = 0
    error_count = 0
    for item in value:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or item.get("name") or "")
        if tool_name and tool_name not in tools:
            tools.append(tool_name)
        if item.get("ok"):
            ok_count += 1
        else:
            error_count += 1
        sample = item.get("summary") or item.get("error") or ""
        if sample and len(samples) < 4:
            samples.append(truncate_text(sample, 260))
    return {
        "count": ok_count + error_count,
        "ok": ok_count,
        "errors": error_count,
        "tools": tools[:8],
        "samples": samples,
    }


def summarize_markdown(markdown: str, *, max_chars: int) -> str:
    text = normalize_whitespace(markdown)
    if len(text) <= max_chars:
        return text

    selected: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("- ") or line.startswith("* ") or line[:3].isdigit():
            selected.append(line)
        elif len(selected) < 8:
            selected.append(line)
        if len("\n".join(selected)) >= max_chars:
            break
    summary = normalize_whitespace("\n".join(selected))
    if not summary:
        summary = text
    return truncate_text(summary, max_chars)


def truncate_list(value: Any, *, item_limit: int, max_items: int = DEFAULT_LIST_LIMIT) -> list[str]:
    if not isinstance(value, list):
        return []
    return [truncate_text(item, item_limit) for item in value[:max_items] if str(item).strip()]


def truncate_text(value: Any, limit: int = DEFAULT_TEXT_LIMIT) -> str:
    text = normalize_whitespace(str(value or ""))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def normalize_whitespace(value: str) -> str:
    return " ".join(value.replace("\r", "\n").split())


def _build_rolling_summary(turns: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for item in turns:
        turn_no = item.get("session_turn") or "?"
        goal = truncate_text(item.get("goal") or "", 360)
        final_summary = truncate_text(item.get("final_summary") or "", 2400)
        sections.append(f"Turn {turn_no}: {goal}\nResult summary: {final_summary}")
    summary = "\n\n".join(sections)
    if len(summary) <= ROLLING_SUMMARY_LIMIT:
        return summary
    return "... earlier session context omitted ...\n" + summary[-ROLLING_SUMMARY_LIMIT:]
