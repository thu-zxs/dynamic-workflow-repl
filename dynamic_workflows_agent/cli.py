from __future__ import annotations

import argparse
import asyncio
import os
import sys

from .agents import SynthesizerAgent, VerifierAgent, WorkerAgent
from .input_reader import create_input_reader
from .llm import DeepSeekClient, FakeLLMClient, LLMError, LLMClient
from .models import Event
from .orchestrator import WorkflowOrchestrator
from .planner import PlannerAgent
from .repl import TerminalREPL, make_confirm_callback
from .store import CheckpointStore
from .tools import ToolRegistry
from .ui import DashboardKeyController, DashboardRenderer, EventBus, TerminalRenderer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dynamic Workflow terminal agent")
    parser.add_argument("--task", help="Run one task and exit")
    parser.add_argument("--offline", action="store_true", help="Use deterministic fake backend")
    parser.add_argument("--yes", action="store_true", help="Auto-confirm planned workflows")
    parser.add_argument(
        "--dashboard",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Render a live ANSI workflow dashboard (default: enabled; use --no-dashboard for plain events)",
    )
    parser.add_argument("--quiet", action="store_true", help="Hide realtime event output")
    parser.add_argument("--runs-dir", default="runs", help="Checkpoint directory")
    args = parser.parse_args(argv)

    try:
        llm = build_llm(args.offline)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("Set DEEPSEEK_API_KEY or run with --offline for a local demo.", file=sys.stderr)
        return 2

    store = CheckpointStore(args.runs_dir)
    event_bus = EventBus()
    event_bus.subscribe(store.append_event)
    _attach_llm_events(llm, event_bus)
    dashboard_renderer = None
    if not args.quiet:
        if args.dashboard:
            dashboard_renderer = DashboardRenderer()
            event_bus.subscribe(dashboard_renderer.handle)
        else:
            event_bus.subscribe(TerminalRenderer().handle)

    tool_registry = ToolRegistry(workspace_root=".")
    orchestrator = WorkflowOrchestrator(
        planner=PlannerAgent(llm),
        worker=WorkerAgent(llm, tool_registry=tool_registry),
        verifier=VerifierAgent(llm),
        synthesizer=SynthesizerAgent(llm),
        store=store,
        event_bus=event_bus,
        confirm_callback=make_confirm_callback(auto_confirm=args.yes),
    )

    if args.task:
        key_controller = None
        if dashboard_renderer is not None and args.yes:
            key_controller = DashboardKeyController(dashboard_renderer)
            key_controller.start()
        try:
            report = asyncio.run(orchestrator.run(goal=args.task, conversation=[]))
        finally:
            if key_controller is not None:
                key_controller.stop()
        print("\n" + report)
        return 0

    repl = TerminalREPL(
        orchestrator=orchestrator,
        store=store,
        auto_confirm=args.yes,
        dashboard_renderer=dashboard_renderer,
        input_reader=create_input_reader(history_path=store.root / ".repl_history"),
    )
    asyncio.run(repl.run())
    return 0


def build_llm(offline: bool) -> LLMClient:
    if offline:
        return FakeLLMClient()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise LLMError("DEEPSEEK_API_KEY is not set")
    return DeepSeekClient.from_env()


def _attach_llm_events(llm: LLMClient, event_bus: EventBus) -> None:
    set_event_callback = getattr(llm, "set_event_callback", None)
    if not callable(set_event_callback):
        return

    def emit(kind: str, message: str, data: dict) -> None:
        run_id = data.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return
        event_bus.emit(Event(run_id=run_id, kind=kind, message=message, data=data))

    set_event_callback(emit)


if __name__ == "__main__":
    raise SystemExit(main())
