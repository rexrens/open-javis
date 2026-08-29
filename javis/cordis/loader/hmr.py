"""Hot module replacement: watch plugin modules and the composition file.

Port of ``@deepseek-ai/cordis-plugin-hmr`` using a simple polling watcher
(no third-party dependencies). On change:

- a plugin module file is saved → the entry is disposed and re-mounted with
  fresh code (its effects unwind, dependents reload);
- ``cordis.yml`` is saved → entries are diffed by id: removed entries are
  unmounted, changed ones re-mounted, new ones mounted.

The watcher task keeps the event loop busy, so a composition with HMR stays
alive — matching the keepalive behavior of the JavaScript original.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..service import Service

if TYPE_CHECKING:
    from ..context import Context


class Hmr(Service):
    """Watches plugin modules and the composition file for changes."""

    inject = ["loader"]

    class Config(BaseModel):
        root: list[str] = Field(default_factory=lambda: ["."])
        interval: float = 0.3

    def __init__(self, ctx: "Context", config: Any):
        super().__init__(ctx, "hmr")
        self.config = config
        self.loader = ctx.get("loader")
        self._mtime: dict[tuple[Any, ...], float] = {}
        self._debounce_tasks: dict[tuple[Any, ...], asyncio.Task[Any]] = {}
        self._task = asyncio.ensure_future(self._watch())
        self.ctx.logger.info(f"hmr watching {config.root}")
        self.ctx.effect(lambda: self._cleanup)

    def _cleanup(self) -> None:
        self._task.cancel()
        for task in self._debounce_tasks.values():
            task.cancel()

    def _debounced(self, key: tuple[Any, ...], action: Any, delay: float | None = None) -> None:
        """Schedule ``action`` after a quiet period; a new change for the same
        key cancels the pending run (debounce, like the JS timer-based HMR).
        """
        delay = delay if delay is not None else max(0.2, self.config.interval * 2)
        old = self._debounce_tasks.pop(key, None)
        if old is not None:
            old.cancel()

        async def run() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            self._debounce_tasks.pop(key, None)
            try:
                await action()
            except BaseException as error:
                self.ctx.logger.error(error)

        self._debounce_tasks[key] = asyncio.ensure_future(run())

    async def _watch(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.config.interval)
                self._scan_modules()
                self._scan_composition()
        except asyncio.CancelledError:
            pass

    def _scan_modules(self) -> None:
        loader = self.loader
        if loader is None:
            return
        for entry_id, path in list(loader.module_paths().items()):
            if path is None:
                continue
            mtime = self._mtime_of(path)
            if mtime is None:
                continue
            key = ("module", entry_id)
            if key not in self._mtime:
                self._mtime[key] = mtime
                continue
            if mtime == self._mtime[key]:
                continue
            self._mtime[key] = mtime
            self.ctx.logger.info(f"hmr reload plugin at {path}")
            self._debounced(key, lambda eid=entry_id: self._reload_entry(eid))

        self._purge_stale_module_keys()

    def _purge_stale_module_keys(self) -> None:
        """Remove ``_mtime`` / ``_debounce_tasks`` entries whose entry_id is
        no longer tracked by the loader (entry removed via recompose)."""
        loader = self.loader
        if loader is None:
            return
        current = set(loader.module_paths())
        for key in list(self._mtime):
            if key[0] == "module" and key[1] not in current:
                del self._mtime[key]
        for key in list(self._debounce_tasks):
            if key[0] == "module" and key[1] not in current:
                task = self._debounce_tasks.pop(key, None)
                if task is not None:
                    task.cancel()

    def _scan_composition(self) -> None:
        loader = self.loader
        if loader is None:
            return
        path = loader.file
        mtime = self._mtime_of(path)
        if mtime is None:
            return
        key = ("composition",)
        if key not in self._mtime:
            self._mtime[key] = mtime
            return
        if mtime == self._mtime[key]:
            return
        self._mtime[key] = mtime
        self.ctx.logger.info(f"hmr reload composition at {path}")
        self._debounced(key, self._recompose)

    def _mtime_of(self, path: str) -> float | None:
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    async def _reload_entry(self, entry_id: str) -> None:
        result = self.loader.reload(entry_id)
        if result is not None:
            try:
                await result
            except BaseException as error:
                self.ctx.logger.error(error)

    async def _recompose(self) -> None:
        try:
            result = self.loader.recompose()
            if result is not None:
                await result
        except BaseException as error:
            self.ctx.logger.error(error)
