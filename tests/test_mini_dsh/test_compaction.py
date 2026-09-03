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


def test_second_compact_if_needed_none_after_successful_compact():
    """回归（P1）：一次 compact 成功后，同一 session 不再重复 compact。

    修复前 _estimate_chars 把已 shadowed 的原始事件照算（压缩后估算不降反升），
    同一 session 再次 compact_if_needed("pressure") 会再次 compact 并留下孤儿
    start/end 对。阈值取在可见面估算（kept + 摘要，≈792）与全量估算（≈1836）
    之间：首次压缩应触发，压缩后估算须低于阈值。
    """
    session = Session("s1")
    _fill(session)  # 全量字符数远超阈值
    comp = Compaction(None, max_chars=1200, keep_messages=2)
    assert comp.compact_if_needed(session, "pressure") is not None  # 首次成功
    # 估算已排除 shadowed → 低于阈值，第二次不压缩
    assert comp.compact_if_needed(session, "pressure") is None
    # 第二次调用不产生新的 compaction/start 事件
    assert len(session.events_of("compaction/start")) == 1
    assert len(session.events_of("compaction/summary")) == 1
    assert len(session.events_of("compaction/end")) == 1


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
