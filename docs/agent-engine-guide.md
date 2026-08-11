# javis Agent 引擎对接指南

本文档面向想把自研/第三方 agent 接入 javis TUI 的引擎作者。读完本文档 + `javis/engine/` 源码，即可实现一个可插拔的引擎 adapter。

配套设计文档：`docs/superpowers/specs/2026-08-11-javis-multi-engine-design.md`

## 1. 概念

| 概念 | 说明 |
|------|------|
| **turn** | 一次用户输入对应的完整处理过程。一个 turn = 引擎从收到 prompt 到产出最终回复的全部活动（可能包含多轮 LLM 调用与工具执行） |
| **事件流** | `run_turn` 产出的 `AgentEvent` 序列，javis 逐个渲染给前端 |
| **镜像历史** | javis 侧的 user/assistant **文本**消息列表（UI 可见内容）。工具轮不镜像。用于 `/status`、会话存储、恢复 |
| **引擎边界** | 工具执行、权限决策、引擎内部历史都由引擎自己管理。javis 只做渲染与协议转换 |

## 2. 必须实现：`AgentBackend.run_turn`

```python
from javis.engine.protocol import AgentBackend
from javis.engine.types import AgentContext

class MyBackend(AgentBackend):
    async def run_turn(
        self,
        prompt: str | ConversationMessage,   # 本次用户输入
        *,
        context: AgentContext,               # cwd / model / system_prompt / messages(镜像) / tool_metadata
    ) -> AsyncIterator[AgentEvent]:
        ...
```

### 事件类型（`javis/engine/types.py`）

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
3. **中断**：javis 取消 `run_turn` 后，你的事件会被丢弃，但引擎内部状态必须保持**合法**——最典型的坑：assistant 消息带了 `tool_calls` 却没有对应的 tool 回复，下次请求会被 OpenAI 兼容 API 拒绝。中断时必须自行补全（corecoder 的做法是 `_answer_pending_tool_calls`）。
4. **usage 可选上报**：`AgentTurnEnd.usage = UsageSnapshot(input_tokens, output_tokens)` 表示**本次 turn** 消耗的 token（非累计）。不填则由 javis 按词数估算。
5. **图片**：`ConversationMessage` 可能含 `ImageBlock`。不支持多模态就把它替换成 `[image omitted]` 占位文本，不要报错。
6. **系统提示词**：把 `context.system_prompt` 作为本会话的 system prompt 注入引擎（每个 turn 都可能更新）。
7. **`context.messages` 是镜像快照**：仅供参考；引擎内部历史才是权威。

## 3. 可选实现（推荐实现）

```python
def load_history(self, messages: list[ConversationMessage]) -> None: ...
def clear_history(self) -> None: ...
```

- `load_history`：用 javis 的消息列表**重建引擎内部历史**（转成引擎自己的格式）。调用时机：会话恢复时。
- `clear_history`：清空引擎内部历史。调用时机：`/clear`。
- 不实现则 javis 跳过（引擎自己管理历史，恢复会话时引擎侧历史为空）。

## 4. 三种适配模式

### 模式一：原生异步引擎（推荐，如 Agno）

引擎本身就是 async，直接转发：

```python
class AgnoBackend(AgentBackend):
    async def run_turn(self, prompt, *, context) -> AsyncIterator[AgentEvent]:
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

引擎的入口是同步阻塞函数（如 `agent.chat(...)`）。线程桥三件套：`asyncio.to_thread` + `call_soon_threadsafe` + `asyncio.Queue`。

```python
class SyncBackend(AgentBackend):
    async def run_turn(self, prompt, *, context) -> AsyncIterator[AgentEvent]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        interrupt = threading.Event()          # 取消检查点，见下

        def emit(item):  loop.call_soon_threadsafe(queue.put_nowait, item)

        def run():      # worker 线程
            try:
                result = self._agent.chat(
                    prompt_text,
                    on_token=lambda t: emit(("delta", t)),
                    on_tool=lambda name, args: emit(("tool_start", name, args)),
                    on_tool_result=lambda name, args, out, err: emit(("tool_result", name, out, err)),
                )
                emit(("done", result))
            except Exception as exc:
                emit(("error", exc))

        task = asyncio.create_task(asyncio.to_thread(run))
        # 取消支持：to_thread 杀不掉线程，只能让引擎主动停——
        # 引擎需要在循环中检查 interrupt.is_set()，置位则抛异常退出
        # （corecoder 双接口方案用 achat() 原生异步，不需要这套；这里适用于
        #   引擎不愿意改造异步的场景）
        try:
            while True:
                kind, *payload = await queue.get()
                ...  # 同模式一的转发逻辑
        finally:
            task.cancel()
```

关键限制（写入文档避免踩坑）：
- `task.cancel()` **杀不掉 worker 线程**，线程会跑到 `chat()` 返回；期间事件被丢弃
- 引擎需提供中断检查点（如 `threading.Event`，循环每轮检查），否则 UI 的 interrupt 无效
- 工具执行窗口内中断必然延迟到下一轮生效

### 模式三：进程引擎（claude code CLI 等）

引擎是独立进程。两种形态：

| 形态 | 做法 | 中断 |
|------|------|------|
| 每 turn 一进程 | 子进程跑 `claude -p "<prompt>"`（可带 `--resume <id>` 续会话），解析 stdout | 杀子进程即可，无状态残留 |
| 长驻进程 | 启动时拉起子进程，用结构化协议（如 `claude --output-format stream-json`）逐事件通信 | 发中断信号（SIGINT/SIGTERM）或杀进程 |

要点：
- 子进程输出解析要处理 stderr 日志与 stdout 结构混排（错误信息进 `AgentError`）
- 会话恢复：进程引擎自带 session id（`--resume`），`load_history` 可映射为"用恢复的会话 id 启动"
- 事件粒度受限于 CLI 输出格式——拿不到工具事件就只发 `AgentTextDelta` + `AgentTurnEnd`，UI 依然可用

## 5. 注册引擎

### 5.1 内置注册（本仓库内）

`javis/engines/registry.py`：

```python
from javis.engines import register_engine, create_agent_backend, list_engines

def build_my_backend(*, model, system_prompt, cwd, max_turns,
                     tool_metadata, engine_config, **kwargs) -> AgentBackend:
    return MyBackend(...)

register_engine("my-engine", build_my_backend)
```

### 5.2 用户配置

`~/.javis/config.json`（workspace 根目录）：

```json
{
  "engine": "my-engine",
  "engines": {
    "my-engine": { "model": "deepseek-chat", "base_url": "...", "api_key": "..." }
  }
}
```

优先级：CLI `--engine` > env `JAVIS_ENGINE` > config `engine` > 默认 `corecoder`。

`engine_config` 参数即 `config["engines"]["my-engine"]`，工厂自行解析（建议同时支持环境变量回退）。

## 6. 常见坑

| 坑 | 说明 |
|----|------|
| 空流 / 无终止事件 | 引擎内部 return 了但没 yield `AgentTurnEnd`/`AgentError`——前端永远等不到 line_complete。用 try/finally 或哨兵保证恰好一次 |
| 中断后历史不合法 | assistant `tool_calls` 无对应 tool 回复 → 下次请求 400。中断路径里补全（见契约 #3） |
| `is_error` 语义混乱 | 工具自身返回的 "Error: ..." 字符串 ≠ 执行失败；`is_error=True` 仅指"工具没能执行"（未找到/参数错/抛异常） |
| 事件粒度太粗 | 一个 turn 只发一个 `AgentTextDelta` + `AgentTurnEnd` 也能工作，但前端打字机效果、工具进度条都没了 |
| 镜像 vs 引擎历史不一致 | 镜像只存文本流，工具轮永远在引擎侧。不要在镜像里塞工具消息，恢复会话时用 `load_history` 重建 |
| 阻塞事件循环 | 同步引擎直接跑在 async 上下文里会卡死整个后端。要么线程桥，要么引擎原生 async |
| 取消不响应 | host 取消后线程/子进程继续跑完整个 turn：消耗资源、状态推进、UI 已丢弃事件。提供检查点或杀进程 |
