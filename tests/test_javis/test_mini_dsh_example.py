"""mini_dsh 示例的端到端测试：cli.py 的 demo 场景全绿。

与 test_mini_dsh/test_composition.py（组合级）互补：这里跑 cli.py 的
``run_demo``，断言 7 场景全过 + 退出码语义（每场景内部带 2–4 条语义断言）。
cli 模块经 importlib 以独立名字加载（mini_dsh_cli），自足 sys.path——
本文件位于 tests/test_javis/ 下，不享受 test_mini_dsh 的 conftest。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MINI_ROOT = Path(__file__).resolve().parents[2] / "examples" / "mini_dsh"
if str(MINI_ROOT) not in sys.path:
    sys.path.insert(0, str(MINI_ROOT))


def _load_cli() -> object:
    spec = importlib.util.spec_from_file_location("mini_dsh_cli", MINI_ROOT / "cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["mini_dsh_cli"] = module
    spec.loader.exec_module(module)
    return module


def test_demo_all_scenarios_ok():
    cli = _load_cli()
    # 7 场景全部通过（exit code 0）——由 cli.run_demo 内部断言
    assert cli.run_demo() == 0
