"""Compaction service（dsh compaction 能力族的轻量版）。

dsh：``packages/compaction/*``——``ctx.compaction`` 服务 +
``compaction/start|summary|end`` 事件（摘要落为 user/message 替换 shadowed
范围）+ 工具结果剪枝。mini 版：

- 压力检测用字符数估算（无 token-meter 服务）；
- 摘要是纯规则（保留最近 N 条，丢弃部分压成一段 "Earlier context: …" 文本；
  LLM 摘要为扩展方向）；
- 无 command-compact 人工命令 / 无 scope / rank；
- ``make_snip_listener`` = dsh ``compaction-tool-result-pruner`` / javis
  ``make_snip_listener`` 同款（``tools/post-execute`` waterfall 监听器）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .session import Session
from .types import (
    PostToolDecision,
    SessionEvents,
    TextBlock,
    UserMessage,
)

DEFAULT_MAX_CHARS = 40_000
DEFAULT_KEEP_MESSAGES = 10
DEFAULT_SNIP_MAX_CHARS = 8_000

#: 可被 compaction shadow 的消息型事件（按日志顺序）。
_MESSAGE_EVENT_TYPES = frozenset(
    {"user/message", "assistant/message", "tool/call", "tool/result"}
)


@dataclass
class CompactionResult:
    compaction_id: str
    start_seq: int
    summary_seq: int
    end_seq: int
    summary: str
    shadowed_seqs: tuple[int, ...]


def _message_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", ()) or ():
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
        for part in getattr(block, "content", ()) or ():
            if isinstance(part, TextBlock):
                parts.append(part.text)
    return " ".join(parts)


class Compaction:
    """The ``"compaction"`` service（dsh ``ctx.compaction``）。"""

    def __init__(
        self,
        ctx: Any,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        keep_messages: int = DEFAULT_KEEP_MESSAGES,
    ) -> None:
        self._ctx = ctx
        self._max_chars = max_chars
        self._keep = max(1, int(keep_messages))
        self._locked = False
        self._count = 0

    # -- dsh API 面 ---------------------------------------------------------

    def compact_if_needed(self, session: Session, trigger: str = "pressure") -> CompactionResult | None:
        """自动策略（pressure / context-overflow）：低于阈值直接 None。"""
        if self._estimate_chars(session) < self._max_chars:
            return None
        return self._compact(session, trigger)

    def compact_now(self, session: Session) -> CompactionResult | None:
        """人工一次（dsh ``compactNow``；mini 无 /compact 命令，直接调用）。"""
        return self._compact(session, "manual")

    # -- 内部 ---------------------------------------------------------------

    def _estimate_chars(self, session: Session) -> int:
        total = 0
        for event in session.events:
            message = (event.data or {}).get("message")
            if message is None:
                continue
            total += len(_message_text(message))
        return total

    def _shadowed_so_far(self, session: Session) -> set[int]:
        shadowed: set[int] = set()
        for event in session.events_of(SessionEvents.COMPACTION_SUMMARY):
            shadowed.update((event.data or {}).get("shadowedSeqs", ()))
        return shadowed

    def _compact(self, session: Session, trigger: str) -> CompactionResult | None:
        if self._locked:
            return None  # dsh：未配对的 start 阻塞所有入口
        self._locked = True
        self._count += 1
        compaction_id = f"compaction-{self._count}"
        start = session.append(
            SessionEvents.COMPACTION_START,
            {"turn": None, "trigger": trigger, "compactionId": compaction_id},
        )
        try:
            shadowed, summary_text = self._pick_and_summarize(session)
            if not shadowed:
                session.append(
                    SessionEvents.COMPACTION_END,
                    {"turn": None, "compactionId": compaction_id},
                )
                return None
            summary_message = UserMessage(
                content=(TextBlock(text=summary_text),),
                source={
                    "kind": "compaction-checkpoint",
                    "compactionId": compaction_id,
                    "trigger": trigger,
                },
            )
            summary_event = session.append(
                SessionEvents.COMPACTION_SUMMARY,
                {
                    "summary": summary_text,
                    "shadowedSeqs": list(shadowed),
                    "compactionId": compaction_id,
                },
            )
            session.append(
                SessionEvents.USER_MESSAGE,
                {"message": summary_message, "compactionId": compaction_id},
            )
            end = session.append(
                SessionEvents.COMPACTION_END,
                {"turn": None, "compactionId": compaction_id},
            )
            return CompactionResult(
                compaction_id=compaction_id,
                start_seq=start.seq,
                summary_seq=summary_event.seq,
                end_seq=end.seq,
                summary=summary_text,
                shadowed_seqs=shadowed,
            )
        except BaseException as exc:  # —— 以 compaction/end(error) 收尾
            session.append(
                SessionEvents.COMPACTION_END,
                {"turn": None, "error": str(exc), "compactionId": compaction_id},
            )
            raise
        finally:
            self._locked = False

    def _pick_and_summarize(self, session: Session) -> tuple[tuple[int, ...], str | None]:
        """规则摘要：保留最近 N 条消息事件，丢弃部分压成一段 Earlier-context 文本。"""
        shadowed_so_far = self._shadowed_so_far(session)
        message_events = [
            event
            for event in session.events
            if event.type in _MESSAGE_EVENT_TYPES and event.seq not in shadowed_so_far
        ]
        if len(message_events) <= self._keep:
            return (), None
        head = message_events[: -self._keep]
        shadowed = tuple(event.seq for event in head)
        parts: list[str] = []
        for event in head:
            message = (event.data or {}).get("message")
            if message is None:
                continue
            text = _message_text(message).strip()
            if text:
                parts.append(text[:80].replace("\n", " "))
        summary = "Earlier context (compacted): " + " | ".join(parts)
        return shadowed, summary


def make_snip_listener(max_chars: int = DEFAULT_SNIP_MAX_CHARS) -> Callable[..., Any]:
    """``tools/post-execute`` 监听器：截断超限工具结果（dsh pruner 同款）。

    契约：``(exec_input, result, next)``——调用 ``next()`` 放行链路；超限则
    返回截断后的 :class:`PostToolDecision`；未超限返回 None。
    """

    def listener(_exec: Any, result: Any, next: Callable[[], Any]) -> Any:
        next()
        text_blocks = [b for b in result.content if isinstance(b, TextBlock)]
        total = sum(len(b.text) for b in text_blocks)
        if total <= max_chars:
            return None
        new_content: list[Any] = []
        remaining = max_chars
        for block in result.content:
            if isinstance(block, TextBlock):
                if remaining <= 0:
                    continue
                if len(block.text) > remaining:
                    new_content.append(
                        TextBlock(
                            text=block.text[:remaining]
                            + f"\n... [truncated by compaction: {len(block.text)} chars]"
                        )
                    )
                    remaining = 0
                else:
                    new_content.append(block)
                    remaining -= len(block.text)
            else:
                new_content.append(block)
        return PostToolDecision(content=new_content)

    return listener
