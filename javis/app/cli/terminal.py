"""Reusable terminal input and output helpers for CLI applications."""

from __future__ import annotations

import asyncio
import sys
from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_input
from prompt_toolkit.output import create_output


class Terminal:
    """Wrap terminal streams and prompt handling behind one small interface."""

    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr

    @property
    def is_interactive(self) -> bool:
        """Return whether both standard streams are attached to a terminal."""
        stdin_is_tty = getattr(self.stdin, "isatty", lambda: False)
        stdout_is_tty = getattr(self.stdout, "isatty", lambda: False)
        return bool(stdin_is_tty()) and bool(stdout_is_tty())

    def read_line(self, prompt: str = "") -> str | None:
        """Read one line, preserving its line ending and returning None at EOF."""
        if self.is_interactive:
            session = self._prompt_session()
            return f"{session.prompt(prompt)}\n"

        self.write(prompt)
        line = self.stdin.readline()
        return line if line else None

    async def read_line_async(self, prompt: str = "") -> str | None:
        """Read one line without blocking the event loop for non-interactive input."""
        if not self.is_interactive:
            return await asyncio.to_thread(self.read_line, prompt)

        session = self._prompt_session()
        return f"{await session.prompt_async(prompt)}\n"

    def _prompt_session(self) -> PromptSession[str]:
        return PromptSession(
            input=create_input(self.stdin),
            output=create_output(self.stdout),
        )

    def write(self, text: str) -> None:
        """Write text to stdout and flush it immediately."""
        self.stdout.write(text)
        self.stdout.flush()

    def write_line(self, text: str = "") -> None:
        """Write one line to stdout and flush it immediately."""
        self.write(f"{text}\n")

    def write_error(self, text: str) -> None:
        """Write text to stderr and flush it immediately."""
        self.stderr.write(text)
        self.stderr.flush()

    def write_error_line(self, text: str = "") -> None:
        """Write one line to stderr and flush it immediately."""
        self.write_error(f"{text}\n")
