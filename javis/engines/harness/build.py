"""HarnessEngine factory — end-to-end assembly (dsh adapter + real provider).

Mirrors the old ``CoreCoderEngine.build``: config parsing stays in the caller
(the runtime resolves provider/model/api_key from ``JavisConfig``); this
method owns assembly — the OpenAI-compatible provider (DeepSeek/Qwen/Kimi/
Ollama/…), the javis tool registry snapshot (built-ins + plugin tools), and
the ``HarnessEngine`` shell.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from javis.contracts.llm import LLMProvider
from javis.contracts.tools import ToolRegistry as JavisToolRegistry

from .compression import HISTORY_MAX_MESSAGES, MAX_TOOL_OUTPUT_CHARS
from .engine import HarnessEngine


def build(
    *,
    model: str,
    api_key: str,
    base_url: str | None = None,
    provider_name: str = "",
    max_tokens: int | None = None,
    system_prompt: str = "",
    cwd: str | Path,
    workspace: str | Path = "",
    session_id: str = "",
    max_turns: int | None = None,
    tool_metadata: dict[str, Any] | None = None,
    javis_tools: JavisToolRegistry | None = None,
    max_parallel_tool_calls: int = 4,
    max_steps_per_turn: int = 20,
    history_max_messages: int = HISTORY_MAX_MESSAGES,
    tool_output_max_chars: int = MAX_TOOL_OUTPUT_CHARS,
    provider: LLMProvider | None = None,
) -> HarnessEngine:
    """Build the harness engine end-to-end.

    Pass ``provider`` to inject an existing LLM provider (tests/demos use
    ``ScriptedProvider``); otherwise an ``OpenAICompatProvider`` is built
    from ``model`` / ``api_key`` / ``base_url`` / ``max_tokens``.
    """
    if provider is None:
        from .providers import OpenAICompatProvider

        provider_kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
        }
        if max_tokens is not None:
            provider_kwargs["max_tokens"] = max_tokens
        provider = OpenAICompatProvider(**provider_kwargs)
    return HarnessEngine(
        provider=provider,
        provider_name=provider_name,
        model=model,
        system_prompt=system_prompt,
        cwd=cwd,
        workspace=workspace,
        session_id=session_id,
        max_turns=max_turns,
        tool_metadata=tool_metadata,
        javis_tools=javis_tools,
        max_parallel_tool_calls=max_parallel_tool_calls,
        max_steps_per_turn=max_steps_per_turn,
        history_max_messages=history_max_messages,
        tool_output_max_chars=tool_output_max_chars,
    )


__all__ = ["build"]
