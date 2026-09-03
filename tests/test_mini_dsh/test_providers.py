"""providers: ScriptedAdapter (LLM 协议) + 场景工厂."""
import json

import pytest
from core import types as t
from core.llm import BlockAssembler
from providers import ScriptedAdapter, scenario_script


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
