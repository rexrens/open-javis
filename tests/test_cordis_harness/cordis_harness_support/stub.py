"""Small stand-ins for the few collaborators the services actually use."""

from __future__ import annotations

from typing import Any


class StubContext:
    """A context that only records emitted events."""

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[Any, ...]]] = []

    def emit(self, name: str, *args: Any) -> None:
        self.events.append((name, args))

    def get(self, name: str, strict: bool = True) -> Any:
        return None

    def names(self) -> list[str]:
        return [name for name, _ in self.events]
