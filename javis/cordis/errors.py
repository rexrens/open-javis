"""Framework errors with stable machine-readable codes."""

from __future__ import annotations

from typing import Any


class CordisError(Exception):
    """Framework error with a stable machine-readable ``code``.

    Mirrors ``CordisError`` from Cordis: the code is the primary identifier and
    doubles as the default message.
    """

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message if message is not None else ERROR_CODES.get(code, code))


class ValidationError(TypeError):
    """Raised when plugin configuration fails schema validation.

    Mirrors Cordis ``ValidationError`` (which extends ``TypeError``). The
    message aggregates one line per issue, e.g.::

        invalid config:
          - $.targets expected array but got not-an-array (at targets)
    """

    name = "ValidationError"

    def __init__(self, issues: list[Any]):
        self.issues = issues
        lines = []
        for issue in issues:
            if isinstance(issue, dict):
                path = issue.get("loc") or issue.get("path") or ()
                msg = issue.get("msg") or issue.get("message") or str(issue)
                if path:
                    lines.append(f"  - {msg} (at {'.'.join(map(str, path))})")
                else:
                    lines.append(f"  - {msg}")
            else:
                lines.append(f"  - {issue}")
        super().__init__("invalid config:\n" + "\n".join(lines))


ERROR_CODES: dict[str, str] = {
    "INACTIVE_EFFECT": "cannot create effect on inactive context",
}
