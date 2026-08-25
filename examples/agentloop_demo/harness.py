"""仿 DeepSeek Harness 的 agent-loop 示例：完整可运行版。

运行（仓库根目录）:
    uv run python -m examples.agentloop_demo.harness

宿主只做四件事：构建插件内核 → 加载/激活插件 → 把用户输入交给
``agentLoop`` 服务 → 关闭。会话日志、系统提示词、工具、LLM 与循环
逻辑全部在插件里——这就是 dsh 的标准模式（thin host + everything is a
plugin）。

默认使用 scripted 演示模型（无需密钥，确定性输出）；想接真实 DeepSeek，
把 ``PLUGINS_CONFIG`` 里 llm 的配置改成 ``{"provider": "deepseek"}`` 并
确保 ``DEEPSEEK_API_KEY`` 可用（环境变量或 ``~/.javis/.env``）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from examples.agentloop_demo.plugins.agent_loop import AgentLoopService
from examples.agentloop_demo.plugins.session import SessionService
from javis.plugins import (
    PluginContext,
    PluginRegistry,
    load_plugins,
)
from javis.plugins.context import ServiceRegistry

console = Console()


class HarnessConfig(BaseModel):
    """内建 ``config`` 服务的数据结构。

    ``services.get("config", HarnessConfig)`` 时经 pydantic 校验并转换为
    模型实例。
    """

    workspace_root: str
    model: str = "deepseek-chat"


# 本示例的插件目录（真实项目里由 loader 的 plugin_dirs() 扫描全局/项目层）。
PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"

# 示例工作区：bash / read_file 等工具都在这里操作。
WORKSPACE_ROOT = Path(__file__).resolve().parent

# 等价于 config.json 的 "plugins" 段：{插件名: {enabled, config}}。
# 想用真实 DeepSeek 模型时打开下面注释（需 DEEPSEEK_API_KEY）。
PLUGINS_CONFIG: dict[str, Any] = {
    "agent_loop": {"config": {"max_steps": 3}},
    # "llm": {"config": {"provider": "deepseek"}},
}

# 交给 agent 的用户输入（scripted 模型会据此决定工具调用）。
PROMPTS: tuple[str, ...] = (
    "请读取 README.md 并总结一下",
    "运行测试",
)


def _say(message: str) -> None:
    """宿主解说输出：与插件的真实输出区分开。"""
    print(f"[harness] {message}")


async def main() -> int:
    # ============ 1/5 构建内核（build_javis_runtime） ============
    _say("1/5 构建插件内核：ServiceRegistry + PluginRegistry")
    services = ServiceRegistry()

    # 内建服务（owner=None，永不随插件卸载而撤销）。
    builtin_config = {
        "workspace_root": str(WORKSPACE_ROOT),
        "model": "deepseek-chat",
    }
    services.provide("config", builtin_config)

    def make_ctx(name: str, config: Any) -> PluginContext:
        return PluginContext(
            name=name,
            config=config,
            services=services,
            javis_config=services.get("config", HarnessConfig),
        )

    registry = PluginRegistry(services=services, ctx_builder=make_ctx)

    # ============ 2/5 加载并激活插件（load + activate） ============
    _say(f"2/5 扫描插件目录 {PLUGINS_DIR} 并并行激活")
    await load_plugins(registry, [PLUGINS_DIR], PLUGINS_CONFIG)
    report = await registry.activate_all()
    _say(f"    loaded={report.loaded} failed={report.failed} skipped={report.skipped}")
    for item in registry.list_plugins():
        state = item["state"].value
        error = f"  ({item['error']})" if item["error"] else ""
        _say(f"    {item['name']:<15} {state}{error}")
    agent_loop = services.get("agentLoop", AgentLoopService)
    if agent_loop is None:
        _say("    agentLoop 服务不可用（插件激活失败），示例终止")
        await registry.close_all()
        return 1

    # ============ 3/5 启动钩子（start_runtime） ============
    _say("3/5 start_runtime：执行各插件的 on_start 钩子")
    await registry.run_start_hooks()

    # ============ 4/5 agent loop：宿主只做"把输入交给 agent" ============
    _say("4/5 agent loop：创建会话，逐条提交用户输入")
    handle = agent_loop.create(
        {"sessionId": "demo-session", "cwd": str(WORKSPACE_ROOT)}
    )
    for prompt in PROMPTS:
        _say(f"    用户: {prompt}")
        final_text = await handle.turn(prompt)
        console.print(Panel(Markdown(final_text), title="最终回答", border_style="green"))

    # 会话日志是事件溯源的：展示真实落库的事件。
    demo = services.get("session", SessionService).get("demo-session")
    _say(f"    会话日志（事件溯源，共 {len(demo.events)} 条）:")
    for event in demo.events:
        brief = json.dumps(event.data, ensure_ascii=False)[:100]
        _say(f"      #{event.seq:<2} {event.type:<18} {brief}")

    # ============ 5/5 关闭（close_runtime） ============
    _say("5/5 close_runtime：逆序执行 disposer，插件进入 DISPOSED")
    await registry.close_all()
    _say("示例结束：插件系统 + dsh 风格 agent loop 已完整跑通。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
