# javis TODO

> 交接清单 — 在家里继续工作用。当前状态：**77 tests passed，DeepSeek 连通，远端 main 已同步**。

## 快速恢复环境（回家后第一步）

```bash
cd open-javis
uv sync --extra dev        # 装依赖（pytest/ruff/debugpy 在 dev 组）
uv run pytest tests/ -q    # 应 77 passed
uv run javis -p "hi"       # 验证 DeepSeek 连通（读 ~/.javis/config.json）
```

注意事项：
- PyPI 官方源很慢（debugpy 5.1MiB 下载超时），装包慢就加 `--index-url https://mirrors.aliyun.com/pypi/simple/`，或考虑在 pyproject.toml 加 `[tool.uv] index-url`
- VSCode 调试配置在 `.vscode/launch.json`（被 .gitignore 忽略，本机已有；6 个配置：print 模式 / backend-only / pytest / corecoder）

---

## 🔴 P0：选择器流程断裂（核心 bug）

`_build_select_command_line`（`javis/host/backend_host.py`）生成的 `/permissions xxx`、`/theme xxx`、`/turns xxx`、`/fast xxx`、`/vim xxx`、`/voice xxx`、`/model xxx` 在 `commands/registry.py` **都不存在** → 被 `handle_line` 当作普通用户消息发给 LLM。

**后果**：TUI 里 Tab/命令选择器切换权限模式、主题、轮次等全部不生效。
**修法**：在 `commands/registry.py` 注册这 7 个设置命令（handler 更新 `AppStateStore` + QueryEngine setter）。

## 🔴 P1：斜杠命令缺失

- `/plan on` / `/plan off`（前端 App.tsx 已实现切换逻辑，后端无 `/plan` 命令）→ 需补命令 + 发 `plan_mode_change` 事件
- `/resume`：前端发 `select_command resume`，后端会话列表逻辑却挂在 `list_sessions` 请求上 → **协议不匹配**，需对齐（`_handle_select_command` 加 resume 分支）
- selector 缺分支：`/provider`、`/output-style`、`/effort`、`/passes`

## 🟡 P2：事件类型定义了但从不发送（protocol 僵尸事件）

`javis/host/wire.py` 定义了这些类型但后端从不发送：

| 事件 | 前端效果 |
|---|---|
| `todo_update` | TodoPanel 永远空白（组件已写好） |
| `plan_mode_change` | 状态栏不跟随 /plan |
| `compact_progress` | 压缩进度不显示（corecoder 有压缩但没转发） |
| `status` | protocol 里**根本没有**这个类型，但前端 useBackendSession 有处理分支 |

## 🟡 P3：状态栏缺字段

`StatusBar.tsx` 显示 `input_tokens`/`output_tokens`，但 `_state_payload`（wire.py）不提供 → token 计数永不显示。需要把 QueryEngine 的 `total_usage` 放进状态快照。

## 🟠 P4：权限弹窗死代码

`_ask_permission` / `_ask_edit_approval` / `_ask_question`（backend_host.py）定义了但**从未被调用**（引擎无权限钩子）→ 前端 ModalHost 的 permission/edit_diff 弹窗永远不会出现。需要给 QueryEngine/corecoder 加权限钩子。

## ⚪ P5：前端死代码

`frontend/terminal/src/components/` 下 4 个未引用的旧组件（三栏布局遗留）：
- `Composer.tsx`（被 PromptInput 替代）
- `Footer.tsx`（被 StatusBar 替代）
- `SidePanel.tsx`（被 StatusBar + TodoPanel/SwarmPanel 替代）
- `TranscriptPane.tsx`（被 ConversationView 替代）

删除或保留二选一（之前讨论过，未定）。

---

## 📦 阶段 2：corecoder 工具注册表化（对齐 dsh tools 组）

- [ ] `corecoder/tools/` 静态 `ALL_TOOLS` → `register_tool()` 注册表（可插拔、可覆盖）
- [ ] llm 拆 provider 概念（对齐 dsh llm 组：服务定义 + 多个 provider）
- [ ] 参考：dsh 的 `packages/core/tools`（ToolDefinition + schema + 注册）

## 🧩 阶段 3：插件系统（核心诉求，借鉴 DeepSeek Harness）

- [ ] `javis/plugins/`：loader（扫描/加载/卸载）+ 生命周期钩子 + 注册表
- [ ] bundle/profile 概念：一组插件 = 一个 profile（dsh 的 `bundle/base`）
- [ ] 顺带解决 `start_runtime`/`close_runtime` no-op（用插件钩子实现）
- [ ] 参考：本机 `/home/rensu/workspace/deepseek-harness` 的 `packages/bundle/`、`packages/core/agent-loop/`、Cordis 插件模型（ctx 服务 + inject + effect）

## 📈 测试与质量

- 总覆盖率 51%，重点补：
  - `corecoder/llm.py` 26%（流式解析/重试/超时，用 mock httpx 传输层）
  - `javis/host/backend_host.py` 36%（端到端协议测试）
  - `javis/session/session_storage.py` 54%
- 目标：51% → 70%+
- 维护：`uv run ruff check javis/ corecoder/`、`uv run mypy javis/`

## 🔧 杂项

- [ ] 决定 `.vscode/` 是否提交（当前被 .gitignore 忽略）
- [ ] README 架构图检查（host 精简后可能有模块路径过时）
- [ ] pyproject.toml 是否加 uv 阿里云镜像源（装包慢）

---

## 当前目录结构速览

```
javis/
  contracts/   纯契约：protocol(AgentBackend), types(AgentEvent), messages, usage
  host/        cli, runtime(含config/prompts), query_engine, backend_host, wire, react_launcher
  session/     session_storage, state, workspace
  commands/    registry（斜杠命令）
  engines/     registry + corecoder/backend
corecoder/     智能体引擎：agent, llm, tools/, context
frontend/terminal/  React/Ink TUI（TS）
```
