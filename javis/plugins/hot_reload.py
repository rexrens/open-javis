"""Plugin file/config watcher for HMR and configuration hot updates."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from watchfiles import awatch

from javis.plugins.loader import discover_plugin_files, reload_plugin
from javis.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)


class PluginWatcher:
    """Watch plugin directories and an optional plugin config file."""

    def __init__(
        self,
        *,
        registry: PluginRegistry,
        dirs: list[Path],
        plugins_cfg: dict[str, Any],
        config_path: str | Path | None = None,
        debounce_ms: int = 300,
    ) -> None:
        self.registry = registry
        self.dirs = [Path(path) for path in dirs]
        self.plugins_cfg = plugins_cfg
        self.config_path = Path(config_path) if config_path else None
        self.debounce_ms = debounce_ms
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        paths = [str(path) for path in self.dirs]
        if self.config_path is not None:
            paths.append(str(self.config_path))
        for path in self.dirs:
            path.mkdir(parents=True, exist_ok=True)

        async for changes in awatch(*paths, debounce=self.debounce_ms):
            changed_paths = {str(path) for _change, path in changes}
            await self._handle_changes(changed_paths)

    async def _handle_changes(self, changed_paths: set[str]) -> None:
        plugin_by_path = {
            str(path): name for path, name in discover_plugin_files(self.dirs)
        }
        for raw_path in sorted(changed_paths):
            path = Path(raw_path).resolve()
            name = plugin_by_path.get(str(path))
            if name is not None and path.suffix == ".py":
                try:
                    await reload_plugin(
                        self.registry,
                        self.dirs,
                        self.plugins_cfg,
                        name,
                    )
                except Exception:
                    log.exception("plugin %r hot reload failed", name)
                continue
            if self.config_path is not None and path == self.config_path.resolve():
                await self._reload_config(path)

    async def _reload_config(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            plugins = data.get("plugins", {})
            if not isinstance(plugins, dict):
                return
            self.plugins_cfg = plugins
            await self.registry.update_many(plugins)
        except Exception:
            log.exception("plugin config hot reload failed for %s", path)


__all__ = ["PluginWatcher"]
