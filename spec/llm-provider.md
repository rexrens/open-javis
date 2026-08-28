# LLM Provider 层设计 v2

> 状态：待审核。参考 agno `models/base.py`、nanobot `providers/base.py`、OpenAI SDK 2.53.0 实际实现。
>
> 2026-08-28：`LLMProvider` + 数据模型（`LLMRequest` / `LLMResponse` /
> `ToolCall`）+ `estimated_cost` 已上提为稳定契约，位于
> `javis/contracts/llm.py`（stdlib-only，无 SDK 依赖）；`corecoder/llm.py`
> 保留 SDK 相关实现（`is_fallback_trigger` / 具体 provider）并 re-export。

## 0. 调研结论（三家设计对比）

| 维度 | agno | nanobot | OpenAI SDK | javis 现状 |
|---|---|---|---|---|
| 抽象方法数 | 4 invoke + 2 parse | **1**（chat 非流式） | 1（`create()` + stream 参数） | 0（无抽象） |
| 同步/异步 | 方法对 | 仅 async | **双客户端**（OpenAI/AsyncOpenAI） | LLM/AsyncLLM 双类 |
| 流式暴露 | yield ModelResponse | 回调 + 返回完整响应 | **yield chunk 迭代器** | on_token 回调 |
| 重试 | 自研分类 + 指数退避 | 自研 + 流中断恢复 | **SDK 内置**（max_retries=2，尊重 Retry-After） | 自研（无分类） |
| 流式/非流式关系 | 各自独立实现 | 流式默认回退非流式 | **一个方法 + stream 参数** | 只有流式内部聚合 |

**三个关键结论：**
1. **抽象方法越少越好**（nanobot 1 个、SDK 1 个）→ javis 抽象 `achat_stream` 一个即可
2. **SDK 内置重试** → 我们不写重试循环，错误分类只用于 fallback 决策
3. **双客户端** → provider 内部建 `client`（同步）+ `aclient`（异步），懒加载

## 1. 接口设计（核心）

### 1.1 统一基类 `LLMProvider`

```python
# corecoder/llm.py
@dataclass
class LLMRequest:
    """一次 LLM 调用的请求内容（模型输入 = 内容 + 采样参数）。

    采样参数字段为 None 表示"不覆盖"，使用 provider 构造时的默认值
    （如 OpenAICompatProvider(temperature=0.0, max_tokens=4096)）。
    非 None 则本次调用覆盖。
    """

    messages: list[dict]                # 对话历史（OpenAI Chat 格式）
    tools: list[dict] | None = None     # 工具 schema
    max_tokens: int | None = None
    temperature: float | None = None
    stop: list[str] | None = None
    top_p: float | None = None
    seed: int | None = None
    response_format: dict | None = None

class LLMProvider(ABC):
    """统一 LLM 接口：sync/async × 非流式/流式 四方法。"""

    # ---- ★ 唯一抽象方法：异步流式（javis 硬需求，TUI 依赖）----
    @abstractmethod
    async def achat_stream(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[LLMResponse]:
        """异步流式：逐 chunk yield 增量 LLMResponse（delta 语义）。"""

    # ---- 派生 1：异步非流式 = 聚合 achat_stream（子类可覆盖优化）----
    async def achat(
        self,
        request: LLMRequest,
        *,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        cache_key = self._cache_key(request)   # 命中直接返回
        merged = LLMResponse()
        async for delta in self.achat_stream(
            request, extra_body=extra_body, on_token=on_token, on_reasoning=on_reasoning
        ):
            merged = merged.merge(delta)
        return merged

    # ---- 派生 2：同步流式（javis 主链路不用，接口完整）----
    def chat_stream(
        self, request: LLMRequest, *, extra_body=None, on_token=None, on_reasoning=None
    ) -> Iterator[LLMResponse]:
        raise NotImplementedError(
            "同步流式未实现；javis 主链路走 achat_stream。需要时在子类覆盖。"
        )

    # ---- 派生 3：同步非流式（同上）----
    def chat(
        self, request: LLMRequest, *, extra_body=None, on_token=None, on_reasoning=None
    ) -> LLMResponse:
        raise NotImplementedError(
            "同步非流式未实现；javis 主链路走 achat。需要时在子类覆盖。"
        )
```

**设计决策：**
- **D1 混合方案**：`achat_stream` 是唯一抽象；`achat` 基类聚合派生（默认可用）；同步两方法默认 NotImplementedError（javis async-only），子类需要时覆盖
- **D2**：`on_token` 回调保留（TUI 渲染依赖，在 yield 前调用）
- **D3**：流式 yield 增量 delta；`LLMResponse.merge()` 聚合
- **D5**：`Agent(llm=...)` 参数名不改，`Agent.chat` 调 `llm.chat`、`Agent.achat` 调 `await llm.achat`
- **D11（2026-08-20）**：请求参数收敛为 `LLMRequest` dataclass（内容 + 采样参数），消除 `**kwargs` 透传黑洞；`extra_body` 作为命名透传口承载厂商特有参数（传输层，不进缓存 key）；LLMRequest 字段 None = 用 provider 构造默认，非 None = 本次覆盖；回调（on_token/on_reasoning）是观察者，留在方法签名不进 request

### 1.2 数据模型

```python
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str | None = None   # DeepSeek-R1 / Kimi 推理
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"

    @property
    def message(self) -> dict: ...         # OpenAI 消息格式（现有逻辑保留）

    def merge(self, other: "LLMResponse") -> "LLMResponse":
        """聚合 delta：content/reasoning 拼接、tool_calls 跨 chunk 累积、
        usage 取最后非零、finish_reason 取最后。"""
```

## 2. 错误处理（简化版：SDK 内置重试 + fallback 决策）

**关键变化**：OpenAI SDK 已内置重试（`max_retries=2`，429 尊重 Retry-After、5xx/连接错误指数退避）。**我们不再手写重试循环**，错误分类的唯一用途是 **fallback 决策**。

```python
def is_fallback_trigger(exc: Exception) -> bool:
    """主 provider 失败后是否切换 fallback。

    - 429 / 5xx / 超时 / 连接错误 → 切换（SDK 重试耗尽后仍失败）
    - 400 / 401 / 403 / 404 / 422 → 不切换（配置/密钥问题，切了也白切）
    """
    if isinstance(exc, (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):   # 4xx
        return False
    return True   # 未知异常：保守切换
```

- OpenAI SDK 异常体系：`APIError` → `APIStatusError`（实例 status_code）→ BadRequest(400)/Auth(401)/Permission(403)/NotFound(404)/Unprocessable(422)/RateLimit(429)/InternalServerError(500+)
- `max_retries` 作为 provider 构造参数透传给 SDK（默认 2；调 0 可禁用）

## 3. 前缀排序（prompt caching 优化）

```python
# LLMProvider 基类共享
def _format_tools(self, tools: list[dict] | None) -> list[dict]:
    """工具按 name 排序 → 请求前缀稳定 → 命中 prompt caching。
    (OpenAI/Anthropic/Gemini 的 prompt caching 都要求稳定前缀)"""
    if not tools:
        return []
    return sorted(tools, key=lambda t: (t.get("function") or {}).get("name", ""))
```

## 4. 缓存（可选）

```python
# LLMProvider 构造参数
cache_response: bool = False   # 默认关
cache_dir: str | None = None   # 默认 ~/.javis/cache/llm/
cache_ttl: int | None = None   # 秒；None = 不过期

# 只缓存非流式完整响应（chat/achat）
# key = hash(model + LLMRequest 全部字段：messages/tools/max_tokens/temperature/stop/top_p/seed/response_format)
# extra_body 不进 key：它是传输层透传，不是模型输入；若某透传字段影响输出，应收编为 LLMRequest 显式字段
# 原子写（复用 session_storage.atomic_write 思路）
```

## 5. 子类

### 5.1 `OpenAICompatProvider`（一期）

```python
class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容端点（DeepSeek/Qwen/Kimi/GLM/Ollama/vLLM…）。"""

    def __init__(self, model, api_key, base_url=None, *,
                 temperature=0.0, max_tokens=4096, max_context_tokens=128_000,
                 max_retries=2,           # 透传 SDK 内置重试
                 cache_response=False, cache_dir=None, cache_ttl=None):
        # ★ 懒加载双客户端（对齐 SDK 双客户端设计）
        self._client: OpenAI | None = None
        self._aclient: AsyncOpenAI | None = None

    async def achat_stream(
        self, request: LLMRequest, *, extra_body=None, on_token=None, on_reasoning=None
    ):
        aclient = self._aclient or AsyncOpenAI(api_key=self.api_key,
                                               base_url=self.base_url,
                                               max_retries=self.max_retries)
        params = self._base_params(request, extra_body)  # request 非 None 字段覆盖构造默认
        stream = await aclient.chat.completions.create(**params, stream=True)
        # BadRequestError → 去掉 stream_options 重试一次（现有逻辑保留）
        async for chunk in stream:
            yield self._parse_delta(chunk)

    def chat_stream(self, messages, tools=None, *, on_token=None, **kwargs):
        """同步流式：OpenAI 同步客户端，同解析逻辑。"""

    def achat(self, messages, tools=None, *, on_token=None, **kwargs) -> LLMResponse:
        """覆盖：stream=False 一次 JSON 请求（比聚合快），可用时用。"""

    def _parse_delta(self, chunk) -> LLMResponse:
        """chunk → delta LLMResponse：
        delta.content → content
        delta.reasoning_content → reasoning_content（DeepSeek-R1/Kimi）
        delta.tool_calls → tool_calls 片段
        chunk.usage → usage（最后 chunk）
        chunk.choices[0].finish_reason → finish_reason"""
```

### 5.2 `ScriptedProvider`（一期，替换 ScriptedLLM/AsyncScriptedLLM）

```python
class ScriptedProvider(LLMProvider):
    """确定性脚本回放（测试/演示）。只实现 achat_stream 一个，其余派生。"""
    def __init__(self, script: list[LLMResponse], model="scripted-demo"): ...
```

### 5.3 `FallbackProvider`（一期，spec fallback 落地）

```python
class FallbackProvider(LLMProvider):
    """主 provider 失败（SDK 重试耗尽且 is_fallback_trigger=True）→ 依次尝试备选。"""
    def __init__(self, primary: LLMProvider, fallbacks: list[LLMProvider]): ...
    # 只实现 achat_stream：try primary → except → 记录 → 切下一个
```

### 5.4 `AnthropicProvider`（二期）

```python
class AnthropicProvider(LLMProvider):
    """Anthropic Messages API + extended thinking。只需实现 achat_stream：
    SSE 事件归一：content_block_delta(text) → content
                  thinking_delta → reasoning_content
                  tool_use 累积 → tool_calls
    错误：anthropic APIStatusError 映射到 is_fallback_trigger 语义。"""
```

### 5.5 `OpenAIResponsesProvider`（二期）

```python
class OpenAIResponsesProvider(LLMProvider):
    """OpenAI Responses API（新一代）。只需实现 achat_stream：
    事件归一：output_text.delta → content
              function_call_arguments.delta → tool_calls
              reasoning.summary → reasoning_content
    input/instructions 格式转换在内部。"""
```

## 6. 消息格式与协议差异（扩展路径）

**线格式约定（一期）**：Agent 内消息保持现有 OpenAI Chat Completions dict 格式；各 provider 内部自行转换（Anthropic/Responses 收到后反转换）。

**二期可选升级**：Agent 迁移到规范消息（ConversationMessage），provider 各自 `format_messages()`——加新协议不再反解析。改动中等，加 Anthropic 时一并做。

| 协议差异 | Chat Completions | Anthropic | Responses |
|---|---|---|---|
| 消息 | messages[] role 数组 | system 顶层 + tool_result 嵌 user + 交替规则 | input[] + instructions 顶层 |
| 工具 | {type,function:{name,parameters}} | {name,description,input_schema} | 类似 Chat + strict |
| 流式 | delta chunks | SSE 事件流 | 事件序列 |
| 推理 | reasoning_content | thinking blocks | reasoning.summary |

## 7. Agent 适配

```python
# Agent.chat（同步循环，现状保留）
resp = self.llm.chat(messages=..., tools=..., on_token=on_token)

# Agent.achat（异步循环，现状保留）
resp = await self.llm.achat(messages=..., tools=..., on_token=on_token)
```

仅调用分流（chat → chat / achat → await achat），其余不动。

## 8. 配置映射（spec/config.md 对接）

```jsonc
"api": "openai-completions"  → OpenAICompatProvider
"api": "anthropic"           → AnthropicProvider（二期）
"api": "litellm"             → ❌ 删除（不可用陷阱）
// provider 级可选字段
"maxRetries": 2,             // SDK 内置重试次数
"cacheResponse": false,      // prompt 缓存（默认关）
"cacheTtl": null,            // 秒
// fallback（spec 已有）
"fallback_provider": "my-vllm",
"fallback_model": "Qwen/Qwen2.5-72B-Instruct"
```

## 9. 迁移与删除

| 旧类 | 处置 | 引用点 |
|---|---|---|
| `LLM` | 🗑 → `OpenAICompatProvider` | corecoder/cli.py、__init__.py |
| `AsyncLLM` | 🗑 → `OpenAICompatProvider` | javis/engines/corecoder/backend.py |
| `LiteLLM` | 🗑 删除 | corecoder/cli.py |
| `ScriptedLLM` / `AsyncScriptedLLM` | 🗑 → `ScriptedProvider` | corecoder/demo.py、tests |

## 10. 测试计划

| 文件 | 内容 |
|---|---|
| `test_llm_provider.py`（新） | 四方法契约、聚合派生、merge、前缀排序、缓存 |
| `test_llm_errors.py`（新） | is_fallback_trigger 分类表、SDK 异常映射 |
| `test_async_llm.py`（改） | achat_stream 流式、reasoning_content 透传 |
| `test_agent_callbacks.py`（改） | ScriptedProvider 替换 |
| `test_corecoder_backend.py`（改） | OpenAICompatProvider 替换 AsyncLLM |

## 11. 实施顺序

1. 重写 `corecoder/llm.py`：LLMProvider 基类 + 数据模型 + merge + 前缀排序 + 缓存 + is_fallback_trigger
2. `ScriptedProvider`（最简单，验证基类派生）
3. `OpenAICompatProvider`（懒加载双客户端 + _parse_delta + reasoning）
4. Agent 适配（chat/achat 分流）
5. 更新引用点（cli.py、demo.py、__init__.py、backend.py）
6. 更新测试 + 新增
7. 全量测试 + ruff + DeepSeek 运行时验证
8. `FallbackProvider`（fallback 字段落地）

## 12. 待确认决策点

- [x] D1: 唯一抽象 `achat_stream`，其余派生（同步方法默认 NotImplementedError，可覆盖）
- [x] D2: 保留 `on_token` 回调
- [x] D3: 流式 yield 增量 delta + merge 聚合
- [x] D5: `Agent(llm=)` 参数名不改，chat/achat 分流
- [x] D6: LiteLLM 直接删除
- [x] D7: 缓存默认关，`~/.javis/cache/llm/`
- [x] D8: FallbackProvider 一期做（实施顺序第 8 步）
- [x] D9: 重试用 SDK 内置（`max_retries` 透传），不手写重试循环 —— **新结论**
- [x] D10: 同步方法（chat/chat_stream）也实现（javis async-only，但接口完整性）
