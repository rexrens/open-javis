"""组合集成测试：cordis.yml + agent 契约驱动（text 场景起步）。

cordis Context 无公开 dispose——每个测试独立 compose 新 ctx，进程退出自然回收；
卸载/可重复装配语义由“每测试重新 compose 都能成功”隐式覆盖。
"""
import os
import tempfile
from pathlib import Path

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


@pytest.mark.asyncio
async def test_tools_scenario_exclusive_barrier(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "tools")
    session = ctx.get("session")
    await _run(ctx, "save a note and check two cities")
    calls = [e.data for e in session.events_of("tool/call")]
    results = [e for e in session.events_of("tool/result")]
    names = [e.data["name"] for e in session.events_of("tool/call")]
    # set_note 独占 → 屏障先行；weather ×2 并行
    assert names == ["set_note", "weather", "weather"]
    assert len(results) == 3

    def _result_text(event) -> str:
        # tool/result message 的内容是 ToolResultBlock（content[0]），
        # 文本块在 block.content 里（既有裁决：content[0].content 读法）
        block = event.data["message"].content[0]
        return "".join(getattr(b, "text", "") for b in block.content)

    # 结果按模型顺序提交：set_note result 的 seq 早于两个 weather result
    note_seq = next(e.seq for e in results if "note" in _result_text(e))
    weather_seqs = [e.seq for e in results if "°C" in _result_text(e)]
    assert note_seq < min(weather_seqs)
    # 事件 data 形状留档：tool/call 有 name/arguments，result 挂 message
    assert calls and all("name" in d and "arguments" in d for d in calls)
    assert all("message" in e.data for e in results)


@pytest.mark.asyncio
async def test_retry_scenario_recovers_via_waterfall(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "retry")
    session = ctx.get("session")
    await _run(ctx, "say something")
    messages = session.derive_messages()
    assert any("Recovered" in getattr(m, "text", "") for m in messages)
    # 只有一条 assistant/message：失败的尝试只留 chunk、不成消息
    assert len(session.events_of("assistant/message")) == 1
    # middleware 观察日志证明走了 waterfall
    observed = ctx.get("middleware-observed", strict=False) or []
    assert any("request-error: retry" in line for line in observed)


@pytest.mark.asyncio
async def test_steer_scenario_injected_at_step_boundary(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "steer")
    session = ctx.get("session")
    agent = ctx.get("agent")
    # 挂 steer 钩子：mock 即将发出 tool-call 时把纠正消息推进 agent inbox
    llm = ctx.get("llm")
    llm.on_tool_call = lambda: agent.steer(
        t.UserMessage.from_text("also include Tokyo's weather in your answer")
    )
    await _run(ctx, "what time is it?")
    messages = session.derive_messages()
    assert any("Tokyo" in getattr(m, "text", "") for m in messages)
    # steer 的 user/message seq 严格晚于 step 1 的 step/end（比 seq）
    step_end_1 = [e for e in session.events_of("step/end") if e.data.get("step") == 1]
    steer_msg = [e for e in session.events_of("user/message") if "Tokyo" in (e.data.get("message").text if e.data.get("message") else "")]
    assert steer_msg and step_end_1
    assert steer_msg[0].seq > step_end_1[0].seq


@pytest.mark.asyncio
async def test_skills_scenario_loads_skill_and_follows_it(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "skills")
    session = ctx.get("session")
    await _run(ctx, "save a note about autumn")
    # skill 工具被调用
    calls = [e.data for e in session.events_of("tool/call")]
    assert any(call.get("name") == "skill" for call in calls)
    # 结果含技能正文（content[0].content 读法，与 tools 场景既有裁决一致）
    results = [e for e in session.events_of("tool/result")]
    texts = []
    for e in results:
        block = e.data["message"].content[0]
        texts.append("".join(getattr(b, "text", "") for b in block.content))
    assert any("two-line poem" in text for text in texts)
    # 最终文本体现技能指令（两行诗）
    messages = session.derive_messages()
    assert any("autumn leaves" in getattr(m, "text", "") for m in messages)
    # session 日志里应有 <available_skills> 目录消息（skill 工具可见时注入）
    catalog_texts = [
        e.data["message"].text for e in session.events_of("user/message")
        if "available_skills" in (e.data.get("message").text if e.data.get("message") else "")
    ]
    assert catalog_texts
    assert "poetic-note" in catalog_texts[0]


@pytest.mark.asyncio
async def test_skills_slash_invocation_injects_body(monkeypatch: pytest.MonkeyPatch):
    """用户显式 /poetic-note 调用：技能正文作为 instructions 注入（catalog 之后）。"""
    ctx = await _compose(monkeypatch, "text")  # 无工具调用脚本
    session = ctx.get("session")
    await _run(ctx, "/poetic-note please summarize this")
    # 断言 source 标记而非子串：catalog 文本（"…two-line poems…"）也含 "two-line poem"
    # 子串，子串断言在 slash 功能完全失效时也会被目录消息满足（假阳性）。
    injected = [
        e for e in session.events_of("user/message")
        if (e.data.get("message").source or {}).get("kind") == "skill-invocation"
    ]
    assert injected, "no skill-invocation message injected"
    body = injected[0].data["message"].text
    assert body.startswith("# Skill: poetic-note")
    # instructions-last：注入 seq 严格晚于目录消息
    catalogs = [
        e for e in session.events_of("user/message")
        if (e.data.get("message").source or {}).get("kind") == "skill-catalog"
    ]
    assert catalogs and injected[0].seq > catalogs[0].seq


@pytest.mark.asyncio
async def test_instructions_baseline_injected_before_first_assistant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text(
        "# Workspace instructions\n\nAlways answer with at most 5 words.\n", encoding="utf-8"
    )
    monkeypatch.setenv("MINI_DSH_CWD", str(tmp_path))
    ctx = await _compose(monkeypatch, "instructions")
    session = ctx.get("session")
    await _run(ctx, "what is your policy?")
    # baseline 消息存在且 seq 早于首个 assistant/message
    baseline = [
        e for e in session.events_of("user/message")
        if (e.data.get("message") or None) is not None
        and (getattr(e.data["message"], "source", None) or {}).get("kind") == "agent-instructions"
    ]
    assert baseline
    first_assistant = session.events_of("assistant/message")[0]
    assert baseline[0].seq < first_assistant.seq
    # 模型按指令回答（≤5 词——由脚本保证 "Understood. Keeping it brief."）
    messages = session.derive_messages()
    assert any("Keeping it brief" in getattr(m, "text", "") for m in messages)
