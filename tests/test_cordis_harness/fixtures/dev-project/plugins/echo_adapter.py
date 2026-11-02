"""Project-local plugin: a model adapter that needs no network and no key."""

from pydantic import BaseModel

from harness.llm import LlmAdapter
from harness.types import block_end, block_start, finish_chunk, message_text, text_delta

name = "echo-provider"
inject = ["llm"]

PROVIDER = "echo"


class Config(BaseModel):
    prefix: str = "[echo]"


class EchoAdapter(LlmAdapter):
    def __init__(self, config: Config):
        self.config = config

    def list_models(self, provider: str) -> list[str]:
        return ["echo-1"]

    async def stream(self, options):
        last_user = next(
            (message for message in reversed(options.messages) if message.get("role") == "user"),
            None,
        )
        reply = f"{self.config.prefix} {message_text(last_user) if last_user else ''}"
        yield block_start(0, "text")
        yield text_delta(0, reply)
        yield block_end(0, {"type": "text", "text": reply})
        yield finish_chunk("stop")


def apply(ctx, config: Config):
    return ctx.get("llm").register_adapter([PROVIDER], EchoAdapter(config))
