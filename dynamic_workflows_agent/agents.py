from __future__ import annotations

import json
from typing import Any

from .llm import LLMClient
from .models import Finding, Subtask, VerificationResult, VerificationStep, WorkflowPlan
from .tools import ToolRegistry


WORKER_SYSTEM = """You are one isolated worker subagent in a dynamic workflow.

Return JSON only. Do not write the final answer. Produce a structured finding:

{
  "id": "F-T1",
  "subtask_id": "T1",
  "agent_role": "role",
  "claim": "specific finding",
  "evidence": ["evidence or reasoning summary"],
  "confidence": 0.0,
  "limitations": ["known gaps"],
  "recommended_next_steps": ["follow-up checks"],
  "tool_results": []
}

Rules:
- Work only from your assigned perspective.
- Do not assume other workers' conclusions.
- Keep evidence concise and auditable.
- Use confidence from 0 to 1.
- Use available tools when file contents, repository search, current web facts,
  or URL content are needed. Summarize tool evidence in the finding.
"""


VERIFIER_SYSTEM = """You are a verifier/refuter subagent in a dynamic workflow.

Return JSON only. Challenge the supplied findings before they are folded into
the final answer:

{
  "id": "V1",
  "target_subtask_ids": ["T1"],
  "mode": "refute",
  "verdict": "accepted | disputed | rejected | insufficient_evidence",
  "issues": ["problems found"],
  "counterarguments": ["ways the finding could be wrong"],
  "needs_followup": false,
  "confidence": 0.0
}

Rules:
- Look for weak evidence, contradictions, missing edge cases, and overclaims.
- Do not invent new final conclusions.
- Mark needs_followup true when the result should not be synthesized yet.
"""


SYNTHESIZER_SYSTEM = """You are the final synthesizer for a dynamic workflow.

Return JSON only:

{
  "report_markdown": "markdown report"
}

Rules:
- Use verified findings and verification summaries only.
- Distinguish verified conclusions from disputed or unresolved items.
- Include validation status and residual risk.
- Produce a coordinated answer for the user.
"""


class WorkerAgent:
    def __init__(self, llm: LLMClient, *, tool_registry: ToolRegistry | None = None) -> None:
        self.llm = llm
        self.tool_registry = tool_registry

    async def run(
        self,
        *,
        plan: WorkflowPlan,
        subtask: Subtask,
        dependency_findings: list[Finding],
    ) -> Finding:
        user = (
            f"ROOT_GOAL:\n{plan.goal}\n\n"
            "SUCCESS_CRITERIA_JSON:\n"
            f"{json.dumps(plan.success_criteria, ensure_ascii=False)}\n\n"
            "SUBTASK_JSON:\n"
            f"{json.dumps(subtask.to_dict(), ensure_ascii=False)}\n\n"
            "DEPENDENCY_FINDINGS_JSON:\n"
            f"{json.dumps([item.to_dict() for item in dependency_findings], ensure_ascii=False)}\n\n"
            "Return one Finding JSON object."
        )
        if self.tool_registry is not None:
            tool_results = []

            def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                result = self.tool_registry.run(name, arguments).to_dict()
                tool_results.append(result)
                return result

            raw, returned_tool_results = await self.llm.chat_json_with_tools(
                system=WORKER_SYSTEM,
                user=user,
                schema_name="finding",
                tools=self.tool_registry.specs(),
                run_tool=run_tool,
                max_tokens=5000,
            )
            raw["tool_results"] = _valid_tool_results(returned_tool_results or tool_results)
        else:
            raw = await self.llm.chat_json(
                system=WORKER_SYSTEM,
                user=user,
                schema_name="finding",
                max_tokens=3500,
            )
            raw["tool_results"] = _valid_tool_results(raw.get("tool_results", []))
        return Finding.from_dict(raw, fallback_subtask_id=subtask.id)


class VerifierAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        plan: WorkflowPlan,
        step: VerificationStep,
        findings: list[Finding],
    ) -> VerificationResult:
        user = (
            f"ROOT_GOAL:\n{plan.goal}\n\n"
            "VERIFICATION_STEP_JSON:\n"
            f"{json.dumps(step.to_dict(), ensure_ascii=False)}\n\n"
            "FINDINGS_JSON:\n"
            f"{json.dumps([item.to_dict() for item in findings], ensure_ascii=False)}\n\n"
            "Return one VerificationResult JSON object."
        )
        raw = await self.llm.chat_json(
            system=VERIFIER_SYSTEM,
            user=user,
            schema_name="verification",
            max_tokens=3500,
        )
        return VerificationResult.from_dict(
            raw,
            fallback_id=step.id,
            fallback_targets=list(step.target_subtask_ids),
            fallback_mode=step.mode,
        )


def _valid_tool_results(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


class SynthesizerAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        goal: str,
        findings: list[Finding],
        verifications: list[VerificationResult],
        unresolved_summary: list[str],
    ) -> str:
        user = (
            f"ROOT_GOAL:\n{goal}\n\n"
            "FINDINGS_JSON:\n"
            f"{json.dumps([item.to_dict() for item in findings], ensure_ascii=False)}\n\n"
            "VERIFICATIONS_JSON:\n"
            f"{json.dumps([item.to_dict() for item in verifications], ensure_ascii=False)}\n\n"
            "UNRESOLVED_SUMMARY_JSON:\n"
            f"{json.dumps(unresolved_summary, ensure_ascii=False)}\n\n"
            "Return final report JSON."
        )
        raw = await self.llm.chat_json(
            system=SYNTHESIZER_SYSTEM,
            user=user,
            schema_name="synthesis",
            max_tokens=5000,
        )
        report = raw.get("report_markdown")
        if not isinstance(report, str) or not report.strip():
            raise ValueError("synthesis response must contain non-empty report_markdown")
        return report.strip()
