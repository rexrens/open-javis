"""Process-wide launch overrides set by the CLI before the composition boots.

The ``cdh`` entry point parses argv before any plugin exists, so the values
live here until the plugins that care about them load (``harness.plugins.cli``
and ``harness.plugins.session``). Only explicitly provided flags are recorded;
absent flags stay ``None`` and therefore leave the ``cordis.yml`` values in
place.
"""

from __future__ import annotations

from typing import Any


class Options:
    """A tiny mutable bag of launch options, read by plugins at load time."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def set(self, **values: Any) -> None:
        """Record every non-``None`` value (``None`` means "flag not given")."""
        for key, value in values.items():
            if value is not None:
                self._values[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._values)

    def clear(self) -> None:
        self._values.clear()


#: The launch options of the current process.
LAUNCH = Options()
