"""会话插件：事件溯源会话日志（仿 ``@deepseek-ai/dsh-session``）。

dsh 的 session 是一个 append-only 事件日志：``turn/start``、
``user/message``、``assistant/chunk``、``assistant/message``、
``tool/result``、``turn/end`` 等全部作为事件追加；发给模型的完整消息
列表由日志**派生**（``deriveMessages``）。本插件实现同样的语义：
``SessionService.append()`` 只追加、不可变；``derive_messages()`` 把
日志折叠成 OpenAI chat 格式的消息列表。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# dsh 已知事件类型（KNOWN_SESSION_EVENT_TYPES 的简化版）。
KNOWN_EVENT_TYPES: tuple[str, ...] = (
    "turn/start",
    "step/start",
    "step/end",
    "user/message",
    "assistant/chunk",
    "assistant/message",
    "tool/result",
    "turn/end",
)


@dataclass(frozen=True)
class SessionEvent:
    """一条不可变的会话事件（对应 dsh 的 ``SessionEvent``）。"""

    seq: int
    type: str
    data: dict[str, Any]
    at: float = field(default_factory=time.time)


class Session:
    """一个会话 = 头部 + append-only 事件日志（对应 dsh 的 ``Session``）。"""

    def __init__(self, session_id: str, *, cwd: str | None = None, title: str = "") -> None:
        self.id = session_id
        self.created_at = time.time()
        self.cwd = cwd
        self.title = title
        self._events: list[SessionEvent] = []

    def append(self, event_type: str, data: dict[str, Any] | None = None) -> SessionEvent:
        event = SessionEvent(
            seq=len(self._events) + 1,
            type=event_type,
            data=dict(data or {}),
        )
        self._events.append(event)
        return event

    def derive_messages(self, system_prompt: str | None = None) -> list[dict[str, Any]]:
        """把事件日志折叠成模型消息（对应 dsh 的 ``deriveMessages``）。

        ``assistant/chunk`` 已折叠进 ``assistant/message``；``turn/*`` 与
        ``step/*`` 是边界事件，不参与请求。
        """
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for event in self._events:
            if event.type == "user/message" or event.type == "assistant/message":
                message = event.data.get("message")
                if isinstance(message, dict):
                    messages.append(message)
            elif event.type == "tool/result":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": event.data.get("tool_call_id", ""),
                        "content": event.data.get("content", ""),
                    }
                )
        return messages

    @property
    def events(self) -> list[SessionEvent]:
        return list(self._events)


class SessionService:
    """插件通过 ``ctx.provide("session", ...)`` 注册的服务。"""

    def __init__(self, emit: Callable[[str, Any], None]) -> None:
        self._sessions: dict[str, Session] = {}
        self._emit = emit  # ctx.emit（fire-and-forget 事件总线）

    def create(self, session_id: str, *, cwd: str | None = None, title: str = "") -> Session:
        if session_id in self._sessions:
            raise ValueError(f"session {session_id!r} already exists")
        session = Session(session_id, cwd=cwd, title=title)
        self._sessions[session_id] = session
        self._emit("session/created", {"session_id": session_id})
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise KeyError(f"session {session_id!r} not found") from None

    def append(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> SessionEvent:
        if event_type not in KNOWN_EVENT_TYPES:
            raise ValueError(
                f"unknown session event type {event_type!r} "
                f"(known: {', '.join(KNOWN_EVENT_TYPES)})"
            )
        event = self.get(session_id).append(event_type, data)
        # 持久化/统计等插件可以监听 session/event（dsh 的模式）。
        self._emit("session/event", {"session_id": session_id, "event": event})
        return event

    def derive_messages(
        self,
        session_id: str,
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.get(session_id).derive_messages(system_prompt)

    def sessions(self) -> dict[str, Session]:
        return dict(self._sessions)


def apply(ctx: Any, config: Any) -> Any:
    """激活入口：注册会话服务，并演示插件间事件通信。"""
    service = SessionService(emit=ctx.emit)
    ctx.provide("session", service)

    def on_turn_end(payload: Any) -> None:
        """监听 ``agent/turn-end``：用真实数据打印该会话的事件总数。"""
        session = service.get(payload["session_id"])
        print(
            f"  [session] {payload['session_id']} turn {payload['turn']} "
            f"结束（reason={payload['reason']}），共记录 {len(session.events)} 条事件"
        )

    ctx.on("agent/turn-end", on_turn_end)
    # 服务与监听器在内核 close 时自动撤销，无需显式 disposer。
