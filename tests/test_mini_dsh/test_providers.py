"""providers: ScriptedAdapter (LLM 协议) + OpenAICompatAdapter 流转换."""
import json
import sys
import types as pytypes
from types import SimpleNamespace

import pytest
from core import types as t
from core.llm import BlockAssembler
from providers import OpenAICompatAdapter, ScriptedAdapter, scenario_script


def _assemble(chunks: list) -> list:
    assembler = BlockAssembler()
    for chunk in chunks:
        assembler.push(chunk)
    return assembler.blocks


@pytest.mark.asyncio
async def test_scripted_adapter_implements_llm_protocol():
    script = scenario_script("text")
    adapter = ScriptedAdapter(script, model="mini-scripted")
    assert adapter.model == "mini-scripted"
    prepared = adapter.prepare_call(t.LlmCallConfig(provider="scripted", model="mini-scripted"))
    assert prepared is not None
    got = [c async for c in adapter.stream(t.GenerateOptions(provider="scripted", model="mini-scripted"))]
    blocks = _assemble(got)
    assert any(getattr(b, "type", "") == "text" for b in blocks)


def test_all_scenarios_are_available_and_deterministic():
    for name in ("text", "tools", "retry", "steer", "skills", "instructions", "compaction"):
        script = scenario_script(name)
        assert isinstance(script, list) and len(script) >= 1
        assert scenario_script(name) == script  # 确定性


def test_tools_scenario_calls_set_note_then_weather_twice():
    script = scenario_script("tools")
    calls = []
    for response in script:
        blocks = _assemble(response)
        calls.extend(
            (b.name, json.loads(b.arguments))
            for b in blocks
            if getattr(b, "type", "") == "tool-call"
        )
    assert calls[0] == ("set_note", {"text": "remember: parrot"})
    assert [name for name, _ in calls[1:]] == ["weather", "weather"]


# ---------------------------------------------------------------------------
# OpenAICompatAdapter：流转换必须过 BlockAssembler 验证（离线 fake 客户端）
# ---------------------------------------------------------------------------


def _delta(content=None, tool_calls=(), reasoning=None):
    return SimpleNamespace(reasoning_content=reasoning, content=content, tool_calls=list(tool_calls))


def _fake_tool_call(index, id=None, name=None, args=""):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=args))


def _install_fake_openai(monkeypatch, stream_chunks: list, captured: list) -> None:
    """替换 sys.modules["openai"]：捕获请求参数、回放固定流（无网络）。"""

    class _FakeCompletions:
        async def create(self, **params):
            captured.append(params)

            async def gen():
                for chunk in stream_chunks:
                    yield chunk

            return gen()

    class _FakeAsyncOpenAI:
        def __init__(self, api_key=None, base_url=None):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    fake_mod = pytypes.ModuleType("openai")
    fake_mod.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_mod)


def _text_tool_chunks() -> list:
    """真实模型典型响应：文本 + 两个工具调用（分片下发）+ usage。"""
    return [
        SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=_delta(content="I'll check both."))]),
        SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(delta=_delta(tool_calls=[_fake_tool_call(0, id="call_0", name="set_note", args='{"text": "n"}')]))],
        ),
        SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(delta=_delta(tool_calls=[_fake_tool_call(1, id="call_1", name="weather", args='{"city": "Paris"}')]))],
        ),
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7), choices=[]),
    ]


@pytest.mark.asyncio
async def test_openai_adapter_sends_its_own_model(monkeypatch):
    """adapter 是 model 属主：请求里的 options.model（driver 种子）不得泄漏到 API 调用。"""
    captured: list = []
    _install_fake_openai(monkeypatch, _text_tool_chunks(), captured)
    adapter = OpenAICompatAdapter(model="deepseek-chat", api_key="sk-test")
    # prepare_call 是 adapter 绑定路由的位置：请求与会话日志 request/context
    # 都派生自 prepared.config，driver 的种子 model 必须在这里被改写
    prepared = adapter.prepare_call(t.LlmCallConfig(provider="scripted", model="mini-scripted"))
    assert prepared.config.model == "deepseek-chat"
    options = t.GenerateOptions(provider="scripted", model="mini-scripted", messages=())
    chunks = [c async for c in adapter.stream(options)]
    assert chunks, "fake stream must produce chunks (finish at least)"
    assert captured, "create() must be called"
    assert captured[0]["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_openai_adapter_text_plus_two_tool_calls_assemble(monkeypatch):
    """文本 + 2 工具调用的响应必须组装成 3 个块（顺序、内容、参数都不丢不串）。"""
    captured: list = []
    _install_fake_openai(monkeypatch, _text_tool_chunks(), captured)
    adapter = OpenAICompatAdapter(model="deepseek-chat", api_key="sk-test")
    options = t.GenerateOptions(provider="deepseek", model="deepseek-chat", messages=())
    blocks = _assemble([c async for c in adapter.stream(options)])
    assert [getattr(b, "type", "") for b in blocks] == ["text", "tool-call", "tool-call"]
    assert blocks[0].text == "I'll check both."
    assert [(b.id, b.name) for b in blocks[1:]] == [("call_0", "set_note"), ("call_1", "weather")]
    assert json.loads(blocks[1].arguments) == {"text": "n"}
    assert json.loads(blocks[2].arguments) == {"city": "Paris"}
