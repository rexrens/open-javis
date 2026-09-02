"""LLM layer — dsh-style ``LlmRuntime`` + adapters.

Port of ``@deepseek-ai/dsh-llm`` (``packages/llm/llm/src/index.ts``),
reworked 2026-09-01: the former two-layer bridge
(``LLMProvider``/``LLMRequest``/``LLMResponse`` in ``javis.contracts.llm`` +
``JavisLLMAdapter`` in the harness) is gone. This package is the whole LLM
layer:

- :class:`LlmRuntime` — the ``llm`` service: adapter registry (provider
  routes → adapters), configurable-provider directory, model discovery, and
  an interceptable ``llm/stream`` waterfall for raw runtime streams.
- :class:`LLMAdapter` — the provider-wire contract: implement
  ``stream(GenerateOptions) → StreamChunk`` only; serialization lives inside
  the adapter.
- :class:`OpenAICompatAdapter` — DeepSeek/Qwen/Kimi/Ollama via the openai SDK.
- :class:`ScriptedAdapter` — deterministic scripted playback (tests/demos).
- :func:`estimated_cost` — pricing table for usage cost estimates.

Consumes the harness message vocabulary (``javis.harness.types``); the
harness engine consumes this package (adapter wiring only).
"""

from __future__ import annotations

from .adapter import (
    LLMAdapter,
    LlmModelInfo,
    LlmProviderInfo,
    LlmResolvedModelInfo,
    PreparedAdapterCall,
)
from .openai_compat import OpenAICompatAdapter, is_fallback_trigger
from .pricing import estimated_cost
from .runtime import (
    AdapterRegistrationHandle,
    DirectoryRegistrationHandle,
    LlmConfigurableProvider,
    LlmDiscoveredModel,
    LlmModelDiscoveryRequest,
    LlmRuntime,
)
from .scripted import ScriptedAdapter

__all__ = [
    "AdapterRegistrationHandle",
    "DirectoryRegistrationHandle",
    "LLMAdapter",
    "LlmConfigurableProvider",
    "LlmDiscoveredModel",
    "LlmModelDiscoveryRequest",
    "LlmModelInfo",
    "LlmProviderInfo",
    "LlmResolvedModelInfo",
    "LlmRuntime",
    "OpenAICompatAdapter",
    "PreparedAdapterCall",
    "ScriptedAdapter",
    "estimated_cost",
    "is_fallback_trigger",
]
