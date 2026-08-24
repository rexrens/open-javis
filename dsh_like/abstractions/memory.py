from abc import ABC, abstractmethod
from typing import List
from abstractions.message import Message


class BaseMemory(ABC):
    """记忆存储抽象契约：所有实现必须严格遵守此接口"""

    @abstractmethod
    async def get_messages(self, session_id: str) -> List[Message]:
        ...

    @abstractmethod
    async def append_message(self, session_id: str, msg: Message) -> None:
        ...

    @abstractmethod
    async def clear(self, session_id: str) -> None:
        ...

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        ...
