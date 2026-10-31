from __future__ import annotations

from pydantic import BaseModel, Field

from .proto import (
    STRICT,
    ContentBlock,
    FinishReason,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
)


class ModelResponse(BaseModel):
    """Response from the model provider"""

    model_config = STRICT

    blocks: list[ContentBlock] = Field(default_factory=list)

    finish_reason: FinishReason

    @property
    def text(self) -> str:
        return "".join(t.text for t in self.blocks if isinstance(t, TextBlock))

    @property
    def reasoning(self) -> str:
        return "".join(t.text for t in self.blocks if isinstance(t, ReasoningBlock))

    @property
    def tool_calls(self) -> list[ToolCallBlock]:
        return [t for t in self.blocks if isinstance(t, ToolCallBlock)]
