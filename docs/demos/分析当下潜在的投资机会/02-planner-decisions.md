# Investment Opportunities Dynamic Workflow Demo: Planner Decisions

Primary run: `20260704-142802-8e87f907`

The application code does not contain an investment-analysis workflow. The planner generated the
roles, groups, dependencies, verifier targets, and convergence policy at runtime.

## Session Plan Coverage

| Run | Primary | Status | Plan Rounds | Workers | Verifiers | Final |
|---|---|---|---:|---:|---:|---|
| `20260704-135134-fc83a0f4` | no | planning_followup | 1 | 10 | 1 | no |
| `20260704-142455-b1a2de66` | no | planning | 0 | 0 | 0 | no |
| `20260704-142518-ddbaa1a1` | no | planning | 0 | 0 | 0 | no |
| `20260704-142554-5a346096` | no | planning | 0 | 0 | 0 | no |
| `20260704-142802-8e87f907` | yes | done | 2 | 22 | 2 | yes |

## Run 20260704-135134-fc83a0f4

Goal: 分析当下潜在的投资机会

### Round 1

Plan goal: Identify actionable current investment opportunities across diverse sectors and markets

#### Success Criteria

- At least 5 distinct investment opportunities identified
- Each opportunity includes rationale, risk factors, and estimated return potential
- Opportunities cover at least 3 different sectors/asset classes
- All opportunities have been critically challenged and validated

#### Parallel Groups

| Group | Concurrency | Fan-out |
|---|---:|---|
| G1 | 10 | T1, T2, T3, T4, T5, T6, T7, T8, T9, T10 |

#### Worker Roles

| ID | Role | Title | Depends On |
|---|---|---|---|
| T1 | Tech Sector Analyst | Tech Sector Opportunity Analysis | - |
| T2 | Healthcare Sector Analyst | Healthcare Sector Opportunity Analysis | - |
| T3 | Energy Sector Analyst | Energy Sector Opportunity Analysis | - |
| T4 | Financial Sector Analyst | Financial Sector Opportunity Analysis | - |
| T5 | Consumer Goods Analyst | Consumer Goods & Retail Opportunity Analysis | - |
| T6 | Real Estate Analyst | Real Estate & REIT Opportunity Analysis | - |
| T7 | Macro Trends Analyst | Macroeconomic & Cross-Sector Opportunity Analysis | - |
| T8 | Quantitative Data Analyst | Quantitative Data Screening | - |
| T9 | Emerging Markets Analyst | Emerging Markets Opportunity Analysis | - |
| T10 | Cryptocurrency Analyst | Cryptocurrency & Digital Assets Analysis | - |

#### Verification Steps

| ID | Mode | Targets | Prompt |
|---|---|---|---|
| V1 | refute | T1, T2, T3, T4, T5, T6, T7, T8, T9, T10 | Critically evaluate every investment opportunity proposed by the analysts. For each, identify potential flaws, overlooked risks, unrealistic assumptions, or conflicting evidence. Present counterarguments to ensure onl... |

#### Convergence Policy

- `max_rounds`: 2
- `min_confidence`: 0.8
- `require_no_critical_disputes`: True

## Run 20260704-142802-8e87f907 (primary)

Goal: 分析当下潜在的投资机会

### Round 1

Plan goal: Analyze current potential investment opportunities across multiple asset classes and provide actionable insights

#### Success Criteria

- Identify at least 3 sectors or asset classes with strong growth potential
- Evaluate key risks for each identified opportunity
- Provide clear, data-driven rationale for each opportunity
- Cover a diverse range including equities, fixed income, and alternative assets

#### Parallel Groups

| Group | Concurrency | Fan-out |
|---|---:|---|
| G1 | 10 | T1, T2, T3, T4, T5, T6, T7, T8, T9, T10 |

#### Worker Roles

| ID | Role | Title | Depends On |
|---|---|---|---|
| T1 | Macroeconomic Analyst | Macroeconomic Environment Scan | - |
| T2 | Sector Analyst - Technology | Technology Sector Analysis | - |
| T3 | Sector Analyst - Healthcare | Healthcare Sector Analysis | - |
| T4 | Commodities and Energy Analyst | Energy and Commodities Analysis | - |
| T5 | Real Estate Investment Analyst | Real Estate and Infrastructure Analysis | - |
| T6 | Fixed Income Analyst | Fixed Income and Bond Market Analysis | - |
| T7 | Political Risk Analyst | Geopolitical Risk Assessment | - |
| T8 | Monetary Policy Specialist | Central Bank Policy and Monetary Trends | - |
| T9 | Alternative Assets Analyst | Alternative Investments Scan | - |
| T10 | ESG Research Analyst | ESG and Sustainable Investing Trends | - |

#### Verification Steps

| ID | Mode | Targets | Prompt |
|---|---|---|---|
| V1 | refute | T1, T2, T3, T4, T5, T6, T7, T8, T9, T10 | Critically examine the findings from all analysts. Identify any inconsistencies, overly optimistic assumptions, missing risk factors, or conflicting signals across different sectors and macro trends. Challenge the val... |

#### Convergence Policy

- `max_rounds`: 3
- `min_confidence`: 0.75
- `require_no_critical_disputes`: True

### Round 2

Plan goal: Address unresolved issues from the prior round: validate claims with current data, reconcile inconsistencies, integrate geopolitical risks into sector opportunities, and achieve high confidence.

#### Success Criteria

- All key findings supported by recent data from primary sources.
- Confidence levels raised above 0.75.
- Disputes from verification V1 resolved.
- Cross-sector integration completed with ranked opportunities.

#### Parallel Groups

| Group | Concurrency | Fan-out |
|---|---:|---|
| G1 | 6 | T1, T2, T3, T4, T5, T6 |
| G2 | 3 | T7, T8, T10 |
| G3 | 3 | T9, T11, T12 |

#### Worker Roles

| ID | Role | Title | Depends On |
|---|---|---|---|
| T1 | Macroeconomic Analyst | Macro Data Refresh | - |
| T2 | Sector Analyst - Technology | Technology Sector Data Refresh | - |
| T3 | Sector Analyst - Healthcare | Healthcare Sector Data Refresh | - |
| T4 | Commodities and Energy Analyst | Energy & Commodities Data Refresh | - |
| T5 | Real Estate Investment Analyst | Real Estate & Infrastructure Data Refresh | - |
| T6 | Monetary Policy Specialist | Fixed Income & Central Bank Policy Update | - |
| T7 | Political Risk Analyst | Geopolitical Risk Integration | T1, T2, T3, T4, T5, T6 |
| T8 | Portfolio Strategist | Cross-Sector Reconciliation | T1, T2, T3, T4, T5, T6 |
| T10 | Data Quality Analyst | Data Quality and Assumptions Check | T1, T2, T3, T4, T5, T6 |
| T9 | Investment Strategist | Risk-Return Opportunity Ranking | T7, T8 |
| T11 | Thematic Analyst | Thematic Opportunity Clustering | T7, T8 |
| T12 | Synthesis Analyst | Final Opportunity Consolidation | T7, T8 |

#### Verification Steps

| ID | Mode | Targets | Prompt |
|---|---|---|---|
| V1 | refute | T1, T2, T3, T4, T5, T6, T7, T8, T10, T9, T11, T12 | Critically examine the entire workflow output. Challenge the data currency, integration logic, and consistency across all findings. Ensure all claims are supported by verifiable sources and that geopolitical risks are... |

#### Convergence Policy

- `max_rounds`: 3
- `min_confidence`: 0.75
- `require_no_critical_disputes`: True

### Missing Round Plan Note

The state reached round 3, but only round-specific plan files through round 2 were found. The demo keeps this visible because it is relevant to resume behavior and checkpoint correctness.
