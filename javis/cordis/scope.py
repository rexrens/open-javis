"""Isolation scopes: service name -> scope label resolution.

Cordis resolves services by *label*: the root context assigns every provided
service name a unique default label; ``ctx.isolate(name, label)`` creates a
child context that resolves ``name`` against a different label, so a different
implementation can be provided without affecting the parent scope. Two
``isolate`` calls sharing a label join the same scope.

This module implements the label map as an immutable-ish chain: child contexts
inherit the parent map and may override individual names.
"""

from __future__ import annotations

from typing import Any


class IsolationMap:
    """A chain of service-name -> label overrides.

    Lookups walk from the current map up to the root; ``override`` writes only
    into the current map; ``ensure`` assigns a fresh default label on first
    use (used by ``ctx.provide`` on the root map).
    """

    __slots__ = ("_parent", "_own")

    def __init__(self, parent: "IsolationMap | None" = None):
        self._parent = parent
        self._own: dict[str, object] = {}

    def get(self, name: str) -> object | None:
        if name in self._own:
            return self._own[name]
        if self._parent is not None:
            return self._parent.get(name)
        return None

    def override(self, name: str, label: object) -> None:
        self._own[name] = label

    def ensure(self, name: str) -> object:
        """Return the existing label for ``name`` or assign a fresh one.

        ``ensure`` only ever assigns when the name has no label anywhere in the
        chain (JS ``??=`` semantics on the root map).
        """
        label = self.get(name)
        if label is None:
            label = object()
            self._own[name] = label
        return label


class InterceptMap:
    """Service-name -> intercept-config chain (mirrors ``Context[intercept]``).

    ``extend()`` chains onto the parent map; ``set`` writes only into the
    current level, shadowing ancestors (like a JS prototype chain). Unlike a
    flat dict this preserves *every* entry for a name so
    ``Service.resolve_config`` can merge ancestor entries first, then nearer
    ones.
    """

    __slots__ = ("_parent", "_own")

    def __init__(self, parent: "InterceptMap | None" = None):
        self._parent = parent
        self._own: dict[str, Any] = {}

    def set(self, name: str, config: Any) -> None:
        self._own[name] = config

    def get(self, name: str) -> Any | None:
        """Nearest (shadowing) entry for ``name``."""
        if name in self._own:
            return self._own[name]
        if self._parent is not None:
            return self._parent.get(name)
        return None

    def entries(self, name: str) -> list[Any]:
        """Every entry for ``name``, ancestors first (root-most first)."""
        result: list[Any] = []
        if self._parent is not None:
            result.extend(self._parent.entries(name))
        if name in self._own:
            result.append(self._own[name])
        return result
