# javis 插件系统（Cordis 接入）

> 状态：已接入 runtime。插件 = `apply(ctx, config)` 模块 + `cordis.yml`
> 组合条目；宿主在每个会话的 `build_runtime` 中创建 Cordis `Context`、提供
> 内建服务、挂载组合并等待所有插件 settle。

## 组合文件（cordis.yml）

默认 `<workspace>/cordis.yml`（缺失时自动创建为空列表）。解析顺序：

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
| `engine` | `javis.contracts.engine.AgentEngine` 实例 | **插件提供**：`ctx.provide("engine", impl)` |
| `llm` | — | 预留，本期不接线（引擎插件用 `config` 自建 provider） |

## 引擎插件

引擎插件在 `apply` 中直接用 `ctx.get("config")` / `ctx.get("tools")` /
`ctx.get("host")` 构建 `AgentEngine` 实例并提供：

```python
# ~/.javis/my_engine.py
from javis.contracts import ENGINE_SERVICE


def apply(ctx):
    cfg = ctx.get('config')
    tools = ctx.get('tools')
    host = ctx.get('host')
    engine = build_my_engine(cfg, tools=tools.all(), host=host)
    ctx.provide(ENGINE_SERVICE, engine)
```

```yaml
# ~/.javis/cordis.yml
- id: engine
  name: './my_engine.py'
  inject: ['config', 'tools', 'host']
```

选择规则：

- 插件 settle 后宿主读 `ctx.get("engine")`；首个成功提供者生效
  （Cordis `provide` 对同名服务抛错，后续提供者 FAILED 隔离）。
- 未提供 / 不是 `AgentEngine` → 告警并回退内建 `HarnessEngine`。
- 工具快照发生在引擎插件 `apply` 内，因此**工具插件条目要排在引擎条目之前**
  （同步 `apply` 按组合顺序执行；异步 `apply` 需自行保证注册先于引擎构建）。
- 宿主随后统一执行 CLI 覆盖（`set_model` / `set_system_prompt`）与
  会话恢复（`load_messages`），插件引擎无需处理。

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

`AgentEngine` 可选实现 `set_permission_checker(checker)`（`hasattr` 探测）。
`BackendHost` 启动时优先调用它注入 TUI 的 ask/deny 权限流；旧
`engine.agent.permission_checker` 路径保留为回退。不实现任何一者的测试替身
直接跳过注入。

## 生命周期

- 启动：`build_runtime` → `Context` → 内建服务 → `Loader` 挂载组合 →
  `settle(ctx)` 等所有 fiber 收敛 → 读 `engine`。
- 退出：`run_backend_mode` / `run_print_mode` 的 finally 调用
  `await bundle.close()`：逆序 dispose 所有插件 fiber（disposer 执行、
  提供的服务撤销），异常只记日志。

## 扩展点（后续）

- `llm` 服务接线（fallback provider / 插件替换 provider）。
- HMR：Cordis `Hmr` 服务已可用，接入 runtime 需加 `--watch` 或配置开关。
- 多组合文件合并 / 目录扫描（改动集中在 `build_runtime` 的组合解析一处）。
- `engines` → `harness` 包重命名（本期所有插件可见契约已收敛到
  `javis.contracts`，改名只涉及内部 import 的机械替换）。
