# javis

[English](README.md) | **简体中文**

> javis —— 你自己的本地 AI 编程助理。完全可自定义，用 Python 编写。

你每天都在用 Claude Code，但它是一个黑盒——无法按照你的工作流去改造它。javis 是一个纯 Python 的本地助理，从设计上就属于**你**：前端、Agent 循环、扩展面全部开放，可自由定制。

- **前端** — 基于 **openharness** 的 React/Ink 终端界面，并持续为 javis 定制改造。你不需要写 TypeScript：前端由 AI 维护，你只管 Python。
- **后端** — **自研的 Python AgentLoop**（`corecoder/`）：LLM 工具调用循环、并行工具执行、上下文压缩、重试/退避和成本统计。
- **可扩展性** — **Cordis 风格插件系统**（借鉴 DeepSeek Harness）：工具、斜杠命令、甚至 Agent 引擎本体都可通过 `cordis.yml` 组合文件插件化。

由两层组成：

- **`corecoder/`** — 自研 AgentLoop：LLM 工具调用循环，支持并行工具执行、上下文压缩、重试/退避和成本统计。
- **`javis/`** — 外壳层：CLI、运行时、JSON-lines 后端主机、引擎注册表、斜杠命令、会话持久化和 TUI 启动器。

## 特性

- **基于 openharness 前端** — React/Ink TUI 从 openharness fork 而来，并针对 javis 持续定制；前端改动由 AI 协助完成，你永远不需要写 TypeScript。
- **自研 AgentLoop** — Python 智能体引擎（`corecoder/`）从零编写：工具循环、并行执行、上下文压缩、重试、成本统计。
- **插件系统** — 借鉴 DeepSeek Harness **"一切皆插件"** 的理念：工具注册表、斜杠命令、甚至 Agent 循环本身都可插拔、可替换（Cordis 服务机制）。
- **任意 OpenAI 兼容模型** — DeepSeek、Qwen、Kimi、GLM、Ollama 等。修改 `base_url` + `api_key` 即可切换供应商。非 OpenAI 兼容供应商（Bedrock、Vertex 等）可通过内置的 LiteLLM 后端使用。
- **智能体工具循环** — `bash`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`，以及嵌套的子 `agent` 工具。多个工具调用**并行执行**（基于线程池，灵感来自 Claude Code 的 `StreamingToolExecutor`）。
- **流式 TUI** — React + Ink 终端前端，支持 Markdown 渲染、工具记录、权限/编辑确认弹窗、主题/权限/轮次选择器，以及图片附件。
- **同步与异步 LLM 双路径** — 提供 `chat()` 和 `achat()` 两种循环，取消时保持对话历史有效（被打断的工具调用会自动回填，确保 OpenAI 兼容 API 的请求始终合法）。
- **上下文管理** — 当工具输出使对话超过 token 预算时自动压缩。
- **健壮的 LLM 层** — 指数退避重试（限流 / 超时 / 5xx）、对不支持 `stream_options` 的供应商自动回退、用量统计和按模型的成本估算。
- **会话持久化** — 每个会话以原子方式写入 JSON 快照，存放在 `~/.javis/sessions/` 下，TUI 中支持 `/resume` 恢复。
- **确定性离线测试** — `ScriptedLLM` / `AsyncScriptedLLM` 让你无需联网即可跑通 corecoder 引擎。

## 架构

前端 React/Ink 界面从 openharness fork 而来并针对 javis 定制改造；其下的协议、运行时、Agent 循环全部是自研的 Python 实现。

```
┌────────────────────────────────────────────────────────────────┐
│  React/Ink TUI (frontend/terminal, TypeScript)                 │
└───────────────▲───────────────────────────────┬────────────────┘
                │ OHJSON: {…} JSON-lines        │ 请求
                │ (stdout)                      │ (stdin)
┌───────────────┴───────────────────────────────▼────────────────┐
│  javis.backend_host.JavisBackendHost                          │
│    (线协议、弹窗、选择器、权限流程)                              │
└───────────────────────────▲───────────────────────────────────┘
                            │ AgentEvent 事件流
┌───────────────────────────┴───────────────────────────────────┐
│  javis.runtime.handle_line (斜杠命令 + 智能体回合)                 │
└───────────────────────────▲───────────────────────────────────┘
                            │ AgentBackend 协议（唯一的接缝）
┌───────────────────────────┴───────────────────────────────────┐
│  javis.harness.HarnessEngine (dsh 风格循环)                        │
└───────────────────────────▲───────────────────────────────────┘
                            │
┌───────────────────────────┴───────────────────────────────────┐
│  ReactLoopAgent — turn/step 循环、exclusive/parallel 工具          │
│  javis.llm.LlmRuntime — adapter 注册表、llm/stream waterfall        │
│  javis.llm — OpenAICompatAdapter / ScriptedAdapter                  │
│  javis.tools — bash/read/write/edit/glob/grep/agent                │
└────────────────────────────────────────────────────────────────┘
```

**`AgentBackend` 协议是唯一的接缝**：无需改动引擎或 TUI，即可把 `MockAgent` 换成 `CoreCoderBackend`（或任何通过 `register_engine` 注册的第三方后端）。

## 快速开始

需要 Python ≥ 3.10、Node.js ≥ 18 和 [uv](https://docs.astral.sh/uv/)。

```bash
# 安装依赖（Python + 前端）
uv sync --extra dev   # --extra dev pulls in pytest, ruff, mypy
cd frontend/terminal && npm install && cd ../..

# 配置模型供应商（见下方"配置"）
mkdir -p ~/.javis
# 创建 ~/.javis/config.json

# 启动 TUI
uv run javis

# 或非交互式单次提问
uv run javis -p "解释一下这个仓库"
```

## 配置

`config.json` 位于 javis 工作区（默认 `~/.javis`，可通过 `JAVIS_WORKSPACE` 或 `--workspace` 覆盖）：

```json
{
  "provider": "deepseek",
  "model": "deepseek-chat",
  "providers": {
    "deepseek": {
      "baseUrl": "https://api.deepseek.com",
      "apiKeyEnv": "DEEPSEEK_API_KEY"
    }
  }
}
```

也可以使用环境变量（会从工作目录向上查找 `.env` 文件并读取，直到 `$HOME`）：

| 变量 | 作用 |
|---|---|
| `CORECODER_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` | API 密钥 |
| `CORECODER_MODEL` | 模型名称（默认 `gpt-5.5`） |
| `CORECODER_BASE_URL` / `OPENAI_BASE_URL` | OpenAI 兼容接口地址 |
| `CORECODER_MAX_TOKENS`、`CORECODER_TEMPERATURE`、`CORECODER_MAX_CONTEXT` | 生成参数 |
| `CORECODER_PROVIDER=litellm` | 通过 LiteLLM 路由（支持 100+ 供应商） |
| `JAVIS_WORKSPACE` | 工作区根目录（默认 `~/.javis`） |

## 插件

插件是 Cordis 风格的 `apply(ctx, config)` 模块，通过 **`cordis.yml` 组合文件**
声明——默认位于 `<workspace>/cordis.yml`（缺失时自动创建）。每次会话启动时
runtime 都会挂载组合文件，等所有插件 settle 后再读取引擎。

组合文件解析顺序：`--plugins <file>` > `JAVIS_PLUGINS` > `config.json`
`pluginsFile` > `<workspace>/cordis.yml`。entry 的 `name:` 相对组合文件所在
目录解析，也支持绝对路径。

```yaml
# ~/.javis/cordis.yml
- id: engine
  name: './my_engine.py'
  inject: ['config', 'tools', 'host']
- id: extra-tools
  name: './extra_tools.py'
  inject: ['tools']
```

```python
# my_engine.py — 替换内建 CoreCoderEngine 的引擎插件
from javis.contracts import ENGINE_SERVICE


def apply(ctx):
    cfg = ctx.get('config')       # JavisConfig
    tools = ctx.get('tools')      # ToolRegistry
    host = ctx.get('host')        # HostContext（cwd/session_id/tool_metadata/…）
    engine = build_my_engine(cfg, tools=tools.all(), host=host)
    ctx.provide(ENGINE_SERVICE, engine)
```

内建服务：`config`（`JavisConfig`）、`tools`（`ToolRegistry`）、`commands`
（`CommandRegistry`）、`host`（`HostContext`）由宿主提供、不可撤销；`engine`
由插件提供——首个成功提供者生效，缺失/非法引擎回退到内建 corecoder。`llm`
接缝保留给后续里程碑。

工具/命令插件使用 disposer 模式，卸载时自动清理：

```python
def apply(ctx):
    tools = ctx.get('tools')
    ctx.effect(tools.register(MyTool()))          # 卸载时自动反注册
    commands = ctx.get('commands')
    ctx.effect(commands.register(Command('hello', 'Say hello', handler)))
```

完整契约参考 [docs/plugins.md](docs/plugins.md)。

## 使用方法

### 运行模式

```bash
uv run javis                    # React/Ink TUI（默认）
uv run javis -p "提示词"         # 单次提问，打印结果后退出
uv run javis --backend-only     # JSON-lines 后端主机（供自定义前端使用）
uv run javis -v                 # 调试日志输出到 stderr
uv run javis doctor             # 检查工作区与前端布局
```

### 斜杠命令

| 命令 | 说明 |
|---|---|
| `/help` | 列出可用命令 |
| `/status` | 模型、消息数、token 用量、当前目录、会话 ID |
| `/clear` | 清空对话历史 |
| `/exit`、`/quit` | 退出 javis |
| `/version` | 显示版本号 |

TUI 命令选择器中还提供交互式选择器：**权限模式**（默认 / 自动 / 计划模式）、**主题**、**最大轮次**、**快速模式**、**Vim 模式**、**语音**、**模型**，以及**恢复会话**（重放历史会话）。

### 权限模式

- **默认** — 写入/执行前询问
- **自动**（`full_auto`）— 自动允许所有工具，编辑 diff 自动批准
- **计划模式** — 阻止所有写操作

## 开发

```bash
uv run pytest tests/ -q          # 84 个测试，全部通过
uv run pytest tests/ --cov=javis --cov=corecoder   # 覆盖率报告
uv run ruff check javis/ corecoder/
uv run mypy javis/
```

### 项目结构

```
corecoder/            智能体引擎：工具循环、LLM 层、工具、上下文管理
javis/                宿主外壳：CLI、运行时、后端主机、线协议
  contracts/          纯契约层：AgentBackend 协议、事件/消息模型
  host/               CLI、运行时、线协议、后端主机、前端启动器
  session/            会话持久化、应用状态、工作区布局
  commands/           斜杠命令注册表
  engines/            后端适配器（corecoder）+ 注册
  frontend/terminal   React/Ink TUI（TypeScript）
tests/                pytest 测试套件
```

## 许可证

MIT
