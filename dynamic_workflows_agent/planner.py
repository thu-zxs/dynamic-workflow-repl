from __future__ import annotations

import json
from typing import Any

from .context import compact_conversation, compact_findings, compact_verifications
from .llm import LLMClient
from .models import WorkflowPlan
from .validator import (
    PlanValidationError,
    plan_repair_instructions,
    repair_workflow_plan_structure,
    validate_workflow_plan,
)


PLANNER_SYSTEM = """You are a dynamic workflow planner for a terminal agent.

Return JSON only. The JSON must describe a task-specific workflow plan:

{
  "goal": "string",
  "success_criteria": ["string"],
  "subtasks": [
    {
      "id": "T1",
      "title": "string",
      "agent_role": "string",
      "prompt": "string",
      "depends_on": [],
      "expected_output": "finding"
    }
  ],
  "parallel_groups": [
    {
      "id": "G1",
      "subtask_ids": ["T1", "T2", "T3"],
      "max_concurrency": 3
    }
  ],
  "verification_steps": [
    {
      "id": "V1",
      "target_subtask_ids": ["T1", "T2", "T3"],
      "mode": "refute",
      "prompt": "string"
    }
  ],
  "convergence_policy": {
    "max_rounds": 3,
    "min_confidence": 0.75,
    "require_no_critical_disputes": true
  }
}

Rules:
- Do not use a fixed workflow template. Adapt roles and subtasks to the task.
- Every parallel group must fan out to 3-10 subagents.
- Each parallel group's max_concurrency must allow at least 3 workers and at most 10.
- Use independent agent perspectives.
- Include verifier or refuter steps that challenge findings before synthesis.
- Dependencies are allowed only when a subtask depends on an earlier parallel group.
- Keep worker outputs limited to structured findings; final writing happens later.
- If SESSION_CONTEXT_JSON is present, treat the user task as the latest turn in
  that session. If the user critiques or modifies a previous result, create a
  workflow that directly addresses the requested revision instead of starting
  from scratch.
"""


class PlannerAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def create_plan(
        self,
        *,
        goal: str,
        conversation: list[dict[str, str]] | None = None,
        session_context: dict[str, Any] | None = None,
    ) -> WorkflowPlan:
        user = (
            f"USER_TASK:\n{goal}\n\n"
            "SESSION_CONTEXT_JSON:\n"
            f"{json.dumps(session_context or {}, ensure_ascii=False)}\n\n"
            "CONVERSATION_CONTEXT_JSON:\n"
            f"{json.dumps(compact_conversation(conversation), ensure_ascii=False)}\n\n"
            "Create a dynamic workflow plan in JSON for this task."
        )
        return await self._call_and_validate(user)

    async def create_followup_plan(
        self,
        *,
        goal: str,
        prior_plan: WorkflowPlan,
        findings: list[dict[str, Any]],
        verifications: list[dict[str, Any]],
        unresolved_summary: list[str],
    ) -> WorkflowPlan:
        user = (
            f"USER_TASK:\n{goal}\n\n"
            "FOLLOW_UP_CONTEXT:\n"
            "The previous workflow round did not converge. Generate a targeted follow-up plan, "
            "still using 3-10 independent subagents in each parallel group.\n\n"
            "PRIOR_PLAN_JSON:\n"
            f"{json.dumps(prior_plan.to_dict(), ensure_ascii=False)}\n\n"
            "COMPACT_FINDINGS_JSON:\n"
            f"{json.dumps(compact_findings(findings), ensure_ascii=False)}\n\n"
            "COMPACT_VERIFICATIONS_JSON:\n"
            f"{json.dumps(compact_verifications(verifications), ensure_ascii=False)}\n\n"
            "UNRESOLVED_SUMMARY_JSON:\n"
            f"{json.dumps(unresolved_summary, ensure_ascii=False)}\n\n"
            "Return corrected follow-up workflow JSON only."
        )
        return await self._call_and_validate(user)

    async def _call_and_validate(self, user: str) -> WorkflowPlan:
        raw = await self.llm.chat_json(
            system=PLANNER_SYSTEM,
            user=user,
            schema_name="workflow_plan",
            max_tokens=6000,
        )
        try:
            return validate_workflow_plan(raw)
        except PlanValidationError as first_error:
            repair_user = (
                f"{user}\n\n"
                f"{plan_repair_instructions(first_error.errors)}\n\n"
                "PREVIOUS_INVALID_JSON:\n"
                f"{json.dumps(raw, ensure_ascii=False)}"
            )
            repaired = await self.llm.chat_json(
                system=PLANNER_SYSTEM,
                user=repair_user,
                schema_name="workflow_plan",
                max_tokens=6000,
            )
            try:
                return validate_workflow_plan(repaired)
            except PlanValidationError as second_error:
                for candidate in (repaired, raw):
                    try:
                        return repair_workflow_plan_structure(candidate)
                    except PlanValidationError:
                        pass
                combined = first_error.errors + second_error.errors
                raise PlanValidationError(combined) from second_error
