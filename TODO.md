# javis TODO

> 交接清单 — 当前状态：**129 tests passed，DeepSeek 连通，远端 main 已同步**。
>
> **Roadmap v2（2026-08-20 定稿）**：插件系统优先。插件是 javis 唯一的扩展机制——一切能力（工具/命令/provider/skill/记忆/MCP…）都以插件形态提供；核心注册表留在宿主层，插件只做"注册自己的贡献"。对齐 dsh 的 Cordis 简化版（插件 = Python 包 + manifest，ctx 注册，profile 组合）。

## 快速恢复环境（回家后第一步）

```bash
cd open-javis
uv sync --extra dev        # 装依赖（pytest/ruff/debugpy 在 dev 组）
.venv/bin/pytest -q        # 应 129 passed（uv run pytest 可能卡网络 sync，用 .venv 直跑）
uv run javis -p "hi"       # 验证 DeepSeek 连通（读 ~/.javis/config.json）
```

注意事项：
- PyPI 官方源很慢，装包慢就加 `--index-url https://mirrors.aliyun.com/pypi/simple/`
- VSCode 调试配置在 `.vscode/launch.json`（被 .gitignore 忽略，本机已有）

---

## Roadmap v2 总览

| 阶段 | 主题 | 状态 |
|---|---|---|
| **Phase 0** | 插件系统 + 核心注册表 | ⬜ 未开始（优先） |
| **Phase 1** | 现有能力插件化 + 修复（迁移期） | ⬜ 未开始 |
| **Phase 2** | 知识层插件（skill/记忆/plan/subagent） | ⬜ 未开始 |
| **Phase 3** | 生态插件（MCP/web/LSP/sandbox/teams） | ⬜ 未开始 |

参考：dsh（本机 `/home/rensu/workspace/deepseek-harness`）`packages/extensions`、`packages/preset`、`packages/bundle`；Cordis 组合模型。

---

## 🟦 Phase 0：插件系统 + 核心注册表（最先做）

> 交付物 = 可用的插件基础设施 + 第一个验证插件。插件系统设计定稿后，其余阶段都只是"写插件"。

- [ ] **F0.1 核心注册表建立**：corecoder 工具静态 `ALL_TOOLS` → `register_tool()` 注册表（可插拔/可覆盖/名称排序稳定）；插件系统的注册落点
- [ ] **F0.2 插件规范与发现**：manifest（name/version/deps/钩子声明）+ 三层目录发现（内置 `javis/plugins/` < 用户 `~/.javis/plugins/` < 项目 `<project>/.javis/plugins/`，对齐配置分层）+ 加载 + 命名冲突检测
- [ ] **F0.3 PluginContext**：注册 API（`register_tool` / `register_command` / `register_llm_provider` / `register_engine` / `register_config_namespace`）+ 事件总线（`subscribe`/`publish`，打通 wire 前端协议）
- [ ] **F0.4 生命周期**：`activate`/`deactivate` 钩子、依赖顺序、错误隔离（单插件崩溃不影响宿主，异常只记日志）
- [ ] **F0.5 Profile**：profile = 插件组合清单（`~/.javis/profiles/<name>.yaml`），支持 CLI 指定；默认 profile 启用内置插件
- [ ] **F0.6 配置命名空间**：插件声明自己的配置节（config.json 顶层键，deep-merge；未知键已容忍）
- [ ] **F0.7 示例插件 + 测试**：一个演示插件（如 `/hello` 命令 + 一个工具）验证全链路；单测覆盖发现/加载/冲突/生命周期/错误隔离

验收：`javis/plugins/` 结构定型；示例插件注册命令+工具全链路可用；tests 覆盖核心路径。

---

## 🟩 Phase 1：现有能力插件化 + 修复（迁移期）

> 验证插件机制成色：把已承诺但断裂的功能用插件机制补上（一举两得），把已有能力迁到插件形态。

- [ ] **F1.1 内置工具迁移**：read/write/bash/grep/glob/edit/agent 7 个工具 → 内置插件注册（验证注册表，删除静态 ALL_TOOLS）
- [ ] **F1.2 命令插件化 + 补全**：命令注册走 ctx；补缺失命令（选择器生成的 /fast /vim /voice /model + 斜杠 /plan /resume /provider /output-style /effort /passes）
- [ ] **F1.3 事件总线打通前端**：僵尸事件激活（todo_update / plan_mode_change / compact_progress / status）经插件事件总线实现并转发 wire
- [ ] **F1.4 LLM provider 插件化**：provider 注册走 ctx；**FallbackProvider** 作为首个 provider 插件（主 provider 失败自动切换，LLMProvider 签名已就绪）
- [ ] **F1.5 权限策略插件化**：permission_checker 策略（default/full_auto/plan）可被插件替换；确认 ModalHost 弹窗全链路（已部分接通）
- [ ] **F1.6 状态栏 token 修复**：total_usage 进状态快照（wire `_state_payload`），StatusBar 显示 input/output tokens

## 🟨 Phase 2：知识层插件

- [ ] **F2.1 Skill 插件**：`dir/SKILL.md` / 扁平 `.md` 发现 → `load_skill` 工具注入 `<skill_content>`；modelInvocable/userInvocable 策略；dsh `packages/skill` 家族模式
- [ ] **F2.2 记忆插件**：跨会话记忆（会话结束写摘要 → 新会话启动注入，MEMORY.md/scratchpad 模式；参考 pi 记忆、dsh session-query）
- [ ] **F2.3 Plan Mode 插件**：复杂任务先出计划 → 用户审阅/批准 → 执行（对齐 Claude Code plan 模式；注册 plan 命令 + 事件）
- [ ] **F2.4 Subagent 完善**：AgentTool（现有雏形）异步化、独立 LLM 参数（model/temperature）、可配置工具集（allow/deny）、context fork 隔离

## 🟧 Phase 3：生态插件

- [ ] **F3.1 MCP 插件**：MCP 客户端（stdio）注册为工具集（MCP-friendly：内置工具 + MCP 并存）
- [ ] **F3.2 Web 插件**：搜索/抓取工具（dsh web 家族）
- [ ] **F3.3 LSP 插件**：补全/诊断/引用（dsh lsp 家族）
- [ ] **F3.4 Sandbox 插件**：bash 执行隔离（bwrap/Docker，匹配监督强度）
- [ ] **F3.5 Agent Teams 插件**：多会话协调（前端 SwarmPanel 已存在，补后端；实验性）

---

## 测试与质量

- 当前 129 passed，总覆盖率 61%：
  - `corecoder/llm.py` 65%（缺口在流式错误路径）
  - `javis/host/backend_host.py` 44%（端到端协议测试，Phase 1 事件打通后自然覆盖）
  - `javis/session/session_storage.py` 54%
- 目标：61% → 70%+（Phase 0 插件系统单测 + Phase 1 事件链路测试贡献主要增量）
- 维护：`ruff check javis/ corecoder/`（历史遗留 10 个错误：F821 `LLM` 未定义（context.py）/ I001 import 排序 ×4 / F401 / S110，可顺手清）、`mypy javis/`

## 杂项

- [ ] 前端死代码清理（Composer/Footer/SidePanel/TranscriptPane 4 个旧组件，删除或保留二选一）
- [ ] 决定 `.vscode/` 是否提交（当前被 .gitignore 忽略）
- [ ] 迁移 `~/.javis/config.json` 到 v2 格式（v1 `engines` 节 → `providers`；api_key 移入 `~/.javis/.env`；不自动迁移，手动调整）
- [ ] README 架构图检查（host 精简后可能有模块路径过时）
- [ ] pyproject.toml 是否加 uv 阿里云镜像源（装包慢）

## 当前目录结构速览

```
javis/
  contracts/   纯契约：protocol(AgentBackend), types(AgentEvent), messages, usage
  host/        cli, runtime(含config/prompts), query_engine, backend_host, wire, react_launcher
  session/     session_storage, state, workspace
  commands/    registry（斜杠命令，Phase 1 插件化）
  engines/     registry + corecoder/backend
  plugins/     ← Phase 0 新建：插件系统（plugin/manifest/loader/context/profile）
corecoder/     智能体引擎：agent, llm(LLMProvider+LLMRequest), tools/(Phase 0 注册表化), context
frontend/terminal/  React/Ink TUI（TS）
```
