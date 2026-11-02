"""cordis_harness 示例的端到端测试：分层组合 + 离线一轮对话。

与 tests/test_cordis_harness/（单元与集成，107 个）互补：这里只验证
「一个使用者照着 README 敲命令能不能跑起来」——`cli.py --dump-config` 能看到
base + patch 两层，直接启动能跑通一轮离线对话，`--no-patches` 会退回真实模型路径。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "cordis_harness"


def run_example(args: list[str], *, stdin: str = "", home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(EXAMPLE_DIR)
    env["HOME"] = str(home)  # keep the real ~/.javis/cordis-harness out of it
    env.pop("OPENAI_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        cwd=EXAMPLE_DIR,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_dump_config_shows_the_base_and_project_layers(tmp_path):
    result = run_example(["--dump-config"], home=tmp_path / "home")
    assert result.returncode == 0, result.stderr
    assert "# cdh composition" in result.stdout
    assert f"# base:    {EXAMPLE_DIR / 'cordis.yml'}" in result.stdout
    assert f"# patch:   {EXAMPLE_DIR / 'cordis.patch.yml'}" in result.stdout
    # 示例 patch 的两件事：离线 provider + 自定义工具
    assert "- id: echo-provider" in result.stdout
    assert "- id: clock" in result.stdout
    assert str(EXAMPLE_DIR / "plugins" / "clock.py") in result.stdout


def test_one_offline_turn_and_tool_listing(tmp_path):
    result = run_example(
        ["--home", str(tmp_path / "home"), "--session-dir", str(tmp_path / "sessions"), "--yolo"],
        stdin="你好\n/tools\n/exit\n",
        home=tmp_path / "home",
    )
    assert result.returncode == 0, result.stderr
    assert "[echo] 收到 2 条消息；你最后说：你好" in result.stdout
    assert "clock (no approval)" in result.stdout
    assert len(list((tmp_path / "sessions").glob("*.jsonl"))) == 1


def test_no_patches_falls_back_to_the_real_provider(tmp_path):
    """分层是真实生效的：去掉 patch 层就回到 cordis.yml 的 OpenAI 路由。"""
    result = run_example(
        ["--no-patches", "--home", str(tmp_path / "home"), "--session-dir", str(tmp_path / "sessions")],
        stdin="你好\n/exit\n",
        home=tmp_path / "home",
    )
    assert result.returncode == 0, result.stderr
    assert "[error] MISSING_CREDENTIAL" in result.stdout
    assert "openai/deepseek-v4-flash" in result.stdout


def test_plugin_row_can_be_disabled_by_a_patch(tmp_path):
    """`--patch` 覆盖示例自带 patch 里的 clock 行，把它关掉。"""
    overlay = tmp_path / "overlay.yml"
    overlay.write_text(json.dumps([{"id": "clock", "disabled": True}]), encoding="utf-8")
    result = run_example(
        [
            "--patch",
            str(overlay),
            "--home",
            str(tmp_path / "home"),
            "--session-dir",
            str(tmp_path / "sessions"),
            "--yolo",
        ],
        stdin="你好\n/tools\n/exit\n",
        home=tmp_path / "home",
    )
    assert result.returncode == 0, result.stderr
    assert "clock (no approval)" not in result.stdout
