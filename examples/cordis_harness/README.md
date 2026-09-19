# cordis_harness — cordis-only 的最小可用 agent harness

> 本目录顶替原 `examples/mini_dsh` 的教学位：同样是「用 cordis 组装一个 dsh 风格的
> harness」，但从「从零复刻 dsh 主流程的教学精简版」换成**一个真能用的最小 harness**。
>
> 代码是 [cordis-harness](https://github.com/) 项目的 harness 层副本；引擎**不额外携带**，
> 直接复用本仓库的 `javis.cordis`（两者实现逐字节相同）。

## 在三个示例里的位置

| 示例 | 职责 | harness 从哪来 |
|---|---|---|
| [`examples/cordis`](../cordis/README.md) | 插件系统接口教程（Context / Loader / inject / 事件模式） | 无 harness 概念 |
| **`examples/cordis_harness`** | **最小可用 harness**：模型适配器 / 工具 / 会话 / agent 循环 / REPL / 组合分层 | `harness/`（本目录，引擎用 `javis.cordis`） |
| [`examples/dsh_harness`](../dsh_harness/README.md) | 生产核心的插件装配 | `javis.harness`（生产 core） |

阅读顺序：先 `examples/cordis` 学插件接口 → 再看这里的 harness 怎么被装配起来 →
最后看 `examples/dsh_harness` 的生产 core 姿势。

## 与 mini_dsh 的差异（这次替换改变了什么）

mini_dsh 是「主流程教学复刻」，本示例是「最小可用产品」。能力面并不等价：

| | mini_dsh（已删除） | cordis_harness（本目录） |
|---|---|---|
| core 来源 | 自包含复刻（`core/` 8 模块，与 `javis/harness` 同结构） | `harness/`（可独立运行的最小实现） |
| 教学覆盖 | skills / compaction / instructions / middleware / exclusive-parallel 调度 / 7 个脚本化场景 | 适配器注册 / 工具与审批 / 会话 JSONL + resume / 自驱 agent 循环 / 组合分层 |
| 真实模型 | `--prompt`（OpenAI 兼容） | 内置 OpenAI 兼容适配器（SSE + 错误码）；`--provider/--model` 切会话路由，`--no-patches` 则是整层回到 base |
| 运行形态 | `cli.py` 跑 7 个 demo 场景 | 交互 REPL + 斜杠命令；也可 `--dump-config` 看组合 |
| 会话 | 内存事件日志 | JSONL 落盘（`~/.javis/cordis-harness/sessions/`）+ `--resume` |
| 扩展现有插件 | 组合文件 + 插件模块 | 组合文件 + **分层 patch**（`cordis.patch.yml`，不改 base 即可加插件） |

mini_dsh 覆盖的 skills / compaction / instructions / middleware 在本示例里**没有对应实现**；
需要它们时按 [docs/plugins.md](docs/plugins.md) 用插件补（每个都是 `ctx.tools` /
`ctx.on("session/event")` / 自定义服务的几行组合）。

## 目录结构

```
examples/cordis_harness/
├── cli.py                  # standalone 驱动：把本目录加入 sys.path 后转调 harness.cli
├── cordis.yml              # base 组合（8 行，dsh: everything is a plugin）
├── cordis.patch.yml        # 示例自带 patch：换成离线 echo 模型 + 注册 clock 工具
├── plugins/
│   ├── echo_adapter.py     # 教学：自定义模型适配器（不需要 key）
│   └── clock.py            # 教学：自定义工具 + 系统提示词段落
├── harness/                # harness 层（types/llm/tools/session/agent/repl/cli/boot/composition）
│   ├── cordis.yml          # 包内默认组合（base 的默认值）
│   └── plugins/            # 内置能力也是插件行：llm / llm_openai / sessions / tools / …
└── docs/plugins.md         # 插件开发指南（分层、扩展点、生命周期、测试、排错）
```

## 跑起来

启动目录就是组合目录：base 找的是当前目录的 `./cordis.yml`，patch 找的是当前目录的
`./cordis.patch.yml`。所以先 `cd` 进来，命令里只写 `cli.py`。

```sh
cd examples/cordis_harness

# 1. 离线交互（示例自带的 patch 把模型换成本地 echo 适配器）
uv run python cli.py
» 你好
[echo] 收到 2 条消息；你最后说：你好
/tools            # 能看到 clock 工具
/exit

# 2. 看组合是怎么合并出来的（base + home patch + project patch + --patch）
uv run python cli.py --dump-config

# 3. 用真实模型：--provider/--model 只切会话路由，示例层照旧生效
export OPENAI_API_KEY=sk-...
uv run python cli.py --provider openai --model deepseek-v4-flash
» 现在几点了？   # 走 base 的 llm-openai 行，clock 工具也还在

# 3b. 整层忽略示例 patch：直接落到 base 的 llm-openai，并失去 clock 与 echo
uv run python cli.py --no-patches

# 4. 换一个 harness home（patch / 生成组合 / 会话都跟着走）
uv run python cli.py --home /tmp/cdh-home --session-dir /tmp/cdh-sessions
```

示例自带的测试在**仓库根目录**跑（107 个，全部离线）——它们起的是独立子进程，
在当前目录有 patch 时会把它当成环境层叠进去：

```sh
uv run pytest tests/test_cordis_harness -q
```

直接从仓库根目录 `uv run python examples/cordis_harness/cli.py` 也能起来，但那里没有
`cordis.yml` / `cordis.patch.yml`，会落到包内默认组合：`openai/deepseek-v4-flash`、
3 个工具、没有 clock，没有 key 时会直接报 `MISSING_CREDENTIAL`。

## 加自己的插件

在本目录（或任何项目目录）放一个插件文件和一行 patch 即可，不需要改 base 组合：

```yaml
# my-project/cordis.patch.yml
- id: my-tool                 # 新 id：追加一行
  name: ./plugins/my_tool.py  # 相对路径按「写这一行的文件」解析
  config: { greeting: 你好 }
```

完整规则（patch 语法、扩展点表、生命周期、测试写法、排错）见
[docs/plugins.md](docs/plugins.md)。

## 与上游 cordis-harness 的关系

本目录是该项目的 harness 层副本，**只改了一处**：引擎 import 从自带的 `cordis`
换成仓库的 `javis.cordis`（因此这里不携带第二份引擎），harness home 从
`~/.cordis-harness` 改为 `~/.javis/cordis-harness`。上游是 source of truth；
同步时重放这两处替换即可。
