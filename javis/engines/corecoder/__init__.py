"""CoreCoder engine — the built-in javis engine implementation.

Exports the agent API (``Agent`` / ``Config`` / ``OpenAICompatProvider`` /
``ALL_TOOLS``) plus the javis-side engine (``CoreCoderEngine``, which
implements the ``AgentEngine`` contract).
"""

from __future__ import annotations

from javis.engines.corecoder.agent import Agent
from javis.engines.corecoder.config import Config
from javis.engines.corecoder.engine import CoreCoderEngine
from javis.engines.corecoder.llm import OpenAICompatProvider
from javis.engines.corecoder.tools import ALL_TOOLS

__version__ = "0.4.0"

__all__ = [
    "ALL_TOOLS",
    "Agent",
    "Config",
    "CoreCoderEngine",
    "OpenAICompatProvider",
    "__version__",
]
