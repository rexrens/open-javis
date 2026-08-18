# javis

[English](README.md) | **简体中文**

> javis —— 你自己的本地 AI 编程助理。完全可自定义，用 Python 编写。

你每天都在用 Claude Code，但它是一个黑盒——无法按照你的工作流去改造它。javis 是一个纯 Python 的本地助理，从设计上就属于**你**：前端、Agent 循环、扩展面全部开放，可自由定制。

- **前端** — 基于 **openharness** 的 React/Ink 终端界面，并持续为 javis 定制改造。你不需要写 TypeScript：前端由 AI 维护，你只管 Python。
- **后端** — **自研的 Python AgentLoop**（`corecoder/`）：LLM 工具调用循环、并行工具执行、上下文压缩、重试/退避和成本统计。
- **可扩展性** — **插件系统（规划中）**，借鉴 DeepSeek Harness 的思路，通过可插拔的插件承载各类扩展方案。

由两层组成：

- **`corecoder/`** — 自研 AgentLoop：LLM 工具调用循环，支持并行工具执行、上下文压缩、重试/退避和成本统计。
- **`javis/`** — 外壳层：CLI、运行时、JSON-lines 后端主机、引擎注册表、斜杠命令、会话持久化和 TUI 启动器。

## 特性

- **基于 openharness 前端** — React/Ink TUI 从 openharness fork 而来，并针对 javis 持续定制；前端改动由 AI 协助完成，你永远不需要写 TypeScript。
- **自研 AgentLoop** — Python 智能体引擎（`corecoder/`）从零编写：工具循环、并行执行、上下文压缩、重试、成本统计。
- **插件系统（规划中）** — 借鉴 DeepSeek Harness **"一切皆插件"** 的理念：模型适配器、工具注册表，甚至 Agent 循环本身都将可插拔、可替换。
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
│  javis.runtime.handle_line (斜杠命令 + 智能体回合)              │
│  javis.core.query_engine.QueryEngine (对话历史)                │
└───────────────────────────▲───────────────────────────────────┘
                            │ AgentBackend 协议（唯一的接缝）
┌───────────────────────────┴───────────────────────────────────┐
│  javis.engines.corecoder_backend.CoreCoderBackend             │
└───────────────────────────▲───────────────────────────────────┘
                            │
┌───────────────────────────┴───────────────────────────────────┐
│  corecoder.Agent.achat — 工具循环、并行执行、上下文压缩          │
│  corecoder.llm.AsyncLLM — 流式请求、重试、token/成本统计         │
│  corecoder.tools — bash/read/write/edit/glob/grep/agent       │
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

引擎选择优先级：**`--engine` CLI 参数 > `JAVIS_ENGINE` 环境变量 > `<workspace>/config.json` 的 `engine` 字段 > 默认值（`corecoder`）**。

`config.json` 位于 javis 工作区（默认 `~/.javis`，可通过 `JAVIS_WORKSPACE` 或 `--workspace` 覆盖）：

```json
{
  "engine": "corecoder",
  "engines": {
    "corecoder": {
      "model": "deepseek-chat",
      "base_url": "https://api.deepseek.com",
      "api_key": "sk-..."
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
| `JAVIS_ENGINE` | 引擎名称（默认 `corecoder`） |
| `JAVIS_WORKSPACE` | 工作区根目录（默认 `~/.javis`） |

## 使用方法

### 运行模式

```bash
uv run javis                    # React/Ink TUI（默认）
uv run javis -p "提示词"         # 单次提问，打印结果后退出
uv run javis --backend-only     # JSON-lines 后端主机（供自定义前端使用）
uv run javis --engine mock -p "hi"   #（mock 引擎已移除，请用 corecoder）
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
