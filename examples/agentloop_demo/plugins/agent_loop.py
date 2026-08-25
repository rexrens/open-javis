"""Agent 循环插件（仿 ``@deepseek-ai/dsh-agent-loop``）。

dsh 中 agent-loop 本身是一个插件：``static inject = ['agents', 'sessions',
'llm', 'tools', 'systemPrompt']`` 声明依赖，启动时创建 ``ReactLoopAgent``。
驱动循环的逻辑（turn/step 边界、组装请求、流式收集、工具执行、事件落库）
全部在插件内部，宿主只负责把用户输入交给 agent。

本插件实现 ``ReactLoopAgent`` 的简化版：一次 ``turn()`` 打开会话边界，
循环 step——组装请求 → LLM 流式输出 → 有工具调用则执行并写回会话 → 再
请求；直到模型不再调用工具或达到 ``max_steps``，关闭边界并广播
``agent/turn-end`` 事件。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from examples.agentloop_demo.plugins.system_prompt import SystemPromptService
from javis.plugins import PluginContext


class Config(BaseModel):
    """插件配置模型（对应 dsh AgentLoop 的 Config）。"""

    max_steps: int = Field(
        default=3,
        ge=1,
        le=50,
        description="单个 turn 内允许的最大 step 数（防止死循环）",
    )
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)


# 声明依赖：这四个服务分别由 llm / session / tools / system_prompt 插件
# 在各自 apply 中 ctx.provide()。agent_loop 会等待它们全部就绪才激活。
inject = ["llm", "session", "tools", "system_prompt"]


class AgentHandle:
    """一次会话对应的 agent（简化版 ReactLoopAgent）。"""

    def __init__(
        self,
        *,
        session_id: str,
        get: Callable[[str], Any],
        emit: Callable[[str, Any], None],
        config: Config,
        cwd: str | None,
    ) -> None:
        self.session_id = session_id
        self._get = get
        self._emit = emit
        self._config = config
        self.cwd = cwd
        self.turn_no = 0

    async def turn(self, prompt: str) -> str:
        """运行一轮 turn：直到模型不再调用工具或达到 max_steps。"""
        session = self._get("session")
        system_prompt:SystemPromptService = self._get("system_prompt")
        tools = self._get("tools")
        llm = self._get("llm")

        self.turn_no += 1
        turn = self.turn_no
        session.append(self.session_id, "turn/start", {"turn": turn})
        session.append(
            self.session_id,
            "user/message",
            {"message": {"role": "user", "content": prompt}},
        )

        final_text = ""
        reason = "completed"
        step = 0
        while True:
            step += 1
            if step > self._config.max_steps:
                reason = "max-steps"
                break
            session.append(self.session_id, "step/start", {"turn": turn, "step": step})

            system = system_prompt.assemble(
                {"cwd": self.cwd, "date": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
            )
            request = {
                "messages": session.derive_messages(self.session_id, system_prompt=system),
                "tools": tools.snapshot(),
                "temperature": self._config.temperature,
                "max_tokens": self._config.max_tokens,
            }
            assistant_message, tool_calls, _usage = await self._collect(
                turn, step, session, llm, request
            )
            session.append(
                self.session_id,
                "assistant/message",
                {"turn": turn, "step": step, "message": assistant_message},
            )
            session.append(self.session_id, "step/end", {"turn": turn, "step": step})

            if not tool_calls:
                final_text = assistant_message.get("content") or ""
                break
            for call in tool_calls:
                result = await tools.execute(call["name"], call["arguments"])
                session.append(
                    self.session_id,
                    "tool/result",
                    {
                        "tool_call_id": call["id"],
                        "name": call["name"],
                        "content": result,
                    },
                )

        session.append(self.session_id, "turn/end", {"turn": turn, "reason": reason})
        self._emit(
            "agent/turn-end",
            {"session_id": self.session_id, "turn": turn, "reason": reason},
        )
        return final_text

    async def _collect(
        self,
        turn: int,
        step: int,
        session: Any,
        llm: Any,
        request: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        """流式收集 LLM 输出：每块先落日志，末尾折叠成 assistant 消息。"""
        text_parts: list[str] = []
        calls: dict[str, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        async for chunk in llm.stream(request):
            session.append(
                self.session_id,
                "assistant/chunk",
                {"turn": turn, "step": step, "chunk": chunk},
            )
            chunk_type = chunk.get("type")
            if chunk_type == "text":
                text_parts.append(str(chunk.get("text", "")))
            elif chunk_type == "tool-call":
                call_id = str(chunk.get("id") or f"call_{len(calls)}")
                calls[call_id] = {
                    "id": call_id,
                    "name": str(chunk.get("name", "")),
                    "arguments": dict(chunk.get("arguments", {})),
                }
            elif chunk_type == "usage":
                usage = dict(chunk)

        content = "".join(text_parts)
        tool_calls = list(calls.values())
        message: dict[str, Any] = {"role": "assistant", "content": content or None}
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                    },
                }
                for call in tool_calls
            ]
        return message, tool_calls, usage


class AgentLoopService:
    """插件通过 ``ctx.provide("agentLoop", ...)`` 注册的服务。"""

    def __init__(
        self,
        *,
        get: Callable[[str], Any],
        emit: Callable[[str, Any], None],
        config: Config,
    ) -> None:
        self._get = get
        self._emit = emit
        self._config = config
        self._handles: dict[str, AgentHandle] = {}

    def create(self, options: dict[str, Any]) -> AgentHandle:
        """创建会话与 agent（对应 dsh ``ctx.agentLoop.create``）。"""
        session_id = str(options["sessionId"])
        cwd = options.get("cwd")
        self._get("session").create(session_id, cwd=cwd)
        handle = AgentHandle(
            session_id=session_id,
            get=self._get,
            emit=self._emit,
            config=self._config,
            cwd=cwd,
        )
        self._handles[session_id] = handle
        return handle

    def get(self, session_id: str) -> AgentHandle:
        return self._handles[session_id]

    def dispose(self) -> None:
        self._handles.clear()


def apply(ctx: PluginContext, config: Config) -> Any:
    """激活入口：此时所有 inject 依赖都已就绪，config 已通过校验。"""
    service = AgentLoopService(get=ctx.get, emit=ctx.emit, config=config)
    ctx.provide("agentLoop", service)

    def on_start() -> None:
        """应用启动钩子（dsh start() 阶段）。"""
        print(f"  [agent_loop] ready (max_steps={config.max_steps})")

    ctx.on_start(on_start)

    def disposer() -> None:
        """卸载清理：终止所有由本插件创建的 agent。"""
        service.dispose()

    return disposer
