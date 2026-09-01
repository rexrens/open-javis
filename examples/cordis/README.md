# Cordis 插件系统教程

`examples/cordis/` 是一个**只讲插件系统本身**的教程：每一章是一个独立目录
（一个 `cordis.yml` 组合 + 插件源码），演示 Cordis 对外提供的一组接口。
它不涉及任何 harness/agent 概念——那是 [`examples/dsh_harness`](../dsh_harness/README.md)
（在 Cordis 方案下一个 harness 怎么做）和
[`examples/plugin_harness`](../plugin_harness/README.md)（独立引擎如何接入宿主）
的职责。三个目录互补：**先在这里学会插件接口，再去看 harness 怎么用它**。

## 运行

从仓库根目录（`javis` 已装好）：

```bash
uv run python examples/cordis/runner.py <章节名>       # 运行一章
uv run python examples/cordis/runner.py hmr --wait     # hmr 章节需 --wait（观察热重载）
```

`runner.py` 是通用引导脚本（约 30 行）：建根 `Context` → 挂
`Loader` → `settle` → 报告 FAILED fiber → 按退出码结束（0 正常 / 1 有插件
失败 / 2 章节不存在）。每个示例都可以用同一个入口跑。

## 章节地图

| # | 章节 | 演示的 Cordis 接口 | 一句话 |
|---|---|---|---|
| 1 | `hello/` | `name` · `apply(ctx)` | 第一个插件：apply 被调用 |
| 2 | `lifecycle/` | `ctx.plugin` · `ctx.effect` · `Fiber.dispose` · `ctx.emit("app/exit")` | fiber 生命周期与可逆 effect（心跳任务 + 清理） |
| 3 | `greeter/` | `ctx.provide` · `ctx.get` · `inject`（依赖驱动加载） | 提供服务 + 消费服务 |
| 4 | `stats/` | `ctx.emit` · `ctx.on` | 事件：一个服务发事件，另一个订阅 |
| 5 | `config/` | `Config`（pydantic schema）· `apply(ctx, config)` | 插件配置：组合文件的 `config:` 字段经 schema 校验 |
| 6 | `events/` | `ctx.on` · `ctx.emit` · `ctx.parallel` · `ctx.serial` · `ctx.bail` · `ctx.waterfall` | **五种事件 dispatch 模式**：顺序/并发/bail 短路/waterfall 中间件链 |
| 7 | `service/` | `Service` 基类 · `super().__init__(ctx, name)` · `init()` · `ctx.inject` | 服务类的构造即注册 + 依赖就绪回调 |
| 8 | `scope/` | `ctx.isolate` · `ctx.extend` · `Service.filter` | 按服务名做作用域隔离（同 label 共享 scope） |
| 9 | `failure/` | `FiberState.FAILED` · runner 的错误报告 | apply 抛错：坏插件 FAILED，好插件继续跑，runner 退出码 1 |
| 10 | `hmr/` | `Hmr`（loader.hmr）· Loader 热重载 | 热模块替换：改插件文件，条目原地重挂 |

## 接口速查

Cordis 对外面（`javis.cordis`）按功能分五组：

**Context 作用域**（`javis.cordis.Context`）
`extend(meta)` 建子上下文 · `isolate(name[, label])` 隔离某个服务 ·
`intercept(name, config)` 给服务加拦截配置 · `baseUrl` 资源基准路径。

**服务仓库**（`ctx.get` / `ctx.set` / `ctx.provide` / `ctx.accessor` / `ctx.mixin`）
`provide(name, value, check)` 返回 disposer（fiber 卸载自动撤销）·
`get(name, strict=True)` 只返回提供者 fiber 已 ACTIVE 的实现。

**事件**（`ctx.on` / `ctx.once` / 五种 dispatch）
`emit`（fire-and-forget）· `parallel`（并发）· `serial`（顺序，bail 短路）·
`bail`（同步短路）· `waterfall`（中间件链，`(payload, next)`，不调 `next()` 即 veto）。

**插件/依赖**（`ctx.plugin` / `ctx.inject` / `Fiber`）
`plugin(plugin, config)` 返回可 await 的 `Fiber`（状态机 PENDING → ACTIVE /
FAILED → …，`dispose()` 逆序回滚 effect）· `inject(deps, callback)` 等依赖
齐了再跑。

**组合加载**（`javis.cordis.loader.Loader` / `Hmr` / `RegistryService`）
`cordis.yml` 条目：`name` / `config` / `inject` / `provide` / `group` /
`isolate` / `disabled`；`Hmr` 热重载。

## 章节文件注释约定

每章的插件源码顶部有一段 `"""…"""` docstring：说明这一章演示的接口、
如何读输出、以及关键机制（如 `events/` 的五模式对照表、`scope/` 的
`strict` get 语义）。先读 docstring 再跑，输出即教学。

## 约定与易踩点（教程里也示范了）

- **`apply` 可以是 async 函数**（`events/` 用它 await 并发/顺序演示）。
- **`ctx.get` 默认 `strict=True`**：同一 apply 内 get 自己刚 provide 的服务
  返回 `None`（fiber 还没 ACTIVE）——消费请用 `ctx.inject` 或等 settle
  （`scope/` 专门踩了这个坑并示范正确写法）。
- **bail 值 = 非 `None`/`False` 的返回值**（javis 无 `symbols.bail`）。
- **组合文件加载顺序无关**：`inject` 决定依赖；失败插件不影响其他插件。
