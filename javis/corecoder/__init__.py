"""CoreCoder - Minimal AI coding agent inspired by Claude Code's architecture."""

__version__ = "0.4.0"

from javis.corecoder.agent import Agent
from javis.corecoder.llm import LLM
from javis.corecoder.config import Config
from javis.corecoder.tools import ALL_TOOLS

__all__ = ["Agent", "LLM", "Config", "ALL_TOOLS", "__version__"]
