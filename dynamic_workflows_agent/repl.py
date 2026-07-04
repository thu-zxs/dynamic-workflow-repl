from __future__ import annotations

import asyncio
from textwrap import shorten
from typing import Any

from .input_reader import InputReader
from .models import WorkflowPlan
from .orchestrator import WorkflowOrchestrator
from .store import CheckpointStore
from .ui import DashboardKeyController, DashboardRenderer


class TerminalREPL:
    def __init__(
        self,
        *,
        orchestrator: WorkflowOrchestrator,
        store: CheckpointStore,
        auto_confirm: bool = False,
        dashboard_renderer: Any | None = None,
        input_reader: InputReader | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.store = store
        self.auto_confirm = auto_confirm
        self.dashboard_renderer = dashboard_renderer
        self.input_reader = input_reader
        self.history: list[dict[str, str]] = []
        self.last_run_id: str | None = None

    async def run(self) -> None:
        if self.dashboard_renderer is not None:
            self.dashboard_renderer.render_idle()
            print("Type /help for commands. Enter a task to start a workflow.")
        else:
            print("Dynamic Workflow Agent REPL. Type /help for commands.")
        while True:
            try:
                if self.input_reader is not None:
                    line = await self.input_reader.prompt("dwf> ")
                else:
                    line = await asyncio.to_thread(input, "dwf> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return
            text = line.strip()
            if not text:
                continue
            if text.startswith("/"):
                should_exit = await self._handle_command(text)
                if should_exit:
                    return
                continue
            await self._run_goal(text)

    async def _run_goal(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        previous_runs = {item["run_id"] for item in self.store.list_runs()}
        key_controller = None
        if self.dashboard_renderer is not None and self.auto_confirm:
            key_controller = DashboardKeyController(self.dashboard_renderer)
            key_controller.start()
        try:
            report = await self.orchestrator.run(goal=text, conversation=self.history)
        finally:
            if key_controller is not None:
                key_controller.stop()
        new_runs = [item["run_id"] for item in self.store.list_runs() if item["run_id"] not in previous_runs]
        if new_runs:
            self.last_run_id = new_runs[0]
        self.history.append({"role": "assistant", "content": shorten(report, width=1200, placeholder=" ...")})
        print("\n" + report + "\n")

    async def _handle_command(self, text: str) -> bool:
        parts = text.split()
        command = parts[0]
        if command in {"/exit", "/quit"}:
            return True
        if command == "/help":
            print(
                "Commands:\n"
                "  /help              Show this help\n"
                "  /runs              List checkpointed workflow runs\n"
                "  /resume <run_id>   Resume a run\n"
                "  /inspect [run_id]  Browse planner/worker/verifier details\n"
                "  /status            Show last run id\n"
                "  /config            Show runtime config\n"
                "  /exit              Exit"
            )
            return False
        if command == "/runs":
            runs = self.store.list_runs()
            if not runs:
                print("No runs yet.")
                return False
            for state in runs:
                print(
                    f"{state.get('run_id')}  {state.get('status')}  "
                    f"round={state.get('current_round')}  {state.get('goal')}"
                )
            return False
        if command == "/resume":
            if len(parts) != 2:
                print("Usage: /resume <run_id>")
                return False
            run_id = parts[1]
            report = await self.orchestrator.run(goal="", conversation=self.history, run_id=run_id, resume=True)
            self.last_run_id = run_id
            self.history.append({"role": "assistant", "content": shorten(report, width=1200, placeholder=" ...")})
            print("\n" + report + "\n")
            return False
        if command == "/inspect":
            run_id = parts[1] if len(parts) >= 2 else self.last_run_id
            if not run_id:
                print("No run id. Use /runs, then /inspect <run_id>.")
                return False
            self._inspect_run(run_id)
            return False
        if command == "/status":
            print(f"Last run: {self.last_run_id or 'none'}")
            return False
        if command == "/config":
            print(f"auto_confirm={self.auto_confirm}")
            print(f"input_backend={self.input_reader.backend_name if self.input_reader else 'input'}")
            return False
        print(f"Unknown command: {command}")
        return False

    def _inspect_run(self, run_id: str) -> None:
        try:
            state = self.store.load_state(run_id)
        except FileNotFoundError:
            print(f"Run not found: {run_id}")
            return
        round_index = state.get("current_round")
        round_value = round_index if isinstance(round_index, int) else None
        try:
            plan = self.store.load_plan(run_id, round_index=round_value).to_dict()
        except (FileNotFoundError, ValueError):
            plan = None
        findings = [finding.to_dict() for finding in self.store.load_findings(run_id)]
        verifications = [result.to_dict() for result in self.store.load_verifications(run_id)]
        final_path = ""
        if self.store.has_final(run_id):
            final_path = str(self.store.run_dir(run_id) / "final.md")
        renderer = self.dashboard_renderer if isinstance(self.dashboard_renderer, DashboardRenderer) else DashboardRenderer()
        renderer.load_snapshot(
            run_id=run_id,
            goal=str(state.get("goal") or ""),
            status=str(state.get("status") or "inspect"),
            round_index=round_value,
            plan=plan,
            findings=findings,
            verifications=verifications,
            final_path=final_path,
        )
        DashboardKeyController(renderer).run_blocking()


def make_confirm_callback(*, auto_confirm: bool = False):
    def confirm(plan: WorkflowPlan) -> bool:
        print("\nWorkflow plan:")
        print(f"Goal: {plan.goal}")
        print(f"Subtasks: {len(plan.subtasks)}")
        for group in plan.parallel_groups:
            print(f"  group {group.id}: {len(group.subtask_ids)} workers, concurrency={group.max_concurrency}")
        print(f"Verification steps: {len(plan.verification_steps)}")
        if auto_confirm:
            print("Auto-confirm enabled; running workflow.\n")
            return True
        answer = input("Run this workflow? [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    return confirm
