# 插件化 standalone harness 示例

这是一个完全独立于 javis 内建 harness（`CoreCoderEngine`）的
`AgentEngine` 实现，通过 cordis 插件机制接入 javis。它演示了"单独写一个
harness"的完整姿势：harness 本体只依赖 `javis.contracts`，装配发生在插件
入口，宿主（TUI / print / backend）零改动即可驱动它。

## 方案：一个独立 harness 怎么写

四步，每步都对应一个文件：

1. **实现引擎契约**（[harness.py](harness.py)）——实现 `AgentEngine`：
   自己维护会话消息镜像、usage 累计、`AgentEvent` turn 事件流。宿主只认识
   这个契约，不知道你的循环长什么样。
2. **定义自己的 provider 层**（[providers.py](providers.py)）——引擎不碰
   OpenAI SDK 或任何 vendor，只认 `ChatProvider` 这一个抽象。示例带两个
   实现：离线 `ScriptedProvider`（demo/测试用，无需 API key）和流式
   `OpenAICompatChatProvider`（DeepSeek/Qwen/Kimi/Ollama 等）。
3. **插件入口做组合根**（[harness_plugin.py](harness_plugin.py)）——在
   `apply(ctx, config)` 里读内建服务：`config`（模型/provider 解析）、
   `tools`（快照工具列表）、`host`（cwd / workspace / session_id / CLI
   覆盖），装配 `HarnessEngine` 后 `ctx.provide(ENGINE_SERVICE, engine)`。
4. **工具/命令走 effect 注册**（[extra_tools.py](extra_tools.py)）——
   `register()` 返回 disposer，交给 `ctx.effect(lambda: ...)`，插件卸载时
   自动撤销。

组合文件 [cordis.yml](cordis.yml) 把上面串起来。工具插件条目必须排在引擎
插件之前（引擎在 `apply` 内快照 `tools.all()`）。

## 接线图

```text
build_runtime
  ├─ Context（根 fiber 内建服务，不可撤销）
  │   ├─ config    → JavisConfig
  │   ├─ tools     → ToolRegistry（7 个内建工具）
  │   ├─ commands  → CommandRegistry（与 bundle.commands 同一实例）
  │   └─ host      → HostContext（cwd/workspace/session_id/CLI 覆盖）
  ├─ ctx.plugin(Loader, cordis.yml)
  │   ├─ extra-tools    注册 workspace_note（inject: tools, host）
  │   └─ harness-engine apply 内快照 tools.all() → provide ENGINE_SERVICE
  └─ settle → ctx.get("engine") → HarnessEngine
        │
        └─ 宿主统一驱动：handle_line / AgentEvent 流
```

## 运行

从仓库根目录（javis 需已安装）：

```bash
# 离线 demo：脚本化 provider，无 API key，走完整工具循环
.venv/bin/python examples/plugin_harness/cli.py --demo

# 单发 prompt（有 DEEPSEEK_API_KEY 等时自动走真实模型）
.venv/bin/python examples/plugin_harness/cli.py --prompt "what is 2+2"

# 交互 REPL（/help /harness /exit 等斜杠命令可用）
.venv/bin/python examples/plugin_harness/cli.py

# 完整 TUI（React 前端，权限弹窗经由 set_permission_checker 注入）
.venv/bin/python -m javis --plugins examples/plugin_harness/cordis.yml
```

provider 选择：`cordis.yml` 的 `config.provider`（`auto` | `scripted` |
`openai`），也可用环境变量 `HARNESS_PROVIDER` 覆盖。`auto` 表示有 API key
走真实模型，否则回退离线 demo。

## 关键设计点

- **组合根模式**：harness 本体不知道 javis 配置长什么样，provider 选择和
  API key 解析都发生在插件入口。想换 provider 只改插件，不动引擎。
- **快照时序**：引擎拿到的是 `tools.all()` 快照（含内建 7 工具 + 插件工具），
  所以工具插件条目在组合文件里排在引擎之前；依赖排序由 cordis inject 保证。
- **权限契约**：实现可选钩子 `set_permission_checker`，`BackendHost` 启动时
  注入 TUI 的 ask/deny 回调；harness 在每次工具执行前调用它
  （`decision != "allow"` → 结果作为 error 文本回给模型）。
- **disposer 化卸载**：`register()` 返回的 disposer 用 `ctx.effect(lambda: ...)`
  包装——注意不能直接 `ctx.effect(tools.register(tool))`，那样 register 先执行、
  effect 又把 disposer 当 execute 立即调用，等于马上撤销（历史上踩过）。
- **事件流自持**：turn 内用 asyncio.Queue 桥接 provider 回调与 `AgentEvent`
  生成器（与内建 CoreCoderEngine 同款模式），消费者取消会取消 provider 任务。

## 与内建 corecoder harness 的对比

| 维度 | corecoder harness | 本示例 |
|---|---|---|
| 引擎契约 | `AgentEngine` | `AgentEngine`（同一条缝） |
| 装配 | `runtime._build_default_engine` 直构 | 插件 `apply` + `ctx.provide("engine")` |
| provider | `javis.engines.corecoder.llm` | 自持 `providers.py`，引擎 SDK-free |
| 工具来源 | `all_tools()` 全局单例 | 会话级 `ToolRegistry` 快照 |
| 权限 | 直插 `engine.agent.permission_checker` | `set_permission_checker` 契约钩子 |
| 卸载 | 无 | `bundle.close()` 逆序 dispose 插件 fiber |

## 扩展方向

- 接 `llm` 预留服务（provider 由插件提供，宿主统一注入引擎）。
- HMR（`--watch`）：改 `cordis.yml` 或插件源码热重载。
- session 事件化：把消息镜像升级为 append-only 事件日志 + 投影（dsh
  `ctx.sessions` 路线）。
