"""Base class for all tools."""

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class Tool(ABC):
    """Minimal tool interface. Subclass this to add new capabilities."""

    name: str
    description: str
    parameters: ClassVar[dict[str, Any]]  # JSON Schema for the function args

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Run the tool and return a text result."""
        ...

    def schema(self) -> dict[str, Any]:
        """OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    # -- concurrency metadata (WIP: no consumer yet) ----------------------

    @property
    def read_only(self) -> bool:
        """Whether this tool is side-effect free and safe to parallelize."""
        return False

    @property
    def exclusive(self) -> bool:
        """Whether this tool should run alone even if concurrency is enabled."""
        return False

    @property
    def concurrency_safe(self) -> bool:
        """Whether this tool can run alongside other concurrency-safe tools."""
        return self.read_only and not self.exclusive
