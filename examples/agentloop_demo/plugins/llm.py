"""LLM 适配器插件（仿 dsh 的 ``llm`` + ``llm-deepseek`` 两层结构）。

dsh 把 LLM 拆成「接口 + 具体适配器」：``llm`` 定义 ``stream()`` 契约，
``llm-deepseek`` 等包实现具体 provider。本插件提供：

- ``LlmService.stream(request)`` —— async generator，逐块产出 chunk：

  - ``{"type": "text", "text": ...}``            文本增量
  - ``{"type": "tool-call", ...}``               一次完整工具调用
  - ``{"type": "usage", ...}``                   本轮 token 用量

- provider 可配置（``config.provider``）：

  - ``scripted``（默认）：确定性演示模型，无需密钥即可跑通完整循环；
  - ``deepseek``：复用 javis 的 ``OpenAICompatProvider``，需要
    ``DEEPSEEK_API_KEY``（环境变量或 ``~/.javis/.env``）。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any, Protocol

from pydantic import BaseModel, Field


class Config(BaseModel):
    """插件配置模型。"""

    provider: str = Field(default="scripted", description="scripted | deepseek")
    model: str | None = Field(default=None, description="覆盖默认模型名")
    api_key: str | None = Field(default=None, description="覆盖 DEEPSEEK_API_KEY")
    base_url: str | None = Field(
        default=None,
        description="OpenAI 兼容端点，默认 https://api.deepseek.com",
    )
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)


class LlmProvider(Protocol):
    """provider 契约：``stream()`` 返回 chunk 的异步迭代器。"""

    name: str
    model: str

    def stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...


class ScriptedProvider:
    """确定性演示模型：按用户输入的关键词决定是否调用工具。

    - 历史末尾有 tool 结果 → 总结该结果（这是循环里的最终回答步）；
    - 输入含 读/read → 调 ``read_file``（从输入里提取文件路径）；
    - 输入含 运行/测试/pytest → 调 ``bash`` 跑示例自带测试
      （``python -m pytest -q test_agentloop.py``）；
    - 输入含 列出/list → 调 ``list_files``；
    - 否则直接给一段文本回答。
    """

    name = "scripted"

    def __init__(self, model: str = "scripted-demo") -> None:
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


class DeepSeekProvider:
    """真实 DeepSeek 适配器：把 javis 的 LLMProvider 流映射成 chunk 协议。"""

    name = "deepseek"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None,
        *,
        temperature: float,
        max_tokens: int,
    ) -> None:
        from javis.engines.corecoder.llm import LLMResponse, OpenAICompatProvider

        self.model = model
        self._response_cls = LLMResponse
        self._provider = OpenAICompatProvider(
            model,
            api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        from javis.engines.corecoder.llm import LLMRequest

        llm_request = LLMRequest(
            messages=request.get("messages", []),
            tools=request.get("tools"),
            temperature=request.get("temperature"),
            max_tokens=request.get("max_tokens"),
        )
        merged = self._response_cls()
        async for delta in self._provider.achat_stream(llm_request):
            merged = merged.merge(delta)
            if delta.content:
                yield {"type": "text", "text": delta.content}
        # 工具调用流式跨多个 chunk，等流结束后统一补发完整块。
        for call in merged.tool_calls:
            yield {
                "type": "tool-call",
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
            }
        if merged.prompt_tokens or merged.completion_tokens:
            yield {
                "type": "usage",
                "input_tokens": merged.prompt_tokens,
                "output_tokens": merged.completion_tokens,
            }


class LlmService:
    """插件通过 ``ctx.provide("llm", ...)`` 注册的服务。"""

    def __init__(self, provider: LlmProvider) -> None:
        self.provider = provider

    async def stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self.provider.stream(request):
            yield chunk


def _build_provider(config: Config) -> LlmProvider:
    if config.provider == "scripted":
        return ScriptedProvider(model=config.model or "scripted-demo")
    if config.provider != "deepseek":
        raise ValueError(f"unknown llm provider {config.provider!r} (scripted | deepseek)")

    from javis.session.credentials import resolve_api_key

    api_key = config.api_key or resolve_api_key("deepseek")
    if not api_key:
        raise ValueError(
            "deepseek provider needs DEEPSEEK_API_KEY "
            "(env var / ~/.javis/.env / llm.config.api_key)"
        )
    return DeepSeekProvider(
        model=config.model or "deepseek-chat",
        api_key=api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )


def apply(ctx: Any, config: Any) -> Any:
    """激活入口：按配置构造 provider 并注册 LLM 服务。"""
    provider = _build_provider(config)
    print(f"  [llm] provider={provider.name} model={provider.model}")
    ctx.provide("llm", LlmService(provider))
