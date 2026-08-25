"""Engine registry — maps engine names to AgentBackend factories.

Engines register with ``EngineRegistry.register`` (or the module-level
``register_engine`` for javis's own built-ins); javis resolves the active
engine via javis.host.runtime and builds the backend with
``create_agent_backend``. The registry module itself never imports concrete
engines (factories import lazily), so unused backends stay unloaded.

``ENGINE_REGISTRY`` is the shared default instance; the module-level
functions delegate to it. Plugins receive the same instance as the
``engines`` service, so plugin-registered engines are visible to
``create_agent_backend`` and can be unregistered on unload via the disposer
returned by ``register``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from javis.contracts.protocol import AgentBackend

BackendFactory = Callable[..., AgentBackend]


class EngineRegistry:
    """Mutable engine table. ``register`` returns an unregister disposer."""

    def __init__(self) -> None:
        self._engines: dict[str, BackendFactory] = {}

    def register(self, name: str, factory: BackendFactory) -> Callable[[], None]:
        # Hyphenated names (e.g. "claude-code-cli", "dummy-test") are allowed;
        # spaces and other punctuation are not.
        if not name or not name.replace("-", "_").isidentifier():
            raise ValueError(f"Invalid engine name: {name!r}")
        self._engines[name] = factory
        return lambda: self.unregister(name)

    def unregister(self, name: str) -> None:
        """Remove an engine by name. Missing names are silently ignored."""
        self._engines.pop(name, None)

    def list(self) -> list[str]:
        return sorted(self._engines)

    def get(self, name: str) -> BackendFactory | None:
        return self._engines.get(name)

    def create(self, name: str, **kwargs: Any) -> AgentBackend:
        factory = self._engines.get(name)
        if factory is None:
            available = ", ".join(self.list()) or "(none registered)"
            raise ValueError(f"Unknown engine {name!r}; available: {available}")
        return factory(**kwargs)


ENGINE_REGISTRY = EngineRegistry()


def register_engine(name: str, factory: BackendFactory) -> Callable[[], None]:
    """Register an engine on the shared registry (compat: returns disposer)."""
    return ENGINE_REGISTRY.register(name, factory)


def unregister_engine(name: str) -> None:
    """Remove an engine from the shared registry. Missing names are ignored."""
    ENGINE_REGISTRY.unregister(name)


def list_engines() -> list[str]:
    return ENGINE_REGISTRY.list()


def get_engine_config(name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Extract the per-engine config subsection (or {})."""
    return dict(config.get("engines", {}).get(name, {}))


def create_agent_backend(
    name: str,
    *,
    model: str | None = None,
    system_prompt: str = "",
    cwd: str,
    max_turns: int | None = None,
    tool_metadata: dict[str, Any] | None = None,
    engine_config: dict[str, Any] | None = None,
) -> AgentBackend:
    return ENGINE_REGISTRY.create(
        name,
        model=model,
        system_prompt=system_prompt,
        cwd=cwd,
        max_turns=max_turns,
        tool_metadata=tool_metadata or {},
        engine_config=engine_config or {},
    )


def _build_corecoder_backend(**kwargs: Any) -> AgentBackend:
    from javis.engines.corecoder.backend import build_corecoder_backend

    return build_corecoder_backend(**kwargs)


def _register_builtin_engines() -> None:
    ENGINE_REGISTRY.register("corecoder", _build_corecoder_backend)


_register_builtin_engines()


__all__ = [
    "BackendFactory",
    "ENGINE_REGISTRY",
    "EngineRegistry",
    "create_agent_backend",
    "get_engine_config",
    "list_engines",
    "register_engine",
    "unregister_engine",
]
