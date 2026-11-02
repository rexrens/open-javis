"""The default agent driver: an inbox in, events out.

The loop is self-driven. Input enters an :class:`Inbox` through
``agent.send()`` / ``agent.inject()``; the agent's own task claims it and runs
turns and steps. Nothing is yielded to a caller, so any number of front ends
and plugins can observe a turn instead of driving it:

- **live** (process-local) — ``agent/status``, ``agent/turn-start``,
  ``agent/step-start``, ``agent/step-end``, ``agent/assistant-stream``,
  ``agent/error``, plus the tool pipeline's ``tool/call`` / ``tool/result``;
- **durable** — the session log, broadcast by the session service as
  ``session/event``.

Events are emitted synchronously, so a listener renders in log order: the
durable ``tool/call`` is appended (and rendered) before the tool pipeline asks
for approval, and a streamed delta is rendered before the next model chunk.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .assembler import BlockAssembler
from .session import (
    Session,
    assistant_event,
    tool_call_event,
    tool_result_event,
    turn_end_event,
    turn_start_event,
    user_event,
)
from .types import ContentBlock, GenerateOptions, blocks_text, system_message

if TYPE_CHECKING:
    from javis.cordis import Context

#: Where a queued message waits: the next turn, or the next step of this one.
TARGET_NEXT_TURN = "next-turn"
TARGET_NEXT_STEP = "next-step"
TARGETS = (TARGET_NEXT_TURN, TARGET_NEXT_STEP)

IDLE = "idle"
WORKING = "working"
DISPOSED = "disposed"


class Inbox:
    """The agent's two ordered pending-input lists (Cordis/dsh ``InboxTarget``).

    ``next-turn`` opens a turn and is claimed one message at a time (that is
    what makes two quick ``send()`` calls two turns). ``next-step`` is
    *steering*: it rides along with the turn that is already running — or
    waits, without waking the loop, for the next waking message.
    """

    def __init__(self) -> None:
        self._lists: dict[str, deque[str]] = {target: deque() for target in TARGETS}

    def insert(self, target: str, text: str) -> None:
        if target not in self._lists:
            raise ValueError(f'unknown inbox target "{target}", expected one of {TARGETS}')
        self._lists[target].append(text)

    def claim_turn_input(self) -> list[str]:
        """Everything parked for the next step, plus one next-turn message."""
        claimed = self.claim_step_input()
        if self._lists[TARGET_NEXT_TURN]:
            claimed.append(self._lists[TARGET_NEXT_TURN].popleft())
        return claimed

    def claim_step_input(self) -> list[str]:
        steering = list(self._lists[TARGET_NEXT_STEP])
        self._lists[TARGET_NEXT_STEP].clear()
        return steering

    def has_turn_input(self) -> bool:
        return bool(self._lists[TARGET_NEXT_TURN])

    def has_step_input(self) -> bool:
        return bool(self._lists[TARGET_NEXT_STEP])

    def pending(self) -> dict[str, list[str]]:
        return {target: list(items) for target, items in self._lists.items()}

    def __repr__(self) -> str:
        return f"<Inbox {self.pending()}>"


@dataclass
class AgentResult:
    """How the agent's most recent turn settled."""

    text: str = ""
    finish: str = "stop"
    failure: dict[str, Any] | None = None
    steps: int = 0


class Agent:
    """One conversation driver bound to a session, provider and model."""

    def __init__(
        self,
        ctx: "Context",
        session: Session,
        provider: str,
        model: str,
        max_steps: int = 12,
    ):
        self.ctx = ctx
        self.session = session
        self.provider = provider
        self.model = model
        self.max_steps = max_steps
        self.inbox = Inbox()
        self.result = AgentResult()
        self.status = IDLE
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def id(self) -> str:
        return self.session.id

    def __repr__(self) -> str:
        return f"<Agent {self.id} {self.status}>"

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> "Agent":
        """Start the driver loop (idempotent); the service calls this once."""
        if self._task is None and not self._closed:
            self._task = asyncio.ensure_future(self._drive())
        return self

    async def dispose(self) -> None:
        """Cancel a running turn, stop the loop, and wait for it to settle."""
        if self._closed and self._task is None:
            return
        self._closed = True
        self._wake.set()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except BaseException:  # noqa: BLE001 - the loop is being torn down
                pass
            self._task = None
        self._set_status(DISPOSED)

    # -- input --------------------------------------------------------------

    def send(self, text: str, target: str = TARGET_NEXT_TURN, wakeup: bool = True) -> None:
        """Queue input, waking the loop by default."""
        if self._closed:
            raise RuntimeError("agent is disposed")
        self.inbox.insert(target, text)
        if wakeup:
            # Work is owed from this moment: a concurrent `wait_idle()` must
            # not report the agent settled before the loop claims the message.
            self._idle.clear()
            self._wake.set()

    def inject(self, text: str) -> None:
        """Park steering input for the next step without waking the loop."""
        self.send(text, TARGET_NEXT_STEP, wakeup=False)

    def cancel(self) -> bool:
        """Cancel the running turn; returns whether one was running."""
        task = self._turn_task
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def wait_idle(self) -> None:
        """Wait until no turn is running and no turn is owed.

        Parked steering input (``inject``) does not owe a turn, so it does not
        keep this waiting — it rides along with the next waking message.
        """
        while True:
            await self._idle.wait()
            if not self.inbox.has_turn_input():
                return
            # A queued turn is about to start; let the driver claim it before
            # blocking on the event again.
            await asyncio.sleep(0)

    # -- driver -------------------------------------------------------------

    async def _drive(self) -> None:
        try:
            while not self._closed:
                await self._wake.wait()
                self._wake.clear()
                while not self._closed and self.inbox.has_turn_input():
                    await self._run_turn_once()
        finally:
            self._idle.set()

    async def _run_turn_once(self) -> None:
        """Run one turn in its own task so :meth:`cancel` can stop it."""
        self._idle.clear()
        self._set_status(WORKING)
        task = asyncio.ensure_future(self._run_turn())
        self._turn_task = task
        try:
            await task
        except asyncio.CancelledError:
            if self._closed:
                raise
            # One cancelled turn is not a cancelled loop: stay available.
        except Exception as error:  # noqa: BLE001 - a broken turn must not kill the loop
            self._emit_error(0, 0, {"code": "AGENT_ERROR", "message": f"{type(error).__name__}: {error}"})
        finally:
            self._turn_task = None
            if not self._closed:
                self._set_status(IDLE)
                self._idle.set()

    async def _run_turn(self) -> None:
        turn = self.session.turns + 1
        claimed = self.inbox.claim_turn_input()
        self.result = AgentResult()
        self._append(turn_start_event(turn))
        self.ctx.emit("agent/turn-start", self, turn)
        try:
            for text in claimed:
                self._append(user_event(text))
            for step in range(self.max_steps):
                self.result.steps = step + 1
                self.ctx.emit("agent/step-start", self, turn, step)
                finish_kind, calls = await self._run_step(turn, step)
                self.ctx.emit("agent/step-end", self, turn, step, finish_kind)
                if finish_kind in ("error", "aborted"):
                    self.result.finish = finish_kind
                    break
                steering = self.inbox.claim_step_input()
                for text in steering:
                    self._append(user_event(text))
                if not calls and not steering:
                    self.result.finish = finish_kind
                    break
            else:
                self.result.finish = "max-steps"
                self._emit_error(
                    turn,
                    self.max_steps,
                    {"code": "MAX_STEPS", "message": f"stopped after {self.max_steps} steps"},
                )
        except asyncio.CancelledError:
            self.result.finish = "aborted"
            raise
        finally:
            # The turn always closes durably, however it ended.
            self._append(turn_end_event(turn))
            self.ctx.emit("agent/turn-end", self, turn, self.result.finish)

    async def _run_step(self, turn: int, step: int) -> tuple[str, list[ContentBlock]]:
        """One model request plus the tools it asked for."""
        assembler = BlockAssembler()
        async for chunk in self.ctx.get("llm").stream(self._request()):
            self.ctx.emit("agent/assistant-stream", self, chunk)
            assembler.push(chunk)

        blocks = assembler.blocks()
        finish = assembler.finish
        finish_kind = finish.kind if finish is not None else "stop"
        failure = finish.failure.to_json() if finish is not None and finish.failure else None
        visible = blocks_text(blocks)
        if visible:
            self.result.text += visible
        if blocks:
            self._append(assistant_event(blocks, assembler.usage, finish_kind))
        if finish_kind in ("error", "aborted"):
            self.result.failure = failure
            self._emit_error(turn, step, failure or {"code": "ERROR", "message": "the model call failed"})
            return finish_kind, []

        calls = [block for block in blocks if block.get("type") == "tool-call"]
        for call in calls:
            await self._run_tool_call(call)
        return finish_kind, calls

    async def _run_tool_call(self, call: ContentBlock) -> None:
        call_id, name = call.get("id", ""), call.get("name", "")
        arguments = call.get("arguments", "")
        # Durable first: subscribers render the call before the tool runs.
        self._append(tool_call_event(call_id, name, arguments))
        self.ctx.emit("tool/call", call_id, name, arguments)
        result = await self.ctx.get("tools").execute(call_id, name, arguments, cwd=self.session.cwd)
        self._append(tool_result_event(call_id, name, result.text, result.is_error))
        self.ctx.emit("tool/result", call_id, name, result.text, result.is_error)

    # -- helpers ------------------------------------------------------------

    def _append(self, event: dict[str, Any]) -> None:
        """Log one durable fact; the session service broadcasts it."""
        self.session.append(event)

    def _emit_error(self, turn: int, step: int, failure: dict[str, Any]) -> None:
        self.ctx.emit("agent/error", self, turn, step, failure)

    def _set_status(self, status: str) -> None:
        self.status = status
        self.ctx.emit("agent/status", self, status)

    def _request(self) -> GenerateOptions:
        tools = self.ctx.get("tools")
        prompt = self.ctx.get("systemPrompt")
        schemas = tools.schemas() if tools is not None else []
        text = prompt.render(schemas, self.session.cwd) if prompt is not None else ""
        messages = [system_message(text)] if text else []
        messages.extend(self.session.messages())
        return GenerateOptions(
            provider=self.provider,
            model=self.model,
            messages=messages,
            tools=schemas,
        )


class AgentsService:
    """Agent factory and registry installed as ``ctx.agents``."""

    def __init__(self, ctx: "Context", provider: str, model: str, max_steps: int = 12):
        self.ctx = ctx
        self.provider = provider
        self.model = model
        self.max_steps = max_steps
        self._agents: list[Agent] = []

    def create(
        self,
        session: Session,
        provider: str | None = None,
        model: str | None = None,
        max_steps: int | None = None,
    ) -> Agent:
        """Create an agent and start its loop."""
        agent = Agent(
            self.ctx,
            session,
            provider or session.provider or self.provider,
            model or session.model or self.model,
            max_steps or self.max_steps,
        )
        self._agents.append(agent)
        return agent.start()

    def get(self, agent_id: str) -> Agent | None:
        """The live agent driving the session with this id, if any."""
        for agent in self._agents:
            if agent.id == agent_id:
                return agent
        return None

    def agents(self) -> list[Agent]:
        """Every agent this service created, in creation order."""
        return list(self._agents)

    async def aclose(self) -> None:
        """Dispose every live agent (the plugin does this on unload)."""
        agents = list(self._agents)
        self._agents.clear()
        for agent in agents:
            await agent.dispose()
