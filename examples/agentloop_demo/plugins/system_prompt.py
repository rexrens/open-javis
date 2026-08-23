"""系统提示词插件：有序 section 注册表（仿 ``@deepseek-ai/dsh-system-prompt``）。

dsh 的 system-prompt 不是一段静态文本，而是一个 section 注册表：任何插件
都可以按 ``order`` 贡献一段提示词（identity / persona / 工具指南 …）。
``assemble()`` 按 order 升序拼接所有 section，并支持 ``{{变量}}`` 插值。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

SectionText = str | Callable[[dict[str, Any]], str]


class Config(BaseModel):
    """插件配置模型（对应 dsh system-prompt 的配置）。"""

    persona: str = Field(
        default=(
            "You are javis, a CLI coding agent. "
            "Answer concisely and use tools when they help."
        ),
        description="基础 persona 文本（对应 dsh 部署人格）",
    )


@dataclass(frozen=True)
class PromptSection:
    """一个提示词 section（对应 dsh 的 ``PromptSection``）。"""

    name: str
    order: int
    text: SectionText


class SystemPromptService:
    """插件通过 ``ctx.provide("system_prompt", ...)`` 注册的服务。"""

    def __init__(self) -> None:
        self._sections: dict[str, PromptSection] = {}

    def section(self, name: str, order: int, text: SectionText) -> None:
        if name in self._sections:
            raise ValueError(f"duplicate system-prompt section {name!r}")
        self._sections[name] = PromptSection(name=name, order=order, text=text)

    def assemble(self, context: dict[str, Any] | None = None) -> str:
        """按 order 升序渲染全部 section（对应 dsh ``systemPrompt.assemble``）。"""
        rendered: list[str] = []
        for section in sorted(self._sections.values(), key=lambda s: (s.order, s.name)):
            text = section.text(context or {}) if callable(section.text) else section.text
            if text:
                rendered.append(text)
        return "\n\n".join(rendered)

    def render(self, template: str, variables: dict[str, Any]) -> str:
        """``{{name}}`` 变量插值（dsh ``renderPrompt`` 的简化版）。"""
        result = template
        for key, value in variables.items():
            result = result.replace("{{" + key + "}}", str(value))
        return result


def apply(ctx: Any, config: Any) -> Any:
    """激活入口：注册系统提示词服务并登记默认 sections。"""
    service = SystemPromptService()
    # order 约定与 dsh 一致：-100 身份、0 环境、100+ 工具指南。
    service.section("identity", -100, config.persona)
    service.section(
        "environment",
        0,
        lambda c: (
            "Environment:\n"
            f"- cwd: {c.get('cwd', '<unknown>')}\n"
            f"- date: {c.get('date', '<unknown>')}"
        ),
    )
    service.section(
        "tools-guide",
        100,
        (
            "Use the available tools when they help. "
            "Read files before summarizing them; run tests before reporting results."
        ),
    )
    ctx.provide("system_prompt", service)
