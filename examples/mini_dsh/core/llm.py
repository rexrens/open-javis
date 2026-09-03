"""LLM service contract + stream assembly.

Port of the ``@deepseek-ai/dsh-llm`` runtime surface that the agent loop
consumes:

- **``LLM``** (dsh ``LlmRuntime``) — ``prepare_call(config)`` for
  exact-model adapter resolution, ``stream(options)`` for the raw streaming
  protocol. Adapters may throw; :func:`normalized_stream` turns any
  producer failure into a terminal ``error``/``aborted`` finish so consumers
  always see a well-formed stream.
- **``BlockAssembler``** (dsh ``BlockAssembler``) — folds ``StreamChunk``
  deltas into assembled content blocks, usage, and the terminal finish reason.
- **``SystemPrompt``** (dsh ``systemPrompt``) — persona + live context
  assembly (cwd / session id / date), rendered into the system slot.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from .types import (
    AbortError,
    AbortSignal,
    BlockEndChunk,
    BlockStartChunk,
    ErrorFinish,
    FinishChunk,
    FinishReason,
    GenerateOptions,
    LlmCallConfig,
    LlmError,
    LlmFailure,
    PromptAssembly,
    PromptSection,
    ReasoningDeltaChunk,
    StopFinish,
    TextBlock,
    TextDeltaChunk,
    TokenUsage,
    ToolCallBlock,
    ToolCallDeltaChunk,
    ToolCallsFinish,
)

# ---------------------------------------------------------------------------
# Adapter / request types (dsh: llm/index.ts PreparedLlmCall, LlmConfigurableProvider)
# ---------------------------------------------------------------------------


@dataclass
class PreparedCall:
    """The adapter registration that resolved one request's exact-model defaults."""

    config: LlmCallConfig
    #: Which config fields were supplied by the adapter, not the caller
    #: (``{"reasoningEffort": True}`` etc.) — logged into the request header.
    adapter_defaults: dict[str, bool] = field(default_factory=dict)
    #: Adapter context (``{"contextWindow": int}``) when advertised.
    context: dict[str, Any] | None = None
    #: Optional retry policy (consumed by ``agent/request-error`` listeners).
    retry_policy: dict[str, Any] | None = None
    #: Adapter-bound stream for this exact-model registration; ``None`` lets
    #: the loop fall back to the provider's plain ``stream(options)``.
    stream: Callable[[GenerateOptions], AsyncIterator[Any]] | None = None


@runtime_checkable
class LLM(Protocol):
    """The model service. Implementations must be SDK-free at this seam."""

    def prepare_call(
        self, config: LlmCallConfig, signal: AbortSignal | None = None
    ) -> PreparedCall | Awaitable[PreparedCall]:
        """Resolve exact-model adapter defaults for ``config``.

        Implementations may be synchronous or asynchronous; consumers should
        await the result before dispatching the returned ``stream``.
        """
        ...

    def stream(self, options: GenerateOptions) -> AsyncIterator[Any]:
        """Emit the raw streaming protocol for one request (a coroutine object)."""
        ...


# ---------------------------------------------------------------------------
# Stream normalization
# ---------------------------------------------------------------------------


async def normalized_stream(
    stream: Any,
    options: GenerateOptions,
    signal: AbortSignal | None = None,
) -> AsyncIterator[Any]:
    """包一层 provider 流，把异常归一化为终止性 finish chunk。

    dsh 语义：adapter 实现可以抛错，但 ``LlmRuntime.stream()`` 在暴露给
    消费方之前必须把失败归一化为 terminal error / aborted finish——
    这样 agent 循环只认 finish，不感知具体异常形态。
    """
    try:
        async for chunk in stream:
            # 每个 chunk 之间都是协作式取消点：中止请求 → 抛 AbortError
            if signal is not None:
                signal.throw_if_aborted()
            yield chunk
    except AbortError:
        # 取消：终止为 aborted finish（携带取消原因），不算 provider 错误
        if signal is not None and signal.aborted:
            yield FinishChunk(reason=_aborted_finish(signal))
    except Exception as exc:  # noqa: BLE001 — normalized to a terminal finish below
        if signal is not None and signal.aborted:
            # 取消与异常同时发生：以取消为准
            yield FinishChunk(reason=_aborted_finish(signal))
            return
        if isinstance(exc, LlmError):
            # 结构化失败（TRANSIENT 等路由码）原样保留，供重试策略决策
            failure: LlmFailure = exc.failure
        else:
            # 任意异常 → UNKNOWN 码；agent 只认 code 决定可否重试
            failure = LlmFailure(message=str(exc), code="UNKNOWN")
        yield FinishChunk(reason=ErrorFinish(failure=failure))


def _aborted_finish(signal: AbortSignal) -> FinishReason:
    from .types import AbortedFinish

    cause = signal.reason
    return AbortedFinish(
        failure=LlmFailure(
            message=str(cause.detail or cause.kind),
            code="ABORTED",
        )
    )


# ---------------------------------------------------------------------------
# BlockAssembler
# ---------------------------------------------------------------------------


class BlockAssembler:
    """Fold stream chunks into assembled blocks + usage + terminal finish.

    Block indexes correlate interleaved deltas; ``block-end`` carries the
    assembled block (dsh ``BlockAssembler``). Adapters emit usage before the
    terminal finish and nothing afterward.
    """

    def __init__(self) -> None:
        self._blocks: list[Any] = []
        self._open: dict[int, dict[str, Any]] = {}
        self._index_by_type: dict[str, list[int]] = {}
        self.usage: TokenUsage | None = None
        self.finish: FinishReason | None = None

    @property
    def blocks(self) -> list[Any]:
        """已组装的块（防御性拷贝）。"""
        return list(self._blocks)

    def push(self, chunk: Any) -> None:
        """把一个流块收进组装（dsh ``BlockAssembler.push``）。

        块按 index 关联：block-start 打开槽，delta 追加内容，block-end
        关闭槽并把完整块加入结果列表。index 相同 = 同一块。
        """
        kind = chunk.type
        if kind == "block-start":
            # 打开/覆盖该 index 的槽：后续 delta 都进这个槽；
            # 同 index 二次 start 会覆盖（adapter 必须保证 index 唯一）
            state = {"block_type": chunk.block_type, "text": "", "id": "", "name": None, "args": ""}
            self._open[chunk.index] = state
            # 按类型记录 index，供中断时按类型物化部分块（_state_for）
            self._index_by_type.setdefault(chunk.block_type, []).append(chunk.index)
        elif kind == "text-delta" or kind == "reasoning-delta":
            # 文本/思考增量直接拼进对应槽
            self._open[chunk.index]["text"] += chunk.text
        elif kind == "tool-call-delta":
            # 工具调用是分段到达的（id/name/arguments 各自成块），逐段收拢
            state = self._open[chunk.index]
            if chunk.id:
                state["id"] = chunk.id
            if chunk.name:
                state["name"] = chunk.name
            state["args"] += chunk.arguments_delta
        elif kind == "block-end":
            # 收尾：关闭槽并把组装好的块按到达顺序入列
            self._open.pop(chunk.index, None)
            self._blocks.append(chunk.block)
        elif kind == "usage":
            self.usage = chunk.usage
        elif kind == "finish":
            self.finish = chunk.reason
        else:  # 未知块类型忽略（协议可合并扩展）
            return

    def _state_for(self, block_type: str) -> dict[str, Any] | None:
        indexes = self._index_by_type.get(block_type, [])
        for index in indexes:
            if index in self._open:
                return self._open[index]
        return None

    def interrupted_blocks(self) -> list[Any]:
        """The partially-assembled blocks, materialized (dsh ``interruptedBlocks``)."""
        out: list[Any] = []
        for index, state in self._open.items():
            block_type = state["block_type"]
            if block_type == "text" and state["text"]:
                out.append(TextBlock(text=state["text"]))
            elif block_type == "reasoning" and state["text"]:
                from .types import ReasoningBlock

                out.append(ReasoningBlock(text=state["text"]))
            elif block_type == "tool-call" and state["name"]:
                    out.append(
                        ToolCallBlock(id=state["id"] or f"call_{index}", name=state["name"], arguments=state["args"])
                    )
        return out

    def _replayable(self) -> list[Any]:
        return self._blocks


def assemble_finish(blocks: list[Any], usage: TokenUsage | None = None) -> FinishReason:
    """Infer a finish reason when an adapter omits one (stop vs tool-calls)."""
    if any(isinstance(block, ToolCallBlock) for block in blocks):
        return ToolCallsFinish()
    del usage
    return StopFinish()


def chunk_response(
    *,
    text: str | None = None,
    reasoning: str | None = None,
    tool_calls: list[ToolCallBlock] | None = None,
    usage: TokenUsage | None = None,
    finish: FinishReason | None = None,
) -> list[Any]:
    """为一次完整响应构造 chunk 序列（供 adapter/测试使用）。

    为 reasoning / text / tool-call 输入发出良构的块流，附带可选的
    usage chunk 与终止性 finish（对齐 dsh 形状）。
    """
    chunks: list[Any] = []
    index = 0
    if reasoning is not None:
        chunks += [
            BlockStartChunk(index=index, block_type="reasoning"),
            ReasoningDeltaChunk(index=index, text=reasoning),
            BlockEndChunk(index=index, block=_reasoning_block(reasoning)),
        ]
        index += 1
    if text is not None:
        chunks += [
            BlockStartChunk(index=index, block_type="text"),
            TextDeltaChunk(index=index, text=text),
            BlockEndChunk(index=index, block=TextBlock(text=text)),
        ]
        index += 1
    for call in tool_calls or []:
        chunks += [
            BlockStartChunk(index=index, block_type="tool-call"),
            ToolCallDeltaChunk(index=index, id=call.id, name=call.name, arguments_delta=call.arguments),
            BlockEndChunk(index=index, block=call),
        ]
        index += 1
    if usage is not None:
        from .types import UsageChunk

        chunks.append(UsageChunk(usage=usage))
    if finish is None:
        blocks: list[Any] = []
        if reasoning is not None:
            blocks.append(_reasoning_block(reasoning))
        if text is not None:
            blocks.append(TextBlock(text=text))
        blocks.extend(tool_calls or [])
        finish = assemble_finish(blocks, usage)
    chunks.append(FinishChunk(reason=finish))
    return chunks


def _reasoning_block(text: str) -> Any:
    from .types import ReasoningBlock

    return ReasoningBlock(text=text)


# ---------------------------------------------------------------------------
# SystemPrompt service (dsh: core/system-prompt)
# ---------------------------------------------------------------------------


class SystemPrompt:
    """The ``"systemPrompt"`` service: persona + live context assembly.

    dsh ``core/system-prompt`` 的轻量版：persona = 一个普通字符串，
    不做 sections 分层渲染的扩展语法；context 段 = cwd / session_id / 日期。
    """

    def __init__(self, ctx: Any, system_prompt: str, *, cwd: str, session_id: str) -> None:
        self._ctx = ctx
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._session_id = session_id

    def assemble(self, *, agent: Any = None, signal: Any = None) -> PromptAssembly:
        """组装 persona/context 分节与当前工具 schema。"""
        registry = self._ctx.get("tools")
        schemas = tuple(registry.schemas()) if hasattr(registry, "schemas") else ()
        sections = (
            PromptSection(title="Persona", body=self._system_prompt, kind="persona"),
            PromptSection(
                title="Context",
                body=f"cwd: {self._cwd} | session: {self._session_id} | date: {datetime.now(UTC).date().isoformat()}",
                kind="context",
            ),
        )
        return PromptAssembly(sections=sections, tools=schemas)

    def render_prompt(self, assembly: PromptAssembly) -> str:
        """把 persona 分节渲染成一条系统提示字符串。"""
        parts = [
            f"# {section.title}\n{section.body}"
            for section in assembly.sections
            if section.kind == "persona"
        ]
        return "\n\n".join(parts)

    def render_context(self, assembly: PromptAssembly) -> str:
        """把 context 分节渲染成步边界上下文字符串。"""
        parts = [
            f"[{section.title}] {section.body}"
            for section in assembly.sections
            if section.kind == "context"
        ]
        return " ; ".join(parts)


__all__ = [
    "LLM",
    "BlockAssembler",
    "PreparedCall",
    "SystemPrompt",
    "assemble_finish",
    "chunk_response",
    "normalized_stream",
]
