from typing import (
    Any,
    Literal,
)

from pydantic import (
    BaseModel,
    Field,
)

from .proto import STRICT, ContentBlock, TextBlock

Role = Literal["system", "user", "assistant", "tool"]


class Message(BaseModel):
    """A message in a model request"""

    model_config = STRICT
    role: Role
    content: list[ContentBlock] = Field(default_factory=list)

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role="user", content=[TextBlock(text=text)])

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls(role="assistant", content=[TextBlock(text=text)])

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls(role="system", content=[TextBlock(text=text)])

    @property
    def text(self) -> str:
        """The full text content of the message"""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))


class JsonSchemaFormat(BaseModel):
    """A JSON schema for a tool call"""

    model_config = STRICT

    name: str
    json_schema: dict[str, Any]


class ModelRequest(BaseModel):
    """Request to the model provider"""

    model_config = STRICT

    message: list[Message] = Field(min_length=1)

    system: str | None = None

    temperature: float | int | None = Field(default=0.0, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    stop: list[str] | None = None

    # only adapter suports this can use , otherwise raise UnsupportedError
    response_format: JsonSchemaFormat | None = None

    # provider specific body
    extra: dict[str, Any] = Field(default_factory=dict)

    def all_system_text(self) -> str:
        """The full system prompt"""
        pri = "".join(m.text for m in self.message if (m.role == "system" and m.text))
        if self.system:
            return pri + self.system
        return pri

    def non_system_messages(self) -> list[Message]:
        """All messages except system prompt"""
        return [m for m in self.message if m.role != "system"]


__all__ = [
    "JsonSchemaFormat",
    "Message",
    "ModelRequest",
    "Role",
]
