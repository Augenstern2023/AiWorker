# LLM 密钥在线轮换设计（Windows 凭据库）

## 背景与目标

当前 LLM 管理页面只允许保存 `api_key_env_var`，真实 API Key 仅从 Web/Worker
启动环境读取。这个方案避免了数据库保存密钥，但管理员无法在页面更换密钥，环境变量
更新后还必须重启 8899，造成明确的可用性回退。

本次改动允许系统管理员在 LLM 管理页面输入并替换 API Key。保存完成后，8899 和同一
Windows 用户下运行的 Worker 在下一次模型调用时立即读取新密钥，不要求重启服务。
已经发出的上游 HTTP 请求继续使用发出时的旧密钥，不中断在途请求。

## 凭据存储与解析

新增独立的 `LLMCredentialStore` 边界。生产实现使用 Python `keyring` 调用 Windows
Credential Manager：

- service name 固定为 `AiWorker.LLM`；
- username 使用 `provider:<provider_uuid>`；
- PostgreSQL 只保存提供商 UUID、配置和可选环境变量名，不保存 API Key 或密文；
- API、日志、审计、异常和页面响应都不返回密钥值；
- 测试使用内存实现，不访问开发机凭据库。

凭据解析顺序固定为：

1. Windows Credential Manager 中该提供商 UUID 对应的密钥；
2. 提供商配置的环境变量；
3. 两者都不存在时返回明确的“凭据缺失”错误。

运行时不缓存 API Key。`effective_default()`、`effective_model()`、
`provider_connection()`、模型同步及连接测试每次调用都通过同一解析函数读取凭据库，
因此 Web 和已经运行的 Worker 会在下一次模型请求时看到新值。环境变量继续作为无人值守
部署和非 Windows 环境的兼容兜底。

如果系统凭据库后端不可用，页面写入返回明确错误；只读取环境变量的现有部署仍可运行。
自定义提供商不再强制填写环境变量名，只要页面密钥或环境变量任一可用即可启用模型。

## 管理 API

保留现有提供商元数据接口，并继续拒绝在创建/更新元数据请求中夹带 `api_key`，避免密钥
意外进入通用审计对象。

新增两个专用接口：

- `PUT /api/llm/providers/{provider_id}/credential`：替换凭据库中的密钥；
- `DELETE /api/llm/providers/{provider_id}/credential`：删除页面管理的密钥，随后按环境
  变量兜底规则重新计算状态。

请求体使用 Pydantic `SecretStr`，长度为 1–4096。接口仅允许系统管理员，并继续受现有
CSRF 校验保护。由于当前 8899 默认是 HTTP，携带真实密钥的请求只允许来自 loopback；
非 loopback 管理必须先通过 HTTPS 反向代理访问。拒绝响应不包含请求值。

连接测试允许提交尚未保存的 `SecretStr`，用于在替换前验证新密钥；该值只用于一次上游
请求，不写入数据库或凭据库。提交临时密钥时应用同一 loopback/HTTPS 限制。

审计只记录动作、提供商 UUID、操作人、凭据状态和来源：

- `llm.provider.credential.replace`；
- `llm.provider.credential.clear`。

审计的 before/after 均不包含密钥、密钥长度、前后缀或哈希。删除提供商后尝试清理对应
凭据项；若系统凭据库清理失败，提供商删除仍保持成功，服务端只记录不含秘密的安全告警，
因为 UUID 不会复用且孤立项无法再由应用路由访问。

## 页面交互

提供商编辑区保留“API Key 环境变量”作为可选兜底，并新增密码输入框“API Key”：

- 已有提供商始终显示空输入框和“留空表示不修改”，不回显现有密钥；
- 输入新值后点击“保存提供商”，先保存元数据，再调用专用凭据接口；
- 两步均成功后清空输入框、重新读取凭据状态并显示“新密钥已生效”；
- 元数据成功而凭据保存失败时明确提示“提供商设置已保存，但新密钥保存失败”，不伪装
  成整体成功；
- “测试连接”优先使用当前输入框中的临时密钥，否则使用已保存凭据；
- 凭据状态显示“系统凭据库”“环境变量”或“缺失”，不显示密钥内容；
- 提供单独的“清除页面密钥”操作，并提示清除后可能回退到环境变量。

保存密钥不会重启 8899、不会终止 Worker，也不会修改 Windows 环境变量。

## 组件与文件边界

- `analysis/llm_credentials.py`：凭据库协议、Windows/keyring 实现、错误转换和测试内存实现
  所需接口；
- `analysis/llm_settings.py`：按 provider UUID 动态解析凭据，并向公开响应提供脱敏状态；
- `analysis/llm_settings_api.py`：专用密钥接口、传输安全守卫和脱敏审计；
- `templates/admin/config.html`：密码输入、凭据来源和清除按钮；
- `static/js/admin/workspace.js`：密钥保存、临时测试、清空表单和错误状态；
- `WebGUI.py` 与 Worker 的 `runtime_settings_repository()`：使用同一种默认凭据库实现；
- `pyproject.toml`/`uv.lock`：增加 `keyring` 依赖。

本次不增加 Alembic 迁移，不恢复 `api_key_encrypted`，不重新引入 Fernet 主密钥。

## 错误处理

- 提供商不存在：404；
- 非系统管理员：403；
- 非 loopback HTTP 提交密钥：400，并提示使用本机页面或 HTTPS；
- 空密钥或超长密钥：422；
- 系统凭据库不可用或写入失败：503，保留原密钥不变；
- 元数据保存成功但密钥写入失败：页面保留错误并重新加载真实状态；
- 凭据缺失：提供商保留，但不能设为默认、同步模型或发起生成。

凭据替换采用 keyring 的单项覆盖操作。只有写入成功后 API 才返回成功；写入失败不能先
删除旧值。

## 测试与验收

测试按 TDD 覆盖：

- 凭据库值优先于环境变量，清除后正确回退环境变量；
- 同一个仓储实例连续两次解析时能读取替换后的值，证明没有密钥缓存；
- Web 和模拟 Worker 仓储读取同一个凭据库并立即获得新值；
- 公开设置、异常和审计记录不包含提交的 API Key；
- 专用接口的管理员、CSRF、loopback/HTTPS 和输入长度约束；
- 页面不会回显密钥，保存后清空输入框，连接测试可使用未保存密钥；
- keyring 不可用时环境变量读取仍兼容，写入产生可操作错误；
- 现有环境变量配置、模型同步、默认模型及 Worker 测试继续通过。

验收时不使用生产 API Key。通过内存凭据库和本地模拟 LLM 验证即时切换；部署新代码所需
的一次服务重载与日后的密钥轮换是两件事。新版本加载后，后续换密钥不再要求重启。
