"""Minimal application state for the javis TUI status bar.

Forked from openharness.state and trimmed to the fields the React frontend
actually renders. No provider/auth/MCP/bridge tracking — those concepts
don't exist in javis.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace


@dataclass
class AppState:
    """Mutable UI/session state shown in the status bar."""

    model: str
    cwd: str
    permission_mode: str = "default"
    theme: str = "default"
    provider: str = "javis"
    auth_status: str = "ok"
    base_url: str = ""
    vim_enabled: bool = False
    voice_enabled: bool = False
    voice_available: bool = False
    voice_reason: str = ""
    fast_mode: bool = False
    effort: str = "medium"
    passes: int = 1
    output_style: str = "default"
    keybindings: dict[str, str] = field(default_factory=dict)


class AppStateStore:
    """Tiny observable state store."""

    def __init__(self, initial_state: AppState) -> None:
        self._state = initial_state
        self._listeners: list[Callable[[AppState], None]] = []

    def get(self) -> AppState:
        return self._state

    def set(self, **updates) -> AppState:
        self._state = replace(self._state, **updates)
        for listener in list(self._listeners):
            listener(self._state)
        return self._state

    def subscribe(self, listener: Callable[[AppState], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe


__all__ = ["AppState", "AppStateStore"]
