"""Behavior tests for the reusable terminal input/output wrapper."""

from __future__ import annotations

import asyncio
from io import StringIO

from javis.app.cli import terminal as terminal_module
from javis.app.cli.terminal import Terminal


class TrackingStringIO(StringIO):
    """String stream that records explicit flushes."""

    def __init__(self, initial_value: str = "") -> None:
        super().__init__(initial_value)
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


def test_read_line_writes_prompt_and_preserves_line_ending() -> None:
    stdin = StringIO("hello\n")
    stdout = TrackingStringIO()
    terminal = Terminal(stdin=stdin, stdout=stdout)

    line = terminal.read_line("You: ")

    assert line == "hello\n"
    assert stdout.getvalue() == "You: "
    assert stdout.flush_count == 1


def test_read_line_returns_none_at_eof() -> None:
    terminal = Terminal(stdin=StringIO(""), stdout=StringIO())

    assert terminal.read_line() is None


def test_write_and_write_line_flush_stdout() -> None:
    stdout = TrackingStringIO()
    terminal = Terminal(stdout=stdout)

    terminal.write("first")
    terminal.write_line("second")

    assert stdout.getvalue() == "firstsecond\n"
    assert stdout.flush_count == 2


def test_error_writes_go_to_stderr() -> None:
    stdout = TrackingStringIO()
    stderr = TrackingStringIO()
    terminal = Terminal(stdout=stdout, stderr=stderr)

    terminal.write_error("failed")
    terminal.write_error_line("!")

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "failed!\n"
    assert stderr.flush_count == 2


def test_string_streams_are_not_interactive() -> None:
    terminal = Terminal(stdin=StringIO(), stdout=StringIO())

    assert terminal.is_interactive is False


def test_read_line_async_reads_non_interactive_stream() -> None:
    terminal = Terminal(stdin=StringIO("async\n"), stdout=StringIO())

    assert asyncio.run(terminal.read_line_async()) == "async\n"


def test_read_line_uses_prompt_session_for_interactive_streams(monkeypatch) -> None:
    prompts: list[str] = []

    class FakePromptSession:
        def __init__(self, *, input, output) -> None:
            pass

        def prompt(self, message: str) -> str:
            prompts.append(message)
            return "typed"

    monkeypatch.setattr(terminal_module, "PromptSession", FakePromptSession)
    monkeypatch.setattr(terminal_module, "create_input", lambda stream: stream)
    monkeypatch.setattr(terminal_module, "create_output", lambda stream: stream)
    terminal = Terminal(stdin=TtyStringIO(), stdout=TtyStringIO())

    line = terminal.read_line("You: ")

    assert line == "typed\n"
    assert prompts == ["You: "]


def test_terminal_is_exported_from_the_cli_package() -> None:
    from javis.app.cli import Terminal as ExportedTerminal

    assert ExportedTerminal is Terminal


class TtyStringIO(StringIO):
    """String stream that reports itself as an interactive terminal."""

    def isatty(self) -> bool:
        return True
