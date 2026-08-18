"""Token usage tracking."""

from __future__ import annotations

from pydantic import BaseModel


class UsageSnapshot(BaseModel):
    """Token usage returned by the agent for one turn."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


__all__ = ["UsageSnapshot"]
