"""示例自带的最小测试：验证会话日志折叠逻辑。

在示例目录下直接运行 `python -m pytest -q test_agentloop.py` 即可；
这样 demo 的「运行测试」场景不依赖整个仓库的测试套件。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让测试文件从仓库根目录导入示例插件（python -m pytest 时 cwd 是示例目录）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.agentloop_demo.plugins.session import Session, SessionService


def test_derive_messages_folds_session_events() -> None:
    session = Session("t")
    session.append("user/message", {"message": {"role": "user", "content": "hi"}})
    session.append(
        "assistant/message",
        {"message": {"role": "assistant", "content": "ok"}},
    )
    session.append("tool/result", {"tool_call_id": "c1", "content": "result"})

    messages = session.derive_messages(system_prompt="sys")
    assert messages == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]


def test_unknown_event_type_rejected() -> None:
    service = SessionService(emit=lambda *_args: None)
    service.create("t")
    with pytest.raises(ValueError):
        service.append("t", "nope/event", {})
