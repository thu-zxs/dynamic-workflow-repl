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

Live dashboard mode:

```bash
python3 -m dynamic_workflows_agent --offline --yes --dashboard --task "Design a migration plan"
```

Dashboard REPL mode:

```bash
python3 -m dynamic_workflows_agent --offline --yes --dashboard
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

## Observability

The default terminal output is an append-only event stream. Use `--dashboard`
for a panel-style ANSI view that refreshes as the workflow runs:

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

During live runs, keyboard navigation is enabled in `--dashboard --yes` mode.
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
