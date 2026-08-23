"""CoreCoder engine — the built-in agent backend.

Formerly a standalone top-level ``corecoder`` package; moved here so the
engine lives under ``javis/engines/`` alongside its ``AgentBackend`` adapter.
Exports the agent API (``Agent`` / ``Config`` / ``OpenAICompatProvider`` /
``ALL_TOOLS``) plus the javis-side adapter (``CoreCoderBackend``).
"""

from __future__ import annotations

from javis.engines.corecoder.agent import Agent
from javis.engines.corecoder.backend import CoreCoderBackend, build_corecoder_backend
from javis.engines.corecoder.config import Config
from javis.engines.corecoder.llm import OpenAICompatProvider
from javis.engines.corecoder.tools import ALL_TOOLS

__version__ = "0.4.0"

__all__ = [
    "ALL_TOOLS",
    "Agent",
    "Config",
    "CoreCoderBackend",
    "OpenAICompatProvider",
    "__version__",
    "build_corecoder_backend",
]
