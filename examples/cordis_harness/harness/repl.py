"""Interactive plain-text REPL front end.

The REPL does not drive the agent. It subscribes to the agent's events,
hands input to the inbox with ``agent.send()``, and waits for the agent to go
idle:

- live ``agent/assistant-stream`` renders streamed text and reasoning;
- live ``agent/error`` renders failures;
- live ``agent/status`` tells the prompt when the agent is idle again;
- the durable ``session/event`` firehose renders tool activity, which keeps
  tool lines in log order relative to the tool pipeline's approval prompt.

Because Cordis dispatches ``emit`` synchronously, these listeners render
inline in the agent's own task: no queue, no handshake, no reordering.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import select
import signal
import sys
import threading
from typing import TYPE_CHECKING, Any, Callable

from .agent import IDLE, WORKING
from .session import SessionError

if TYPE_CHECKING:
    from javis.cordis import Context

HELP = """Commands:
  /help                 show this help
  /exit, /quit          leave the harness
  /new                  start a fresh session
  /sessions             list recent sessions
  /resume <id|latest>   continue a stored session
  /model [id]           show or switch the model
  /provider [id]        show or switch the provider route
  /tools                list the registered tools
  /yolo [on|off]        toggle automatic approval of dangerous calls
  /cwd [path]           show or change the working directory

Anything else is sent to the agent."""

RESULT_LINES = 12
RESULT_CHARS = 1200
SUMMARY_CHARS = 200
#: How long a wait polls the interrupt flag before looking again.
POLL_SECONDS = 0.2


class Repl:
    """One interactive session against the harness services."""

    def __init__(
        self,
        ctx: "Context",
        *,
        cwd: str | None = None,
        resume: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        auto_approve: bool = False,
        show_reasoning: bool = False,
        list_sessions: bool = False,
        input_fn: Callable[[str], str] | None = None,
        out: Any = None,
    ):
        self.ctx = ctx
        self.cwd = cwd
        self.resume = resume
        self.provider = provider
        self.model = model
        self.auto_approve = auto_approve
        self.show_reasoning = show_reasoning
        self.list_sessions = list_sessions
        self._input = input_fn or input
        self.out = out if out is not None else sys.stdout
        self.session: Any = None
        self.agent: Any = None
        self.exit_code = 0
        self._reasoning_open = False
        self._interrupted = False
        self._previous_sigint: Any = None
        self._idle = asyncio.Event()
        self._disposers: list[Callable[[], Any]] = []

    # -- output -------------------------------------------------------------

    def _write(self, text: str = "") -> None:
        self.out.write(text)
        self.out.flush()

    def _finish(self, code: int) -> None:
        self.exit_code = code
        self.ctx.emit("app/exit", code)

    def _banner(self, tool_count: int) -> None:
        provider = self.provider or "?"
        self._write(
            f"cordis-harness · session {self.session.id}\n"
            f"model {provider}/{self.model} · cwd {self.session.cwd} · {tool_count} tool(s)\n"
            "type /help for commands, /exit to quit\n\n"
        )

    # -- terminal -----------------------------------------------------------

    def _install_signals(self) -> None:
        """Turn Ctrl-C into a flag the run loop can act on.

        Keeping the default handler would let a stray Ctrl-C kill the process
        mid-turn; this way an interrupt cancels the running turn (or leaves the
        prompt) and the session log still gets its ``turn/end``.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        self._previous_sigint = signal.getsignal(signal.SIGINT)
        try:
            signal.signal(signal.SIGINT, self._on_sigint)
        except ValueError:  # pragma: no cover - not the main interpreter
            self._previous_sigint = None

    def _restore_signals(self) -> None:
        if self._previous_sigint is not None:
            with contextlib.suppress(ValueError):
                signal.signal(signal.SIGINT, self._previous_sigint)
            self._previous_sigint = None

    def _on_sigint(self, signum: int, frame: Any) -> None:
        self._interrupted = True

    def _interactive(self) -> bool:
        """True when the terminal itself echoes what the user types."""
        return self._input is input and sys.stdin.isatty()

    async def _prompt(self, prompt: str) -> str:
        """Write ``prompt`` and read one line; raises ``EOFError`` at end."""
        self._write(prompt)
        line = await asyncio.to_thread(self._read_line)
        if not self._interactive():
            # Piped input is not echoed back, so start the next output line.
            self._write("\n")
        if line is None:
            raise EOFError
        return line

    def _read_line(self) -> str | None:
        """Blocking line read (runs in a worker thread).

        On a terminal the read polls so an interrupt flag stops it within
        ``POLL_SECONDS``; an injected reader (tests) is used as-is. An
        unreadable stdin (closed, or captured by a test runner) is treated as
        end of input instead of raising out of the REPL task.
        """
        if self._input is not input:
            try:
                return self._input("")
            except EOFError:
                return None
        try:
            if not sys.stdin.isatty():
                line = sys.stdin.readline()
                return line if line else None
            while not self._interrupted:
                ready, _, _ = select.select([sys.stdin], [], [], POLL_SECONDS)
                if ready:
                    line = sys.stdin.readline()
                    return line if line else None
        except (OSError, ValueError):  # closed or captured stdin
            return None
        self._write("\n")
        return None

    # -- setup --------------------------------------------------------------

    def _initial_session(self, sessions: Any, cwd: str) -> Any:
        if self.resume:
            return sessions.latest() if self.resume == "latest" else sessions.open(self.resume)
        return sessions.create(cwd, self.provider or "", self.model or "")

    def _print_sessions(self, sessions: Any) -> None:
        infos = sessions.list(limit=20)
        if not infos:
            self._write(f"no sessions in {sessions.directory}\n")
            return
        for info in infos:
            self._write(f"{info.id}  {info.updated}  {info.provider}/{info.model}  {info.cwd}\n")

    # -- run ----------------------------------------------------------------

    async def run(self) -> None:
        sessions = self.ctx.get("sessions")
        agents = self.ctx.get("agents")
        tools = self.ctx.get("tools")

        if self.list_sessions:
            self._print_sessions(sessions)
            self._finish(0)
            return

        cwd = os.path.abspath(os.path.expanduser(self.cwd or os.getcwd()))
        self.provider = self.provider or agents.provider
        self.model = self.model or agents.model
        try:
            self.session = self._initial_session(sessions, cwd)
        except SessionError as error:
            self._write(f"[error] {error}\n")
            self._finish(1)
            return

        tools.set_approver(self._approve)
        await self._restart_agent()
        self._subscribe()
        self._banner(len(tools.schemas()))
        self._install_signals()
        try:
            while True:
                self._interrupted = False
                try:
                    line = (await self._prompt("» ")).strip()
                except EOFError:
                    break
                if self._interrupted:
                    break
                if not line:
                    continue
                if line.startswith("/"):
                    if await self._command(line, sessions, tools):
                        break
                    continue
                await self._send(line)
        finally:
            self._restore_signals()
            self._unsubscribe()
            with contextlib.suppress(BaseException):
                await self.agent.dispose()
            self._write("\n")
        self._finish(0)

    async def _send(self, text: str) -> None:
        """Hand one message to the agent and wait until it is idle again."""
        self._idle.clear()
        self.agent.send(text)
        aborted = False
        while not self._idle.is_set():
            try:
                await asyncio.wait_for(self._idle.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                if self._interrupted:
                    self._interrupted = False
                    if not aborted and self.agent.cancel():
                        aborted = True
                        self._close_reasoning()
                        self._write("\n[aborted]\n")
        self._close_reasoning()
        self._write("\n")

    async def _restart_agent(self) -> None:
        """Bind a fresh agent (a new route and/or session) to this front end."""
        if self.agent is not None:
            with contextlib.suppress(BaseException):
                await self.agent.dispose()
        self.agent = self.ctx.get("agents").create(self.session, self.provider, self.model)
        self._idle.set()

    # -- subscriptions ------------------------------------------------------

    def _subscribe(self) -> None:
        self._disposers = [
            self.ctx.on("agent/assistant-stream", self._on_stream),
            self.ctx.on("agent/status", self._on_status),
            self.ctx.on("agent/error", self._on_error),
            self.ctx.on("session/event", self._on_session_event),
        ]

    def _unsubscribe(self) -> None:
        disposers, self._disposers = self._disposers, []
        for dispose in disposers:
            with contextlib.suppress(BaseException):
                dispose()

    def _on_status(self, agent: Any, status: str) -> None:
        if agent is not self.agent:
            return
        if status == WORKING:
            self._idle.clear()
        elif status == IDLE:
            self._idle.set()

    def _on_stream(self, agent: Any, chunk: dict[str, Any]) -> None:
        if agent is not self.agent:
            return
        kind = chunk.get("type")
        if kind == "text-delta":
            self._close_reasoning()
            self._write(chunk.get("text", ""))
        elif kind == "reasoning-delta":
            if not self.show_reasoning:
                return
            if not self._reasoning_open:
                self._write("\n[thinking] ")
                self._reasoning_open = True
            self._write(chunk.get("text", ""))

    def _on_session_event(self, session: Any, event: dict[str, Any]) -> None:
        if session is not self.session:
            return
        kind = event.get("type")
        if kind == "tool/call":
            summary = _summarize_arguments(event.get("name", ""), event.get("arguments", ""))
            self._close_reasoning()
            self._write(f"\n[tool] {event.get('name', '')}: {summary}\n")
        elif kind == "tool/result":
            label = "failed" if event.get("isError") else "ok"
            self._write(f"[result:{label}] {_shorten(event.get('text', ''))}\n")

    def _on_error(self, agent: Any, turn: int, step: int, failure: dict[str, Any]) -> None:
        if agent is not self.agent:
            return
        self._close_reasoning()
        code = failure.get("code", "error")
        message = failure.get("message", "")
        self._write(f"\n[error] {code}{': ' + message if message else ''}\n")

    def _close_reasoning(self) -> None:
        if self._reasoning_open:
            self._write("\n")
            self._reasoning_open = False

    # -- approval -----------------------------------------------------------

    async def _approve(self, call_id: str, definition: Any, arguments: dict[str, Any]) -> bool:
        if self.auto_approve:
            return True
        summary = json.dumps(arguments, ensure_ascii=False)
        if len(summary) > SUMMARY_CHARS:
            summary = summary[:SUMMARY_CHARS] + "…"
        self._write(f"[approval] {definition.name} {summary}\n")
        try:
            answer = await self._prompt("approve? [y/N] ")
        except EOFError:
            return False
        if self._interrupted:
            return False
        return answer.strip().lower() in ("y", "yes")

    # -- commands -----------------------------------------------------------

    async def _command(self, line: str, sessions: Any, tools: Any) -> bool:
        """Handle one slash command; return ``True`` to leave the REPL."""
        parts = line.split(maxsplit=1)
        name = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if name in ("/exit", "/quit"):
            return True
        if name == "/help":
            self._write(HELP + "\n")
        elif name == "/new":
            self.session = sessions.create(self.session.cwd, self.provider or "", self.model or "")
            await self._restart_agent()
            self._write(f"[session] {self.session.id}\n")
        elif name == "/sessions":
            self._print_sessions(sessions)
        elif name == "/resume":
            if not argument:
                self._write("[error] usage: /resume <id|latest>\n")
            else:
                try:
                    target = sessions.latest() if argument == "latest" else sessions.open(argument)
                except SessionError as error:
                    self._write(f"[error] {error}\n")
                else:
                    self.session = target
                    await self._restart_agent()
                    self._write(f"[session] {self.session.id} ({len(self.session.messages())} messages)\n")
        elif name == "/model":
            if argument and argument != self.model:
                self.model = argument
                await self._restart_agent()
            self._write(f"[model] {self.provider}/{self.model}\n")
        elif name == "/provider":
            if argument and argument != self.provider:
                self.provider = argument
                await self._restart_agent()
            self._write(f"[provider] {self.provider} ({', '.join(self.ctx.get('llm').list_providers())})\n")
        elif name == "/tools":
            for schema in tools.schemas():
                approval = "needs approval" if tools.get(schema.name).approval else "no approval"
                self._write(f"{schema.name} ({approval}): {schema.description}\n")
        elif name == "/yolo":
            if argument in ("on", "off"):
                self.auto_approve = argument == "on"
            elif argument:
                self._write("[error] usage: /yolo [on|off]\n")
            self._write(f"[yolo] {'on' if self.auto_approve else 'off'}\n")
        elif name == "/cwd":
            if argument:
                target = os.path.abspath(os.path.expanduser(argument))
                if not os.path.isdir(target):
                    self._write(f"[error] not a directory: {target}\n")
                else:
                    self.session.cwd = target
            self._write(f"[cwd] {self.session.cwd}\n")
        else:
            self._write(f"[error] unknown command {name} (try /help)\n")
        return False


def _summarize_arguments(name: str, raw: str) -> str:
    try:
        arguments = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        arguments = {}
    if name == "bash" and isinstance(arguments, dict) and "command" in arguments:
        summary = str(arguments["command"])
    else:
        summary = json.dumps(arguments, ensure_ascii=False) if arguments else raw
    summary = summary.replace("\n", " ⏎ ")
    return summary[:SUMMARY_CHARS] + ("…" if len(summary) > SUMMARY_CHARS else "")


def _shorten(text: str) -> str:
    lines = text.splitlines()
    shortened = "\n".join(lines[:RESULT_LINES])
    if len(lines) > RESULT_LINES:
        shortened += f"\n… {len(lines) - RESULT_LINES} more line(s)"
    if len(shortened) > RESULT_CHARS:
        shortened = shortened[:RESULT_CHARS] + "…"
    return shortened
