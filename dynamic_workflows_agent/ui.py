from __future__ import annotations

import shutil
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from textwrap import wrap
import time
import re
from typing import Any, Callable

from .models import Event


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

STYLES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Callable[[Event], None]] = []
        self._lock = threading.RLock()

    def subscribe(self, callback: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def emit(self, event: Event) -> None:
        with self._lock:
            for callback in list(self._subscribers):
                callback(event)


class TerminalRenderer:
    def __init__(self, *, quiet: bool = False) -> None:
        self.quiet = quiet

    def handle(self, event: Event) -> None:
        if self.quiet:
            return
        time_part = _short_time(event.timestamp)
        kind = event.kind.upper().replace("_", "-")
        print(f"[{time_part}] {kind:<14} {event.message}", flush=True)


@dataclass(slots=True)
class DashboardItem:
    id: str
    title: str
    status: str
    round_index: int | None = None
    role: str = ""
    confidence: float | None = None
    verdict: str = ""
    needs_followup: bool | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelActivity:
    request_id: str
    label: str
    component: str
    schema_name: str
    status: str = "preparing"
    item_id: str = ""
    round_index: int | None = None
    model: str = ""
    prompt_tokens_estimate: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    max_tokens: int | None = None
    attempt: int = 0
    tool_count: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    elapsed_seconds: float = 0.0
    error: str = ""


@dataclass(slots=True)
class DashboardState:
    run_id: str = ""
    goal: str = ""
    status: str = "idle"
    phase: str = ""
    round_index: int | None = None
    workers: dict[str, DashboardItem] = field(default_factory=dict)
    verifiers: dict[str, DashboardItem] = field(default_factory=dict)
    recent_events: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    final_path: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    model_activities: dict[str, ModelActivity] = field(default_factory=dict)


@dataclass(slots=True)
class NavigationItem:
    key: str
    kind: str
    title: str
    detail_lines: list[str]


class DashboardRenderer:
    """Small ANSI dashboard for workflow events.

    This is deliberately simpler than a curses app: events mutate an in-memory
    view model and each event redraws the screen.
    """

    def __init__(self, *, stream=None, enabled: bool = True) -> None:
        self.stream = stream or sys.stdout
        self.enabled = enabled
        self.state = DashboardState()
        self._has_drawn = False
        self.selected_index = 0
        self.detail_open = False
        self.detail_scroll = 0
        self._draw_lock = threading.Lock()
        self._ticker_thread: threading.Thread | None = None
        self._ticker_stop = threading.Event()

    def handle(self, event: Event) -> None:
        self.update(event)
        if self.enabled:
            self.draw()
            self._ensure_ticker()

    def render_idle(self) -> None:
        self.state.status = "idle"
        self.state.phase = "waiting_for_input"
        self.state.started_at = 0.0
        if not self.state.recent_events:
            self.state.recent_events.append(f"{datetime.now().strftime('%H:%M:%S')} repl: waiting for input")
        if self.enabled:
            self.draw()

    def handle_key(self, key: str) -> bool:
        """Handle one navigation key.

        Returns True when an interactive inspector should exit.
        """
        if key in {"up", "k"}:
            self.move_selection(-1)
        elif key in {"down", "j", "tab"}:
            self.move_selection(1)
        elif key in {"enter", "right", "l"}:
            self.open_detail()
        elif key in {"esc", "left", "h", "backspace"}:
            self.close_detail()
        elif key in {"pageup"}:
            self.scroll_detail(-6)
        elif key in {"pagedown", "space"}:
            self.scroll_detail(6)
        elif key == "q":
            if self.detail_open:
                self.close_detail()
            else:
                return True
        else:
            return False
        if self.enabled:
            self.draw()
        return False

    def move_selection(self, delta: int) -> None:
        items = self.navigation_items()
        if not items:
            self.selected_index = 0
            return
        self.selected_index = (self.selected_index + delta) % len(items)
        self.detail_scroll = 0

    def open_detail(self) -> None:
        if self.navigation_items():
            self.detail_open = True
            self.detail_scroll = 0

    def close_detail(self) -> None:
        self.detail_open = False
        self.detail_scroll = 0

    def scroll_detail(self, delta: int) -> None:
        if not self.detail_open:
            return
        item = self.selected_navigation_item()
        if item is None:
            return
        max_scroll = max(0, len(item.detail_lines) - 4)
        self.detail_scroll = max(0, min(max_scroll, self.detail_scroll + delta))

    def navigation_items(self) -> list[NavigationItem]:
        items: list[NavigationItem] = []
        if self.state.plan:
            items.append(
                NavigationItem(
                    key="planner",
                    kind="Planner",
                    title="Dynamic workflow plan",
                    detail_lines=_plan_detail_lines(self.state.plan),
                )
            )
            items.append(
                NavigationItem(
                    key="dispatcher",
                    kind="Dispatcher",
                    title="Parallel groups and execution graph",
                    detail_lines=_dispatcher_detail_lines(self.state.plan),
                )
            )
        for key, item in sorted(self.state.workers.items(), key=lambda kv: ((kv[1].round_index or 0), kv[1].id)):
            items.append(
                NavigationItem(
                    key=key,
                    kind="Worker",
                    title=f"{item.id}: {item.title}",
                    detail_lines=_worker_detail_lines(item),
                )
            )
        for key, item in sorted(self.state.verifiers.items(), key=lambda kv: ((kv[1].round_index or 0), kv[1].id)):
            items.append(
                NavigationItem(
                    key=key,
                    kind="Verifier",
                    title=f"{item.id}: {item.role}",
                    detail_lines=_verifier_detail_lines(item),
                )
            )
        if self.selected_index >= len(items):
            self.selected_index = max(0, len(items) - 1)
        return items

    def selected_navigation_item(self) -> NavigationItem | None:
        items = self.navigation_items()
        if not items:
            return None
        return items[self.selected_index]

    def load_snapshot(
        self,
        *,
        run_id: str,
        goal: str,
        status: str,
        round_index: int | None,
        plan: dict[str, Any] | None = None,
        findings: list[dict[str, Any]] | None = None,
        verifications: list[dict[str, Any]] | None = None,
        final_path: str = "",
    ) -> None:
        self.state = DashboardState(
            run_id=run_id,
            goal=goal,
            status=status,
            phase="inspect",
            round_index=round_index,
            final_path=final_path,
        )
        self.selected_index = 0
        self.detail_open = False
        self.detail_scroll = 0
        if plan:
            self._register_plan({"plan": plan, "round_index": round_index})
        for finding in findings or []:
            subtask_id = finding.get("subtask_id")
            if not isinstance(subtask_id, str):
                continue
            self._update_worker(
                {
                    "round_index": round_index,
                    "subtask_id": subtask_id,
                    "agent_role": finding.get("agent_role", ""),
                    "confidence": finding.get("confidence"),
                    "finding": finding,
                },
                status="done",
            )
        for verification in verifications or []:
            step_id = verification.get("id")
            if not isinstance(step_id, str):
                continue
            self._update_verifier(
                {
                    "round_index": round_index,
                    "step_id": step_id,
                    "mode": verification.get("mode", "verify"),
                    "verdict": verification.get("verdict"),
                    "needs_followup": verification.get("needs_followup"),
                    "verification": verification,
                },
                status="done",
            )
        self.state.recent_events.append(f"{datetime.now().strftime('%H:%M:%S')} inspect: loaded run snapshot")
        if self.enabled:
            self.draw()

    def update(self, event: Event) -> None:
        data = event.data or {}
        if event.kind == "created" and self.state.run_id and event.run_id != self.state.run_id:
            self.state = DashboardState()
            self.selected_index = 0
            self.detail_open = False
            self.detail_scroll = 0
        self.state.run_id = event.run_id
        self.state.phase = event.kind
        self.state.status = _status_for_event(event.kind, self.state.status)
        if event.kind in {"created", "resume"} or self.state.started_at <= 0:
            self.state.started_at = time.time()
        if isinstance(data.get("goal"), str):
            self.state.goal = data["goal"]
        if isinstance(data.get("round_index"), int):
            self.state.round_index = data["round_index"]
        if isinstance(data.get("final_path"), str):
            self.state.final_path = data["final_path"]

        if event.kind == "planned":
            self._register_plan(data)
        elif event.kind == "worker_start":
            self._update_worker(data, status="running")
        elif event.kind == "worker_done":
            self._update_worker(data, status="done")
        elif event.kind == "verify_start":
            self._update_verifier(data, status="running")
        elif event.kind == "verify_done":
            self._update_verifier(data, status="done")
        elif event.kind == "verify_skip":
            self._update_verifier(data, status="skipped")
        elif event.kind.startswith("llm_"):
            self._update_model_activity(event)

        self.state.recent_events.append(f"{_short_time(event.timestamp)} {event.kind}: {event.message}")

    def draw(self) -> None:
        terminal_size = shutil.get_terminal_size((112, 34))
        width = max(80, terminal_size.columns)
        height = max(24, terminal_size.lines)
        lines = self._render_lines(width, height)
        with self._draw_lock:
            self.stream.write("\033[2J\033[H")
            self.stream.write("\n".join(lines))
            self.stream.write("\n")
            self.stream.flush()
        self._has_drawn = True

    def _register_plan(self, data: dict) -> None:
        plan = data.get("plan")
        round_index = data.get("round_index") if isinstance(data.get("round_index"), int) else None
        if not isinstance(plan, dict):
            return
        self.state.plan = plan
        if isinstance(plan.get("goal"), str):
            self.state.goal = plan["goal"]
        for subtask in plan.get("subtasks", []):
            if not isinstance(subtask, dict) or not isinstance(subtask.get("id"), str):
                continue
            key = _item_key(round_index, subtask["id"])
            self.state.workers.setdefault(
                key,
                DashboardItem(
                    id=subtask["id"],
                    title=str(subtask.get("title") or ""),
                    role=str(subtask.get("agent_role") or ""),
                    status="planned",
                    round_index=round_index,
                    detail={"subtask": subtask, "plan": plan},
                ),
            )
        for step in plan.get("verification_steps", []):
            if not isinstance(step, dict) or not isinstance(step.get("id"), str):
                continue
            key = _item_key(round_index, step["id"])
            self.state.verifiers.setdefault(
                key,
                DashboardItem(
                    id=step["id"],
                    title=str(step.get("prompt") or ""),
                    role=str(step.get("mode") or "verify"),
                    status="planned",
                    round_index=round_index,
                    detail={"verification_step": step, "plan": plan},
                ),
            )

    def _update_worker(self, data: dict, *, status: str) -> None:
        subtask_id = data.get("subtask_id")
        if not isinstance(subtask_id, str):
            return
        round_index = data.get("round_index") if isinstance(data.get("round_index"), int) else None
        key = _item_key(round_index, subtask_id)
        item = self.state.workers.get(key)
        if item is None:
            item = DashboardItem(
                id=subtask_id,
                title=str(data.get("title") or ""),
                role=str(data.get("agent_role") or ""),
                status=status,
                round_index=round_index,
                detail={key: value for key, value in data.items() if key in {"finding", "subtask_id", "round_index"}},
            )
            self.state.workers[key] = item
        item.status = status
        item.title = str(data.get("title") or item.title)
        item.role = str(data.get("agent_role") or item.role)
        item.detail.update({key: value for key, value in data.items() if key in {"finding", "subtask_id", "round_index"}})
        confidence = data.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            item.confidence = float(confidence)

    def _update_verifier(self, data: dict, *, status: str) -> None:
        step_id = data.get("step_id")
        if not isinstance(step_id, str):
            return
        round_index = data.get("round_index") if isinstance(data.get("round_index"), int) else None
        key = _item_key(round_index, step_id)
        item = self.state.verifiers.get(key)
        if item is None:
            item = DashboardItem(
                id=step_id,
                title="",
                role=str(data.get("mode") or "verify"),
                status=status,
                round_index=round_index,
                detail={key: value for key, value in data.items() if key in {"verification", "step_id", "round_index"}},
            )
            self.state.verifiers[key] = item
        item.status = status
        item.role = str(data.get("mode") or item.role)
        item.detail.update({key: value for key, value in data.items() if key in {"verification", "step_id", "round_index"}})
        verdict = data.get("verdict")
        if isinstance(verdict, str):
            item.verdict = verdict
        followup = data.get("needs_followup")
        if isinstance(followup, bool):
            item.needs_followup = followup

    def _update_model_activity(self, event: Event) -> None:
        data = event.data or {}
        request_id = str(data.get("request_id") or "")
        if not request_id:
            return
        activity = self.state.model_activities.get(request_id)
        if activity is None:
            activity = ModelActivity(
                request_id=request_id,
                label=str(data.get("label") or data.get("component") or data.get("schema_name") or "model"),
                component=str(data.get("component") or ""),
                schema_name=str(data.get("schema_name") or ""),
                item_id=str(data.get("item_id") or ""),
                round_index=data.get("round_index") if isinstance(data.get("round_index"), int) else None,
            )
            self.state.model_activities[request_id] = activity
        activity.updated_at = time.time()
        activity.label = str(data.get("label") or activity.label)
        activity.component = str(data.get("component") or activity.component)
        activity.schema_name = str(data.get("schema_name") or activity.schema_name)
        activity.item_id = str(data.get("item_id") or activity.item_id)
        if isinstance(data.get("round_index"), int):
            activity.round_index = data["round_index"]
        if isinstance(data.get("model"), str):
            activity.model = data["model"]
        if isinstance(data.get("prompt_tokens_estimate"), int):
            activity.prompt_tokens_estimate = data["prompt_tokens_estimate"]
        if isinstance(data.get("max_tokens"), int):
            activity.max_tokens = data["max_tokens"]
        if isinstance(data.get("attempt"), int):
            activity.attempt = data["attempt"]
        if isinstance(data.get("tool_count"), int):
            activity.tool_count = data["tool_count"]

        if event.kind == "llm_request_start":
            activity.status = "preparing"
        elif event.kind == "llm_request_sent":
            activity.status = "waiting"
        elif event.kind == "llm_response":
            activity.status = "received"
            if isinstance(data.get("elapsed_seconds"), (int, float)) and not isinstance(data.get("elapsed_seconds"), bool):
                activity.elapsed_seconds = float(data["elapsed_seconds"])
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            activity.prompt_tokens = _int_from_usage(usage.get("prompt_tokens"), activity.prompt_tokens_estimate)
            activity.completion_tokens = _int_from_usage(
                usage.get("completion_tokens"),
                _int_from_usage(data.get("completion_tokens_estimate"), 0),
            )
            activity.total_tokens = _int_from_usage(
                usage.get("total_tokens"),
                activity.prompt_tokens + activity.completion_tokens,
            )
        elif event.kind == "llm_request_error":
            activity.status = "error"
            activity.error = str(data.get("error") or "")
            if isinstance(data.get("elapsed_seconds"), (int, float)) and not isinstance(data.get("elapsed_seconds"), bool):
                activity.elapsed_seconds = float(data["elapsed_seconds"])

        self._refresh_item_model_summary(activity)

    def _refresh_item_model_summary(self, activity: ModelActivity) -> None:
        if activity.component not in {"worker", "verifier"} or not activity.item_id:
            return
        if activity.component == "worker":
            item = self.state.workers.get(_item_key(activity.round_index, activity.item_id))
        else:
            item = self.state.verifiers.get(_item_key(activity.round_index, activity.item_id))
        if item is None:
            return
        related = [
            model
            for model in self.state.model_activities.values()
            if model.component == activity.component
            and model.item_id == activity.item_id
            and model.round_index == activity.round_index
        ]
        active = [model for model in related if _model_is_active(model)]
        prompt_tokens = sum(model.prompt_tokens or model.prompt_tokens_estimate for model in related)
        completion_tokens = sum(model.completion_tokens for model in related)
        total_tokens = sum(model.total_tokens or (model.prompt_tokens or model.prompt_tokens_estimate) for model in related)
        latest = max(related, key=lambda model: model.updated_at)
        item.detail["model"] = {
            "status": latest.status,
            "active": bool(active),
            "request_count": len(related),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "label": latest.label,
            "schema_name": latest.schema_name,
            "started_at": latest.started_at,
            "elapsed_seconds": _model_elapsed(latest),
            "error": latest.error,
        }

    def _render_lines(self, width: int, height: int) -> list[str]:
        selected = self.selected_navigation_item()
        selected_text = "-"
        if selected is not None:
            selected_text = f"{selected.kind}: {selected.title}"
        elapsed = "-" if self.state.started_at <= 0 else f"{time.time() - self.state.started_at:.0f}s"
        progress = _progress_summary(self.state)
        summary_lines = [
            (
                f"{_style('Status', 'dim')} {_status_badge(self.state.status)}   "
                f"{_style('Phase', 'dim')} {_phase_label(self.state.phase)}   "
                f"{_style('Round', 'dim')} {self.state.round_index or '-'}   "
                f"{_style('Elapsed', 'dim')} {elapsed}"
            ),
            f"{_style('Run', 'dim')} {self.state.run_id or '-'}   {progress}",
            _model_io_summary(self.state),
            f"{_style('Goal', 'dim')} {self.state.goal or 'Waiting for a task at the dwf> prompt'}",
            f"{_style('Selected', 'dim')} {selected_text}",
            _style("↑/↓ select  Enter details  Esc back  PgUp/PgDn scroll  q quit browser", "dim"),
        ]
        if self.state.final_path:
            summary_lines.append(f"{_style('Final', 'dim')} {self.state.final_path}")

        workers = self._worker_panel_lines()
        right_title = "Verification"
        right_lines = self._verification_panel_lines()
        if self.detail_open:
            right_title = "Inspector"
            if selected is not None:
                visible = selected.detail_lines[self.detail_scroll :]
                if self.detail_scroll:
                    visible = [f"... scrolled {self.detail_scroll} line(s) ..."] + visible
                right_lines = visible
            else:
                right_lines = ["no selected item"]

        panel_rows = max(9, min(max(len(workers), len(right_lines)), max(9, height - 17)))

        recent_events = list(self.state.recent_events)
        log_budget = max(5, min(10, height - panel_rows - 10))
        log_lines = recent_events[-log_budget:] if recent_events else ["no events yet"]

        lines: list[str] = []
        lines.extend(_box("Dynamic Workflow Dashboard", summary_lines, width))
        lines.extend(_side_by_side_box("Workers", workers, right_title, right_lines, width, panel_rows))
        lines.extend(_box("Live Log", log_lines, width, min_body_lines=log_budget))
        return lines

    def _ensure_ticker(self) -> None:
        if not self._stream_is_tty() or self._ticker_thread is not None and self._ticker_thread.is_alive():
            return
        if not self._has_active_model_activity():
            return
        self._ticker_stop.clear()
        self._ticker_thread = threading.Thread(target=self._ticker_loop, name="dwf-dashboard-refresh", daemon=True)
        self._ticker_thread.start()

    def _ticker_loop(self) -> None:
        while not self._ticker_stop.wait(0.5):
            if not self._has_active_model_activity():
                return
            self.draw()

    def _has_active_model_activity(self) -> bool:
        return any(_model_is_active(activity) for activity in self.state.model_activities.values())

    def _stream_is_tty(self) -> bool:
        return bool(hasattr(self.stream, "isatty") and self.stream.isatty())

    def _worker_panel_lines(self) -> list[str]:
        if not self.state.workers:
            return ["no workers planned yet"]
        rows = []
        selected = self.selected_navigation_item()
        for key, item in sorted(self.state.workers.items(), key=lambda kv: ((kv[1].round_index or 0), kv[1].id)):
            rows.append(_worker_line(item, selected=selected is not None and selected.key == key))
        return rows

    def _verification_panel_lines(self) -> list[str]:
        if not self.state.verifiers:
            return ["no verification steps planned yet"]
        rows = []
        selected = self.selected_navigation_item()
        for key, item in sorted(self.state.verifiers.items(), key=lambda kv: ((kv[1].round_index or 0), kv[1].id)):
            rows.append(_verifier_line(item, selected=selected is not None and selected.key == key))
        return rows


class DashboardKeyController:
    """Best-effort raw-key navigation for dashboard mode."""

    def __init__(self, renderer: DashboardRenderer, *, input_stream=None) -> None:
        self.renderer = renderer
        self.input_stream = input_stream or sys.stdin
        self._running = False
        self._thread: threading.Thread | None = None
        self._old_termios = None

    def start(self) -> bool:
        if self._running:
            return True
        if not hasattr(self.input_stream, "isatty") or not self.input_stream.isatty():
            return False
        try:
            import termios
            import tty

            fd = self.input_stream.fileno()
            self._old_termios = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:
            self._old_termios = None
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run, name="dwf-dashboard-keys", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.3)
        self._thread = None
        if self._old_termios is not None:
            try:
                import termios

                termios.tcsetattr(self.input_stream.fileno(), termios.TCSADRAIN, self._old_termios)
            except Exception:
                pass
        self._old_termios = None

    def run_blocking(self) -> None:
        if not hasattr(self.input_stream, "isatty") or not self.input_stream.isatty():
            self.renderer.draw()
            return
        try:
            import termios
            import tty

            fd = self.input_stream.fileno()
            old_termios = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:
            self.renderer.draw()
            return

        try:
            self.renderer.draw()
            while True:
                key = self._read_key()
                if key and self.renderer.handle_key(key):
                    break
        finally:
            try:
                termios.tcsetattr(self.input_stream.fileno(), termios.TCSADRAIN, old_termios)
            except Exception:
                pass

    def _run(self) -> None:
        try:
            import select
        except ImportError:
            return
        while self._running:
            try:
                ready, _, _ = select.select([self.input_stream], [], [], 0.1)
            except Exception:
                return
            if not ready:
                continue
            key = self._read_key()
            if not key:
                continue
            should_quit = self.renderer.handle_key(key)
            if should_quit:
                self._running = False

    def _read_key(self) -> str:
        try:
            char = self.input_stream.read(1)
        except Exception:
            return ""
        if char == "\x1b":
            sequence = char + self.input_stream.read(2)
            if sequence == "\x1b[A":
                return "up"
            if sequence == "\x1b[B":
                return "down"
            if sequence == "\x1b[C":
                return "right"
            if sequence == "\x1b[D":
                return "left"
            if sequence == "\x1b[5":
                self.input_stream.read(1)
                return "pageup"
            if sequence == "\x1b[6":
                self.input_stream.read(1)
                return "pagedown"
            return "esc"
        if char in {"\r", "\n"}:
            return "enter"
        if char in {"\x7f", "\b"}:
            return "backspace"
        if char == " ":
            return "space"
        if char:
            return char.lower()
        return ""


def _short_time(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(timestamp).strftime("%H:%M:%S")
    except ValueError:
        return "--:--:--"


def _status_for_event(kind: str, current: str) -> str:
    mapping = {
        "created": "created",
        "resume": "resuming",
        "planning": "planning",
        "planned": "planned",
        "round_start": "running",
        "workers_start": "running",
        "worker_start": "running",
        "worker_done": "running",
        "verify_start": "verifying",
        "verify_done": "verifying",
        "iterate": "iterating",
        "converged": "converged",
        "max_rounds": "max_rounds",
        "synthesizing": "synthesizing",
        "complete": "done",
        "cancelled": "cancelled",
    }
    return mapping.get(kind, current)


def _item_key(round_index: int | None, item_id: str) -> str:
    return f"r{round_index or 0}:{item_id}"


def _status_tag(status: str) -> str:
    tags = {
        "idle": "[IDLE]",
        "created": "[NEW]",
        "resuming": "[RESUME]",
        "planning": "[PLAN]",
        "planned": "[READY]",
        "running": "[RUN]",
        "verifying": "[VERIFY]",
        "iterating": "[ITERATE]",
        "converged": "[OK]",
        "max_rounds": "[LIMIT]",
        "synthesizing": "[WRITE]",
        "done": "[DONE]",
        "cancelled": "[CANCEL]",
    }
    return tags.get(status, f"[{status.upper()[:8]}]")


def _status_badge(status: str) -> str:
    labels = {
        "idle": ("● IDLE", "dim"),
        "created": ("● NEW", "cyan"),
        "resuming": ("● RESUME", "cyan"),
        "planning": ("◆ PLAN", "yellow"),
        "planned": ("◆ READY", "cyan"),
        "running": ("▶ RUN", "green"),
        "verifying": ("◆ VERIFY", "magenta"),
        "iterating": ("↻ ITERATE", "yellow"),
        "converged": ("✓ OK", "green"),
        "max_rounds": ("! LIMIT", "yellow"),
        "synthesizing": ("✎ WRITE", "cyan"),
        "done": ("✓ DONE", "green"),
        "cancelled": ("× CANCEL", "red"),
        "skipped": ("- SKIP", "dim"),
    }
    label, color = labels.get(status, (status.upper(), "white"))
    return _style(label, color)


def _phase_label(phase: str) -> str:
    return phase.replace("_", " ") if phase else "-"


def _progress_summary(state: DashboardState) -> str:
    total_workers = len(state.workers)
    done_workers = sum(1 for item in state.workers.values() if item.status == "done")
    running_workers = sum(1 for item in state.workers.values() if item.status == "running")
    total_verifiers = len(state.verifiers)
    done_verifiers = sum(1 for item in state.verifiers.values() if item.status == "done")
    worker_bar = _bar(done_workers, total_workers)
    verify_bar = _bar(done_verifiers, total_verifiers)
    return (
        f"{_style('Workers', 'dim')} {done_workers}/{total_workers} {worker_bar} "
        f"{_style('running', 'dim')} {running_workers}   "
        f"{_style('Verify', 'dim')} {done_verifiers}/{total_verifiers} {verify_bar}"
    )


def _model_io_summary(state: DashboardState) -> str:
    activities = list(state.model_activities.values())
    if not activities:
        return f"{_style('Model I/O', 'dim')} idle"
    active = [activity for activity in activities if _model_is_active(activity)]
    prompt_tokens = sum(activity.prompt_tokens or activity.prompt_tokens_estimate for activity in activities)
    completion_tokens = sum(activity.completion_tokens for activity in activities)
    total_tokens = sum(
        activity.total_tokens or (activity.prompt_tokens or activity.prompt_tokens_estimate) + activity.completion_tokens
        for activity in activities
    )
    latest = max(activities, key=lambda activity: activity.updated_at)
    if active:
        active_labels = ", ".join(f"{item.label} {_model_elapsed(item):.0f}s" for item in active[:3])
        tail = f"active {len(active)} [{active_labels}]"
    else:
        tail = f"last {latest.label} {latest.status}"
    return (
        f"{_style('Model I/O', 'dim')} {tail}   "
        f"{_style('sent', 'dim')} {_format_tokens(prompt_tokens)}   "
        f"{_style('recv', 'dim')} {_format_tokens(completion_tokens)}   "
        f"{_style('total', 'dim')} {_format_tokens(total_tokens)}"
    )


def _bar(done: int, total: int, *, width: int = 10) -> str:
    if total <= 0:
        return _style("░" * width, "dim")
    filled = min(width, int(round(done / total * width)))
    color = "green" if done >= total else "yellow"
    return _style("█" * filled, color) + _style("░" * (width - filled), "dim")


def _tool_count(item: DashboardItem) -> str:
    finding = item.detail.get("finding") if isinstance(item.detail.get("finding"), dict) else {}
    tool_results = finding.get("tool_results") if isinstance(finding, dict) else []
    if isinstance(tool_results, list) and tool_results:
        return str(len(tool_results))
    return "-"


def _model_brief(item: DashboardItem) -> str:
    model = item.detail.get("model") if isinstance(item.detail.get("model"), dict) else {}
    if not model:
        return _style("tok -", "dim")
    total = _int_from_usage(model.get("total_tokens"), 0)
    recv = _int_from_usage(model.get("completion_tokens"), 0)
    status = str(model.get("status") or "")
    if model.get("active"):
        elapsed = _item_model_elapsed(model)
        return _style(f"tok {_format_tokens(total)} wait {elapsed}s", "yellow")
    if status == "error":
        return _style("tok error", "red")
    return _style(f"tok {_format_tokens(total)} ↓{_format_tokens(recv)}", "dim")


def _model_detail_lines(item: DashboardItem) -> list[str]:
    model = item.detail.get("model") if isinstance(item.detail.get("model"), dict) else {}
    if not model:
        return ["Model: -", "Tokens: -"]
    status = str(model.get("status") or "-")
    elapsed = _item_model_elapsed(model)
    prompt_tokens = _int_from_usage(model.get("prompt_tokens"), 0)
    completion_tokens = _int_from_usage(model.get("completion_tokens"), 0)
    total_tokens = _int_from_usage(model.get("total_tokens"), prompt_tokens + completion_tokens)
    lines = [
        f"Model: {model.get('label') or '-'} ({status}, {elapsed}s)",
        (
            "Tokens: "
            f"sent {_format_tokens(prompt_tokens)}, "
            f"received {_format_tokens(completion_tokens)}, "
            f"total {_format_tokens(total_tokens)}"
        ),
    ]
    if model.get("error"):
        lines.append(f"Model error: {model.get('error')}")
    return lines


def _confidence_text(value: str) -> str:
    try:
        number = float(value)
    except ValueError:
        return _style(value, "dim")
    if number >= 0.8:
        return _style(value, "green")
    if number >= 0.6:
        return _style(value, "yellow")
    return _style(value, "red")


def _verdict_text(value: str) -> str:
    if value in {"accepted", "pass", "verified"}:
        return _style(value, "green")
    if value in {"disputed", "insufficient_evidence"}:
        return _style(value, "yellow")
    if value in {"rejected", "failed"}:
        return _style(value, "red")
    return _style(value, "dim")


def _worker_line(item: DashboardItem, *, selected: bool = False) -> str:
    confidence = "--" if item.confidence is None else f"{item.confidence:.2f}"
    round_part = f"r{item.round_index or '-'}"
    detail = f"{item.role}: {item.title}".strip(": ")
    marker = _style("▸", "cyan") if selected else " "
    tools = _tool_count(item)
    model = _model_brief(item)
    return (
        f"{marker} {round_part:<3} {item.id:<6} {_status_badge(item.status)} "
        f"conf {_confidence_text(confidence)}  tools {tools:<2} {model} {detail}"
    )


def _verifier_line(item: DashboardItem, *, selected: bool = False) -> str:
    round_part = f"r{item.round_index or '-'}"
    verdict = item.verdict or "--"
    followup = "--" if item.needs_followup is None else str(item.needs_followup)
    marker = _style("▸", "cyan") if selected else " "
    model = _model_brief(item)
    return (
        f"{marker} {round_part:<3} {item.id:<6} {_status_badge(item.status)} "
        f"{item.role:<10} verdict {_verdict_text(verdict)}  followup {followup} {model}"
    )


def _plan_detail_lines(plan: dict[str, Any]) -> list[str]:
    lines = [
        f"Goal: {plan.get('goal', '-')}",
        "",
        "Success criteria:",
    ]
    lines.extend(_bullet_lines(plan.get("success_criteria")))
    lines.append("")
    lines.append("Subtasks:")
    for subtask in _dict_list(plan.get("subtasks")):
        lines.append(f"- {subtask.get('id', '?')}: {subtask.get('title', '-')}")
        lines.append(f"  role: {subtask.get('agent_role', '-')}")
        depends_on = subtask.get("depends_on") or []
        lines.append(f"  depends_on: {', '.join(depends_on) if depends_on else '-'}")
        prompt = str(subtask.get("prompt") or "")
        if prompt:
            lines.extend(_wrapped_prefixed("  prompt: ", prompt))
    lines.append("")
    policy = plan.get("convergence_policy") if isinstance(plan.get("convergence_policy"), dict) else {}
    lines.append("Convergence policy:")
    lines.append(f"- max_rounds: {policy.get('max_rounds', '-')}")
    lines.append(f"- min_confidence: {policy.get('min_confidence', '-')}")
    lines.append(f"- require_no_critical_disputes: {policy.get('require_no_critical_disputes', '-')}")
    return lines


def _dispatcher_detail_lines(plan: dict[str, Any]) -> list[str]:
    lines = ["Parallel dispatch groups:"]
    groups = _dict_list(plan.get("parallel_groups"))
    if not groups:
        lines.append("- none")
    for group in groups:
        subtask_ids = group.get("subtask_ids") if isinstance(group.get("subtask_ids"), list) else []
        lines.append(f"- {group.get('id', '?')}: concurrency={group.get('max_concurrency', '-')}")
        lines.append(f"  fan_out: {', '.join(str(item) for item in subtask_ids) if subtask_ids else '-'}")
    lines.append("")
    lines.append("Verification routing:")
    steps = _dict_list(plan.get("verification_steps"))
    if not steps:
        lines.append("- none")
    for step in steps:
        target_ids = step.get("target_subtask_ids") if isinstance(step.get("target_subtask_ids"), list) else []
        lines.append(f"- {step.get('id', '?')}: mode={step.get('mode', '-')}")
        lines.append(f"  targets: {', '.join(str(item) for item in target_ids) if target_ids else '-'}")
        prompt = str(step.get("prompt") or "")
        if prompt:
            lines.extend(_wrapped_prefixed("  prompt: ", prompt))
    return lines


def _worker_detail_lines(item: DashboardItem) -> list[str]:
    finding = item.detail.get("finding") if isinstance(item.detail.get("finding"), dict) else {}
    subtask = item.detail.get("subtask") if isinstance(item.detail.get("subtask"), dict) else {}
    confidence = "--" if item.confidence is None else f"{item.confidence:.2f}"
    lines = [
        f"Worker: {item.id}",
        f"Role: {item.role or subtask.get('agent_role', '-')}",
        f"Status: {item.status}",
        f"Confidence: {confidence}",
        *_model_detail_lines(item),
        "",
        f"Title: {item.title or subtask.get('title', '-')}",
    ]
    prompt = str(subtask.get("prompt") or "")
    if prompt:
        lines.extend(_wrapped_prefixed("Prompt: ", prompt))
        lines.append("")
    if not finding:
        lines.append("Finding: not available yet")
        return lines
    lines.extend(_wrapped_prefixed("Claim: ", str(finding.get("claim") or "-")))
    lines.append("")
    lines.append("Evidence:")
    lines.extend(_bullet_lines(finding.get("evidence")))
    lines.append("")
    lines.append("Limitations:")
    lines.extend(_bullet_lines(finding.get("limitations"), empty="- none"))
    lines.append("")
    lines.append("Recommended next steps:")
    lines.extend(_bullet_lines(finding.get("recommended_next_steps"), empty="- none"))
    lines.append("")
    lines.append("Tool results:")
    lines.extend(_tool_result_lines(finding.get("tool_results")))
    return lines


def _verifier_detail_lines(item: DashboardItem) -> list[str]:
    result = item.detail.get("verification") if isinstance(item.detail.get("verification"), dict) else {}
    step = item.detail.get("verification_step") if isinstance(item.detail.get("verification_step"), dict) else {}
    lines = [
        f"Verifier: {item.id}",
        f"Mode: {item.role or step.get('mode', '-')}",
        f"Status: {item.status}",
        f"Verdict: {item.verdict or result.get('verdict', '--')}",
        f"Needs follow-up: {item.needs_followup if item.needs_followup is not None else result.get('needs_followup', '--')}",
        *_model_detail_lines(item),
        "",
    ]
    target_ids = step.get("target_subtask_ids") or result.get("target_subtask_ids") or []
    if isinstance(target_ids, list):
        lines.append(f"Targets: {', '.join(str(item_id) for item_id in target_ids) if target_ids else '-'}")
    prompt = str(step.get("prompt") or "")
    if prompt:
        lines.extend(_wrapped_prefixed("Prompt: ", prompt))
    if not result:
        lines.append("")
        lines.append("Verification result: not available yet")
        return lines
    lines.append("")
    lines.append("Issues:")
    lines.extend(_bullet_lines(result.get("issues"), empty="- none"))
    lines.append("")
    lines.append("Counterarguments:")
    lines.extend(_bullet_lines(result.get("counterarguments"), empty="- none"))
    confidence = result.get("confidence")
    lines.append("")
    lines.append(f"Verifier confidence: {confidence if confidence is not None else '--'}")
    return lines


def _bullet_lines(value: Any, *, empty: str = "- none") -> list[str]:
    if not isinstance(value, list) or not value:
        return [empty]
    lines: list[str] = []
    for item in value:
        lines.extend(_wrapped_prefixed("- ", str(item)))
    return lines


def _tool_result_lines(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["- none"]
    lines: list[str] = []
    for result in value:
        if not isinstance(result, dict):
            continue
        ok = "ok" if result.get("ok") else "error"
        name = result.get("name", "tool")
        summary = result.get("summary") or result.get("error") or ""
        lines.append(f"- {name} [{ok}]: {summary}")
        data = result.get("data")
        if isinstance(data, dict):
            for preview in _tool_data_preview(data):
                lines.append(f"  {preview}")
    return lines or ["- none"]


def _tool_data_preview(data: dict[str, Any]) -> list[str]:
    if isinstance(data.get("files"), list):
        return [f"file: {item.get('path')} ({item.get('size')} bytes)" for item in data["files"][:5] if isinstance(item, dict)]
    if isinstance(data.get("matches"), list):
        return [
            f"{item.get('path')}:{item.get('line')}: {item.get('text')}"
            for item in data["matches"][:5]
            if isinstance(item, dict)
        ]
    if isinstance(data.get("results"), list):
        return [
            f"{item.get('title')} - {item.get('url')}"
            for item in data["results"][:5]
            if isinstance(item, dict)
        ]
    if isinstance(data.get("path"), str):
        return [f"path: {data.get('path')}", f"content: {str(data.get('content', ''))[:160]}"]
    if isinstance(data.get("url"), str):
        return [f"url: {data.get('url')}", f"content: {str(data.get('content', ''))[:160]}"]
    return []


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _model_is_active(activity: ModelActivity) -> bool:
    return activity.status in {"preparing", "waiting"}


def _model_elapsed(activity: ModelActivity) -> float:
    if _model_is_active(activity):
        return max(0.0, time.time() - activity.started_at)
    return max(0.0, activity.elapsed_seconds)


def _item_model_elapsed(model: dict[str, Any]) -> int:
    if model.get("active") and isinstance(model.get("started_at"), (int, float)):
        return max(0, int(time.time() - float(model["started_at"])))
    return _int_from_usage(model.get("elapsed_seconds"), 0)


def _int_from_usage(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return default


def _format_tokens(value: int) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def _wrapped_prefixed(prefix: str, text: str, *, width: int = 96) -> list[str]:
    pieces = wrap(text, width=max(20, width - len(prefix))) or [""]
    lines = [prefix + pieces[0]]
    continuation = " " * len(prefix)
    for piece in pieces[1:]:
        lines.append(continuation + piece)
    return lines


def _side_by_side_box(
    left_title: str,
    left_lines: list[str],
    right_title: str,
    right_lines: list[str],
    width: int,
    min_body_lines: int,
) -> list[str]:
    left_width = max(36, (width - 1) // 2)
    right_width = max(36, width - left_width - 1)
    if left_width + right_width + 1 > width:
        right_width = width - left_width - 1
    body_lines = max(min_body_lines, len(left_lines), len(right_lines))
    left_box = _box(left_title, left_lines, left_width, min_body_lines=body_lines)
    right_box = _box(right_title, right_lines, right_width, min_body_lines=body_lines)
    return [left + " " + right for left, right in zip(left_box, right_box)]


def _box(title: str, content: list[str], width: int, *, min_body_lines: int = 0) -> list[str]:
    width = max(12, width)
    inner_width = width - 2
    body_width = max(1, width - 4)
    rows = list(content)
    while len(rows) < min_body_lines:
        rows.append("")
    title_rule = _title_rule(title, inner_width)
    return [
        "╭" + title_rule + "╮",
        *[f"│ {_pad(_fit(row, body_width), body_width)} │" for row in rows],
        "╰" + ("─" * inner_width) + "╯",
    ]


def _title_rule(title: str, width: int) -> str:
    clean = _style(f" {title.strip()} ", "bold")
    visible = _visible_len(clean)
    if visible >= width:
        return _fit(clean, width)
    left = max(1, (width - visible) // 2)
    right = width - visible - left
    return ("─" * left) + clean + ("─" * right)


def _rule(width: int, label: str = "") -> str:
    if not label:
        return "-" * width
    label = label[: max(0, width - 2)]
    left = max(1, (width - len(label)) // 2)
    right = max(1, width - len(label) - left)
    return ("-" * left) + label + ("-" * right)


def _fit(text: str, width: int) -> str:
    if width <= 1:
        return ""
    if _visible_len(text) <= width:
        return text
    return _truncate_ansi(text, max(0, width - 1)) + "…"


def _pad(text: str, width: int) -> str:
    return text + (" " * max(0, width - _visible_len(text)))


def _visible_len(text: str) -> int:
    return len(ANSI_RE.sub("", text))


def _truncate_ansi(text: str, width: int) -> str:
    visible = 0
    output: list[str] = []
    index = 0
    while index < len(text) and visible < width:
        match = ANSI_RE.match(text, index)
        if match:
            output.append(match.group(0))
            index = match.end()
            continue
        output.append(text[index])
        visible += 1
        index += 1
    if any(part.startswith("\033[") for part in output):
        output.append(STYLES["reset"])
    return "".join(output)


def _style(text: str, style: str) -> str:
    if not text:
        return text
    code = STYLES.get(style)
    if not code:
        return text
    return f"{code}{text}{STYLES['reset']}"
