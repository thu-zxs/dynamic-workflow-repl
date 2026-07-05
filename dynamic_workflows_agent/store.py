from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .context import make_session_turn_summary, merge_session_turn
from .models import Event, Finding, VerificationResult, WorkflowPlan, utc_now_iso


class CheckpointStore:
    def __init__(self, root: str | Path = "runs") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def new_session_id(self) -> str:
        return f"session-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    def create_run(
        self,
        goal: str,
        *,
        session_id: str | None = None,
        session_title: str | None = None,
    ) -> dict[str, Any]:
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        session_id = session_id or self.new_session_id()
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "findings").mkdir()
        (run_dir / "verifications").mkdir()
        (run_dir / "plans").mkdir()
        state = {
            "run_id": run_id,
            "session_id": session_id,
            "session_title": session_title or goal[:120],
            "session_turn": self._next_session_turn(session_id),
            "goal": goal,
            "status": "created",
            "current_round": 1,
            "completed_subtasks": [],
            "completed_verifications": [],
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        self.save_state(run_id, state)
        return state

    def _next_session_turn(self, session_id: str) -> int:
        turns: list[int] = []
        for state in self.list_runs():
            if state.get("session_id") != session_id:
                continue
            turn = state.get("session_turn")
            if isinstance(turn, int):
                turns.append(turn)
        return max(turns, default=0) + 1

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def session_dir(self) -> Path:
        path = self.root / "_sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_path(self, session_id: str) -> Path:
        return self.session_dir() / f"{_safe_name(session_id)}.json"

    def state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state.json"

    def load_state(self, run_id: str) -> dict[str, Any]:
        return self._read_json(self.state_path(run_id))

    def save_state(self, run_id: str, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now_iso()
        self._write_json(self.state_path(run_id), state)

    def append_event(self, event: Event) -> None:
        path = self.run_dir(event.run_id) / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def save_plan(self, run_id: str, plan: WorkflowPlan, *, round_index: int) -> None:
        data = plan.to_dict()
        self._write_json(self.run_dir(run_id) / "plan.json", data)
        self._write_json(self.run_dir(run_id) / "plans" / f"round-{round_index}.json", data)

    def load_plan(self, run_id: str, *, round_index: int | None = None) -> WorkflowPlan:
        path = self.run_dir(run_id) / "plan.json"
        if round_index is not None:
            round_path = self.run_dir(run_id) / "plans" / f"round-{round_index}.json"
            if not round_path.exists():
                raise FileNotFoundError(round_path)
            path = round_path
        return WorkflowPlan.from_dict(self._read_json(path))

    def save_finding(self, run_id: str, finding: Finding, *, round_index: int) -> None:
        path = self.run_dir(run_id) / "findings" / f"round-{round_index}-{finding.subtask_id}.json"
        self._write_json(path, finding.to_dict())

    def load_findings(self, run_id: str) -> list[Finding]:
        directory = self.run_dir(run_id) / "findings"
        if not directory.exists():
            return []
        findings: list[Finding] = []
        for path in sorted(directory.glob("*.json")):
            findings.append(Finding.from_dict(self._read_json(path)))
        return findings

    def save_verification(self, run_id: str, result: VerificationResult, *, round_index: int) -> None:
        path = self.run_dir(run_id) / "verifications" / f"round-{round_index}-{result.id}.json"
        self._write_json(path, result.to_dict())

    def load_verifications(self, run_id: str) -> list[VerificationResult]:
        directory = self.run_dir(run_id) / "verifications"
        if not directory.exists():
            return []
        results: list[VerificationResult] = []
        for path in sorted(directory.glob("*.json")):
            results.append(VerificationResult.from_dict(self._read_json(path)))
        return results

    def save_final(self, run_id: str, report_markdown: str) -> None:
        self._write_text(self.run_dir(run_id) / "final.md", report_markdown)

    def load_final(self, run_id: str) -> str:
        return (self.run_dir(run_id) / "final.md").read_text(encoding="utf-8")

    def has_final(self, run_id: str) -> bool:
        return (self.run_dir(run_id) / "final.md").exists()

    def load_session(self, session_id: str) -> dict[str, Any]:
        if not session_id:
            return {}
        path = self.session_path(session_id)
        if not path.exists():
            return {
                "session_id": session_id,
                "session_title": "",
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "latest_run_id": "",
                "rolling_summary": "",
                "turns": [],
            }
        return self._read_json(path)

    def save_session(self, session: dict[str, Any]) -> None:
        session_id = str(session.get("session_id") or "")
        if not session_id:
            return
        self._write_json(self.session_path(session_id), session)

    def append_session_turn(self, run_id: str, report_markdown: str) -> None:
        state = self.load_state(run_id)
        session_id = str(state.get("session_id") or "")
        if not session_id:
            return
        session = self.load_session(session_id)
        if not session.get("session_title"):
            session["session_title"] = state.get("session_title") or state.get("goal") or ""
        turn = make_session_turn_summary(state=state, report_markdown=report_markdown)
        self.save_session(merge_session_turn(session, turn))

    def list_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for path in sorted(self.root.iterdir(), reverse=True):
            if not path.is_dir():
                continue
            state_path = path / "state.json"
            if not state_path.exists():
                continue
            try:
                runs.append(self._read_json(state_path))
            except (OSError, json.JSONDecodeError):
                continue
        return runs

    def _read_json(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return value

    def _write_json(self, path: Path, value: dict[str, Any]) -> None:
        self._write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)


def _safe_name(value: str) -> str:
    allowed = []
    for char in value:
        if char.isalnum() or char in {"-", "_", "."}:
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed).strip("._") or "session"
