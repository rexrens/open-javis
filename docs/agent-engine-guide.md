# javis Agent 引擎对接指南

本文档面向想把自研/第三方 agent 接入 javis TUI 的引擎作者。读完本文档 + `javis/contracts/engine.py`，即可实现一个引擎。

配套设计文档：`docs/superpowers/specs/2026-08-26-engine-simplification-design.md`（本指南取代旧的 2026-08-11 多引擎设计中的 AgentBackend/QueryEngine 双层接法）

## 1. 概念

| 概念 | 说明 |
|------|------|
| **引擎** | 一个实现 `AgentEngine` 契约的对象：**自己拥有**会话历史与 usage，`submit_message` 产出 `AgentEvent` 流 |
| **turn** | 一次用户输入对应的完整处理过程。一个 turn = 引擎从收到 prompt 到产出最终回复的全部活动（可能包含多轮 LLM 调用与工具执行） |
| **事件流** | `submit_message` 产出的 `AgentEvent` 序列，javis 逐个渲染给前端 |
| **镜像历史** | `engine.messages`（`ConversationMessage` 列表，user/assistant 文本）。用于 `/status`、会话存储、恢复。**这是 javis 侧唯一权威历史**，引擎内部历史（如 corecoder.Agent 的 dict 消息）由引擎负责同步 |
| **引擎边界** | 工具执行、权限决策、引擎内部历史都由引擎自己管理。javis 只做渲染与协议转换 |

## 2. 必须实现：`AgentEngine`

```python
from javis.contracts.engine import AgentEngine
from javis.contracts.messages import ConversationMessage
from javis.contracts.types import AgentEvent

class MyEngine(AgentEngine):
    # 属性
    @property
    def messages(self) -> list[ConversationMessage]: ...      # 镜像历史（权威）
    @property
    def total_usage(self) -> UsageSnapshot: ...               # 累计 usage
    @property
    def model(self) -> str: ...
    @property
    def system_prompt(self) -> str: ...
    @property
    def max_turns(self) -> int | None: ...
    @property
    def tool_metadata(self) -> dict[str, Any]: ...

    # turn 执行（核心）
    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[AgentEvent]:
        ...

    # 生命周期
    def clear(self) -> None: ...
    def load_messages(self, messages: list[ConversationMessage]) -> None: ...
    def set_system_prompt(self, prompt: str) -> None: ...
    def set_max_turns(self, max_turns: int | None) -> None: ...
    def set_model(self, model: str) -> None: ...
    def set_effort(self, effort: str | None) -> None: ...
```

### 事件类型（`javis/contracts/types.py`）

| 事件 | 含义 | 时机 |
|------|------|------|
| `AgentTextDelta(text)` | 增量文本 | LLM 流式输出时，逐块发出（越细前端越顺滑） |
| `AgentToolCallStart(tool_name, tool_input)` | 即将执行工具 | 工具执行**前** |
| `AgentToolCallResult(tool_name, output, is_error)` | 工具执行结果 | 工具执行**后** |
| `AgentTurnEnd(text, usage=None)` | 本 turn 结束 | 恰好一次，且必须是流的终点 |
| `AgentError(message, recoverable=True)` | 出错结束 | 作为流的终点（与 `AgentTurnEnd` 二选一） |
| `AgentStatus(message)` | 瞬时状态 | 任意时刻，0 到多次 |

### 契约条款（违反即 bug）

1. **终止语义**：流必须以恰好一个 `AgentTurnEnd` 或 `AgentError` 结束。不允许空流、不允许没有终止事件。
2. **工具事件时序**：`AgentToolCallStart` 在工具执行前，`AgentToolCallResult` 在执行后；`is_error=True` 表示执行失败（LLM 下一轮会看到错误内容，所以失败也要把错误文本放进 `output`）。
3. **中断**：javis 取消 `submit_message` 后，你的事件会被丢弃，但引擎内部状态必须保持**合法**——最典型的坑：assistant 消息带了 `tool_calls` 却没有对应的 tool 回复，下次请求会被 OpenAI 兼容 API 拒绝。中断时必须自行补全（corecoder 的做法是 `_answer_pending_tool_calls`）。
4. **usage 可选上报**：`AgentTurnEnd.usage = UsageSnapshot(input_tokens, output_tokens)` 表示**本次 turn** 消耗的 token（非累计）。不填则由引擎按词数估算（内建引擎的行为）。
5. **图片**：`ConversationMessage` 可能含 `ImageBlock`。不支持多模态就把它替换成 `[image omitted]` 占位文本，不要报错。
6. **系统提示词**：`set_system_prompt` 会随时被调用（每个 turn 都可能更新），引擎必须把当前值注入后续请求。
7. **历史同步**：`load_messages` 用 javis 镜像重建引擎内部历史；`clear` 同时清空镜像与引擎内部历史。

## 3. 内建实现：`CoreCoderEngine`

`javis/engines/corecoder/engine.py` 是默认实现——一个对象同时承担了旧设计里 QueryEngine（历史/usage/事件流壳）+ CoreCoderBackend（适配层）+ build_corecoder_backend（组装）的职责，内部驱动 `corecoder.Agent`（纯 chat/achat loop）。

```python
from javis.engines.corecoder.engine import CoreCoderEngine

engine = CoreCoderEngine.build(
    model="deepseek-chat",
    api_key="...",
    base_url="https://api.deepseek.com",
    max_tokens=8192,        # 可选
    system_prompt="...",
    cwd="/path/to/workspace",
    max_turns=32,           # 可选
    tool_metadata={},
)
```

`engine.agent` 暴露内部 `corecoder.Agent`，供宿主注入钩子（如权限检查器 `agent.permission_checker`）。

## 4. 三种适配模式（实现自己的引擎时参考）

### 模式一：原生异步引擎（推荐，如 Agno）

引擎本身就是 async，直接转发：

```python
class AgnoEngine(AgentEngine):
    async def submit_message(self, prompt) -> AsyncIterator[AgentEvent]:
        text = prompt.text if isinstance(prompt, ConversationMessage) else prompt
        try:
            async for delta, tool_use in self._agent.run(text, stream=True):  # 伪代码
                if delta:      yield AgentTextDelta(text=delta)
                if tool_use:   yield AgentToolCallStart(tool_name=tool_use.name, tool_input=tool_use.input)
                if tool_use.result:
                    yield AgentToolCallResult(tool_name=tool_use.name,
                                              output=tool_use.result,
                                              is_error=tool_use.error is not None)
            yield AgentTurnEnd(text=self._agent.last_response, usage=...)   # 确保恰好一次
        except asyncio.CancelledError:
            self._fixup_history_after_interrupt()   # 契约 #3
            raise
```

### 模式二：同步引擎（线程桥）

引擎的入口是同步阻塞函数（如 `agent.chat(...)`）。线程桥三件套：`asyncio.to_thread` + `call_soon_threadsafe` + `asyncio.Queue`（内建 CoreCoderEngine 用原生 `achat`，不需要这套）。

### 模式三：进程引擎（claude code CLI 等）

引擎是独立进程。两种形态：

| 形态 | 做法 | 中断 |
|------|------|------|
| 每 turn 一进程 | 子进程跑 `claude -p "<prompt>"`（可带 `--resume <id>` 续会话），解析 stdout | 杀子进程即可，无状态残留 |
| 长驻进程 | 启动时拉起子进程，用结构化协议（如 `claude --output-format stream-json`）逐事件通信 | 发中断信号（SIGINT/SIGTERM）或杀进程 |

## 5. 接入与替换

### 5.1 当前接入

`build_javis_runtime` 直接构建 `CoreCoderEngine.build(...)`（provider/api_key 从 config 解析后传参，无注册表/工厂间接层）。测试用 `engine=FakeEngine()` 注入假引擎（`tests/test_javis/fake_backend.py`）。

### 5.2 未来替换（插件系统）

引擎替换走插件系统：插件实现 `AgentEngine` 并通过 `ctx.provide("engine", impl)` 提供服务，宿主接线时取该服务（见 `docs/plugins.md`）。当前核心不接插件，接口形状已为此预留——新引擎只需实现同一个 `AgentEngine` 契约。

## 6. 常见坑

| 坑 | 说明 |
|----|------|
| 空流 / 无终止事件 | 引擎内部 return 了但没 yield `AgentTurnEnd`/`AgentError`——前端永远等不到 line_complete。用 try/finally 或哨兵保证恰好一次 |
| 中断后历史不合法 | assistant `tool_calls` 无对应 tool 回复 → 下次请求 400。中断路径里补全（见契约 #3） |
| `is_error` 语义混乱 | 工具自身返回的 "Error: ..." 字符串 ≠ 执行失败；`is_error=True` 仅指"工具没能执行"（未找到/参数错/抛异常） |
| 事件粒度太粗 | 一个 turn 只发一个 `AgentTextDelta` + `AgentTurnEnd` 也能工作，但前端打字机效果、工具进度条都没了 |
| 镜像 vs 引擎历史不一致 | 镜像只存文本流，工具轮永远在引擎侧。不要在镜像里塞工具消息；`load_messages`/`clear` 必须同步引擎内部历史 |
| 阻塞事件循环 | 同步引擎直接跑在 async 上下文里会卡死整个后端。要么线程桥，要么引擎原生 async |
| 取消不响应 | host 取消后线程/子进程继续跑完整个 turn：消耗资源、状态推进、UI 已丢弃事件。提供检查点或杀进程 |
