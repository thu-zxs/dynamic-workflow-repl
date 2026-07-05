# Dynamic Workflow Terminal Agent

This is a small terminal REPL agent that implements the core orchestration
mechanics of Claude Code Dynamic Workflows:

- model-generated workflow plans
- 3-10 concurrent worker subagents per parallel group
- isolated worker contexts
- structured findings
- verifier/refuter agents before synthesis
- iterative convergence
- realtime terminal events
- checkpointed runs with resume support

The implementation is intentionally standard-library Python. DeepSeek is the
default live model backend through its OpenAI-compatible API. A fake backend is
included for tests and local demos.

## Run

```bash
export DEEPSEEK_API_KEY="..."
python3 -m dynamic_workflows_agent --yes
```

Enhanced REPL input:

```bash
python3 -m pip install -e ".[repl]"
```

The REPL prefers `prompt_toolkit` when installed, matching the input approach
used by `~/Repo/repl-agent`. Without it, the program loads standard-library
`readline` so arrow-key cursor movement, command history, and tab completion
still work on normal Unix/macOS terminals.

One-shot task:

```bash
python3 -m dynamic_workflows_agent --yes --task "Audit this design for failure modes"
```

Offline demo:

```bash
python3 -m dynamic_workflows_agent --offline --yes --task "Design a migration plan"
```

One-shot task with the default dashboard:

```bash
python3 -m dynamic_workflows_agent --offline --yes --task "Design a migration plan"
```

Default dashboard REPL mode:

```bash
python3 -m dynamic_workflows_agent --offline --yes
```

The dashboard draws an idle panel first. Enter a task at the `dwf>` prompt to
start a workflow; worker, verifier, and model I/O status refresh as workflow
events arrive.

## REPL Commands

```text
/help
/runs
/resume <run_id>
/inspect [run_id]
/session
/session new [name]
/status
/config
/exit
```

## Checkpoints

Each workflow run writes artifacts under `runs/<run_id>/`:

- `state.json`
- `events.jsonl`
- `plan.json`
- `plans/round-<n>.json`
- `findings/*.json`
- `verifications/*.json`
- `final.md`

Resume a run from the REPL:

```text
/resume 20260703-174500-abc12345
```

If a run fails mid-workflow, `/resume <run_id>` continues from the checkpointed
state. Completed worker findings and verifier results are skipped, failed or
missing items are retried, and a missing follow-up round plan is regenerated
from the previous round's findings and verification results.

Each REPL process starts with a workflow session ID. Every task you submit in
that REPL is checkpointed with the same `session_id`, a stable `session_title`,
and an incrementing `session_turn`. Use `/session` to show the current session
and `/session new [name]` to start a fresh one.

After a workflow reaches the final summary, keep typing in the same REPL to
continue from that result. User feedback such as "revise the recommendations",
"focus on China instead", or "challenge the risky assumptions" becomes the next
session turn. The planner receives compressed context from prior turns and
generates a fresh dynamic workflow for the requested revision.

One-shot tasks can also be attached to a known session:

```bash
python3 -m dynamic_workflows_agent --yes --session-id session-investment-demo --task "分析当下潜在的投资机会"
```

## Generate Demo from Runs

Use the standalone demo pipeline to turn checkpointed runs into a Git-friendly
case study:

```bash
python3 -m dynamic_workflows_agent.demo_pipeline --query "分析当下潜在的投资机会"
```

The installed console script is equivalent:

```bash
dynamic-workflows-demo --query "分析当下潜在的投资机会"
```

The pipeline is session-oriented. It selects all matching run folders, chooses
the most complete run as the primary narrative, and still includes every
matched run in the session timeline, planner coverage, worker summaries,
verifier decisions, and structured artifacts. Use `--list-sessions` to inspect
inferred sessions, or `--session-id <id>` when run state files contain an
explicit `session_id`.

When a session ID exists, prefer it over fuzzy text matching:

```bash
dynamic-workflows-demo --session-id session-investment-demo
```

By default, generated output is grouped by session under
`docs/demos/<session_id>/`. If run state does not contain an explicit
`session_id`, the pipeline falls back to a sanitized inferred session key. Use
`--output docs/demos/custom-name` to override the exact directory, or
`--output-root <dir>` to keep session grouping under a different root.

Each session directory contains:

- `README.md`
- `01-session-timeline.md`
- `02-planner-decisions.md`
- `03-worker-findings.md`
- `04-verifier-decisions.md`
- `05-convergence-and-resume.md`
- `final-report.md`
- `artifacts/*.json`

This keeps raw `runs/` out of git while preserving the orchestration evidence
needed for a demo.

## Observability

The default terminal output is a panel-style ANSI dashboard that refreshes as
the workflow runs. Use `--no-dashboard` for an append-only event stream:

- top status bar: active run, colored phase badge, elapsed time, round, goal, progress bars, model token I/O, and final report path
- left panel: worker subagents with status icon, role/title, confidence, tool count, and token summary
- right panel: verification summary, or an Inspector view when details are opened
- bottom panel: live event log, including model request send/receive and tool-call events

The layout follows the same four-zone idea as `~/Repo/repl-agent`: header,
workers, verification/inspector, and footer logs. It is implemented with
standard ANSI rendering, so Rich is not required.

Dashboard keyboard controls:

- `Up` / `Down`, `k` / `j`: move between Planner, Dispatcher, Workers, and Verifiers
- `Enter`, `Right`, `l`: open formatted details for the selected item
- `Esc`, `Left`, `h`, `Backspace`: return to overview
- `PageUp` / `PageDown`, `Space`: scroll details
- `q`: exit a detail browser

During live runs, keyboard navigation is enabled in dashboard `--yes` mode.
After any run, use `/inspect [run_id]` to browse the saved planner, dispatcher,
worker, and verifier details from checkpoint artifacts.

`--quiet` disables realtime output and still writes checkpoint artifacts.

## Model Configuration

Environment variables:

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_MODEL`, default `deepseek-v4-pro`
- `DEEPSEEK_BASE_URL`, default `https://api.deepseek.com`
- `DEEPSEEK_TIMEOUT`, default `60`
- `DEEPSEEK_MAX_RETRIES`, default `3`
- `DEEPSEEK_JSON_REPAIR_RETRIES`, default `2`

## Worker Tools

Worker subagents can use tools when running under the CLI. DeepSeek receives
OpenAI-compatible function definitions, decides which tools to call, and this
program executes those tools locally before sending results back to the model.

Available tools:

- `list_files(root, max_results)` lists workspace files
- `read_file(path, max_chars)` reads a workspace text file
- `search_files(pattern, root, max_results)` searches files with `rg` or a Python fallback
- `web_search(query, max_results)` searches the web through DuckDuckGo HTML, with Bing RSS fallback when DuckDuckGo is unavailable
- `fetch_url(url, max_chars)` fetches and truncates public URL text

File tools are restricted to the current workspace. Attempts to read outside
the workspace return a structured tool error. Tool results are saved inside each
worker finding and are visible in dashboard detail view or `/inspect`.

DeepSeek API note: DeepSeek supports function calling, but the documented
execution model requires the client to provide and execute the function
implementation. The model returns tool calls; this agent runs them and feeds
the results back.

## Design Note

The program fixes the orchestration protocol, not the task workflow. The
planner model generates subtasks, parallel groups, verification strategy, and
follow-up rounds at runtime. The local code only validates the plan, executes
it, persists progress, and enforces concurrency and verification boundaries.

## Context Compression

The agent uses deterministic context compression in two places:

- Between user turns in the same session, completed final reports are reduced
  into `runs/_sessions/<session_id>.json` as a rolling summary plus recent turn
  records. The next planner call receives that compact session context.
- Between workflow rounds, follow-up planning, verification, and synthesis use
  compact finding/verifier payloads. Full raw findings and tool results remain
  on disk, but model prompts receive bounded summaries with claims, evidence,
  limitations, confidence, and tool-call counts.
