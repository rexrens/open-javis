# 给 cdh 加插件（开发者指南）

> **在 Javis 仓库里读这篇**：文中的 `cdh` 指本示例的入口
> `uv run python examples/cordis_harness/cli.py`；harness home 是
> `~/.javis/cordis-harness`（patch、生成的组合、会话日志都在那里）；
> 包内默认组合是 `harness/cordis.yml`。

这篇文档面向「在自己的项目里扩展 cdh」的开发者：不改 harness 源码、不 fork，只写自己的插件文件加一个 patch 文件，`cdh` 启动时就带上它。

文中所有代码在 `tests/` 里都有对应测试（`tests/test_composition.py`、`tests/test_add_plugin.py`、`tests/test_plugin_shapes.py`），命令都实测过。

## 0. 三十秒版本

```text
my-project/
├── cordis.patch.yml          # 你要写的那一行（patch 层）
└── plugins/
    └── clock.py              # 你要写的插件（一个普通的 .py）
```

```yaml
# my-project/cordis.patch.yml
- id: clock                     # 新的 id：追加一行
  name: ./plugins/clock.py      # 相对路径按「写这一行的文件」解析
  config:
    format: "%H:%M:%S"
```

```sh
cd my-project
cdh --dump-config        # 看清合并后的组合（含你这一行）
cdh                      # 直接启动，clock 工具已经注册进来了
```

就这些。下面讲清楚：分层怎么算、插件怎么写、能接到哪些扩展点、怎么测、怎么排错。

## 1. cdh 的组合是「分层」的

`cdh` 不是只读一个文件，而是把若干层按顺序合并成一份组合再挂载：

| 顺序 | 层 | 默认位置 | 必需 |
|---|---|---|---|
| 1 | base | `--config FILE` → 当前目录 `./cordis.yml` → 包内默认（`harness/cordis.yml`） | 有默认值 |
| 2 | home patch | `$CDH_HOME/cordis.patch.yml`（默认 `~/.javis/cordis-harness`） | 可选 |
| 3 | project patch | 当前目录 `./cordis.patch.yml` | 可选 |
| 4 | `--patch FILE` | 按命令行出现顺序，可重复 | 可选 |

后一层覆盖前一层。相关开关：

```sh
cdh --dump-config                 # 打印合并结果后退出（不启动）
cdh --no-patches                  # 忽略第 2、3 层（显式 --patch 仍然生效）
cdh --home /path/to/home          # 换 harness home（patch、生成物、会话都跟着走）
cdh --config my.yml               # 换 base 层（patch 照样叠上去）
cdh --patch a.yml --patch b.yml   # 临时叠加，最后生效
```

有 patch 时，合并结果写到 `$CDH_HOME/composed.yml`（带 `# generated` 注释，别手改），loader 挂载的就是它；`--dump-config` 打印的也是它。**没有任何 patch 时不会生成这个文件**，base 文件被直接挂载。

### patch 的语法

patch 文件本身就是一份普通的组合（一个 row 列表），语义只有两条：

* `id` 已存在 → 更新这一行；
* `id` 是新的 → 追加到末尾。

| 字段 | 效果 |
|---|---|
| `id` | 必填。patch 行没有 `id` 会直接报错（否则无法定位） |
| `name` | 换掉这行的插件模块/文件 |
| `config` | **整体替换**该行的 config（不是深合并；写全你需要的字段） |
| `disabled` | `true` 关掉这行，`false` 打开 |
| `inject` / `provide` / `isolate` / `group` | 覆盖该行对应字段 |

```yaml
- id: agent                     # 更新已有行：模型换成本地 echo 路由
  config:
    provider: echo
    model: echo-1
    maxSteps: 4

- id: tools-builtin             # 关掉内置的 bash/read_file/write_file
  disabled: true

- id: clock                     # 新 id：追加一行
  name: ./plugins/clock.py
```

> 相对路径 `name:` 一律相对于**写这一行的文件**所在目录解析，所以 `~/my-plugins/cordis.patch.yml` 里的 `./x.py` 指的是 `~/my-plugins/x.py`，与你在哪个目录启动 `cdh` 无关。合并后的行里存的是绝对路径。

### 插件文件放哪

| 放法 | `name:` 怎么写 | 适用 |
|---|---|---|
| 项目内文件 | `./plugins/clock.py` | 最常见：插件跟项目走 |
| 任意绝对路径 | `/opt/team/plugins/clock.py` | 团队共享目录 |
| 已安装的 Python 包 | `my_company_harness.clock` | 插件要发布/复用（`uv add my-company-harness`） |
| harness 自带 | `harness.plugins.tools` | 内置能力（base 层就是这么写的） |

## 2. 插件长什么样

一个插件就是**一个可调用的东西**：`apply(ctx, config)`。模块方式最常用：

```python
# plugins/clock.py
from datetime import datetime

from pydantic import BaseModel

from harness.tools import ToolDefinition

name = "clock"          # 显示名（日志、fiber.name）
inject = ["tools"]      # 依赖：等 ctx.tools ACTIVE 之后才加载


class Config(BaseModel):          # 可选的配置校验
    format: str = "%Y-%m-%d %H:%M:%S"


def apply(ctx, config: Config):
    def clock(args, cwd):
        return datetime.now().strftime(args.get("format") or config.format)

    definition = ToolDefinition(
        name="clock",
        description="Report the current local time.",
        parameters={
            "type": "object",
            "properties": {"format": {"type": "string", "description": "strftime format"}},
            "additionalProperties": False,
        },
        execute=clock,
    )
    return ctx.get("tools").register(definition)   # 返回值 = 卸载时的清理
```

模块里可以导出的东西：`apply`（必需）、`name`、`inject`、`Config`、`provide`（说明性）。

### apply 的签名规则

引擎按「你声明了几个参数」决定怎么调用（对齐 JS 里多传参数被忽略的行为）：

| 你的写法 | 收到什么 |
|---|---|
| `def apply(ctx):` | 只有 ctx |
| `def apply(ctx, config):` | `(ctx, config)` |
| `def apply(ctx, config=None):` | 仍然传 config（默认值只是兜底） |
| `def apply(ctx, *, config):` | `ctx` + 关键字 `config=` |
| `async def apply(ctx, config):` | 会被 await |

### 五种插件形态

```python
# 1. 函数
def apply(ctx, config): ...

# 2. 带 apply 的对象
class Widget:
    def apply(self, ctx, config): ...

# 3. 字典（loader 把模块包装成的就是这种）
{"name": "widget", "inject": ["tools"], "apply": apply}

# 4. 类：__init__(ctx, config) 之后可选 init()，init() 的返回值当 effect
class Widget:
    def __init__(self, ctx, config): ...
    def init(self): return lambda: ...   # 卸载时执行

# 5. Service 子类：构造即把自己注册成服务
class Counter(Service):
    provide = "counter"
    def __init__(self, ctx):
        super().__init__(ctx, "counter")
```

五种都有测试覆盖：`tests/test_plugin_shapes.py`。

> **组合行只能指向模块。** `cordis.yml`/patch 里的 `name:` 会被 import 成一个模块，然后取它的 `apply`，所以文件名必须是一个「导出 `apply` 的 .py」或一个已安装的包。要挂一个类或 Service 子类（例如引擎自带的 HMR），就写一个模块包一层——见 §11 的 HMR 例子。

### Config 校验失败会怎样

`Config` 必须是 pydantic 的 `BaseModel`。校验失败 → 这个 fiber 进入 FAILED，`cdh` 打印 `[error] <name> FAILED: invalid config: ...` 并以退出码 1 结束——**不会带着半截配置继续跑**：

```sh
$ cdh --config broken.yml
[error] clock FAILED: invalid config:
  - value: Input should be a valid integer (at value)
```

## 3. 你能接到哪些扩展点

### 服务（`ctx.get(name)`）

| 服务 | 谁提供 | 你能用它做什么 |
|---|---|---|
| `ctx.llm` | `harness.plugins.llm` | `register_adapter(providers, adapter)` 加模型路由；`stream(options)` 直接发一次调用 |
| `ctx.tools` | `harness.plugins.tools` | `register(definition)` 给模型加工具；`set_approver(fn)` 接管审批 |
| `ctx.sessions` | `harness.plugins.session` | 建/开会话；`session.append(event)` 落盘（自动广播 `session/event`） |
| `ctx.systemPrompt` | `harness.plugins.system_prompt` | `register_section(name, render)` 加提示词段落 |
| `ctx.agents` | `harness.plugins.agent` | `create(session, provider?, model?)` 起一个 agent；`get(id)`、`aclose()` |
| `ctx.repl` | `harness.plugins.cli` | 换成你自己的前端（提供同名服务即可） |

### 事件（`ctx.on(name, listener)`）

两类事实，别混：

| 通道 | 事件 | 载荷 |
|---|---|---|
| live | `agent/status` | `(agent, "working" \| "idle" \| "disposed")` |
| live | `agent/assistant-stream` | `(agent, chunk)`，chunk 是流式词表里的 `text-delta` 等 |
| live | `agent/turn-start` / `agent/step-start` / `agent/step-end` / `agent/turn-end` | `(agent, ...)` |
| live | `agent/error` | `(agent, turn, step, failure)` |
| live | `tool/call` / `tool/result` / `tools/approval-request` | 工具管道 |
| durable | `session/event` | `(session, event)`，`event` 是落盘的那一条（`turn/start`、`user/message`、`assistant/message`、`tool/call`、`tool/result`、`turn/end`） |

`emit` 是**同步**派发：监听器在事件发生的那一步里按顺序执行（所以渲染与日志天然有序），别在里面做慢活。异步监听器会被排成 task，**异常只记日志**，不会打断 agent。

### 典型任务 → 用什么

| 我想… | 做法 |
|---|---|
| 给模型加一个能力 | `ctx.tools.register(ToolDefinition(...))` |
| 让模型知道某种约定 | `ctx.systemPrompt.register_section(...)` |
| 换/加一个模型厂商 | 实现 `LlmAdapter.stream()`，`ctx.llm.register_adapter([...], adapter)` |
| 记录/上报每一轮对话 | `ctx.on("session/event", ...)` |
| 在 turn 前后做事 | `ctx.on("agent/turn-start" / "agent/turn-end", ...)` |
| 拦截危险工具 | `ctx.tools.set_approver(fn)` 或 `ToolDefinition(approval=True)` |
| 换掉交互界面 | 提供 `ctx.repl`（`cdh` 会把终端让给它，等它发 `app/exit`） |
| 在别的进程里驱动 agent | `ctx.agents.create(...)` + `agent.send()` + `agent.wait_idle()` |

## 4. 可逆注册与生命周期

插件的每一样注册都能回滚，这是「卸载 = 撤销」的基础：

```python
def apply(ctx, config):
    ctx.provide("greeter", greet)                 # 提供即注册
    ctx.on("session/event", on_event)             # 监听
    ctx.get("tools").register(definition)         # 工具

    def start():
        handle = open("x.log", "a")               # 立即执行
        return lambda: handle.close()             # 卸载时执行
    ctx.effect(start, "log file")

    return [disposer_1, disposer_2]               # 返回值同样被收集
```

状态机：`PENDING → LOADING → ACTIVE → UNLOADING → DISPOSED`（失败进 `FAILED`）。

* `inject` 里的服务没就绪时插件停在 PENDING——**这不是错误**，只是还没轮到它；依赖永远不出现才是问题，用 `cdh --dump-config` 检查行是否还在。
* 同一插件的多个 disposer 按**注册逆序**执行；一个 record 内的同步 disposer 立即执行，`ctx.effect` 的异步清理在这些之后并发等待（别依赖两类之间的绝对先后）。
* 提供者卸载时，依赖它的插件先被卸载；依赖回来后会重新加载。
* 引擎自带 HMR（`cordis/loader/hmr.py`，轮询 + 防抖），默认组合没挂它；要边改边生效就把它作为一行加进 patch，或直接重启 `cdh`。

## 5. 完整示例：加一个工具 + 让模型知道它

参考实现：`tests/fixtures/dev-project/`（测试里真实跑过）。

```yaml
# my-project/cordis.patch.yml
- id: clock
  name: ./plugins/clock.py
  config:
    format: "%H:%M:%S"
```

```python
# my-project/plugins/clock.py
from datetime import datetime

from pydantic import BaseModel

from harness.tools import ToolDefinition

name = "clock"
inject = ["tools", "systemPrompt"]


class Config(BaseModel):
    format: str = "%Y-%m-%d %H:%M:%S"


def apply(ctx, config: Config):
    def clock(args, cwd):
        return datetime.now().strftime(args.get("format") or config.format)

    tool = ToolDefinition(
        name="clock",
        description="Report the current local time.",
        parameters={
            "type": "object",
            "properties": {"format": {"type": "string"}},
            "additionalProperties": False,
        },
        execute=clock,
        approval=False,          # 只读工具：不需要人工确认
    )

    # 两个注册，一起回滚
    return [
        ctx.get("tools").register(tool),
        ctx.get("systemPrompt").register_section(
            "time-policy",
            lambda tools, cwd: "需要当前时间时调用 clock 工具，不要凭记忆猜。",
        ),
    ]
```

```sh
$ cd my-project && cdh
» 现在几点？
[tool] clock: {}
[result:ok] 15:42:07
```

确认插件真的注册了：交互里敲 `/tools`，或看 `cdh --dump-config` 的合并结果。

## 6. 完整示例：加一个模型适配器

适配器是 harness 与厂商协议之间唯一的翻译层。实现 `LlmAdapter.stream()`，按流式词表吐 chunk，最后必须以 `finish` 收尾。下面这个不需要 key、不联网，适合把整条链路在本地跑通（对应 `tests/fixtures/dev-project/plugins/echo_adapter.py`）：

```python
# plugins/echo_adapter.py
from pydantic import BaseModel

from harness.llm import LlmAdapter
from harness.types import block_end, block_start, finish_chunk, message_text, text_delta

name = "echo-provider"
inject = ["llm"]


class Config(BaseModel):
    prefix: str = "[echo]"


class EchoAdapter(LlmAdapter):
    def __init__(self, config: Config):
        self.config = config

    def list_models(self, provider):          # 可选：模型目录（供发现用）
        return ["echo-1"]

    async def stream(self, options):
        last_user = next(
            (m for m in reversed(options.messages) if m.get("role") == "user"), None)
        reply = f"{self.config.prefix} {message_text(last_user) if last_user else ''}"
        yield block_start(0, "text")
        yield text_delta(0, reply)
        yield block_end(0, {"type": "text", "text": reply})
        yield finish_chunk("stop")


def apply(ctx, config: Config):
    return ctx.get("llm").register_adapter(["echo"], EchoAdapter(config))
```

```yaml
# cordis.patch.yml
- id: echo-provider
  name: ./plugins/echo_adapter.py
  config: { prefix: "[dev]" }

- id: agent                # 让 agent 用这个路由
  config: { provider: echo, model: echo-1, maxSteps: 4 }
```

```sh
$ cdh
» 你好
[dev] 你好
```

要接真实厂商就把请求发出去：分块遵守 `block-start → delta… → block-end → finish`，失败用 `finish_chunk("error", LlmFailure(message, code))` 收尾（`code` 用稳定值，如 `AUTH`、`RATE_LIMIT`）。`ctx.llm.stream()` 会保证每条流都有终止 chunk。完整实现可对照 `src/harness/llm_openai.py`（OpenAI 兼容 + SSE + 错误映射）。

## 7. 完整示例：观察者（订阅 + 管理资源）

```python
# plugins/transcript.py
from pathlib import Path

from pydantic import BaseModel

from harness.types import blocks_text

name = "transcript"
inject = ["sessions"]


class Config(BaseModel):
    directory: str | None = None


def apply(ctx, config: Config):
    handles: dict[str, object] = {}

    def on_event(session, event):
        handle = handles.get(session.id)
        if handle is None:
            directory = Path(config.directory) if config.directory else Path(session.path).parent
            directory.mkdir(parents=True, exist_ok=True)
            handle = handles[session.id] = (directory / f"{session.id}.log").open("a", encoding="utf-8")
        if event["type"] == "assistant/message":
            handle.write(blocks_text(event.get("content") or []) + "\n")
            handle.flush()

    ctx.on("session/event", on_event)

    def start():
        return lambda: [h.close() for h in handles.values()]   # 卸载时收尾

    ctx.effect(start, "transcript")
```

要在别的进程里用同一套 harness（不经过 REPL），走服务接口 + 自驱 agent：

```python
import asyncio

from harness.boot import load_composition


async def main():
    ctx, _ = await load_composition("src/harness/cordis.yml")   # 或 load_layers(...) 带 patch
    ctx.on("agent/assistant-stream", lambda agent, chunk: print(chunk.get("text", ""), end=""))

    session = ctx.get("sessions").create(".", "openai", "deepseek-v4-flash")
    agent = ctx.get("agents").create(session)
    agent.send("用一句话介绍这个仓库")
    await agent.wait_idle()

    agent.inject("补充：只用中文")     # steering：跟着正在跑的 turn 走
    agent.send("再详细一点")
    await agent.wait_idle()
    await ctx.get("agents").aclose()


asyncio.run(main())
```

## 8. 测试你的插件

插件是普通 Python 代码，用 pytest 直接挂载即可（项目已配 `pytest-asyncio`，`asyncio_mode=auto`）：

```python
from cordis import Context

from harness.boot import PACKAGED_CONFIG, failed_fibers, load_layers


async def test_my_project_layer_loads(tmp_path):
    ctx = Context()
    _, applied = await load_layers(
        ctx,
        PACKAGED_CONFIG,
        patches=[tmp_path / "cordis.patch.yml"],
        composed_path=tmp_path / "composed.yml",
    )
    assert failed_fibers(ctx) == []
    assert "clock" in ctx.get("tools").names()
    await ctx.get("agents").aclose()
```

要在测试里驱动一轮对话（不联网、不要 key），用仓库自带的脚本化适配器：

```python
from tests.support.fake_llm import ScriptedAdapter, text_step, tool_step


async def test_my_tool_runs(ctx):
    ctx.get("llm").register_adapter(["fake"], ScriptedAdapter([tool_step("c1", "clock", {}), text_step("done")]))
    session = ctx.get("sessions").create(".", "fake", "fake-model")
    agent = ctx.get("agents").create(session)
    agent.send("几点了？")
    await agent.wait_idle()
    assert [event["type"] for event in session.events()][-1] == "turn/end"
```

断言素材三个来源，按需选：`session.events()`（落盘事实）、`ctx.on(...)` 收集到的 live 事件、`agent.result`（最近一轮结果）。

## 9. 调试

| 症状 | 先做什么 |
|---|---|
| 插件"没生效" | `cdh --dump-config`：确认这一行真的在合并结果里，绝对路径是否指对 |
| 启动报 FAILED | 看 `[error] <name> FAILED: ...`：Config 校验、import 错误、依赖服务没提供都会在这里 |
| 一直 PENDING | `inject` 里的服务没人提供；检查提供者那行是否被 patch 关掉了 |
| 想看事件流 | 临时插件里 `ctx.on("session/event", lambda s, e: print(e["type"]))` |
| 想确认模型看到什么 | 订阅 `session/event` 看 `user/message`、`assistant/message`；提示词在 `agent._request()` 组装的 system 消息里 |
| 打日志 | `ctx.logger.info("...")`，输出到 stderr（带时间戳与 scope） |

退出码：组合/插件失败 = `1`，正常 = `0`，终端被 Ctrl-C 打断 = `130`。

## 10. 常见陷阱

* **patch 的 `config` 是整体替换**：只写 `model` 会把同一行原来的 `maxSteps` 抹掉，要保留就写全。
* **相对路径基准**：永远相对「写这一行的文件」，不是相对 cwd、也不是相对 base 文件。
* **`id` 必须稳定**：loader 靠 `id` 判断「改行」还是「删+加」，改名等于换行。
* **`emit` 是同步的**：监听器里别做网络/重活，要慢活就 `asyncio.ensure_future`。
* **异步监听器的异常只记日志**：不会中断 turn，重要失败要自己捕获上报。
* **卸载路径要能跑通**：注册都返回 disposer，用它；别把清理塞进 `__del__`。
* **别绕过服务**：直接改 session 文件、直接调适配器都会让 durable 事实与事件流脱节（日志必须走 `session.append`）。
* **工具审批是安全边界**：编程用法下没有 approver 时默认放行；CLI 里会问 `approve? [y/N]`。
* **`--no-patches` 只关环境层**：显式 `--patch` 仍然生效，适合复现实验。

## 11. 进阶

* **隔离与多实例**：行里加 `isolate: my-realm`，这组插件看到独立的服务作用域（同名服务互不干扰）。
* **分组**：`group:` 把若干行当成一个整体加载/卸载。
* **发布成包**：插件放进 Python 包，`uv add` 之后在 patch 里写点号模块名（`my_company_harness.clock`），团队共用。
* **换前端**：提供 `ctx.repl` 服务（实现一个 `run()`），`cdh` 把终端让给它并等它 emit `app/exit`。
* **HMR**：引擎自带热重载（`cordis/loader/hmr.py`），默认组合没挂。因为它是一个 Service 子类而不是模块，需要一个三行包装：

  ```python
  # my-plugins/hmr.py
  from cordis.loader.hmr import Hmr

  apply = Hmr                 # 模块的 apply 直接指向这个类
  inject = ["loader"]         # 类自己的 inject/Config 会被模块层遮住，要转出来
  Config = Hmr.Config
  ```

  然后在 patch 里加一行，之后改插件文件即自动重载（`root` 是要监视的目录）：

  ```yaml
  - id: hmr
    name: ./my-plugins/hmr.py
    config: { root: ["."], interval: 0.3 }
  ```

  这个配方有测试兜底：`tests/test_add_plugin.py::test_an_engine_service_class_is_mounted_through_a_wrapper_module`。

* **参照内置实现**：`src/harness/plugins/*.py` 是最权威的示例——每个内置能力都是这样一行插件。
