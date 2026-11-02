"""Shared fixtures for the web host tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GRAPH: dict[str, Any] = {
    "rev": "abc123abc123",
    "entries": [
        {
            "id": "@deepseek-ai/dsh-client-modules",
            "url": "/plugins/??@deepseek-ai/dsh-client-modules/client.js&rev=aaa111aaa111",
            "rev": "aaa111aaa111",
        }
    ],
    "batches": [
        {
            "phase": "bootstrap",
            "url": "/plugins/??@deepseek-ai/dsh-client-modules/client.js&rev=bbb222bbb222",
            "rev": "bbb222bbb222",
            "entries": ["@deepseek-ai/dsh-client-modules"],
        }
    ],
}


def write_assets(root: Path, *, dsh_version: str = "0.1.6-alpha.2") -> Path:
    """Create one synthetic asset directory the host can serve."""
    dist = root / "dist"
    plugins = root / "plugins"
    dist.mkdir(parents=True, exist_ok=True)
    plugins.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>t</title></head><body><div id=root></div>"
        '<script type="module" src="/assets/app.js"></script></body></html>',
        encoding="utf-8",
    )
    (dist / "assets").mkdir(exist_ok=True)
    (dist / "assets" / "app.js").write_text("export const app = 1\n", encoding="utf-8")
    (plugins / "combo-bootstrap.js").write_text("globalThis.__combo = 1\n", encoding="utf-8")

    boot = {
        "graph": GRAPH,
        "injections": [
            {
                "kind": "script",
                "placement": "head",
                "text": "window.__ModuleLoader__={mode:'queue',pendingQueue:[]}",
            },
            {
                "kind": "script-preload",
                "src": "/plugins/??@deepseek-ai/dsh-client-modules/client.js&rev=bbb222bbb222",
            },
            {"kind": "global", "name": "__DSH_BOOT__", "value": GRAPH},
        ],
        "bundles": {
            "/plugins/??@deepseek-ai/dsh-client-modules/client.js&rev=aaa111aaa111": (
                "combo-bootstrap.js"
            ),
            "/plugins/??@deepseek-ai/dsh-client-modules/client.js&rev=bbb222bbb222": (
                "combo-bootstrap.js"
            ),
        },
        "assets": {"distRoot": "dist", "distIndex": "dist/index.html"},
    }
    manifest = {
        "generatedAt": "2026-09-20T00:00:00.000Z",
        "dshVersion": dsh_version,
        "dshCommit": "ddefc45fbc",
        "entryCount": 1,
        "bundles": 2,
        "excludedRows": [],
        "autoAddedPackages": [],
    }
    (root / "boot.json").write_text(json.dumps(boot), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root
