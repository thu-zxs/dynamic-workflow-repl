# Investment Opportunities Dynamic Workflow Demo: Convergence and Resume

Primary run: `20260704-142802-8e87f907`

## How Convergence Is Decided

- Any finding below the plan's `min_confidence` contributes to unresolved risk.
- Any verifier result with `needs_followup=true` contributes to unresolved risk.
- Verifier verdicts `disputed`, `rejected`, or `insufficient_evidence` block clean convergence.
- If unresolved risks remain and `max_rounds` has not been reached, the planner is asked for a follow-up plan.
- If `max_rounds` is reached, synthesis proceeds with unresolved risks carried into the final report.

## Session Events

| Time | Kind | Round | Message |
|---|---|---:|---|
| 2026-07-04 06:36:25 | iterate | 1 | round 1 did not converge; asking planner for follow-up subtasks |
| 2026-07-04 06:58:30 | iterate | 2 | round 2 did not converge; asking planner for follow-up subtasks |
| 2026-07-04 07:20:00 | resume | - | resuming workflow 20260704-142802-8e87f907 |
| 2026-07-04 07:29:06 | llm_request_error | 3 | finding: request failed |
| 2026-07-04 07:39:40 | max_rounds | 3 | maximum workflow rounds reached; synthesizing with unresolved risks |
| 2026-07-04 07:39:40 | synthesizing | 3 | creating final coordinated report |
| 2026-07-04 07:41:08 | complete | 3 | workflow complete; report saved to runs/20260704-142802-8e87f907/final.md |
| 2026-07-04 07:41:08 | resume | - | resuming workflow 20260704-142802-8e87f907 |
| 2026-07-04 07:41:08 | complete | - | workflow already completed |

## Resume/Plan Integrity Note

The run reached round 3, but only `2` round-specific plan files exist.
This is preserved in the demo because it reveals a real checkpoint/resume edge case:
resume can continue with the latest saved plan even when a new round-specific plan file is missing.
