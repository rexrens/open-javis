"""Mock agent backend that emits canned responses for TUI development.

This is a stand-in for a real agent. It routes on prompt keywords so the
TUI can exercise every render path (text deltas, tool calls, errors,
status messages, permission prompts) without any model backend.

Replace ``MockAgent`` with a real ``AgentBackend`` implementation when ready.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from openharness.engine.messages import ConversationMessage

from javis.engine.types import (
    AgentContext,
    AgentError,
    AgentEvent,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)


def _prompt_text(prompt: str | ConversationMessage) -> str:
    if isinstance(prompt, ConversationMessage):
        return prompt.text
    return prompt or ""


class MockAgent:
    """Canned agent backend that dispatches on prompt keywords.

    Routing rules (case-insensitive, first match wins):
        - contains "error" or "错误"  → emit ``AgentError`` and stop
        - contains "status"           → emit ``AgentStatus`` then a normal turn
        - contains "tool" or "工具"   → emit a fake ``echo`` tool call
        - contains "permission" or "权限" → emit a tool call (host will
          surface the permission modal before the tool runs)
        - contains "chinese" or "中文" → reply in Chinese
        - otherwise                    → echo the prompt back
    """

    async def run_turn(
        self,
        prompt: str | ConversationMessage,
        *,
        context: AgentContext,
    ) -> AsyncIterator[AgentEvent]:
        text = _prompt_text(prompt).strip()
        lower = text.lower()

        if "error" in lower or "错误" in text:
            yield AgentError(message=f"Mock error triggered by prompt: {text!r}", recoverable=True)
            return

        if "status" in lower:
            yield AgentStatus(message=f"[mock] processing turn in {context.cwd}")

        if "permission" in lower or "权限" in text:
            yield AgentTextDelta(text="I need to run a tool that requires your permission.\n\n")
            yield AgentToolCallStart(
                tool_name="write_file",
                tool_input={"path": "mock.txt", "content": "permission demo"},
            )
            yield AgentToolCallResult(
                tool_name="write_file",
                output="Wrote 15 bytes to mock.txt (mock — no real file touched).",
            )
            yield AgentTextDelta(text="Done. The permission modal was exercised.\n")
            yield AgentTurnEnd(text="I need to run a tool that requires your permission.\n\nDone. The permission modal was exercised.\n")
            return

        if "tool" in lower or "工具" in text:
            yield AgentTextDelta(text=f"I'll use the echo tool to repeat: {text!r}\n\n")
            yield AgentToolCallStart(tool_name="echo", tool_input={"text": text})
            yield AgentToolCallResult(
                tool_name="echo",
                output=json.dumps({"echoed": text}, ensure_ascii=False),
            )
            yield AgentTextDelta(text="The echo tool completed successfully.\n")
            yield AgentTurnEnd(text=f"I'll use the echo tool to repeat: {text!r}\n\nThe echo tool completed successfully.\n")
            return

        if "chinese" in lower or "中文" in text:
            reply = f"（中文 mock 响应）你说了：{text}\n这是一个模拟回复，用于验证 TUI 中文渲染。"
            yield AgentTextDelta(text=reply + "\n")
            yield AgentTurnEnd(text=reply + "\n")
            return

        # Default: echo with a mock prefix
        reply = f"[mock] You said: {text}\n\nThis is a canned response from javis MockAgent."
        yield AgentTextDelta(text=reply + "\n")
        yield AgentTurnEnd(text=reply + "\n")


__all__ = ["MockAgent"]
