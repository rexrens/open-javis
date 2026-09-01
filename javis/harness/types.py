"""Harness contract surface (dsh-aligned).

Python port of the deepseek-harness contract types that shape the main flow:

- ``packages/llm/llm/src/types.ts``        — blocks / chunks / finish / usage / failure
- ``packages/llm/llm/src/message.ts``      — Message / UserMessage / AssistantMessage / ToolResultMessage
- ``packages/llm/llm/src/call-config.ts``  — LlmCallConfig + callConfigEquals
- ``packages/core/agent/src/runtime-types.ts`` — agent events / decisions / statuses
- ``packages/core/agent-loop/src/agent.ts`` — TurnEndReason / AgentCancelCause
- ``packages/core/tools/src/index.ts``     — ToolExecutionInput / Result / modes

Naming is aligned with dsh (camelCase → snake_case); the *shape* and *semantics*
are what the demo is about. Everything here is a pure data contract: no
behavior beyond ``AbortController`` / ``AbortSignal`` (Python has no native
abort primitive).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

#: Provider-issued call id; correlates a tool call with its result.
CallId = str
#: One session in the store (and its persistence artifacts).
SessionId = str


# ---------------------------------------------------------------------------
# Abort (dsh: AbortController / AbortSignal)
# ---------------------------------------------------------------------------


class AbortError(Exception):
    """Raised by ``AbortSignal.throw_if_aborted``; carries the cancel cause."""

    def __init__(self, cause: AgentCancelCause) -> None:
        super().__init__(f"aborted: {cause.kind}")
        self.cause = cause


@dataclass
class AbortSignal:
    """Minimal port of ``AbortSignal``: monotonic abort with a stable cause."""

    _aborted: bool = False
    _cause: AgentCancelCause | None = None

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> AgentCancelCause | None:
        return self._cause

    def abort(self, cause: AgentCancelCause) -> None:
        if not self._aborted:  # first cause wins
            self._aborted = True
            self._cause = cause

    def throw_if_aborted(self) -> None:
        if self._aborted:
            raise AbortError(self._cause)


@dataclass
class AbortController:
    signal: AbortSignal = field(default_factory=AbortSignal)

    def abort(self, cause: AgentCancelCause) -> None:
        self.signal.abort(cause)


# ---------------------------------------------------------------------------
# LLM content blocks (dsh: llm/types.ts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextBlock:
    """Plain text visible to the end user."""

    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ReasoningBlock:
    """Reasoning / thinking content, distinct from visible text."""

    text: str
    type: Literal["reasoning"] = "reasoning"


@dataclass(frozen=True)
class ToolCallBlock:
    """A tool invocation requested by the model."""

    id: CallId
    name: str
    #: Raw JSON string as produced by the model.
    arguments: str
    type: Literal["tool-call"] = "tool-call"


@dataclass(frozen=True)
class ToolResultBlock:
    """The result of a tool invocation, sent back to the model."""

    tool_call_id: CallId
    content: tuple[Any, ...] = ()
    is_error: bool = False
    type: Literal["tool-result"] = "tool-result"


#: Any known content block; switch on ``type`` and fall through unknowns.
ContentBlock = TextBlock | ReasoningBlock | ToolCallBlock | ToolResultBlock


@dataclass(frozen=True)
class LlmFailure:
    """Serializable provider or transport failure facts; policy decides retry."""

    message: str
    #: Stable provider-neutral machine-routing code (e.g. ``TRANSIENT``).
    code: str
    status: int | None = None
    provider_retry_after_ms: int | None = None
    request_id: str | None = None


# ---------------------------------------------------------------------------
# Finish reasons & usage (dsh: llm/types.ts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StopFinish:
    kind: Literal["stop"] = "stop"


@dataclass(frozen=True)
class ToolCallsFinish:
    kind: Literal["tool-calls"] = "tool-calls"


@dataclass(frozen=True)
class MaxTokensFinish:
    kind: Literal["max-tokens"] = "max-tokens"


@dataclass(frozen=True)
class AbortedFinish:
    failure: LlmFailure
    kind: Literal["aborted"] = "aborted"


@dataclass(frozen=True)
class ErrorFinish:
    failure: LlmFailure
    kind: Literal["error"] = "error"


FinishReason = StopFinish | ToolCallsFinish | MaxTokensFinish | AbortedFinish | ErrorFinish


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting for one model call (cache fields are optional)."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None


# ---------------------------------------------------------------------------
# Stream protocol (dsh: llm/types.ts StreamChunk)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockStartChunk:
    index: int
    block_type: str
    type: Literal["block-start"] = "block-start"


@dataclass(frozen=True)
class TextDeltaChunk:
    index: int
    text: str
    type: Literal["text-delta"] = "text-delta"


@dataclass(frozen=True)
class ReasoningDeltaChunk:
    index: int
    text: str
    type: Literal["reasoning-delta"] = "reasoning-delta"


@dataclass(frozen=True)
class ToolCallDeltaChunk:
    index: int
    id: CallId
    name: str | None = None
    arguments_delta: str = ""
    type: Literal["tool-call-delta"] = "tool-call-delta"


@dataclass(frozen=True)
class BlockEndChunk:
    index: int
    block: ContentBlock
    type: Literal["block-end"] = "block-end"


@dataclass(frozen=True)
class UsageChunk:
    usage: TokenUsage
    type: Literal["usage"] = "usage"


@dataclass(frozen=True)
class FinishChunk:
    reason: FinishReason
    type: Literal["finish"] = "finish"


StreamChunk = (
    BlockStartChunk
    | TextDeltaChunk
    | ReasoningDeltaChunk
    | ToolCallDeltaChunk
    | BlockEndChunk
    | UsageChunk
    | FinishChunk
)


# ---------------------------------------------------------------------------
# Tools & call config (dsh: llm/types.ts ToolSchema, llm/call-config.ts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSchema:
    """JSON-schema description of a tool, as sent to the model."""

    name: str
    description: str
    #: JSON Schema object for the arguments.
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmCallConfig:
    """Provider routing, model, and sampling scalars of one conversation's requests."""

    provider: str
    model: str
    reasoning_effort: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stop: tuple[str, ...] | None = None


def _cfg_field(obj: Any, name: str) -> Any:
    """Field access tolerant of the request object's shape.

    ``GenerateOptions`` (the dispatch payload) carries no ``reasoning_effort``
    or ``stop``; treat them as ``None`` so prepared-call config drift checks
    work against either object type (dsh ``callConfigEquals``).
    """
    return getattr(obj, name, None)


def call_config_equals(a: LlmCallConfig, b: LlmCallConfig) -> bool:
    """Field-wise equality over :class:`LlmCallConfig` (dsh ``callConfigEquals``)."""
    if (
        a.provider != b.provider
        or a.model != b.model
        or _cfg_field(a, "reasoning_effort") != _cfg_field(b, "reasoning_effort")
        or a.temperature != b.temperature
        or a.max_tokens != b.max_tokens
    ):
        return False
    a_stop = _cfg_field(a, "stop")
    b_stop = _cfg_field(b, "stop")
    if a_stop is None or b_stop is None:
        return a_stop == b_stop
    return list(a_stop) == list(b_stop)


class LlmError(Exception):
    """A structured LLM failure: ``message`` + stable ``code`` + failure facts."""

    def __init__(self, message: str, code: str, failure: LlmFailure | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.failure = failure or LlmFailure(message=message, code=code)


@dataclass(frozen=True)
class GenerateOptions:
    """A single model request, fully assembled (dsh ``GenerateOptions``)."""

    provider: str
    model: str
    #: Ordered conversation messages, exactly as the provider sees them.
    messages: tuple[Any, ...] = ()
    #: System prompt text (adapters map to the provider's system slot).
    system: str | None = None
    #: Tool schemas (adapters map to the provider's ``tools`` field).
    tools: tuple[ToolSchema, ...] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    #: The request's abort signal (dsh carries it on the request object).
    signal: AbortSignal | None = None


# ---------------------------------------------------------------------------
# Messages (dsh: llm/message.ts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    role: str
    content: tuple[Any, ...] = ()
    #: Provenance: ``{"provider", "model"}`` for assistant, ``{"tool"}`` for results.
    source: dict[str, Any] | None = None
    #: True when the assistant message was cut short by interruption.
    interrupted: bool = False

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    @property
    def tool_calls(self) -> list[ToolCallBlock]:
        return [block for block in self.content if isinstance(block, ToolCallBlock)]


@dataclass(frozen=True)
class UserMessage(Message):
    role: str = "user"

    @staticmethod
    def from_text(text: str) -> UserMessage:
        return UserMessage(content=(TextBlock(text),))


@dataclass(frozen=True)
class AssistantMessage(Message):
    role: str = "assistant"


@dataclass(frozen=True)
class ToolResultMessage(Message):
    """A durable tool-result message (carries the correlated ``callId``)."""

    role: str = "tool"
    call_id: CallId = ""

    @staticmethod
    def for_call(call_id: CallId, content: Sequence[Any], is_error: bool = False) -> ToolResultMessage:
        return ToolResultMessage(
            content=(ToolResultBlock(tool_call_id=call_id, content=tuple(content), is_error=is_error),),
            call_id=call_id,
            source={"tool": True},
        )


# ---------------------------------------------------------------------------
# Agent runtime types (dsh: core/agent/src/runtime-types.ts)
# ---------------------------------------------------------------------------

#: Where a sent message joins the inbox.
InboxTarget = Literal["next-turn", "next-step"]

#: Agent lifecycle state, emitted on every transition as ``agent/status``.
AgentStatus = Literal["idle", "running"]


@dataclass(frozen=True)
class AgentOptions:
    """Agent creation options. Provider/model are resolved at call time."""

    provider: str | None = None
    model: str | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class AgentCancelCause:
    """Stable caller intent carried by the active operation signal."""

    kind: str  # "user" | "disposed" | "error"
    detail: str | None = None


@dataclass(frozen=True)
class TurnCompleted:
    kind: Literal["completed"] = "completed"


@dataclass(frozen=True)
class TurnMaxTokens:
    kind: Literal["max-tokens"] = "max-tokens"


@dataclass(frozen=True)
class TurnBlocked:
    kind: Literal["blocked"] = "blocked"


@dataclass(frozen=True)
class TurnAborted:
    reason: AgentCancelCause
    kind: Literal["aborted"] = "aborted"


@dataclass(frozen=True)
class TurnError:
    failure: LlmFailure
    kind: Literal["error"] = "error"


TurnEndReason = TurnCompleted | TurnMaxTokens | TurnBlocked | TurnAborted | TurnError


@dataclass(frozen=True)
class PreStepReject:
    """The loop must not enter the proposed step."""

    kind: Literal["reject"] = "reject"


@dataclass(frozen=True)
class PreStepEnter:
    """Enter the step with these messages (the default includes context)."""

    messages: tuple[Any, ...]
    kind: Literal["enter"] = "enter"


PreStepDecision = PreStepReject | PreStepEnter


@dataclass(frozen=True)
class RetryAction:
    """A listener owns model-request recovery: the loop retries the step."""

    kind: Literal["retry"] = "retry"


#: Action returned by a listener that owns model-request recovery (or None).
RequestErrorAction = RetryAction | None


# ---------------------------------------------------------------------------
# Tool execution (dsh: core/tools)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExclusiveMode:
    kind: Literal["exclusive"] = "exclusive"


@dataclass(frozen=True)
class ParallelMode:
    kind: Literal["parallel"] = "parallel"


ToolExecutionMode = ExclusiveMode | ParallelMode


@dataclass(frozen=True)
class ToolExecutionInput:
    """One tool call after argument parsing, ready to schedule."""

    call_id: CallId
    name: str
    #: Parsed arguments (invalid JSON is preserved as text, empty input → {}).
    arguments: Any
    #: The initiating agent.
    agent: Any
    #: Abort signal shared by the step.
    signal: AbortSignal


@dataclass
class ToolExecutionResult:
    """The outcome of one tool execution (content blocks for the model)."""

    content: list[Any] = field(default_factory=list)
    is_error: bool = False
    #: Structured failure facts (``message`` / ``code`` / ``info``).
    error: dict[str, Any] | None = None
    #: Tool-private presentation payload, persisted for replay.
    meta: Any = None
    #: When True, the turn completes right after this result is committed.
    concludes_turn: bool = False
    #: Extra user messages to stage into the owning step's inbox.
    additional_contexts: tuple[UserMessage, ...] = ()

    @staticmethod
    def text(text: str, is_error: bool = False) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text)], is_error=is_error)


@dataclass
class PostToolDecision:
    """``tools/post-execute`` waterfall result: may rewrite content or add context."""

    content: list[Any] | None = None
    additional_contexts: tuple[UserMessage, ...] = ()


#: Tool that was skipped after cancellation (dsh TOOL_ABORTED_BEFORE_DISPATCH).
TOOL_ABORTED_BEFORE_DISPATCH = "TOOL_ABORTED_BEFORE_DISPATCH"


# ---------------------------------------------------------------------------
# Prompt assembly (dsh: core/system-prompt)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptSection:
    title: str
    body: str
    #: ``"persona"`` sections render into the system prompt; ``"context"``
    #: sections render into the step-boundary context message.
    kind: str = "persona"


@dataclass
class PromptAssembly:
    """The assembled system prompt: ordered sections + the tool schemas."""

    sections: tuple[PromptSection, ...] = ()
    tools: tuple[ToolSchema, ...] = ()


# ---------------------------------------------------------------------------
# Loop driver config (dsh: ctx.agentLoop.config)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentLoopConfig:
    """Loop-driver configuration (dsh ``ctx.agentLoop.config``).

    javis additions over the demo copy:
    - ``max_steps_per_turn`` — hard cap on tool-call steps within one turn
      (the dsh loop has no bound; javis replaces the old ``max_rounds=50``
      semantics with this guard, default 20).
    - ``history_compressor`` — optional ``(messages) -> messages`` hook the
      loop applies after ``session.derive_messages()`` and before building
      the next request (javis' compression middleware slot).
    """

    max_parallel_tool_calls: int = 4
    max_steps_per_turn: int = 20
    history_compressor: Any = None


class AgentLoop:
    """The ``"agentLoop"`` service: the loop driver's configuration."""

    def __init__(self, config: AgentLoopConfig) -> None:
        self.config = config


# ---------------------------------------------------------------------------
# Session event types (dsh: core/session)
# ---------------------------------------------------------------------------


class SessionEvents:
    TURN_START = "turn/start"
    STEP_START = "step/start"
    USER_MESSAGE = "user/message"
    ASSISTANT_CHUNK = "assistant/chunk"
    ASSISTANT_MESSAGE = "assistant/message"
    TOOL_CALL = "tool/call"
    TOOL_RESULT = "tool/result"
    STEP_END = "step/end"
    TURN_END = "turn/end"
    REQUEST_HEADER = "request/header"
    REQUEST_CONTEXT = "request/context"
    INBOX_SPLICED = "agent/inbox/spliced"


#: The full session-log vocabulary; ``Session.append`` rejects anything else.
SESSION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        SessionEvents.TURN_START,
        SessionEvents.STEP_START,
        SessionEvents.USER_MESSAGE,
        SessionEvents.ASSISTANT_CHUNK,
        SessionEvents.ASSISTANT_MESSAGE,
        SessionEvents.TOOL_CALL,
        SessionEvents.TOOL_RESULT,
        SessionEvents.STEP_END,
        SessionEvents.TURN_END,
        SessionEvents.REQUEST_HEADER,
        SessionEvents.REQUEST_CONTEXT,
        SessionEvents.INBOX_SPLICED,
    }
)


# ---------------------------------------------------------------------------
# Live agent event names (dsh: core/agent/src/runtime-types.ts Events)
# ---------------------------------------------------------------------------


class Events:
    """Agent-subject live events and their dispatch modes.

    - ``agent/status``            — emit (fire-and-forget notification)
    - ``agent/error``             — emit
    - ``agent/inbox/*``           — emit
    - ``agent/pre-step``          — waterfall (may reject or rewrite messages)
    - ``agent/request``           — waterfall (may rewrite provider/model/config)
    - ``agent/request-error``     — waterfall (may claim recovery: ``retry``)
    - ``agent/turn-stopping``     — serial (around the turn boundary)
    - ``tools/execute``           — waterfall (wrap/replace the tool body)
    - ``tools/post-execute``      — waterfall (rewrite content / add context)
    - ``tools/result``            — emit (result committed)
    """

    AGENT_STATUS = "agent/status"
    AGENT_ERROR = "agent/error"
    AGENT_INBOX_INSERTED = "agent/inbox/inserted"
    AGENT_INBOX_CLAIMED = "agent/inbox/claimed"
    AGENT_INBOX_DISCARDED = "agent/inbox/discarded"
    AGENT_PRE_STEP = "agent/pre-step"
    AGENT_REQUEST = "agent/request"
    AGENT_REQUEST_ERROR = "agent/request-error"
    AGENT_TURN_STOPPING = "agent/turn-stopping"
    #: emit — loop limit reached (e.g. ``max-steps``); javis addition over dsh.
    AGENT_LIMIT = "agent/limit"
    TOOLS_EXECUTE = "tools/execute"
    TOOLS_POST_EXECUTE = "tools/post-execute"
    TOOLS_RESULT = "tools/result"


#: The on-disk session format version (dsh SESSION_FORMAT_VERSION).
SESSION_FORMAT_VERSION = 0


__all__ = [
    "SESSION_EVENT_TYPES",
    "SESSION_FORMAT_VERSION",
    "TOOL_ABORTED_BEFORE_DISPATCH",
    "AbortController",
    "AbortError",
    "AbortSignal",
    "AbortedFinish",
    "AgentCancelCause",
    "AgentLoop",
    "AgentLoopConfig",
    "AgentOptions",
    "AgentStatus",
    "AssistantMessage",
    "BlockEndChunk",
    "BlockStartChunk",
    "CallId",
    "ContentBlock",
    "Events",
    "ExclusiveMode",
    "FinishChunk",
    "FinishReason",
    "GenerateOptions",
    "InboxTarget",
    "LlmCallConfig",
    "LlmError",
    "LlmFailure",
    "MaxTokensFinish",
    "Message",
    "ParallelMode",
    "PostToolDecision",
    "PreStepDecision",
    "PreStepEnter",
    "PreStepReject",
    "PromptAssembly",
    "PromptSection",
    "ReasoningBlock",
    "ReasoningDeltaChunk",
    "RequestErrorAction",
    "RetryAction",
    "SessionEvents",
    "SessionId",
    "StopFinish",
    "StreamChunk",
    "TextBlock",
    "TextDeltaChunk",
    "TokenUsage",
    "ToolCallBlock",
    "ToolCallDeltaChunk",
    "ToolCallsFinish",
    "ToolExecutionInput",
    "ToolExecutionMode",
    "ToolExecutionResult",
    "ToolResultBlock",
    "ToolResultMessage",
    "ToolSchema",
    "TurnAborted",
    "TurnBlocked",
    "TurnCompleted",
    "TurnEndReason",
    "TurnError",
    "TurnMaxTokens",
    "UsageChunk",
    "UserMessage",
    "call_config_equals",
]
