"""Skill registry + filesystem provider（dsh skill 能力的轻量版）。

dsh：``packages/skill/skill``（``ctx.skills`` 注册表服务）+
``packages/skill/skill-filesystem``（SKILL.md 目录包 provider）。mini 版：

- 无 rank 优先级/scope 分层/watch——第一个命中 name 的 provider 胜出；
- frontmatter 只认 ``name``（缺省用目录名）与 ``description``；
- 目录包形态：``<root>/<skill-name>/SKILL.md``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_skill_name(name: str) -> bool:
    """kebab-case 技能名（dsh 同款文法）。"""
    return bool(_SKILL_NAME.fullmatch(name))


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    source: str = "filesystem"
    provider: str = "local"


@dataclass(frozen=True)
class SkillDefinition(SkillSummary):
    content: str = ""
    path: str | None = None


class SkillProvider(Protocol):
    name: str

    def list(self) -> list[SkillSummary]: ...

    def get(self, name: str) -> SkillDefinition | None: ...


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """YAML frontmatter（``--- ... ---``）→ (meta, body)。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), parts[2].strip()


class FileSkillProvider:
    """扫描 ``<root>/<name>/SKILL.md`` 目录包（dsh directory-bundle 形态）。"""

    def __init__(self, root: str | Path, name: str = "local") -> None:
        self.root = Path(root).expanduser().resolve()
        self.name = name

    def list(self) -> list[SkillSummary]:
        out: list[SkillSummary] = []
        if not self.root.is_dir():
            return out
        for entry in sorted(self.root.iterdir()):
            skill_file = entry / "SKILL.md"
            if not entry.is_dir() or not skill_file.is_file():
                continue
            meta, _ = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            skill_name = str(meta.get("name") or entry.name)
            if not is_skill_name(skill_name):
                continue
            out.append(
                SkillSummary(
                    name=skill_name,
                    description=str(meta.get("description") or ""),
                    source="filesystem",
                    provider=self.name,
                )
            )
        return out

    def get(self, name: str) -> SkillDefinition | None:
        if not is_skill_name(name):
            return None
        skill_file = self.root / name / "SKILL.md"
        if not skill_file.is_file():
            return None
        meta, content = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        return SkillDefinition(
            name=str(meta.get("name") or name),
            description=str(meta.get("description") or ""),
            source="filesystem",
            provider=self.name,
            content=content,
            path=str(skill_file),
        )


class SkillRegistry:
    """The ``"skills"`` service：合并 provider 目录 + runtime 贡献（dsh ``ctx.skills``）。"""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._providers: list[SkillProvider] = []
        self._runtime: dict[str, SkillDefinition] = {}

    def register_provider(self, provider: SkillProvider) -> None:
        self._providers.append(provider)

    def register_skill(self, definition: SkillDefinition) -> None:
        """Runtime 贡献（dsh ``ctx.skills.register``），优先于 provider。"""
        self._runtime[definition.name] = definition

    def list(self) -> list[SkillSummary]:
        seen: dict[str, SkillSummary] = {}
        for provider in self._providers:
            for summary in provider.list():
                seen.setdefault(summary.name, summary)
        for name, definition in self._runtime.items():
            seen[name] = SkillSummary(
                name=name,
                description=definition.description,
                source="runtime",
                provider=definition.provider,
            )
        return list(seen.values())

    def get(self, name: str) -> SkillDefinition | None:
        if name in self._runtime:
            return self._runtime[name]
        for provider in self._providers:
            definition = provider.get(name)
            if definition is not None:
                return definition
        return None
