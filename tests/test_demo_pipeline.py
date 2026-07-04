from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dynamic_workflows_agent.demo_pipeline import (
    choose_primary_run,
    generate_demo,
    load_run_records,
    select_session_runs,
)


class DemoPipelineTests(unittest.TestCase):
    def test_generates_demo_from_session_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            output_dir = root / "demo"

            _write_run(
                runs_dir,
                run_id="run-a",
                goal="分析当下潜在的投资机会",
                status="running",
                created_at="2026-07-04T10:00:00+00:00",
                updated_at="2026-07-04T10:10:00+00:00",
                final=False,
                claim="run-a macro scan claim",
                verdict="disputed",
            )
            _write_run(
                runs_dir,
                run_id="run-b",
                goal="分析当下潜在的投资机会",
                status="done",
                created_at="2026-07-04T10:20:00+00:00",
                updated_at="2026-07-04T10:40:00+00:00",
                final=True,
                claim="run-b cross-asset synthesis claim",
                verdict="insufficient_evidence",
            )
            _write_run(
                runs_dir,
                run_id="run-other",
                goal="unrelated task",
                status="done",
                created_at="2026-07-04T09:00:00+00:00",
                updated_at="2026-07-04T09:10:00+00:00",
                final=True,
                claim="unrelated claim",
                verdict="accepted",
            )

            records = load_run_records(runs_dir)
            selected = select_session_runs(records, query="投资机会", session_id=None)

            self.assertEqual([record.run_id for record in selected], ["run-a", "run-b"])
            self.assertEqual(choose_primary_run(selected).run_id, "run-b")

            _write_text(output_dir / "artifacts" / "plan-stale-round-1.json", "{}")
            generate_demo(selected, output_dir=output_dir, title="Demo", query="投资机会")

            timeline = (output_dir / "01-session-timeline.md").read_text(encoding="utf-8")
            planner = (output_dir / "02-planner-decisions.md").read_text(encoding="utf-8")
            workers = (output_dir / "03-worker-findings.md").read_text(encoding="utf-8")
            verifiers = (output_dir / "04-verifier-decisions.md").read_text(encoding="utf-8")
            final_report = (output_dir / "final-report.md").read_text(encoding="utf-8")
            session = json.loads((output_dir / "artifacts" / "session.json").read_text(encoding="utf-8"))

            self.assertIn("run-a", timeline)
            self.assertIn("run-b", timeline)
            self.assertIn("run-a", planner)
            self.assertIn("run-b", planner)
            self.assertIn("run-a macro scan claim", workers)
            self.assertIn("run-b cross-asset synthesis claim", workers)
            self.assertIn("disputed", verifiers)
            self.assertIn("insufficient_evidence", verifiers)
            self.assertIn("Source run: `run-b`", final_report)
            self.assertEqual(session["session"]["run_count"], 2)
            self.assertEqual(session["session"]["primary_run_id"], "run-b")
            self.assertFalse((output_dir / "artifacts" / "plan-stale-round-1.json").exists())


def _write_run(
    runs_dir: Path,
    *,
    run_id: str,
    goal: str,
    status: str,
    created_at: str,
    updated_at: str,
    final: bool,
    claim: str,
    verdict: str,
) -> None:
    run_dir = runs_dir / run_id
    (run_dir / "plans").mkdir(parents=True)
    (run_dir / "findings").mkdir()
    (run_dir / "verifications").mkdir()
    _write_json(
        run_dir / "state.json",
        {
            "run_id": run_id,
            "goal": goal,
            "status": status,
            "current_round": 1,
            "completed_subtasks": ["round-1:T1"],
            "completed_verifications": ["round-1:V1"],
            "created_at": created_at,
            "updated_at": updated_at,
        },
    )
    _write_json(
        run_dir / "plans" / "round-1.json",
        {
            "goal": goal,
            "success_criteria": ["identify opportunities", "state evidence gaps"],
            "subtasks": [
                {
                    "id": "T1",
                    "title": "Cross-asset opportunity scan",
                    "agent_role": "macro analyst",
                    "prompt": "Find signals and risks.",
                }
            ],
            "parallel_groups": [{"id": "G1", "subtask_ids": ["T1"], "max_concurrency": 1}],
            "verification_steps": [
                {"id": "V1", "target_subtask_ids": ["T1"], "mode": "refute", "prompt": "Check evidence."}
            ],
            "convergence_policy": {
                "max_rounds": 3,
                "min_confidence": 0.75,
                "require_no_critical_disputes": True,
            },
        },
    )
    _write_json(
        run_dir / "findings" / "round-1-T1.json",
        {
            "id": "F-T1",
            "subtask_id": "T1",
            "agent_role": "macro analyst",
            "claim": claim,
            "evidence": ["evidence one"],
            "confidence": 0.6,
            "limitations": ["sample limitation"],
            "recommended_next_steps": ["verify live data"],
            "tool_results": [{"tool_name": "web_search", "ok": True, "summary": "sample"}],
        },
    )
    _write_json(
        run_dir / "verifications" / "round-1-V1.json",
        {
            "id": "V1",
            "mode": "refute",
            "verdict": verdict,
            "needs_followup": verdict != "accepted",
            "confidence": 0.5,
            "target_subtask_ids": ["T1"],
            "issues": ["sample issue"],
            "counterarguments": ["sample counterargument"],
        },
    )
    _write_text(
        run_dir / "events.jsonl",
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": created_at,
                        "kind": "created",
                        "message": f"created {run_id}",
                        "data": {"round_index": 1},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": updated_at,
                        "kind": "verify_done",
                        "message": f"verified {run_id}",
                        "data": {"round_index": 1, "verdict": verdict},
                    }
                ),
            ]
        ),
    )
    if final:
        _write_text(run_dir / "final.md", "# Final Report\n\nSession final output.\n")


def _write_json(path: Path, value: dict[str, object]) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
