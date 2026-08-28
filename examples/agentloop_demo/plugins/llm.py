"""Scripted LLM provider plugin, built on the javis LLMProvider contract.

Uses the real ``javis.contracts`` contract (not a demo-local copy):
``LLMProvider`` requires only ``achat_stream``; the demo provider decides
between a plain text answer and a tool call by regex on the last user
message. Deltas are yielded as ``LLMResponse`` chunks, matching the
streaming contract (``merge`` in the agent loop aggregates them).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from pydantic import BaseModel, Field

from javis.contracts import LLM_SERVICE, LLMProvider, LLMRequest, LLMResponse, ToolCall

name = "llm"
inject: list[str] = []
provides = [LLM_SERVICE]


class Config(BaseModel):
    provider: str = Field(default="scripted")
    model: str = Field(default="scripted-demo")


class DemoScriptedProvider(LLMProvider):
    """Deterministic provider: text answer or tool call, no network."""

    def __init__(self, model: str) -> None:
        super().__init__(model=model)

    async def achat_stream(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[LLMResponse]:
        del extra_body, on_token, on_reasoning
        messages = request.messages
        if messages and messages[-1].get("role") == "tool":
            content = str(messages[-1].get("content", ""))
            preview = content if len(content) <= 4000 else content[:4000] + "\n... (truncated)"
            yield LLMResponse(content="工具执行完成，结果如下：")
            yield LLMResponse(content=f"\n\n{preview}")
            return

        prompt = self._last_user_text(messages)
        tool_call = self._decide(prompt)
        if tool_call is None:
            yield LLMResponse(
                content=(
                    "这个请求不需要工具。可用工具有 read_file / list_files / bash；"
                    "你可以让我读取文件、列出目录或运行命令。"
                )
            )
            return
        yield LLMResponse(content="我先调用工具获取信息。")
        yield LLMResponse(tool_calls=[ToolCall(**tool_call)])

    @staticmethod
    def _last_user_text(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                return message["content"]
        return ""

    @staticmethod
    def _decide(prompt: str) -> dict[str, Any] | None:
        path = re.search(r"[\w./-]+\.\w+", prompt)
        if re.search(r"读|read|总结", prompt, re.IGNORECASE):
            return {
                "id": "call_read",
                "name": "read_file",
                "arguments": {"file_path": path.group(0) if path else "README.md"},
            }
        if re.search(r"运行|测试|pytest|test", prompt, re.IGNORECASE):
            return {
                "id": "call_bash",
                "name": "bash",
                "arguments": {"command": "python -m pytest -q test_agentloop.py"},
            }
        if re.search(r"列出|list", prompt, re.IGNORECASE):
            return {"id": "call_list", "name": "list_files", "arguments": {}}
        return None


def apply(ctx: Any, config: Config) -> None:
    if config.provider != "scripted":
        raise ValueError(f"mock demo supports only provider='scripted', got {config.provider!r}")
    provider = DemoScriptedProvider(config.model)
    ctx.provide(LLM_SERVICE, provider)

    def on_start() -> None:
        print(f"  [llm] provider={provider.model}")

    ctx.on_start(on_start)
