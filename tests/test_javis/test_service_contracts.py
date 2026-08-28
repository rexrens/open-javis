"""Stage 1: typed service contracts for the plugin system.

Asserts the contract shape — stable service-name constants plus registry
types that validate through ``ctx.get(name, Type)`` — which the host will
provide as built-in services and plugins will consume.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from javis.commands.registry import Command, CommandRegistry
from javis.contracts import (
    COMMANDS_SERVICE,
    CONFIG_SERVICE,
    ENGINE_SERVICE,
    LLM_SERVICE,
    TOOLS_SERVICE,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ToolCall,
)
from javis.engines.corecoder.llm import (
    LLMProvider as EngineLLMProvider,
)
from javis.engines.corecoder.llm import (
    LLMRequest as EngineLLMRequest,
)
from javis.engines.corecoder.llm import (
    LLMResponse as EngineLLMResponse,
)
from javis.engines.corecoder.llm import (
    ToolCall as EngineToolCall,
)
from javis.engines.corecoder.tools import TOOL_REGISTRY, ToolRegistry
from javis.engines.corecoder.tools.base import Tool
from javis.plugins.context import PluginContext, ServiceRegistry


class ContractTool(Tool):
    name = "contract_tool"
    description = "registered through the tools service"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return "ok"


class EchoProvider(LLMProvider):
    """Minimal plugin-style provider: implements only the abstract method."""

    def __init__(self) -> None:
        super().__init__(model="echo")

    async def achat_stream(
        self,
        request: LLMRequest,
        *,
        extra_body=None,
        on_token=None,
        on_reasoning=None,
    ):
        del request, extra_body, on_token, on_reasoning
        yield LLMResponse(content="echo")


def test_service_name_constants():
    assert TOOLS_SERVICE == "tools"
    assert COMMANDS_SERVICE == "commands"
    assert CONFIG_SERVICE == "config"
    assert LLM_SERVICE == "llm"
    assert ENGINE_SERVICE == "engine"


def test_tools_service_is_typed_registry():
    services = ServiceRegistry()
    services.provide(TOOLS_SERVICE, TOOL_REGISTRY)
    ctx = PluginContext(name="p", config=None, services=services)
    tools = ctx.get(TOOLS_SERVICE, ToolRegistry)  # type-validated contract
    assert isinstance(tools, ToolRegistry)
    cancel = tools.register(ContractTool())
    assert tools.get("contract_tool") is not None
    cancel()
    assert tools.get("contract_tool") is None


def test_tools_service_rejects_mismatched_type():
    services = ServiceRegistry()
    services.provide(TOOLS_SERVICE, "not-a-registry")
    ctx = PluginContext(name="p", config=None, services=services)
    with pytest.raises(TypeError, match="expected"):
        ctx.get(TOOLS_SERVICE, ToolRegistry)


def test_commands_service_register_returns_disposer():
    registry = CommandRegistry()

    async def handler(args, context):
        return None  # pragma: no cover

    original = Command("status", "built-in status", handler)
    registry.register(original)
    override = Command("status", "plugin status", handler)
    cancel = registry.register(override)
    assert registry.lookup("/status")[0] is override
    cancel()
    assert registry.lookup("/status")[0] is original


def test_llm_contract_is_canonical_type():
    """The contract in contracts is the same type the engine uses."""
    assert LLMProvider is EngineLLMProvider
    assert LLMRequest is EngineLLMRequest
    assert LLMResponse is EngineLLMResponse
    assert ToolCall is EngineToolCall


def test_llm_provider_contract_is_single_abstract_method():
    """Stability guarantee from spec D1: implement one method, get the rest."""
    assert set(LLMProvider.__abstractmethods__) == {"achat_stream"}


def test_llm_provider_usable_as_typed_service():
    """A plugin provider validates through ctx.get(name, LLMProvider)."""
    services = ServiceRegistry()
    provider = EchoProvider()
    services.provide(LLM_SERVICE, provider)
    ctx = PluginContext(name="p", config=None, services=services)
    got = ctx.get(LLM_SERVICE, LLMProvider)
    assert got is provider
