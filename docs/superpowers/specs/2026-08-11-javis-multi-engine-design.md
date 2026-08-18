# javis 多引擎（agent engine）架构设计

日期：2026-08-11
状态：已确认（brainstorming 完成，待实施）

## 1. 背景与目标

javis 是自包含的 TUI 壳：React 前端（`frontend/terminal/`）+ JSON-lines 后端（`javis/backend_host.py`）+ 通用引擎层（`javis/engine/mock_engine.py`）+ 协议 seam（`javis/engine/protocol.py` 的 `AgentBackend`）。目前唯一的引擎实现是 `MockAgent`（关键词路由的假 agent，供 TUI 开发）。

`corecoder/` 是仓库顶层的独立参考 agent：同步 `Agent.chat()` 循环、自带工具（bash/read/write/edit/glob/grep/agent）、OpenAI 兼容 LLM 层。

目标：让 javis 支持多个后端 agent 引擎，可插拔切换。首批接入 **corecoder**（默认引擎）；后续接入 **Agno**（异步框架）与 **claude code CLI**（独立进程）。corecoder 的适配过程产出一份**对接文档**（`docs/agent-engine-guide.md`），供第三方引擎作者照此实现。

## 2. 已确认的决策

| 决策点 | 结论 |
|--------|------|
| 引擎边界 | **A：turn 级 seam，adapter 包一切**。工具执行、权限、历史都在引擎内部，javis 只渲染事件 |
| claude CLI 形态 | 本轮只留接口，adapter 后续实现 |
| 引擎选择 | 配置文件 `~/.javis/config.json` + CLI `--engine` + env `JAVIS_ENGINE`，**默认 corecoder**，mock 保留给开发调试 |
| 交付范围 | 设计确认后直接实现（本 spec 的 8-11 节） |
| corecoder 异步化 | **B：同步异步双接口**。新增 `AsyncLLM` + `Agent.achat()`，同步 `chat()`/CLI/demo 不动；adapter 原生 async，**无线程桥** |
| 图片 | 不增补多模态，adapter 用 `[image omitted]` 占位 |
| usage | `AgentTurnEnd` 增补可选 `usage` 字段，引擎上报真实 token；消费与估算由 javis 引擎层统一处理 |

## 3. 架构总览

```
frontend/ (React TUI)                          ← 不动，wire protocol 不变
      │ OHJSON: BackendEvent / FrontendRequest
javis/backend_host.py                          ← 渲染 AgentEvent → 前端事件，不感知具体引擎
      │ AgentEvent 流
javis/engine/mock_engine.py                    ← 通用引擎层：镜像历史、usage 累加、turn 分发
      │ AgentBackend（协议 seam）
      ├── MockAgent                            ← 内置 mock 引擎（开发调试）
      └── CoreCoderBackend ──→ corecoder/Agent（achat）──→ corecoder/AsyncLLM
javis/engines/registry.py                      ← name → 工厂，懒加载
javis/config.py                                ← ~/.javis/config.json 读取 + 优先级解析
cli.py --engine                                ← 覆盖配置（前端 spawn 后端时同步传递）
```

## 4. 对接协议 v2（`javis/engine/protocol.py`）

```python
class AgentBackend(Protocol):
    # 必需
    async def run_turn(
        self,
        prompt: str | ConversationMessage,
        *,
        context: AgentContext,
    ) -> AsyncIterator[AgentEvent]: ...

    # 可选钩子（javis 引擎层用 hasattr 检测；没有则跳过）
    def load_history(self, messages: list[ConversationMessage]) -> None: ...
    def clear_history(self) -> None: ...
```

`AgentTurnEnd` 增补：

```python
@dataclass(frozen=True)
class AgentTurnEnd:
    text: str = ""
    usage: UsageSnapshot | None = None   # 新增：本次 turn 消耗的 token（非累计）
```

### 协议契约（对接文档的核心条款）

1. **终止语义**：`run_turn` 必须以恰好一个 `AgentTurnEnd` 或 `AgentError` 结束；不允许空流（没有结束事件视为违约）。
2. **工具在引擎内部**：javis 只渲染 `AgentToolCallStart` / `AgentToolCallResult`，不执行工具、不做权限。`AgentToolCallStart` 必须在工具执行**前**发出，`AgentToolCallResult` 在执行**后**发出；`is_error=True` 表示执行失败（LLM 会在下一轮看到错误内容）。
3. **镜像历史 = user/assistant 文本流**：javis 引擎层维护 javis 侧镜像（UI 可见的文本消息，工具轮不镜像），用于 `/status`、会话存储与恢复。**引擎内部历史是权威**（LLM 上下文以它为准）。`load_history`/`clear_history` 是同步点：恢复会话与 `/clear` 时调用，引擎负责转换为自己的格式。
4. **中断语义**：host cancel 后 `run_turn` 的事件被丢弃。引擎应尽快停止；若引擎内部状态可能因中断而不完整（如已有 assistant tool_calls 未回复），必须自行修复（corecoder 的 `_answer_pending_tool_calls` 模式），保证下次调用历史合法。异步引擎直接传播 `CancelledError`；同步引擎靠检查点（如 `threading.Event`）在下一轮停止；进程引擎靠杀子进程或发信号。
5. **usage**：`AgentTurnEnd.usage` 若非空 = 本次 turn 消耗的 token（输入+输出）。该字段的消费与具体引擎无关，由 **javis 引擎层**统一处理（`javis/engine/mock_engine.py` 是当前唯一的引擎层实现）：非空则累加进 `engine.total_usage`，为空则按词数估算（现状保留）。
6. **图片**：`ConversationMessage` 可含 `ImageBlock`。不支持多模态的引擎用 `[image omitted]` 占位文本替代，不报错。
7. **系统提示词**：`context.system_prompt` 由 `MockEngine` 在构建时提供；引擎应将其作为本会话的系统提示词（corecoder 通过 `set_system_prompt` 注入）。

## 5. corecoder 增补（全部在 `corecoder/`）

### 5.1 `corecoder/llm.py`：新增 `AsyncLLM`

- 用 `openai.AsyncOpenAI` 客户端；`async def chat(messages, tools=None, on_token=None) -> LLMResponse`，流式迭代与现有 `LLM.chat` 逻辑一致
- async 版 `_call_with_retry`（同样的重试策略：RateLimit/Timeout/Connection 指数退避，5xx 重试、4xx 直接抛）
- 维护 `total_prompt_tokens` / `total_completion_tokens` 实例属性（与同步 `LLM` 一致，adapter 用前后差值算 usage）
- 与同步 `LLM` 共享 `_PRICING` 定价表

### 5.2 `corecoder/agent.py`：新增 `async achat(...)` 与回调

- `async def achat(user_input, on_token=None, on_tool=None, on_tool_result=None) -> str`：循环逻辑与 `chat()` 一致，共用消息维护与工具执行方法
- **取消语义**：`CancelledError` 在 `await` 处抛出 → 先 `_answer_pending_tool_calls()` 补全未回复的工具调用 → re-raise
- **工具执行用 `asyncio.to_thread` 包装**（同步工具不能阻塞事件循环）；工具执行窗口内取消不生效（与进程引擎一致，写入对接文档）
- 每轮 LLM 调用后、工具执行前调用 `maybe_compress`（与同步路径一致）

### 5.3 `Agent.chat()` 与 `achat()` 都新增 `on_tool_result` 回调

- 签名：`on_tool_result(name, args, result, is_error)`；单发与并行路径都要调用
- `is_error` 判定：未知工具 / 参数 bind 失败 / 执行异常 → `True`；否则 `False`（工具自身返回的 "Error: ..." 字符串视为正常执行结果，`False`）
- 实现：新增内部方法返回 `(result, is_error)`，`_exec_tool` 保持现有签名不变

### 5.4 公开接口

- `Agent.load_messages(messages: list[dict])`：替换 `self.messages`
- `Agent.set_system_prompt(prompt)`：替换 `self._system`
- （`Agent.reset()` 已存在，用作 `clear_history`）

### 5.5 `corecoder/llm.py`：`ScriptedLLM` 加 async 变体

- 新增 `AsyncScriptedLLM` 类：与同步版同样的离线脚本回放语义（`async def chat`，token 计数用实例属性），供 adapter 离线测试

## 6. `CoreCoderBackend` adapter（`javis/engines/corecoder_backend.py`）

原生异步，无线程桥。`run_turn` 生产者-消费者模式，生产者为 asyncio task：

```python
async def run_turn(self, prompt, *, context) -> AsyncIterator[AgentEvent]:
    queue: asyncio.Queue = asyncio.Queue()

    def emit(item) -> None:
        queue.put_nowait(item)          # 同线程，无需 call_soon_threadsafe

    async def producer() -> None:       # achat 是 async 的，直接 create_task
        try:
            final = await self._agent.achat(
                prompt_text,
                on_token=lambda t: emit(("delta", t)),
                on_tool=lambda name, args: emit(("tool_start", name, args)),
                on_tool_result=lambda n, a, out, err: emit(("tool_result", n, out, err)),
            )
            emit(("done", final))
        except Exception as exc:
            emit(("error", exc))

    task = asyncio.create_task(producer())
    try:
        while True:
            kind, *payload = await queue.get()
            if kind == "done":
                final_text = payload[0]; break
            if kind == "error":
                yield AgentError(message=str(payload[0]), recoverable=True); return
            if kind == "delta":        yield AgentTextDelta(text=payload[0])
            if kind == "tool_start":   yield AgentToolCallStart(tool_name=payload[0], tool_input=payload[1])
            if kind == "tool_result":  yield AgentToolCallResult(tool_name=payload[0], output=payload[1], is_error=payload[2])
    finally:
        if not task.done():            # 外部取消传播到 run_turn 时，确保 producer 收尾
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
```

- **取消路径**：外部 cancel 传播到 `run_turn` 的 `await queue.get()` → `CancelledError` 在 `finally` 中 cancel producer → `achat` 内部捕 `CancelledError`，补全未回复的工具调用（契约 #3）后 re-raise → 任务以取消结束。**run_turn 被取消时不产生终止事件**，UI 提示由 host 负责（契约 #1 的终止事件约束适用于正常流程；中断路径豁免，host 已发 "Interrupted by user" transcript）
- 悬挂风险已排除：`run_turn` 活着且 producer 意外死亡（取消）只可能发生在 run_turn 自身也被取消时（同一条取消链），不会死等 `queue.get()`
- usage：producer 启动前记录 `async_llm.total_prompt_tokens / total_completion_tokens`，结束时差值 → `AgentTurnEnd(usage=UsageSnapshot(...))`
- `load_history(messages)`：`_to_corecoder_messages(messages)`（javis `ConversationMessage` → OpenAI dict，含 `ImageBlock → "[image omitted]"`、tool result → `tool` 角色消息）→ `agent.load_messages(...)`
- `clear_history()`：`agent.reset()`
- 构造：工厂从 `corecoder.config.Config` 解析（env + config.json 合并），`max_turns → agent.max_rounds`，`system_prompt → agent.set_system_prompt`

## 7. 注册与配置

### 7.1 `javis/engines/registry.py`

```python
def register_engine(name: str, factory: Callable[..., AgentBackend]) -> None
def create_agent_backend(name, *, model, system_prompt, cwd, max_turns,
                         tool_metadata, engine_config) -> AgentBackend
def list_engines() -> list[str]
def get_engine_config(name, config: dict) -> dict   # config["engines"][name] 或 {}
```

- 内置注册：`mock`（工厂返回 `MockAgent()`，忽略配置）、`corecoder`（懒加载 import `corecoder`，构造 AsyncLLM + Agent + CoreCoderBackend）
- registry 模块本身不 import corecoder（mock-only 环境可用）
- 未知引擎名：抛 `ValueError`，列出可用引擎

### 7.2 `javis/config.py`（新文件）

- `load_config(workspace) -> dict`：读 `<workspace>/config.json`（不存在 → `{}`）
- `resolve_engine_name(cli: str | None, config: dict, env: Mapping) -> str`：优先级 **CLI `--engine` > env `JAVIS_ENGINE` > config `engine` > 默认 `"corecoder"`**
- config.json 示例：

```json
{
  "engine": "corecoder",
  "engines": {
    "corecoder": { "model": "deepseek-chat", "base_url": "...", "api_key": "..." }
  }
}
```

### 7.3 runtime / CLI

- `build_javis_runtime(engine: str | None = None, agent_backend: AgentBackend | None = None, ...)`：
  - 两者都传 → `ValueError`（互斥）
  - 显式 `agent_backend` → 跳过注册表（测试用）
  - 都缺省 → `resolve_engine_name` → `create_agent_backend`
  - `restore_messages` 非空 → `engine.load_messages(restored)` + `backend.load_history(restored)`（hasattr 检测）
- `cli.py` 加 `--engine` 参数，透传给 `run_javis_print_mode` / `run_javis_backend` / `launch_react_tui`
- `react_launcher.build_backend_command` 加 `--engine` 透传（前端 spawn 后端时生效，前端代码零改动）

## 8. 数据流（一个 turn）

```
submit_line → handle_line → MockEngine.submit_message
  → 镜像追加 user msg → backend.run_turn(prompt, context)
  → [queue 转发] agent.achat 循环:
      on_token        → AgentTextDelta      → host assistant_delta
      on_tool         → AgentToolCallStart  → host tool_started
      on_tool_result  → AgentToolCallResult → host tool_completed
      → AgentTurnEnd(text, usage)           → host assistant_complete
      → LLM 异常       → AgentError         → host error
      → 取消           → AgentError(Interrupted) → host error
  → 镜像追加 assistant 文本 + usage 累加 → status_snapshot + line_complete
```

## 9. 错误处理

| 场景 | 行为 |
|------|------|
| LLM/网络错误 | `AgentError(recoverable=True)` → error 事件 + transcript；镜像只含 user msg；引擎内部历史保留（下个 turn 继续可用） |
| 工具错误 | `AgentToolCallResult(is_error=True)`，agent 循环继续，LLM 下一轮看到错误内容 |
| 用户中断 | host cancel → `achat` 捕 `CancelledError` → 补全未回复工具调用 → re-raise → `run_turn` 结束（无 turn_end）；host 发 "Interrupted by user" transcript + status_snapshot + line_complete |
| 未知引擎名 | 启动时报错，列出可用引擎 |
| 配置缺失（corecoder 无 API key） | 构造 AsyncLLM 时用空 key，首次 LLM 调用报错 → 走 LLM/网络错误路径（错误信息里提示设置 `OPENAI_API_KEY` 等） |

## 10. 测试策略

- **corecoder**（新增 `tests/test_corecoder/`）：`on_tool_result`（单发/并行/错误判定）、`achat` 完整循环（AsyncScriptedLLM 驱动）、`achat` 取消语义（`_answer_pending_tool_calls` 生效）、`load_messages`/`set_system_prompt`
- **adapter**（`tests/test_javis/test_corecoder_backend.py`）：Scripted 驱动完整事件序列（delta→tool_start→tool_result→turn_end）、usage 差值上报、LLM 错误 → `AgentError`、中断 → 结束无 turn_end、`load_history`/`clear_history` 钩子、`_to_corecoder_messages` 转换（含 image 占位、tool result）
- **registry/config**：注册/工厂/未知引擎报错；engine 解析优先级（CLI > env > config > 默认）
- **runtime**：默认 engine=corecoder 但 mock 可用；`build_javis_runtime(engine="mock")` 不构建 LLM；互斥参数报错
- **现有测试适配**：`build_javis_runtime` 无参调用默认变 corecoder（需 API key）→ 相关测试显式 `engine="mock"` 或 `agent_backend=MockAgent()`

## 11. 交付物与实施顺序

1. `docs/agent-engine-guide.md`——对接文档（见 §12）
2. corecoder 增补：AsyncLLM、achat、on_tool_result、公开接口、AsyncScriptedLLM
3. javis 协议 v2：`AgentTurnEnd.usage`、可选钩子
4. `javis/engines/`（registry + corecoder_backend）、`javis/config.py`
5. runtime/CLI 接线（`--engine`、透传、互斥校验）
6. 测试：corecoder 增补测试、adapter 测试、registry/config 测试、现有测试适配
7. 收尾：`pytest` 全绿、`python -m javis --print` 冒烟、`uv build` 验证

## 12. 对接文档（`docs/agent-engine-guide.md`）

面向第三方引擎作者，内容：
- 概念：turn、事件流、镜像历史、引擎边界（工具在引擎内）
- 必须实现：`run_turn` 契约（§4 的 7 条条款）
- 可选实现：`load_history`/`clear_history`、`AgentTurnEnd.usage` 上报
- 三种适配模式：**原生 async**（Agno——直接 `async for` 转发）、**同步引擎**（线程桥模板：`to_thread` + `call_soon_threadsafe` + queue，含 interrupt 检查点说明）、**进程引擎**（claude CLI——子进程/长驻 JSON 协议，杀进程或发信号的中断语义）
- 注册引擎：registry API + config.json 示例
- 常见坑：取消后历史合法、工具事件时序、`is_error` 语义、空流违约、事件粒度（delta 越细 UI 越顺滑）
