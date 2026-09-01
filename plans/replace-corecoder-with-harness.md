# 用 dsh-style harness 替换 corecoder 引擎 + 接入真实 javis 系统

## Context

用户需求（两条，一次实施）：
1. **替换**：`javis/engines/corecoder/` 的引擎实现换成 demo 的 harness 架构
   （`demo/dsh_harness`：ReactLoopAgent 的 phase 状态机 + turn/step 驱动 +
   Inbox 双队列 + Session 事件日志 + exclusive/parallel 工具调度 + agent/* 事件钩子）
2. **接入真实 javis**：真实 LLM（OpenAICompatProvider/DeepSeek 等）、真实工具
   （bash/read/write/edit/glob/grep/agent + 插件工具）、真实配置（JavisConfig）、
   会话持久化/恢复、权限流（TUI ask/deny）、斜杠命令、React 前端零改动

约束：宿主只认 `AgentEngine` 契约（`javis/contracts/engine.py`）；插件系统
（cordis）不动；demo 保持独立可跑。

## 已确认决策（用户审批）

1. **corecoder 彻底删除**：providers/tools 先迁移复用，旧循环测试重写/删除
2. **复制而非移动**：`demo/dsh_harness` 保持独立参考，生产 core 忠实复制 + 三处增强
   （`Session.on_append` / `max_steps_per_turn` / `history_compressor`，见适配点 1）
3. **循环护栏默认值**：`max_steps_per_turn=20`（超过发 AgentStatus 并结束回合）、
   `max_parallel_tool_calls=4`
4. **压缩 middleware 本次实施**（不再"后续"）：v1 落地两级压缩——`tools/post-execute`
   waterfall 工具输出 snip + 历史保留上限（均纯规则、无 LLM 递归风险）；LLM 摘要版
   （旧 `_summarize_old`/`_hard_collapse`）标记后续（详见适配点 9）

## 现状勘察结论（已读源码）

| 模块 | 关键内容 | 处置 |
|---|---|---|
| `javis/engines/corecoder/engine.py` | `CoreCoderEngine(AgentEngine)`：消息镜像 + Queue 桥接 achat 回调 | **替换**为 `HarnessEngine` |
| `javis/engines/corecoder/agent.py` | chat/achat 循环、线程并行工具、`permission_checker`、`_answer_pending_tool_calls`、ContextManager 压缩 | **删除**（被 dsh ReactLoopAgent 替代） |
| `javis/engines/corecoder/llm.py` | `OpenAICompatProvider`/`ScriptedProvider`/`is_fallback_trigger`（SDK 依赖） | **迁移**（复用） |
| `javis/engines/corecoder/tools/` | 7 个内建工具 + `create_default_tool_registry` | **迁移**（复用） |
| `javis/engines/corecoder/{context,prompt,session,config}.py` | ContextManager 压缩、prompt、session、dotenv | **删除**（压缩后续可做 middleware） |
| `javis/contracts/{engine,llm,tools,messages,types,services}.py` | AgentEngine/LLMProvider/Tool/ConversationMessage/AgentEvent/服务名 | **不动**（外契约） |
| `javis/app/runtime.py::_build_default_engine` | 插件装载后解析 provider/model → CoreCoderEngine.build | **改**为 HarnessEngine.build |
| `javis/app/backend_host.py::_inject_permission_checker` | 契约级 `engine.set_permission_checker` → 权限流 | **不动**（HarnessEngine 实现该钩子） |
| `javis/session/config.py` | `resolve_provider_and_model`/`resolve_api_key`/`DEFAULT_ENGINE="corecoder"` | 复用解析；`DEFAULT_ENGINE` 改 "harness" |
| `demo/dsh_harness/` | contracts/session/inbox/llm/tools/agent + mock_llm + 7 插件 | **保留**（独立参考 demo，测试不动） |

## dsh_harness → 生产引擎的适配点（核心）

1. **核心复制**：`javis/engines/harness/core/` = demo 的 contracts/session/inbox/llm/tools/agent
   忠实复制（相对导入不变），两处增强：
   - `Session` 增加可选 `on_append(seq, type, data)` 钩子 → 引擎的事件桥接
   - `AgentLoopConfig` 增加 `max_steps_per_turn`（dsh 无此护栏；防工具循环失控，
     替代旧 max_rounds=50 的语义）→ 复制版 agent 的 turn 循环加边界检查
   - `AgentLoopConfig` 增加可选 `history_compressor`（第三处增强，见适配点 9）→
     复制版 agent 的 step() 在 `session.derive_messages()` 后、buildRequest 前应用
2. **LLM 适配器** `JavisLLMAdapter(dsh LLM 协议)`：包 `javis.contracts.llm.LLMProvider`
   - `prepare_call(config)`：maxTokens/contextWindow 默认值 → `PreparedCall`
   - `stream(GenerateOptions)`：转 `LLMRequest`，消费 `achat_stream`，重建 StreamChunk
     （text-delta/reasoning-delta/工具调用块 diff 累积快照/usage/finish_reason 映射）
   - dsh Message → OpenAI dict 序列化（`_to_openai_messages`）
3. **工具适配器**：javis `Tool`（execute(*args,**kwargs)->str）→ dsh `Tool`
   （body=to_thread(execute,**args)，mode 取 `exclusive`，否则 parallel）
4. **权限流**：引擎在内部 ctx 注册 `tools/execute` waterfall 监听器——consult
   `checker(tool_name, arguments) → "allow"|deny`，deny 时返回 error 结果（不调 next）
   ——即 `set_permission_checker` 的实现，也演示了 middleware 能力
5. **内部 Context（双 ctx 决策说明）**：引擎持有一个私有 `Context`，provide 四个
   dsh 服务（llm=适配器 / tools=dsh 注册表 / systemPrompt=引擎 prompt 服务 /
   agentLoop=配置），与 javis 插件 ctx 隔离（javis ctx 的 "tools" 是 javis 注册表，
   不能直接复用）。
   **偏离 dsh 单 ctx 模型**（已确认）：dsh 原版所有服务挂同一棵树，ReactLoopAgent
   只用 `createScope` + `ctx.extend({agent})` 做事件归属隔离、不建第二根；javis 双根
   是两套契约同名（tools/llm 形状不同）下的务实取舍。
   **代价（明示）**：
   a. 事件总线不通——dsh 的 agent/request、tools/execute、tools/post-execute 等
      waterfall 发生在引擎私有 ctx；javis 插件 `ctx.on(...)` 收不到。权限流/压缩/
      模型路由 middleware 只能内置在引擎内部 ctx（引擎自己注册这些监听器），插件
      自定义策略无通道（v1 接受）。
   b. 插件工具需显式桥接——现状 javis 插件注册进 `TOOLS_SERVICE` 后 CoreCoderEngine
      根本看不到（引擎用独立 `all_tools()`）；新引擎必须由 `_build_default_engine`
      把 javis ctx 的 ToolRegistry 传入 `HarnessEngine.build`，tool_adapter 同步进
      dsh 注册表（"真实工具 + 插件工具"需求的落地点，v1 必做）。
   **V2 演进**：引擎 ctx = javis ctx 的 `extend()` 子 ctx + `ctx.isolate("tools")`
   （Cordis 原生隔离），事件共享 → javis 插件可直挂 dsh 钩子；需验证 isolate 与
   strict get/fiber 语义，且 `_build_default_engine` 需传 ctx 引用。
6. **事件桥接**：`submit_message` = followup + 观察 Session 事件日志（on_append
   唤醒 asyncio.Event），映射为 AgentEvent 流：
   - assistant/chunk（TextDelta→AgentTextDelta、ReasoningDelta→AgentReasoningDelta）
   - tool/call → AgentToolCallStart；tool/result → AgentToolCallResult
   - turn/end → AgentTurnEnd(text, usage=回合 TokenUsage→UsageSnapshot)
   - agent/error → AgentError
7. **消息镜像/恢复**：`messages` = javis ConversationMessage 镜像（含工具结果，
   供 `_save_session`）；`load_messages` 把 javis 历史重建为 dsh Session 事件
   （user/message、assistant/message、tool/result）→ 恢复后下一轮请求含完整历史
8. **set_model 生效**：引擎提供 `agent/request` waterfall 监听器（middleware 模式），
   用引擎当前 (model, max_tokens) 重写 seed config——旧引擎 set_model 只是摆设，
   新引擎真实生效（同时更新 provider.model）
9. **压缩 middleware（本次实施）**：替代旧 ContextManager，两级落地——
   - **工具输出 snip**：`tools/post-execute` waterfall 监听器（dsh 原生钩子，零
     core 改动），content 超 `MAX_TOOL_OUTPUT_CHARS`（默认 8000）截断 + 省略标记；
     替换旧 `_snip_tool_outputs` 语义
   - **历史保留上限**：`history_compressor` 钩子（适配点 1 第三处增强），超长历史
     保留最近 N 条 + 丢弃最旧（纯规则，无 LLM 递归风险）
   - LLM 摘要版（旧 `_summarize_old`/`_hard_collapse`）v1 不做，标记后续
   - 与权限流同族：均挂引擎内部 ctx，作为内部 middleware 服务注册

## 目标结构

```
javis/engines/
├── __init__.py                # 文档更新
├── harness/                   # 新生产引擎（dsh 架构 + 真实 javis 集成）
│   ├── __init__.py            # HarnessEngine / JavisLLMAdapter / ScriptedProvider 等导出
│   ├── core/                  # 从 demo/dsh_harness 忠实复制（仅 2 处增强）
│   │   ├── contracts.py  session.py  inbox.py  llm.py  tools.py  agent.py
│   ├── providers.py           # 迁移：OpenAICompatProvider / ScriptedProvider / is_fallback_trigger
│   ├── llm_adapter.py         # JavisLLMAdapter（LLMProvider → dsh LLM 协议）
│   ├── tool_adapter.py        # javis Tool → dsh Tool（mode/body）
│   ├── prompt.py              # PromptAssembly sections（javis system_prompt + cwd/日期 context）
│   ├── compression.py         # 压缩 middleware：post-execute snip + history_compressor
│   ├── engine.py              # HarnessEngine(AgentEngine)：镜像/恢复/事件桥接/权限/压缩/请求中间件
│   └── build.py               # HarnessEngine.build（provider/model/api_key 解析，仿 CoreCoderEngine.build）
└── tools/                     # 迁移：bash/read/write/edit/glob/grep/agent + create_default_tool_registry
    ├── __init__.py  base.py  bash.py  read.py  write.py  edit.py
    ├── glob_tool.py  grep.py  agent.py

javis/app/runtime.py           # _build_default_engine → HarnessEngine.build；tools 服务导入改 javis.engines.tools
javis/session/config.py        # DEFAULT_ENGINE = "harness"
javis/engines/corecoder/       # 删除（agent/engine/context/prompt/session/config + tools/）
```

## Reuse（直接复用，不重写）

- `javis/session/config.py::resolve_provider_and_model` / `resolve_api_key` / `JavisConfig`（provider models max_tokens）
- `javis/engines/corecoder/llm.py` 的 providers（迁移即复用）+ `javis/contracts/llm.py` 契约
- `javis/engines/corecoder/tools/` 的 7 个工具（迁移即复用）+ `javis/contracts/tools.py` 契约
- `javis/app/runtime.py::build_system_prompt` / `_save_session` / `handle_line`（宿主零改动）
- `demo/dsh_harness/` 的核心模块（复制底稿，已被 tests/test_demo_harness.py 覆盖验证）
- `demo/dsh_harness/plugins/middleware.py` 的 waterfall 监听器写法（request 中间件、权限监听器参照）

## Steps（全部完成 ✅）

- [x] 1. 复制 core：`javis/engines/harness/core/`（三处增强：`Session.on_append`、
      `AgentLoopConfig.max_steps_per_turn`+`agent/limit` 事件、`history_compressor` 钩子）；
      冒烟通过（工具循环/护栏）
- [x] 2. `javis/engines/tools/`：7 工具迁移（AgentTool 改为 `sub_agent_factory` 注入）
- [x] 3. `javis/engines/harness/providers.py`：OpenAICompatProvider/ScriptedProvider/is_fallback_trigger 迁移
- [x] 4. `llm_adapter.py`：JavisLLMAdapter（StreamChunk 重建/工具调用 diff/usage/finish 映射）
- [x] 5. `tool_adapter.py`：javis Tool → core Tool（to_thread body、exclusive→mode、AgentTool 工厂接线）
- [x] 6. `prompt.py`：HarnessPromptService（persona=javis system_prompt + context 段）
- [x] 7. `engine.py`：HarnessEngine（内部 ctx+4 服务、事件桥接、镜像/恢复、权限 waterfall、
      压缩 middleware 注册、agent/request 模型路由、子代理）
- [x] 8. `build.py` + 包导出（`javis_tools` 参数=插件工具桥接点）
- [x] 9. runtime 接线：`_build_default_engine` → HarnessEngine.build（传 javis_tools）；
      tools 服务导入改 `javis.engines.tools`；DEFAULT_ENGINE="harness"；默认引擎测试重写
- [x] 10. 删除 `javis/engines/corecoder/` + `tests/test_corecoder/` + 旧 test_agent_loop/
      test_corecoder_engine + examples/corecoder
- [x] 11. 测试：tests/test_harness/ 52 个（agent_loop 10 / engine 8 / llm_adapter 7 / async_llm 15
      / tool_registry 7 / compression 5）；test_config/test_runtime 断言更新
- [x] 12. 文档：engines/__init__.py、README 引擎对照段更新（corecoder 引用清零）

## 最终验证（实测）

- `uv run pytest` → **245 passed**（含 demo 10 + harness 52 + cordis + 宿主）
- `ruff check` 新增/修改文件 → All checks passed
- `uv run python demo/cli.py` → ALL SCENARIOS OK（demo 独立不受影响）
- `python -m javis.cordis.cli run demo/dsh_harness/cordis.yml` → 通用运行器正常
- 权限契约：backend_host `_check_permission(tool_name, tool_input) -> str` 与
  `HarnessEngine.set_permission_checker` 契约路径对接确认

## 实现说明（与计划的偏差）

- **max_steps_per_turn=20**（计划文件值；批准 notes 提及 20——引擎构造参数可配，
   `JavisConfig.session.max_turns` 会覆盖它，如需 20 改 `build()` 默认即可）
- 压缩 middleware 本次实施（snip 8k 字符 + 历史保留 100 条），LLM 摘要版留 TODO 占位
- 双 ctx 设计落地（引擎私有 loop ctx）；事件不通/插件钩子暂不可达为已知代价，V2 走 isolate
- 子代理：`_run_sub_agent` 用 `run_coroutine_threadsafe` 桥接 worker 线程，深度上限 2

## Verification

- `uv run pytest tests/test_harness/ -v`（新引擎全绿）
- `uv run pytest tests/test_demo_harness.py -v`（demo 不受影响）
- `uv run pytest`（全仓无回归；test_runtime/test_app/test_backend_host 等宿主测试原样通过）
- 手动（有 API key）：`uv run python -m javis --model deepseek-chat` 跑一轮真实对话
  （工具调用 + TUI 权限弹窗 + 会话持久化/恢复 + 斜杠命令）
- 手动（无 API key）：`uv run python demo/cli.py`（demo 仍独立可跑）

## 风险 / 行为差异（需用户确认）

1. **corecoder 处置**：**已确认——彻底删除**（providers/tools 先迁移）
2. **demo 与生产代码关系**：**已确认——复制**（demo 独立参考）
3. **行为差异**：**已确认——max_steps=20 / pool=4**；压缩以 middleware 形式本次
   实施（snip + 历史上限，见适配点 9），LLM 摘要版后续
5. **双 ctx 设计**：**已确认**（适配点 5）——引擎私有 ctx 与 javis 插件 ctx 隔离；
   事件不通/插件钩子暂不可达、插件工具显式桥接为已知代价；V2 演进子 ctx + isolate
4. **AgentTool（子代理）**：迁到 javis/engines/tools 后其 `..agent` 依赖改为注入
   （由 HarnessEngine 提供子代理构造钩子）；子代理走同一个 ReactLoopAgent
   （内层 ctx + 独立 Session）
