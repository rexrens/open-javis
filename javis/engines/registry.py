"""Engine registry — maps engine names to AgentBackend factories.

Third-party engines register themselves with register_engine(); javis
resolves the active engine via javis.host.runtime and builds the backend with
create_agent_backend(). The registry module itself never imports concrete
engines (factories import lazily), so unused backends stay unloaded.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from javis.contracts.protocol import AgentBackend

BackendFactory = Callable[..., AgentBackend]

_ENGINES: dict[str, BackendFactory] = {}


def register_engine(name: str, factory: BackendFactory) -> None:
    # Hyphenated names (e.g. "claude-code-cli", "dummy-test") are allowed;
    # spaces and other punctuation are not.
    if not name or not name.replace("-", "_").isidentifier():
        raise ValueError(f"Invalid engine name: {name!r}")
    _ENGINES[name] = factory


def list_engines() -> list[str]:
    return sorted(_ENGINES)


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
    factory = _ENGINES.get(name)
    if factory is None:
        available = ", ".join(list_engines()) or "(none registered)"
        raise ValueError(f"Unknown engine {name!r}; available: {available}")
    return factory(
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
    register_engine("corecoder", _build_corecoder_backend)


_register_builtin_engines()


__all__ = [
    "BackendFactory",
    "create_agent_backend",
    "get_engine_config",
    "list_engines",
    "register_engine",
]
