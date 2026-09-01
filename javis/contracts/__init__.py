"""javis contracts: the stable protocol layer shared by every package.

Only pure definitions live here — no runtime logic, no side effects:

- ``engine.py`` — the ``AgentEngine`` interface (the only seam; an engine
  object owns history + usage and yields ``AgentEvent`` streams)
- ``host.py`` — the ``HostContext`` runtime facts the host injects as the
  ``host`` service (cwd / session_id / tool_metadata / CLI overrides)
- ``llm.py`` — the ``LLMProvider`` contract + data models (``LLMRequest`` /
  ``LLMResponse`` / ``ToolCall``); SDK-free, implementable by any provider
- ``services.py`` — stable service names for the plugin system's typed
  service contracts (``tools`` / ``commands`` / ``config`` / ``host`` /
  ``engine``)
- ``tools.py`` — the ``Tool`` interface and ``ToolRegistry`` (the typed
  ``tools`` service)
- ``types.py`` — the ``AgentEvent`` stream (text deltas, tool calls, errors…)
- ``messages.py`` — the ``ConversationMessage`` model and sanitization
- ``usage.py`` — the ``UsageSnapshot`` token/cost record

Everything above (host, session, engines) may depend on this
package; it depends on nothing within javis.
"""

from javis.contracts.engine import AgentEngine
from javis.contracts.host import HostContext
from javis.contracts.llm import LLMProvider, LLMRequest, LLMResponse, ToolCall
from javis.contracts.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock
from javis.contracts.services import (
    COMMANDS_SERVICE,
    CONFIG_SERVICE,
    ENGINE_SERVICE,
    HOST_SERVICE,
    LLM_SERVICE,
    TOOLS_SERVICE,
)
from javis.contracts.tools import Tool, ToolRegistry
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.contracts.usage import UsageSnapshot

__all__ = [
    "COMMANDS_SERVICE",
    "CONFIG_SERVICE",
    "ENGINE_SERVICE",
    "HOST_SERVICE",
    "LLM_SERVICE",
    "TOOLS_SERVICE",
    "AgentEngine",
    "AgentError",
    "AgentEvent",
    "AgentStatus",
    "AgentTextDelta",
    "AgentToolCallResult",
    "AgentToolCallStart",
    "AgentTurnEnd",
    "ConversationMessage",
    "HostContext",
    "ImageBlock",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "TextBlock",
    "Tool",
    "ToolCall",
    "ToolRegistry",
    "ToolResultBlock",
    "UsageSnapshot",
]
