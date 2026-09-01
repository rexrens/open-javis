"""ScriptedAdapter — deterministic offline playback (tests/demos).

Plays back a fixed script of ``StreamChunk`` sequences, one per
:meth:`stream` call. A scripted model doesn't respond to request content or
sampling params; running out of turns is an error (a broken loop shows up
immediately).

Scripts are built with :func:`javis.harness.llm.chunk_response` — e.g.
``ScriptedAdapter([chunk_response(text="hello"), chunk_response(tool_calls=[...])])``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from javis.harness.types import AbortSignal, GenerateOptions, StreamChunk
from javis.llm.adapter import LLMAdapter, LlmProviderInfo, LlmResolvedModelInfo


class ScriptedAdapter(LLMAdapter):
    """Plays back scripted chunk sequences, one per stream() call."""

    def __init__(
        self,
        script: list[list[Any]],
        model: str = "scripted-demo",
        *,
        max_context_tokens: int = 128_000,
        max_tokens: int = 4096,
    ) -> None:
        self.model = model
        self.max_context_tokens = max_context_tokens
        self.max_tokens = max_tokens
        self._turns = [list(seq) for seq in script]

    # -- provider metadata ----------------------------------------------------

    def set_model(self, model: str) -> None:
        self.model = model

    def provider_info(self, provider: str) -> LlmProviderInfo:
        return LlmProviderInfo(id=provider, name=provider)

    async def resolve_model(
        self,
        provider: str,
        model: str,
        signal: AbortSignal | None = None,
    ) -> LlmResolvedModelInfo:
        del signal
        return LlmResolvedModelInfo(
            provider=provider,
            id=model,
            name=model,
            context_window=self.max_context_tokens,
            default_max_tokens=self.max_tokens,
        )

    # -- core stream ----------------------------------------------------------

    async def stream(self, options: GenerateOptions) -> AsyncIterator[StreamChunk]:
        """Play back the next scripted chunk sequence (request ignored)."""
        del options
        if not self._turns:
            raise RuntimeError("ScriptedAdapter ran out of turns")
        for chunk in self._turns.pop(0):
            yield chunk


__all__ = ["ScriptedAdapter"]
