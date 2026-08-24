from abc import ABC, abstractmethod
from abstractions.message import ToolCall


class BaseToolRegistry(ABC):
    """工具注册表抽象契约"""

    @abstractmethod
    def register_tool(self, name: str, func) -> None:
        ...

    @abstractmethod
    async def execute(self, tool_call: ToolCall) -> str:
        ...

    @abstractmethod
    def get_tool_definitions(self) -> list[dict]:
        ...
