# javis/plugins/registry.py
"""PluginRegistry — the table of PluginInstances and their lifecycle driver."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from javis.plugins.context import EventBus, ServiceRegistry
from javis.plugins.instance import CtxBuilder, PluginInstance, PluginState


@dataclass
class LoadReport:
    """Outcome of activating all plugins."""

    loaded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


class PluginRegistry:
    """Owns the plugin instance table and drives activation/shutdown."""

    def __init__(
        self,
        *,
        services: ServiceRegistry,
        bus: EventBus,
        ctx_builder: CtxBuilder,
    ) -> None:
        self.services = services
        self.bus = bus
        self.ctx_builder = ctx_builder
        self._instances: dict[str, PluginInstance] = {}

    def add(self, instance: PluginInstance) -> None:
        self._instances[instance.name] = instance

    def get(self, name: str) -> PluginInstance | None:
        return self._instances.get(name)

    async def activate_all(self, timeout: float = 10.0) -> LoadReport:
        """Start every instance in parallel; never raises."""
        report = LoadReport(skipped=[])
        results = await asyncio.gather(
            *(i.start() for i in self._instances.values()),
            return_exceptions=True,
        )
        for name, result in zip(self._instances, results):
            inst = self._instances[name]
            if isinstance(result, Exception):
                inst.error = result
                inst.state = PluginState.FAILED
            if inst.state is PluginState.ACTIVE:
                report.loaded.append(name)
            elif inst.state is PluginState.FAILED:
                report.failed.append(name)
                report.errors[name] = str(inst.error)
        return report

    async def close_all(self) -> None:
        """Stop every instance in parallel; disposer errors are logged, not raised."""
        await asyncio.gather(
            *(i.stop() for i in self._instances.values()),
            return_exceptions=True,
        )

    async def run_start_hooks(self) -> None:
        for inst in self._instances.values():
            if inst.ctx is not None and inst.state is PluginState.ACTIVE:
                await inst.ctx.run_start_hooks()

    def list_plugins(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "state": inst.state, "error": inst.error}
            for name, inst in sorted(self._instances.items())
        ]
