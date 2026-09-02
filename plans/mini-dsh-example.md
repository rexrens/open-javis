# examples/plugin_harness → examples/mini_dsh：cordis-only 的 dsh 精简 harness

Status: **ready for review**

## Context

`examples/plugin_harness` 现在与 javis 形成依赖关系（import javis 9 类模块：
`contracts.*` / `session.config` / `session.credentials` / `commands.registry` /
`app.runtime`），角色是"独立实现的 `AgentEngine` 接入 javis 宿主"
（`ctx.provide(ENGINE_SERVICE, ...)`、`python -m javis --plugins` 可挂载）。

用户反馈（2026-09-02）：plugin_harness 与 javis 形成依赖关系，想让它按**真实 dsh
的逻辑**做一个**精简版本**，不依赖 javis 的任何东西，**仅仅复用 cordis**。

相关事实（探索确认）：

- `javis/cordis/` 内部零 javis import（除 javis.cordis 自身）——是一个干净的 cordis
  port，可独立复用。
- `javis/harness/` 的架构层（`types.py`/`agent.py`/`tools.py`/`llm.py`/`session.py`/
  `inbox.py`）本身已是 cordis-only 的 dsh port（不 import javis 其他模块），~2400 行；
  `engine.py`/`build.py`/`tool_adapter.py`/`compression.py`/`prompt.py` 是 javis 集成壳。
  这就是"真实 dsh 逻辑"在仓库内最直接的参照物。
- cordis 驱动姿势见 `examples/cordis/runner.py`：`ctx.plugin(Loader, {"file": ...})`。
- 引用 plugin_harness 的地方：`examples/cordis/README.md`（教程矩阵）、
  `examples/dsh_harness/README.md`（对照表）、`tests/test_javis/test_plugin_harness_example.py`
  （1 个经 `build_runtime` 的集成测试）。

## 决策（用户确认 2026-09-02）

- **Q1 → 完全 standalone，脱离 javis 宿主**：放弃 AgentEngine 契约 / ENGINE_SERVICE /
  `--plugins` 挂 javis TUI 的接入故事；新示例自带 CLI，唯一外部依赖 `javis.cordis`。
- **Q2 → 语义保真精简版（~1.5k 行量级）**：dsh 语义精髓全保留——session 事件日志 +
  inbox/steer、waterfall 钩子（pre-step/request/request-error/turn-stopping）、
  exclusive/parallel 工具调度、流式 chunk + BlockAssembler、失败归一化 + retry；
  砍掉 javis 特有层；类型面大幅合并。
- **Q3 → 目录改名 `examples/plugin_harness` → `examples/mini_dsh`**（git mv，同步更新
  引用与测试文件名）。
- **Q4（自定，规格中可改）**：demo 场景保留 4 个的精简版（text 闭环 / tools 调度 /
  retry 恢复 / steer 注入）——core 含 inbox 就该有 steer 的验证；断言比
  dsh_harness 精简（每场景 2–4 条）。
- **Q5（用户质疑后修正）**：session 必须插件化、按真实 dsh 原样——dsh 里 session 是
  一等 cordis 服务（`packages/core/session::SessionStore extends Service`，`ctx.sessions`；
  `create()` 走 fiber effect，`announce()` 发 `session/created`；agent 工厂把 session+agent
  生命周期折进有序 teardown 链）。原设计 driver 直接构造 Session 是 dsh_harness 的 port
  简化，不是 dsh 原样。修正为：**轻量 SessionStore 服务**（~80 行）：core/session.py =
  Session + SessionStore（cordis Service，名 `"sessions"`）；plugins/session.py provide
  store；driver 从 `store.create()` 取 session。

## 目标形态

### 目录结构

```
examples/mini_dsh/                  # 由 examples/plugin_harness git mv 而来
├── README.md                       # 全重写：新定位（见"示例矩阵"）
├── cordis.yml                      # 组合文件（5 个插件条目）
├── cli.py                          # standalone driver：demo 场景（带断言）/ 单发 prompt / REPL
├── core/                           # ★ 自包含精简 dsh core（唯一外部依赖 javis.cordis）
│   ├── __init__.py                 # 导出核心符号
│   ├── types.py                    # 数据契约（合并精简，目标 ~260 行）
│   ├── session.py                  # Session（事件日志 + derive_messages）+ SessionStore
│   │                               #   （cordis Service，~160 行）
│   ├── inbox.py                    # next-turn/next-step 双队列（~70 行）
│   ├── llm.py                      # LLM 服务契约 + BlockAssembler + chunk 归一化（~150 行）
│   ├── tools.py                    # ToolRegistry + exclusive/parallel 调度（~200 行）
│   └── agent.py                    # ReactLoopAgent 精简状态机（~330 行）
├── plugins/                        # cordis 插件（装配层）
│   ├── session.py                  # provide("sessions")：SessionStore（dsh 原样：一等服务）
│   ├── llm.py                      # provide("llm")：从 providers.py 选 adapter（scripted/openai）
│   ├── tools.py                    # provide("tools")：demo 工具（now/weather/set_note…）
│   ├── middleware.py               # waterfall 演示：agent/request-error 重试（可 veto 证明）
│   └── driver.py                   # 组合根：store.create() → ReactLoopAgent → provide agent
└── providers.py                    # ScriptedAdapter（4 场景工厂）+ OpenAICompatAdapter
```

代码规模目标：core ~1.2k + 外围（plugins/providers/cli）~0.7k ≈ **1.8k 行以内**
（session 插件化后 core 上浮 ~70 行，从 types/agent 的文档与冷门分支里找补）。

### core 精简裁切清单（参照 javis/harness 架构层）

| javis/harness | mini_dsh 处理 |
|---|---|
| `types.py` 730 | 保留 dsh 词汇：block（Text/Reasoning/ToolCall/ToolResult）、StreamChunk、5 种 finish、Message 族、usage、事件名常量、GenerateOptions/LlmCallConfig/AgentOptions；砍 javis 扩展（history_compressor 等）与冷门类型，dataclass 文档合并 |
| `agent.py` 692 | 保留 phase 状态机（idle/maintenance/running）+ turn/step 主循环 + 四个 waterfall/serial 钩子 + abort；砍 javis 引擎桥接相关分支，max-tokens sticky 最小实现 |
| `tools.py` 390 | 保留 ToolRegistry + exclusive/parallel 调度 + concludes_turn + abort 合成结果；砍 javis tools 适配扩展 |
| `llm.py` 278 | 保留 LLM 服务协议（prepare_call/stream）+ BlockAssembler + 失败归一化（error/aborted finish）；不引入 javis.llm 的 LlmRuntime 注册表/discovery/waterfall |
| `session.py` + `inbox.py` | 保留事件日志/derive_messages/双队列；砍 on_append 宿主钩子；**新增**
  SessionStore（cordis Service，dsh 原样）：create（fiber effect 生命周期）/
  get/announce（emit `session/created`）；砍 store 的 typert 注册、fork/seed、
  surface 折叠等 dsh 高级特性 |
| engine/build/compression/tool_adapter/prompt | 整层删除（javis 集成壳）；systemPrompt 服务并入 llm 插件或 driver 内提供（精简为普通字符串，不做 sections） |

命名对齐：模块名与类名沿用 javis/harness（即 dsh TS 源码的命名），保证与
`examples/dsh_harness` 的对照表可读。**代码是独立复刻**（copy + trim + 精简），不是
import——mini_dsh 对 javis 的依赖只有 `javis.cordis`。

### 装配模型（cordis，5 插件）

- 服务名沿用 dsh 词汇：`sessions` / `llm` / `tools` / `agentLoop` / `agent`。
- `cli.py` 引导 = `Context` + `ctx.plugin(Loader, {"file": cordis.yml})`（同
  examples/cordis/runner.py 姿势），宿主只认 `agent` 服务契约
  （followup / steer / inject / cancel / when_idle）。
- session 插件 provide `"sessions"`（SessionStore）——dsh 原样：session 是
  一等服务，宿主不直接构造 Session。
- driver 插件 `inject=[sessions, llm, tools]`：
  `session = ctx.get("sessions").create(id?, cwd=…)`（creation 走 store 的
  fiber effect）→ `ReactLoopAgent(ctx, session.id, AgentOptions(provider=…, model=…))`
  → `provide("agent")`；同时 `provide("agentLoop")`：
  `AgentLoopConfig(max_parallel_tool_calls=2)`（tools 场景的并行池上限；
  core/tools 是运行时 `ctx.get("agentLoop")`，不需要 load-time inject）。
- 卸载顺序：driver（agent）先于 session store 卸载 → agent 最终事件先落日志、
  再 detach session——与 dsh 的有序 teardown 语义一致（dsh 是把 session+agent
  折进同一条 effect 链；mini 版靠 fiber 逆序卸载达到同效果，README 言明简化）。
- middleware 插件的 steer 触发：steer 场景由 ScriptedAdapter 在即将发出 tool-call
  block 的时机调 `agent.steer(...)`（同 dsh_harness mock_llm 的 steer_hook——注入
  时机确定性，不与真实模型赛跑）。
- middleware 插件挂 agent/request-error waterfall：TRANSIENT 每 step 重试一次。
- import 技巧：cordis Loader 按文件路径加载 `plugins/*.py`，插件内
  `sys.path.insert(0, <examples/mini_dsh>)` 后 `import core…`（沿用现
  harness_plugin.py 的 `_DIR` 手法）；`cli.py` 直接跑时同目录包结构天然可 import core。

### provider 形态（providers.py）

- `ScriptedAdapter`：离线确定性模型，`stream(GenerateOptions) → AsyncIterator[StreamChunk]`
  按脚本吐出（chunk 词汇直接是 core/types 的 StreamChunk，不再有自定义 ChatProvider
  中间抽象）；内置 4 场景工厂（text/tools/retry/steer），同 dsh_harness 的
  `$HARNESS_DEMO_SCENARIO` 环境变量选场景。
- `OpenAICompatAdapter`：openai SDK → StreamChunk（流式累积 tool-call delta、usage、
  失败抛 LlmError 由 core 归一化）；真实模型可跑。

### CLI / 运行

```bash
uv run python examples/mini_dsh/cli.py                # 全部 demo 场景（带断言）
uv run python examples/mini_dsh/cli.py --scenario tools
uv run python examples/mini_dsh/cli.py --prompt "2+2" # 有 API key 走真实模型
uv run python examples/mini_dsh/cli.py --repl         # 交互
```

- 场景断言 = 检查 turn/start↔turn/end 成对、finish 类型、工具执行顺序
  （exclusive 屏障 vs parallel 池，比 seq）、retry 恢复标记、steer 消息在 step 边界
  被认领（比 seq）。cli 退出码表达成败。

### 测试

- `tests/test_javis/test_plugin_harness_example.py` **改名** `test_mini_dsh_example.py`，
  去掉 `build_runtime`：直接驱动 cordis 装配 + agent 契约跑场景断言
  （import 的是 examples/mini_dsh 代码 + javis.cordis，不经 javis 宿主）。
- 全仓 pytest 保持绿色（当前基线 ~250 passed，工作区有未提交改动，基线以当下为准）。

### 引用更新

- `git mv examples/plugin_harness examples/mini_dsh`；删除旧文件
  （harness.py / harness_plugin.py / extra_tools.py / providers.py 重写）；
  新目录按上述结构。
- `examples/cordis/README.md`：教程矩阵 plugin_harness 条目 → "mini_dsh：cordis-only
  的 dsh 精简 harness（core 自包含、可整体拷贝）"。
- `examples/dsh_harness/README.md`：对照表第三列改为"核心自包含在 examples 内、
  零 javis 依赖"；两个示例的区分 = 生产 core 装配 vs 从零精简 core。
- `examples/mini_dsh/README.md`：示例矩阵定位 + 接线图 + 运行方式 + 与 dsh_harness/
  cordis 的对照。

## 示例矩阵（重写后定位）

| 目录 | 角色 | 引擎从哪来 |
|---|---|---|
| `examples/cordis` | cordis 插件系统接口教程 | —（只讲 Context/Loader/inject/事件） |
| `examples/mini_dsh` | 用 cordis 从零写一个 dsh 风格 harness | **core 自包含**：examples 内精简复刻（仅依赖 javis.cordis） |
| `examples/dsh_harness` | javis 生产引擎的装配演示 | **javis.harness** 生产核心（7 插件装配） |

## 验证标准

1. `uv run python examples/mini_dsh/cli.py` 4 场景全 OK（退出码 0）。
2. `uv run pytest tests/test_javis/test_mini_dsh_example.py -v` 通过。
3. 全仓 pytest 通过（~250）。
4. `ruff` 改动文件零新增（全仓历史遗留 115 个 I001 不算）。
5. grep 证实 examples/mini_dsh 下除 `javis.cordis` 外无任何 `javis.*` import。
6. 无悬空引用：examples/cordis、examples/dsh_harness、docs 中不再有
   `plugin_harness` 字样（plans/ 历史计划除外，不追改）。

## 风险与备注

- 行数预算：若实现超 1.8k，优先裁场景断言长度 / ScriptedAdapter 脚本文本，
  不裁语义（先砍 steer 场景的话需用户点头）。
- javis/harness 架构层与 mini_dsh core 会部分同源（结构相似）——这是刻意的
  （同一 dsh 逻辑的两个表达：生产 core vs 教学精简），README 对照表中言明。
- 现工作区有用户未提交改动（examples/dsh_harness 等 5 文件 + plugin_harness
  README），git mv 与引用更新需与用户改动协调；`uv run` 前缀已由用户统一。

## 未决（实现计划前可再调）

- demo 默认全跑 4 场景（含 steer）；README 提供 `--scenario` 单选。
- 是否在 cli.py 提供 REPL（原 plugin_harness 有）——默认保留最简版。
