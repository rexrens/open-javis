"""Agent factory plugin, built on the javis LLM contract.

Injects the real contracts — ``LLMProvider`` (javis.contracts) and
``ToolRegistry`` (javis.engines.corecoder.tools) — plus the demo-local
session / system-prompt services, runs the agent loop, and provides the
``agents`` service. The loop drives ``achat_stream`` with an ``LLMRequest``,
aggregates ``LLMResponse`` deltas, and executes ``ToolCall`` results through
the tool registry.
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from javis.contracts import LLMProvider, LLMRequest, ToolCall
from javis.engines.corecoder.tools import ToolRegistry

from .session import SessionStore
from .system_prompt import SystemPromptService


class AgentsService(ABC):
    """Agent factory service, shaped after dsh ``ctx.agents``."""

    @abstractmethod
    async def create(self, options: dict[str, Any]) -> Any:
        raise NotImplementedError


name = "agents"
inject = [LLMProvider, ToolRegistry, SessionStore, SystemPromptService]
provides = [AgentsService]


class Config(BaseModel):
    max_steps: int = Field(default=3, ge=1, le=50)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)


class AgentHandle:
    """One session-scoped agent handle with dsh-like followup/when_idle."""

    def __init__(self, *, ctx: Any, session_id: str, cwd: str | None, config: Config) -> None:
        self.ctx = ctx
        self.session_id = session_id
        self.cwd = cwd
        self.config = config
        self.turn_no = 0
        self.final_text = ""
        self._turn_task: asyncio.Task[str] | None = None

    async def followup(self, prompt: str) -> None:
        if self._turn_task is not None:
            raise RuntimeError("agent is busy; call when_idle() before followup()")
        self._turn_task = asyncio.create_task(self._run_turn(prompt))

    async def when_idle(self) -> None:
        task = self._turn_task
        if task is None:
            return
        self._turn_task = None
        await task

    async def _run_turn(self, prompt: str) -> str:
        session: SessionStore = self.ctx.services[SessionStore]
        system_prompt: SystemPromptService = self.ctx.services[SystemPromptService]
        tools: ToolRegistry = self.ctx.services[ToolRegistry]
        llm: LLMProvider = self.ctx.services[LLMProvider]

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
            if step > self.config.max_steps:
                reason = "max-steps"
                break
            session.append(self.session_id, "step/start", {"turn": turn, "step": step})

            assembled = system_prompt.assemble(
                {"cwd": self.cwd, "date": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
            )
            request = LLMRequest(
                messages=session.derive_messages(self.session_id, system_prompt=assembled),
                tools=[tool.schema() for tool in tools.all()],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
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
                tool = tools.get(call.name)
                if tool is None:
                    result = f"Error: unknown tool {call.name!r}"
                else:
                    result = tool.execute(**call.arguments)
                session.append(
                    self.session_id,
                    "tool/result",
                    {
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": result,
                    },
                )

        session.append(self.session_id, "turn/end", {"turn": turn, "reason": reason})
        self.final_text = final_text
        self.ctx.emit(
            "agents/turn-end",
            {"session_id": self.session_id, "turn": turn, "reason": reason},
        )
        return final_text

    async def _collect(
        self,
        turn: int,
        step: int,
        session: SessionStore,
        llm: LLMProvider,
        request: LLMRequest,
    ) -> tuple[dict[str, Any], list[ToolCall], dict[str, int]]:
        text_parts: list[str] = []
        calls: dict[str, ToolCall] = {}
        prompt_tokens = 0
        completion_tokens = 0

        async for delta in llm.achat_stream(request):
            session.append(
                self.session_id,
                "assistant/chunk",
                {
                    "turn": turn,
                    "step": step,
                    "chunk": {
                        "content": delta.content,
                        "reasoning_content": delta.reasoning_content,
                        "tool_calls": [
                            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                            for tc in delta.tool_calls
                        ],
                    },
                },
            )
            if delta.content:
                text_parts.append(delta.content)
            for tc in delta.tool_calls:
                calls[tc.id] = tc
            prompt_tokens = delta.prompt_tokens or prompt_tokens
            completion_tokens = delta.completion_tokens or completion_tokens

        content = "".join(text_parts)
        tool_calls = list(calls.values())
        message: dict[str, Any] = {"role": "assistant", "content": content or None}
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in tool_calls
            ]
        return message, tool_calls, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }


class DemoAgentsService(AgentsService):
    def __init__(self, ctx: Any, config: Config) -> None:
        self.ctx = ctx
        self.config = config
        self._handles: dict[str, AgentHandle] = {}

    async def create(self, options: dict[str, Any]) -> AgentHandle:
        session_id = str(options["sessionId"])
        cwd = options.get("cwd")
        self.ctx.services[SessionStore].create(session_id, cwd=cwd)
        handle = AgentHandle(
            ctx=self.ctx,
            session_id=session_id,
            cwd=cwd,
            config=self.config,
        )
        self._handles[session_id] = handle
        return handle

    def get(self, session_id: str) -> AgentHandle:
        return self._handles[session_id]

    def dispose(self) -> None:
        self._handles.clear()


def apply(ctx: Any, config: Config) -> Any:
    service = DemoAgentsService(ctx, config)
    ctx.provide(AgentsService, service)

    def on_start() -> None:
        print(f"  [agents] ready (max_steps={config.max_steps})")

    ctx.on_start(on_start)

    def disposer() -> None:
        service.dispose()

    return disposer
