# javis TODO

> 交接清单 — 当前状态：**174 tests passed，DeepSeek 连通，插件系统（Phase 0）核心已完成，已合并远端 main（未推送）**。
>
> **Roadmap v2（2026-08-20 定稿）**：插件系统优先。插件是 javis 唯一的扩展机制——一切能力（工具/命令/provider/skill/记忆/MCP…）都以插件形态提供；核心注册表留在宿主层，插件只做"注册自己的贡献"。对齐 dsh 的 Cordis 简化版（插件 = Python 包 + manifest，ctx 注册，profile 组合）。

## 快速恢复环境（回家后第一步）

```bash
cd open-javis
uv sync --extra dev        # 装依赖（pytest/ruff/debugpy 在 dev 组）
.venv/bin/pytest -q        # 应 174 passed（uv run pytest 可能卡网络 sync，用 .venv 直跑）
uv run javis -p "hi"       # 验证 DeepSeek 连通（读 ~/.javis/config.json）
```

注意事项：
- PyPI 官方源很慢，装包慢就加 `--index-url https://mirrors.aliyun.com/pypi/simple/`
- VSCode 调试配置在 `.vscode/launch.json`（被 .gitignore 忽略，本机已有）

---

## Roadmap v2 总览

| 阶段 | 主题 | 状态 |
|---|---|---|
| **Phase 0** | 插件系统 + 核心注册表 | 🟦 核心完成（2026-08-21）；manifest/profile/内置层待续 |
| **Phase 1** | 现有能力插件化 + 修复（迁移期） | ⬜ 未开始（基础设施已就绪） |
| **Phase 2** | 知识层插件（skill/记忆/plan/subagent） | ⬜ 未开始 |
| **Phase 3** | 生态插件（MCP/web/LSP/sandbox/teams） | ⬜ 未开始 |

参考：dsh（本机 `/home/rensu/workspace/deepseek-harness`）`packages/extensions`、`packages/preset`、`packages/bundle`；Cordis 组合模型。

---

## 🟦 Phase 0：插件系统 + 核心注册表（核心已完成 ✅）

> 交付物 = 可用的插件基础设施 + 第一个验证插件。**2026-08-21 已完成核心**：`javis/plugins/` 六模块（errors/context/instance/registry/loader/__init__）+ 工具注册表化 + runtime 接入 + 示例插件（hello_tool/hello_command）+ 45 个新测试。

- [x] **F0.1 核心注册表建立**：`corecoder.tools` 静态 `ALL_TOOLS` → `register_tool()/get_tool()/all_tools()/unregister_tool()` 注册表（幂等覆盖 + 告警；LLM 层 `_format_tools` 按名排序保证请求前缀稳定）
- [~] **F0.2 插件规范与发现**：✅ 两层目录发现（用户 `~/.javis/plugins/` < 项目 `<project>/.javis/plugins/`，同名项目层覆盖）+ importlib 加载 + 冲突检测；⬜ 未做：manifest（name/version/deps/钩子声明）、内置层 `javis/plugins/`（内置 bundle）
- [~] **F0.3 PluginContext**：✅ 注册 API（`register_tool` / `register_command` / `register_engine`）+ 事件总线（`on/emit/emit_serial`，插件间通信）；⬜ 未做：`register_llm_provider` / `register_config_namespace`、事件打通 wire 前端协议（Phase 0 明确 YAGNI）
- [x] **F0.4 生命周期**：`PluginInstance` 六态状态机（PENDING→LOADING→ACTIVE/FAILED→UNLOADING→DISPOSED）、inject 依赖等待（asyncio.Condition，10s 超时）、错误隔离（单插件崩溃不影响宿主，仅记日志）、disposers 逆序清理
- [ ] **F0.5 Profile**：未做（设计预留——`plugin_dirs()` 已抽象为"目录源列表"，profile = 往列表插 `~/.javis/profiles/<name>/plugins/` + 配置覆盖层，后续一行配置启用）
- [~] **F0.6 配置命名空间**：✅ 插件声明 pydantic `Config`，`config.json` 的 `plugins.<name>.config` 校验注入（键用声明名 spec.name；非 dict 配置容错）；⬜ 未做：插件注册自己的顶层配置节（deep-merge 到 config.json 顶层键）
- [x] **F0.7 示例插件 + 测试**：`examples/plugins/hello_tool.py`（greet 工具）+ `hello_command.py`（/hello 命令）；45 个新测试覆盖发现/加载/状态机/依赖等待/生命周期/错误隔离/runtime 集成

**Phase 0 验收**：`javis/plugins/` 结构定型 ✅；示例插件注册命令+工具全链路可用 ✅；tests 覆盖核心路径 ✅。剩余：manifest、内置插件层、profile、config 顶层键、provider 注册（见下方延后项）。

---

## 🟩 Phase 1：现有能力插件化 + 修复（迁移期）

> 验证插件机制成色：把已承诺但断裂的功能用插件机制补上（一举两得），把已有能力迁到插件形态。**基础设施已全部就绪（ctx 注册 + 生命周期 + 配置注入），此阶段主要是"写插件/迁移"。**

- [~] **F1.1 内置工具迁移**：✅ 7 个工具已注册表化（import 时自注册，`ALL_TOOLS` 保留为兼容别名）；⬜ 迁为内置插件形态（`javis/plugins/` 内置层）
- [~] **F1.2 命令插件化 + 补全**：✅ 命令注册走 ctx（`ctx.register_command`，插件命令与内建命令合并）；⬜ 补缺失命令（选择器生成的 /fast /vim /voice /model + 斜杠 /plan /resume /provider /output-style /effort /passes）
- [ ] **F1.3 事件总线打通前端**：僵尸事件激活（todo_update / plan_mode_change / compact_progress / status）经插件事件总线实现并转发 wire（需先补事件→wire 桥）
- [ ] **F1.4 LLM provider 插件化**：provider 注册走 ctx（需先补 `ctx.register_llm_provider`）；**FallbackProvider** 作为首个 provider 插件（主 provider 失败自动切换；`config.json` 已预留 `fallback_provider`/`fallback_model` 字段）
- [ ] **F1.5 权限策略插件化**：permission_checker 策略（default/full_auto/plan）可被插件替换；确认 ModalHost 弹窗全链路
- [ ] **F1.6 状态栏 token 修复**：total_usage 进状态快照（wire `_state_payload`），StatusBar 显示 input/output tokens

## 🧩 Phase 0 完成后：插件系统延后项（2026-08-21 最终审查甄别）

> 插件系统核心已完成并合并 main。以下为最终代码审查标记的延后项，**建议按优先级清理**：

- [ ] **P0-A 生命周期钩子错误隔离**：`run_start_hooks` 逐 hook try/except（当前一个 on_start 抛异常会中断其余 hook 并冒泡到 `start_runtime`）；可用 `_safe_call(handler, kind)` 收敛 `_consume`/`close`/`run_start_hooks` 三处重复
- [ ] **P0-B LoadReport 合并消费**：`load_plugins`（import 失败/disabled）与 `activate_all`（loaded/failed）两个 report 当前都被 runtime 丢弃——至少让 runtime 打一条结构化摘要（loaded/failed/skipped + 原因）
- [ ] **P0-C 裸 bool 配置语义**：`"hello": false` 当前按空配置（enabled）处理而非禁用——支持裸 bool 作为 enable/disable 简写（loader 目前只 warning）
- [ ] **P0-D 工具注册表 reset/重名边界**：`unregister_tool` 不恢复被覆盖的内建工具（插件与内建工具重名时 close 后内建缺失）；测试隔离需要 reset 机制（`test_examples` 的 greet 与 `test_tool_registry` 的 test_echo 写入全局注册表无 teardown）
- [ ] **P0-E 文档补全**：`docs/plugins.md` 补 `config=None` 语义（未声明 Config 时 apply 第二参数为 None）、`ctx.get` 未提供服务抛 KeyError、生命周期箭头注记（配置/依赖失败直接 PENDING→FAILED 不经 LOADING）
- [ ] **P0-F runtime 死代码清理**：`runtime.py` 中 `if cfg else` ×5（cfg 恒非 None）与 `commands if cfg is not None else ...` 死分支
- [ ] **P0-G 测试夹具补全**：`test_context` 的 "tools" 桩缺 `unregister_tool`（与 close() 的撤销路径隐性耦合，新增"注册工具后 close"测试会 AttributeError）
- [x] **P0-H mypy 基线**：`corecoder/tools/*.py` 预存 11 个 mypy 错误（`execute` 签名 override 等，`strict` 下）——本次净增 0，但影响 `uv run mypy corecoder/` 全绿

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

- 当前 174 passed，总覆盖率 65%（Phase 0 插件测试贡献主要增量）：
  - `corecoder/llm.py` 65%（缺口在流式错误路径）
  - `javis/host/backend_host.py` 44%（端到端协议测试，Phase 1 事件打通后自然覆盖）
  - `javis/session/session_storage.py` 54%
- 目标：65% → 70%+（Phase 1 事件链路测试 + 插件延后项测试）
- **质量门已全绿（2026-08-21）**：`ruff check javis/ corecoder/` → All checks passed（0 错误）；`mypy javis/ corecoder/` → Success（51 files，strict）

## 杂项

- [ ] 推送 main 到远端（当前本地 ahead，插件系统 + 远端合并未推送）
- [ ] 前端死代码清理（Composer/Footer/SidePanel/TranscriptPane 4 个旧组件，删除或保留二选一）
- [ ] 决定 `.vscode/` 是否提交（当前被 .gitignore 忽略）
- [ ] 迁移 `~/.javis/config.json` 到 v2 格式（v1 `engines` 节 → `providers`；api_key 移入 `~/.javis/.env`；不自动迁移，手动调整）
- [ ] README 架构图检查（host 精简后可能有模块路径过时；插件系统接入后 README 架构图需补 `javis/plugins/`）
- [ ] pyproject.toml 是否加 uv 阿里云镜像源（装包慢）
- [ ] 未跟踪文件处理（`banner`、`joke.txt`、`docs/superpowers/plans/2026-08-18-fix-p0-theme-turns-selector.md` 当前 untracked，决定提交/删除/忽略）

## 当前目录结构速览

```
javis/
  contracts/   纯契约：protocol(AgentBackend), types(AgentEvent), messages, usage
  host/        cli, runtime(插件激活+生命周期), query_engine, backend_host, wire, react_launcher
  session/     session_storage, state, workspace
  commands/    registry（斜杠命令，Phase 1 插件化）
  engines/     registry + corecoder/backend
  plugins/     ← Phase 0 完成：errors/context/instance/registry/loader/__init__（插件内核）
corecoder/     智能体引擎：agent, llm(LLMProvider+LLMRequest), tools/(register_tool 注册表), context
frontend/terminal/  React/Ink TUI（TS）
examples/plugins/   示例插件（hello_tool / hello_command）
```
