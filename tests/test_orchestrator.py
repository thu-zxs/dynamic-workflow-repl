from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from dynamic_workflows_agent.agents import SynthesizerAgent, VerifierAgent, WorkerAgent
from dynamic_workflows_agent.llm import FakeLLMClient
from dynamic_workflows_agent.models import Finding
from dynamic_workflows_agent.orchestrator import WorkflowOrchestrator
from dynamic_workflows_agent.planner import PlannerAgent
from dynamic_workflows_agent.store import CheckpointStore
from dynamic_workflows_agent.ui import EventBus
from dynamic_workflows_agent.validator import validate_workflow_plan


class CountingFakeLLM(FakeLLMClient):
    def __init__(self, *, finding_delay: float = 0.0, force_dispute: bool = False) -> None:
        super().__init__(force_dispute=force_dispute)
        self.finding_delay = finding_delay
        self.finding_calls = 0

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        if schema_name == "finding":
            self.finding_calls += 1
            if self.finding_delay:
                await asyncio.sleep(self.finding_delay)
        return await super().chat_json(
            system=system,
            user=user,
            schema_name=schema_name,
            max_tokens=max_tokens,
        )


def make_orchestrator(store: CheckpointStore, llm: FakeLLMClient) -> WorkflowOrchestrator:
    event_bus = EventBus()
    event_bus.subscribe(store.append_event)
    return WorkflowOrchestrator(
        planner=PlannerAgent(llm),
        worker=WorkerAgent(llm),
        verifier=VerifierAgent(llm),
        synthesizer=SynthesizerAgent(llm),
        store=store,
        event_bus=event_bus,
        confirm_callback=lambda plan: True,
    )


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_workflow_completes_and_persists_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(tmp)
            llm = CountingFakeLLM()
            orchestrator = make_orchestrator(store, llm)

            report = await orchestrator.run(goal="Create a migration risk plan", conversation=[])

            runs = store.list_runs()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "done")
            self.assertIn("Final Report", report)
            self.assertEqual(len(store.load_findings(runs[0]["run_id"])), 3)
            self.assertEqual(len(store.load_verifications(runs[0]["run_id"])), 1)
            self.assertTrue((Path(tmp) / runs[0]["run_id"] / "events.jsonl").exists())

    async def test_workers_fan_out_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(tmp)
            llm = CountingFakeLLM(finding_delay=0.15)
            orchestrator = make_orchestrator(store, llm)

            started = time.perf_counter()
            await orchestrator.run(goal="Check concurrency", conversation=[])
            elapsed = time.perf_counter() - started

            self.assertEqual(llm.finding_calls, 3)
            self.assertLess(elapsed, 0.35)

    async def test_resume_skips_completed_subtasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(tmp)
            llm = CountingFakeLLM()
            plan = validate_workflow_plan(llm._workflow_plan("USER_TASK:\nResume goal"))
            state = store.create_run("Resume goal")
            run_id = state["run_id"]
            store.save_plan(run_id, plan, round_index=1)
            store.save_finding(
                run_id,
                Finding(
                    id="F-T1",
                    subtask_id="T1",
                    agent_role="cached worker",
                    claim="Cached finding",
                    evidence=["Already done"],
                    confidence=0.9,
                ),
                round_index=1,
            )
            state = store.load_state(run_id)
            state["status"] = "running"
            state["completed_subtasks"] = ["round-1:T1"]
            store.save_state(run_id, state)

            orchestrator = make_orchestrator(store, llm)
            await orchestrator.run(goal="", conversation=[], run_id=run_id, resume=True)

            self.assertEqual(llm.finding_calls, 2)
            self.assertEqual(store.load_state(run_id)["status"], "done")
            self.assertEqual(len(store.load_findings(run_id)), 3)

    async def test_disputed_verification_triggers_followup_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(tmp)
            llm = CountingFakeLLM(force_dispute=True)
            orchestrator = make_orchestrator(store, llm)

            await orchestrator.run(goal="Force a follow-up round", conversation=[])

            run_id = store.list_runs()[0]["run_id"]
            self.assertTrue((Path(tmp) / run_id / "plans" / "round-2.json").exists())
            self.assertGreaterEqual(llm.calls.count("workflow_plan"), 2)
            self.assertEqual(store.load_state(run_id)["status"], "done")


if __name__ == "__main__":
    unittest.main()
