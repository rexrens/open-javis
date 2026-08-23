"""工具注册表插件（仿 ``@deepseek-ai/dsh-tools``）。

dsh 的 tools 是一个注册表：插件注册「schema + execute」两步；模型请求
下发 ``snapshot()`` 生成的 schema，执行时 ``execute()`` 查找并运行。
本示例内置三个真实工具：``read_file`` / ``list_files`` / ``bash``。

注意：这是教学示例，没有 dsh 的 sandbox 层，``bash`` 直接在示例
工作区执行（cwd = harness 配置的 workspace_root）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ToolFn = Callable[..., Any]


@dataclass(frozen=True)
class Tool:
    """一个工具 = 元数据（schema）+ 执行函数（对应 dsh 的 Tool）。"""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: ToolFn


class ToolsService:
    """插件通过 ``ctx.provide("tools", ...)`` 注册的服务。"""

    def __init__(self, workspace_root: Path) -> None:
        self._tools: dict[str, Tool] = {}
        self._workspace_root = workspace_root

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name!r}")
        self._tools[tool.name] = tool

    def snapshot(self) -> list[dict[str, Any]]:
        """OpenAI function-calling schema（随请求下发，按名排序保证前缀稳定）。"""
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]
        return sorted(schemas, key=lambda s: s["function"]["name"])

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """执行一次工具调用；错误以文本返回给模型（工具错误不中断循环）。"""
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool {name!r}"
        try:
            result = tool.fn(**arguments)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        except Exception as exc:  # noqa: BLE001 — 工具错误是给模型看的结果
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# 内置工具实现（dsh 中它们分散在 fs / shell 等插件族，这里合并演示）
# ---------------------------------------------------------------------------


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def read_file(root: Path, file_path: str, offset: int = 1, limit: int = 2000) -> str:
    """读取文件并带行号输出（与 javis 内建 read 工具同款行为）。"""
    path = _resolve(root, file_path)
    if not path.exists():
        return f"Error: {file_path} not found"
    if not path.is_file():
        return f"Error: {file_path} is not a file"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(0, offset - 1)
    chunk = lines[start : start + limit]
    numbered = [f"{start + i + 1}\t{line}" for i, line in enumerate(chunk)]
    result = "\n".join(numbered)
    if total > start + limit:
        result += f"\n... ({total} lines total, showing {start + 1}-{start + len(chunk)})"
    return result or "(empty file)"


def list_files(root: Path, max_entries: int = 50) -> str:
    """列出工作区文件（跳过 .git/.venv/__pycache__ 等目录）。"""
    skip = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for filename in sorted(filenames):
            rel = Path(dirpath).relative_to(root) / filename
            entries.append(str(rel))
            if len(entries) >= max_entries:
                return "\n".join(entries) + f"\n... ({len(entries)} shown, truncated)"
    return "\n".join(entries) or "(empty directory)"


async def bash(root: Path, command: str, timeout: float = 30.0) -> str:
    """在示例工作区执行命令，返回 stdout+stderr（截断到 20000 字符）。"""
    # 让子进程里的 `python` 解析到运行本示例的 venv（sys.prefix 的 bin
    # 目录），而不是 PATH 上随机的系统 python。用 sys.prefix 而非
    # sys.executable 的 resolve()，因为 venv 里的 python 可能是软链。
    env = dict(os.environ)
    interpreter_dir = str(Path(sys.prefix) / "bin")
    env["PATH"] = interpreter_dir + os.pathsep + env.get("PATH", "")
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"Error: command timed out after {timeout}s"
    output = (stdout + stderr).decode(errors="replace").strip()
    if proc.returncode != 0:
        output += f"\n[exit code: {proc.returncode}]"
    return output[-20000:] if len(output) > 20000 else output or "(no output)"


def apply(ctx: Any, config: Any) -> Any:
    """激活入口：注册工具服务并登记三个真实工具。"""
    root = Path(ctx.javis_config["workspace_root"]).resolve()
    service = ToolsService(workspace_root=root)
    service.register(
        Tool(
            name="read_file",
            description=(
                "Read a file's contents with line numbers. "
                "Always read a file before summarizing or editing it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file"},
                    "offset": {"type": "integer", "description": "Start line (1-based)"},
                    "limit": {"type": "integer", "description": "Max lines to read"},
                },
                "required": ["file_path"],
            },
            fn=lambda **kw: read_file(root, **kw),
        )
    )
    service.register(
        Tool(
            name="list_files",
            description="List files in the workspace (skips .git/.venv/__pycache__).",
            parameters={
                "type": "object",
                "properties": {
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum entries to return",
                    }
                },
            },
            fn=lambda **kw: list_files(root, **kw),
        )
    )
    service.register(
        Tool(
            name="bash",
            description=(
                "Run a shell command in the demo workspace and return its output. "
                "Demo only: no sandbox."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {"type": "number", "description": "Timeout in seconds"},
                },
                "required": ["command"],
            },
            fn=lambda **kw: bash(root, **kw),
        )
    )
    ctx.provide("tools", service)
