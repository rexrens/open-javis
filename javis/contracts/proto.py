"""协议层：provider 归一化后的「公共语言」。

词汇表对齐 DeepSeek Harness 的 StreamChunk
(``packages/llm/llm/src/types.ts``)，做了三处有意简化：

1. **不含工具执行**。Model 层只负责「把 provider 的输出翻译成统一块」，
   永远不执行工具。tool-call 块只保留解析结果（arguments 保持原始 JSON 字符串，
   不解析、不校验、不调用），执行是上层的事。
2. **去掉 image / file 块**。本层聚焦文本与推理，多模态输入不在本期范围。
3. **block-end 的 block 字段可选**。DSH 用它携带 adapter 私有元数据用于重放；
   这里块内容完全由 delta 累积决定，不存在第二份真源，因此允许留空。

设计约束（照 DSH 的三条不变量）：
- ``usage`` 必须在终止 ``finish`` 之前发出，之后不得再有任何 chunk；
- block index 在同一响应内单调递增，delta 复用其所属块的 index；
- provider adapter 可以抛异常，但对外暴露的流必须以 ``finish`` 终止。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 全局严格模式：禁止未知字段，防止 provider adapter 悄悄塞私货。
STRICT = ConfigDict(extra="forbid", populate_by_name=True)

BlockType = Literal["text", "reasoning", "tool-call"]
FinishKind = Literal["stop", "tool-calls", "max-tokens", "aborted", "error"]


class _Proto(BaseModel):
    """协议对象基类。"""

    model_config = STRICT


# --------------------------------------------------------------------------- #
# 内容块
# --------------------------------------------------------------------------- #
class TextBlock(_Proto):
    """正文文本块。"""

    type: Literal["text"] = "text"
    text: str = ""


class ReasoningBlock(_Proto):
    """推理 / 思维链块（provider 私有格式统一落这里）。"""

    type: Literal["reasoning"] = "reasoning"
    text: str = ""


class ToolCallBlock(_Proto):
    """模型请求的一次工具调用。

    ``arguments`` 保持 provider 产出的**原始 JSON 字符串**，不做 json.loads。
    原因：流式过程中参数是逐字符到达的半截 JSON，提前解析必然失败；
    解析时机应由真正要执行的调用方决定。
    """

    type: Literal["tool-call"] = "tool-call"
    id: str
    name: str
    arguments: str = ""


ContentBlock = Annotated[
    Union[TextBlock, ReasoningBlock, ToolCallBlock],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------- #
# 用量
# --------------------------------------------------------------------------- #
class TokenUsage(_Proto):
    """一次模型调用的 token 记账。

    **计数是不相交的（disjoint）**：``input_tokens`` 只统计未命中缓存的输入，
    命中缓存的输入单独记在 ``cache_read_tokens`` / ``cache_write_tokens``。
    计费输入 = 三者之和，见 :attr:`billed_input_tokens`。

    这条约定必须显式维护，因为各家口径不同：
    - Anthropic 的 ``input_tokens`` 天然不含缓存（直接对应本约定）；
    - OpenAI / DeepSeek 的 ``prompt_tokens`` 把缓存命中折进总数里，
      adapter 必须**减出去**，否则缓存成本会被重复计算。
    """

    input_tokens: int = Field(default=0, ge=0, description="未命中缓存的输入 token")
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0, description="命中缓存的输入 token")
    cache_write_tokens: int | None = Field(default=None, ge=0, description="写入缓存的输入 token")
    reasoning_tokens: int | None = Field(default=None, ge=0)

    @property
    def billed_input_tokens(self) -> int:
        """计费口径的输入总量（未缓存 + 读缓存 + 写缓存）。"""
        return self.input_tokens + (self.cache_read_tokens or 0) + (self.cache_write_tokens or 0)

    def merge(self, other: TokenUsage) -> TokenUsage:
        """累加两次调用的用量，返回新对象（不可变语义）。"""

        def _add(a: int | None, b: int | None) -> int | None:
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=_add(self.total_tokens, other.total_tokens),
            cache_read_tokens=_add(self.cache_read_tokens, other.cache_read_tokens),
            cache_write_tokens=_add(self.cache_write_tokens, other.cache_write_tokens),
            reasoning_tokens=_add(self.reasoning_tokens, other.reasoning_tokens),
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return self.merge(other)


# --------------------------------------------------------------------------- #
# 失败与终止原因
# --------------------------------------------------------------------------- #
class LlmFailure(_Proto):
    """一次失败的完整描述，可安全序列化后回传前端 / 落库。"""

    kind: Literal["transport", "provider", "protocol", "aborted"] = "provider"
    message: str
    status_code: int | None = None
    provider: str | None = None
    model: str | None = None
    retryable: bool = False
    raw: dict[str, Any] | None = None


class FinishReason(_Proto):
    """为什么这次生成结束了。"""

    kind: FinishKind
    failure: LlmFailure | None = None

    @model_validator(mode="after")
    def _check_failure_consistency(self) -> FinishReason:
        needs_failure = self.kind in ("aborted", "error")
        if needs_failure and self.failure is None:
            raise ValueError(f"finish kind={self.kind!r} 必须携带 failure 详情")
        if not needs_failure and self.failure is not None:
            raise ValueError(f"finish kind={self.kind!r} 不应携带 failure 详情")
        return self


# --------------------------------------------------------------------------- #
# 流式 chunk（7 种）
# --------------------------------------------------------------------------- #
class BlockStart(_Proto):
    type: Literal["block-start"] = "block-start"
    index: int = Field(ge=0)
    block_type: BlockType


class TextDelta(_Proto):
    type: Literal["text-delta"] = "text-delta"
    index: int = Field(ge=0)
    text: str


class ReasoningDelta(_Proto):
    type: Literal["reasoning-delta"] = "reasoning-delta"
    index: int = Field(ge=0)
    text: str


class ToolCallDelta(_Proto):
    type: Literal["tool-call-delta"] = "tool-call-delta"
    index: int = Field(ge=0)
    id: str
    name: str | None = None
    arguments_delta: str = ""


class BlockEnd(_Proto):
    type: Literal["block-end"] = "block-end"
    index: int = Field(ge=0)
    block: ContentBlock | None = None


class UsageChunk(_Proto):
    type: Literal["usage"] = "usage"
    usage: TokenUsage


class FinishChunk(_Proto):
    type: Literal["finish"] = "finish"
    reason: FinishReason
    raw_reason: str | None = Field(
        default=None, description="provider 原生终止值，仅用于排查，不参与逻辑判断"
    )
    replay_state: dict[str, Any] | None = Field(
        default=None, description="adapter 私有重放状态（响应 id、原生元数据），对上层不透明"
    )


StreamChunk = Annotated[
    Union[
        BlockStart,
        TextDelta,
        ReasoningDelta,
        ToolCallDelta,
        BlockEnd,
        UsageChunk,
        FinishChunk,
    ],
    Field(discriminator="type"),
]

__all__ = [
    "BlockType",
    "FinishKind",
    "TextBlock",
    "ReasoningBlock",
    "ToolCallBlock",
    "ContentBlock",
    "TokenUsage",
    "LlmFailure",
    "FinishReason",
    "BlockStart",
    "TextDelta",
    "ReasoningDelta",
    "ToolCallDelta",
    "BlockEnd",
    "UsageChunk",
    "FinishChunk",
    "StreamChunk",
]
