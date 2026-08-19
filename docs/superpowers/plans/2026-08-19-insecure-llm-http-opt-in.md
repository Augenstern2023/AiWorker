# 明文 HTTP LLM 显式授权实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 只有在运行配置明确授权时，允许 Sub2api 通过公网明文 HTTP 发送 API 密钥，并恢复编剧大师响应。

**架构：** 在统一 LLM provider 增加默认关闭的 `allow_insecure_http` 属性；配置解析层负责把 `LLM_ALLOW_INSECURE_HTTP` 的真值传入 provider。当前本机 `.env` 显式配置既有 Sub2api HTTP 地址与授权开关，其他环境不配置开关时继续被安全拦截。

**技术栈：** Python 3.11、httpx、pytest、python-dotenv、FastAPI

---

## 文件结构

- 修改：`tests/analysis/test_llm.py`——覆盖默认拒绝、显式授权和配置传递行为。
- 修改：`analysis/llm.py`——解析显式授权并在 provider 传输检查中使用。
- 修改：`config/bilibili_config.py`——从环境读取 `LLM_ALLOW_INSECURE_HTTP`。
- 修改（本机、Git 忽略）：`.env`——配置现有 Sub2api 地址与显式授权开关。

### 任务 1：实现显式明文 HTTP 授权

**文件：**
- 修改：`tests/analysis/test_llm.py`
- 修改：`analysis/llm.py:94-152,345-410`
- 修改：`config/bilibili_config.py:89-100`

- [ ] **步骤 1：编写失败的 provider 行为测试**

在现有默认拒绝测试之后增加：

```python
def test_sub2api_allows_public_plaintext_http_when_explicitly_enabled():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json={"output_text": "ok"})

    provider = OpenAICompatibleLLMProvider(
        base_url="http://203.0.113.10:8080/v1",
        api_key="key",
        model="gpt-5.6-sol",
        provider_name="sub2api",
        wire_api="responses",
        allow_insecure_http=True,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        provider.complete_text([{"role": "user", "content": "普通分析"}])
    )
    assert result == "ok"
    assert [request.url.path for request in requests] == ["/v1/responses"]
```

- [ ] **步骤 2：编写失败的配置传递测试**

```python
def test_configured_provider_propagates_insecure_http_opt_in():
    class DummyConfig:
        LLM_PROVIDER = "sub2api"
        LLM_BASE_URL = "http://203.0.113.10:8080/v1"
        LLM_API_KEY = "key"
        LLM_ALLOW_INSECURE_HTTP = "true"

    provider = create_llm_provider_from_config(DummyConfig)

    assert provider.allow_insecure_http is True
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/analysis/test_llm.py -k "plaintext_http or insecure_http_opt_in" -q -p no:cacheprovider --basetemp=.runtime/tmp/pytest-llm-http-red
```

预期：新增行为测试因 provider 不接受 `allow_insecure_http` 参数而失败，配置测试因属性不存在而失败；原默认拒绝测试通过。

- [ ] **步骤 4：编写最少实现代码**

在 `analysis/llm.py` 中增加：

```python
def _boolean_setting(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
```

给 `OpenAICompatibleLLMProvider.__init__` 增加 `allow_insecure_http: bool = False`，保存为实例属性，并把公网 HTTP 拦截条件改为同时要求 `not self.allow_insecure_http`。`resolve_llm_settings` 返回：

```python
"allow_insecure_http": _boolean_setting(
    getattr(config_module, f"{prefix}_ALLOW_INSECURE_HTTP", "")
    or getattr(config_module, "LLM_ALLOW_INSECURE_HTTP", "")
),
```

`create_llm_provider_from_config` 将该值传给 provider。`config/bilibili_config.py` 增加：

```python
LLM_ALLOW_INSECURE_HTTP = os.getenv("LLM_ALLOW_INSECURE_HTTP", "")
```

- [ ] **步骤 5：运行测试验证通过**

重复步骤 3 的命令，预期相关测试全部通过。

- [ ] **步骤 6：运行 LLM 与编剧大师回归测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/analysis/test_llm.py tests/test_writer_agent_service.py tests/test_writer_agent_api.py -q -p no:cacheprovider --basetemp=.runtime/tmp/pytest-llm-http-regression
```

预期：全部通过。

- [ ] **步骤 7：提交代码与测试**

```powershell
git add analysis/llm.py config/bilibili_config.py tests/analysis/test_llm.py
git commit -m "fix: allow explicit insecure LLM HTTP"
```

### 任务 2：配置并验证当前运行环境

**文件：**
- 修改：`.env`

- [ ] **步骤 1：写入本机显式授权配置**

将 `.env` 中的空地址改为：

```dotenv
LLM_BASE_URL=http://54.92.99.171:8080/v1
LLM_ALLOW_INSECURE_HTTP=true
NO_PROXY=127.0.0.1,localhost,54.92.99.171
```

不修改或输出 `SUB2API_KEY`。`NO_PROXY` 确保该明文连接不经过本机 HTTP 代理。

- [ ] **步骤 2：验证配置解析但不打印凭据**

运行一个只输出 provider 名称、地址、模型和授权状态的 Python 检查，断言 `api_key_set=True`，不得打印密钥内容。

- [ ] **步骤 3：执行一次最小真实 LLM 请求**

通过 `create_llm_provider_from_config(config)` 发送短 JSON 请求，只输出成功状态和 provider，不输出 Authorization header 或密钥。

- [ ] **步骤 4：重启 WebGUI 并验证服务**

确认 `8899` 的监听进程属于当前 WebGUI，停止后用隐藏窗口重新启动；检查 `/review/writer-agent` 在未登录时返回登录跳转，证明服务重新加载配置并正常提供页面。

- [ ] **步骤 5：执行完成前检查**

```powershell
.\.venv\Scripts\python.exe tools/file_header_manager.py --check analysis/llm.py config/bilibili_config.py tests/analysis/test_llm.py
git diff --check -- analysis/llm.py config/bilibili_config.py tests/analysis/test_llm.py
```

预期：文件头合规，diff 无空白错误。
