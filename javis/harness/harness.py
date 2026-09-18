"""Harness — the javis-side harness over the dsh-style ``AgentLoop``.

``Harness`` implements :class:`javis.contracts.harness.Harness` (the host's
single seam): it owns the javis conversation mirror (``ConversationMessage``),
accumulates usage, and yields ``AgentEvent`` streams per turn — driven by the
dsh-style loop in ``javis.harness.agent`` (phase state machine, inbox, session
event log, exclusive/parallel tool scheduling, ``agent/*`` waterfalls).

Assembly (mirrors the demo's ``driver`` plugin):

- every service the loop needs comes from the root context and was provided by
  its own composition row — ``llm`` (``javis.llm.LlmRuntime`` adapter
  registry), ``agentTools`` (the live view over the host tool registry),
  ``systemPrompt``, ``agentLoop``. ``Harness`` builds nothing privately;
  ``javis.harness.plugins.harness`` is the row that constructs it.
- middleware registered on this context: ``tools/execute`` permission checker
  (``Harness.set_permission_checker``), ``agent/request`` model routing so
  ``set_model`` takes effect, ``agent/limit`` max-steps status. Tool-output
  snip lives in its own ``snip`` row.

The turn bridge maps the session event log to ``AgentEvent`` (text/reasoning
deltas, tool start/result, turn end with per-turn usage) and maintains the
javis message mirror (user / tool results as user messages / assistant with
tool uses) so session save/restore round-trips.
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

from javis.contracts.harness import Harness as HarnessContract
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
from javis.contracts.services import (
    AGENT_LOOP_SERVICE,
    AGENT_TOOLS_SERVICE,
    LLM_SERVICE,
    SYSTEM_PROMPT_SERVICE,
)
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

from .agent import AgentLoop
from .session import Session
from .types import (
    AgentOptions,
    Events,
    MutableLoopConfig,
    ReasoningDeltaChunk,
    SessionEvents,
    TextDeltaChunk,
    ToolCallBlock,
    ToolExecutionResult,
)
from .types import (
    AssistantMessage as DshAssistantMessage,
)
from .types import (
    TextBlock as DshTextBlock,
)
from .types import (
    ToolResultBlock as DshToolResultBlock,
)
from .types import (
    ToolResultMessage as DshToolResultMessage,
)
from .types import (
    UserMessage as DshUserMessage,
)

_IMAGE_PLACEHOLDER = "[image omitted: engine does not process images]"

_SUB_AGENT_MAX_DEPTH = 2


class Harness(HarnessContract):
    """javis-side harness over a dsh-style ``AgentLoop``."""

    def __init__(
        self,
        ctx: Context,
        *,
        provider_name: str,
        model: str,
        system_prompt: str = "",
        cwd: str | Path = "",
        workspace: str | Path = "",
        session_id: str = "",
        max_turns: int | None = None,
        tool_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._ctx = ctx
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

        # -- services from the root context (provided by composition rows) ---
        # These four are what the loop reads on every turn; anything missing is
        # a broken composition, so name all of them up front instead of failing
        # later inside the loop.
        llm = ctx.get(LLM_SERVICE)
        agent_tools = ctx.get(AGENT_TOOLS_SERVICE)
        self._prompt_service = ctx.get(SYSTEM_PROMPT_SERVICE)
        loop_service = ctx.get(AGENT_LOOP_SERVICE)
        missing = [
            (name, row)
            for name, value, row in (
                (LLM_SERVICE, llm, "javis.harness.plugins.llm"),
                (AGENT_TOOLS_SERVICE, agent_tools, "javis.harness.plugins.agent_tools"),
                (
                    SYSTEM_PROMPT_SERVICE,
                    self._prompt_service,
                    "javis.harness.plugins.system_prompt",
                ),
                (AGENT_LOOP_SERVICE, loop_service, "javis.harness.plugins.agent_loop"),
            )
            if value is None
        ]
        if missing:
            details = ", ".join(f"'{name}' (add a row 'name: {row}')" for name, row in missing)
            raise RuntimeError(f"harness assembly is missing required service(s): {details}")
        # Never mutate the row's config in place: a third-party row may publish
        # the frozen ``AgentLoopConfig``. Copy its values into a mutable config
        # (assigned back so the live loop and ``set_max_turns`` stay in sync).
        provided = getattr(loop_service, "config", None) or loop_service
        self._loop_config = MutableLoopConfig(
            max_parallel_tool_calls=getattr(provided, "max_parallel_tool_calls", 4),
            max_steps_per_turn=getattr(provided, "max_steps_per_turn", 20),
            history_compressor=getattr(provided, "history_compressor", None),
            default_max_steps_per_turn=getattr(provided, "default_max_steps_per_turn", None),
        )
        if hasattr(loop_service, "config"):
            loop_service.config = self._loop_config
        self._default_max_steps = max(
            1,
            int(
                getattr(provided, "default_max_steps_per_turn", None)
                or getattr(provided, "max_steps_per_turn", 20)
            ),
        )
        # ctor-level max_turns wins over the row's max_steps_per_turn (the
        # CLI ``--max-turns`` override path).
        self._loop_config.max_steps_per_turn = (
            self._max_turns if self._max_turns is not None else self._default_max_steps
        )

        # -- middleware on the harness's own context -------------------------
        ctx.on(Events.TOOLS_EXECUTE, self._permission_listener)
        ctx.on(Events.AGENT_REQUEST, self._request_middleware)
        ctx.on(Events.AGENT_LIMIT, self._on_agent_limit)

        self._reset_session()

    # ------------------------------------------------------------------
    # Assembly / lifecycle
    # ------------------------------------------------------------------

    def _reset_session(self) -> None:
        """Fresh dsh session + agent (clear / load_messages start from zero)."""
        self._session = Session(self._session_id, cwd=self._cwd, on_append=self._on_append)
        self._agent = AgentLoop(
            self._ctx,
            self._session_id,
            AgentOptions(provider=self._provider_name or "javis", model=self._model),
            self._session,
        )

    def _on_append(self, _seq: int, _type: str, _data: dict[str, Any]) -> None:
        """Session append observer → wake the turn bridge (thread-safe enough:
        appends and bridge runs on the same event loop)."""
        # 这个回调由 Session.append 在**每次写入后**同步调用（session.py）。
        # 它不是“交数据”，只是按门铃：事件本体已经在日志里，桥醒来后自己按游标去取。
        # 所以三个参数都不用（名字前缀下划线就是这个意思）。
        # 为什么不是 asyncio.Queue：队列会引入“谁消费/漏消费”的状态；
        # Event + 游标扫描是幂等的——多叫醒几次无害，醒来先扫快照也不会丢事件。
        if self._append_event is not None:
            self._append_event.set()

    # ------------------------------------------------------------------
    # Harness properties
    # ------------------------------------------------------------------

    @property
    def messages(self) -> list[ConversationMessage]:
        return list(self._messages)

    @property
    def agent(self) -> AgentLoop:
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

    def set_effort(self, effort: str | None) -> None:
        self._effort = effort

    def set_max_turns(self, max_turns: int | None) -> None:
        self._max_turns = None if max_turns is None else max(1, int(max_turns))
        self._loop_config.max_steps_per_turn = (
            self._max_turns if self._max_turns is not None else self._default_max_steps
        )

    def set_permission_checker(self, checker: Any) -> None:
        """Optional Harness hook: the host's async permission callback
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
        # 契约入口（javis/contracts/harness.py）：异步生成器，调用方用
        # ``async for event in engine.submit_message(prompt)`` 边收边渲染。
        # 本方法**不自己跑循环**：它只做两件事——把消息投进 dsh inbox（followup），
        # 然后盯住 session 日志，把新事件翻译成 AgentEvent 逐个 yield 出去。
        # 事件约定：每个 yield 都是“日志里已经落盘的事实”的派生，正常以
        # AgentTurnEnd 收尾，失败时以 AgentError 收尾并 return。
        user_message = (
            prompt
            if isinstance(prompt, ConversationMessage)
            else ConversationMessage.from_user_text(prompt)
        )
        # javis 会话镜像先落地：UI 展示与会话保存/恢复都读它；
        # 而且即便后面的 dsh 循环立刻抛错，用户说过的话也不会丢。
        self._messages.append(user_message)
        # call_id → 工具名 的映射是本轮的：工具结果事件只带 call_id，
        # 要还原工具名全靠这张表，所以每次提交先清空（跨轮不残留）。
        self._call_names.clear()
        # 缓存事件循环：子 agent（run_sub_agent）会从工具的工作线程用
        # run_coroutine_threadsafe 回到这个 loop 上跑。
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        # 懒创建唤醒信号（必须在一个正在跑的 loop 里创建），之后反复复用。
        if self._append_event is None:
            self._append_event = asyncio.Event()

        # 本轮的“起跑线”：只翻译这之后新增的事件，不会把历史事件当新鲜事重发。
        # 注意 cursor 是**下标**而 start_seq 是**seq**，这里能直接相等，是因为
        # Session.append 让 seq 从 1 开始且逐条 +1 —— seq 为 N+1 的事件恰好落在下标 N。
        # 若将来支持“带种子事件恢复会话”（seq 不连续），这行就必须改成按 seq 定位。
        start_seq = self._session.events[-1].seq if self._session.events else 0
        # 真正让 agent 动起来的一步：消息进 next-turn 队列并唤醒 driver。
        # 它立即返回（不 await 循环本体），后续进展只能通过日志观察。
        self._agent.followup(_to_dsh_user(user_message))

        cursor = start_seq
        snapshot = self._session.events
        turn_ends: Any = None
        while True:
            # ① 扫完当前快照里所有新事件：逐条映射并 yield。
            #    turn/end 只记录、不提前 break，保证它之前的事件都已发出。
            while cursor < len(snapshot):
                event = snapshot[cursor]
                cursor += 1
                if event.type == SessionEvents.TURN_END:
                    turn_ends = event
                # 纯映射：不产生 UI 事件的事件（如组装好的 assistant/message）返回 None，
                # 它只更新 javis 镜像，所以 UI 的文本来自 delta 累加 + turn end 校正。
                mapped = self._map_event(event)
                if mapped is not None:
                    yield mapped
            # ② 本轮已结束 → 跳出，去跑下面的汇总逻辑。
            if turn_ends is not None:
                break
            # ③ 还没结束 → 等新事件。这三行的顺序是防丢事件的关键：
            #    必须先 clear 再取快照。若反过来（先取快照后 clear），
            #    则“取快照→clear”之间新增的事件会先把 Event 置位、随即被 clear 抹掉，
            #    随后 wait() 就再也等不到人叫醒 → 转发直接挂死。
            self._append_event.clear()
            snapshot = self._session.events
            # clear 之后重取快照，若已有新事件就直接继续扫（省一次无谓等待）。
            if cursor < len(snapshot):
                continue
            # 真正空转等待；被 append 叫醒后重取快照再扫。
            await self._append_event.wait()
            snapshot = self._session.events

        # turn/end 是在 _turn 的 finally 里写的，此时 driver 可能还没回到 idle
        # （后面还有 latch / 补起 driver 的收尾），所以要等整场活动真正静下来，
        # 否则紧接着的读取会与写日志的协程竞争。
        await self._agent.when_idle()
        # 失败收尾：不产 AgentTurnEnd，只发一个可恢复的 AgentError 就 return。
        # recoverable=True 的含义：agent 已回 idle，可以直接提交下一条消息。
        reason = turn_ends.data["reason"]
        if reason.kind == "error":
            yield AgentError(message=reason.failure.message, recoverable=True)
            return

        # 汇总只统计**本轮**：日志里还堆着历史轮次，所以先取 turn 号当过滤条件。
        turn_no = turn_ends.data["turn"]
        # 本轮可能有多条 assistant/message（一个 turn 可以跑多个 step），
        # 最后一条非空文本才是给用户的答复（前面的可能是“我去读个文件”）。
        texts = [
            e.data["message"].text
            for e in self._session.events_of(SessionEvents.ASSISTANT_MESSAGE)
            if e.data.get("turn") == turn_no and e.data["message"].text
        ]
        final_text = texts[-1] if texts else ""
        # 本轮用量 = 该 turn 内**每一步**的 usage 之和（一次 turn 可能多次调模型）；
        # 只统计带 usage 的消息（适配器可以不报）。
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
        # 累计用量（状态条显示用）。
        self._usage = UsageSnapshot(
            input_tokens=self._usage.input_tokens + in_tok,
            output_tokens=self._usage.output_tokens + out_tok,
        )

        # 撞上 max-steps 时循环会 emit agent/limit，由 _on_agent_limit 记在 _last_limit。
        # 这里一次性消费：只在本轮真的撞到限制时提示一次，随后清掉，避免下一轮重复提示。
        if self._last_limit is not None and self._last_limit.get("turn") == turn_no:
            yield AgentStatus(
                message=f"reached max steps ({self._last_limit['limit']}) per turn"
            )
            self._last_limit = None
        # 契约上的结束标记：前端据此收尾（assistant_complete）。
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
    # Middleware listeners (root context)
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
        """``agent/request`` waterfall: the engine's current model and effort
        win over the loop seed, so ``set_model`` and ``set_effort`` take
        effect on the next request."""
        config = next()
        if self._model and config.model != self._model:
            config = replace(config, model=self._model)
        if self._effort is not None and config.reasoning_effort != self._effort:
            config = replace(config, reasoning_effort=self._effort)
        return config

    def _on_agent_limit(self, payload: dict[str, Any]) -> None:
        """``agent/limit`` emit: record the max-steps limit hit."""
        self._last_limit = payload

    # ------------------------------------------------------------------
    # Sub-agent spawner (wired into the adapted AgentTool)
    # ------------------------------------------------------------------

    def run_sub_agent(self, task: str) -> str:
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
        """Run one sub-task through a fresh AgentLoop (independent
        session, same llm/tools; recursion depth-capped)."""
        if self._sub_depth >= _SUB_AGENT_MAX_DEPTH:
            return "Error: sub-agent nesting too deep"
        self._sub_depth += 1
        try:
            sub_session = Session(f"{self._session_id}-sub-{uuid4().hex[:6]}")
            sub = AgentLoop(
                self._ctx,
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


__all__ = ["Harness"]
