# Investment Opportunities Dynamic Workflow Demo: Convergence and Resume

Primary run: `20260705-192125-827258b5`

## How Convergence Is Decided

- Any finding below the plan's `min_confidence` contributes to unresolved risk.
- Any verifier result with `needs_followup=true` contributes to unresolved risk.
- Verifier verdicts `disputed`, `rejected`, or `insufficient_evidence` block clean convergence.
- If unresolved risks remain and `max_rounds` has not been reached, the planner is asked for a follow-up plan.
- If `max_rounds` is reached, synthesis proceeds with unresolved risks carried into the final report.

## Session Events

| Time | Kind | Round | Message |
|---|---|---:|---|
| 2026-07-05 11:22:27 | llm_request_error | 1 | workflow_plan: request failed |
| 2026-07-05 11:43:04 | iterate | 1 | round 1 did not converge; asking planner for follow-up subtasks |
| 2026-07-05 11:55:35 | iterate | 2 | round 2 did not converge; asking planner for follow-up subtasks |
| 2026-07-05 15:57:39 | resume | - | resuming workflow 20260705-192125-827258b5 |
| 2026-07-05 16:03:15 | max_rounds | 3 | maximum workflow rounds reached; synthesizing with unresolved risks |
| 2026-07-05 16:03:15 | synthesizing | 3 | creating final coordinated report |
| 2026-07-05 16:03:53 | complete | 3 | workflow complete; report saved to runs/20260705-192125-827258b5/final.md |

## Resume/Plan Integrity Note

Round-specific plan files are present for the observed primary run rounds.
