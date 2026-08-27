# javis 配置文件设计 v2

> 状态：待确认。确认后按此实现。

## 1. 基本原则

1. **配置与密钥分离**：
   - 非敏感配置 → `config.json`
   - 密钥 → `.env`（环境变量体系）
   - 兼容：`config.json` 中可读 `apiKey`（旧格式兼容，读取时警告）
2. **分层合并**（对齐 opencode/Claude Code）：默认值 < 全局 < 项目 < CLI/env，**深合并（deep merge）不替换**，冲突键后者覆盖
3. **密钥优先级**（对齐 dsh）：进程环境变量 > `~/.javis/.env` > 项目 `.env`
4. **格式简单**：JSON（Python 标准库支持，无额外依赖）；`.env` 用 dotenv 语法（corecoder 已有 `_load_dotenv`）
5. **强校验**：pydantic 模型校验；**未知键警告（宽容）**——为插件命名空间预留

## 2. 配置文件位置与层次

| 层次 | 位置 | 说明 |
|---|---|---|
| 内置默认 | 代码中（`JavisConfig` 模型默认值） | 最低优先级 |
| **全局** | `~/.javis/config.json` + `~/.javis/.env` | 主配置，启动时不存在则自动创建默认模板 |
| **项目级** | `<项目根>/.javis/config.json` + `<项目根>/.javis/.env` | 覆盖全局；与全局深合并 |
| CLI / 环境变量 | `--model`、`JAVIS_*` 等 | 最高优先级 |

- 项目级配置从**当前工作目录向上查找**最近的 `.javis/`（对齐 corecoder `_load_dotenv` 的向上查找逻辑）
- `JAVIS_WORKSPACE` 环境变量可覆盖全局位置（已有机制）

### 优先级合并模型

```
CLI 参数 / 进程环境变量        ← 最高
  ↓ 覆盖
<项目>/.javis/config.json       ← 深合并
  ↓ 覆盖
~/.javis/config.json            ← 深合并
  ↓ 覆盖
内置默认值（JavisConfig 模型）   ← 最低
```

### 密钥解析优先级（独立于配置）

```
① 进程环境变量（最高，只读）
    apiKeyEnv 指定的变量名 > <PROVIDER_NAME>_API_KEY 推断 > CORECODER_API_KEY 全局兜底
② ~/.javis/.env（只读兜底）
③ <项目>/.javis/.env（只读兜底）
④ config.json 中 providers.<name>.apiKey（兼容读取，警告）
```

## 3. config.json 完整格式

```jsonc
{
  // —— 模型选择 ——
  "provider": "deepseek",           // 默认 LLM 供应商（必须存在于 providers）
  "model": "deepseek-v4-flash",         // 默认模型（缺省取 provider.models[0].id）
  "fallback_provider": "my-vllm",
  "fallback_model": "Qwen/Qwen2.5-72B-Instruct",

  // —— LLM 供应商 ——
  "providers": {
    "deepseek": {
      "baseUrl": "https://api.deepseek.com",
      "api": "openai-completions",  // 协议类型：openai-completions | openai | anthropic | litellm
      "apiKeyEnv": "DEEPSEEK_API_KEY",  // 显式 env 变量名（缺省推断 <NAME>_API_KEY）
      "models": [
        {
          "id": "deepseek-chat",
          "contextWindow": 128000,
          "maxTokens": 8192
        }
      ]
    },
    "openai": {
      "baseUrl": "https://api.openai.com/v1",
      "api": "openai",
      "apiKeyEnv": "OPENAI_API_KEY",
      "models": [
        { "id": "gpt-4o", "contextWindow": 128000, "maxTokens": 16384 }
      ]
    },
    "my-vllm": {
      "baseUrl": "http://localhost:8000/v1",
      "api": "openai-completions",
      "apiKeyEnv": "VLLM_API_KEY",
      "models": [
        { "id": "Qwen/Qwen2.5-72B-Instruct", "contextWindow": 128000, "maxTokens": 8192 }
      ]
    }
  },

  // —— 外观 ——
  "appearance": {
    "theme": "default",             // default | dark | light | solarized | gruvbox
    "output_style": "default"       // default | codex
  },

  // —— 会话 ——
  "session": {
    "max_turns": 32,
    "permission_mode": "default",   // default | plan | full_auto
    "fast_mode": false
  },

  // —— 编辑器 ——
  "editor": {
    "vim_enabled": false
  },

  // —— 日志 ——
  "logging": {
    "level": "info"                 // debug | info | warning | error
  },

  // —— 权限（预留，实现依赖权限系统）——
  "permission": {
    "mode": "default",
    "allowed_tools": [],            // 空 = 不限制
    "denied_tools": [],
    "path_rules": [                 // 路径规则：{"pattern": "/etc/*", "allow": false}
    ],
    "denied_commands": []           // 如 "rm -rf /"
  },

  // —— 插件（预留，阶段 3）——
  "plugins": {}
}
```

### 模型选择语义

- `provider` 指向 `providers` 中的键，缺失则报错（明确提示可用列表）
- `model` 可省略：取 `providers[provider].models[0].id`
- `model` 指定但不在该 provider 的 models 中：警告 + 仍使用（允许运行时切换未登记模型，如 CLI `--model`）

## 4. .env 格式

```
# ~/.javis/.env 或 <项目>/.javis/.env
DEEPSEEK_API_KEY=sk-xxx
OPENAI_API_KEY=sk-xxx
CORECODER_API_KEY=sk-xxx      # 全局兜底（可选）
CORECODER_MODEL=deepseek-chat # 可选：覆盖默认模型
CORECODER_BASE_URL=...        # 可选：覆盖默认 baseUrl
```

- 变量名规范：`<PROVIDER_NAME>_API_KEY`（大写 + snake），`apiKeyEnv` 可显式指定任意名
- 仅 `~/.javis/.env` 与 `<项目>/.javis/.env` 两个位置被读取（**不读任意 cwd 的 .env**，避免误读他人文件）

## 5. 启动行为

1. 启动时检查 `~/.javis/config.json`：
   - 不存在 → 创建默认模板（含 `provider`/`model`/`providers` 骨架，注释用 JSON 可表示的最小示例）
   - 存在但 JSON 解析失败 → 报错并提示（不静默覆盖）
2. 项目级 `<项目>/.javis/config.json` 存在则深合并；不存在则跳过
3. 校验失败（pydantic）→ 报错指出具体字段；未知键 → 警告（log warning）


## 6. pydantic Schema（实现参考）

```python
# javis/session/config.py
from pydantic import BaseModel, Field

class ModelConfig(BaseModel):
    id: str
    context_window: int = 128_000
    max_tokens: int = 8192

class ProviderConfig(BaseModel):
    base_url: str
    api: Literal["openai-completions", "openai", "anthropic", "litellm"] = "openai-completions"
    api_key_env: str | None = None      # 缺省推断 <NAME>_API_KEY
    api_key: str | None = None          # v1 兼容，读取警告
    models: list[ModelConfig] = []

class AppearanceConfig(BaseModel): ...
class SessionConfig(BaseModel):
    max_turns: int | None = 32
    permission_mode: Literal["default", "plan", "full_auto"] = "default"
    fast_mode: bool = False
class EditorConfig(BaseModel):
    vim_enabled: bool = False
class LoggingConfig(BaseModel):
    level: Literal["debug", "info", "warning", "error"] = "info"
class PermissionConfig(BaseModel):
    mode: Literal["default", "plan", "full_auto"] = "default"
    allowed_tools: list[str] = []
    denied_tools: list[str] = []
    path_rules: list[PathRule] = []
    denied_commands: list[str] = []

class JavisConfig(BaseModel):
    model_config = ConfigDict(extra="allow")   # 未知键宽容（插件预留）
    provider: str | None = None                 # 缺省：providers 第一个键
    model: str | None = None
    providers: dict[str, ProviderConfig] = {}
    appearance: AppearanceConfig = AppearanceConfig()
    session: SessionConfig = SessionConfig()
    editor: EditorConfig = EditorConfig()
    logging: LoggingConfig = LoggingConfig()
    permission: PermissionConfig = PermissionConfig()
    plugins: dict[str, Any] = {}
```

## 8. 实现文件清单

| 文件 | 内容 |
|---|---|
| `javis/session/config.py` | pydantic 模型 + 加载/深合并/校验/默认创建 |
| `javis/session/credentials.py` | 密钥解析：环境变量 > .env > apiKey 兼容 |
| `javis/session/workspace.py` | 扩展：项目级 `.javis/` 向上查找 |
| `javis/app/runtime.py` | 接入新配置（load_config + provider/model 解析；engine 固定 corecoder） |
| `corecoder/config.py` | 保留 CORECODER_* 兼容；新增从 providers 建 Config |
| `tests/test_javis/test_config.py` | 重写：模型校验/深合并/迁移/密钥解析 |
| `tests/test_javis/test_credentials.py` | 新增：优先级/环境变量/.env 解析 |

## 9. 待确认的最终决策点

- [x] ① 模型选择：顶层 `provider` + `model` 字段
- [x] ② engine 固定内建 corecoder（选择层已移除），provider（LLM 供应商）独立解析
- [x] ③ 密钥关联：`apiKeyEnv` 显式 > 名称推断 > 全局兜底
- [x] ④ 项目级配置：`<项目根>/.javis/config.json`，向上查找
- [ ] ⑤ `.env` 只读两个位置（`~/.javis/.env` + `<项目>/.javis/.env`），不读任意 cwd .env —— 是否接受？（比 corecoder 现在的"任意 cwd 向上找"更严格）
- [ ] ⑦ 默认模板自动创建 `~/.javis/config.json`——是否接受？（现在 config.json 是我们手动放的）
