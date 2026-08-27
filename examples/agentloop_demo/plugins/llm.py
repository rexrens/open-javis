"""Scripted LLM adapter plugin for the dsh-style demo."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from examples.agentloop_demo.contracts import LLM, LLM_SERVICE

name = "llm"
inject: list[str] = []
provides = [LLM_SERVICE]


class Config(BaseModel):
    provider: str = Field(default="scripted")
    model: str = Field(default="scripted-demo")


class ScriptedProvider(LLM):
    """Deterministic provider that returns text or tool calls without a network."""

    def __init__(self, model: str) -> None:
        self.model = model

    async def stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        messages = request.get("messages", [])
        if messages and messages[-1].get("role") == "tool":
            content = str(messages[-1].get("content", ""))
            preview = content if len(content) <= 4000 else content[:4000] + "\n... (truncated)"
            yield {"type": "text", "text": "工具执行完成，结果如下："}
            yield {"type": "text", "text": f"\n\n{preview}"}
            return

        prompt = self._last_user_text(messages)
        tool_call = self._decide(prompt)
        if tool_call is None:
            yield {
                "type": "text",
                "text": (
                    "这个请求不需要工具。可用工具有 read_file / list_files / bash；"
                    "你可以让我读取文件、列出目录或运行命令。"
                ),
            }
            return
        yield {"type": "text", "text": "我先调用工具获取信息。"}
        yield {"type": "tool-call", **tool_call}

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


class LlmService(LLM):
    def __init__(self, provider: ScriptedProvider) -> None:
        self.provider = provider

    async def stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self.provider.stream(request):
            yield chunk


def apply(ctx: Any, config: Config) -> Any:
    if config.provider != "scripted":
        raise ValueError(f"mock demo supports only provider='scripted', got {config.provider!r}")
    provider = ScriptedProvider(config.model)
    ctx.provide(LLM_SERVICE, LlmService(provider))

    def on_start() -> None:
        print(f"  [llm] provider={provider.model}")

    ctx.on_start(on_start)
