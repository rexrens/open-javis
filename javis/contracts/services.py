"""Typed service contracts for the plugin system.

A service is ``(name, type)``: plugins look it up by name and validate the
type via ``ctx.get(name, Type)``. The host provides built-ins (owner=None,
never revoked); plugins provide their own with ``ctx.provide``.

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
LLM_SERVICE = "llm"
HOST_SERVICE = "host"

# A plugin that provides "engine" replaces the built-in CoreCoderEngine
# (see javis.app.runtime.build_runtime).
ENGINE_SERVICE = "engine"

__all__ = [
    "COMMANDS_SERVICE",
    "CONFIG_SERVICE",
    "ENGINE_SERVICE",
    "HOST_SERVICE",
    "LLM_SERVICE",
    "TOOLS_SERVICE",
]
