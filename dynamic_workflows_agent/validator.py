from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import SchemaError, WorkflowPlan


@dataclass(slots=True)
class PlanValidationError(ValueError):
    errors: list[str]

    def __str__(self) -> str:
        return "; ".join(self.errors)


def validate_workflow_plan(value: Any) -> WorkflowPlan:
    errors: list[str] = []
    try:
        plan = WorkflowPlan.from_dict(value)
    except SchemaError as exc:
        raise PlanValidationError([str(exc)]) from exc

    subtask_by_id = plan.subtask_by_id()
    if not plan.subtasks:
        errors.append("plan must include at least three subtasks")
    if len(subtask_by_id) != len(plan.subtasks):
        errors.append("subtask IDs must be unique")

    group_by_id = {group.id: group for group in plan.parallel_groups}
    if not plan.parallel_groups:
        errors.append("plan must include at least one parallel group")
    if len(group_by_id) != len(plan.parallel_groups):
        errors.append("parallel group IDs must be unique")

    scheduled: set[str] = set()
    group_index_by_subtask: dict[str, int] = {}
    for group_index, group in enumerate(plan.parallel_groups):
        count = len(group.subtask_ids)
        if count < 3 or count > 10:
            errors.append(f"parallel group {group.id} must fan out to 3-10 subtasks, got {count}")
        if group.max_concurrency < min(3, count):
            errors.append(f"parallel group {group.id} must allow at least three concurrent workers")
        if group.max_concurrency > 10:
            errors.append(f"parallel group {group.id} max_concurrency must be <= 10")
        for subtask_id in group.subtask_ids:
            if subtask_id not in subtask_by_id:
                errors.append(f"parallel group {group.id} references unknown subtask {subtask_id}")
            scheduled.add(subtask_id)
            group_index_by_subtask[subtask_id] = group_index

    for subtask in plan.subtasks:
        if subtask.id not in scheduled:
            errors.append(f"subtask {subtask.id} is not scheduled in any parallel group")
        for dependency_id in subtask.depends_on:
            if dependency_id not in subtask_by_id:
                errors.append(f"subtask {subtask.id} depends on unknown subtask {dependency_id}")
            elif group_index_by_subtask.get(dependency_id, 0) >= group_index_by_subtask.get(subtask.id, 0):
                errors.append(
                    f"subtask {subtask.id} dependency {dependency_id} must be in an earlier parallel group"
                )

    if _has_dependency_cycle(plan):
        errors.append("subtask dependencies must not contain cycles")

    if not plan.success_criteria:
        errors.append("plan must include success criteria")

    if not plan.verification_steps:
        errors.append("plan must include verification steps")

    verification_by_id = {step.id: step for step in plan.verification_steps}
    if len(verification_by_id) != len(plan.verification_steps):
        errors.append("verification step IDs must be unique")
    for step in plan.verification_steps:
        if step.mode not in {"verify", "refute", "adversarial", "cross_check", "review"}:
            errors.append(f"verification step {step.id} has unsupported mode {step.mode}")
        for subtask_id in step.target_subtask_ids:
            if subtask_id not in subtask_by_id:
                errors.append(f"verification step {step.id} targets unknown subtask {subtask_id}")

    if plan.convergence_policy.max_rounds < 1:
        errors.append("convergence max_rounds must be >= 1")

    if errors:
        raise PlanValidationError(errors)
    return plan


def _has_dependency_cycle(plan: WorkflowPlan) -> bool:
    graph = {subtask.id: list(subtask.depends_on) for subtask in plan.subtasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visited:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for dependency in graph.get(node, []):
            if visit(dependency):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def plan_repair_instructions(errors: list[str]) -> str:
    joined = "\n".join(f"- {error}" for error in errors)
    return (
        "The previous workflow plan JSON failed validation. Return corrected JSON only.\n"
        "Validation errors:\n"
        f"{joined}\n\n"
        "Constraints to satisfy:\n"
        "- Every parallel group must include 3 to 10 subtasks.\n"
        "- max_concurrency must allow at least three workers and no more than ten.\n"
        "- Dependencies must point to earlier parallel groups only.\n"
        "- Include at least one verification/refutation step.\n"
        "- Keep the plan dynamic and task-specific; do not use a fixed template."
    )
