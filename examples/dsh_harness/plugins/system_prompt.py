"""插件：系统提示词服务（dsh ``ctx.systemPrompt``）。

``assemble()`` 返回 :class:`~javis.harness.types.PromptAssembly` ——
有序的 section 列表 **外加工具 schema**（dsh 语义：工具是提示词装配的
一部分，模型看到的"工具清单"来自这里）。两个渲染方法分工不同：

- ``render_prompt``：把 ``persona`` 类 section 渲染进 system 槽
  （每次请求的固定系统提示）；
- ``render_context``：把 ``context`` 类 section 渲染成 step 边界的
  上下文消息——由 ``agent/pre-step`` 默认逻辑注入（每步都带）。
"""

from javis.harness.types import PromptAssembly, PromptSection

# 插件名：必须与 cordis.yml 组合文件里的条目名一致。
name = "system-prompt"

# 三个提示词 section：
# - 前两个 kind="persona"（默认）→ 进 system 槽；
# - 第三个 kind="context" → 每步边界渲染成上下文消息。
# 正文是英文（喂给模型的），注释是给人看的。
SECTIONS: tuple[PromptSection, ...] = (
    PromptSection(
        title="Persona",
        body=(
            "You are Javis-Demo, a compact assistant harness demo. Answer "
            "concisely. When a task needs facts (time, weather), call the "
            "available tools instead of guessing."
        ),
    ),
    PromptSection(
        title="Tool usage",
        body=(
            "Tools run in model order; exclusive tools form barriers. Tool "
            "results are returned as text blocks."
        ),
    ),
    PromptSection(
        title="Session context",
        body="workspace=open-javis ; date=2026-08-31 ; provider=mock",
        kind="context",
    ),
)


class SystemPromptService:
    """systemPrompt 服务实现：装配 section + 工具 schema，按需渲染。"""

    def __init__(self, ctx, sections: tuple[PromptSection, ...] = SECTIONS) -> None:
        self._ctx = ctx
        self.sections = sections

    def assemble(self, *, agent=None, signal=None) -> PromptAssembly:
        """一次请求的装配结果：section 列表 + 当前工具注册表的活 schema。

        工具 schema 在每次 assemble 时从 ``tools`` 服务现取——工具注册/
        卸载（fiber effect）后，下一请求的模型立刻看到新工具清单。
        """
        registry = self._ctx.get("tools")
        return PromptAssembly(sections=self.sections, tools=tuple(registry.schemas()))

    def render_prompt(self, assembly: PromptAssembly) -> str:
        """渲染 system 槽文本：只取 persona 类 section。"""
        parts = [f"# {section.title}\n{section.body}" for section in assembly.sections if section.kind == "persona"]
        return "\n\n".join(parts)

    def render_context(self, assembly: PromptAssembly) -> str:
        """渲染 step 边界上下文消息：只取 context 类 section（分号连接）。"""
        parts = [f"[{section.title}] {section.body}" for section in assembly.sections if section.kind == "context"]
        return " ; ".join(parts)


def apply(ctx):
    # 发布 systemPrompt 服务。注意 ctx 要传进去：assemble 时要靠它
    # 现取 tools 注册表。
    ctx.provide("systemPrompt", SystemPromptService(ctx))
