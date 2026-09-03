"""组合集成测试：cordis.yml + agent 契约驱动（text 场景起步）。

cordis Context 无公开 dispose——每个测试独立 compose 新 ctx，进程退出自然回收；
卸载/可重复装配语义由“每测试重新 compose 都能成功”隐式覆盖。
"""
import os
import tempfile

import pytest
from core import types as t

from javis.cordis import Context
from javis.cordis.loader import Loader
from javis.cordis.registry import settle

MINI_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "examples", "mini_dsh")


async def _compose(monkeypatch: pytest.MonkeyPatch, scenario: str) -> Context:
    monkeypatch.setenv("HARNESS_DEMO_SCENARIO", scenario)
    monkeypatch.setenv("MINI_DSH_PROVIDER", "scripted")
    ctx = Context()
    # cwd = MINI_DSH_CWD（instructions 场景指向临时 workspace）否则临时目录：
    # tools 场景会把 notes.txt 写进 session cwd，不能指向仓库内目录
    ctx.baseUrl = os.environ.get("MINI_DSH_CWD") or tempfile.mkdtemp(prefix="mini-dsh-test-")
    loader_fiber = ctx.plugin(Loader, {"file": os.path.join(MINI_ROOT, "cordis.yml")})
    await loader_fiber
    await settle(ctx)
    return ctx


async def _run(ctx: Context, prompt: str) -> None:
    agent = ctx.get("agent")
    agent.followup(t.UserMessage.from_text(prompt))
    await agent.when_idle()


@pytest.mark.asyncio
async def test_composition_has_task11_services(monkeypatch: pytest.MonkeyPatch):
    """Task 11 的 4 个插件提供 7 个服务；skills/compaction 在 Task 13/15 加入，
    届时由 Task 15 的全量服务断言接管。"""
    ctx = await _compose(monkeypatch, "text")
    for service in ("sessions", "llm", "tools", "agentLoop", "systemPrompt", "agent", "session"):
        assert ctx.get(service, strict=False) is not None, f"missing service {service}"


@pytest.mark.asyncio
async def test_text_scenario_completes(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "text")
    session = ctx.get("session")
    await _run(ctx, "what is 2+2?")
    types_seen = [e.type for e in session.events]
    assert "turn/start" in types_seen and "turn/end" in types_seen
    messages = session.derive_messages()
    assert any("2 + 2 = 4" in getattr(m, "text", "") for m in messages)
