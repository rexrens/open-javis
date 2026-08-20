# javis 插件系统设计

日期：2026-08-21
状态：已确认（brainstorming 完成，待实施）

## 1. 背景与目标

javis 目前的扩展能力是三个独立注册表/静态列表：引擎注册表（`javis/engines/registry.py` 的 `register_engine`）、斜杠命令注册表（`javis/commands/registry.py`）、**静态**工具列表（`corecoder/tools/__init__.py` 的 `ALL_TOOLS`）。`start_runtime` / `close_runtime` 生命周期钩子是 no-op。没有统一的加载、生命周期、配置注入机制。

README 与 TODO.md（阶段 3，核心诉求）规划：借鉴 DeepSeek Harness（`dsh`）的 **"everything is a plugin"** 理念，实现插件化管理。dsh 基于 Cordis 框架：插件 = `apply(ctx, config)`，Context 是服务仓库，`inject` 声明依赖（DI），effect/disposer 管理生命周期，Fiber 状态机跟踪每个插件实例。

目标：为 javis 实现一个 **Python 手写的轻量 cordis-like 插件内核**——加载器（本地目录扫描）、生命周期（状态机 + 异步 apply + disposer 清理）、配置注入（pydantic）、服务仓库（provide/get）、事件（on/emit）。MVP 先把工具、命令、生命周期三类扩展点插件化；引擎与 LLM provider 在设计上覆盖、暂不迁移。

## 2. 已确认的决策

| 决策点 | 结论 |
|--------|------|
| 插件化范围 | **框架全量，MVP 先做核心**：API 设计覆盖工具/命令/生命周期/引擎/LLM provider 五类扩展点；MVP 落地工具 + 命令 + 生命周期，引擎/provider 保持现有注册表、后续迁移 |
| 插件形态 | **Cordis 风格 ctx/inject/effect**，Python 手写轻量内核（不引入现成 DI 框架） |
| 内核模型 | **保留 dsh 的 Fiber 状态机模型**，改名 **`PluginInstance`**（fiber 是协程术语，实际职责是生命周期跟踪器，PluginInstance 语义更准） |
| 插件来源 | **本地目录优先**：`~/.javis/plugins/`（全局层）+ `<project>/.javis/plugins/`（项目层，同名覆盖全局） |
| 热重载 | **不做**。启动时一次性加载；fiber/instance 模型为将来热重载铺路（加 watcher + dispose/rebuild 即可，内核零改动） |
| profile | **设计预留**：加载器抽象为"目录源列表"，profile 后续 = 往列表插一个目录（`~/.javis/profiles/<name>/plugins/`）+ 配置覆盖层。MVP 只做两层 |
| 工具注册表化 | **随 MVP 一并做**（TODO 阶段 2 前置），3 处引用点改动 |
| 运行时实例命名 | `PluginInstance`（状态机为骨架，职责 = 状态 + 校验配置 + 子上下文 + start/stop） |

## 3. 架构总览

新增 `javis/plugins/` 包，位于 runtime 与扩展点之间：

```
javis/plugins/
  context.py    PluginContext — 服务仓库（provide/get）+ 事件（on/emit）+ effect/on_close/on_start
  instance.py   PluginInstance — 状态机 + async start/stop + 依赖等待（对齐 dsh Fiber）
  registry.py   PluginRegistry — 插件表、收敛/超时、list_plugins
  loader.py     目录源扫描 + importlib 动态加载 + 元数据提取 + 配置注入
  errors.py     PluginError / PluginConfigError / PluginDependencyError
  __init__.py   公开 API
```

```
build_javis_runtime (async)
  │ 1. loader 扫描目录源列表（~/.javis/plugins → <project>/.javis/plugins）
  │ 2. importlib 加载 → 提取元数据（name/Config/inject/apply）
  │ 3. 创建 PluginInstance（全部 PENDING）
  │ 4. await asyncio.gather(所有 instance.start())   ← 并行激活：依赖等待 → apply → ACTIVE
  │ 5. 快照工具注册表 → 传给 corecoder backend factory
  │ 6. 命令注册表 = 内建 + 插件命令
start_runtime：保留为应用级钩子（插件可注册 on_start 后台任务）
close_runtime (async)
  │ 7. await asyncio.gather(所有 instance.stop())    ← 逆序执行 disposers → DISPOSED
```

## 4. 插件形态（对齐 dsh export 约定，四种）

```python
# ① 模块级 apply 函数（最常见）
def apply(ctx: PluginContext, config: Config) -> Callable | None:
    ctx.register_tool(MyTool())
    return disposer          # 可选：卸载清理

# ② 模块级声明式变量
Config = MyConfig            # pydantic model；None/缺省 = 无配置
inject = ["tools"]           # 依赖服务名列表
name = "my-plugin"           # 可选，默认模块名

# ③ 模块级 plugin 对象
plugin = {"name": ..., "Config": ..., "inject": [...], "apply": ...}

# ④ 模块级 __plugins__ 列表（一个文件导出多个插件）
__plugins__ = [PluginA, PluginB]
```

- `apply` 支持 `async def`（插件可 await 初始化：连接 MCP、初始化 SDK）
- 插件名唯一性：模块内显式 `name` > 模块名；项目层同名插件覆盖全局层

## 5. PluginContext（服务仓库 + 事件 + 生命周期钩子）

- **服务**：`ctx.provide(name, value)` 注册、`ctx.get(name)` 获取；服务生命周期绑定插件——插件卸载时自动撤销其提供的服务
- **内建服务**（MVP 五类）：

| 服务名 | 内容 |
|---|---|
| `tools` | 工具注册表（register_tool / get_tool / all_tools 快照） |
| `commands` | 命令注册表（register_command） |
| `engines` | 引擎注册表封装（register_engine，薄封装） |
| `config` | 只读全局 JavisConfig |
| `logger` | 插件独立 logger（`javis.plugins.<name>`） |

- **扩展点快捷方式**：`ctx.register_tool(tool)`、`ctx.register_command(cmd)`、`ctx.register_engine(name, factory)` 底层写入对应服务
- **事件**：`ctx.on(name, handler)`（返回取消函数，插件卸载自动移除）、`ctx.emit(name, payload)`（fire-and-forget）、`ctx.emit_serial(name, payload)`（等待所有 handler 完成）。MVP 只做这两种模式；waterfall/parallel/bail 后续
- **生命周期钩子**：`ctx.effect(disposer)` / `ctx.on_close(fn)` 注册卸载清理（逆序执行）；`ctx.on_start(fn)` 注册应用级启动钩子（start_runtime 时执行，可 async）
- **事件命名**：插件间通信用自由字符串（`"agent/turn_end"` 风格）；javis 内部 AgentEvent 流（引擎→前端协议）MVP 不暴露给插件

## 6. PluginInstance（状态机 + 异步生命周期）

**职责边界**（对齐 dsh Fiber 源码注释：tracks dependency state, validated config, lifecycle effects, and cleanup）：状态机为骨架，其余职责都是挂在状态转换上的动作。插件自身逻辑（apply 内部）不属于 Instance——Instance 只负责"在正确时机调用它、记录状态、卸载时清理它"。

```python
class PluginState(enum.Enum):
    PENDING = ...    # 等待 inject 依赖就绪
    LOADING = ...    # 正在执行 apply(ctx, config)
    ACTIVE = ...     # 加载完成，提供服务/工具/命令
    FAILED = ...     # 配置校验失败 / apply 抛异常（单独失败，不影响其他插件）
    UNLOADING = ...  # 正在逆序执行 disposers
    DISPOSED = ...   # 已清理，不可恢复

class PluginInstance:
    state: PluginState
    config: Config | None          # 校验后的配置（LOADING 前 resolveConfig）
    ctx: PluginContext             # 插件子上下文（服务/事件/effect 都挂这里）
    disposers: list[...]           # effect/on_close 收集的清理函数

    async def start(self) -> None:
        # PENDING → 等待 inject 依赖齐备 → LOADING → await apply(ctx, config)
        # → 收集 disposer → ACTIVE；异常 → FAILED
    async def stop(self) -> None:
        # UNLOADING → 逆序 await disposers → DISPOSED
```

- **依赖等待**：所有插件通过 `asyncio.gather` 并行启动；依赖未提供的 instance 停在 PENDING，`ctx.provide(name, value)` 时 notify 等待中的 instance，其检查自身 inject 是否齐备后继续（asyncio.Condition / Event）
- **启动收敛超时**：10s 后仍未 ACTIVE 的 instance 标记 FAILED + 列出缺失依赖

## 7. 加载器（loader.py）

**目录源列表**：固定有序列表 `[~/.javis/plugins, <project>/.javis/plugins]`；同名插件后者覆盖前者。profile 后续 = 往列表插入 `~/.javis/profiles/<name>/plugins`（设计预留，MVP 不实现）。

**插件目录结构约定**：
- 单个 `.py` 文件：`hello.py` → 插件名 `hello`
- 目录：`my-plugin/__init__.py` → 插件名 `my-plugin`（目录名）
- importlib 动态加载（`spec_from_file_location`）

**禁用机制**：`config.json` 的 `plugins.<name>.enabled = false`（文件仍在目录里，加载器跳过）。

**加载流程**：扫描 → import → 提取元数据（§4 四种形态）→ 创建 instance → 进入 build 流程的 gather 激活。

## 8. 插件配置注入

`JavisConfig.plugins` 顶层命名空间已预留（`extra="allow"`）：

```json
{
  "plugins": {
    "hello": {
      "enabled": true,
      "config": { "greeting": "你好，插件世界" }
    }
  }
}
```

- 校验时机：instance 进入 LOADING 前，`Config.model_validate(config 段)`；插件未声明 Config 则忽略配置段
- 校验失败 → FAILED，日志明确指出配置段与缺失字段
- `ctx.config` 提供只读全局配置

## 9. 五类扩展点：MVP 落地矩阵

| 扩展点 | 现状 | MVP 动作 | 插件 API |
|---|---|---|---|
| **工具** | 静态 `ALL_TOOLS` | **迁移为注册表**，内建 7 工具自注册 | `ctx.register_tool(tool)` |
| **命令** | `CommandRegistry`（build 内建） | 插件可注册，命令表 = 内建 + 插件 | `ctx.register_command(cmd)` |
| **生命周期** | start/close no-op | **真正实现**（instance 激活 + disposer 逆序清理 + on_start） | `apply` 返回 disposer / `ctx.on_close` / `ctx.on_start` |
| **引擎** | `register_engine` 已存在 | 设计覆盖，不迁移 | `ctx.register_engine(name, factory)`（薄封装） |
| **LLM provider** | 统一 `LLMProvider` 类 | 设计覆盖，预留接口不实现 | 后续 `ctx.register_provider(...)` |

## 10. 工具注册表化（阶段 2 前置，随 MVP 一并落地）

```python
# corecoder/tools/__init__.py — 静态列表 → 注册表
def register_tool(tool: Tool) -> None    # 幂等，重名覆盖并告警
def get_tool(name: str) -> Tool | None
def all_tools() -> list[Tool]            # 快照，替代 ALL_TOOLS
# 内建 7 个工具模块 import 时自注册
```

引用点改动（3 处）：
1. `corecoder/agent.py:40` — `self.tools = tools if tools is not None else ALL_TOOLS` → `all_tools()`
2. `javis/engines/corecoder/backend.py:216` — `Agent(llm=llm, ..., tools=all_tools())` ← **关键**：插件工具由此进入引擎
3. `corecoder/__init__.py` — `ALL_TOOLS` 导出保留为兼容别名

## 11. profile（设计预留，MVP 不做）

- 加载器抽象为"目录源列表"，profile = 往列表插入 `~/.javis/profiles/<name>/plugins/`
- 配置分层：`deep_merge(global, project, profile)`（现有 `deep_merge` 复用）
- 启动：`javis --profile <name>` 等价于 dsh `--profile`
- 不做热重载时 profile 价值 = 一键切换插件组合

## 12. 错误处理

| 阶段 | 错误 | 处理 |
|---|---|---|
| 加载期 | import 失败（语法/缺依赖） | 该插件 FAILED + WARNING 日志，其余继续 |
| 加载期 | 配置校验失败（pydantic ValidationError） | FAILED + 指明配置段与缺失字段 |
| 加载期 | inject 依赖缺失 | PENDING 停留至收敛超时（10s）→ FAILED + 列出缺失依赖 |
| 加载期 | apply 抛异常 | FAILED + 堆栈日志 |
| 运行期 | disposer 抛异常 | `asyncio.gather(return_exceptions=True)`，记日志继续清理其余插件 |
| 运行期 | 工具/命令执行错误 | 沿用现有机制（Tool.execute 返回错误文本 / CommandResult） |

加载结束后 loader 返回 **LoadReport**（成功/禁用/失败清单 + 原因）；`PluginRegistry.list_plugins()` 暴露每插件 `{name, state, error}`。

## 13. 测试策略

**单元测试**：
- `context`：provide/get、服务随插件卸载撤销、on/emit 事件、disposer 逆序执行
- `instance`：状态机流转（含 FAILED 分支）、async apply、依赖后提供时 PENDING→继续、stop 逆序 disposers
- `loader`：目录扫描（.py 文件/目录两种形态）、四种元数据提取、`enabled=false` 跳过、同名 project 覆盖 global、import 失败隔离、配置注入

**集成测试**：
- 测试插件 fixture（`tests/fixtures/plugins/` + `tmp_path` 动态生成）
- 端到端：注册自定义工具的测试插件 → `build_javis_runtime`（注入测试 backend）→ 断言工具出现在 agent 的 tool schemas
- 生命周期：start/close 调用顺序断言

**不做**：热重载测试（无此功能）。

## 14. 落地步骤（4 阶段，每阶段独立验收）

```
阶段 A：工具注册表化（阶段 2 前置）       ← 先行，独立合并
  A1  corecoder/tools/__init__.py → register_tool/get_tool/all_tools，内建自注册
  A2  agent.py / backend.py / __init__.py 三处引用点
  A3  现有 77 tests 全绿

阶段 B：插件内核
  B1  javis/plugins/context.py    PluginContext（服务仓库+事件+effect）
  B2  javis/plugins/instance.py   PluginInstance（状态机 + start/stop + 依赖等待）
  B3  javis/plugins/registry.py   PluginRegistry（收敛/超时/list_plugins）
  B4  javis/plugins/loader.py     目录源扫描 + importlib + 元数据提取 + 配置注入
  B5  javis/plugins/errors.py     异常类型
  B6  javis/plugins/__init__.py   公开 API

阶段 C：接入 runtime
  C1  build_javis_runtime：加载 → gather 激活 → 工具快照传 backend → 命令合并
  C2  start_runtime / close_runtime 变为真实生命周期
  C3  config.json plugins 段读取（enabled + config）

阶段 D：示例 + 测试 + 文档
  D1  examples/plugins/（一个工具插件 + 一个命令插件）
  D2  单元 + 集成测试
  D3  docs/plugins.md 用户文档
```

## 15. 明确不做（YAGNI）

- 热重载 / HMR（文件监听）
- `/plugins` 管理命令（list_plugins 已暴露，命令后续加）
- profile 第三层目录
- 引擎 / LLM provider 插件化迁移（仅设计覆盖）
- 插件事件挂钩到 javis 内部 AgentEvent 流
- 事件模式 waterfall/parallel/bail（仅 emit / emit_serial）
- 插件通过 pip 分发（entry-points 来源）

## 16. 对现有代码的影响

| 文件 | 改动 |
|---|---|
| `corecoder/tools/__init__.py` | 静态列表 → 注册表（§10） |
| `corecoder/agent.py` | 默认工具列表引用 → `all_tools()` |
| `javis/engines/corecoder/backend.py` | `Agent(...)` 传入 `tools=all_tools()` |
| `corecoder/__init__.py` | `ALL_TOOLS` 保留为兼容别名 |
| `javis/host/runtime.py` | build 流程接入插件加载；start/close 变真实生命周期 |
| `javis/session/config.py` | `plugins` 段读取辅助（enabled + config 提取） |
| 其余（engines/registry、commands/registry、session、frontend、wire 协议） | **不动** |
