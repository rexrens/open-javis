"""LLM provider implementations for the javis ``LLMProvider`` contract.

Concrete providers (``OpenAICompatProvider`` for DeepSeek/Qwen/Kimi/Ollama,
``ScriptedProvider`` for deterministic offline tests) live here, separate from
the ``javis.contracts.llm`` contract definitions and from any engine that
consumes them. ``javis.contracts.llm`` re-exports these names so callers can
stay on the contract module.
"""

from __future__ import annotations

from .providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    OpenAICompatProvider,
    ScriptedProvider,
    ToolCall,
    estimated_cost,
    is_fallback_trigger,
)

__all__ = [
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "OpenAICompatProvider",
    "ScriptedProvider",
    "ToolCall",
    "estimated_cost",
    "is_fallback_trigger",
]
