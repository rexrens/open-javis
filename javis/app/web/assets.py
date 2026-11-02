"""Locate and validate the prepared dsh web assets (``frontend/web``)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: dsh release the wire contract in this package was written against.
REQUIRED_DSH_VERSION = "0.1.6-alpha.2"

#: Files the assembly step must have produced.
BOOT_FILE = "boot.json"
MANIFEST_FILE = "manifest.json"


class MissingAssetsError(RuntimeError):
    """Assets are absent or unusable; the message carries the fix command."""


@dataclass(frozen=True)
class WebAssets:
    """One prepared asset directory."""

    root: Path
    boot: dict[str, Any]
    manifest: dict[str, Any]

    @property
    def dist_root(self) -> Path:
        return self.root / str(self.boot.get("assets", {}).get("distRoot", "dist"))

    @property
    def dist_index(self) -> Path:
        return self.root / str(self.boot.get("assets", {}).get("distIndex", "dist/index.html"))

    @property
    def graph(self) -> dict[str, Any]:
        graph = self.boot.get("graph")
        return graph if isinstance(graph, dict) else {}

    @property
    def injections(self) -> list[dict[str, Any]]:
        rows = self.boot.get("injections")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    @property
    def dsh_version(self) -> str:
        return str(self.manifest.get("dshVersion", "unknown"))

    @property
    def dsh_commit(self) -> str:
        return str(self.manifest.get("dshCommit", "unknown"))

    def plugin_path(self, pathname: str) -> Path | None:
        """Resolve one ``/plugins/...`` request to a file, or None.

        Exact bundle keys win; anything else resolves as a path under the
        ``plugins/`` root, which is how a package's dynamic chunks
        (``./client.<name>.js``) are served next to its entry bundle.
        """
        plugins_root = (self.root / "plugins").resolve()
        bundles = self.boot.get("bundles")
        file_name = bundles.get(pathname) if isinstance(bundles, dict) else None
        relative = file_name if isinstance(file_name, str) else None
        if relative is None:
            if not pathname.startswith("/plugins/"):
                return None
            relative = pathname[len("/plugins/") :]
            if relative == "" or "?" in relative:
                return None
        target = (plugins_root / relative).resolve()
        if not target.is_file() or not str(target).startswith(str(plugins_root)):
            return None
        return target

    def dist_path(self, pathname: str) -> Path | None:
        """Resolve one static request inside the dist root, or None.

        Traversal outside the dist root is rejected, and absent targets return
        None rather than falling back to the SPA index - dsh's own rule.
        """
        relative = pathname.lstrip("/")
        root = self.dist_root.resolve()
        target = (root / relative).resolve() if relative else root
        if target != root and not str(target).startswith(str(root)):
            return None
        if target == root or target == self.dist_index.resolve():
            return self.dist_index.resolve()
        if target.is_file():
            return target
        return None


def build_hint(frontend_dir: Path) -> str:
    """The command a user must run when assets are missing."""
    return (
        f"web assets are missing in {frontend_dir}. Build them once with:\n"
        f"  cd {frontend_dir} && npm run prepare -- --dsh-root /path/to/deepseek-harness\n"
        "(the dsh checkout needs `pnpm install && pnpm run build` first)"
    )


def load_assets(frontend_dir: Path) -> WebAssets:
    """Load and validate one prepared asset directory.

    Raises:
        MissingAssetsError: the assembly output is absent, malformed, or has no
            usable boot graph.
    """
    boot_path = frontend_dir / BOOT_FILE
    manifest_path = frontend_dir / MANIFEST_FILE
    if not boot_path.is_file() or not manifest_path.is_file():
        raise MissingAssetsError(build_hint(frontend_dir))
    try:
        boot = json.loads(boot_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise MissingAssetsError(
            f"web assets in {frontend_dir} are unreadable: {exc}\n{build_hint(frontend_dir)}"
        ) from exc
    if not isinstance(boot, dict) or not isinstance(manifest, dict):
        raise MissingAssetsError(
            f"web assets in {frontend_dir} are malformed\n{build_hint(frontend_dir)}"
        )

    assets = WebAssets(root=frontend_dir, boot=boot, manifest=manifest)
    if not assets.dist_index.is_file():
        raise MissingAssetsError(
            f"web assets in {frontend_dir} have no {assets.dist_index.name}; "
            f"the dsh build output was not copied.\n{build_hint(frontend_dir)}"
        )
    if not assets.graph.get("entries"):
        raise MissingAssetsError(
            f"web assets in {frontend_dir} carry an empty boot graph.\n{build_hint(frontend_dir)}"
        )
    return assets


def version_mismatch(assets: WebAssets, expected: str | None = None) -> str | None:
    """Return a mismatch message, or None when the recorded version fits."""
    wanted = expected or REQUIRED_DSH_VERSION
    if assets.dsh_version == wanted:
        return None
    return (
        f"web assets were prepared from dsh {assets.dsh_version} but this javis build "
        f"implements the {wanted} wire contract; rebuild them with "
        "`javis web --rebuild-assets --dsh-root <checkout>`"
    )
