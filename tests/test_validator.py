from __future__ import annotations

import unittest

from dynamic_workflows_agent.validator import PlanValidationError, validate_workflow_plan


def valid_plan() -> dict:
    return {
        "goal": "Audit a migration plan",
        "success_criteria": ["Find risks", "Verify findings"],
        "subtasks": [
            {
                "id": "T1",
                "title": "Architecture review",
                "agent_role": "architect",
                "prompt": "Review architecture risks.",
                "depends_on": [],
                "expected_output": "finding",
            },
            {
                "id": "T2",
                "title": "Operations review",
                "agent_role": "operator",
                "prompt": "Review rollout risks.",
                "depends_on": [],
                "expected_output": "finding",
            },
            {
                "id": "T3",
                "title": "Adversarial review",
                "agent_role": "refuter",
                "prompt": "Try to refute the migration assumptions.",
                "depends_on": [],
                "expected_output": "finding",
            },
        ],
        "parallel_groups": [{"id": "G1", "subtask_ids": ["T1", "T2", "T3"], "max_concurrency": 3}],
        "verification_steps": [
            {
                "id": "V1",
                "target_subtask_ids": ["T1", "T2", "T3"],
                "mode": "refute",
                "prompt": "Challenge every finding.",
            }
        ],
        "convergence_policy": {
            "max_rounds": 2,
            "min_confidence": 0.7,
            "require_no_critical_disputes": True,
        },
    }


class PlanValidatorTests(unittest.TestCase):
    def test_valid_plan_passes(self) -> None:
        plan = validate_workflow_plan(valid_plan())
        self.assertEqual(plan.goal, "Audit a migration plan")
        self.assertEqual(len(plan.subtasks), 3)

    def test_parallel_group_must_have_three_to_ten_subtasks(self) -> None:
        data = valid_plan()
        data["parallel_groups"] = [{"id": "G1", "subtask_ids": ["T1", "T2"], "max_concurrency": 2}]
        with self.assertRaises(PlanValidationError) as raised:
            validate_workflow_plan(data)
        self.assertIn("3-10", str(raised.exception))

    def test_dependency_must_be_in_earlier_group(self) -> None:
        data = valid_plan()
        data["subtasks"][0]["depends_on"] = ["T2"]
        with self.assertRaises(PlanValidationError) as raised:
            validate_workflow_plan(data)
        self.assertIn("earlier parallel group", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
