"""HarnessEngine — the javis-side engine over the dsh-style ReactLoopAgent.

``HarnessEngine`` implements the :class:`javis.contracts.engine.AgentEngine`
contract (the host's single seam): it owns the javis conversation mirror
(``ConversationMessage``), accumulates usage, and yields ``AgentEvent``
streams per turn — exactly like the old ``CoreCoderEngine`` did, but driven
by the dsh-style loop from ``javis.dsh`` (phase state
machine, inbox, session event log, exclusive/parallel tool scheduling,
``agent/*`` waterfalls).

Assembly (mirrors the demo's ``driver`` plugin, in-engine):

- a private loop context provides the four dsh services: ``llm`` (the
  :class:`JavisLLMAdapter` over a real ``javis.contracts.llm.LLMProvider``),
  ``tools`` (the core registry adapted from the javis tool registry the
  runtime provided — plugins included), ``systemPrompt`` (the runtime's
  system prompt + session context) and ``agentLoop`` (loop config);
- middleware registered on the loop context: ``tools/execute`` permission
  checker (``AgentEngine.set_permission_checker``), ``tools/post-execute``
  tool-output snip (compression), ``agent/request`` model routing so
  ``set_model`` takes effect, ``agent/limit`` max-steps status;

The turn bridge maps the session event log to ``AgentEvent`` (text/reasoning
deltas, tool start/result, turn end with per-turn usage) and maintains the
javis message mirror (user / tool results as user messages / assistant with
tool uses) so session save/restore round-trips.

Design note (double-context): javis' plugin context and the engine's loop
context are deliberately separate — the two contracts share service names
(``tools`` / ``llm``) with different shapes. Cost: javis plugins can't hook
the dsh waterfalls directly (v1 accepts this; the engine owns its
middleware). V2 evolution: the loop context becomes a child of the plugin
context with ``isolate("tools")`` so events flow to plugin listeners.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from javis.contracts.engine import AgentEngine
from javis.contracts.llm import LLMProvider
from javis.contracts.messages import (
    ConversationMessage,
    ToolUseBlock,
)
from javis.contracts.messages import (
    TextBlock as JTextBlock,
)
from javis.contracts.messages import (
    ToolResultBlock as JToolResultBlock,
)
from javis.contracts.tools import ToolRegistry as JavisToolRegistry
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentReasoningDelta,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.contracts.usage import UsageSnapshot
from javis.cordis import Context
from javis.dsh.agent import ReactLoopAgent
from javis.dsh.contracts import (
    AgentLoop,
    AgentOptions,
    Events,
    ReasoningDeltaChunk,
    SessionEvents,
    TextDeltaChunk,
    ToolCallBlock,
    ToolExecutionResult,
)
from javis.dsh.contracts import (
    AssistantMessage as DshAssistantMessage,
)
from javis.dsh.contracts import (
    TextBlock as DshTextBlock,
)
from javis.dsh.contracts import (
    ToolResultBlock as DshToolResultBlock,
)
from javis.dsh.contracts import (
    ToolResultMessage as DshToolResultMessage,
)
from javis.dsh.contracts import (
    UserMessage as DshUserMessage,
)
from javis.dsh.session import Session
from javis.dsh.tools import ToolRegistry as CoreToolRegistry

from .compression import (
    HISTORY_MAX_MESSAGES,
    MAX_TOOL_OUTPUT_CHARS,
    HistoryCompressor,
    make_snip_listener,
)
from .llm_adapter import JavisLLMAdapter
from .prompt import HarnessPromptService
from .tool_adapter import adapt_registry

_IMAGE_PLACEHOLDER = "[image omitted: engine does not process images]"

_SUB_AGENT_MAX_DEPTH = 2


class _MutableLoopConfig:
    """Mutable stand-in for the frozen ``AgentLoopConfig`` dataclass.

    ``set_max_turns`` mutates ``max_steps_per_turn`` live; the core reads
    attributes via ``getattr`` so any object shape works.
    """

    def __init__(
        self,
        max_parallel_tool_calls: int,
        max_steps_per_turn: int,
        history_compressor: Any,
    ) -> None:
        self.max_parallel_tool_calls = max(1, int(max_parallel_tool_calls))
        self.max_steps_per_turn = max(1, int(max_steps_per_turn))
        self.history_compressor = history_compressor


class HarnessEngine(AgentEngine):
    """javis-side engine over a dsh-style ``ReactLoopAgent``."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        provider_name: str,
        model: str,
        system_prompt: str = "",
        cwd: str | Path = "",
        workspace: str | Path = "",
        session_id: str = "",
        max_turns: int | None = None,
        tool_metadata: dict[str, Any] | None = None,
        javis_tools: JavisToolRegistry | None = None,
        max_parallel_tool_calls: int = 4,
        max_steps_per_turn: int = 20,
        history_max_messages: int = HISTORY_MAX_MESSAGES,
        tool_output_max_chars: int = MAX_TOOL_OUTPUT_CHARS,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._model = model
        self._system_prompt = system_prompt
        self._cwd = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
        self._workspace = str(Path(workspace).expanduser().resolve()) if workspace else self._cwd
        self._session_id = session_id
        self._max_turns = None if max_turns is None else max(1, int(max_turns))
        self._tool_metadata = dict(tool_metadata or {})
        self._effort: str | None = None
        self._usage = UsageSnapshot()
        self._permission_checker: Any = None
        self._messages: list[ConversationMessage] = []
        self._call_names: dict[str, str] = {}
        self._last_limit: dict[str, Any] | None = None
        self._sub_depth = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._append_event: asyncio.Event | None = None
        self._default_max_steps = max(1, int(max_steps_per_turn))

        # -- inner loop context: the four dsh services ----------------------
        self._loop_ctx = Context()
        self._adapter = JavisLLMAdapter(provider)
        self._loop_ctx.provide("llm", self._adapter)
        if javis_tools is not None:
            self._core_tools = adapt_registry(
                javis_tools, self._loop_ctx, sub_agent_factory=self._run_sub_agent
            )
        else:
            self._core_tools = CoreToolRegistry(self._loop_ctx)
        self._loop_ctx.provide("tools", self._core_tools)
        self._prompt_service = HarnessPromptService(
            self._loop_ctx,
            system_prompt,
            cwd=self._cwd,
            workspace=self._workspace,
            session_id=self._session_id,
        )
        self._loop_ctx.provide("systemPrompt", self._prompt_service)
        self._loop_config = _MutableLoopConfig(
            max_parallel_tool_calls=max_parallel_tool_calls,
            max_steps_per_turn=self._max_turns if self._max_turns is not None else self._default_max_steps,
            history_compressor=HistoryCompressor(history_max_messages),
        )
        self._loop_ctx.provide("agentLoop", AgentLoop(self._loop_config))

        # -- middleware on the loop context ---------------------------------
        self._loop_ctx.on(Events.TOOLS_EXECUTE, self._permission_listener)
        self._loop_ctx.on(Events.TOOLS_POST_EXECUTE, make_snip_listener(tool_output_max_chars))
        self._loop_ctx.on(Events.AGENT_REQUEST, self._request_middleware)
        self._loop_ctx.on(Events.AGENT_LIMIT, self._on_agent_limit)

        self._reset_session()

    # ------------------------------------------------------------------
    # Assembly / lifecycle
    # ------------------------------------------------------------------

    def _reset_session(self) -> None:
        """Fresh dsh session + agent (clear / load_messages start from zero)."""
        self._session = Session(self._session_id, cwd=self._cwd, on_append=self._on_append)
        self._agent = ReactLoopAgent(
            self._loop_ctx,
            self._session_id,
            AgentOptions(provider=self._provider_name or "javis", model=self._model),
            self._session,
        )

    def _on_append(self, _seq: int, _type: str, _data: dict[str, Any]) -> None:
        """Session append observer → wake the turn bridge (thread-safe enough:
        appends and bridge runs on the same event loop)."""
        if self._append_event is not None:
            self._append_event.set()

    # ------------------------------------------------------------------
    # AgentEngine properties
    # ------------------------------------------------------------------

    @property
    def messages(self) -> list[ConversationMessage]:
        return list(self._messages)

    @property
    def agent(self) -> ReactLoopAgent:
        """The inner dsh-style agent (used by host legacy hooks / tests)."""
        return self._agent

    @property
    def total_usage(self) -> UsageSnapshot:
        return self._usage

    @property
    def model(self) -> str:
        return self._model

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def max_turns(self) -> int | None:
        return self._max_turns

    @property
    def tool_metadata(self) -> dict[str, Any]:
        return self._tool_metadata

    # ------------------------------------------------------------------
    # Setters (called by the runtime / host on config changes)
    # ------------------------------------------------------------------

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt
        self._prompt_service.set_system_prompt(prompt)

    def set_model(self, model: str) -> None:
        self._model = model
        self._adapter.set_model(model)

    def set_effort(self, effort: str | None) -> None:
        self._effort = effort

    def set_max_turns(self, max_turns: int | None) -> None:
        self._max_turns = None if max_turns is None else max(1, int(max_turns))
        self._loop_config.max_steps_per_turn = (
            self._max_turns if self._max_turns is not None else self._default_max_steps
        )

    def set_permission_checker(self, checker: Any) -> None:
        """Optional AgentEngine hook: the host's async permission callback
        (``checker(tool_name, arguments) -> "allow" | deny-reason``) is
        consulted by the ``tools/execute`` middleware before every tool run."""
        self._permission_checker = checker

    def clear(self) -> None:
        self._messages.clear()
        self._usage = UsageSnapshot()
        self._call_names.clear()
        self._reset_session()

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        """Rebuild the dsh session from javis history (session restore)."""
        self._messages = list(messages)
        self._call_names.clear()
        self._reset_session()
        for message in messages:
            _append_to_session(self._session, message)

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[AgentEvent]:
        """Run one user turn through the dsh loop, bridging the session event
        log to the ``AgentEvent`` stream (ends with ``AgentTurnEnd``)."""
        user_message = (
            prompt
            if isinstance(prompt, ConversationMessage)
            else ConversationMessage.from_user_text(prompt)
        )
        self._messages.append(user_message)
        self._call_names.clear()
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        if self._append_event is None:
            self._append_event = asyncio.Event()

        start_seq = self._session.events[-1].seq if self._session.events else 0
        self._agent.followup(_to_dsh_user(user_message))

        cursor = start_seq
        snapshot = self._session.events
        turn_ends: Any = None
        while True:
            while cursor < len(snapshot):
                event = snapshot[cursor]
                cursor += 1
                if event.type == SessionEvents.TURN_END:
                    turn_ends = event
                mapped = self._map_event(event)
                if mapped is not None:
                    yield mapped
            if turn_ends is not None:
                break
            self._append_event.clear()
            snapshot = self._session.events
            if cursor < len(snapshot):
                continue
            await self._append_event.wait()
            snapshot = self._session.events

        await self._agent.when_idle()
        reason = turn_ends.data["reason"]
        if reason.kind == "error":
            yield AgentError(message=reason.failure.message, recoverable=True)
            return

        turn_no = turn_ends.data["turn"]
        texts = [
            e.data["message"].text
            for e in self._session.events_of(SessionEvents.ASSISTANT_MESSAGE)
            if e.data.get("turn") == turn_no and e.data["message"].text
        ]
        final_text = texts[-1] if texts else ""
        in_tok = sum(
            e.data["usage"].input_tokens
            for e in self._session.events_of(SessionEvents.ASSISTANT_MESSAGE)
            if e.data.get("turn") == turn_no and e.data.get("usage") is not None
        )
        out_tok = sum(
            e.data["usage"].output_tokens
            for e in self._session.events_of(SessionEvents.ASSISTANT_MESSAGE)
            if e.data.get("turn") == turn_no and e.data.get("usage") is not None
        )
        turn_usage = UsageSnapshot(input_tokens=in_tok, output_tokens=out_tok)
        self._usage = UsageSnapshot(
            input_tokens=self._usage.input_tokens + in_tok,
            output_tokens=self._usage.output_tokens + out_tok,
        )

        if self._last_limit is not None and self._last_limit.get("turn") == turn_no:
            yield AgentStatus(
                message=f"reached max steps ({self._last_limit['limit']}) per turn"
            )
            self._last_limit = None
        yield AgentTurnEnd(text=final_text, usage=turn_usage)

    # ------------------------------------------------------------------
    # Event bridge
    # ------------------------------------------------------------------

    def _map_event(self, event: Any) -> AgentEvent | None:
        """Map one session event to an AgentEvent (and update the javis mirror
        for durable events). Context user messages are deliberately NOT
        mirrored — the mirror is the javis conversation, not the dsh log."""
        kind = event.type
        if kind == SessionEvents.ASSISTANT_CHUNK:
            chunk = event.data["chunk"]
            if isinstance(chunk, TextDeltaChunk):
                return AgentTextDelta(text=chunk.text)
            if isinstance(chunk, ReasoningDeltaChunk):
                return AgentReasoningDelta(text=chunk.text)
            return None
        if kind == SessionEvents.TOOL_CALL:
            name = event.data["name"]
            call_id = event.data["callId"]
            self._call_names[call_id] = name
            return AgentToolCallStart(tool_name=name, tool_input=_parse_args(event.data["arguments"]))
        if kind == SessionEvents.TOOL_RESULT:
            message = event.data["message"]
            call_id = getattr(message, "call_id", "")
            block = message.content[0] if message.content else None
            text = _tool_result_text(message)
            is_error = bool(getattr(block, "is_error", False)) if block is not None else False
            self._messages.append(
                ConversationMessage(
                    role="user",
                    content=[JToolResultBlock(tool_use_id=call_id, content=text, is_error=is_error)],
                )
            )
            return AgentToolCallResult(
                tool_name=self._call_names.get(call_id, "?"),
                output=text,
                is_error=is_error,
            )
        if kind == SessionEvents.ASSISTANT_MESSAGE:
            dsh_message = event.data["message"]
            content: list[Any] = []
            for block in dsh_message.content:
                if isinstance(block, DshTextBlock):
                    content.append(JTextBlock(text=block.text))
                elif isinstance(block, ToolCallBlock):
                    content.append(
                        ToolUseBlock(id=block.id, name=block.name, input=_parse_args(block.arguments))
                    )
            self._messages.append(ConversationMessage(role="assistant", content=content))
            return None
        return None

    # ------------------------------------------------------------------
    # Middleware listeners (loop context)
    # ------------------------------------------------------------------

    async def _permission_listener(self, exec_input: Any, next: Any) -> Any:
        """``tools/execute`` waterfall: consult the host's permission checker
        before the tool body runs; deny → error result instead of executing.

        The listener is async, so it awaits the chain result itself (the core
        awaits the waterfall result once; nesting ``next()``'s coroutine would
        otherwise leak an un-awaited coroutine).
        """
        checker = self._permission_checker
        if checker is None:
            result = next()
            return await result if inspect.isawaitable(result) else result
        decision = checker(exec_input.name, exec_input.arguments)
        if inspect.isawaitable(decision):
            decision = await decision
        if decision == "allow":
            result = next()
            return await result if inspect.isawaitable(result) else result
        return ToolExecutionResult.text(f"[permission denied: {decision}]", is_error=True)

    def _request_middleware(self, payload: dict[str, Any], next: Any) -> Any:
        """``agent/request`` waterfall: the engine's current model wins over
        the loop seed, so ``set_model`` takes effect on the next request."""
        config = next()
        if self._model and config.model != self._model:
            config = replace(config, model=self._model)
        return config

    def _on_agent_limit(self, payload: dict[str, Any]) -> None:
        """``agent/limit`` emit: record the max-steps limit hit."""
        self._last_limit = payload

    # ------------------------------------------------------------------
    # Sub-agent spawner (wired into the adapted AgentTool)
    # ------------------------------------------------------------------

    def _run_sub_agent(self, task: str) -> str:
        """Synchronous entry (called from the tool adapter's worker thread):
        bridge onto the engine's event loop and run a fresh sub-agent."""
        if self._loop is None:
            return "Error: sub-agent unavailable (no event loop)"
        try:
            return asyncio.run_coroutine_threadsafe(
                self._run_sub_agent_async(task), self._loop
            ).result()
        except BaseException as exc:  # noqa: BLE001 — tool errors are text
            return f"Sub-agent error: {exc}"

    async def _run_sub_agent_async(self, task: str) -> str:
        """Run one sub-task through a fresh ReactLoopAgent (independent
        session, same llm/tools; recursion depth-capped)."""
        if self._sub_depth >= _SUB_AGENT_MAX_DEPTH:
            return "Error: sub-agent nesting too deep"
        self._sub_depth += 1
        try:
            sub_session = Session(f"{self._session_id}-sub-{uuid4().hex[:6]}")
            sub = ReactLoopAgent(
                self._loop_ctx,
                sub_session.id,
                AgentOptions(provider=self._provider_name or "javis", model=self._model),
                sub_session,
            )
            sub.followup(DshUserMessage.from_text(task))
            await sub.when_idle()
            texts = [
                e.data["message"].text
                for e in sub_session.events_of(SessionEvents.ASSISTANT_MESSAGE)
                if e.data["message"].text
            ]
            return texts[-1] if texts else "(sub-agent produced no output)"
        finally:
            self._sub_depth -= 1


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _to_dsh_user(message: ConversationMessage) -> DshUserMessage:
    """javis user message → dsh user message (text blocks; images → placeholder)."""
    parts: list[str] = []
    from javis.contracts.messages import ImageBlock

    for block in message.content:
        if isinstance(block, JTextBlock):
            parts.append(block.text)
        elif isinstance(block, ImageBlock):
            parts.append(_IMAGE_PLACEHOLDER)
    return DshUserMessage.from_text("".join(parts))


def _to_dsh_tool_result(block: JToolResultBlock) -> DshToolResultMessage:
    return DshToolResultMessage.for_call(
        block.tool_use_id,
        [DshTextBlock(text=block.content)],
        block.is_error,
    )


def _append_to_session(session: Session, message: ConversationMessage) -> None:
    """Rebuild the dsh session log from one javis conversation message."""
    if message.role == "user":
        text = message.text
        tool_results = [b for b in message.content if isinstance(b, JToolResultBlock)]
        if text:
            session.append(SessionEvents.USER_MESSAGE, {"message": DshUserMessage.from_text(text)})
        for result in tool_results:
            session.append(
                SessionEvents.TOOL_RESULT,
                {"message": _to_dsh_tool_result(result)},
            )
    elif message.role == "assistant":
        blocks: list[Any] = []
        for block in message.content:
            if isinstance(block, JTextBlock):
                blocks.append(DshTextBlock(text=block.text))
            elif isinstance(block, ToolUseBlock):
                blocks.append(
                    ToolCallBlock(id=block.id, name=block.name, arguments=json.dumps(block.input))
                )
        if blocks:
            session.append(
                SessionEvents.ASSISTANT_MESSAGE,
                {"message": DshAssistantMessage(content=tuple(blocks))},
            )


def _tool_result_text(message: DshToolResultMessage) -> str:
    """Text of one dsh tool-result message (block → text blocks)."""
    block = message.content[0] if message.content else None
    if isinstance(block, DshToolResultBlock):
        return "".join(b.text for b in block.content if isinstance(b, DshTextBlock))
    return message.text


def _parse_args(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw) if raw else {}
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


__all__ = ["HarnessEngine"]
