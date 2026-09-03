"""Provider 层：LLM 协议的两个实现（core/llm.LLM 契约面）。

- :class:`ScriptedAdapter` —— 离线确定性模型：按脚本逐条吐 StreamChunk
  （用 ``core.llm.chunk_response`` 构造）；``retry`` 场景的响应含
  :class:`_Fault` 哨兵，stream 中途抛 ``LlmError(TRANSIENT)``（由 core 的
  ``normalized_stream`` 归一化成 error finish）。
- :class:`OpenAICompatAdapter` —— openai SDK → StreamChunk（真实模型）。

场景工厂 :func:`scenario_script` 产出 7 个确定性脚本（text/tools/retry/
steer/skills/instructions/compaction），与 dsh_harness 的 ``mock_llm`` 同思路。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from core import types as t
from core.llm import PreparedCall, chunk_response

# ---------------------------------------------------------------------------
# 场景脚本（确定性，离线）
# ---------------------------------------------------------------------------


def _text() -> list[list[Any]]:
    return [chunk_response(text="2 + 2 = 4.", reasoning="2 + 2 is basic arithmetic; the answer is 4.")]


def _tools() -> list[list[Any]]:
    return [
        chunk_response(
            tool_calls=[
                t.ToolCallBlock(id="note", name="set_note", arguments=json.dumps({"text": "remember: parrot"})),
                t.ToolCallBlock(id="wx1", name="weather", arguments=json.dumps({"city": "Paris"})),
                t.ToolCallBlock(id="wx2", name="weather", arguments=json.dumps({"city": "Tokyo"})),
            ]
        ),
        chunk_response(
            text="Paris is 18°C (light rain) and Tokyo is 24°C (sunny) — bring an umbrella for Paris."
        ),
    ]


@dataclass
class _Fault:
    """retry 场景哨兵：stream 遇到它即抛 TRANSIENT LlmError。"""

    message: str = "connection reset by peer"


def _retry() -> list[list[Any]]:
    return [
        [  # 尝试 1：半截文本后故障
            t.BlockStartChunk(index=0, block_type="text"),
            t.TextDeltaChunk(index=0, text="Almost "),
            _Fault(),
        ],
        chunk_response(text="Recovered after one transient provider failure — all good."),
    ]


def _steer() -> list[list[Any]]:
    return [
        chunk_response(tool_calls=[t.ToolCallBlock(id="now1", name="now", arguments="{}")]),
        chunk_response(
            text="It is 2026-08-31T18:00:00Z, and (per your steering) Tokyo's weather is 24°C sunny."
        ),
    ]


def _skills() -> list[list[Any]]:
    return [
        chunk_response(tool_calls=[t.ToolCallBlock(id="sk1", name="skill", arguments=json.dumps({"name": "poetic-note"}))]),
        chunk_response(text="Notes fall like autumn leaves —\nwhat you save, time preserves in green."),
    ]


def _instructions() -> list[list[Any]]:
    # AGENTS.md 指令："回答必须 ≤ 5 个词" → 5 词内回应
    return [chunk_response(text="Understood. Keeping it brief.")]


def _compaction() -> list[list[Any]]:
    return [
        chunk_response(tool_calls=[t.ToolCallBlock(id="blob1", name="big_read", arguments=json.dumps({"file": "big.txt"}))]),
        chunk_response(text="Done reading the big file. It was mostly noise."),
    ]


_SCENARIOS: dict[str, Any] = {
    "text": _text,
    "tools": _tools,
    "retry": _retry,
    "steer": _steer,
    "skills": _skills,
    "instructions": _instructions,
    "compaction": _compaction,
}

SCENARIOS: tuple[str, ...] = tuple(_SCENARIOS)


def scenario_script(scenario: str) -> list[list[Any]]:
    """按名字产出确定性脚本（未知场景抛 ValueError）。"""
    factory = _SCENARIOS.get(scenario)
    if factory is None:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {sorted(SCENARIOS)}")
    return factory()


# ---------------------------------------------------------------------------
# ScriptedAdapter
# ---------------------------------------------------------------------------


class ScriptedAdapter:
    """离线确定性 LLM：按脚本流式回复（LLM 协议实现）。"""

    def __init__(self, script: list[list[Any]], model: str = "mini-scripted") -> None:
        self.model = model
        self._script = list(script)
        self._cursor = 0
        #: steer 钩子：在即将发出 tool-call block 前被调用（由 cli/测试挂载）。
        self.on_tool_call = None

    def prepare_call(self, config: t.LlmCallConfig, signal: t.AbortSignal | None = None) -> PreparedCall:
        return PreparedCall(config=config)

    def stream(self, options: t.GenerateOptions) -> AsyncIterator[Any]:
        if self._cursor >= len(self._script):
            # 脚本耗尽：收尾短句（REPL/多轮时不会重复最后一条）
            chunks = chunk_response(text="(scripted demo: no more turns)")
        else:
            chunks = self._script[self._cursor]
        self._cursor += 1

        async def gen():
            for chunk in chunks:
                if isinstance(chunk, _Fault):
                    raise t.LlmError(chunk.message, "TRANSIENT")
                if (
                    self.on_tool_call is not None
                    and isinstance(chunk, t.BlockStartChunk)
                    and chunk.block_type == "tool-call"
                ):
                    self.on_tool_call()
                yield chunk

        return gen()


# ---------------------------------------------------------------------------
# OpenAICompatAdapter
# ---------------------------------------------------------------------------


class OpenAICompatAdapter:
    """openai SDK → StreamChunk（DeepSeek/Qwen/Kimi/Ollama 等兼容端点）。"""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key or ""
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._client: Any = None

    def prepare_call(self, config: t.LlmCallConfig, signal: t.AbortSignal | None = None) -> PreparedCall:
        return PreparedCall(config=config)

    def close(self) -> None:
        self._client = None

    async def stream(self, options: t.GenerateOptions) -> AsyncIterator[Any]:
        from openai import AsyncOpenAI

        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._api_key or "sk-missing", base_url=self._base_url)
        messages = _to_openai_messages(options)
        params: dict[str, Any] = {
            "model": options.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if options.tools:
            params["tools"] = [{"type": "function", "function": {"name": s.name, "description": s.description, "parameters": s.parameters}} for s in options.tools]
        if self._max_tokens is not None:
            params["max_tokens"] = self._max_tokens
        try:
            stream = await self._client.chat.completions.create(**params)
        except Exception:  # noqa: BLE001 —— 有些端点拒绝 stream_options
            params.pop("stream_options", None)
            stream = await self._client.chat.completions.create(**params)

        # 把 OpenAI 流转换成 dsh StreamChunk（逐块流式）
        index = 0
        open_slot: dict[int, dict[str, str]] = {}
        usage: Any = None
        text_parts: list[str] = []
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield t.BlockStartChunk(index=index, block_type="reasoning")
                yield t.ReasoningDeltaChunk(index=index, text=reasoning)
                yield t.BlockEndChunk(index=index, block=t.ReasoningBlock(text=reasoning))
                index += 1
            if delta.content:
                if not open_slot.get(-1, {}).get("open"):
                    yield t.BlockStartChunk(index=index, block_type="text")
                    open_slot[-1] = {"open": True}
                yield t.TextDeltaChunk(index=index, text=delta.content)
                text_parts.append(delta.content)
            if delta.tool_calls:
                for call in delta.tool_calls:
                    slot = open_slot.setdefault(call.index, {"id": "", "name": "", "arguments": "", "open": False})
                    if not slot["open"]:
                        yield t.BlockStartChunk(index=index, block_type="tool-call")
                        slot["open"] = True
                    if call.id:
                        slot["id"] = call.id
                    if call.function and call.function.name:
                        slot["name"] += call.function.name
                    if call.function and call.function.arguments:
                        yield t.ToolCallDeltaChunk(index=index, id=call.id or f"call_{call.index}", name=slot["name"], arguments_delta=call.function.arguments)
                        slot["arguments"] += call.function.arguments
        # 收尾：关闭块、usage、finish
        seen_ids: set[int] = set()
        for idx, slot in sorted(open_slot.items()):
            if idx < 0 or idx in seen_ids:
                continue
            seen_ids.add(idx)
            if slot["open"]:
                yield t.BlockEndChunk(index=idx, block=t.ToolCallBlock(id=slot["id"], name=slot["name"], arguments=slot["arguments"]))
        if text_parts:
            yield t.BlockEndChunk(index=index, block=t.TextBlock(text="".join(text_parts)))
        if usage is not None:
            yield t.UsageChunk(
                usage=t.TokenUsage(
                    input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                    output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                )
            )
        yield t.FinishChunk(reason=t.StopFinish())


def _to_openai_messages(options: t.GenerateOptions) -> list[dict[str, Any]]:
    """把 core 的 Message 族转成 OpenAI messages（text 消息 + tool 结果）。"""
    out: list[dict[str, Any]] = []
    if options.system:
        out.append({"role": "system", "content": options.system})
    for message in options.messages:
        role = getattr(message, "role", "")
        if role == "user":
            out.append({"role": "user", "content": message.text or ""})
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": message.call_id, "content": message.text})
        elif role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            calls = getattr(message, "tool_calls", None)
            if calls:
                entry["tool_calls"] = [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in calls
                ]
            out.append(entry)
    return out
