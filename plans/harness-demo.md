# Harness Demo（demo/ 文件夹，基于 Cordis 插件系统，参考 dsh 主流程）— 已完成

## Context

用户要一个 **harness demo**：
- 仓库根目录新建 `demo/` 文件夹
- **参考 deepseek-harness（dsh）主流程**设计，不基于 javis 现有 harness 实现（corecoder / examples/plugin_harness）
- 要求 **完整的流程和契约接口**：Agent / Inbox / Session / LLM / Tools 的契约面 + 完整 turn/step 驱动循环 + 全部事件钩子
- 真实实现用 **mock 数据**：MockLLM 按脚本流式返回 StreamChunk，mock 工具
- 用 **javis 的 Cordis 插件系统**（`javis.cordis`）做服务装配、事件分发、cordis.yml 组合

**已确认**：① 范围 = 完整流程（phase 状态机 + Inbox 双队列 + Session 事件日志 + parallel/exclusive 工具池 + 全部事件钩子 + MockLLM 4 场景）；② 验证 = cli 场景 + pytest 冒烟测试（`tests/test_demo_harness.py`）。

## 交付物（实际落地）

> 命名修正：demo 包最终命名为 **`demo/dsh_harness/`**（而非 `harness/`）——
> legacy 示例 `examples/plugin_harness/harness.py` 以顶层模块 `harness` 导入，
> 同进程 pytest 下 `sys.modules` 冲突导致 `test_plugin_harness_example` 回归失败，改名后消除。

```
demo/
├── README.md                      # 流程图 + 运行说明 + 与 dsh 对照表 + 契约面速览
├── cli.py                         # 入口：--scenario text|tools|retry|steer（默认全跑）+ 场景断言
└── dsh_harness/
    ├── __init__.py
    ├── contracts.py               # 契约面（dsh 命名对齐，dataclass）
    ├── session.py                 # Session 事件日志（seq、白名单、derive_messages、request_header）
    ├── inbox.py                   # Inbox 双队列（splice/claim/clear、三类回调）
    ├── llm.py                     # LLM 协议 + PreparedCall + BlockAssembler + normalized_stream
    ├── tools.py                   # ToolRegistry + execute_tool_calls（barrier/池、3 类事件钩子）
    ├── agent.py                   # ReactLoopAgent（phase 状态机 + kick/turn/step + 事件钩子）
    ├── mock_llm.py                # MockLLM（4 场景脚本 + steer_hook + adapterDefaults）
    ├── plugins/                   # 7 个插件：agent_loop_config / system_prompt / llm / demo_tools /
    │                              #   middleware / observer / driver
    └── cordis.yml                 # 组合文件（driver inject 依赖排序）

tests/test_demo_harness.py         # 10 个测试（4 场景端到端 + 6 契约语义）
```

## Steps（全部完成）

- [x] 1. `dsh_harness/contracts.py`：完整契约面（blocks/chunks/finish/usage/failure、messages、LlmCallConfig+call_config_equals+GenerateOptions、Agent 运行时类型、TurnEndReason 5 种、PreStepDecision/RequestErrorAction、工具执行类型、PromptSection/PromptAssembly、AgentLoopConfig、AbortController/Signal/Error、SessionEvents 白名单、Events 事件名）
- [x] 2. `session.py`：Session 事件日志（append/derive_messages/request_header/request_context/usage_total）
- [x] 3. `inbox.py`：Inbox 双队列（splice 语义对齐 dsh、claim、clear、inserted/claimed/discarded 回调）
- [x] 4. `llm.py`：LLM 协议 + PreparedCall（adapterDefaults/context/retryPolicy/stream 绑定）+ BlockAssembler（interrupted_blocks）+ normalized_stream（异常 → error/aborted finish）
- [x] 5. `tools.py`：ToolRegistry（effect 化注册）+ execute_tool_calls（exclusive barrier、bounded parallel 池、tools/execute + post-execute waterfall + tools/result emit、模型顺序提交、concludesTurn、abort 合成结果 `TOOL_ABORTED_BEFORE_DISPATCH`、additionalContexts → next-step）
- [x] 6. `agent.py`：ReactLoopAgent（idle/maintenance/running 状态机 + wake latch、send/followup/steer/inject/cancel/when_idle/run_maintenance、kick/turn/step、pre-step/request/request-error waterfall、turn-stopping serial、status/error emit、request/header 变更日志 + request/context、max-tokens sticky、interrupted 消息落盘）
- [x] 7. `mock_llm.py`：MockLLM（text/tool-calls/error/max-tokens 脚本、prepare_call adapterDefaults、on_tool_call 钩子）+ 4 场景脚本 + steer_hook
- [x] 8. `plugins/*`：7 个插件（agent_loop_config / system_prompt / llm / demo_tools(now·weather 并行 + set_note·end_session 独占) / middleware(3 个 waterfall 钩子) / observer(转写 + session 报告) / driver(组合根)）
- [x] 9. `dsh_harness/cordis.yml`：组合文件（driver inject=[llm, tools, systemPrompt, agentLoop]）
- [x] 10. `cli.py`：4 场景运行器（steer 经 on_tool_call 钩子确定性注入）+ 场景断言
- [x] 11. `README.md`：接线图、运行说明、与 dsh 对照表、契约面速览、关键语义
- [x] 12. `tests/test_demo_harness.py`：10 测试（4 场景 + concludesTurn + pre-step reject + max-tokens sticky + 非可重试失败 + abort 合成结果 + additionalContexts）

## Verification（实测结果）

- `uv run python demo/cli.py` → **ALL SCENARIOS OK**（text/tools/retry/steer）
- `uv run python demo/cli.py --scenario tools` → ✓
- `uv run pytest tests/test_demo_harness.py -v` → **10 passed**
- `uv run pytest` → **250 passed**（原 240 无回归，含 legacy plugin_harness 示例）
- `uv run python -m javis.cordis.cli run demo/dsh_harness/cordis.yml` → 加载成功、退出码 0
- `ruff check demo tests/test_demo_harness.py` → All checks passed

## 踩坑记录（实现期修复）

1. **Cordis effect 契约**：`ctx.effect(execute)` 在加载期执行 `execute()`、以其返回值作为卸载 disposer——最初把 disposer 当 execute 传入，导致 fiber ACTIVE 时工具注册被立即撤销。改为 `setup() → return disposer` 模式。
2. **waterfall 默认函数签名**：内层默认收到「全部 dispatch 参数 + 内层 next」——`tools/post-execute` 的默认需 `(exec, result, next)` 三参。
3. **abort 合成结果位置**：未开始调用的合成 error 结果须在 `_run_group` 内部按 `group[started:]` 落盘（dsh 语义）——caller 侧 `planned[next:]` 只覆盖未进入的组。
4. **顶层模块名冲突**：`demo/harness` 包与 legacy `examples/plugin_harness/harness.py`（顶层 `harness`）在共享 pytest 进程里 sys.modules 互踩 → 包名改 `dsh_harness`。
