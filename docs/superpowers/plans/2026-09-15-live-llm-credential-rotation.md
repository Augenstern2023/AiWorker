# LLM 密钥在线轮换实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让系统管理员在 LLM 管理页面把 API Key 保存到 Windows Credential Manager，并让 Web 与 Worker 在下一次模型调用时立即使用新值，无需重启 8899。

**架构：** 新增可注入的 `LLMCredentialStore`，生产实现通过 `keyring` 访问操作系统凭据库，测试使用内存假实现。`LLMSettingsRepository` 以 provider UUID 动态解析“凭据库优先、环境变量兜底”，专用 FastAPI 路由负责写入和脱敏审计，管理页面只提交新值且永不回显。

**技术栈：** Python 3.11、keyring、FastAPI/Pydantic、SQLAlchemy Core、Alpine.js、pytest、Playwright、uv。

---

## 文件结构

### 新建文件

- `analysis/llm_credentials.py`：凭据库协议、keyring 适配器、运行时工厂和安全错误。
- `tests/analysis/test_llm_credentials.py`：keyring 适配、缺失后端和秘密不泄露测试。

### 修改文件

- `pyproject.toml`、`uv.lock`：加入 `keyring>=25,<26`。
- `analysis/llm_settings.py`：注入凭据库、动态解析与来源状态。
- `analysis/llm_settings_api.py`：密钥替换/清除接口、loopback/HTTPS 守卫和临时密钥测试。
- `WebGUI.py`：Web 仓储使用运行时凭据库工厂。
- `templates/admin/config.html`：密码框、凭据来源和清除操作。
- `static/js/admin/workspace.js`：保存、清除、临时测试和输入清空逻辑。
- `tests/analysis/test_llm_settings.py`：解析优先级、即时轮换与环境变量回退。
- `tests/analysis/test_llm_settings_api.py`：授权、传输安全、审计和专用接口。
- `tests/test_llm_settings_browser.py`：页面交互和请求体回归。
- `tests/test_webgui_ui_changes.py`、`tests/test_web_security.py`、`tests/test_worker_launcher.py`：静态安全和 Worker 动态读取回归。

本实现不修改 Alembic，不读取或恢复 `api_key_encrypted`，不重启正在运行的 8899。

---

### 任务 1：建立操作系统凭据库边界

**文件：**
- 创建：`analysis/llm_credentials.py`
- 创建：`tests/analysis/test_llm_credentials.py`
- 修改：`pyproject.toml`
- 修改：`uv.lock`

- [ ] **步骤 1：编写失败的凭据库测试**

测试用假 keyring backend 验证 provider UUID 到 keyring username 的稳定映射、覆盖写入、删除、空值拒绝和异常脱敏：

```python
def test_keyring_store_replaces_provider_secret_without_exposing_it():
    backend = RecordingKeyring()
    store = KeyringLLMCredentialStore(backend)

    store.set("provider-1", "first-secret")
    store.set("provider-1", "second-secret")

    assert store.get("provider-1") == "second-secret"
    assert backend.last_service == "AiWorker.LLM"
    assert backend.last_username == "provider:provider-1"
    assert "second-secret" not in repr(store)
```

另写 `NoKeyringError`/写入失败测试，要求抛出 `LLMCredentialStoreError("系统凭据库不可用")`，异常中不出现传入的密钥。

- [ ] **步骤 2：运行测试确认功能缺失**

运行：

```powershell
uv run pytest tests/analysis/test_llm_credentials.py -q
```

预期：FAIL，原因是 `analysis.llm_credentials` 尚不存在。

- [ ] **步骤 3：加入 keyring 依赖**

运行：

```powershell
uv add "keyring>=25,<26"
```

确认只修改 `pyproject.toml` 和 `uv.lock`，不创建仓库内 uv 缓存目录。

- [ ] **步骤 4：实现最小凭据库适配器**

接口固定为：

```python
class LLMCredentialStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...
    def set(self, provider_id: str, api_key: str) -> None: ...
    def delete(self, provider_id: str) -> None: ...

class KeyringLLMCredentialStore:
    service_name = "AiWorker.LLM"

    def __init__(self, backend: Any | None = None) -> None: ...
    def get(self, provider_id: str) -> str | None: ...
    def set(self, provider_id: str, api_key: str) -> None: ...
    def delete(self, provider_id: str) -> None: ...
```

`get()` 在凭据不存在或无可用 backend 时返回 `None`，让环境变量部署继续工作；`set()` 和 `delete()` 的 backend 失败转换为不含秘密的 `LLMCredentialStoreError`。

- [ ] **步骤 5：运行凭据库测试**

运行：

```powershell
uv run pytest tests/analysis/test_llm_credentials.py -q
```

预期：PASS。

- [ ] **步骤 6：提交任务 1**

```powershell
git add analysis/llm_credentials.py tests/analysis/test_llm_credentials.py pyproject.toml uv.lock
git commit -m "feat(llm): add operating system credential store"
```

---

### 任务 2：让仓储动态解析并立即轮换凭据

**文件：**
- 修改：`analysis/llm_settings.py`
- 修改：`tests/analysis/test_llm_settings.py`
- 修改：`tests/test_worker_launcher.py`

- [ ] **步骤 1：编写失败的动态解析测试**

扩展测试 `_repository(credential_store=...)`，并用内存凭据库断言优先级和即时变化：

```python
def test_repository_reads_replaced_vault_secret_without_restart(monkeypatch):
    monkeypatch.setenv("ROTATION_FALLBACK_KEY", "environment-secret")
    vault = MemoryCredentialStore()
    repository = _repository(credential_store=vault)
    provider = repository.create_provider(
        name="Rotation", preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="ROTATION_FALLBACK_KEY",
    )
    vault.set(provider["id"], "first-secret")
    assert repository.provider_connection(provider["id"])["api_key"] == "first-secret"
    vault.set(provider["id"], "second-secret")
    assert repository.provider_connection(provider["id"])["api_key"] == "second-secret"
```

补充清除后回退环境变量、凭据库优先、环境变量单独可用、公开响应只显示
`credential_source`/`credential_status`、Web 与两个仓储实例共享同一 store 的测试。

- [ ] **步骤 2：运行测试确认当前只读取环境变量**

```powershell
uv run pytest tests/analysis/test_llm_settings.py tests/test_worker_launcher.py -q
```

预期：新增测试 FAIL，旧环境变量测试保持 PASS。

- [ ] **步骤 3：实现仓储注入和统一解析**

`LLMSettingsRepository` 接受可选 `credential_store`，所有运行时路径调用：

```python
def resolve_provider_api_key(self, provider: Mapping[str, Any]) -> tuple[str, str]:
    provider_id = str(provider["id"])
    stored = self.credential_store.get(provider_id) if self.credential_store else None
    if stored:
        return stored, "credential_store"
    variable = validate_api_key_env_var(provider.get("api_key_env_var", ""), required=False)
    if variable:
        return environment_api_key(variable), "environment"
    raise LLMSecretError("该提供商尚未配置 API Key")
```

`effective_default()`、`effective_model()`、`provider_connection()`、设置默认模型校验、可用模型列表和公开状态只通过该方法解析。自定义提供商允许空的环境变量名。新增
`replace_api_key(provider_id, api_key)` 和 `clear_api_key(provider_id)`，先验证提供商存在再调用 store。

- [ ] **步骤 4：运行仓储和 Worker 测试**

```powershell
uv run pytest tests/analysis/test_llm_settings.py tests/test_worker_launcher.py -q
```

预期：PASS；连续解析返回新值，公开对象中没有任何测试密钥。

- [ ] **步骤 5：提交任务 2**

```powershell
git add analysis/llm_settings.py tests/analysis/test_llm_settings.py tests/test_worker_launcher.py
git commit -m "feat(llm): resolve provider keys dynamically"
```

---

### 任务 3：新增专用密钥接口和脱敏审计

**文件：**
- 修改：`analysis/llm_settings_api.py`
- 修改：`tests/analysis/test_llm_settings_api.py`
- 修改：`tests/test_web_security.py`

- [ ] **步骤 1：编写失败的 API 安全测试**

使用 `TestClient(base_url="http://127.0.0.1")` 验证系统管理员可替换密钥且响应、审计和异常不含密钥；同一仓储下一次调用立即返回新值。增加以下边界：

```python
def test_non_loopback_http_cannot_submit_provider_secret():
    response = TestClient(app, base_url="http://testserver").put(
        f"/api/llm/providers/{provider_id}/credential",
        json={"api_key": "never-log-this"},
    )
    assert response.status_code == 400
    assert "本机或 HTTPS" in response.json()["detail"]
    assert "never-log-this" not in response.text
```

同时验证 HTTPS 远端允许、非管理员 403、空值 422、store 写入失败 503、清除后回退环境变量，以及提交临时 key 的连接测试不持久化该值。

- [ ] **步骤 2：运行测试确认专用路由不存在**

```powershell
uv run pytest tests/analysis/test_llm_settings_api.py tests/test_web_security.py -q
```

预期：新增路由测试 FAIL（404 或旧接口拒绝密钥）。

- [ ] **步骤 3：实现请求模型和传输守卫**

新增：

```python
class ProviderCredentialRequest(LLMSettingsRequest):
    api_key: SecretStr = Field(min_length=1, max_length=4096)

def require_secure_secret_transport(request: Request) -> None:
    host = request.client.host if request.client else ""
    if request.url.scheme != "https" and host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(400, "API Key 只能通过本机页面或 HTTPS 提交")
```

新增 PUT/DELETE credential 路由。成功响应仅包含 provider ID、`credential_status` 和
`credential_source`。审计 before/after 只使用这些字段。

- [ ] **步骤 4：允许连接测试使用一次性 SecretStr**

`ConnectionTestRequest.api_key` 改为 `SecretStr | None`。仅在字段存在时调用传输守卫，
通过 `get_secret_value()` 传给 `LLMSettingsService.test_connection()`，请求完成即丢弃；
不调用 repository 的写入方法。

- [ ] **步骤 5：运行 API 与安全回归**

```powershell
uv run pytest tests/analysis/test_llm_settings_api.py tests/test_web_security.py -q
```

预期：PASS；测试输出、响应和审计不出现测试密钥。

- [ ] **步骤 6：提交任务 3**

```powershell
git add analysis/llm_settings_api.py tests/analysis/test_llm_settings_api.py tests/test_web_security.py
git commit -m "feat(llm): add secure credential rotation API"
```

---

### 任务 4：恢复页面密钥编辑并保证保存后清空

**文件：**
- 修改：`templates/admin/config.html`
- 修改：`static/js/admin/workspace.js`
- 修改：`tests/test_llm_settings_browser.py`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写失败的浏览器交互测试**

调整 mock API，记录 credential PUT/DELETE，并增加：

```python
page.get_by_label("API Key").fill("rotated-secret")
page.get_by_role("button", name="保存提供商").click()
expect.poll(lambda: credential_request()["body"]["api_key"]).to_be("rotated-secret")
expect(page.get_by_label("API Key")).to_have_value("")
expect(page.get_by_text("新密钥已生效", exact=True)).to_be_visible()
```

补充页面源码和浏览器断言：密钥框为 `type=password`，选择提供商不会填入旧值，临时连接
测试提交当前输入，空输入使用已保存凭据，清除按钮不把密钥放入 URL。

- [ ] **步骤 2：运行测试确认页面没有密钥输入框**

```powershell
uv run pytest tests/test_llm_settings_browser.py tests/test_webgui_ui_changes.py -q
```

预期：新增测试 FAIL，原因是“API Key”控件不存在。

- [ ] **步骤 3：实现模板控件**

在环境变量兜底字段下增加：

```html
<label class="form-label" for="llm-provider-api-key">API Key</label>
<input id="llm-provider-api-key" type="password" x-model="llmSettings.editor.api_key"
       maxlength="4096" autocomplete="new-password" placeholder="留空表示不修改">
```

凭据来源 badge 显示“系统凭据库”“环境变量”或“凭据缺失”。只有已有 provider 且来源为
`credential_store` 时显示“清除页面密钥”。

- [ ] **步骤 4：实现前端保存与临时测试**

`selectLLMProvider()` 和 `startNewLLMProvider()` 始终初始化 `api_key: ''`。保存元数据成功后，
若 `api_key.trim()` 非空，则向 credential PUT 提交；成功后在 `finally` 清空本地字段并刷新
设置。连接测试有输入时提交 `api_key`，否则不提交该字段。清除操作使用 credential DELETE
并重新加载状态。

- [ ] **步骤 5：运行页面测试**

```powershell
uv run pytest tests/test_llm_settings_browser.py tests/test_webgui_ui_changes.py -q
```

预期：PASS；DOM 和请求记录之外没有任何密钥回显。

- [ ] **步骤 6：提交任务 4**

```powershell
git add templates/admin/config.html static/js/admin/workspace.js tests/test_llm_settings_browser.py tests/test_webgui_ui_changes.py
git commit -m "feat(web): rotate llm keys without restart"
```

---

### 任务 5：接入 Web/Worker 并完成回归验证

**文件：**
- 修改：`WebGUI.py`
- 修改：`analysis/llm_settings.py`
- 修改：`tests/test_worker_launcher.py`
- 修改：`README.md`

- [ ] **步骤 1：编写失败的运行时工厂测试**

Monkeypatch `default_llm_credential_store()`，断言 Web 仓储和
`runtime_settings_repository()` 都使用同一 keyring 适配类型，并且 worker 环境不复制任何
页面密钥或新增主加密密钥。

- [ ] **步骤 2：运行运行时测试确认工厂尚未接入**

```powershell
uv run pytest tests/test_worker_launcher.py tests/test_web_security.py -q
```

预期：新增工厂断言 FAIL。

- [ ] **步骤 3：接入运行时工厂并更新文档**

Web 和 Worker 创建 `LLMSettingsRepository` 时调用 `default_llm_credential_store()`。README
说明：页面密钥保存在当前 Windows 用户凭据库、保存后下一次调用生效、环境变量为兜底、
远程 HTTP 页面不能提交密钥。

- [ ] **步骤 4：运行完整相关测试矩阵**

先把 `AIWORKER_SYSTEM_DATABASE_URL` 指向已配置的测试数据库并确保其处于当前 Alembic
head，然后运行：

```powershell
uv run pytest tests/analysis/test_llm_credentials.py tests/analysis/test_llm_settings.py tests/analysis/test_llm_settings_api.py tests/analysis/test_llm.py tests/test_llm_settings_browser.py tests/test_webgui_ui_changes.py tests/test_web_security.py tests/test_worker_launcher.py -q
uv run pre-commit run --files analysis/llm_credentials.py analysis/llm_settings.py analysis/llm_settings_api.py WebGUI.py templates/admin/config.html static/js/admin/workspace.js tests/analysis/test_llm_credentials.py tests/analysis/test_llm_settings.py tests/analysis/test_llm_settings_api.py tests/test_llm_settings_browser.py tests/test_webgui_ui_changes.py tests/test_web_security.py tests/test_worker_launcher.py README.md pyproject.toml
```

预期：所有相关测试和 hooks PASS；测试和日志不包含任何真实 API Key。

- [ ] **步骤 5：复核 8899 未受开发过程影响**

比较实施前后的监听 PID，并请求：

```powershell
Invoke-WebRequest http://127.0.0.1:8899/login -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8899/openapi.json -UseBasicParsing
```

预期：PID 未变化，两个请求均为 HTTP 200。新代码需要一次受控部署重载才能首次加载；加载
后页面换密钥不再要求重启。

- [ ] **步骤 6：提交任务 5**

```powershell
git add WebGUI.py analysis/llm_settings.py tests/test_worker_launcher.py README.md
git commit -m "docs(llm): document live credential rotation"
```

提交前运行 `git diff --cached --name-only`，确保没有 `.runtime`、备份、真实密钥或用户的
`docs/自由创作现状与迁移评估.md`。
