from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dynamic_workflows_agent.demo_pipeline import (
    choose_primary_run,
    generate_demo,
    load_run_records,
    resolve_output_dir,
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
                session_id="session-investment",
                session_title="Investment session",
                session_turn=1,
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
                session_id="session-investment",
                session_title="Investment session",
                session_turn=2,
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
                session_id="session-other",
                session_title="Other session",
                session_turn=1,
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
            self.assertEqual(
                [record.run_id for record in select_session_runs(records, query=None, session_id="session-investment")],
                ["run-a", "run-b"],
            )

            _write_text(output_dir / "artifacts" / "plan-stale-round-1.json", "{}")
            generate_demo(selected, output_dir=output_dir, title="Demo", query="session-investment")

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
            self.assertEqual(session["session"]["session_id"], "session-investment")
            self.assertEqual(session["session"]["session_title"], "Investment session")
            self.assertEqual(session["session"]["primary_run_id"], "run-b")
            self.assertFalse((output_dir / "artifacts" / "plan-stale-round-1.json").exists())

    def test_resolves_default_demo_output_by_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            _write_run(
                runs_dir,
                run_id="run-a",
                goal="分析当下潜在的投资机会",
                session_id="session-20260705-190415-c9984f98",
                session_title="Investment session",
                session_turn=1,
                status="done",
                created_at="2026-07-04T10:00:00+00:00",
                updated_at="2026-07-04T10:10:00+00:00",
                final=True,
                claim="claim",
                verdict="accepted",
            )

            records = load_run_records(runs_dir)
            output_dir = resolve_output_dir(
                records,
                output=None,
                output_root=root / "docs" / "demos",
                query="",
            )

            self.assertEqual(output_dir, root / "docs" / "demos" / "session-20260705-190415-c9984f98")

    def test_explicit_demo_output_overrides_session_grouping(self) -> None:
        record = _sample_record(session_id="session-demo")

        output_dir = resolve_output_dir(
            [record],
            output="docs/demos/custom-demo",
            output_root=Path("docs/demos"),
            query="",
        )

        self.assertEqual(output_dir, Path("docs/demos/custom-demo"))

    def test_resolves_default_demo_output_without_explicit_session_id(self) -> None:
        record = _sample_record(session_id="", session_title="投资机会 demo/session")

        output_dir = resolve_output_dir(
            [record],
            output=None,
            output_root=Path("docs/demos"),
            query="",
        )

        self.assertEqual(output_dir, Path("docs/demos") / "投资机会-demo-session")


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
    session_id: str = "",
    session_title: str = "",
    session_turn: int | None = None,
) -> None:
    run_dir = runs_dir / run_id
    (run_dir / "plans").mkdir(parents=True)
    (run_dir / "findings").mkdir()
    (run_dir / "verifications").mkdir()
    _write_json(
        run_dir / "state.json",
        {
            "run_id": run_id,
            "session_id": session_id,
            "session_title": session_title,
            "session_turn": session_turn,
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


def _sample_record(*, session_id: str, session_title: str = ""):
    from dynamic_workflows_agent.demo_pipeline import RunRecord

    return RunRecord(
        run_id="run-sample",
        path=Path("runs/run-sample"),
        goal="sample goal",
        status="done",
        created_at="2026-07-04T10:00:00+00:00",
        updated_at="2026-07-04T10:10:00+00:00",
        current_round=1,
        completed_subtasks=1,
        completed_verifications=1,
        has_final=True,
        session_id=session_id,
        session_title=session_title,
        session_turn=1,
    )


def _write_json(path: Path, value: dict[str, object]) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
