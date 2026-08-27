# 仿 DeepSeek Harness 的 agent-loop 示例（完整可运行版）

这个示例按 dsh 的**标准模式**写了一个能真正跑起来的 agent：
宿主（harness）很薄，只负责构建插件内核、加载插件、把用户输入交给
`agentLoop` 服务；会话日志、系统提示词、工具、LLM 与循环逻辑全部在
插件里，且每个插件都做真实工作——不是打桩打印函数名。

## 运行

```bash
uv run python -m examples.agentloop_demo.harness
```

不想触发 `uv sync`（网络慢时可能卡住）也可以直接：

```bash
.venv/bin/python -m examples.agentloop_demo.harness
```

默认使用 `scripted` 演示模型（确定性输出，无需 API key）：它会根据用户
输入决定调用 `read_file` / `bash` / `list_files`，并把工具结果总结成最终
回答。想接真实 DeepSeek 模型，把 `harness.py` 里 `PLUGINS_CONFIG` 中 llm
的配置改为 `{"provider": "deepseek"}`（需要 `DEEPSEEK_API_KEY`，可用环境
变量或 `~/.javis/.env`）。

## 目录结构

```
examples/agentloop_demo/
├── harness.py          # 薄宿主：构建内核 → 加载/激活 → 提交输入 → 关闭
├── test_agentloop.py   # 示例自带测试：会话日志折叠（"运行测试"场景会跑它）
├── plugins/
│   ├── agent_loop.py   #   agent-loop 插件：inject 依赖 + ReactLoopAgent 简化版
│   ├── llm.py          #   LLM 适配器：stream() chunk 契约 + scripted/deepseek provider
│   ├── session.py      #   会话插件：事件溯源日志 + derive_messages()
│   ├── system_prompt.py#   提示词插件：有序 section 注册表 + {{变量}} 插值
│   └── tools.py        #   工具插件：注册表 + read_file / list_files / bash
└── README.md
```

## dsh 标准模式对照

| dsh 组件 | 本示例 |
|---|---|
| `ctx = createContext(...)` | `ServiceRegistry` + `EventBus`（插件内核） |
| `ctx.plugin(AgentLoop, {...})` | `load_plugins` + `activate_all` |
| `AgentLoop`（plugin，inject 依赖） | `plugins/agent_loop.py` |
| `ReactLoopAgent.turn()` | `AgentHandle.turn()`（一轮 turn） |
| `ReactLoopAgent.step()` | `AgentHandle.turn()` 内的 step 循环 |
| `llm.stream(request)`（chunk 流） | `plugins/llm.py` 的 `LlmService.stream()` |
| `session.append(...)` / `deriveMessages()` | `plugins/session.py` |
| `systemPrompt.assemble()`（section 注册表） | `plugins/system_prompt.py` |
| `tools.snapshot()` / `execute()` | `plugins/tools.py` |
| `ctx.agentLoop.create(id, opts)` | `AgentLoopService.create()` |
| start() / dispose() | `ctx.on_start` 钩子 / disposer |

关键点：

1. **宿主薄、插件厚**：`harness.py` 不 import 任何插件模块，只通过
   `services.get("agentLoop")` 拿服务、提交用户输入。循环怎么跑、
   事件怎么落库，全由 `agent_loop` 插件负责。
2. **依赖注入**：`agent_loop` 声明 `inject = ["llm", "session", "tools",
   "system_prompt"]`，内核保证这四个服务齐备后才激活它（依赖等待）。
3. **配置校验**：`agent_loop` / `llm` / `system_prompt` 声明 pydantic
   `Config`，内核在进入 LOADING 前校验，校验后的配置对象就是 `apply`
   的第二个参数。
4. **事件溯源会话**：`turn/start`、`step/start`、`assistant/chunk`、
   `assistant/message`、`tool/result`、`turn/end` 全部是日志事件；每步
   请求的消息列表由 `session.derive_messages()` 折叠生成。
5. **LLM 流式契约**：`LlmService.stream()` 是 async generator，逐块产出
   `{"type": "text" | "tool-call" | "usage"}`；`agent_loop` 把每块先落
   日志再折叠成 assistant 消息（dsh 的 `BlockAssembler` 思路）。
6. **工具循环**：assistant 消息含 tool-call → 执行 `tools.execute()` →
   `tool/result` 写回会话 → 下一 step；无 tool-call → `turn/end` 结束。
7. **事件总线**：turn 结束广播 `agent/turn-end`，`session` 插件监听并
   打印真实数据（事件条数），演示插件间通信。
8. **卸载**：`close_runtime` 逆序执行 disposer；服务/监听器由内核自动撤销。

## 输出怎么读

- `[harness]` 前缀的行来自宿主，解说阶段与最终结果。
- `[llm]` / `[agent_loop]` / `[session]` 前缀的行来自插件，是真实工作
  的副作用输出（provider 选择、启动就绪、turn 结束统计）。
- 会话日志部分打印的是 `session` 插件里真实追加的事件（事件溯源），
  不是解说文案。

## 与 dsh / javis 真实实现的关系

- 本示例的宿主对应 javis 的 `build_javis_runtime` + `handle_line`
  （`javis/app/runtime.py`）以及 dsh 的宿主组合。
- `AgentHandle.turn()` 对应 dsh 的 `ReactLoopAgent`；`_collect()` 对应
  dsh 的 `BlockAssembler` + 逐 chunk 落日志。
- 插件的四种书写形态见 `docs/plugins.md`；本示例全部使用模块级
  `apply(ctx, config)` 形态，并演示 `inject` / `Config` / `ctx.provide` /
  `ctx.on_start` / disposer / `ctx.on` 的完整组合。
