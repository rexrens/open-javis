"""CoreCoder - Minimal AI coding agent inspired by Claude Code's architecture."""

__version__ = "0.4.0"

from corecoder.agent import Agent
from corecoder.config import Config
from corecoder.llm import OpenAICompatProvider
from corecoder.tools import ALL_TOOLS

__all__ = ["ALL_TOOLS", "Agent", "Config", "OpenAICompatProvider", "__version__"]
