from dataclasses import dataclass, field
from typing import List
from abstractions.message import Message, ToolCall


@dataclass
class Session:
    session_id: str
    messages: List[Message] = field(default_factory=list)
    tools: List[dict] = field(default_factory=list)

    def add_message(self, msg: Message):
        self.messages.append(msg)

    def append_tool_result(self, tool_call: ToolCall, result: str):
        self.messages.append(Message(
            role="tool",
            tool_call_id=tool_call.id,
            content=result
        ))
