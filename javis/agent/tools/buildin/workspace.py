from __future__ import annotations

from dataclasses import dataclass
import difflib
from enum import Enum
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Tuple
from pydantic import Field

from javis.agent.tools.base import ToolCallResult
from javis.agent.tools.function import tool
from javis.agent.tools.toolset import ToolSet



class PathError(str, Enum):
    """路径层面的真正错误。文件不存在不在此列——它是中性信息。"""
    NOT_WHITELISTED = "not_whitelisted"
    RESOLVE_FAILED = "resolve_failed"
    EMPTY_WHITELIST = "empty_whitelist"


@dataclass(frozen=True)
class ResolveResult:
    error: PathError | None     # None = 白名单通过、解析成功
    path: Path
    exists: bool = False        # 仅当 error is None 时有意义
    is_dir: bool = False
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None



class WorkspaceToolSet(ToolSet):

    _ANSI_GREEN = "\033[32m"
    _ANSI_RED = "\033[31m"
    _ANSI_RESET = "\033[0m"

    name = "workspace"
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        include: Iterable[str] | str | None = None,
        exclude: Iterable[str] | str | None = None,
    ) -> None:
        self.root = Path.cwd().resolve() if root is None else Path(root).expanduser().resolve()
        super().__init__(include=include, exclude=exclude)

    def _resolve_whitelisted_path(
        self,
        candidate: str,
        whitelist: Iterable[str | Path],
        *,
        base: Path | None = None,
    ) -> ResolveResult:
        raw = Path(candidate).expanduser()
        if base is not None and not raw.is_absolute():
            raw = base / raw

        try:
            target = raw.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            return ResolveResult(PathError.RESOLVE_FAILED, Path(candidate), message=str(e))

        roots: list[Path] = []
        for w in whitelist:
            try:
                roots.append(Path(w).expanduser().resolve(strict=False))
            except (OSError, RuntimeError):
                continue
        if not roots:
            return ResolveResult(PathError.EMPTY_WHITELIST, Path(candidate))

        if not any(self._is_within(target, r) for r in roots):
            return ResolveResult(PathError.NOT_WHITELISTED, Path(candidate),
                                message=f"not under any of {[str(r) for r in roots]}")

        # 存在性作为信息返回，不判为错误
        try:
            exists = target.exists()
            is_dir = target.is_dir() if exists else False
        except OSError:
            exists, is_dir = False, False

        return ResolveResult(None, target, exists=exists, is_dir=is_dir)


    def _is_within(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @tool
    async def read_file(self, path: str, encoding: str= "utf-8") -> ToolCallResult:
        """Read a UTF-8 text file from the local workspace."""

        r = self._resolve_whitelisted_path(path, [self.root], base=self.root)
        if r.error:
            return ToolCallResult(output=f"Error: {r.error.value}" + (f" ({r.message})" if r.message else ""), is_error=True)
        if not r.exists:                    # read 场景：不存在 = 失败
            return ToolCallResult(output=f"File not found: {path}", is_error=True)
        if r.is_dir:
            return ToolCallResult(output=f"File not found: {path}", is_error=True)
        return ToolCallResult(r.path.read_text(encoding=encoding))

    @tool
    async def write_file(self, path: str, content: str) -> ToolCallResult:
        """Write UTF-8 text to a file in the workspace."""

        r = self._resolve_whitelisted_path(path, [self.root], base=self.root)
        if r.error:
            return ToolCallResult(output=f"错误: {r.error.value}" + (f" ({r.message})" if r.message else ""), is_error=True)

        if r.is_dir:                        # write 场景：不存在 = 正常（新建）
            return ToolCallResult(output=f"target is idr: {r.path}", is_error=True)

        r.path.parent.mkdir(parents=True, exist_ok=True)
        r.path.write_text(content, encoding="utf-8")
        return ToolCallResult(output=f"Wrote {len(content)} characters to {r.path.relative_to(self.root)}")

    @tool
    async def delete_file(self, path: str) -> ToolCallResult:
        """Delete a file from the workspace."""

        r = self._resolve_whitelisted_path(path, [self.root], base=self.root)
        if r.error:
            return ToolCallResult(output=r.message, is_error=True)

        if not r.path.exists():
            return ToolCallResult(output=f"File not found: {path}", is_error=True)
        if r.path.is_dir():
            return ToolCallResult(output=f"Cannot delete directory: {path}", is_error=True)

        try:
            r.path.unlink()
            return ToolCallResult(output=f"Deleted {r.path.relative_to(self.root)}")
        except Exception as e:
            return ToolCallResult(output=f"Error deleting file: {e}", is_error=True)


    def _compute_diff(self, filename: str, original: str, updated: str) -> tuple[str, int, int]:
        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=filename,
                tofile=filename,
                lineterm="",
            )
        )
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        return "".join(diff_lines), added, removed

    @tool
    async def edit_file(self,
        path: str,
        old_str: str = Field(description="Existing text to replace"),
        new_str: str = Field(description="Replacement text"),
        replace_all: bool = Field(default=False)
    ) -> ToolCallResult:
        """Edit a file in the workspace."""
        if not old_str:
            return ToolCallResult(output="old_str must not be empty", is_error=True)

        r = self._resolve_whitelisted_path(path, [self.root], base=self.root)
        # err, target = self._resolve_path(path)
        if r.error:
            return ToolCallResult(output=r.message, is_error=True)

        if not r.path.exists():
            return ToolCallResult(output=f"File not found: {path}", is_error=True)

        original = r.path.read_text(encoding="utf-8")
        count = original.count(old_str)
        if count == 0:
            return ToolCallResult(output=f"No occurrences of '{old_str}' found in {path}", is_error=True)

        if count > 1 and not replace_all:
            return ToolCallResult(output=f"Multiple occurrences of '{old_str}' found in {path}. Use replace_all=True to replace all occurrences.", is_error=True)

        updated = original.replace(old_str, new_str) if replace_all else original.replace(old_str, new_str, 1)
        tmp_file = r.path.with_suffix(r.path.suffix + ".tmp")
        diff_text, added, removed = self._compute_diff(str(path), original, updated)
        stats = f"  ({self._ANSI_GREEN}+{added}{self._ANSI_RESET} {self._ANSI_RED}-{removed}{self._ANSI_RESET})"

        try:
            tmp_file.write_text(updated, encoding="utf-8")
            os.replace(tmp_file, r.path)
        except Exception as e:
            return ToolCallResult(output=f"Error edit file{path}: {e}", is_error=True)
        finally:
            if tmp_file.exists():
                tmp_file.unlink()
        return ToolCallResult(output=f"Updated {path}{stats}")

    @tool(run_in_thread=True)
    def run_command(
        self,
        args: list[str],
        tail: int = 100,
        timeout: int = 60,
    ) -> ToolCallResult:
        """Run a command in the workspace without invoking a shell."""
        if not args:
            return ToolCallResult(output="command args must not be empty", is_error=True)

        tail = max(0, int(tail))
        timeout = max(1, int(timeout))

        def _tail(text: str) -> str:
            if tail == 0:
                return ""
            return "\n".join(text.splitlines()[-tail:])

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                cwd=self.root,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolCallResult(
                output=f"command timed out after {timeout} seconds",
                is_error=True,
            )

        stdout = _tail(proc.stdout)
        stderr = _tail(proc.stderr)
        if proc.returncode != 0:
            detail = stderr or stdout or "<no output>"
            return ToolCallResult(
                output=f"command failed with exit code {proc.returncode}: {detail}",
                is_error=True,
            )

        output = "\n".join(part for part in (stdout, stderr) if part)
        return ToolCallResult(output=output)
