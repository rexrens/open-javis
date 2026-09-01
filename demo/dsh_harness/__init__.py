"""harness — a dsh-style agent harness on the Cordis plugin system.

Contract surface (``contracts``), session log (``session``), inbox
(``inbox``), LLM seam (``llm``), tools (``tools``), the ReactLoopAgent
(``agent``), and a scripted mock provider (``mock_llm``). Everything is
wired by the Cordis plugin system in ``plugins/`` + ``cordis.yml``.
"""

from .agent import ReactLoopAgent
from .contracts import (
    AgentOptions,
    ToolExecutionResult,
)
from .llm import LLM, BlockAssembler
from .session import Session
from .tools import Tool, ToolRegistry

__all__ = [
    "LLM",
    "AgentOptions",
    "BlockAssembler",
    "ReactLoopAgent",
    "Session",
    "Tool",
    "ToolExecutionResult",
    "ToolRegistry",
]
