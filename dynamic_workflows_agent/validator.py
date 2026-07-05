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


def repair_workflow_plan_structure(value: Any) -> WorkflowPlan:
    """Repair model-generated scheduling structure without changing task text.

    The model occasionally emits a reasonable set of subtasks but illegal
    orchestration metadata: missing group membership, tiny serial groups, or
    dependencies that point within the same group. This function preserves the
    subtask definitions and rewrites only groups, invalid dependencies, and
    verifier targets so the plan satisfies the local execution protocol.
    """

    try:
        plan = WorkflowPlan.from_dict(value)
    except SchemaError as exc:
        raise PlanValidationError([str(exc)]) from exc

    data = plan.to_dict()
    subtasks = data.get("subtasks") if isinstance(data.get("subtasks"), list) else []
    subtask_ids = [str(item.get("id") or "") for item in subtasks if isinstance(item, dict) and item.get("id")]
    if len(subtask_ids) < 3:
        raise PlanValidationError(["plan must include at least three subtasks"])
    if len(set(subtask_ids)) != len(subtask_ids):
        raise PlanValidationError(["subtask IDs must be unique"])

    groups = _build_repaired_groups(subtasks)
    group_index_by_subtask = {
        subtask_id: group_index
        for group_index, group in enumerate(groups)
        for subtask_id in group["subtask_ids"]
    }
    known_ids = set(subtask_ids)

    for subtask in subtasks:
        subtask_id = str(subtask.get("id") or "")
        dependencies = subtask.get("depends_on") if isinstance(subtask.get("depends_on"), list) else []
        repaired_dependencies: list[str] = []
        for dependency_id in dependencies:
            dependency_id = str(dependency_id)
            if dependency_id not in known_ids or dependency_id == subtask_id:
                continue
            if group_index_by_subtask.get(dependency_id, 0) >= group_index_by_subtask.get(subtask_id, 0):
                continue
            if dependency_id not in repaired_dependencies:
                repaired_dependencies.append(dependency_id)
        subtask["depends_on"] = repaired_dependencies

    data["parallel_groups"] = groups
    data["verification_steps"] = _repair_verification_steps(data.get("verification_steps"), subtask_ids)
    return validate_workflow_plan(data)


def _build_repaired_groups(subtasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subtask_ids = [str(item["id"]) for item in subtasks]
    dependency_by_id = {
        str(item["id"]): [
            str(dependency)
            for dependency in item.get("depends_on", [])
            if str(dependency) in set(subtask_ids) and str(dependency) != str(item["id"])
        ]
        for item in subtasks
    }

    pending = list(subtask_ids)
    resolved: set[str] = set()
    grouped: list[list[str]] = []
    while pending:
        ready = [subtask_id for subtask_id in pending if all(dep in resolved for dep in dependency_by_id[subtask_id])]
        if not ready:
            ready = list(pending)
        group = ready[:10]
        if len(group) < 3 and len(pending) > len(group):
            for subtask_id in pending:
                if subtask_id not in group:
                    group.append(subtask_id)
                if len(group) >= 3:
                    break
        for subtask_id in group:
            if subtask_id in pending:
                pending.remove(subtask_id)
        grouped.append(group)
        resolved.update(group)

    grouped = _rebalance_small_final_group(grouped)
    return [
        {
            "id": f"G{index}",
            "subtask_ids": group,
            "max_concurrency": min(10, max(3, len(group))),
        }
        for index, group in enumerate(grouped, start=1)
    ]


def _rebalance_small_final_group(groups: list[list[str]]) -> list[list[str]]:
    if not groups:
        return groups
    while len(groups) >= 2 and len(groups[-1]) < 3:
        final = groups[-1]
        previous = groups[-2]
        if len(previous) + len(final) <= 10:
            groups[-2] = previous + final
            groups.pop()
            continue
        while len(final) < 3 and len(previous) > 3:
            final.insert(0, previous.pop())
        if len(final) < 3:
            break
    return groups


def _repair_verification_steps(value: Any, subtask_ids: list[str]) -> list[dict[str, Any]]:
    steps = value if isinstance(value, list) else []
    repaired: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(steps, start=1):
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("id") or f"V{index}")
        if step_id in used_ids:
            step_id = f"V{index}"
        used_ids.add(step_id)
        mode = str(item.get("mode") or "refute").lower()
        if mode not in {"verify", "refute", "adversarial", "cross_check", "review"}:
            mode = "refute"
        targets = item.get("target_subtask_ids") if isinstance(item.get("target_subtask_ids"), list) else []
        targets = [str(target) for target in targets if str(target) in set(subtask_ids)]
        if not targets:
            targets = list(subtask_ids)
        prompt = str(item.get("prompt") or "Challenge the findings and identify weak evidence.")
        repaired.append({"id": step_id, "target_subtask_ids": targets, "mode": mode, "prompt": prompt})
    if not repaired:
        repaired.append(
            {
                "id": "V1",
                "target_subtask_ids": list(subtask_ids),
                "mode": "refute",
                "prompt": "Challenge every finding and flag weak evidence before synthesis.",
            }
        )
    return repaired


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
