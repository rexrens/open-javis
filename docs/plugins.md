# javis 插件系统（Cordis 接入）

> 状态：已接入 runtime。插件 = `apply(ctx, config)` 模块 + `cordis.yml`
> 组合条目；宿主在每个会话的 `build_runtime` 中创建 Cordis `Context`、提供
> 内建服务、挂载组合并等待所有插件 settle。内置 Harness 本身也是组合行。

## 组合文件（cordis.yml）

默认 `<workspace>/cordis.yml`（缺失时自动写入全量六行组合：`llm` /
`agent-tools` / `system-prompt` / `agent-loop` / `snip` / `harness`）。
组合是 Harness 的唯一装配来源：空组合 `[]` 启动即 `RuntimeError`，没有内建
回退。解析顺序：

1. CLI `--plugins <file>`（相对当前目录解析）
2. 环境变量 `JAVIS_PLUGINS`（相对工作区根解析）
3. `config.json` 的 `pluginsFile` 字段（相对工作区根解析）
4. `<workspace>/cordis.yml`

entry 字段（Cordis Loader 原生支持）：

| 字段 | 说明 |
|---|---|
| `id` | 稳定标识（HMR/配置热更新按 id 区分） |
| `name` | 模块路径（相对组合文件目录）或点分包名 |
| `config` | 插件配置（pydantic `Config` 校验） |
| `inject` | 依赖的服务名列表（依赖满足后才 ACTIVE） |
| `provide` | 插件提供的服务名（说明性） |
| `disabled` | 保留条目但跳过挂载 |
| `group` / `isolate` | 组合加载 / 独立服务 scope |

## 内建服务

宿主在根 context 上提供以下服务（owner=根 fiber，不可覆盖、不可撤销）：

| 服务名 | 类型 | 说明 |
|---|---|---|
| `config` | `javis.session.config.JavisConfig` | 当前会话合并后的全局配置（含 `pluginsFile`） |
| `tools` | `javis.contracts.tools.ToolRegistry` | 每会话新建，预注册 7 个内建工具 |
| `commands` | `javis.commands.registry.CommandRegistry` | 与 `RuntimeBundle.commands` 同一实例 |
| `host` | `javis.contracts.host.HostContext` | `cwd` / `workspace` / `session_id` / `tool_metadata` / CLI 覆盖（`model_override` / `max_turns_override` / `system_prompt`） |

以下服务全部由 `javis.harness.plugins.*` 组合行提供（可在组合里替换/禁用）：

| 服务名 | 类型 | 提供行 |
|---|---|---|
| `llm` | `javis.llm.LlmRuntime` | `plugins.llm`：provider adapter 注册表 + 路由 |
| `agentTools` | `AgentToolView`（`ToolRegistry` 兼容视图） | `plugins.agent_tools`：宿主 `tools` 的循环侧实时视图 |
| `systemPrompt` | `HarnessPromptService` | `plugins.system_prompt`：persona + 步骤 context + 工具 schema |
| `agentLoop` | `AgentLoopService` | `plugins.agent_loop`：循环配置（并行池上限、每回合步数、压缩钩子） |
| `harness` | `javis.contracts.harness.Harness` 实例 | `plugins.harness`：Session + AgentLoop + Harness 外壳，**组合行提供** |

## 替换 Harness

内置 Harness 由组合里的 `harness` 行装配（`javis.harness.plugins.harness`）。
替换 = 改这一行：把 `name` 指向自己的驱动插件，或 `disabled: true` 后另加自建行。
服务不可重复 provide，所以不存在两套实现并存。

```yaml
- id: harness
  name: './my_harness.py'
  inject: [llm, agentTools, systemPrompt, agentLoop, config, host]
```

缺失 `harness` 行（含空组合 `[]`）→ 启动 `RuntimeError`，错误信息含组合文件路径与补救提示。

宿主随后统一执行 CLI 覆盖（`set_model` / `set_system_prompt`）与会话恢复
（`load_messages`），插件侧的 Harness 无需处理。

## 工具 / 命令插件

注册即返回 disposer，交给 `ctx.effect`，卸载自动反注册：

```python
from javis.commands.registry import Command, CommandResult
from javis.contracts.tools import Tool


class MyTool(Tool):
    name = "my_tool"
    description = "do something"
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}}

    def execute(self, **kwargs):
        return "done"


def apply(ctx):
    tools = ctx.get('tools')
    ctx.effect(lambda: tools.register(MyTool()))

    commands = ctx.get('commands')

    async def handler(args, context):
        return CommandResult(message="hello from plugin")

    ctx.effect(lambda: commands.register(Command("hello", "Say hello", handler)))
```

## 权限钩子

`Harness` 可选实现 `set_permission_checker(checker)`（`hasattr` 探测）。
`BackendHost` 启动时优先调用它注入 TUI 的 ask/deny 权限流；旧
`engine.agent.permission_checker` 路径保留为回退。不实现任何一者的测试替身
直接跳过注入。

## 生命周期

- 启动：`build_runtime` → `Context` → 内建服务 → `Loader` 挂载组合 →
  `settle(ctx)` 等所有 fiber 收敛 → `assert_entries_settled(ctx)` → 读
  `harness` 服务（缺失或类型不符 → `RuntimeError`）。
- 退出：`run_backend_mode` / `run_print_mode` 的 finally 调用
  `await bundle.close()`：逆序 dispose 所有插件 fiber（disposer 执行、
  提供的服务撤销），异常只记日志。

## 扩展点（后续）

- HMR：Cordis `Hmr` 服务已可用，接入 runtime 需加 `--watch` 或配置开关。
- 多组合文件合并 / 目录扫描（改动集中在 `build_runtime` 的组合解析一处）。
