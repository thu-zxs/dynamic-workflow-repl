from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


class SchemaError(ValueError):
    """Raised when model JSON cannot be coerced into the local protocol."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expect_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{name} must be an object")
    return value


def _expect_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{name} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if value is None and allow_empty:
        return []
    if not isinstance(value, list):
        raise SchemaError(f"{name} must be a list")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise SchemaError(f"{name}[{index}] must be a string")
        stripped = item.strip()
        if stripped:
            items.append(stripped)
    if not allow_empty and not items:
        raise SchemaError(f"{name} must contain at least one item")
    return items


def _float_between(value: Any, name: str, *, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"{name} must be a number")
    return max(0.0, min(1.0, float(value)))


def _int_between(value: Any, name: str, *, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{name} must be an integer")
    return max(low, min(high, value))


@dataclass(slots=True)
class Subtask:
    id: str
    title: str
    agent_role: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    expected_output: str = "finding"

    @classmethod
    def from_dict(cls, value: Any) -> "Subtask":
        data = _expect_dict(value, "subtask")
        return cls(
            id=_expect_str(data.get("id"), "subtask.id"),
            title=_expect_str(data.get("title"), "subtask.title"),
            agent_role=_expect_str(data.get("agent_role"), "subtask.agent_role"),
            prompt=_expect_str(data.get("prompt"), "subtask.prompt"),
            depends_on=_string_list(data.get("depends_on", []), "subtask.depends_on", allow_empty=True),
            expected_output=str(data.get("expected_output") or "finding").strip() or "finding",
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ParallelGroup:
    id: str
    subtask_ids: list[str]
    max_concurrency: int

    @classmethod
    def from_dict(cls, value: Any) -> "ParallelGroup":
        data = _expect_dict(value, "parallel_group")
        subtask_ids = _string_list(data.get("subtask_ids"), "parallel_group.subtask_ids")
        return cls(
            id=_expect_str(data.get("id"), "parallel_group.id"),
            subtask_ids=subtask_ids,
            max_concurrency=_int_between(
                data.get("max_concurrency"),
                "parallel_group.max_concurrency",
                default=len(subtask_ids),
                low=1,
                high=10,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VerificationStep:
    id: str
    target_subtask_ids: list[str]
    mode: str
    prompt: str

    @classmethod
    def from_dict(cls, value: Any) -> "VerificationStep":
        data = _expect_dict(value, "verification_step")
        return cls(
            id=_expect_str(data.get("id"), "verification_step.id"),
            target_subtask_ids=_string_list(
                data.get("target_subtask_ids"), "verification_step.target_subtask_ids"
            ),
            mode=_expect_str(data.get("mode", "refute"), "verification_step.mode").lower(),
            prompt=_expect_str(data.get("prompt"), "verification_step.prompt"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ConvergencePolicy:
    max_rounds: int = 3
    min_confidence: float = 0.75
    require_no_critical_disputes: bool = True

    @classmethod
    def from_dict(cls, value: Any) -> "ConvergencePolicy":
        data = {} if value is None else _expect_dict(value, "convergence_policy")
        return cls(
            max_rounds=_int_between(data.get("max_rounds"), "convergence_policy.max_rounds", default=3, low=1, high=8),
            min_confidence=_float_between(
                data.get("min_confidence"), "convergence_policy.min_confidence", default=0.75
            ),
            require_no_critical_disputes=bool(data.get("require_no_critical_disputes", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkflowPlan:
    goal: str
    success_criteria: list[str]
    subtasks: list[Subtask]
    parallel_groups: list[ParallelGroup]
    verification_steps: list[VerificationStep]
    convergence_policy: ConvergencePolicy = field(default_factory=ConvergencePolicy)

    @classmethod
    def from_dict(cls, value: Any) -> "WorkflowPlan":
        data = _expect_dict(value, "workflow_plan")
        return cls(
            goal=_expect_str(data.get("goal"), "workflow_plan.goal"),
            success_criteria=_string_list(data.get("success_criteria"), "workflow_plan.success_criteria"),
            subtasks=[Subtask.from_dict(item) for item in data.get("subtasks", [])],
            parallel_groups=[ParallelGroup.from_dict(item) for item in data.get("parallel_groups", [])],
            verification_steps=[VerificationStep.from_dict(item) for item in data.get("verification_steps", [])],
            convergence_policy=ConvergencePolicy.from_dict(data.get("convergence_policy")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "success_criteria": list(self.success_criteria),
            "subtasks": [item.to_dict() for item in self.subtasks],
            "parallel_groups": [item.to_dict() for item in self.parallel_groups],
            "verification_steps": [item.to_dict() for item in self.verification_steps],
            "convergence_policy": self.convergence_policy.to_dict(),
        }

    def subtask_by_id(self) -> dict[str, Subtask]:
        return {item.id: item for item in self.subtasks}


@dataclass(slots=True)
class Finding:
    id: str
    subtask_id: str
    agent_role: str
    claim: str
    evidence: list[str]
    confidence: float
    limitations: list[str] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: Any, *, fallback_subtask_id: str = "") -> "Finding":
        data = _expect_dict(value, "finding")
        subtask_id = str(data.get("subtask_id") or fallback_subtask_id).strip()
        if not subtask_id:
            raise SchemaError("finding.subtask_id must be a non-empty string")
        return cls(
            id=str(data.get("id") or f"F-{subtask_id}").strip(),
            subtask_id=subtask_id,
            agent_role=_expect_str(data.get("agent_role", "worker"), "finding.agent_role"),
            claim=_expect_str(data.get("claim"), "finding.claim"),
            evidence=_string_list(data.get("evidence"), "finding.evidence"),
            confidence=_float_between(data.get("confidence"), "finding.confidence", default=0.0),
            limitations=_string_list(data.get("limitations", []), "finding.limitations", allow_empty=True),
            recommended_next_steps=_string_list(
                data.get("recommended_next_steps", []),
                "finding.recommended_next_steps",
                allow_empty=True,
            ),
            tool_results=_dict_list(data.get("tool_results", []), "finding.tool_results"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dict_list(value: Any, name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SchemaError(f"{name} must be a list")
    results: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise SchemaError(f"{name}[{index}] must be an object")
        results.append(item)
    return results


@dataclass(slots=True)
class VerificationResult:
    id: str
    target_subtask_ids: list[str]
    mode: str
    verdict: str
    issues: list[str]
    counterarguments: list[str]
    needs_followup: bool
    confidence: float

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        fallback_id: str = "",
        fallback_targets: list[str] | None = None,
        fallback_mode: str = "verify",
    ) -> "VerificationResult":
        data = _expect_dict(value, "verification_result")
        targets = data.get("target_subtask_ids")
        if targets is None:
            targets = fallback_targets or []
        return cls(
            id=str(data.get("id") or fallback_id or "V").strip(),
            target_subtask_ids=_string_list(targets, "verification_result.target_subtask_ids"),
            mode=str(data.get("mode") or fallback_mode).strip().lower() or fallback_mode,
            verdict=_expect_str(data.get("verdict"), "verification_result.verdict").lower(),
            issues=_string_list(data.get("issues", []), "verification_result.issues", allow_empty=True),
            counterarguments=_string_list(
                data.get("counterarguments", []),
                "verification_result.counterarguments",
                allow_empty=True,
            ),
            needs_followup=bool(data.get("needs_followup", False)),
            confidence=_float_between(data.get("confidence"), "verification_result.confidence", default=0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Event:
    run_id: str
    kind: str
    message: str
    timestamp: str = field(default_factory=utc_now_iso)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
