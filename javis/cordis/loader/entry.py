"""Loader entry model: one row of a ``cordis.yml`` composition."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class Entry(BaseModel):
    """A Cordis configuration entry (mirrors ``EntryOptions`` from the loader).

    Fields:

    - ``id`` — stable identity so the loader can tell "modify" from
      "remove + add"; entries without an id get a fresh generated id on every
      read and are therefore always re-mounted on change.
    - ``name`` — module specifier (relative path or dotted package name).
    - ``config`` — plugin config, validated against the module's ``Config``.
    - ``disabled`` — keep the entry but skip mounting it.
    - ``inject`` — services the plugin requires (merged over module metadata).
    - ``provide`` — service name(s) the plugin provides (informational).
    - ``group`` — a nested entry list loaded/unloaded as one unit.
    - ``isolate`` — give ``name`` an independent service scope for this group.
    """

    id: str | None = None
    name: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    disabled: bool = False
    inject: list[str] | dict[str, Any] | None = None
    provide: str | list[str] | None = None
    group: list["Entry"] | None = None
    isolate: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name_not_null(cls, v: Any) -> Any:
        return v or ""

    def effective_id(self, fallback: int) -> str:
        return self.id if self.id else f"anon-{fallback}"


def parse_entries(data: Any) -> list[Entry]:
    """Parse a parsed-YAML composition into entries.

    Accepts a list of entries, or ``{"insert": [...]}`` patch-style payloads
    (the ``insert`` key is unwrapped; patch addressing by id is a
    DSH-specific loader extension not implemented here).
    """
    if isinstance(data, dict) and "insert" in data:
        data = data["insert"]
    if not isinstance(data, list):
        raise TypeError("cordis.yml must be a list of entries")
    return [Entry.model_validate(item) for item in data]
