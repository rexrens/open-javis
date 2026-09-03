"""插件：组合根——从 services 装配 ReactLoopAgent。

dsh 原样：宿主不直接构造 Session——session 走 ``sessions`` 服务的
``create()``（fiber effect 生命周期）；driver 只做组合：取 services，
构造 agent，发布 ``agent`` / ``agentLoop`` / ``systemPrompt`` / ``session``。
卸载顺序：driver（agent）先于 sessions store 卸载 → agent 最终事件先落日志、
再 detach session（dsh 有序 teardown 的 mini 表达，靠 fiber 逆序卸载达成）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import types as t
from core.agent import ReactLoopAgent
from core.llm import SystemPrompt
from core.session import SessionStore  # 仅为类型/契约引用
from core.tools import ToolRegistry  # 仅为类型/契约引用


def apply(ctx) -> None:
    """组合根：取 sessions/llm/tools 服务，装配 agent 并发布相关服务。

    发布 ``agentLoop`` / ``session`` / ``systemPrompt`` / ``agent``；session
    经 ``store.create`` 走 fiber effect 生命周期（见 core/session.SessionStore）。
    """
    store: SessionStore = ctx.get("sessions")
    _llm = ctx.get("llm")
    _tools: ToolRegistry = ctx.get("tools")

    # agentLoop：tools 场景的并行池上限（core/tools 运行时 ctx.get("agentLoop")）
    ctx.provide("agentLoop", t.AgentLoop(config=t.AgentLoopConfig(max_parallel_tool_calls=2)))

    session = store.create(cwd=ctx.baseUrl if hasattr(ctx, "baseUrl") else None)
    ctx.provide("session", session)
    ctx.provide(
        "systemPrompt",
        SystemPrompt(ctx, "You are mini_dsh, a small cordis-assembled agent.", cwd=session.header.cwd or "", session_id=session.id),
    )
    agent = ReactLoopAgent(
        ctx,
        session.id,
        t.AgentOptions(provider="scripted", model="mini-scripted"),
        session,
    )
    ctx.provide("agent", agent)
