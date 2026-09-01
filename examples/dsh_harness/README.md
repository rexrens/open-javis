# Harness Demo — dsh 主流程 × Cordis 插件系统

一个**完整流程、完整契约接口**的 agent harness 演示：参考
[deepseek-harness](https://github.com/deepseek-harness)（dsh）的主流程
（`ReactLoopAgent` / Inbox / Session 事件日志 / exclusive-parallel 工具调度 /
agent 事件钩子），用 Python 重新表达，**真实实现全部用 mock 数据**——
MockLLM 按脚本流式返回 `StreamChunk`，mock 工具返回固定文本。

整个 harness **全部由 Cordis 插件系统装配**（`javis.cordis`）：7 个插件 +
一份 `cordis.yml` 组合文件，宿主零改动即可驱动。

```
examples/dsh_harness/
├── cli.py                     # 场景运行器（text / tools / retry / steer）
└── dsh_harness/
    ├── contracts.py           # 契约面：blocks/chunks/messages/usage/failure、
    │                          #   LlmCallConfig/GenerateOptions、Agent 运行时类型、
    │                          #   工具执行类型、prompt assembly、事件名常量
    ├── session.py             # Session 事件日志（append-only、seq、derive_messages）
    ├── inbox.py               # Inbox 双队列（next-turn / next-step、splice/claim）
    ├── llm.py                 # LLM 服务契约 + BlockAssembler + 流归一化
    ├── tools.py               # ToolRegistry + execute_tool_calls（barrier/池调度）
    ├── agent.py               # ReactLoopAgent（phase 状态机 + kick/turn/step）
    ├── mock_llm.py            # MockLLM：脚本化 provider + 4 场景脚本 + steer 钩子
    ├── plugins/               # 7 个 Cordis 插件（见下）
    └── cordis.yml             # 组合文件
```

## 运行

从仓库根目录（`javis` 已装好，无需 API key）：

```bash
uv run python examples/dsh_harness/cli.py                    # 全部 4 个场景
uv run python examples/dsh_harness/cli.py --scenario tools   # 单场景

# 通用 Cordis 运行器也可以加载同一份组合（只装配、不驱动）：
uv run python -m javis.cordis.cli run examples/dsh_harness/dsh_harness/cordis.yml
```

冒烟测试：

```bash
uv run pytest tests/test_demo_harness.py -v
```

## 4 个场景

| 场景 | 演示 |
|---|---|
| `text` | 最小闭环：reasoning + text 流式 → `stop` finish → turn 完成 |
| `tools` | **exclusive barrier**（`set_note`）+ **parallel 池**（`weather`×2，上限 2）、模型顺序提交、结果回灌模型 |
| `retry` | provider 抛 `TRANSIENT` 失败 → `agent/request-error` waterfall 重试一次 → 成功（失败尝试只留 chunk、不留 assistant/message） |
| `steer` | `now()` 工具发起时经 `MockLLM.on_tool_call` 钩子**确定性注入** steering 消息 → 下一步 pre-step 认领 → 模型看到 steering |

## 接线图

```text
examples/dsh_harness/cli.py
  └─ Context（根 fiber）+ Loader（cordis.yml，依赖驱动排序）
       ├─ agent-loop-config   provide("agentLoop")      max_parallel_tool_calls=2
       ├─ system-prompt       provide("systemPrompt")   persona/context sections + 工具 schema 组装
       ├─ llm                 provide("llm")            MockLLM（$HARNESS_DEMO_SCENARIO 脚本）
       ├─ demo-tools          provide("tools")          now/weather(并行) + set_note/end_session(独占)
       ├─ middleware          ctx.on(agent/request)          改写路由 mock-mini → mock-mini-2026
       │                       ctx.on(agent/pre-step)         每步追加上下文消息
       │                       ctx.on(agent/request-error)    TRANSIENT 每步重试一次
       ├─ observer            ctx.on(agent/status, inbox/*, tools/result, turn-stopping, error)
       └─ driver              inject=[llm, tools, systemPrompt, agentLoop]
                             create Session + ReactLoopAgent
                             provide("session") / provide("agent")
              │
              └─ 宿主只认 Agent 契约：followup / steer / inject / cancel / when_idle
                   └─ turn 循环（每步）：
                        pre-step: inbox.claim + systemPrompt.assemble + agent/pre-step waterfall
                        request:  agent/request waterfall + llm.prepare_call
                                  + request/header 变更日志 + request/context
                        stream:   llm.stream → StreamChunk → BlockAssembler
                                  （error → agent/request-error waterfall：retry/throw）
                        工具:     execute_tool_calls：exclusive barrier / parallel 池
                                  （tools/execute、tools/post-execute、tools/result）
                        边界:     step/end → agent/turn-stopping(serial) → turn/end
```

## 与 dsh 的对照

| dsh | 本 demo |
|---|---|
| `ReactLoopAgent`（`packages/core/agent-loop/src/agent.ts`） | `dsh_harness/agent.py::ReactLoopAgent` |
| `Inbox`（next-turn / next-step + splice 日志） | `dsh_harness/inbox.py`（`agent/inbox/spliced` 记入 session） |
| `Session` 事件日志 + `deriveMessages` | `dsh_harness/session.py`（同一套事件词汇表） |
| `LlmRuntime.stream` / `prepareCall` / `BlockAssembler` | `dsh_harness/llm.py`（`normalized_stream` 把异常归一化为 `error`/`aborted` finish） |
| `executeToolCalls`（exclusive barrier / parallel pool / `concludesTurn` / abort 合成结果） | `dsh_harness/tools.py`（`maxParallelToolCalls` 读 `agentLoop.config`） |
| 事件：`agent/status|error|inbox/*`、`agent/pre-step|request|request-error`（waterfall）、`agent/turn-stopping`（serial） | `dsh_harness/contracts.py::Events`（javis cordis 的 emit/waterfall/serial 一一对应） |
| `StreamChunk` / `FinishReason` / `TokenUsage` / `LlmFailure` / `GenerateOptions` | `dsh_harness/contracts.py`（dataclass，命名对齐） |
| `LlmCallConfig` + `callConfigEquals` + `canonicalHeader` | `dsh_harness/contracts.py` + `dsh_harness/agent.py::_canonical_header` |

## 契约面速览（`dsh_harness/contracts.py`）

- **内容**：`TextBlock` / `ReasoningBlock` / `ToolCallBlock` / `ToolResultBlock`
- **流**：`StreamChunk` = `block-start | text-delta | reasoning-delta | tool-call-delta | block-end | usage | finish`
- **结束**：`StopFinish | ToolCallsFinish | MaxTokensFinish | AbortedFinish | ErrorFinish`
- **请求**：`LlmCallConfig`（+`call_config_equals`）/ `GenerateOptions` / `PreparedCall`（adapter defaults + retryPolicy）
- **消息**：`Message / UserMessage / AssistantMessage / ToolResultMessage`
- **Agent**：`AgentOptions` / `AgentStatus` / `AgentCancelCause` / `TurnEndReason`（5 种）/ `PreStepDecision` / `RequestErrorAction`
- **工具**：`ToolExecutionInput` / `ToolExecutionResult`（`concludes_turn` / `additional_contexts`）/ `PostToolDecision` / exclusive|parallel
- **Prompt**：`PromptSection`（persona|context）/ `PromptAssembly`（sections + tools）
- **会话**：`SessionEvents`（12 种事件类型白名单）
- **控制**：`AbortController` / `AbortSignal` / `AbortError`

## 关键语义（与 dsh 一致）

- **依赖驱动加载**：`driver` 声明 `inject=[llm, tools, systemPrompt, agentLoop]`，
  在任一服务未 ACTIVE 前保持 PENDING——组合文件的书写顺序不重要。
- **事件钩子可 veto**：waterfall 监听器不调 `next()` 即截断链路；
  `agent/pre-step` 可整步 reject（turn 以 `blocked` 结束）。
- **max-tokens sticky**：任何一步命中上限后，后续正常完成的步骤不会降级 turn 结果。
- **abort 语义**：未开始的任务记合成 error 结果（`TOOL_ABORTED_BEFORE_DISPATCH`），
  已开始的先排空再按模型顺序提交；流中断时部分组装的 block 落为 `interrupted` 消息。
- **卸载可逆**：插件 `ctx.provide` / 工具注册都是 fiber effect——卸载即回滚。

## 扩展方向

- 把 `plugins/llm.py` 换成真实 adapter（实现 `dsh_harness.llm.LLM` 契约即可，引擎零改动）。
- 接 `additional_contexts`（工具结果附带上下文注入 next-step）——契约已就位。
- HMR：`javis.cordis` 的 Loader 支持 `--watch` 热重载同一份组合。
