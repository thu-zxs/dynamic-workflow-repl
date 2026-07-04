from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .models import Event, Finding, VerificationResult, WorkflowPlan, utc_now_iso


class CheckpointStore:
    def __init__(self, root: str | Path = "runs") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create_run(self, goal: str) -> dict[str, Any]:
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "findings").mkdir()
        (run_dir / "verifications").mkdir()
        (run_dir / "plans").mkdir()
        state = {
            "run_id": run_id,
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

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

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
            if round_path.exists():
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
