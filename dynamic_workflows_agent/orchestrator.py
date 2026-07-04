from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .agents import SynthesizerAgent, VerifierAgent, WorkerAgent
from .llm import llm_context
from .models import Event, Finding, VerificationResult, WorkflowPlan
from .planner import PlannerAgent
from .store import CheckpointStore
from .ui import EventBus


ConfirmCallback = Callable[[WorkflowPlan], bool]


class WorkflowOrchestrator:
    def __init__(
        self,
        *,
        planner: PlannerAgent,
        worker: WorkerAgent,
        verifier: VerifierAgent,
        synthesizer: SynthesizerAgent,
        store: CheckpointStore,
        event_bus: EventBus,
        confirm_callback: ConfirmCallback | None = None,
    ) -> None:
        self.planner = planner
        self.worker = worker
        self.verifier = verifier
        self.synthesizer = synthesizer
        self.store = store
        self.event_bus = event_bus
        self.confirm_callback = confirm_callback

    async def run(
        self,
        *,
        goal: str,
        conversation: list[dict[str, str]] | None = None,
        run_id: str | None = None,
        resume: bool = False,
    ) -> str:
        if resume:
            if not run_id:
                raise ValueError("run_id is required when resume=True")
            state = self.store.load_state(run_id)
            goal = str(state["goal"])
            self._emit(run_id, "resume", f"resuming workflow {run_id}", {"goal": goal})
            if state.get("status") == "done" and self.store.has_final(run_id):
                self._emit(run_id, "complete", "workflow already completed")
                return self.store.load_final(run_id)
        else:
            state = self.store.create_run(goal)
            run_id = str(state["run_id"])
            self._emit(run_id, "created", f"created workflow run for: {goal[:90]}", {"goal": goal})

        state = self.store.load_state(run_id)
        round_index = int(state.get("current_round", 1))

        plan = self._load_plan_if_present(run_id, round_index)
        if plan is None:
            state["status"] = "planning"
            state["current_round"] = round_index
            self.store.save_state(run_id, state)
            self._emit(run_id, "planning", "asking planner for a dynamic workflow", {"goal": goal})
            with llm_context(
                run_id=run_id,
                component="planner",
                label="Planner",
                round_index=round_index,
            ):
                plan = await self.planner.create_plan(goal=goal, conversation=conversation or [])
            self.store.save_plan(run_id, plan, round_index=round_index)
            self._emit(
                run_id,
                "planned",
                f"planned {len(plan.subtasks)} subtasks, {len(plan.parallel_groups)} group(s), "
                f"{len(plan.verification_steps)} verification step(s)",
                {
                    "round_index": round_index,
                    "subtask_count": len(plan.subtasks),
                    "parallel_group_count": len(plan.parallel_groups),
                    "verification_step_count": len(plan.verification_steps),
                    "plan": plan.to_dict(),
                },
            )
            if self.confirm_callback is not None and not self.confirm_callback(plan):
                state["status"] = "cancelled"
                self.store.save_state(run_id, state)
                self._emit(run_id, "cancelled", "workflow cancelled before execution")
                return "Workflow cancelled before execution."

        max_rounds = plan.convergence_policy.max_rounds
        while True:
            state = self.store.load_state(run_id)
            state["status"] = "running"
            state["current_round"] = round_index
            self.store.save_state(run_id, state)

            self._emit(
                run_id,
                "round_start",
                f"starting workflow round {round_index}",
                {"round_index": round_index},
            )
            await self._execute_round(run_id=run_id, round_index=round_index, plan=plan)

            findings = self.store.load_findings(run_id)
            verifications = self.store.load_verifications(run_id)
            converged, unresolved = self._evaluate_convergence(plan, findings, verifications)

            if converged or round_index >= max_rounds:
                if converged:
                    self._emit(run_id, "converged", "verification criteria converged")
                else:
                    self._emit(
                        run_id,
                        "max_rounds",
                        "maximum workflow rounds reached; synthesizing with unresolved risks",
                        {"round_index": round_index, "unresolved": unresolved},
                    )
                state = self.store.load_state(run_id)
                state["status"] = "synthesizing"
                self.store.save_state(run_id, state)
                self._emit(
                    run_id,
                    "synthesizing",
                    "creating final coordinated report",
                    {"round_index": round_index},
                )
                with llm_context(
                    run_id=run_id,
                    component="synthesizer",
                    label="Synthesizer",
                    round_index=round_index,
                ):
                    report = await self.synthesizer.run(
                        goal=goal,
                        findings=findings,
                        verifications=verifications,
                        unresolved_summary=unresolved,
                    )
                self.store.save_final(run_id, report)
                state["status"] = "done"
                self.store.save_state(run_id, state)
                self._emit(
                    run_id,
                    "complete",
                    f"workflow complete; report saved to runs/{run_id}/final.md",
                    {"round_index": round_index, "final_path": f"runs/{run_id}/final.md"},
                )
                return report

            round_index += 1
            state = self.store.load_state(run_id)
            state["status"] = "planning_followup"
            state["current_round"] = round_index
            self.store.save_state(run_id, state)
            self._emit(
                run_id,
                "iterate",
                f"round {round_index - 1} did not converge; asking planner for follow-up subtasks",
                {"round_index": round_index - 1, "next_round_index": round_index, "unresolved": unresolved},
            )
            with llm_context(
                run_id=run_id,
                component="planner",
                label=f"Planner r{round_index}",
                round_index=round_index,
            ):
                plan = await self.planner.create_followup_plan(
                    goal=goal,
                    prior_plan=plan,
                    findings=[item.to_dict() for item in findings],
                    verifications=[item.to_dict() for item in verifications],
                    unresolved_summary=unresolved,
                )
            self.store.save_plan(run_id, plan, round_index=round_index)
            max_rounds = max(max_rounds, plan.convergence_policy.max_rounds)
            self._emit(
                run_id,
                "planned",
                f"planned follow-up round with {len(plan.subtasks)} subtasks",
                {
                    "round_index": round_index,
                    "subtask_count": len(plan.subtasks),
                    "parallel_group_count": len(plan.parallel_groups),
                    "verification_step_count": len(plan.verification_steps),
                    "plan": plan.to_dict(),
                },
            )

    async def _execute_round(self, *, run_id: str, round_index: int, plan: WorkflowPlan) -> None:
        state = self.store.load_state(run_id)
        completed_subtasks = set(state.get("completed_subtasks", []))
        completed_verifications = set(state.get("completed_verifications", []))
        subtask_by_id = plan.subtask_by_id()

        for group in plan.parallel_groups:
            group_subtasks = [subtask_by_id[subtask_id] for subtask_id in group.subtask_ids]
            runnable = [
                subtask
                for subtask in group_subtasks
                if _round_key(round_index, subtask.id) not in completed_subtasks
            ]
            if not runnable:
                self._emit(
                    run_id,
                    "workers_skip",
                    f"round {round_index} group {group.id} already complete",
                    {"round_index": round_index, "group_id": group.id},
                )
                continue
            self._emit(
                run_id,
                "workers_start",
                f"running group {group.id} with {len(runnable)} worker(s), concurrency {group.max_concurrency}",
                {
                    "round_index": round_index,
                    "group_id": group.id,
                    "worker_count": len(runnable),
                    "max_concurrency": group.max_concurrency,
                },
            )
            semaphore = asyncio.Semaphore(min(group.max_concurrency, len(runnable)))

            async def run_one(subtask_id: str) -> Finding:
                subtask = subtask_by_id[subtask_id]
                async with semaphore:
                    self._emit(
                        run_id,
                        "worker_start",
                        f"{subtask.id}: {subtask.title}",
                        {
                            "round_index": round_index,
                            "subtask_id": subtask.id,
                            "title": subtask.title,
                            "agent_role": subtask.agent_role,
                        },
                    )
                    dependencies = _dependency_findings(self.store.load_findings(run_id), subtask.depends_on)
                    with llm_context(
                        run_id=run_id,
                        component="worker",
                        item_id=subtask.id,
                        label=f"Worker {subtask.id}",
                        round_index=round_index,
                        title=subtask.title,
                        agent_role=subtask.agent_role,
                    ):
                        finding = await self.worker.run(plan=plan, subtask=subtask, dependency_findings=dependencies)
                    self.store.save_finding(run_id, finding, round_index=round_index)
                    current_state = self.store.load_state(run_id)
                    done = set(current_state.get("completed_subtasks", []))
                    done.add(_round_key(round_index, subtask.id))
                    current_state["completed_subtasks"] = sorted(done)
                    self.store.save_state(run_id, current_state)
                    self._emit(
                        run_id,
                        "worker_done",
                        f"{subtask.id}: confidence {finding.confidence:.2f}",
                        {
                            "round_index": round_index,
                            "subtask_id": subtask.id,
                            "title": subtask.title,
                            "agent_role": subtask.agent_role,
                            "confidence": finding.confidence,
                            "finding": finding.to_dict(),
                        },
                    )
                    return finding

            tasks = [asyncio.create_task(run_one(subtask.id)) for subtask in runnable]
            for task in asyncio.as_completed(tasks):
                await task

        findings = self.store.load_findings(run_id)
        for step in plan.verification_steps:
            key = _round_key(round_index, step.id)
            if key in completed_verifications:
                self._emit(
                    run_id,
                    "verify_skip",
                    f"{step.id} already complete",
                    {"round_index": round_index, "step_id": step.id, "mode": step.mode},
                )
                continue
            targets = [finding for finding in findings if finding.subtask_id in set(step.target_subtask_ids)]
            if not targets:
                self._emit(
                    run_id,
                    "verify_skip",
                    f"{step.id} has no target findings",
                    {"round_index": round_index, "step_id": step.id, "mode": step.mode},
                )
                continue
            self._emit(
                run_id,
                "verify_start",
                f"{step.id}: {step.mode} {len(targets)} finding(s)",
                {
                    "round_index": round_index,
                    "step_id": step.id,
                    "mode": step.mode,
                    "target_subtask_ids": list(step.target_subtask_ids),
                    "target_count": len(targets),
                },
            )
            with llm_context(
                run_id=run_id,
                component="verifier",
                item_id=step.id,
                label=f"Verifier {step.id}",
                round_index=round_index,
                mode=step.mode,
            ):
                result = await self.verifier.run(plan=plan, step=step, findings=targets)
            self.store.save_verification(run_id, result, round_index=round_index)
            current_state = self.store.load_state(run_id)
            done = set(current_state.get("completed_verifications", []))
            done.add(key)
            current_state["completed_verifications"] = sorted(done)
            self.store.save_state(run_id, current_state)
            self._emit(
                run_id,
                "verify_done",
                f"{step.id}: {result.verdict}; follow-up={result.needs_followup}",
                {
                    "round_index": round_index,
                    "step_id": step.id,
                    "mode": step.mode,
                    "verdict": result.verdict,
                    "needs_followup": result.needs_followup,
                    "verification": result.to_dict(),
                },
            )

    def _evaluate_convergence(
        self,
        plan: WorkflowPlan,
        findings: list[Finding],
        verifications: list[VerificationResult],
    ) -> tuple[bool, list[str]]:
        unresolved: list[str] = []
        min_confidence = plan.convergence_policy.min_confidence

        if not findings:
            unresolved.append("No worker findings were produced.")
        for finding in findings:
            if finding.confidence < min_confidence:
                unresolved.append(
                    f"Finding {finding.id} confidence {finding.confidence:.2f} is below {min_confidence:.2f}."
                )

        weak_verdicts = {"disputed", "rejected", "insufficient_evidence"}
        for verification in verifications:
            if verification.needs_followup:
                unresolved.append(f"Verification {verification.id} requested follow-up.")
            if verification.verdict in weak_verdicts:
                unresolved.append(f"Verification {verification.id} verdict is {verification.verdict}.")

        if plan.convergence_policy.require_no_critical_disputes and unresolved:
            return False, unresolved
        return not unresolved, unresolved

    def _load_plan_if_present(self, run_id: str, round_index: int) -> WorkflowPlan | None:
        try:
            return self.store.load_plan(run_id, round_index=round_index)
        except (FileNotFoundError, ValueError):
            return None

    def _emit(self, run_id: str, kind: str, message: str, data: dict[str, Any] | None = None) -> None:
        self.event_bus.emit(Event(run_id=run_id, kind=kind, message=message, data=data or {}))


def _round_key(round_index: int, item_id: str) -> str:
    return f"round-{round_index}:{item_id}"


def _dependency_findings(findings: list[Finding], dependency_ids: list[str]) -> list[Finding]:
    wanted = set(dependency_ids)
    return [finding for finding in findings if finding.subtask_id in wanted]
