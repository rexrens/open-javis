# mini_dsh — cordis-only 的 dsh 精简 harness

> **从零复刻 dsh 主流程的教学精简版**（唯一外部依赖 `javis.cordis`）。
>
> 与 [`examples/cordis`](../cordis/README.md)（只讲插件系统接口的教程）互补：
> 那里学 `Context` / `Loader` / `inject` / 五种事件模式怎么调用，
> 这里看它们组合起来装配一个从零写出的 agent harness。
> 与 [`examples/dsh_harness`](../dsh_harness/README.md) 的对照：
> dsh_harness 的引擎**由插件装配**（生产 core 是 `javis.harness` 包本身）；
> mini_dsh 的 core 是**独立自包含实现**、插件只做组合根把它接起来——
> 两种"引擎 core 从哪来"的姿势，见下方对照表。

一个**完整主流程、但刻意裁剪契约面**的 agent harness 示例：参考
[deepseek-harness](https://github.com/deepseek-harness)（dsh）的主流程
（`ReactLoopAgent` / Inbox / Session 事件日志 / exclusive-parallel 工具调度 /
agent 事件钩子），用 Python 从零重新表达。`core/` 的 8 个模块与
`javis/harness` 架构层同结构、同命名、同语义，但**代码是独立复刻**——不
import `javis.harness` / `javis.llm` / `javis.contracts` 的任何符号，整个
目录可整体拷贝出去（只要环境里装了 `javis`，因为 `javis.cordis` 是唯一
外部依赖）。

## 示例矩阵（三个目录的分工）

| 示例 | 职责 | harness 从哪来 | 适合 |
|---|---|---|---|
| `examples/cordis` | 插件系统接口教程（11 章） | 无 harness 概念 | 先学插件接口 |
| `examples/dsh_harness` | 生产核心的插件装配 | `javis.harness`（生产 core） | 生产装配姿势 |
| `examples/mini_dsh` | 从零精简 core 的教学版 | 自包含 `core/`（教学精简） | 理解 harness 内部 |

一句话：**先 cordis 学接口 → 再 mini_dsh 懂 harness 内部 → 最后 dsh_harness
看生产 core 怎么装配**。mini_dsh 与 dsh_harness 的区别 = 从零精简 core
（教学）vs 生产 core 装配（生产）。

## 与 javis/harness 的同源说明

`mini_dsh/core` 与 `javis/harness` 架构层是**同一 dsh 逻辑的两个表达**：

- `javis/harness/`（生产 core）——完整契约面 + 宿主（TUI / print / backend）
  集成 + 权限/压缩/模型路由等生产 middleware；
- `mini_dsh/core/`（教学精简）——同结构、同命名、同事件词汇，但只保留
  demo 需要的最小子集（无 typert 注册 / fs-touch / scope / rank 分层等）。

两者共享同一份单一来源（dsh TS 源码），命名一一对齐（`camelCase` →
`snake_case`），因此读 mini_dsh 的核心能直接映射到生产包的对应模块。

## 目录结构

```
examples/mini_dsh/
├── cli.py                     # standalone 驱动（7 场景 / --prompt / --repl）
├── cordis.yml                 # 组合文件（8 条目）
├── providers.py               # LLM 协议两个实现 + 7 场景脚本工厂
├── core/                      # 自包含 dsh 精简 core（唯一外部依赖 javis.cordis）
│   ├── types.py               # 数据契约（blocks/chunks/messages/events/…）
│   ├── session.py             # Session 事件日志 + SessionStore（一等服务）
│   ├── inbox.py               # 双队列（next-turn / next-step）+ splice 语义
│   ├── llm.py                 # LLM 契约 + BlockAssembler + SystemPrompt + 流归一化
│   ├── tools.py               # ToolRegistry + execute_tool_calls（exclusive/parallel 调度）
│   ├── agent.py               # ReactLoopAgent 相位状态机
│   ├── compaction.py          # Compaction 服务 + snip 监听器
│   └── skill.py               # SkillRegistry + 文件系统 provider
├── plugins/                   # 8 个 Cordis 插件（组合根）
│   ├── session.py             # provide("sessions")：SessionStore
│   ├── llm.py                 # provide("llm")：ScriptedAdapter / OpenAICompatAdapter
│   ├── tools.py               # provide("tools")：now/weather(并行) + set_note/big_read(独占)
│   ├── driver.py              # inject=[sessions, llm, tools] → create Session + ReactLoopAgent
│   ├── middleware.py          # agent/request-error waterfall：TRANSIENT 每步重试一次
│   ├── skill_tool.py          # provide("skills") + skill 工具 + /<name> + 目录发布
│   ├── instructions.py        # agent/pre-step：AGENTS.md 注入 + 哈希重注入
│   └── compaction.py          # provide("compaction") + snip + 压力检查
├── skills/                    # 目录包（<name>/SKILL.md）
│   └── poetic-note/SKILL.md
└── fixtures/
    └── AGENTS.md              # instructions 场景的指令文件
```

## 接线图

```text
examples/mini_dsh/cli.py
  └─ Context（根 fiber）+ Loader（cordis.yml，依赖驱动排序）
       ├─ sessions      provide("sessions")     SessionStore（session 一等服务）
       ├─ llm           provide("llm")          ScriptedAdapter（$HARNESS_DEMO_SCENARIO 脚本）
       ├─ tools         provide("tools")        now/weather(并行) + set_note/big_read(独占)
       ├─ driver        inject=[sessions, llm, tools]
       │                create Session + provide session/systemPrompt/agentLoop/agent
       ├─ middleware    ctx.on(agent/request-error)  TRANSIENT 每步重试一次
       ├─ skill-tool    provide("skills") + skill 工具 + /<name> + 目录发布
       ├─ instructions  ctx.on(agent/pre-step)  AGENTS.md 注入 + 哈希重注入
       └─ compaction    provide("compaction") + tools/post-execute snip + 压力检查
              │
              └─ 宿主只认 Agent 契约：followup / steer / when_idle
                   └─ turn 循环（每步）：
                        pre-step: inbox.claim + systemPrompt.assemble + agent/pre-step waterfall
                        request:  agent/request waterfall + llm.prepare_call
                                  + request/header & request/context 变更日志
                        stream:   llm.stream → StreamChunk → BlockAssembler
                                  （error/aborted → agent/request-error waterfall：retry/throw）
                        工具:     execute_tool_calls：exclusive barrier / parallel 池
                                  （tools/execute、tools/post-execute、tools/result）
                        边界:     step/end → agent/turn-stopping(serial) → turn/end
```

## 运行

从仓库根目录（`javis` 已装好，离线场景无需 API key）：

```bash
uv run python examples/mini_dsh/cli.py                    # 全部 7 个场景
uv run python examples/mini_dsh/cli.py --scenario tools   # 单场景
uv run python examples/mini_dsh/cli.py --prompt "2+2"     # 真实模型（有 API key）
uv run python examples/mini_dsh/cli.py --repl             # 交互 REPL（/exit 退出）
```

provider 选择：`cordis.yml` 的 `llm.config.provider`（`scripted` | `openai` |
`auto`），可用环境变量 `MINI_DSH_PROVIDER` 覆盖（优先级：env > config > 默认
`scripted`）。`auto` 表示有 `DEEPSEEK_API_KEY`/`OPENAI_API_KEY` 走真实模型，
否则回退离线 demo。

## 7 个场景

| 场景 | 演示 |
|---|---|
| `text` | 最小闭环：reasoning + text 流式 → `stop` finish → turn 完成 |
| `tools` | **exclusive barrier**（`set_note`）+ **parallel 池**（`weather`×2，上限 2）、模型顺序提交、结果回灌模型 |
| `retry` | provider 抛 `TRANSIENT` 失败 → `agent/request-error` waterfall 重试一次 → 成功（失败尝试只留 chunk、不留 assistant/message） |
| `steer` | `now()` 工具发出前经 `ScriptedAdapter.on_tool_call` 钩子**确定性注入** steering 消息 → 下一步认领 → 模型看到 steering |
| `skills` | `skill` 工具加载 + `<available_skills>` 目录发布 + `/poetic-note` 显式调用注入 |
| `instructions` | `AGENTS.md`（"回答 ≤ 5 词"）baseline 注入 → 模型遵循约束 |
| `compaction` | `big_read` 超大工具结果 → `tools/post-execute` snip 截断 + `compaction/start|summary|end` 事件链 + shadow 压史 |

## 关键设计点

- **session 是一等服务**：宿主不直接构造 `Session`——`sessions` 服务
  （`SessionStore.create`）走 `ctx.effect` 生命周期，fiber 卸载即从 store
  移除；`driver` 只做组合。卸载顺序靠 fiber 逆序 dispose 达成：driver
  （agent）先于 sessions store 卸载 → agent 最终事件先落日志、再 detach
  session（dsh 有序 teardown 的 mini 表达）。
- **SKILL**：目录包形态 `<skillsRoot>/<name>/SKILL.md`（frontmatter 只认
  `name`/`description`）；`skill` 工具按名加载全文；用户消息首行 `/<name>`
  触发显式调用注入；`<available_skills>` 目录每会话只发布一次（skill 工具
  可见即视为已注册）。
- **memory（指令文件）**：`instructions` 插件在 `agent/pre-step` 扫描工作区
  `AGENTS.md`/`CLAUDE.md`——session 无 `agent-instructions` baseline 消息时
  注入全文，文件内容哈希变化时重注入更新消息；判重扫描跳过被 compaction
  shadow 的事件（否则 baseline 被压掉后从模型视野静默消失）。
- **history（compaction）**：`compaction` 服务 + 事件链
  `compaction/start → summary → end`；`derive_messages` 跳过
  `compaction/summary` 里 `shadowedSeqs` 标记的消息，摘要本身作为
  `user/message` 保留；规则摘要是"保留最近 N 条、丢弃部分压成一段
  `Earlier context (compacted): …` 文本"（LLM 摘要为扩展方向）；
  `make_snip_listener` 挂在 `tools/post-execute` 截断超限工具结果。
- **waterfall 可 veto**：监听器契约 `listener(payload, next)`——不调
  `next()` 即截断链路。`agent/pre-step` 可整步 reject（turn 以 `blocked`
  结束），`agent/request-error` 可认领恢复（返回 `retry`），
  `tools/post-execute` 可改写结果内容或追加上下文。
- **依赖驱动加载**：`driver` 声明 `inject=[sessions, llm, tools]`，在任一
  服务未 ACTIVE 前保持 PENDING——组合文件的书写顺序不重要。
- **import 技巧（sys.path）**：`cli.py`、`providers.py` 与每个插件在模块顶部
  把 `examples/mini_dsh/` 插进 `sys.path`，使 `from core import …` 独立于
  启动目录可用；这是 standalone 示例的可拷贝保障。

## 与 dsh 的对照

| dsh | mini_dsh core / plugins |
|---|---|
| `ReactLoopAgent`（`packages/core/agent-loop/src/agent.ts`） | `core/agent.py::ReactLoopAgent`（相位状态机） |
| `Inbox`（next-turn / next-step + splice 日志） | `core/inbox.py`（双队列 + `agent/inbox/spliced` 记入 session） |
| `Session` 事件日志 + `deriveMessages` | `core/session.py`（append-only + 白名单词汇） |
| `SessionStore`（`ctx.sessions`） | `core/session.py::SessionStore`（一等服务） |
| `LlmRuntime.stream` / `prepareCall` / `BlockAssembler` | `core/llm.py`（`normalized_stream` 把异常归一化为 `error`/`aborted` finish） |
| `executeToolCalls`（exclusive barrier / parallel pool / `concludesTurn` / abort 合成结果） | `core/tools.py`（`maxParallelToolCalls` 读 `agentLoop.config`） |
| `ctx.skills` + directory-bundle provider + tool-skill | `core/skill.py` + `plugins/skill_tool.py` |
| `agent-instructions`（工作区指令） | `plugins/instructions.py`（baseline + 哈希重注入） |
| `ctx.compaction` + `compaction/*` 事件 + tool-result-pruner | `core/compaction.py` + `plugins/compaction.py` |
| 事件：`agent/status|error|inbox/*`、`agent/pre-step|request|request-error`（waterfall）、`agent/turn-stopping`（serial） | `core/types.py::Events`（cordis 的 emit/waterfall/serial 一一对应） |
| `StreamChunk` / `FinishReason` / `TokenUsage` / `LlmFailure` / `GenerateOptions` | `core/types.py`（dataclass，命名对齐） |
| `LlmCallConfig` + `callConfigEquals` + `canonicalHeader` | `core/types.py` + `core/agent.py::_canonical_header` |

## 验证标准

```bash
uv run pytest tests/test_javis/test_mini_dsh_example.py tests/test_mini_dsh/ -v   # 示例集成 + core 单测
uv run pytest -q                                                                   # 全仓绿
uv run ruff check examples/mini_dsh tests/test_mini_dsh tests/test_javis/test_mini_dsh_example.py
```

冒烟：`uv run python examples/mini_dsh/cli.py`（7 场景全部断言通过）。

## 扩展方向

- 把 `plugins/llm.py` 换成任意实现 `core.llm.LLM` 契约的 adapter（引擎零改动）。
- compaction 摘要从规则升级为 LLM 摘要——`Compaction._pick_and_summarize` 是
  唯一接入点。
- HMR：`javis.cordis` 的 Loader 内置热重载——组合里挂 `apply = Hmr` 包装条目即可
  （CLI 暂未暴露 `--watch` 开关）。
