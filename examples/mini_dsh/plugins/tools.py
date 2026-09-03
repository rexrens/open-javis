"""插件：provide "tools" —— demo 工具集（now/weather 并行、set_note/big_read 独占）。"""
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.tools import Tool, ToolRegistry

_WEATHER = {"Paris": "18°C, light rain", "Tokyo": "24°C, sunny"}


def _now(_input: Any) -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _weather(exec_input: Any) -> str:
    city = str((exec_input.arguments or {}).get("city", ""))
    return _WEATHER.get(city, f"{city}: 20°C, cloudy")


def _set_note(exec_input: Any) -> str:
    text = str((exec_input.arguments or {}).get("text", ""))
    notes = Path(exec_input.agent.session.header.cwd or ".") / "notes.txt"
    notes.parent.mkdir(parents=True, exist_ok=True)
    with notes.open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip() + "\n")
    return f"note saved: {text[:40]}"


def _big_read(_input: Any) -> str:
    return "x" * 50_000  # compaction 场景：超大工具结果


def apply(ctx) -> None:
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)
    registry.register(Tool(name="now", description="Current UTC time", body=_now, mode="parallel"))
    registry.register(
        Tool(
            name="weather",
            description="Weather for a city (Paris / Tokyo / ...)",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            body=_weather,
            mode="parallel",
        )
    )
    registry.register(
        Tool(
            name="set_note",
            description="Append a line to notes.txt in the session cwd",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            body=_set_note,
            mode="exclusive",
        )
    )
    registry.register(Tool(name="big_read", description="Read a big file (demo)", body=_big_read, mode="exclusive"))
