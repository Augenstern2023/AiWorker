# LLM 仅环境变量凭据实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 LLM 提供商只保存 API Key 环境变量名，Web 与 Worker 从进程环境读取真实凭据，并彻底停止 Fernet 密文和配置密钥的使用。

**架构：** `LLMSettingsRepository` 作为唯一凭据解析边界，统一验证变量名并通过 `os.getenv()` 取值；管理 API 和界面只传递环境变量名。现有 `api_key_encrypted` 列保留但应用不再使用，上线时在验证环境凭据后清空密文并删除 Fernet 密钥配置。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLAlchemy、PostgreSQL、Alpine.js、pytest、Playwright、uv

---

## 文件结构

- 修改 `analysis/llm_settings.py`：环境变量名校验、提供商凭据解析、数据库读写和运行时模型解析。
- 修改 `analysis/llm_settings_api.py`：仅环境变量的管理请求、连接测试和错误映射。
- 修改 `WebGUI.py`：移除 Fernet cipher 初始化，只注入系统数据库。
- 修改 `static/js/common/tasks.js`：管理页面的环境凭据编辑状态。
- 修改 `static/js/admin/workspace.js`：提供商保存与连接测试只提交变量名。
- 修改 `templates/admin/config.html`：用环境变量输入和状态提示替换密码输入。
- 修改 `templates/common/workspace/free_creative_modals.html`：更新模型凭据来源说明。
- 修改 `tests/analysis/test_llm_settings.py`：环境凭据存储、解析、缺失和遗留密文回归测试。
- 修改 `tests/analysis/test_llm_settings_api.py`：API 契约、连接测试和审计脱敏测试。
- 修改 `tests/test_llm_settings_browser.py`：管理页面交互和请求体测试。
- 修改 `tests/test_worker_launcher.py`：Worker 环境继承且不依赖 Fernet 密钥。
- 修改 `tests/test_web_security.py`：生产应用不再创建 LLM cipher。
- 修改 `tests/test_webgui_ui_changes.py`：页面文案和字段静态断言。

### 任务 1：将仓储切换为环境变量凭据

**文件：**
- 修改：`analysis/llm_settings.py:27-216,228-285,287-387,538-883,913-929`
- 测试：`tests/analysis/test_llm_settings.py:18-211,214-567`

- [ ] **步骤 1：编写失败的变量名与存储测试**

删除只验证 Fernet 文件共享的测试，新增以下真实行为测试：

```python
def test_provider_presets_define_environment_variable_names():
    module = _settings_module()
    assert module.PROVIDER_PRESETS["openai"]["env_key"] == "OPENAI_API_KEY"
    assert module.PROVIDER_PRESETS["deepseek"]["env_key"] == "DEEPSEEK_API_KEY"
    assert module.PROVIDER_PRESETS["minimax"]["env_key"] == "MINIMAX_API_KEY"
    assert module.PROVIDER_PRESETS["sub2api"]["env_key"] == "SUB2API_KEY"
    assert module.PROVIDER_PRESETS["custom"]["env_key"] == ""


@pytest.mark.parametrize("value", ["", "1BAD", "HAS-DASH", "HAS SPACE"])
def test_custom_provider_rejects_invalid_environment_variable_name(value):
    repository = _repository()
    with pytest.raises(ValueError, match="环境变量"):
        repository.create_provider(
            name="Custom",
            preset="custom",
            base_url="https://llm.example.test/v1",
            wire_api="chat_completions",
            api_key_env_var=value,
        )


def test_repository_stores_only_environment_variable_name(monkeypatch):
    repository = _repository()
    monkeypatch.setenv("CUSTOM_LLM_KEY", "runtime-secret")
    provider = repository.create_provider(
        name="Custom",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="CUSTOM_LLM_KEY",
    )
    with repository.database.connection() as connection:
        row = connection.execute(select(llm_providers)).mappings().one()
    assert row["api_key_env_var"] == "CUSTOM_LLM_KEY"
    assert row["api_key_encrypted"] is None
    assert provider["credential_status"] == "configured"
    assert "runtime-secret" not in repr(provider)
```

- [ ] **步骤 2：运行测试确认红灯**

运行：

```powershell
uv run pytest tests/analysis/test_llm_settings.py -k "environment_variable or stores_only" -v
```

预期：FAIL；预设没有 `env_key`，仓储也不接受 `api_key_env_var`。

- [ ] **步骤 3：实现变量名校验和无密文写入**

在 `analysis/llm_settings.py` 增加并复用：

```python
_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_api_key_env_var(value: str, *, required: bool = True) -> str:
    normalized = str(value or "").strip()
    if not normalized and not required:
        return ""
    if not _ENV_VAR_NAME.fullmatch(normalized):
        raise LLMSettingsError("API Key 环境变量名称无效")
    return normalized


def environment_api_key(variable_name: str) -> str:
    normalized = validate_api_key_env_var(variable_name)
    api_key = os.getenv(normalized, "").strip()
    if not api_key:
        raise LLMSecretError(f"环境变量 {normalized} 未配置或为空")
    return api_key
```

给每个预设增加 `env_key`。`create_provider()` 和 `update_provider()` 只写
`api_key_env_var`，并始终把 `api_key_encrypted` 设为 `None`；自定义提供商要求显式
变量名，内置预设允许使用其默认变量名。删除 `LLMSecretCipher` 及其文件创建逻辑。

- [ ] **步骤 4：编写失败的统一解析与遗留密文测试**

```python
def test_effective_default_reads_environment_and_ignores_legacy_ciphertext(monkeypatch):
    repository = _repository()
    monkeypatch.setenv("MODEL_GATEWAY_KEY", "environment-secret")
    provider = repository.create_provider(
        name="Gateway",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="MODEL_GATEWAY_KEY",
    )
    model = repository.create_model(provider["id"], model_id="model-a")
    with repository.database.transaction() as connection:
        connection.execute(
            llm_providers.update()
            .where(llm_providers.c.id == provider["id"])
            .values(api_key_encrypted="legacy-unreadable-value")
        )
    repository.set_default_model(model["id"])
    assert repository.effective_default()["api_key"] == "environment-secret"


def test_missing_environment_hides_models_and_blocks_default(monkeypatch):
    repository = _repository()
    monkeypatch.delenv("MISSING_LLM_KEY", raising=False)
    provider = repository.create_provider(
        name="Missing",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="MISSING_LLM_KEY",
    )
    model = repository.create_model(provider["id"], model_id="model-a")
    assert repository.settings_payload()["providers"][0]["credential_status"] == "missing"
    assert repository.available_models_payload()["items"] == []
    with pytest.raises(repository.conflict_error, match="MISSING_LLM_KEY"):
        repository.set_default_model(model["id"])
```

- [ ] **步骤 5：运行新测试确认红灯**

运行：

```powershell
uv run pytest tests/analysis/test_llm_settings.py -k "effective_default_reads_environment or missing_environment" -v
```

预期：FAIL；当前实现仍尝试解密 `api_key_encrypted`。

- [ ] **步骤 6：让所有运行时路径使用统一解析函数**

调整 `_provider_dict()`、`public_provider()`、`set_default_model()`、
`settings_payload()`、`available_models_payload()`、`effective_default()`、
`effective_model()`、`provider_connection()` 和 `import_legacy()`：

```python
def _credential_status(provider: dict[str, Any]) -> str:
    try:
        environment_api_key(provider["api_key_env_var"])
    except LLMSecretError:
        return "missing"
    return "configured"
```

公开响应包含 `api_key_env_var`、`credential_status`、
`credential_source="environment"` 和兼容字段 `has_api_key`，但绝不包含真实值或
`api_key_encrypted`。`runtime_settings_repository()` 改为
`LLMSettingsRepository(database)`，不再接收运行目录。

- [ ] **步骤 7：运行仓储测试并提交**

运行：

```powershell
uv run pytest tests/analysis/test_llm_settings.py -v
```

预期：全部 PASS。

提交：

```powershell
git add analysis/llm_settings.py tests/analysis/test_llm_settings.py
git commit -m "refactor(llm): resolve credentials from environment"
```

### 任务 2：收紧管理 API 为环境变量契约

**文件：**
- 修改：`analysis/llm_settings_api.py:42-86,123-169,274-322,465-503`
- 测试：`tests/analysis/test_llm_settings_api.py`

- [ ] **步骤 1：编写失败的 API 契约测试**

```python
def test_provider_routes_accept_environment_variable_and_reject_plaintext_key(monkeypatch):
    module = _api_module()
    repository = _repository()
    audit = _RecordingAudit()
    app = FastAPI()
    app.include_router(module.build_llm_settings_router(
        module.LLMSettingsService(repository),
        audit,
        administrator_dependency=lambda: _admin(),
    ))
    client = TestClient(app)
    monkeypatch.setenv("ROUTE_LLM_KEY", "route-secret")
    created = client.post("/api/llm/providers", json={
        "name": "Gateway",
        "preset": "custom",
        "base_url": "https://llm.example.test/v1",
        "wire_api": "chat_completions",
        "api_key_env_var": "ROUTE_LLM_KEY",
        "is_enabled": True,
    })
    assert created.status_code == 201
    assert created.json()["api_key_env_var"] == "ROUTE_LLM_KEY"
    rejected = client.post("/api/llm/providers", json={
        "name": "Legacy",
        "preset": "custom",
        "base_url": "https://llm.example.test/v1",
        "wire_api": "chat_completions",
        "api_key": "must-not-be-accepted",
    })
    assert rejected.status_code == 400
    assert "must-not-be-accepted" not in rejected.text
```

连接测试新增断言：保存过的提供商从 `provider_connection()` 取环境凭据；未保存草稿
提交 `api_key_env_var`，服务端读取环境变量；请求和审计均不包含真实密钥。

- [ ] **步骤 2：运行 API 测试确认红灯**

运行：

```powershell
uv run pytest tests/analysis/test_llm_settings_api.py -k "environment_variable or reject_plaintext" -v
```

预期：FAIL；当前请求模型忽略或接受 `api_key`，且没有环境变量字段。

- [ ] **步骤 3：实现最小 API 改动**

把请求基类改为拒绝无关额外字段，并替换凭据字段。为避免 FastAPI/Pydantic 的验证
响应把旧客户端提交的密钥原样回显，旧字段作为仅检测、不序列化的兼容字段声明，由
路由检查 `model_fields_set` 后返回不含输入值的 HTTP 400：

```python
class LLMSettingsRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")


class ProviderCreateRequest(LLMSettingsRequest):
    # 其余字段保持不变
    api_key_env_var: str = Field(default="", max_length=255)
    api_key: str | None = Field(default=None, max_length=4096, exclude=True, repr=False)


class ProviderUpdateRequest(LLMSettingsRequest):
    # 其余字段保持不变
    api_key_env_var: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096, exclude=True, repr=False)
    clear_api_key: bool | None = Field(default=None, exclude=True, repr=False)


class ConnectionTestRequest(LLMSettingsRequest):
    provider_id: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key_env_var: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096, exclude=True, repr=False)
    model_id: str = Field(min_length=1, max_length=255)
    wire_api: WireAPI | None = None
```

每条接收上述模型的路由首先执行：

```python
def reject_legacy_api_key_fields(payload: LLMSettingsRequest) -> None:
    forbidden = {"api_key", "clear_api_key"} & payload.model_fields_set
    if forbidden:
        raise HTTPException(400, "不再接受 API Key；请配置 API Key 环境变量名称")
```

路由不再读取 `payload.api_key`。已有提供商调用 `provider_connection()`；新提供商草稿
调用 `environment_api_key(payload.api_key_env_var)`，然后把结果仅作为内存参数传给
`LLMSettingsService.test_connection()`。

- [ ] **步骤 4：运行 API 测试并提交**

运行：

```powershell
uv run pytest tests/analysis/test_llm_settings_api.py -v
```

预期：全部 PASS，HTTP mock 收到环境变量中的测试值，响应和审计不含该值。

提交：

```powershell
git add analysis/llm_settings_api.py tests/analysis/test_llm_settings_api.py
git commit -m "refactor(llm): expose environment credential contract"
```

### 任务 3：更新 LLM 管理界面

**文件：**
- 修改：`static/js/common/tasks.js:141-161`
- 修改：`static/js/admin/workspace.js:946-1049,1057-1065,1215-1270`
- 修改：`templates/admin/config.html:65-110,175-191`
- 修改：`templates/common/workspace/free_creative_modals.html:9`
- 测试：`tests/test_llm_settings_browser.py`
- 测试：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写失败的浏览器和静态测试**

新增浏览器测试，断言页面只有变量名输入框：

```python
def test_llm_provider_uses_environment_variable_instead_of_password(page, llm_page):
    page.goto(llm_page)
    assert page.get_by_label("API Key 环境变量").is_visible()
    assert page.locator('input[type="password"]').count() == 0
    page.get_by_label("API Key 环境变量").fill("CUSTOM_LLM_KEY")
    page.get_by_role("button", name="保存提供商").click()
    request = page.evaluate("window.requests.at(-1)")
    assert request["body"]["api_key_env_var"] == "CUSTOM_LLM_KEY"
    assert "api_key" not in request["body"]
    assert "clear_api_key" not in request["body"]
```

静态测试断言模板不再包含“Fernet 加密保存”、`llm-provider-api-key`、
`clear_api_key`，并包含 `llm-provider-api-key-env-var` 和“环境变量”。

- [ ] **步骤 2：运行界面测试确认红灯**

运行：

```powershell
uv run pytest tests/test_llm_settings_browser.py tests/test_webgui_ui_changes.py -k "llm" -v
```

预期：FAIL；当前页面仍渲染密码框并提交 `api_key`。

- [ ] **步骤 3：实现环境变量编辑交互**

编辑器状态改为：

```javascript
state.editor = {
    id: provider.id,
    name: provider.name || '',
    preset: provider.preset || 'custom',
    base_url: provider.base_url || '',
    wire_api: provider.wire_api || 'chat_completions',
    api_key_env_var: provider.api_key_env_var || '',
    credential_status: provider.credential_status || 'missing',
    is_enabled: provider.is_enabled !== false
};
```

新建提供商使用 `preset.env_key`；切换预设同步默认变量名；保存和连接测试请求仅带
`api_key_env_var`。模板提示“服务与 Worker 从启动环境读取，不保存 API Key”，删除
清空密钥复选框，并把删除确认文案改为“不删除环境变量中的凭据”。

- [ ] **步骤 4：运行界面测试并提交**

运行：

```powershell
uv run pytest tests/test_llm_settings_browser.py tests/test_webgui_ui_changes.py -k "llm" -v
```

预期：全部 PASS。

提交：

```powershell
git add static/js/common/tasks.js static/js/admin/workspace.js templates/admin/config.html templates/common/workspace/free_creative_modals.html tests/test_llm_settings_browser.py tests/test_webgui_ui_changes.py
git commit -m "refactor(web): configure llm credentials by environment"
```

### 任务 4：移除 Web 与 Worker 的 Fernet 依赖

**文件：**
- 修改：`WebGUI.py:73,543-546`
- 修改：`tests/test_worker_launcher.py`
- 修改：`tests/test_web_security.py`
- 测试：`tests/analysis/test_llm.py`

- [ ] **步骤 1：编写失败的启动与 Worker 环境测试**

```python
def test_worker_env_inherits_provider_key_without_fernet_key(monkeypatch):
    monkeypatch.setenv("SUB2API_KEY", "worker-secret")
    monkeypatch.delenv("AIWORKER_LLM_CONFIG_KEY", raising=False)
    env = build_worker_env()
    assert env["SUB2API_KEY"] == "worker-secret"
    assert "AIWORKER_LLM_CONFIG_KEY" not in env
```

更新 Web 安全测试，断言 `WebGUI.llm_settings_repository.database` 是系统数据库，模块
不再导入或构造 `LLMSecretCipher`。

- [ ] **步骤 2：运行测试确认红灯**

运行：

```powershell
uv run pytest tests/test_worker_launcher.py tests/test_web_security.py -k "worker_env or llm" -v
```

预期：WebGUI 断言 FAIL，因为当前仍构造 cipher。

- [ ] **步骤 3：移除生产初始化中的 cipher**

将 WebGUI 初始化简化为：

```python
llm_settings_repository = LLMSettingsRepository(system_database)
```

删除 `LLMSecretCipher` 导入。保持 Worker 的 `os.environ.copy()` 行为，不增加注册表或
文件回退。

- [ ] **步骤 4：运行所有相关测试并提交**

运行：

```powershell
uv run pytest tests/analysis/test_llm_settings.py tests/analysis/test_llm_settings_api.py tests/analysis/test_llm.py tests/test_llm_settings_browser.py tests/test_worker_launcher.py tests/test_web_security.py tests/test_webgui_ui_changes.py -v
```

预期：全部 PASS。

提交：

```powershell
git add WebGUI.py tests/test_worker_launcher.py tests/test_web_security.py tests/analysis/test_llm.py
git commit -m "refactor(runtime): remove llm encryption key dependency"
```

### 任务 5：完整验证与生产配置切换

**文件：**
- 不创建或修改仓库文件；操作用户环境变量、现有 LLM 配置记录和 8899 服务。

- [ ] **步骤 1：运行静态与完整测试**

运行：

```powershell
git diff --check
uv run pre-commit run --all-files
uv run pytest
```

若完整测试导入 WebGUI 时缺少测试 PostgreSQL URL，先记录这一基线环境问题；不得把
生产数据库 URL 注入完整测试。相关测试套件必须全部通过，完整套件仅可在隔离测试库
可用时运行。

- [ ] **步骤 2：在清空密文前准备并验证所有环境凭据**

确认生产库只有已知的 Sub2API 与 Burncloud 提供商。复用已验证的用户级
`SUB2API_KEY`；在仍含旧 cipher 的主工作区进程中解密 Burncloud 凭据，并通过内存
变量写入 Windows 用户环境的 `BURNCLOUD_API_KEY`，全过程不打印真实值。分别请求两
个提供商的 `/models`，预期 HTTP 200。

- [ ] **步骤 3：切换提供商记录并清空密文**

在一个 PostgreSQL 事务中执行等价更新：

```sql
UPDATE llm_providers
SET api_key_env_var = CASE
        WHEN preset = 'sub2api' THEN 'SUB2API_KEY'
        WHEN name = 'Burncloud' THEN 'BURNCLOUD_API_KEY'
        ELSE api_key_env_var
    END,
    api_key_encrypted = NULL,
    updated_at = CURRENT_TIMESTAMP;
```

提交事务前检查每个启用提供商的变量名非空且当前进程能读取到非空值。提交后断言：

```sql
SELECT count(*) FROM llm_providers WHERE api_key_encrypted IS NOT NULL;
```

预期结果：`0`。

- [ ] **步骤 4：删除 Fernet 配置并重启 8899**

先确认将删除的精确路径分别是：

```text
D:\Crawler\AiWorker\.git\aiworker\llm_config.key
D:\Crawler\AiWorker\.runtime\llm_config.key
```

然后删除用户级 `AIWORKER_LLM_CONFIG_KEY` 和上述两个文件。停止精确监听 8899 的
AiWorker Uvicorn 进程，以显式加载用户级 `AIWORKER_SYSTEM_DATABASE_URL`、
`SUB2API_KEY`、`BURNCLOUD_API_KEY` 且不设置 `AIWORKER_LLM_CONFIG_KEY` 的环境启动
新服务。

- [ ] **步骤 5：端到端验收**

依次验证：

```text
GET /api/llm/settings：两个提供商 credential_status 均为 configured
GET /api/llm/available-models：默认 gpt-5.6-luna 可见
最小 JSON LLM 请求：返回 {"ok": true}
后台 Worker 风格的默认模型解析：不读取 SUB2API_KEY 以外的密钥文件
重试剧本审核任务：任务进入 succeeded，审核记录进入 pending_confirmation
```

检查新日志中不再出现 `Stored LLM API key cannot be decrypted`。最后确认 Git 工作
区只包含本计划内提交，且用户原有未跟踪文档未被修改。
