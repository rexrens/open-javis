"""Session: the append-only event log and durable source of truth.

Port of ``packages/core/session`` (dsh): every conversation fact — turn/step
boundaries, user/assistant/tool messages, request headers, inbox splices — is
an immutable :class:`SessionEvent` with a monotonically increasing ``seq``.
The agent loop *appends*; consumers (UI bridges, replay, tests) read the log
and rebuild state with :meth:`Session.derive_messages`.

Only the vocabulary in ``types.SESSION_EVENT_TYPES`` is accepted — the
per-event ``ignorable`` guard of dsh is subsumed by this whitelist for the
demo (unknown event types fail at append time, not at read time).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .types import (
    SESSION_EVENT_TYPES,
    SESSION_FORMAT_VERSION,
    SessionEvents,
    SessionId,
    ToolResultMessage,
)


@dataclass
class SessionHeader:
    """Immutable validated storage metadata, kept outside the event log."""

    version: int
    id: SessionId
    #: Non-negative safe-integer Unix epoch milliseconds.
    created_at: int
    #: Absolute working directory the session was created in (if any).
    cwd: str | None = None


@dataclass
class SessionEvent:
    """One immutable event in the session log."""

    seq: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    #: Seqs of the events this one cites (e.g. a result cites its call).
    source_event_seqs: tuple[int, ...] = ()


class Session:
    """An append-only event log; ``session.append()`` is the only write path."""

    def __init__(self, id: SessionId, cwd: str | None = None) -> None:
        self.id = id
        self.header = SessionHeader(
            version=SESSION_FORMAT_VERSION,
            id=id,
            created_at=int(time.time() * 1000),
            cwd=cwd,
        )
        self._events: list[SessionEvent] = []
        self._seq = 0

    # -- read ----------------------------------------------------------------

    @property
    def events(self) -> list[SessionEvent]:
        """事件日志的快照（防御性拷贝）。"""
        return list(self._events)

    def find_last(self, type: str) -> SessionEvent | None:
        """最近一条 ``type`` 类型的事件，无则 None。"""
        for event in reversed(self._events):
            if event.type == type:
                return event
        return None

    def events_of(self, type: str) -> list[SessionEvent]:
        """全部 ``type`` 类型的事件，按日志顺序。"""
        return [event for event in self._events if event.type == type]

    def last_turn(self) -> int:
        """最近的 ``turn/start`` 的 turn 编号（无则 0）。"""
        event = self.find_last("turn/start")
        return int(event.data["turn"]) if event else 0

    def derive_messages(self) -> list[Any]:
        """重建模型所见的会话（dsh ``deriveMessages``）。

        mini 增强：任一 ``compaction/summary`` 事件 shadowedSeqs 里的消息事件
        被跳过（摘要消息本身保留——它不在 shadowed 集合里）。
        """
        shadowed: set[int] = set()
        for event in self.events_of(SessionEvents.COMPACTION_SUMMARY):
            shadowed.update((event.data or {}).get("shadowedSeqs", ()))
        out: list[Any] = []
        for event in self._events:
            if event.seq in shadowed:
                continue
            if event.type in ("user/message", "assistant/message", "tool/result"):
                out.append(event.data["message"])
        return out

    def request_header(self) -> dict[str, Any] | None:
        """The last canonical request header (config snapshot), if any."""
        event = self.find_last("request/header")
        return event.data["header"] if event else None

    def request_context(self) -> dict[str, Any] | None:
        """The last request context (provider/model/contextWindow), if any."""
        event = self.find_last("request/context")
        return event.data if event else None

    def usage_total(self) -> tuple[int, int]:
        """Sum ``(input_tokens, output_tokens)`` over every assistant message."""
        total_in = total_out = 0
        for event in self.events_of("assistant/message"):
            usage = event.data.get("usage")
            if usage:
                total_in += usage.input_tokens
                total_out += usage.output_tokens
        return total_in, total_out

    # -- write ---------------------------------------------------------------

    def append(self, type: str, data: dict[str, Any] | None = None, **options: Any) -> SessionEvent:
        """追加一条事件并返回（自带新分配的 ``seq``）。

        未知事件类型抛 ``ValueError``（dsh 在 ``ignorable`` 守卫下记日志；
        本 demo 选择快速失败）。
        """
        if type not in SESSION_EVENT_TYPES:
            raise ValueError(f"session {self.id}: unknown event type {type!r}")
        # seq 单调递增且永不重用：它是跨事件的唯一序，compaction shadow、
        # 重放对齐、变更检测都靠它。data 拷贝一份，防止调用方事后改日志。
        self._seq += 1
        event = SessionEvent(
            seq=self._seq,
            type=type,
            data=dict(data or {}),
            source_event_seqs=tuple(options.get("sourceEventSeqs") or ()),
        )
        self._events.append(event)
        return event


class SessionStore:
    """The ``"sessions"`` service: create / get with fiber-effect lifecycle.

    dsh ``SessionStore``（``packages/core/session``）的轻量版：create 走
    ``ctx.effect``（fiber 卸载即从 store 移除），announce 发 ``session/created``
    （emit）。没有 typert 注册 / fork / seed / surface 折叠。
    """

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._sessions: dict[str, Session] = {}
        self._counter = 0

    def create(self, id: str | None = None, cwd: str | None = None) -> Session:
        """创建并登记一条会话，生命周期绑定到调用方 fiber。

        调用 ``create`` 的 fiber 卸载时，会话会从 store 移除
        （dsh ``SessionStore.create``）。省略 ``id`` 时自动分配。
        """
        if id is None:
            self._counter += 1
            id = f"session-{self._counter}"
        if id in self._sessions:
            raise ValueError(f"session {id!r} already exists")
        session = Session(id, cwd=cwd)

        def setup() -> Callable[[], None]:
            """把会话登记进 store（在 create 时执行）。"""
            self._sessions[id] = session

            def disposer() -> None:
                """所属 fiber 卸载时把会话从 store 移除。"""
                self._sessions.pop(id, None)

            return disposer

        # Cordis effect contract: ``execute`` runs at create time and its
        # return value is the teardown disposer (fiber unload removes it).
        self._ctx.effect(setup, f"sessions.create({id})")
        self._ctx.emit("session/created", {"session": session})
        return session

    def get(self, id: str) -> Session | None:
        """按 ``id`` 取已存储的会话，无则 None。"""
        return self._sessions.get(id)


def create_tool_result_message(call_id: str, content: list[Any], is_error: bool = False) -> ToolResultMessage:
    """dsh ``createToolResultMessage`` — one durable tool-result message."""
    return ToolResultMessage.for_call(call_id, content, is_error)


__all__ = ["Session", "SessionEvent", "SessionHeader", "SessionStore", "create_tool_result_message"]
