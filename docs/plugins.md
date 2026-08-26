# javis 插件系统

> **当前状态（2026-08-25）：独立探索层，未接入 javis 核心。**
> `javis/plugins` 内核完整可用（见 `examples/agentloop_demo/` 自包含演示），但
> `build_javis_runtime` 不再加载插件目录，核心（host/engines/commands/session）
> 不依赖插件系统。以下内建服务表 / 生命周期描述的是**插件系统自身的接线形态**，
> 未来接入核心时在 `build_javis_runtime` 重建该接线即可（`ctx.provide` 类型化
> 注册表实例，见 §内建服务）。

javis 借鉴 DeepSeek Harness 的 cordis 模型实现了轻量插件内核：
插件 = `apply(ctx, config)`，`ctx` 提供服务仓库、事件与生命周期钩子；
`PluginInstance` 状态机跟踪每个插件的生命周期（PENDING→LOADING→ACTIVE/FAILED→UNLOADING→DISPOSED）。

## 快速开始

1. 建目录 `~/.javis/plugins/`（全局）或 `<项目>/.javis/plugins/`（项目级）
2. 放入一个 `.py` 文件
3. 用 `PluginRegistry` + `load_plugins` 驱动（宿主代码接线，见 `examples/agentloop_demo/harness.py`）

> 注意：当前 javis 主程序（`build_javis_runtime`）**不会**自动加载插件；
> 插件系统的宿主是自建 demo / 未来接入代码。

## 插件形态（四种）

1. 模块级 `apply(ctx, config)` 函数
2. 模块级声明式变量：`Config`（pydantic）/ `inject` / `name` + `apply`
3. 模块级 `plugin = {"name": ..., "Config": ..., "inject": [...], "apply": ...}` 对象
4. 模块级 `__plugins__ = [...]` 列表（一个文件多个插件；每个条目是带 `name` / `Config` / `inject` / `apply` 属性的对象）

`apply` 可以返回一个 disposer（或 async disposer），插件卸载时逆序执行。

## 配置

```json
{
  "plugins": {
    "hello": { "enabled": true, "config": { "greeting": "你好" } }
  }
}
```

- 配置键是插件**声明的名字**（模块级 `name`、`plugin["name"]` 或 `__plugins__` 条目的 `name`），不是文件名
- `enabled: false` 跳过该插件的激活（`apply` 不会执行）；文件本身仍会被 import，import 失败仍会记录日志
- `config` 用插件声明的 pydantic `Config` 校验后传入 `apply` 的第二个参数
- 校验失败 → 该插件 FAILED，不影响其他插件

> 注：`config.json` 的 `plugins` 段已随核心去插件化移除；未来接入时恢复 `JavisConfig.plugins` 字段即可。

## ctx API

| 方法 | 说明 |
|---|---|
| `ctx.get(name)` / `ctx.get(name, Type)` | 跨插件服务；带 `Type` 时校验类型（pydantic `model_validate` / `isinstance`），不匹配抛 `TypeError`，未提供抛 `KeyError` |
| `ctx.provide(name, value)` | 注册服务（插件卸载时自动撤销） |
| `ctx.on(event, handler)` / `ctx.emit(event, payload)` | 事件（fire-and-forget） |
| `ctx.emit_serial(event, payload)` | 事件（等待所有 handler） |
| `ctx.effect(disposer)` / `ctx.on_close(fn)` | 卸载清理（逆序） |
| `ctx.on_start(fn)` | 应用启动钩子 |
| `ctx.config` / `ctx.logger` | 校验后的插件配置 / 独立 logger |
| `ctx.javis_config` | 完整 javis 配置（`JavisConfig`） |

内核本身不认识"工具/命令/引擎"等任何领域概念——注册表只是普通服务。
**内建服务**（宿主接线时提供，owner=None 永不撤销；未接入核心时不提供）：

| 服务名 | 类型 | 说明 |
|---|---|---|
| `tools` | `javis.engines.corecoder.tools.ToolRegistry` | 工具注册表；`register(tool)` 返回 disposer |
| `commands` | `javis.commands.registry.CommandRegistry` | 斜杠命令注册表；`register(cmd)` 返回 disposer |
| `engines` | `javis.engines.EngineRegistry` | 引擎注册表；`register(name, factory)` 返回 disposer |
| `config` | `JavisConfig` | 只读全局配置 |

> 注：`ToolRegistry` / `EngineRegistry` 类型化注册表类是插件系统的服务契约；
> 核心当前使用纯函数注册表（`register_tool` / `register_engine`），未来接入时
> 可恢复类化注册表提供为服务。

注册扩展的标准姿势是"取服务 → 注册 → 把 disposer 交给 effect"：

```python
from javis.engines.corecoder.tools import ToolRegistry
from javis.commands.registry import CommandRegistry


def apply(ctx, config):
    tools = ctx.get("tools", ToolRegistry)          # 类型校验
    ctx.effect(tools.register(MyTool()))            # 卸载时自动反注册

    commands = ctx.get("commands", CommandRegistry)
    ctx.effect(commands.register(Command("hello", "...", handler)))
```

`inject = ["service-name"]` 声明依赖：依赖服务未提供时插件停在 PENDING，提供后自动继续；超时（10s）未提供则 FAILED。

## 生命周期

```
宿主接线: ServiceRegistry + EventBus → PluginRegistry → load_plugins → 并行激活（依赖等待 → apply → ACTIVE）
卸载: registry.unload(name) → 级联卸载依赖它的插件（先卸依赖者，后卸提供者）→ DISPOSED
关闭: close_all() → 逆拓扑序停止（依赖者先于提供者）→ 撤销服务 → DISPOSED
```

`PluginRegistry` 提供依赖图编排（对齐 cordis RegistryService / dsh 的 fiber 依赖图）：

- `dependency_graph()` — 插件名 → 注入它提供的服务的插件列表。图从**运行时事实**推导（`ctx.provide` 记录的 owner + 各插件的 `inject` 声明），无需静态 `provides` 声明，插件代码零改动
- `load_order()` — 拓扑序（提供者先于依赖者）；含环时环内按注册序回退，不抛错
- `unload(name)` — 停止一个插件并**级联**停止所有注入它提供的服务的插件（传递闭包，依赖者先停）；返回停止顺序；未知/已卸载名字为 no-op
- `close_all()` — 全量关闭按逆拓扑序（依赖者先于提供者），保证依赖者的 disposer 仍能看到它注入的服务

目录来源按顺序为全局（`~/.javis/plugins/`）、项目（`<项目>/.javis/plugins/`），同名插件以后者覆盖前者。

## 设计预留

- **profile**：`~/.javis/profiles/<name>/plugins/` 未来作为第三层目录源（`javis --profile <name>`）
- **热重载**：PluginInstance 模型支持 dispose + 重建，未来加目录 watcher 即可

## 调试

- 日志：`javis.plugins`（`javis` 命名空间默认 INFO 级别可见；`-v`/`--verbose` 看 debug）
- 插件加载失败只影响该插件，不阻塞启动；失败原因在日志中
