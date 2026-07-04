from __future__ import annotations

import io
import unittest

from dynamic_workflows_agent.models import Event
from dynamic_workflows_agent.ui import DashboardRenderer


class DashboardRendererTests(unittest.TestCase):
    def test_dashboard_can_render_idle_repl_state(self) -> None:
        stream = io.StringIO()
        renderer = DashboardRenderer(stream=stream)

        renderer.render_idle()

        self.assertEqual(renderer.state.status, "idle")
        self.assertEqual(renderer.state.phase, "waiting_for_input")
        output = stream.getvalue()
        self.assertIn("Dynamic Workflow Dashboard", output)
        self.assertIn("waiting for input", output)
        self.assertIn("Live Log", output)

    def test_dashboard_updates_worker_verifier_and_final_state(self) -> None:
        stream = io.StringIO()
        renderer = DashboardRenderer(stream=stream)
        run_id = "run-1"
        plan = {
            "goal": "Audit rollout",
            "subtasks": [
                {"id": "T1", "title": "Architecture", "agent_role": "architect"},
                {"id": "T2", "title": "Operations", "agent_role": "operator"},
                {"id": "T3", "title": "Refutation", "agent_role": "refuter"},
            ],
            "verification_steps": [
                {"id": "V1", "mode": "refute", "prompt": "Challenge findings."},
            ],
        }

        renderer.handle(Event(run_id=run_id, kind="created", message="created", data={"goal": "Audit rollout"}))
        renderer.handle(Event(run_id=run_id, kind="planned", message="planned", data={"round_index": 1, "plan": plan}))
        renderer.handle(
            Event(
                run_id=run_id,
                kind="worker_start",
                message="T1 started",
                data={"round_index": 1, "subtask_id": "T1", "title": "Architecture", "agent_role": "architect"},
            )
        )
        renderer.handle(
            Event(
                run_id=run_id,
                kind="worker_done",
                message="T1 done",
                data={
                    "round_index": 1,
                    "subtask_id": "T1",
                    "title": "Architecture",
                    "agent_role": "architect",
                    "confidence": 0.91,
                },
            )
        )
        renderer.handle(
            Event(
                run_id=run_id,
                kind="verify_done",
                message="V1 accepted",
                data={
                    "round_index": 1,
                    "step_id": "V1",
                    "mode": "refute",
                    "verdict": "accepted",
                    "needs_followup": False,
                },
            )
        )
        renderer.handle(
            Event(
                run_id=run_id,
                kind="complete",
                message="complete",
                data={"round_index": 1, "final_path": "runs/run-1/final.md"},
            )
        )

        self.assertEqual(renderer.state.status, "done")
        self.assertEqual(renderer.state.workers["r1:T1"].status, "done")
        self.assertEqual(renderer.state.workers["r1:T1"].confidence, 0.91)
        self.assertEqual(renderer.state.verifiers["r1:V1"].verdict, "accepted")
        self.assertEqual(renderer.state.verifiers["r1:V1"].needs_followup, False)
        self.assertEqual(renderer.state.final_path, "runs/run-1/final.md")
        output = stream.getvalue()
        self.assertIn("Dynamic Workflow Dashboard", output)
        self.assertIn("Workers", output)
        self.assertIn("Verification", output)
        self.assertIn("Live Log", output)
        self.assertIn("╭", output)
        self.assertIn("│", output)

    def test_dashboard_renders_model_token_activity(self) -> None:
        stream = io.StringIO()
        renderer = DashboardRenderer(stream=stream)
        run_id = "run-token"
        plan = {
            "goal": "Audit rollout",
            "subtasks": [{"id": "T1", "title": "Architecture", "agent_role": "architect"}],
            "verification_steps": [],
        }

        renderer.handle(Event(run_id=run_id, kind="created", message="created", data={"goal": "Audit rollout"}))
        renderer.handle(Event(run_id=run_id, kind="planned", message="planned", data={"round_index": 1, "plan": plan}))
        renderer.handle(
            Event(
                run_id=run_id,
                kind="worker_start",
                message="T1 started",
                data={"round_index": 1, "subtask_id": "T1", "title": "Architecture", "agent_role": "architect"},
            )
        )
        renderer.handle(
            Event(
                run_id=run_id,
                kind="llm_request_sent",
                message="finding sent",
                data={
                    "request_id": "req-1",
                    "component": "worker",
                    "item_id": "T1",
                    "label": "Worker T1",
                    "round_index": 1,
                    "schema_name": "finding",
                    "prompt_tokens_estimate": 1200,
                    "attempt": 1,
                },
            )
        )
        renderer.handle(
            Event(
                run_id=run_id,
                kind="llm_response",
                message="finding received",
                data={
                    "request_id": "req-1",
                    "component": "worker",
                    "item_id": "T1",
                    "label": "Worker T1",
                    "round_index": 1,
                    "schema_name": "finding",
                    "usage": {"prompt_tokens": 1210, "completion_tokens": 340, "total_tokens": 1550},
                    "elapsed_seconds": 2.2,
                },
            )
        )

        output = stream.getvalue()
        self.assertIn("Model I/O", output)
        self.assertIn("sent", output)
        self.assertIn("recv", output)
        self.assertIn("tok", output)
        self.assertEqual(renderer.state.workers["r1:T1"].detail["model"]["total_tokens"], 1550)

    def test_dashboard_keyboard_navigation_opens_formatted_details(self) -> None:
        stream = io.StringIO()
        renderer = DashboardRenderer(stream=stream)
        plan = {
            "goal": "Audit rollout",
            "success_criteria": ["Find concrete risk"],
            "subtasks": [
                {
                    "id": "T1",
                    "title": "Architecture",
                    "agent_role": "architect",
                    "prompt": "Review architecture risks.",
                    "depends_on": [],
                },
                {"id": "T2", "title": "Ops", "agent_role": "operator", "prompt": "Review ops.", "depends_on": []},
                {"id": "T3", "title": "Refute", "agent_role": "refuter", "prompt": "Refute.", "depends_on": []},
            ],
            "parallel_groups": [{"id": "G1", "subtask_ids": ["T1", "T2", "T3"], "max_concurrency": 3}],
            "verification_steps": [{"id": "V1", "mode": "refute", "target_subtask_ids": ["T1"], "prompt": "Challenge."}],
            "convergence_policy": {"max_rounds": 2, "min_confidence": 0.8, "require_no_critical_disputes": True},
        }
        renderer.load_snapshot(
            run_id="run-1",
            goal="Audit rollout",
            status="done",
            round_index=1,
            plan=plan,
            findings=[
                {
                    "id": "F-T1",
                    "subtask_id": "T1",
                    "agent_role": "architect",
                    "claim": "Architecture risk exists.",
                    "evidence": ["Interface changed."],
                    "confidence": 0.91,
                    "limitations": ["No production data."],
                    "recommended_next_steps": ["Run canary."],
                }
            ],
            verifications=[
                {
                    "id": "V1",
                    "target_subtask_ids": ["T1"],
                    "mode": "refute",
                    "verdict": "accepted",
                    "issues": [],
                    "counterarguments": ["Rollback should be tested."],
                    "needs_followup": False,
                    "confidence": 0.87,
                }
            ],
        )

        renderer.handle_key("down")
        renderer.handle_key("down")
        renderer.handle_key("enter")

        output = stream.getvalue()
        self.assertIn("Inspector", output)
        self.assertIn("Worker: T1", output)
        self.assertIn("Claim: Architecture risk exists.", output)
        self.assertIn("▸", output)

        renderer.handle_key("pagedown")
        output = stream.getvalue()
        self.assertIn("Evidence:", output)

        renderer.handle_key("esc")
        renderer.handle_key("down")
        renderer.handle_key("down")
        renderer.handle_key("down")
        renderer.handle_key("enter")

        output = stream.getvalue()
        self.assertIn("Inspector", output)
        self.assertIn("Verifier: V1", output)
        self.assertIn("Verdict: accepted", output)

        renderer.handle_key("pagedown")
        output = stream.getvalue()
        self.assertIn("Counterarguments:", output)


if __name__ == "__main__":
    unittest.main()
