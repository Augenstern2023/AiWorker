# Sub2api GPT-5.6 Sol 自动推理路由实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 移除云雾与 OpenCode 文本 LLM 线路，将所有共用 LLM 工作流切换到 Sub2api `gpt-5.6-sol`，并按每次请求的任务复杂度自动选择推理强度。

**架构：** 在 `analysis.llm` 内加入无外部调用的确定性任务路由器，由消息内容和长度选择 `low`、`medium`、`high`、`xhigh` 或 `max`。Provider 优先调用同一 Sub2api 的 Responses API，仅在协议不兼容时回退同一服务商的 Chat Completions；运行时 `.env` 和代码默认值统一为 Sub2api，并移除备用模型与备用 provider 配置入口。Sub2api base URL 必须是 HTTPS 或本机加密隧道地址。

**技术栈：** Python 3.11、httpx、pytest、python-dotenv、uv

---

## 文件结构

- 修改：`analysis/llm.py`——推理强度归一化与自动路由、每请求强度注入、Responses 协议兼容回退、Sub2api 默认值。
- 修改：`config/bilibili_config.py`——全局 LLM 默认配置升级为 Sub2api `gpt-5.6-sol`、Responses 和自动推理。
- 修改：`tests/analysis/test_llm.py`——自动路由、显式覆盖、协议回退、鉴权错误和默认配置的行为测试。
- 修改（本机、Git 忽略）：`.env`——删除云雾/OpenCode 运行时配置，使用 `SUB2API_KEY`。

### 任务 1：实现任务级自动推理强度

**文件：**
- 修改：`tests/analysis/test_llm.py`
- 修改：`analysis/llm.py`

- [ ] **步骤 1：编写失败的自动路由测试**

在导入列表加入 `resolve_reasoning_effort` 和 `select_reasoning_effort`，并添加表驱动测试。每个 fixture 使用手工确定的期望值；这些测试要能抓住路由顺序、长度门槛或显式覆盖被破坏的情况。

```python
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("请从短文本中提取标题并分类。", "low"),
        ("请总结本周数据并给出常规分析。", "medium"),
        ("请完成人物设计、故事梗概和分集规划。", "high"),
        ("请审核剧本逻辑，交叉核验人物动机和时间线。", "xhigh"),
        ("请深度优化剧本并审核全部伏笔。" + "长上下文" * 8000, "max"),
    ],
)
def test_select_reasoning_effort_routes_representative_tasks(content, expected):
    messages = [{"role": "user", "content": content}]

    assert select_reasoning_effort(messages) == expected


@pytest.mark.parametrize("configured", ["none", "low", "medium", "high", "xhigh", "max"])
def test_resolve_reasoning_effort_preserves_explicit_override(configured):
    messages = [{"role": "user", "content": "请深度优化剧本" + "长上下文" * 5000}]

    assert resolve_reasoning_effort(configured, messages) == configured


def test_resolve_reasoning_effort_treats_invalid_configuration_as_auto():
    messages = [{"role": "user", "content": "请提取标题并分类"}]

    assert resolve_reasoning_effort("turbo", messages) == "low"
```

- [ ] **步骤 2：运行测试验证正确失败**

运行：

```powershell
uv run pytest tests/analysis/test_llm.py -k "select_reasoning_effort or resolve_reasoning_effort" -q
```

预期：测试收集阶段因两个函数尚不存在而失败；失败原因不是 fixture、导入路径或测试环境错误。

- [ ] **步骤 3：实现最小自动路由器**

在 `analysis/llm.py` 集中定义支持值、任务关键词和门槛，并实现两个纯函数：

```python
SUPPORTED_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
LOW_REASONING_MARKERS = ("提取", "抽取", "分类", "格式转换", "修复json", "extract", "classify", "format")
HIGH_REASONING_MARKERS = ("人物设计", "故事梗概", "分集", "剧本生成", "创作", "策划", "screenplay", "outline")
DEEP_REASONING_MARKERS = ("审核剧本", "剧本审核", "深度优化", "交叉核验", "时间线", "review screenplay", "optimize screenplay")
LONG_CONTEXT_CHARS = 20_000
MAX_CONTEXT_CHARS = 30_000


def select_reasoning_effort(messages: List[Dict[str, str]]) -> str:
    text = "\n".join(str(message.get("content", "")) for message in messages)
    normalized = text.lower()
    if any(marker in normalized for marker in DEEP_REASONING_MARKERS):
        return "max" if len(text) >= MAX_CONTEXT_CHARS else "xhigh"
    if any(marker in normalized for marker in HIGH_REASONING_MARKERS):
        return "xhigh" if len(text) >= LONG_CONTEXT_CHARS else "high"
    if len(text) >= LONG_CONTEXT_CHARS:
        return "high"
    if any(marker in normalized for marker in LOW_REASONING_MARKERS) and len(text) < 8_000:
        return "low"
    return "medium"


def resolve_reasoning_effort(configured: str, messages: List[Dict[str, str]]) -> str:
    normalized = str(configured or "auto").strip().lower()
    if normalized in SUPPORTED_REASONING_EFFORTS:
        return normalized
    return select_reasoning_effort(messages)
```

在 Responses 与 Chat Completions 请求构造前调用 `resolve_reasoning_effort(self.reasoning_effort, messages)`。两种协议必须针对同一 messages 得到同一结果；不得用共享可变属性保存“本次”强度，以免并发请求互相污染。

- [ ] **步骤 4：运行定向测试确认通过**

运行：

```powershell
uv run pytest tests/analysis/test_llm.py -k "select_reasoning_effort or resolve_reasoning_effort or reasoning_effort" -q
```

预期：自动路由、显式覆盖和已有推理参数测试全部通过。

- [ ] **步骤 5：提交任务 1**

```powershell
git add analysis/llm.py tests/analysis/test_llm.py
git commit -m "feat: add task-aware LLM reasoning routing"
```

### 任务 2：切换 Sub2api GPT-5.6 Sol 并限制协议回退

**文件：**
- 修改：`tests/analysis/test_llm.py`
- 修改：`analysis/llm.py`
- 修改：`config/bilibili_config.py`

- [ ] **步骤 1：编写失败的 Provider 默认值和协议回退测试**

替换旧的 `gpt-5.5` 默认断言，并新增真实 provider 边界测试。MockTransport 只替换外部 HTTP 边界，provider、请求构造、错误分类和回退逻辑均使用真实代码。

```python
def test_resolve_llm_settings_defaults_to_sub2api_gpt_5p6_sol(monkeypatch):
    class DummyConfig:
        LLM_PROVIDER = "sub2api"
        LLM_BASE_URL = ""
        LLM_API_KEY = ""
        LLM_MODEL = ""
        LLM_WIRE_API = ""
        LLM_REASONING_EFFORT = ""

    monkeypatch.setenv("SUB2API_KEY", "secret")
    settings = resolve_llm_settings(DummyConfig)

    assert settings["provider"] == "sub2api"
    assert settings["model"] == "gpt-5.6-sol"
    assert settings["wire_api"] == "responses"
    assert settings["reasoning_effort"] == "auto"


def test_responses_unsupported_falls_back_to_same_sub2api_chat_completions():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/responses"):
            return httpx.Response(404, request=request, text="route not found")
        return httpx.Response(200, request=request, json={
            "choices": [{"message": {"content": '{"ok": true}'}}],
        })

    provider = OpenAICompatibleLLMProvider(
        base_url="https://sub2api.example.test/v1",
        api_key="key",
        model="gpt-5.6-sol",
        provider_name="sub2api",
        wire_api="responses",
        reasoning_effort="auto",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    messages = [{"role": "user", "content": "请完成人物设计和分集规划"}]

    assert asyncio.run(provider.complete_text(messages, json_mode=True)) == '{"ok": true}'
    assert [path for path, _ in requests] == ["/v1/responses", "/v1/chat/completions"]
    assert requests[0][1]["model"] == requests[1][1]["model"] == "gpt-5.6-sol"
    assert requests[0][1]["reasoning"] == {"effort": "high"}
    assert requests[1][1]["reasoning_effort"] == "high"
    assert "temperature" not in requests[1][1]


@pytest.mark.parametrize("status", [401, 403])
def test_responses_auth_errors_do_not_fall_back_to_chat_completions(status):
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(status, request=request, text="denied")

    provider = OpenAICompatibleLLMProvider(
        base_url="https://sub2api.example.test/v1",
        api_key="key",
        model="gpt-5.6-sol",
        wire_api="responses",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.complete_text([{"role": "user", "content": "普通分析"}]))
    assert paths == ["/v1/responses"]
```

- [ ] **步骤 2：运行测试验证正确失败**

运行：

```powershell
uv run pytest tests/analysis/test_llm.py -k "defaults_to_sub2api_gpt_5p6_sol or responses_unsupported or auth_errors" -q
```

预期：默认值仍为 `gpt-5.5`、Chat Completions 和 `high`，且 404 尚不能触发受限回退，因此测试失败。

- [ ] **步骤 3：实现 Sub2api 默认值与受限回退**

将 `provider_defaults("sub2api")` 与 `config/bilibili_config.py` 的默认值统一为：

```python
{
    "name": "sub2api",
    "base_url": "",
    "env_key": "SUB2API_KEY",
    "model": "gpt-5.6-sol",
    "wire_api": "responses",
    "reasoning_effort": "auto",
}
```

增加协议错误分类，并仅为已确认的不兼容状态回退：

```python
def is_responses_api_unsupported_exception(exc: Exception) -> bool:
    if isinstance(exc, ResponsesAPIUnsupportedError):
        return True
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and int(exc.response.status_code) in {404, 405, 415, 422}
    )
```

`_complete_text_with_model` 捕获异常后先调用该函数；返回 `False` 时原样抛出。Chat Completions 在推理强度不为 `none` 时不发送 `temperature`，避免推理模型的采样参数与推理参数冲突。

删除 `resolve_secondary_llm_settings` 及 `LLM_SECONDARY_*` 生产配置入口；provider 工厂固定不注入备用模型或备用 provider。

- [ ] **步骤 4：运行任务 2 定向测试**

运行：

```powershell
uv run pytest tests/analysis/test_llm.py -q
```

预期：`tests/analysis/test_llm.py` 全部通过，输出中无网络请求、密钥或未处理警告。

- [ ] **步骤 5：提交任务 2**

```powershell
git add analysis/llm.py config/bilibili_config.py tests/analysis/test_llm.py
git commit -m "fix: route LLM workloads through Sub2api GPT-5.6 Sol"
```

### 任务 3：清理本机运行时线路并完成回归验证

**文件：**
- 修改（Git 忽略）：`.env`
- 验证：`analysis/llm.py`
- 验证：`config/bilibili_config.py`
- 验证：`tests/analysis/test_llm.py`
- 验证：`tests/test_rag_phase1.py`

- [ ] **步骤 1：更新本机 `.env` 的非密钥配置**

将现有 LLM 配置归一化为以下值；把 `LLM_API_KEY` 清空，凭据只由已存在的系统环境变量 `SUB2API_KEY` 提供。删除所有 `LLM_SECONDARY_*` 行，确保云雾和 OpenCode 地址不再留在运行配置中。

```dotenv
LLM_PROVIDER=sub2api
LLM_ENV_KEY=SUB2API_KEY
LLM_MODEL=gpt-5.6-sol
LLM_BASE_URL=<HTTPS Sub2api URL，或本机加密隧道 URL>
LLM_API_KEY=
LLM_WIRE_API=responses
LLM_REASONING_EFFORT=auto
LLM_TIMEOUT_SECONDS=600
LLM_MAX_RETRIES=4
LLM_RETRY_BASE_DELAY=2
```

不得将 `SUB2API_KEY` 的值写入文件、命令输出、测试 fixture 或 Git。

- [ ] **步骤 2：验证运行时解析结果且不输出密钥**

运行一个只输出非敏感字段的配置检查：

```powershell
uv run python -c "import config; from analysis.llm import resolve_llm_settings; s=resolve_llm_settings(config); print({k:s[k] for k in ('provider','base_url','model','wire_api','reasoning_effort')}); print('api_key_set=', bool(s['api_key']))"
```

预期：provider 为 `sub2api`，model 为 `gpt-5.6-sol`，wire API 为 `responses`，reasoning effort 为 `auto`，`api_key_set=True`。

- [ ] **步骤 3：运行相关回归测试**

运行：

```powershell
uv run pytest tests/analysis/test_llm.py tests/test_rag_phase1.py tests/test_hongguo_analysis_agent.py tests/analysis/test_bili_video_analyzer.py -q
```

预期：全部通过；这些测试覆盖全局 provider、RAG 继承、分析 agent 和 B 站分析器的共用调用链。

- [ ] **步骤 4：运行文件头与差异检查**

运行：

```powershell
uv run pre-commit run --all-files
git diff --check
git status --short
```

预期：pre-commit 和 diff check 退出码为 0；状态中仅保留用户原有改动以及本计划明确产生的文件，不出现 `.env` 或密钥文件。

- [ ] **步骤 5：检查提交边界**

运行：

```powershell
git log -3 --oneline
git show --stat --oneline HEAD~1..HEAD
```

预期：两个实现提交仅包含 `analysis/llm.py`、`config/bilibili_config.py` 和 `tests/analysis/test_llm.py`；本机 `.env` 不在提交中，用户的其他工作区改动未被暂存或提交。
