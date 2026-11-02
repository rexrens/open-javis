"""Typed service contracts for the plugin system.

A service is ``(name, type)``: plugins look it up by name and validate the
type via ``ctx.get(name, Type)``. The host provides built-ins (owner=None,
never revoked); composition rows provide their own with ``ctx.provide``.

Names are stable strings — changing one breaks every plugin using it. The
contract *types* live in ``javis.contracts`` (``ToolRegistry`` /
``HostContext``) or with the objects they describe (``JavisConfig`` /
``CommandRegistry``); this module only fixes the names so core and plugins
agree on them.
"""

from __future__ import annotations

TOOLS_SERVICE = "tools"
COMMANDS_SERVICE = "commands"
CONFIG_SERVICE = "config"
HOST_SERVICE = "host"

# -- harness rows (javis.harness.plugins.*) --------------------------------
#: Loop-facing tool registry (the live view over the host ``tools``).
AGENT_TOOLS_SERVICE = "agentTools"
#: Prompt assembly (persona + step context + tool schemas).
SYSTEM_PROMPT_SERVICE = "systemPrompt"
#: Loop driver configuration.
AGENT_LOOP_SERVICE = "agentLoop"
#: The model service (a ``javis.llm.LlmRuntime`` adapter registry).
LLM_SERVICE = "llm"
#: The whole agent: a ``javis.contracts.harness.Harness`` instance.
HARNESS_SERVICE = "harness"

__all__ = [
    "AGENT_LOOP_SERVICE",
    "AGENT_TOOLS_SERVICE",
    "COMMANDS_SERVICE",
    "CONFIG_SERVICE",
    "HARNESS_SERVICE",
    "HOST_SERVICE",
    "LLM_SERVICE",
    "SYSTEM_PROMPT_SERVICE",
    "TOOLS_SERVICE",
]
