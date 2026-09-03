"""插件：provide "llm" —— 从 providers.py 选 adapter（scripted/离线 | openai/真实）。"""
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from providers import OpenAICompatAdapter, ScriptedAdapter, scenario_script


class Config(BaseModel):
    provider: str = "scripted"  # scripted | openai | auto


def _resolve(config: Config, scenario: str | None) -> Any:
    # 优先级：显式环境变量 > 插件 config > 默认 scripted。
    # （env 优先才能让 cli 的 --prompt 用 MINI_DSH_PROVIDER=auto 切真实模型，
    #  同时 cordis.yml 的 provider: scripted 保持 demo 默认确定性）
    choice = (
        os.environ.get("MINI_DSH_PROVIDER")
        or config.provider
        or "scripted"
    ).lower()
    if choice == "scripted":
        return ScriptedAdapter(scenario_script(scenario or "text"), model="mini-scripted")
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if choice == "openai" or (choice == "auto" and api_key):
        return OpenAICompatAdapter(
            model=os.environ.get("MINI_DSH_MODEL", "deepseek-chat"),
            api_key=api_key or "",
            base_url=os.environ.get("MINI_DSH_BASE_URL"),
        )
    return ScriptedAdapter(scenario_script(scenario or "text"), model="mini-scripted")


def apply(ctx, config: Config) -> None:
    scenario = os.environ.get("HARNESS_DEMO_SCENARIO")
    ctx.provide("llm", _resolve(config, scenario))
