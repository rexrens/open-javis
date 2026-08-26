# javis/plugins/registry.py
"""PluginRegistry — the plugin instance table and lifecycle driver.

Owns activation, shutdown, and (new) dependency-aware orchestration, mirroring
dsh's RegistryService / fiber dependency graph:

- ``dependency_graph`` / ``load_order`` — build the provide/inject DAG from
  runtime facts (``ServiceRegistry`` owners + each instance's ``inject``
  declaration), so no static ``provides`` list is needed.
- ``unload`` — stop one plugin and, transitively, every plugin that injects a
  service it provides (cascade, dependents first — dsh unload semantics).
- ``close_all`` — full shutdown in reverse topological order so a dependent's
  disposers still see the services it injected.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from javis.plugins.context import ServiceRegistry
from javis.plugins.instance import CtxBuilder, PluginInstance, PluginState

log = logging.getLogger(__name__)


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
        ctx_builder: CtxBuilder,
    ) -> None:
        self.services = services
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

    # ------------------------------------------------------------------
    # dependency graph (provide/inject DAG, derived from runtime facts)
    # ------------------------------------------------------------------

    def dependency_graph(self) -> dict[str, list[str]]:
        """Map plugin name → plugins that inject a service it provides.

        Built from the runtime provider index (``ServiceRegistry`` owners,
        recorded when ``ctx.provide`` runs) plus each instance's ``inject``
        declaration. Built-in services (owner=None) produce no edges, so a
        plugin that only injects built-ins is a graph leaf. Deterministic:
        dependents appear in registration order.
        """
        provided: dict[str, list[str]] = {}
        for svc, owner in self.services.owners().items():
            provided.setdefault(owner, []).append(svc)
        injects: dict[str, set[str]] = {
            inst.name: set(inst.inject) for inst in self._instances.values()
        }
        graph: dict[str, list[str]] = {name: [] for name in self._instances}
        for provider, svcs in provided.items():
            deps = graph.get(provider)
            if deps is None:  # service owned by an unregistered plugin — skip
                continue
            for svc in svcs:
                for name, reqs in injects.items():
                    if name != provider and svc in reqs and name not in deps:
                        deps.append(name)
        return graph

    def load_order(self) -> list[str]:
        """Topological order: providers before dependents (deterministic).

        Cyclic groups are appended after the acyclic prefix in registration
        order (no raise — shutdown paths must never fail).
        """
        graph = self.dependency_graph()
        in_degree: dict[str, int] = {name: 0 for name in graph}
        for deps in graph.values():
            for dep in deps:
                in_degree[dep] += 1
        order: list[str] = []
        queue = [name for name in graph if in_degree[name] == 0]  # registration order
        i = 0
        while i < len(queue):
            name = queue[i]
            i += 1
            order.append(name)
            for dep in graph[name]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)
        if len(order) < len(graph):
            seen = set(order)
            order.extend(name for name in graph if name not in seen)
        return order

    # ------------------------------------------------------------------
    # shutdown: cascade unload + full close
    # ------------------------------------------------------------------

    async def unload(self, name: str) -> list[str]:
        """Stop one plugin and, transitively, every plugin that injects a
        service it provides (dependents first, provider last).

        Returns the stopped plugin names in stop order. Unknown or already
        disposed plugins are no-ops (return ``[]``).
        """
        inst = self._instances.get(name)
        if inst is None or inst.state is PluginState.DISPOSED:
            return []
        graph = self.dependency_graph()
        stop_order: list[str] = []
        visited: set[str] = set()

        def visit(plugin: str) -> None:
            if plugin in visited:
                return
            visited.add(plugin)
            for dependent in graph.get(plugin, ()):
                visit(dependent)
            stop_order.append(plugin)

        visit(name)
        stopped: list[str] = []
        for plugin in stop_order:
            target = self._instances[plugin]
            if target.state is PluginState.DISPOSED:
                continue
            try:
                await target.stop()
            except Exception:
                log.exception("plugin %r failed during unload cascade", plugin)
            stopped.append(plugin)
        return stopped

    async def close_all(self) -> None:
        """Stop every instance: dependents before providers.

        Uses reverse topological order (same ordering as cascade unload) so a
        dependent's disposers still see the services it injected. Disposer
        errors are logged inside ``ctx.close``; each stop is additionally
        isolated so ``close_all`` itself never raises.
        """
        for name in reversed(self.load_order()):
            inst = self._instances.get(name)
            if inst is None or inst.state is PluginState.DISPOSED:
                continue
            try:
                await inst.stop()
            except Exception:
                log.exception("plugin %r failed during close_all", name)

    async def run_start_hooks(self) -> None:
        for inst in self._instances.values():
            if inst.ctx is not None and inst.state is PluginState.ACTIVE:
                await inst.ctx.run_start_hooks()

    def list_plugins(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "state": inst.state, "error": inst.error}
            for name, inst in sorted(self._instances.items())
        ]
