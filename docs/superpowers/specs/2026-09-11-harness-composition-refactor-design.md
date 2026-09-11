# Harness 组合式重构设计

> 日期：2026-09-11
> 状态：已评审通过，待实现
> 范围：`javis/harness/`、`javis/app/runtime.py`、`javis/contracts/`、`javis/session/config.py`、测试与文档
> 明确不动：TUI（`frontend/`）、app 对外消费契约（`RuntimeBundle` 形态与 `Harness` 协议方法面）、`commands` 契约
> 注意：`javis/app/runtime.py` **会被修改**（本次重构的落点之一），改动范围见 §8

## 1. 背景与目标

当前 `build_runtime`（`javis/app/runtime.py:167`）在插件加载完成后做兜底：

- `ctx.get("engine")` 取不到、或取到的不是 `AgentEngine`，就调用
  `_build_default_engine`（`javis/app/runtime.py:111`）现场组装内建
  `HarnessEngine`；类型不对时只 `log.warning` 后静默回退（`:249-265`）。
- 默认组合文件是空列表 `[]`（`ensure_default_composition`，
  `javis/session/config.py:175-183`），所以**默认路径永远走兜底**，"插件化改造"
  在默认配置下名存实亡。
- Harness 的内部装配（llm / tools / systemPrompt / agentLoop 四个服务）藏在
  `HarnessEngine.__init__` 的私有 `Context` 里（`javis/harness/engine.py`），
  组合层看不到、改不动；`javis/harness/build.py` 又是一层独立装配入口。

目标：**组合文件成为 Harness 组装的唯一来源**。

1. 默认即插件化 —— 随发行写一份全量默认组合，默认启动就经由插件行组装。
2. 缺失即报错 —— 组合里没有 `harness` 服务行，启动直接失败并给出补救提示，
   不做任何静默兜底。
3. Harness 内部结构可组合 —— 内部四服务（llm / 工具 / 系统提示 / 循环配置）
   直接上根 context，后续迭代 harness 只改插件行与实现，不动 `app` 层。
4. 保留 `app` 消费契约 —— `RuntimeBundle`、`AgentEngine`（改名后为 `Harness`）
   的宿主可见面保持稳定。

### 非目标

- 本期不做 dsh 式补丁层（`cordis.patch.yml` 按 id 叠加）；默认文件即全量组合，
  用户直接编辑该文件。补丁层留作后续演进。
- 不自动改写已存在的空 `[]` 组合文件（可能是用户有意为之）；启动时报错并提示。
- 不重构 TUI / `app` 层结构，不改宿主与 Harness 的职责分工。`runtime.py` 的
  装配逻辑改动是本次重构的必然落点（见 §8），但不重排 app 内部职责。
- 不拆 `host` 服务：保留 `HostContext` 现形态（路径 + 会话标识 + CLI 覆盖的
  异质袋）。"覆盖项并入 config / 拆 session 服务"留作后续调整。

## 2. 术语

| 概念 | 名称 | 代码落点 |
|---|---|---|
| 产品 | **open-javis** | 仓库 / 文档 |
| 整个 Agent（对外整体） | **Harness** | 契约 `Harness`（`javis/contracts/harness.py`，由 `engine.py` 改名）；服务名 `HARNESS_SERVICE = "harness"`；实现类 `Harness`（`javis/harness/harness.py`，由 `engine.py` 改名） |
| 内部 LLM↔工具循环 | **AgentLoop** | `AgentLoop`（由 `ReactAgentLoop` 改名）；循环配置服务 `AgentLoopService`（由 `types.AgentLoop` 改名） |

伴随改名（机械替换，无行为变化）：

- `ENGINE_SERVICE = "engine"` → `HARNESS_SERVICE = "harness"`
- `AgentEngine` 协议 → `Harness`
- `HarnessEngine` → `Harness`
- `HarnessEngine.docstring` 中"engine"表述一并更新

`RuntimeBundle.engine` 的**属性名保留**（`javis/app/runtime.py:76`），仅类型
标注随协议改名 —— app / TUI 消费面零改动。

## 3. 目标结构

一个根 context，宿主服务 + 组合行提供的服务全部可见：

```
根 Context
├── config      宿主提供（根 fiber，不可撤销）
├── tools       宿主提供 —— javis 侧 ToolRegistry（宿主工具，含 metadata）
├── commands    宿主提供
├── host        宿主提供（HostContext：cwd/workspace/session_id/CLI 覆盖）
├── llm         插件行 javis.harness.plugins.llm        → LlmRuntime + adapter
├── agentTools  插件行 javis.harness.plugins.agent_tools → 面向循环的实时只读视图
├── systemPrompt 插件行 javis.harness.plugins.system_prompt
├── agentLoop   插件行 javis.harness.plugins.agent_loop  → AgentLoopService（配置）
└── harness     插件行 javis.harness.plugins.harness     → Harness 实例（驱动）
```

`Harness` 实现不再持有私有 `Context`；`self._loop_ctx` 的自建装配全部删除，
服务一律从根 context 读取。`.on(...)` 监听器（权限、request 中间件、limit、
snip）仍注册在 `Harness` 所在 context 上。

### 服务职责

- **`llm`**：`LlmRuntime` 注册 provider adapter（DeepSeek/Qwen/Kimi/Ollama…），
  从 `config` + `host` 解析 provider/model/api_key/base_url/max_tokens。
- **`agentTools`**：面向循环的 `ToolRegistry`（dsh 语义：schema 导出、
  `execution_mode`、异步 body）。**实现为宿主 `tools` 的实时视图**，而非
  构建期快照 —— 解决两个问题：
  1. 服务名冲突：宿主 `tools`（javis ToolRegistry）与循环 `tools`
     （core ToolRegistry）语义不同，同名会踩踏；
  2. 现存的构建期快照缺陷：`adapt_registry` 在插件加载**前**快照，
     后注册的工具对循环不可见。实时视图让注册顺序不再敏感。
  视图机制：读操作（`get` / `all` / `schemas` / `execution_mode`）每次委托
  宿主注册表并即时适配；`register` 转发到宿主（仍是唯一事实源）。
- **`systemPrompt`**：`HarnessPromptService` 从 `agentTools` 读取 schema 组装提示词。
- **`agentLoop`**：`AgentLoopService` 持有 `AgentLoopConfig`
  （`max_parallel_tool_calls` / `max_steps_per_turn` / `history_compressor`）。
- **`compression`**：`tools/post-execute` 中间件（工具输出截断），不 provide 服务。
- **`harness`**：驱动行 —— 构造 `Session`、`AgentLoop` 实例与 `Harness` 外壳。

## 4. 默认组合文件

`ensure_default_composition` 在 `<workspace>/cordis.yml` 缺失时写入以下全量组合
（已存在的文件一律不改写）：

```yaml
- id: llm
  name: javis.harness.plugins.llm
  inject: [config, host]
- id: agent-tools
  name: javis.harness.plugins.agent_tools
  inject: [tools]
- id: system-prompt
  name: javis.harness.plugins.system_prompt
  inject: [config, host, agentTools]
- id: agent-loop
  name: javis.harness.plugins.agent_loop
  config: {maxParallelToolCalls: 4, maxStepsPerTurn: 20}
- id: compression
  name: javis.harness.plugins.compression
- id: harness
  name: javis.harness.plugins.harness
  inject: [llm, agentTools, systemPrompt, agentLoop, config, host]
```

`inject` 即依赖契约：行只有在其列出的服务全部就绪后才 ACTIVE，缺失则在
boot 断言处报出服务名。`agent-loop` / `compression` 无依赖，配置经由行
`config` 传入（Cordis entry 原生字段）。

用户替换 Harness 的方式从"再 provide 一个 `engine`"改为：**改这一行**
（`name` 指向自己的驱动插件，或 `disabled: true` 后接自建行）。由于服务
不可重复 provide（`javis/cordis/reflect.py:151-154`），选择即替换，
不可能出现两套实现并存。

## 5. `build_runtime` = boot + 断言

`build_runtime` 的职责收敛为：

1. 提供宿主服务（`config` / `tools` / `commands` / `host`）。
2. 挂载 `Loader`，`await settle(ctx)`。
3. **入口断言**（dsh `assertEntriesLoaded` / `assertEntriesActivated` 的等价物）：
   - 有 entry 没对应 fiber → `RuntimeError`（模块名拼错等）；
   - fiber `FAILED` → `RuntimeError`，带原始异常与 entry id；
   - fiber `PENDING`（`inject` 未满足）→ `RuntimeError`，列出缺失服务名。
     *必要性*：javis 的 `settle()`（`javis/cordis/registry.py:185-209`）对
     PENDING fiber 静默放过，不补断言的话"依赖没满足"会伪装成"服务没注册"。
4. `ctx.get(HARNESS_SERVICE)`；`None` 或类型不符 → `RuntimeError`，消息含
   组合文件路径与补救提示（引导用户对照默认组合添加 `harness` 行，或删除
   空文件让默认组合重新生成）。
5. CLI 覆盖（`--model` / `--system-prompt`）仍然作用于取回的实例。

删除 `_build_default_engine`（`javis/app/runtime.py:111-164`），删除
`javis/harness/build.py`（其装配逻辑拆入插件行；仅存调用点为 runtime）。

## 6. 迁移顺序

1. **改名**：契约与实现按 §2 术语表重命名，全仓机械替换，测试同步；此步无
   行为变化，单独提交。
2. **拆插件行**：新增 `javis/harness/plugins/{llm,agent_tools,system_prompt,
   agent_loop,compression,harness}.py`；`ensure_default_composition` 写全量
   组合；`build_runtime` 改为 boot + 断言；删除 `_build_default_engine` 与
   `build.py`。
3. **Harness 去私有装配**：`Harness` 实现删除 `_loop_ctx` 自建部分，服务从
   根 context 读；sub-agent 工厂改为经 `ctx.get(HARNESS_SERVICE)` 惰性解析。
4. **测试**：`tests/test_javis/conftest.py` 的 `fake_engine_factory` 换 seam
   （改 patch `javis.harness.plugins.harness.build_harness`）；更新
   `test_missing_composition_auto_created_and_falls_back`（断言全量组合内容）、
   删除 `test_invalid_engine_service_falls_back`；新增"组合缺 harness 行 →
   `RuntimeError`"与"后注册工具对循环可见"用例。
5. **文档**：`docs/plugins.md`、`README.md` / `README.zh-CN.md` 术语与组合说明
   更新（`engine` → `harness`，默认组合为全量）。

## 7. 失败模式与错误消息

| 场景 | 行为 |
|---|---|
| 组合文件缺失 | 自动写入全量默认组合（现有行为保留） |
| 组合为空 `[]` | 断言失败 → `RuntimeError`，提示删除空文件或补齐 `harness` 行 |
| `harness` 行模块路径错 | 入口断言 → `RuntimeError`，含 entry id / 模块名 / 原异常 |
| `inject` 的服务不存在 | 入口断言 → `RuntimeError`，列出缺失服务名 |
| `harness` 行 provide 了错误类型 | `RuntimeError`，含类型名与组合文件路径 |

## 8. 影响面

### `javis/app`（本次会被修改）

- `runtime.py`：
  - 删除 `_build_default_engine`（`:111-164`）；
  - `build_runtime` 的兜底段（`:248-265`）替换为 boot 断言 + `ctx.get(HARNESS_SERVICE)`；
  - `RuntimeBundle.engine` 类型标注改名（属性名不变）；
  - 导入与 docstring 更新（`:6`、`:31`、`:37`、`:66`、`:76`、`:179-186`）。
- `backend_host.py`：仅 `:483` 一处 docstring 中的 `AgentEngine` 字样。
- `app.py` / `wire.py` / `react_launcher.py`：不动（TUI 管道，与本次无关）。

边界原则：不动 app 的职责划分、`RuntimeBundle` 字段与 `Harness` 协议方法面；
改的只是"引擎从哪来、怎么装"这段逻辑的落点（从 runtime 函数移入组合行）。

### 其余

- `javis/harness/`：`engine.py`→`harness.py` 并去私有装配；`build.py` 删除；
  新增 `plugins/` 包；`tool_adapter.py` 增加实时视图适配；
  `types.AgentLoop` 改名。
- `javis/contracts/`：`engine.py`→`harness.py`，`ENGINE_SERVICE`→
  `HARNESS_SERVICE`，`AgentEngine`→`Harness`。
- `javis/session/config.py`：默认组合内容。
- `javis/commands/`、`javis/llm/`：仅类型名 / docstring 的机械替换。
- 测试与文档。
