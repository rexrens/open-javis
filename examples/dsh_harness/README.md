# Harness Demo — dsh 主流程 × Cordis 插件系统

> **在 Cordis 插件方案下，一个 harness 可以怎么做**（应用案例）。
>
> 与 [`examples/cordis`](../cordis/README.md)（只讲插件系统接口的教程）互补：
> 那里学 `Context` / `Loader` / `inject` / 五种事件模式怎么调用，
> 这里看它们组合起来装配一个完整 agent harness。
> 与 [`examples/mini_dsh`](../mini_dsh/README.md) 的对照：
> 本示例的生产 core 是 `javis.harness`（生产核心装配）；
> mini_dsh 的 core 是**从零精简复刻**（教学）——
> 两种"引擎 core 从哪来"的姿势，见下方对比表。

一个**完整流程、完整契约接口**的 agent harness 演示：参考
[deepseek-harness](https://github.com/deepseek-harness)（dsh）的主流程
（`ReactLoopAgent` / Inbox / Session 事件日志 / exclusive-parallel 工具调度 /
agent 事件钩子），用 Python 重新表达，**真实实现全部用 mock 数据**——
MockLLM 按脚本流式返回 `StreamChunk`，mock 工具返回固定文本。

整个 harness **全部由 Cordis 插件系统装配**（`javis.cordis`）：7 个插件 +
一份 `cordis.yml` 组合文件，宿主零改动即可驱动。主流程本身（
`ReactLoopAgent` / Inbox / Session / 工具调度）就在 **`javis.harness`**（生产
引擎包本身）——生产引擎与 demo **共享同一份单一来源**，不再有重复拷贝。

```
examples/dsh_harness/
├── cli.py                     # 场景运行器（text / tools / retry / steer）
├── cordis.yml                 # 组合文件
├── mock_llm.py                # MockLLM：脚本化 provider + 4 场景脚本 + steer 钩子
└── plugins/                   # 7 个 Cordis 插件（见下）
    ├── agent_loop_config.py   # provide("agentLoop")：max_parallel_tool_calls=2
    ├── system_prompt.py       # provide("systemPrompt")：persona/context + 工具 schema
    ├── llm.py                 # provide("llm")：MockLLM（$HARNESS_DEMO_SCENARIO 脚本）
    ├── demo_tools.py          # provide("tools")：now/weather(并行) + set_note/end_session(独占)
    ├── middleware.py          # agent/request、agent/pre-step、agent/request-error 三个 waterfall
    ├── observer.py            # agent/status、inbox/*、tools/result、turn-stopping、error
    └── driver.py              # inject=[llm, tools, systemPrompt, agentLoop]
                               # create Session + ReactLoopAgent → provide session/agent
```

架构层（`javis.harness`）的契约面：`types.py`（blocks/chunks/messages/
usage/failure、LlmCallConfig/GenerateOptions、工具执行类型、事件名常量）、
`session.py`（事件日志）、`inbox.py`（双队列）、`llm.py`（LLM 服务契约 +
BlockAssembler）、`tools.py`（ToolRegistry + exclusive/parallel 调度）、
`agent.py`（ReactLoopAgent 状态机）。

## 与 mini_dsh 的对照（两种引擎姿势）

| 维度 | 本示例（dsh_harness） | examples/mini_dsh |
|---|---|---|
| 引擎 core | **javis.harness**（生产核心，完整契约面 + 宿主集成） | **从零精简 core**（自包含，唯一外部依赖 `javis.cordis`） |
| core 代码 | 生产包本身（`javis/harness/`） | `examples/mini_dsh/core/`（独立复刻，同结构同命名） |
| 插件角色 | 提供引擎的每个部件（llm/tools/systemPrompt/agentLoop 都是插件 provide） | 提供部件 + 组合根（driver 装配 ReactLoopAgent） |
| 宿主 | 自持 cli.py（4 场景） | 自持 cli.py（7 场景） |
| 定位 | 生产 core 装配（生产） | 从零精简 core（教学） |

两者的共同点：都是 `cordis.yml` 组合 + `ctx.plugin(Loader, …)` 装配、
`inject` 依赖驱动、effect 可逆卸载——插件系统本身的用法看
[`examples/cordis`](../cordis/README.md)。

## 运行

从仓库根目录（`javis` 已装好，无需 API key）：

```bash
uv run python examples/dsh_harness/cli.py                    # 全部 4 个场景
uv run python examples/dsh_harness/cli.py --scenario tools   # 单场景
```

冒烟测试：

```bash
uv run pytest tests/test_demo_harness.py -v
```

## 4 个场景

四个场景都是**脚本化回放**：`MockAdapter`（`mock_llm.py`）读环境变量
`HARNESS_DEMO_SCENARIO`，按 `scenario_script()` 的脚本每次 `stream()` 调用
吐一个响应——行为完全确定，不需要真实 LLM，跑多少遍结果都一样。所以这些场景
验证的是**循环本身的行为**（流式组装、工具调度、错误恢复、消息注入），而不是
模型输出质量。`cli.py` 的 `check()` 对每个场景做断言，全部通过才算
"scenario OK"。

| 场景 | 验证什么 | 关键机制 |
|---|---|---|
| `text` | 最小闭环（基线） | reasoning + text 流式、`stop` finish |
| `tools` | 工具调度语义 | exclusive 屏障 + parallel 池 |
| `retry` | 错误恢复 | `agent/request-error` waterfall |
| `steer` | 轮中消息注入 | inbox + step 边界认领 |

### `text` — 最小闭环

**模拟什么**：最基础的"问一句 → 答一句"，验证不用任何工具时，循环也能
走完一个完整的 turn。这是其他场景的基线——它不过，后面都没法看。

脚本只有一个响应，按流式协议依次发出：

1. reasoning block："2 + 2 is basic arithmetic; the answer is 4."
2. text block："2 + 2 = 4."
3. `stop` finish（正常结束）。

**断言验证**：流式 chunk（block-start / *-delta / block-end / usage / finish）
被 `BlockAssembler` 组装成完整消息；turn 以 `completed` 结束；turn/start 与
turn/end 成对；最终文本含答案（"4"）。

### `tools` — 工具调度语义（exclusive 屏障 + parallel 池）

**模拟什么**：模型在一轮里发起多个工具调用——其中有的必须串行（exclusive，
如"写笔记"这种要独占屏障的），有的可以并行（parallel，如查两个城市天气）；
执行完把结果回灌给模型，由模型生成最终总结。这是 dsh 主流程
（`executeToolCalls`）最核心的调度语义，本场景把它拆开来验证。

脚本是两个响应，对应两步：

- **Step 1**：模型一次返回 3 个工具调用（按模型给出的顺序）：
  1. `set_note(text=...)` —— **exclusive 模式**：调度器把它当作屏障，
     它执行期间别的工具调用不得提交；
  2. `weather(city=Paris)` —— **parallel 模式**；
  3. `weather(city=Tokyo)` —— **parallel 模式**。后两个组成并行对，
     正好打在 `max_parallel_tool_calls=2`（`agent_loop_config.py` 提供）的上限上。
  三个工具结果分别回灌成 `tool/result` 事件。
- **Step 2**：模型看到三个结果后生成总结——
  "Paris is 18°C (light rain) and Tokyo is 24°C (sunny) — bring an umbrella for Paris."

**断言验证**（本场景重点）：
- 工具调用按**模型提交顺序**执行：`set_note → weather → weather`；
- **exclusive 屏障证明**：`set_note` 的 result 必须比两个 weather result
  **更早**写进会话日志（在追加日志上比 seq）——这就是"独占是屏障"的语义保证，
  而不是靠运气；
- 总结覆盖两个城市；每个 tool/call 都有配对的 tool/result。

### `retry` — 错误恢复（agent/request-error waterfall）

**模拟什么**：请求途中 provider 侧发生瞬时故障（网络抖动、502 这类），
**由中间件接管恢复**，而不是整个 turn 直接崩掉。这是 dsh 的
"错误恢复可插拔"设计：循环自身不重试——它只把失败归一化成 terminal
`error` finish chunk，并触发 `agent/request-error` waterfall；**重试与否由
监听器决定**。demo 的 `middleware.py` 插件就是这个监听器。

脚本是两个响应，对应同一步的两个尝试：

1. **尝试 1（失败）**：mock 先流出一个半截文本增量（让中断语义真实——
   失败前确实已经有部分 chunk 了），然后抛
   `LlmError(code="TRANSIENT", message="connection reset by peer")`；
   LLM 层把它归一化成 `error` finish，循环走到 `agent/request-error` waterfall。
2. **中间件决策**（`middleware.py` 的 `on_request_error`）：失败码在可重试集合
   （`TRANSIENT`）里，且这个 (turn, step) 还没重试过 → 返回 `RetryAction`，
   同一步用脚本的下一条响应重放。**每个 step 只允许重试一次**（`retried`
   集合记录），防止无限重试。
3. **尝试 2（成功）**：返回 "Recovered after one transient provider failure — all good."

**断言验证**：
- 最终文本含恢复标记（"Recovered"）；
- middleware 的观察日志（`middleware-observed` 服务）里至少有一条 retry
  决策——证明恢复**走的是 waterfall**，而不是悄悄重掷；
- 会话里**只有一条 assistant/message**：失败的尝试只留下半截 chunk，
  不成消息、不污染日志（这是 dsh 的关键语义：失败尝试不算数）。

### `steer` — 轮中消息注入（inbox + step 边界）

**模拟什么**：agent 正在干活（两步之间），用户追加了一条指令——
"对了，再把东京天气带上"。这是 inbox/steering 机制：step 中途提交的消息
**不打断当前 step**，而是在**下一个 step 边界**被认领、进入模型上下文。

脚本是两个响应：

1. **Step 1**：模型调用 `now` 工具取时间（2026-08-31T18:00:00Z）。关键在于
   **注入时机**：`cli.py` 预先给 mock 挂了 `on_tool_call` 钩子（`mock_llm.py`
   的 `steer_hook`）——mock 即将发出这个工具调用块的那一刻，钩子调
   `agent.steer(...)` 把 steering 消息（"also include Tokyo's weather in
   your answer"）推进 agent 的 inbox。用"工具调用即将发出"做触发点，注入
   时机是**确定性的**，不用和真实模型的速度赛跑。
2. **Step 2**：step 边界处循环从 inbox 认领消息（记 `user/message` 事件），
   模型的上下文里出现 steering；最终答案同时包含时间和东京天气——
   "It is 2026-08-31T18:00:00Z, and (per your steering) Tokyo's weather is 24°C sunny."

**断言验证**：
- 最终答案体现 steering 被吸收（含 "Tokyo"）；
- **inbox 语义证明**：steering 的 `user/message` 事件的 seq **严格晚于**
  step 1 的 `step/end` seq——step 中途提交的消息只能在下一个 step 边界被
  认领，不会打断当前 step（比 seq，不靠印象）。

### 公共断言（四个场景都查）

- turn 以 `completed` 结束；
- turn/start 与 turn/end 成对；step/start 与 step/end 成对；
- 每个 tool/call 都有配对的 tool/result。

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
| `ReactLoopAgent`（`packages/core/agent-loop/src/agent.ts`） | `javis/harness/agent.py::ReactLoopAgent` |
| `Inbox`（next-turn / next-step + splice 日志） | `javis/harness/inbox.py`（`agent/inbox/spliced` 记入 session） |
| `Session` 事件日志 + `deriveMessages` | `javis/harness/session.py`（同一套事件词汇表） |
| `LlmRuntime.stream` / `prepareCall` / `BlockAssembler` | `javis/harness/llm.py`（`normalized_stream` 把异常归一化为 `error`/`aborted` finish） |
| `executeToolCalls`（exclusive barrier / parallel pool / `concludesTurn` / abort 合成结果） | `javis/harness/tools.py`（`maxParallelToolCalls` 读 `agentLoop.config`） |
| 事件：`agent/status|error|inbox/*`、`agent/pre-step|request|request-error`（waterfall）、`agent/turn-stopping`（serial） | `javis/harness/types.py::Events`（javis cordis 的 emit/waterfall/serial 一一对应） |
| `StreamChunk` / `FinishReason` / `TokenUsage` / `LlmFailure` / `GenerateOptions` | `javis/harness/types.py`（dataclass，命名对齐） |
| `LlmCallConfig` + `callConfigEquals` + `canonicalHeader` | `javis/harness/types.py` + `javis/harness/agent.py::_canonical_header` |

## 契约面速览（`javis/harness/types.py`）

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

- 把 `plugins/llm.py` 换成真实 adapter（实现 `javis.harness.llm.LLM` 契约即可，引擎零改动）。
- 接 `additional_contexts`（工具结果附带上下文注入 next-step）——契约已就位。
- HMR：`javis.cordis` 的 Loader 内置热重载——在组合里挂一个 `apply = Hmr` 的包装条目即可（CLI 暂未暴露 `--watch` 开关）。
