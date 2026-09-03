"""Compaction service: event chain + shadow + snip listener."""
from core import types as t
from core.compaction import Compaction, make_snip_listener
from core.session import Session
from core.tools import ToolExecutionResult


def _fill(session: Session, n: int = 6) -> None:
    for i in range(n):
        session.append(t.SessionEvents.USER_MESSAGE, {"message": t.UserMessage.from_text(f"user-{i}")})
        session.append(
            t.SessionEvents.ASSISTANT_MESSAGE,
            {"message": t.AssistantMessage(content=(t.TextBlock(text=f"asst-{i}" * 50),))},
        )


def test_compact_under_pressure_shadows_old_messages():
    session = Session("s1")
    _fill(session)  # 总字符数远超阈值
    comp = Compaction(None, max_chars=10, keep_messages=2)
    result = comp.compact_if_needed(session, "pressure")
    assert result is not None
    assert result.start_seq < result.summary_seq < result.end_seq
    assert result.summary.startswith("Earlier context (compacted):")
    # 事件链成对
    assert len(session.events_of("compaction/start")) == 1
    assert len(session.events_of("compaction/summary")) == 1
    assert len(session.events_of("compaction/end")) == 1
    # shadow 生效：只留最近 2 条 + 摘要消息
    messages = session.derive_messages()
    assert len(messages) == 3
    assert messages[-1].text == result.summary
    assert "user-0" not in [m.text for m in messages]


def test_below_threshold_returns_none():
    session = Session("s1")
    session.append(t.SessionEvents.USER_MESSAGE, {"message": t.UserMessage.from_text("tiny")})
    comp = Compaction(None, max_chars=1_000_000)
    assert comp.compact_if_needed(session, "pressure") is None


def test_lock_blocks_reentrant_compact():
    session = Session("s1")
    _fill(session)
    comp = Compaction(None, max_chars=10, keep_messages=1)
    comp._locked = True  # 模拟一个未配对的 start
    assert comp.compact_now(session) is None
    assert len(session.events_of("compaction/start")) == 0


def test_snip_listener_truncates_oversized_result():
    listener = make_snip_listener(max_chars=8)
    result = ToolExecutionResult.text("x" * 100)
    decision = listener(None, result, lambda: None)
    assert decision is not None
    assert len(decision.content[0].text) < 60
    assert "truncated" in decision.content[0].text
