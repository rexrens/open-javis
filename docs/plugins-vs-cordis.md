# javis plugins 与 dsh Cordis 的主要差距

> 状态：2026-08-27
>
> 对比对象：`javis/plugins/` 当前实现与 dsh 仓库 `vendor/cordis/` 的 Cordis 核心，以及 dsh 的 Loader / HMR / 组合层。

## 1. 总体判断

`javis/plugins` 已经实现了 Cordis-like 的核心骨架：插件形态、异步 `apply`、配置校验、启动时依赖等待、服务注册、事件、disposer、状态机和级联卸载。

但它目前更像“启动时一次性加载、扁平服务仓库、手动卸载”的简化版。真正 Cordis 的核心价值不仅是启动编排，还包括：

- 依赖驱动的运行时卸载 / 重载
- 可组合的上下文树
- 服务隔离与 intercept 配置
- 完整事件中间件
- 声明式 Loader / HMR / profile

因此主要差距不在“能不能跑”，而在“运行时可组合性和生态完整度”。

## 2. 当前已具备的能力

| 能力 | javis/plugins 现状 |
|---|---|
| 插件形态 | 支持 `apply(ctx, config)`、模块级声明、对象形态、`__plugins__` 多插件 |
| 异步插件 | 支持 async `apply` |
| 配置校验 | 使用 pydantic 校验插件 `Config` |
| 依赖声明 | 支持 `inject` 启动时依赖等待 |
| 服务 | 支持 `provide` / `get` / 类型化 `get(name, Type)` |
| 事件 | 支持 `on` / `emit` / `emit_serial` |
| 清理 | 支持 `ctx.effect`、disposer、`on_close`、`on_start` |
| 生命周期 | 有 `PENDING → LOADING → ACTIVE/FAILED → UNLOADING → DISPOSED` |
| 依赖图 | 能推导 provider/consumer 图，支持拓扑排序和手动级联卸载 |
| 插件来源 | 支持全局目录和项目目录两层发现 |

相关实现：

- `javis/plugins/context.py`
- `javis/plugins/instance.py`
- `javis/plugins/registry.py`
- `javis/plugins/loader.py`

## 3. 主要差距

### 3.1 尚未接入 javis 主运行时代码

这是目前最大的差距。

- `javis/app/runtime.py` 没有调用 `load_plugins`。
- `javis/session/config.py` 没有真正解析 `plugins` 配置段。
- 插件系统目前是独立实验层，`uv run javis` 实际不会加载插件。
- 工具、命令、引擎注册表还没有作为真实服务注入插件上下文。
- `register_tool` 和 `CommandRegistry.register` 当前没有返回 disposer，无法直接像 dsh 那样用 `ctx.effect(registry.register(...))` 自动回滚。

### 3.2 缺少运行时依赖跟踪

Cordis 会在插件激活后持续跟踪注入的服务：

- provider 被卸载时，依赖它的插件也会自动卸载。
- provider 重新出现后，依赖插件会重新加载。
- 插件热替换时，旧 fiber 先 dispose，新实现再加载。

`javis/plugins` 目前只在启动阶段 `wait_for`：

- 服务消失后不会自动停掉 consumer。
- 服务重新提供后不会自动恢复 consumer。
- `unload()` 是手动触发，不是依赖驱动的运行时反应。

### 3.3 缺少 child context / fiber 树 / 组合能力

Cordis 支持：

- `ctx.plugin(child)`：在插件内部挂载子插件。
- `ctx.inject(...)`：声明依赖并在依赖就绪后运行。
- `ctx.extend()`：创建子上下文。
- `ctx.isolate()`：为某个服务创建独立作用域。
- `ctx.intercept()`：为服务提供 per-context 配置覆盖。

`javis/plugins` 是扁平实例表：

- 没有 fiber 父子关系。
- 没有子上下文。
- 没有服务隔离。
- 没有 intercept 配置。

### 3.4 PENDING 语义不同

Cordis 中：

- 插件缺依赖时保持 `PENDING`。
- provider 稍后出现时插件会自动继续加载。
- `PENDING` 是合法状态，不是错误。

javis 中：

- 缺依赖超过 `start_timeout` 后直接变成 `FAILED`。
- 之后即使 provider 出现也不会重试。

这个差异直接影响动态组合、HMR 和延迟加载。

### 3.5 服务层 API 不完整

Cordis `ctx` 是代理对象，支持：

- `ctx.serviceName` 直接属性访问。
- `ctx.set(name, value)` 更新服务。
- `ctx.accessor()` 定义计算属性。
- `ctx.mixin()` 把服务方法混入 `ctx`。
- `Service` 基类和类插件。
- 服务可用性 check。
- 重复 provide 保护。
- 可选依赖通过 `ctx.get(name)` 返回 `undefined`。

`javis/plugins` 目前只有：

- `provide`
- `get`
- 字符串服务名

并且 `PluginContext.get` 对缺失服务抛 `KeyError`，不是可选依赖语义。

### 3.6 事件系统缺少分发模式

`javis/plugins` 当前事件 API：

- `on`
- `emit`
- `emit_serial`

Cordis 还支持：

- `once`
- `parallel`
- `serial`
- `bail`
- `waterfall`
- `prepend`
- `global`
- context filter

其中 `waterfall` 是 dsh 决策和拦截的核心，例如权限审批、工具执行、模型请求拦截都依赖它。

### 3.7 缺少 restart / update / hot reload

Cordis fiber 支持：

- `fiber.await()`
- `fiber.restart()`
- `fiber.update(config)`
- `fiber.assertActive()`
- `fiber.getEffects()`
- `internal/status` 状态事件

`javis/plugins` 只有：

- `start()`
- `stop()`

没有配置更新、代码重载或 effect 诊断树。

### 3.8 Loader / 组合层差距明显

dsh 在 Cordis 之上还有完整生态：

- `cordis.yml` 声明插件组合。
- stable `id`。
- `disabled` 动态开关。
- group 嵌套。
- overlay / patch。
- profile。
- package / module specifier。
- `!!js` 计算配置。
- 文件 watcher / HMR。

`javis/plugins` 目前只有：

- `.py` 文件扫描。
- 静态 `enabled` 配置。
- 全局 / 项目两层目录。

### 3.9 错误语义不同

Cordis / dsh：

- 插件启动错误可以经 `fiber.await()` 抛给宿主。
- dsh `app-boot` 会 fail loud，启动审计失败时拒绝继续。

javis：

- `activate_all()` 从不抛出。
- 单个插件失败被隔离并记录。

这个差异可能是刻意设计，但不等于 Cordis parity。

## 4. 建议优先级

如果目标是继续向 dsh Cordis 靠拢，建议按以下顺序推进：

1. 把插件系统接入主 runtime，提供真实 `tools` / `commands` / `engines` / `config` 服务，并让注册表返回 disposer。
2. 增加运行时依赖跟踪：service provide / revoke 时通知相关 fiber 自动 stop / start。
3. 增加 child context 和组合能力：`ctx.plugin()`、`ctx.inject()`、`isolate()`、`intercept()`。
4. 补齐事件分发模式，尤其是 `waterfall` 和 `parallel`。
5. 再实现 Loader 声明式组合、配置热更新和 HMR。

## 5. 参考

- `javis/plugins/context.py`
- `javis/plugins/instance.py`
- `javis/plugins/registry.py`
- `javis/plugins/loader.py`
- `docs/plugins.md`
- dsh `vendor/cordis/src/context.ts`
- dsh `vendor/cordis/src/fiber.ts`
- dsh `vendor/cordis/src/events.ts`
- dsh `vendor/cordis/src/registry.ts`
- dsh `vendor/cordis/src/reflect.ts`
- dsh `vendor/cordis/src/service.ts`
