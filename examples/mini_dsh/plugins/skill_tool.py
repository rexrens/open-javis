"""插件：provide "skills" + skill 加载工具 + 目录发布 + /<name> 注入（dsh tool-skill）。"""
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.skill import FileSkillProvider, SkillRegistry
from core.tools import Tool
from core.types import Events, PreStepEnter, TextBlock, UserMessage

name = "skill_tool"

#: 依赖：apply 里 ``ctx.get("tools")`` 需要 tools 服务先 ACTIVE。
inject = ["tools"]


class Config(BaseModel):
    skillsRoot: str = "./skills"


def _render_skill(skill: Any) -> UserMessage:
    body = f"# Skill: {skill.name}\n\n{skill.description}\n\n{skill.content}"
    return UserMessage(content=(TextBlock(text=body),), source={"kind": "skill-invocation", "name": skill.name})


def apply(ctx, config: Config) -> None:
    root = Path(config.skillsRoot)
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    registry = SkillRegistry(ctx)
    registry.register_provider(FileSkillProvider(root))
    ctx.provide("skills", registry)

    tools = ctx.get("tools")

    def load(exec_input: Any) -> Any:
        from core.tools import ToolExecutionResult  # 局部 import 避免循环

        skill_name = str((exec_input.arguments or {}).get("name", ""))
        skill = registry.get(skill_name)
        if skill is None:
            return ToolExecutionResult.text(
                f'skill "{skill_name}" is unknown or no longer available', is_error=True
            )
        return ToolExecutionResult.text(
            f"# Skill: {skill.name}\n\n{skill.description}\n\n{skill.content}"
        )

    tools.register(
        Tool(
            name="skill",
            description=(
                "Load the full instructions for an available skill. Call this with the exact skill "
                "name from the session skill catalog before acting on a task that names or clearly "
                "matches that skill."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "The exact skill name."}},
                "required": ["name"],
            },
            body=load,
            mode="parallel",
        )
    )

    # -- /<name> 显式调用（只扫用户文本消息；注册在目录监听器之前，注入次序靠前） --
    def on_invocation(payload, next):
        decision = next()
        if getattr(decision, "kind", None) == "reject":
            return decision
        messages = list(decision.messages)
        injected: list[UserMessage] = []
        for message in messages:
            source = getattr(message, "source", None) or {}
            # 只扫用户文本消息：source 为 None（from_text）或显式 user 来源；
            # baseline 指令 / skill 目录 / compaction 摘要消息都有非 user source 标记
            if source.get("kind") not in (None, "user"):
                continue
            text = (message.text or "").strip()
            first_line = text.splitlines()[0] if text else ""
            if not first_line.startswith("/"):
                continue
            skill_name = first_line[1:].strip()
            skill = registry.get(skill_name)
            if skill is None:
                continue
            injected.append(_render_skill(skill))
        if not injected:
            return decision
        return PreStepEnter(messages=tuple(list(decision.messages) + injected))

    # -- <available_skills> 目录发布（skill 工具可见即视为已注册；每会话只注入一次） --
    def on_catalog(payload, next):
        decision = next()
        if getattr(decision, "kind", None) == "reject":
            return decision
        agent = payload["agent"]
        session = agent.session
        # 会话日志已有 skill-catalog 来源消息就不再注入（只注入一次）
        already_published = any(
            (getattr((e.data or {}).get("message"), "source", None) or {}).get("kind")
            == "skill-catalog"
            for e in session.events_of("user/message")
        )
        if already_published:
            return decision
        summaries = registry.list()
        if not summaries:
            return decision
        lines = "\n".join(f"- {s.name}: {s.description}" for s in summaries)
        catalog = UserMessage(
            content=(TextBlock(text=f"<available_skills>\n{lines}\n</available_skills>"),),
            source={"kind": "skill-catalog"},
        )
        return PreStepEnter(messages=tuple(list(decision.messages) + [catalog]))

    ctx.on(Events.AGENT_PRE_STEP, on_invocation)
    ctx.on(Events.AGENT_PRE_STEP, on_catalog)
