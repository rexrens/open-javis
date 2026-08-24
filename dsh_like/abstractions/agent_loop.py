from abc import ABC, abstractmethod
from abstractions.session import Session


class BaseAgentLoop(ABC):
    """Agent 循环抽象契约"""

    @abstractmethod
    async def run(self, session: Session) -> None:
        """运行一轮 Agent 会话，直到任务结束"""
        ...
