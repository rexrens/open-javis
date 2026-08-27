# dsh-style agent-loop mock demo

这是一个完全自包含的 dsh 风格演示，不调用 javis 插件内核，也不依赖真实
LLM API。默认使用 scripted provider，离线即可跑通完整 agent loop。

## 运行

```bash
.venv/bin/python -m examples.agentloop_demo.harness
```

## 演示内容

- `settings.json`：插件组合声明，等价于 dsh 的 `cordis.yml`。
- `contracts.py`：typed service 契约。
- `mock_dsh.py`：极简 dsh mock runtime，只演示组合、依赖和生命周期形状。
- `plugins/`：llm、tools、session、system_prompt、agents 五个插件。
- `harness.py`：thin host，只负责加载组合并调用 `ctx.agents`。

## 与 dsh 的对应关系

| dsh | 本 demo |
|---|---|
| `cordis.yml` | `settings.json` |
| `ctx.plugin(...)` / Loader | `DshRuntime.mount_settings()` |
| `ctx.agents.create(...)` | `AgentsService.create(...)` |
| `agent.followup()` / `agent.whenIdle()` | 同名 dsh 风格方法 |
| `ctx.llm` / `ctx.tools` / `ctx.session` | typed service access |
| `inject` / `provides` | 插件模块级声明 |

## 说明

这里的 `DshRuntime` 是 mock，不实现 Cordis 的 HMR、动态依赖重载、
isolate/intercept 或 child context。它只用于展示 dsh 风格的应用组织方式。
