from abc import ABC, abstractmethod
from typing import List, AsyncGenerator
from abstractions.message import Message


class BaseLLM(ABC):
    """大模型抽象契约"""

    @abstractmethod
    async def stream(self, messages: List[Message], tools: List[dict] | None = None) -> AsyncGenerator[Message, None]:
        """流式生成，逐块返回Message增量"""
        ...

    @abstractmethod
    async def chat(self, messages: List[Message], tools: List[dict] | None = None) -> Message:
        """非流式生成，返回完整消息"""
        ...
