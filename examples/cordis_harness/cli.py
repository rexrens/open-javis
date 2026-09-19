#!/usr/bin/env python
"""cordis_harness 的 standalone 驱动（无 javis 宿主，仅 javis.cordis）。

    cd examples/cordis_harness
    uv run python cli.py                    # 交互 REPL（离线 echo 模型 + clock 工具）
    uv run python cli.py --dump-config      # 看分层合并结果
    uv run python cli.py --list-sessions
    uv run python cli.py --no-patches       # 整层回到 base：真实 provider，无 clock

启动目录决定组合：base 是当前目录的 ``./cordis.yml``，patch 是当前目录的
``./cordis.patch.yml``。在仓库根目录直接跑 ``examples/cordis_harness/cli.py``
也能起来，但拿不到示例那层 patch。

这只是 ``harness/cli.py`` 的一层包装：把本目录放进 ``sys.path``，让组合文件里的
``harness.plugins.*`` 能按名字解析，然后把参数原样转给 ``harness.cli.main``。
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from harness.cli import main  # noqa: E402
except ModuleNotFoundError as error:  # pragma: no cover - environment hint
    raise SystemExit(
        f"{error}\n\n本示例依赖仓库环境，请用 uv 运行：\n"
        "    uv run python examples/cordis_harness/cli.py\n"
    ) from error

if __name__ == "__main__":
    raise SystemExit(main())
