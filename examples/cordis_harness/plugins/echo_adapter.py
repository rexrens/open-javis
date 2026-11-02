"""示例插件：一个不需要网络、不需要 API key 的模型适配器。

适配器是 harness 与厂商协议之间唯一的翻译层：实现 ``LlmAdapter.stream()``、
按流式词表吐 chunk、以 ``finish`` 收尾即可。真实厂商实现见
``harness/llm_openai.py``（OpenAI 兼容 + SSE + 错误码映射）。
"""

from pydantic import BaseModel

from harness.llm import LlmAdapter
from harness.types import block_end, block_start, finish_chunk, message_text, text_delta, usage_chunk

name = "echo-provider"

#: ``ctx.llm`` 必须先存在，适配器才有地方注册。
inject = ["llm"]


class Config(BaseModel):
    prefix: str = "[echo]"


class EchoAdapter(LlmAdapter):
    """把最后一条用户消息回显给调用方。"""

    def __init__(self, config: Config):
        self.config = config

    def list_models(self, provider: str) -> list[str]:
        return ["echo-1"]

    async def stream(self, options):
        last_user = next(
            (message for message in reversed(options.messages) if message.get("role") == "user"),
            None,
        )
        heard = message_text(last_user) if last_user is not None else ""
        reply = f"{self.config.prefix} 收到 {len(options.messages)} 条消息；你最后说：{heard}"

        # 流式词表：block-start → delta… → block-end，最后必须以 finish 收尾。
        yield block_start(0, "text")
        yield text_delta(0, reply)
        yield block_end(0, {"type": "text", "text": reply})
        yield usage_chunk({"inputTokens": 0, "outputTokens": len(reply)})
        yield finish_chunk("stop")


def apply(ctx, config: Config):
    # 返回值是 disposer：插件卸载时这条路由一起注销。
    return ctx.get("llm").register_adapter(["echo"], EchoAdapter(config))
