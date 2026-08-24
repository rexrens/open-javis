import json
from runtime.context import Context
from abstractions import BaseToolRegistry, ToolCall


class SimpleToolRegistry(BaseToolRegistry):
    def __init__(self):
        self._tools: dict[str, callable] = {}
        self._defs: list[dict] = []

    def register_tool(self, name: str, func):
        self._tools[name] = func
        self._defs.append({
            "type": "function",
            "function": {"name": name, "description": func.__doc__ or ""}
        })

    async def execute(self, tool_call: ToolCall) -> str:
        if tool_call.name not in self._tools:
            return f"error: tool {tool_call.name} not found"
        try:
            args = tool_call.arguments
            result = self._tools[tool_call.name](**args)
            return str(result)
        except Exception as e:
            return f"error: {str(e)}"

    def get_tool_definitions(self) -> list[dict]:
        return self._defs.copy()


inject = []
provides = [BaseToolRegistry]


def apply(ctx: Context):
    registry = SimpleToolRegistry()
    # 内置一个演示工具
    def add(a: int, b: int) -> int:
        """两数相加"""
        return a + b
    registry.register_tool("add", add)

    ctx.provide(BaseToolRegistry, registry)
    print("✅ SimpleToolRegistry 服务已注册")

    def dispose():
        print("❌ SimpleToolRegistry 服务卸载")
    return dispose
