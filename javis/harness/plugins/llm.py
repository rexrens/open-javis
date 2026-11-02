"""组合行：``llm`` 服务 —— provider adapter 注册与路由解析。

Config resolution (provider / model / api-key / max-tokens) lives here now:
the row reads the ``config`` service and the ``host`` per-session facts
(CLI ``--model`` override included) and registers an ``OpenAICompatAdapter``
under the resolved provider route.
"""

from __future__ import annotations

from typing import Any

from javis.contracts.services import CONFIG_SERVICE, HOST_SERVICE
from javis.llm import LlmRuntime, OpenAICompatAdapter
from javis.session.config import resolve_provider_and_model
from javis.session.credentials import resolve_api_key

name = "javis.harness.plugins.llm"
inject = [CONFIG_SERVICE, HOST_SERVICE]


def build_runtime(ctx: Any) -> LlmRuntime:
    """Resolve the route and register the adapter (``llm`` service)."""
    cfg = ctx.get(CONFIG_SERVICE)
    host = ctx.get(HOST_SERVICE)
    provider_name, model_id = resolve_provider_and_model(cfg, cli_model=host.model_override)
    provider_cfg = cfg.providers[provider_name]
    api_key = resolve_api_key(
        provider_name,
        provider_cfg.api_key_env,
        provider_cfg.api_key,
        workspace=host.workspace,
        cwd=host.cwd,
    )
    max_tokens = next(
        (m.max_tokens for m in provider_cfg.models if m.id == model_id),
        None,
    )
    adapter_kwargs: dict[str, Any] = {
        "model": model_id,
        "api_key": api_key or "",
        "base_url": provider_cfg.base_url,
    }
    if max_tokens is not None:
        adapter_kwargs["max_tokens"] = max_tokens
    # LlmRuntime's constructor auto-provides the "llm" service (Service base).
    runtime = LlmRuntime(ctx)
    runtime.register_adapter([provider_name], OpenAICompatAdapter(**adapter_kwargs))
    return runtime


def apply(ctx):
    build_runtime(ctx)
