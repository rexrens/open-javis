# 修复 P0：`/theme` 与 `/turns` 选择器流程断裂

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TUI 选择器切换主题和最大轮次真正生效——注册缺失的 `/theme`、`/turns` 两条斜杠命令。

**Architecture:** 根因是 `javis/host/backend_host.py` 的 `_build_select_command_line` 会生成 `/theme xxx`、`/turns xxx`，但 `javis/commands/registry.py` 没注册这两条命令，`handle_line` 的 `bundle.commands.lookup()` 返回 `None`，整行被当作普通用户消息喂给 LLM。修法是直接在 `create_default_command_registry()` 里注册两个 handler：`/theme` 写 `AppStateStore`，`/turns` 调 `QueryEngine.set_max_turns`（已存在）。`CommandContext` 已带 `engine` 和 `app_state` 两个字段，无需改协议、无需改前端。

**Tech Stack:** Python 3.10+ / asyncio / dataclasses / pytest-asyncio

**范围说明：** 本计划只覆盖 P0 的 2 个子项（`/theme`、`/turns`）。其余 5 个（`/permissions`、`/fast`、`/vim`、`/voice`、`/model`）留待后续按同模式补。

---

## 文件结构

- **修改：`javis/commands/registry.py`** —— 在 `create_default_command_registry()` 内新增 `_theme_handler`、`_turns_handler` 两个 handler，并注册为 `theme`、`turns` 命令。
- **测试：`tests/test_javis/test_backend_host.py`** —— 追加 `_apply_select_command` 端到端测试，证明「选择器 → 命令 → 状态更新」全链路通了（这正是 P0 断掉的那一环）。
- **测试：`tests/test_javis/test_runtime.py`** —— 扩展现有 `test_build_javis_runtime_includes_commands`，断言 `theme`/`turns` 出现在命令列表里。

---

### Task 1: 注册 `/theme` 命令（写 AppState）

**文件：**
- 修改：`javis/commands/registry.py:100-122`（在 `_status_handler` 之后、`registry.register(...)` 区段之前插入 handler；在 `status` 注册行之后追加 register 调用）
- 测试：`tests/test_javis/test_backend_host.py`（追加 1 个失败测试）

- [ ] **Step 1: 写失败测试**

在 `tests/test_javis/test_backend_host.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_apply_select_theme_updates_state(isolated_env):
    """P0: /theme selector must update AppState, not fall through to the LLM."""
    host, events = await _make_host(isolated_env)
    try:
        await host._apply_select_command("theme", "dark")
    finally:
        await close_runtime(host._bundle)

    assert host._bundle.app_state.get().theme == "dark"
```

- [ ] **Step 2: 跑测试确认失败**

运行：`uv run pytest tests/test_javis/test_backend_host.py::test_apply_select_theme_updates_state -q`
预期：FAIL，报 `assert 'default' == 'dark'`（`/theme dark` 被当成普通消息喂给 FakeBackend，状态没变）

- [ ] **Step 3: 实现——注册 `/theme` handler**

在 `javis/commands/registry.py` 的 `_status_handler` 之后、`registry.register(Command("help", ...))` 之前插入：

```python
    async def _theme_handler(args: str, context: CommandContext) -> CommandResult:
        value = args.strip()
        if not value:
            return CommandResult(message="Usage: /theme <name>")
        context.app_state.set(theme=value)
        return CommandResult(message=f"Theme set to {value}.")
```

在 `registry.register(Command("status", "Show session status", _status_handler))` 之后追加：

```python
    registry.register(Command("theme", "Set UI theme", _theme_handler))
```

- [ ] **Step 4: 跑测试确认通过**

运行：`uv run pytest tests/test_javis/test_backend_host.py -q`
预期：全部 PASS

- [ ] **Step 5: Commit**

```bash
git add javis/commands/registry.py tests/test_javis/test_backend_host.py
git commit -m "fix(javis): register /theme command so the theme selector works"
```

---

### Task 2: 注册 `/turns` 命令（写 QueryEngine）

**文件：**
- 修改：`javis/commands/registry.py`（在 `_theme_handler` 之后插入 `_turns_handler`，在 `theme` 注册行之后追加 register 调用）
- 测试：`tests/test_javis/test_backend_host.py`（追加 1 个失败测试 + 1 个回归测试）

- [ ] **Step 1: 写失败测试**

在 `tests/test_javis/test_backend_host.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_apply_select_turns_updates_engine(isolated_env):
    """P0: /turns selector must update QueryEngine.max_turns, not fall through to the LLM."""
    host, events = await _make_host(isolated_env)
    try:
        await host._apply_select_command("turns", "64")
    finally:
        await close_runtime(host._bundle)

    assert host._bundle.engine.max_turns == 64


@pytest.mark.asyncio
async def test_apply_select_turns_unlimited_clears_limit(isolated_env):
    """/turns unlimited must reset the engine's max-turn limit to None."""
    host, events = await _make_host(isolated_env)
    try:
        await host._apply_select_command("turns", "64")
        await host._apply_select_command("turns", "unlimited")
    finally:
        await close_runtime(host._bundle)

    assert host._bundle.engine.max_turns is None
```

- [ ] **Step 2: 跑测试确认失败**

运行：`uv run pytest tests/test_javis/test_backend_host.py::test_apply_select_turns_updates_engine -q`
预期：FAIL，报 `assert None == 64`（`/turns 64` 被当成普通消息，`max_turns` 保持 `None`）

- [ ] **Step 3: 实现——注册 `/turns` handler**

在 `javis/commands/registry.py` 的 `_theme_handler` 之后插入：

```python
    async def _turns_handler(args: str, context: CommandContext) -> CommandResult:
        value = args.strip()
        if value.lower() in ("", "unlimited", "none"):
            context.engine.set_max_turns(None)
            return CommandResult(message="Max turns set to unlimited.")
        try:
            turns = int(value)
        except ValueError:
            return CommandResult(message=f"Invalid max turns: {value!r}. Use a number or 'unlimited'.")
        context.engine.set_max_turns(turns)
        return CommandResult(message=f"Max turns set to {turns}.")
```

在 `registry.register(Command("theme", ...))` 之后追加：

```python
    registry.register(Command("turns", "Set max turns", _turns_handler))
```

说明：`QueryEngine.set_max_turns` 已存在（`javis/host/query_engine.py:96`），内部做 `None if max_turns is None else max(1, int(max_turns))`，所以 `0`/负数会被钳到 `1`，无需在 handler 里重复钳制。

- [ ] **Step 4: 跑测试确认通过**

运行：`uv run pytest tests/test_javis/test_backend_host.py -q`
预期：全部 PASS（含 Task 1 的 1 个 + 本 Task 的 2 个新测试）

- [ ] **Step 5: Commit**

```bash
git add javis/commands/registry.py tests/test_javis/test_backend_host.py
git commit -m "fix(javis): register /turns command so the max-turns selector works"
```

---

### Task 3: 命令注册列表断言 + 全量回归

**文件：**
- 测试：`tests/test_javis/test_runtime.py`（扩展现有断言）

- [ ] **Step 1: 扩展命令注册断言**

在 `tests/test_javis/test_runtime.py` 的 `test_build_javis_runtime_includes_commands` 里，把断言块改为：

```python
    command_names = {cmd.name for cmd in bundle.commands.list_commands()}
    assert "help" in command_names
    assert "exit" in command_names
    assert "clear" in command_names
    assert "theme" in command_names
    assert "turns" in command_names
```

- [ ] **Step 2: 跑全量测试**

运行：`uv run pytest tests/ -q`
预期：全部 PASS（现有 77 个 + 新增 3 个 = 80 passed）

- [ ] **Step 3: 跑 lint（如可用）**

运行：`uv run ruff check javis/commands/registry.py`
预期：无新增告警

- [ ] **Step 4: Commit**

```bash
git add tests/test_javis/test_runtime.py
git commit -m "test(javis): assert /theme and /turns are registered commands"
```

---

## 自检

1. **规格覆盖度：** P0 的 2 个子项（`/theme`、`/turns`）各有：失败测试（Task 1 Step 1、Task 2 Step 1）、实现（Task 1 Step 3、Task 2 Step 3）、通过验证（Step 4）、commit（Step 5）。✅
2. **占位符扫描：** 无「待定 / TODO / 补充细节」，所有代码块均为可直接粘贴的具体代码。✅
3. **类型一致性：** handler 统一返回 `CommandResult`；`context.app_state.set(theme=...)` 对应 `AppStateStore.set(**updates)`（`javis/session/state.py`）；`context.engine.set_max_turns(...)` 对应 `QueryEngine.set_max_turns`（`javis/host/query_engine.py:96`）。`CommandContext.engine` 虽标注为 `object`，但现有 `_clear_handler`/`_status_handler` 已直接调用其方法，本计划沿用同一模式。✅
