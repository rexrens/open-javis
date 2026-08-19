"""CoreCoder engine adapter — drives corecoder.Agent.achat as an AgentBackend.

Producer-consumer pattern over asyncio.Queue: the achat task is the producer
(a native asyncio task, no thread bridge), run_turn is the consumer yielding
AgentEvents. Cancellation: cancelling run_turn's awaiting task propagates into
achat (its own CancelledError handling keeps history valid), then run_turn's
finally cancels the producer task.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from javis.contracts.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock
from javis.contracts.protocol import AgentBackend
from javis.contracts.types import (
    AgentContext,
    AgentError,
    AgentEvent,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.contracts.usage import UsageSnapshot

_IMAGE_PLACEHOLDER = "[image omitted: engine does not process images]"


def _user_text(content: list[Any]) -> str:
    """Join a user message's text blocks, substituting images with a placeholder."""
    parts = []
    for block in content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ImageBlock):
            parts.append(_IMAGE_PLACEHOLDER)
    return "".join(parts).strip()


def _to_corecoder_messages(messages: list[ConversationMessage]) -> list[dict]:
    """Convert javis conversation history into OpenAI-style message dicts.

    Tool results live in ``user`` messages in javis; corecoder expects them
    as standalone ``tool`` messages with ``tool_call_id``.
    """
    out: list[dict] = []
    for msg in messages:
        if msg.role == "user":
            tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
            text = _user_text(msg.content)
            if text:
                out.append({"role": "user", "content": text})
            for tr in tool_results:
                out.append({"role": "tool", "tool_call_id": tr.tool_use_id, "content": tr.content})
        elif msg.role == "assistant":
            assistant: dict[str, Any] = {"role": "assistant", "content": msg.text or None}
            if msg.tool_uses:
                assistant["tool_calls"] = [
                    {
                        "id": tu.id,
                        "type": "function",
                        "function": {
                            "name": tu.name,
                            "arguments": json.dumps(tu.input),
                        },
                    }
                    for tu in msg.tool_uses
                ]
            out.append(assistant)
    return out


class CoreCoderBackend(AgentBackend):
    """AgentBackend adapter over a corecoder.Agent (async path)."""

    def __init__(
        self,
        agent: Any,
        *,
        model: str,
        system_prompt: str,
        max_turns: int | None = None,
    ) -> None:
        self._agent = agent
        self._model = model
        if system_prompt:
            agent.set_system_prompt(system_prompt)
        if max_turns is not None:
            agent.max_rounds = max(1, int(max_turns))

    @property
    def agent(self) -> Any:
        return self._agent

    @property
    def model(self) -> str:
        return self._model

    async def run_turn(
        self,
        prompt: str | ConversationMessage,
        *,
        context: AgentContext,
    ) -> AsyncIterator[AgentEvent]:
        del context  # the agent owns its history; context is informational
        if isinstance(prompt, ConversationMessage):
            prompt_text = _user_text(prompt.content)
        else:
            prompt_text = prompt
        llm = self._agent.llm
        prompt_before = getattr(llm, "total_prompt_tokens", 0)
        completion_before = getattr(llm, "total_completion_tokens", 0)

        queue: asyncio.Queue[tuple] = asyncio.Queue()

        def emit(item: tuple) -> None:
            queue.put_nowait(item)

        async def producer() -> None:
            try:
                final = await self._agent.achat(
                    prompt_text,
                    on_token=lambda t: emit(("delta", t)),
                    on_tool=lambda name, args: emit(("tool_start", name, args)),
                    on_tool_result=lambda n, a, out, err: emit(("tool_result", n, a, out, err)),
                )
                emit(("done", final))
            except Exception as exc:
                emit(("error", exc))

        task = asyncio.create_task(producer())
        final_text: str | None = None
        try:
            while True:
                kind, *payload = await queue.get()
                if kind == "done":
                    final_text = payload[0]
                    break
                if kind == "error":
                    yield AgentError(message=str(payload[0]), recoverable=True)
                    return
                if kind == "delta":
                    yield AgentTextDelta(text=payload[0])
                elif kind == "tool_start":
                    yield AgentToolCallStart(tool_name=payload[0], tool_input=payload[1])
                elif kind == "tool_result":
                    yield AgentToolCallResult(
                        tool_name=payload[0],
                        output=payload[2],
                        is_error=payload[3],
                    )
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        input_tokens = max(0, getattr(llm, "total_prompt_tokens", 0) - prompt_before)
        output_tokens = max(0, getattr(llm, "total_completion_tokens", 0) - completion_before)
        yield AgentTurnEnd(
            text=final_text or "",
            usage=UsageSnapshot(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    def load_history(self, messages: list[ConversationMessage]) -> None:
        self._agent.load_messages(_to_corecoder_messages(messages))

    def clear_history(self) -> None:
        self._agent.reset()


def build_corecoder_backend(
    *,
    model: str | None = None,
    system_prompt: str = "",
    cwd: str | None = None,
    max_turns: int | None = None,
    tool_metadata: dict[str, Any] | None = None,
    engine_config: dict | None = None,
) -> CoreCoderBackend:
    """Build a CoreCoderBackend from env + per-engine config."""
    del cwd, tool_metadata
    from corecoder.agent import Agent
    from corecoder.config import Config
    from corecoder.llm import OpenAICompatProvider

    cfg = Config.from_env()
    if engine_config:
        cfg = Config(
            model=engine_config.get("model", cfg.model),
            api_key=engine_config.get("api_key", cfg.api_key),
            base_url=engine_config.get("base_url", cfg.base_url),
            max_tokens=engine_config.get("max_tokens", cfg.max_tokens),
            temperature=engine_config.get("temperature", cfg.temperature),
            max_context_tokens=engine_config.get("max_context_tokens", cfg.max_context_tokens),
            provider=engine_config.get("provider", cfg.provider),
        )

    resolved_model = model or cfg.model
    llm = OpenAICompatProvider(
        model=resolved_model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        max_tokens=cfg.max_tokens,
        temperature=cfg.temperature,
    )
    agent = Agent(llm=llm, max_context_tokens=cfg.max_context_tokens)
    return CoreCoderBackend(
        agent,
        model=resolved_model,
        system_prompt=system_prompt,
        max_turns=max_turns,
    )


__all__ = ["CoreCoderBackend", "build_corecoder_backend"]
