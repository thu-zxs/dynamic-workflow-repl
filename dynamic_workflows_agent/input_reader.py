from __future__ import annotations

import asyncio
import atexit
import os
from pathlib import Path
from typing import Protocol


DEFAULT_COMMANDS = [
    "/help",
    "/runs",
    "/resume ",
    "/inspect ",
    "/status",
    "/config",
    "/exit",
    "/quit",
]


class InputReader(Protocol):
    async def prompt(self, prompt_text: str) -> str:
        """Read one edited input line."""

    @property
    def backend_name(self) -> str:
        """Human-readable backend name."""


def create_input_reader(
    *,
    history_path: str | Path,
    commands: list[str] | None = None,
    prefer_prompt_toolkit: bool = True,
) -> InputReader:
    if prefer_prompt_toolkit and not os.environ.get("DWF_DISABLE_PROMPT_TOOLKIT"):
        reader = _try_prompt_toolkit_reader(history_path=history_path, commands=commands or DEFAULT_COMMANDS)
        if reader is not None:
            return reader
    return ReadlineInputReader(history_path=history_path, commands=commands or DEFAULT_COMMANDS)


def _try_prompt_toolkit_reader(*, history_path: str | Path, commands: list[str]) -> InputReader | None:
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.patch_stdout import patch_stdout
    except ImportError:
        return None

    history_file = Path(history_path)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    completer = WordCompleter(commands, ignore_case=True, sentence=True)
    session = PromptSession(history=FileHistory(str(history_file)), completer=completer)

    class PromptToolkitInputReader:
        backend_name = "prompt_toolkit"

        async def prompt(self, prompt_text: str) -> str:
            with patch_stdout():
                return await session.prompt_async(prompt_text)

    return PromptToolkitInputReader()


class ReadlineInputReader:
    def __init__(self, *, history_path: str | Path, commands: list[str]) -> None:
        self.history_path = Path(history_path)
        self.commands = list(commands)
        self._readline = None
        self.backend_name = "input"
        self._configure_readline()

    async def prompt(self, prompt_text: str) -> str:
        return await asyncio.to_thread(self._prompt_sync, prompt_text)

    def _prompt_sync(self, prompt_text: str) -> str:
        value = input(prompt_text)
        stripped = value.strip()
        if stripped and self._readline is not None:
            try:
                self._readline.add_history(value)
                self._readline.write_history_file(str(self.history_path))
            except OSError:
                pass
        return value

    def _configure_readline(self) -> None:
        try:
            import readline
        except ImportError:
            return

        self._readline = readline
        self.backend_name = "readline"
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            readline.read_history_file(str(self.history_path))
        except FileNotFoundError:
            pass
        except OSError:
            pass

        try:
            readline.set_history_length(500)
        except AttributeError:
            pass

        try:
            readline.parse_and_bind("tab: complete")
        except Exception:
            pass

        def complete(text: str, state: int) -> str | None:
            matches = [command for command in self.commands if command.startswith(text)]
            if state < len(matches):
                return matches[state]
            return None

        try:
            readline.set_completer(complete)
        except Exception:
            pass

        def save_history() -> None:
            try:
                readline.write_history_file(str(self.history_path))
            except OSError:
                pass

        atexit.register(save_history)
