"""Minimal named logger service (``ctx.logger``).

Mirrors ``LoggerService`` from Cordis: ``ctx.logger(name)`` returns a named
logger; ``ctx.logger.info(...)`` etc. log through the root logger. Output goes
to stderr with a timestamp, level and scope.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context import Context

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}

_lock = threading.Lock()


class Logger:
    def __init__(self, scope: str | None = None, level: int = 20):
        self.scope = scope
        self.level = level

    def _log(self, level: str, level_no: int, message: str, *args: Any) -> None:
        if level_no < self.level:
            return
        if args:
            try:
                message = message % args
            except (TypeError, ValueError):
                message = f"{message} {args!r}"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        scope = f" [{self.scope}]" if self.scope else ""
        with _lock:
            print(f"{ts} [{level[0].upper()}] {message}{scope}", file=sys.stderr)

    def debug(self, message: str, *args: Any) -> None:
        self._log("debug", _LEVELS["debug"], message, *args)

    def info(self, message: str, *args: Any) -> None:
        self._log("info", _LEVELS["info"], message, *args)

    def warn(self, message: str, *args: Any) -> None:
        self._log("warn", _LEVELS["warn"], message, *args)

    def error(self, message: Any, *args: Any) -> None:
        text = f"{message}" if isinstance(message, BaseException) else str(message)
        if isinstance(message, BaseException):
            text = f"{type(message).__name__}: {message}"
        self._log("error", _LEVELS["error"], text, *args)


class LoggerService:
    """The logging service; callable to obtain a named logger."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self._root = Logger(level=20)

    def __call__(self, name: str | None = None) -> Logger:
        if name is None:
            return self._root
        return Logger(scope=name)

    def __getattr__(self, level: str) -> Any:
        return getattr(self._root, level)
