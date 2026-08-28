"""Typed service contracts for the plugin system.

A service is ``(name, type)``: plugins look it up by name and validate the
type via ``ctx.get(name, Type)``. The host provides built-ins (owner=None,
never revoked); plugins provide their own with ``ctx.provide``.

Names are stable strings — changing one breaks every plugin using it. The
contract *types* live with the objects they describe (e.g.
``javis.engines.corecoder.tools.ToolRegistry`` for ``tools``), not here; this
module only fixes the names so core and plugins agree on them.
"""

from __future__ import annotations

TOOLS_SERVICE = "tools"
COMMANDS_SERVICE = "commands"
CONFIG_SERVICE = "config"
LLM_SERVICE = "llm"

# Reserved for the engine seam (phase 3): a plugin that provides "engine"
# replaces the built-in CoreCoderEngine (see runtime._build_default_engine).
ENGINE_SERVICE = "engine"

__all__ = [
    "COMMANDS_SERVICE",
    "CONFIG_SERVICE",
    "ENGINE_SERVICE",
    "LLM_SERVICE",
    "TOOLS_SERVICE",
]
