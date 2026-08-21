"""Plugin framework errors."""

from __future__ import annotations


class PluginError(Exception):
    """Base class for plugin framework errors."""


class PluginConfigError(PluginError):
    """Plugin config failed pydantic validation."""


class PluginDependencyError(PluginError):
    """A plugin's inject dependencies were never provided."""
