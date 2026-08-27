"""javis plugin system — cordis-like kernel.

Public API:
- PluginContext (services / events / lifecycle hooks)
- PluginInstance + PluginState (state machine)
- PluginRegistry (activation / shutdown / report)
- loader helpers: load_plugins / plugin_dirs
- errors
"""

from __future__ import annotations

from javis.plugins.context import PluginContext
from javis.plugins.errors import (
    PluginConfigError,
    PluginDependencyError,
    PluginError,
)
from javis.plugins.hot_reload import PluginWatcher
from javis.plugins.instance import PluginInstance, PluginState
from javis.plugins.loader import load_plugins, plugin_dirs, reload_plugin
from javis.plugins.registry import LoadReport, PluginRegistry

__all__ = [
    "LoadReport",
    "PluginConfigError",
    "PluginContext",
    "PluginDependencyError",
    "PluginError",
    "PluginInstance",
    "PluginRegistry",
    "PluginState",
    "PluginWatcher",
    "load_plugins",
    "plugin_dirs",
    "reload_plugin",
]
