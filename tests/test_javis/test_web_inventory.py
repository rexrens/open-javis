"""The generated dsh web interface inventory stays reproducible."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "dsh-web-interface-inventory.md"


def _dsh_root() -> Path | None:
    candidates = [
        os.environ.get("DSH_ROOT"),
        str(REPO_ROOT.parent / "deepseek-harness"),
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "packages").is_dir():
            return Path(candidate)
    return None


def _load_module() -> ModuleType:
    path = REPO_ROOT / "scripts" / "inventory_dsh_web.py"
    spec = importlib.util.spec_from_file_location("inventory_dsh_web", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_inventory_document_is_committed_and_complete():
    text = DOC_PATH.read_text(encoding="utf-8")
    for section in [
        "## 0. 传输总览",
        "## 1. 页面配置",
        "## 2. WebSocket",
        "## 3. HTTP",
        "## 4. 精确 Fetch 路由",
        "## 5. 版本与再生成",
    ]:
        assert section in text
    assert "`$events`" in text
    assert "`/api/remote.mux`" in text
    assert "`session/follow`" in text


def test_inventory_check_passes_for_the_local_checkout():
    root = _dsh_root()
    if root is None:
        pytest.skip("no local deepseek-harness checkout to scan")
    module = _load_module()
    assert module.main(["--dsh-root", str(root), "--check"]) == 0


def test_inventory_lists_the_expected_surface():
    root = _dsh_root()
    if root is None:
        pytest.skip("no local deepseek-harness checkout to scan")
    module = _load_module()
    unary, streams = module.collect_endpoints(root)
    assert len(unary) >= 100
    paths = {row.path for row in streams}
    assert {"session/follow", "session/control", "workspace/follow"} <= paths
    routes = {route for route, _verbs, _source in module.collect_fetch_routes(root)}
    assert {"/api/file", "/api/session.export", "/api/session/uploadFileBinary"} <= routes
    events = dict(module.collect_forwarded_events(root))
    assert events["approval/request"] == "waterfall"
    assert len(module.collect_injection_contributors(root)) >= 3


def test_inventory_rejects_a_non_checkout(tmp_path: Path, capsys):
    module = _load_module()
    assert module.main(["--dsh-root", str(tmp_path)]) == 2
    assert "not a dsh checkout" in capsys.readouterr().err
