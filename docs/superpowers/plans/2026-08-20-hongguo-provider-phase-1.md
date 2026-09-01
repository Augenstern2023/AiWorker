# 红果独立 Provider 改造一期实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不破坏现有抓包下载的前提下，恢复七个第三方来源的剧目检索、人工选源、剧集读取和所选来源下载，并建立后续编排器可复用的独立 Provider 契约。

**架构：** 新建 `agents/drama_providers`，用统一模型和注册表隔离来源标识；七个第三方来源共享 HTTP 实现但以独立 Provider 实例注册。`HongguoDramaClient` 继续作为兼容门面，按 `source_platform` 路由第三方 Provider 或现有抓包目录；两个 Worker 根据任务来源选择第三方直连下载或现有抓包下载路径。

**技术栈：** Python 3.11、httpx、FastAPI、Pydantic、pytest、Jinja2、Bootstrap 5、Alpine.js、uv。

---

## 文件结构

- 创建 `agents/drama_providers/__init__.py`：导出 Provider 公共类型。
- 创建 `agents/drama_providers/models.py`：候选、剧集、媒体、健康状态、下载结果和结构化错误。
- 创建 `agents/drama_providers/base.py`：Provider Protocol。
- 创建 `agents/drama_providers/third_party.py`：配置中心解析、七源 HTTP 检索、剧集、播放和下载实现。
- 创建 `agents/drama_providers/registry.py`：Provider 注册、查询、并发精确搜索和结果分组。
- 修改 `agents/hongguo_drama.py`：合并旧第三方设置与当前抓包设置，作为兼容门面路由来源。
- 修改 `agents/hongguo_drama_download_worker.py`：增加所选第三方来源批量下载分支，保留抓包分支。
- 修改 `agents/hongguo_drama_worker.py`：增加所选第三方来源单集下载分支，保留抓包和后续 ASR。
- 修改 `WebGUI.py`：扩展设置契约、分组搜索响应和 Provider 标识兼容字段。
- 修改 `templates/admin/config.html`：同时呈现第三方和抓包设置。
- 修改 `static/js/admin/workspace.js`：读写两组设置并分别检查。
- 修改 `static/js/selection/dramas.js`：检索页人工选择 Provider。
- 修改 `static/js/selection/breakdowns.js`：选题下载弹窗人工选择 Provider。
- 修改 `templates/common/workspace/selection_dramas.html`：展示来源分组和当前来源。
- 修改相关测试文件，并创建 `tests/test_drama_providers.py`。

## 任务 1：Provider 模型、配置解析和注册表

**文件：**
- 创建：`agents/drama_providers/__init__.py`
- 创建：`agents/drama_providers/models.py`
- 创建：`agents/drama_providers/base.py`
- 创建：`agents/drama_providers/registry.py`
- 测试：`tests/test_drama_providers.py`

- [ ] **步骤 1：编写失败测试**

覆盖以下真实行为：

```python
def test_registry_keeps_same_title_from_different_providers_separate():
    registry = DramaProviderRegistry([FakeProvider("hg"), FakeProvider("hm")])
    result = registry.search_exact_all("目标短剧")
    assert [item.provider_id for item in result.candidates] == ["hg", "hm"]

def test_registry_reports_one_provider_error_without_hiding_other_results():
    registry = DramaProviderRegistry([FailingProvider("hg"), FakeProvider("hm")])
    result = registry.search_exact_all("目标短剧")
    assert [item.provider_id for item in result.candidates] == ["hm"]
    assert result.providers[0].error_code == "provider_unavailable"
```

会让测试失败的生产缺陷：合并同名来源、一个来源异常导致全局异常、注册表允许重复 ID。

- [ ] **步骤 2：运行红灯测试**

运行：

```powershell
uv run pytest tests/test_drama_providers.py -q
```

预期：导入失败，因为 Provider 包和类型尚不存在。

- [ ] **步骤 3：实现最小模型和注册表**

实现不可变数据类：

```python
@dataclass(frozen=True)
class DramaCandidate:
    provider_id: str
    series_ref: str
    title: str
    cover: str = ""
    summary: str = ""
    episode_count: int | str = ""

@dataclass(frozen=True)
class ProviderFailure(RuntimeError):
    code: str
    safe_message: str
    retryable: bool = False
```

注册表必须拒绝空或重复 `provider_id`，并使用有界线程池并发执行 `search_exact()`，输出稳定按注册顺序排列的候选和来源状态。

- [ ] **步骤 4：运行绿灯测试**

```powershell
uv run pytest tests/test_drama_providers.py -q
```

预期：任务 1 用例全部通过。

- [ ] **步骤 5：提交任务 1**

```powershell
git add agents/drama_providers tests/test_drama_providers.py
git commit -m "feat: 增加剧目 Provider 基础契约"
```

当前脏工作区执行时仅在可证明暂存区不含既有修改的情况下提交；否则保留未提交并在交付中说明。

## 任务 2：恢复七源第三方 Provider

**文件：**
- 创建：`agents/drama_providers/third_party.py`
- 修改：`agents/drama_providers/__init__.py`
- 修改：`agents/drama_providers/registry.py`
- 测试：`tests/test_drama_providers.py`

- [ ] **步骤 1：编写失败测试**

将 `tests/test_hongguo_drama.py` 中被 `legacy_remote_contract` 跳过的配置中心、查询参数、精确搜索、剧集、付费和播放地址行为迁移为 Provider 测试，并新增：

```python
def test_third_party_provider_never_reuses_another_provider_identity():
    provider = ThirdPartyDramaProvider("hm", config, request_json=fake_json)
    episodes = provider.episodes("hm-book-1")
    assert episodes[0].provider_id == "hm"
    assert episodes[0].series_ref == "hm-book-1"

def test_play_requires_book_id_only_when_configured():
    media = provider.resolve_playable("book-1", "video-1")
    assert captured_params == {"video_id": "video-1", "book_id": "book-1"}
```

会让测试失败的生产缺陷：丢失配置 URL 查询参数、跨来源复用标识、所有来源都附带或都不附带 `book_id`、原始响应泄漏。

- [ ] **步骤 2：运行红灯测试**

```powershell
uv run pytest tests/test_drama_providers.py -q
```

预期：缺少 `ThirdPartyDramaProvider`、配置解析和 HTTP 行为。

- [ ] **步骤 3：恢复最小第三方实现**

从 Git 中 `agents/hongguo_drama.py` 的旧实现恢复并拆分：

- `DOWNLOAD_SEARCH_SOURCES` 七源常量。
- `_find_platform_configs()`。
- 保留既有查询参数的 `_request_url()`。
- `_json()` 的 HTTP 与安全错误包装。
- `search()`、`search_exact()`、`episodes()`、`resolve_playable()`。
- 付费标记调用 `paid_content_reason()`。
- 下载器保留 Range、2 GiB、暂停和原子替换。

Provider 公开结果不得包含 `raw` 或 URL。

- [ ] **步骤 4：运行绿灯和抓包回归**

```powershell
uv run pytest tests/test_drama_providers.py tests/test_hongguo_capture.py tests/test_hongguo_video_capture_client.py -q
```

预期：第三方 Provider 与现有抓包测试通过。

- [ ] **步骤 5：提交任务 2**

```powershell
git add agents/drama_providers tests/test_drama_providers.py
git commit -m "feat: 恢复红果七源 Provider"
```

## 任务 3：合并设置并改造兼容门面

**文件：**
- 修改：`agents/hongguo_drama.py`
- 修改：`tests/test_hongguo_drama.py`
- 修改：`tests/test_hongguo_capture.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_settings_preserve_third_party_and_capture_fields(tmp_path):
    saved = save_settings(
        tmp_path,
        config_url="https://config.example.test",
        activation_code="secret",
        capture_root=str((tmp_path / "capture").resolve()),
        capture_service_url="http://127.0.0.1:8776",
        wait_timeout_seconds=600,
    )
    assert saved["activation_code"] == "secret"
    assert public_settings(saved)["has_activation_code"] is True
    assert "activation_code" not in public_settings(saved)
```

再覆盖旧第三方设置、当前抓包设置和新合并设置三种文件格式。

会让测试失败的生产缺陷：读取旧格式后丢失第三方字段、保存第三方字段时覆盖抓包配置、公开激活码。

- [ ] **步骤 2：运行红灯测试**

```powershell
uv run pytest tests/test_hongguo_drama.py tests/test_hongguo_capture.py -q
```

预期：当前 `save_settings()` 不接受第三方字段，兼容门面只检索抓包目录。

- [ ] **步骤 3：实现合并设置和来源路由**

`HongguoDramaClient` 初始化 Capture Provider 和七源 Registry：

- `source_platform=hongguo_capture` 时保持现有目录检索、剧集状态和抓包能力。
- 第三方来源时调用对应 Provider。
- `search_exact_all_sources()` 返回七源候选。
- `search_for_download()` 返回包含 `candidates/providers/capture_candidates` 的搜索结果对象。
- `episodes()` 依据来源严格路由。
- 兼容公开字典继续包含 `book_id`、`source_platform`。

- [ ] **步骤 4：运行绿灯测试**

```powershell
uv run pytest tests/test_drama_providers.py tests/test_hongguo_drama.py tests/test_hongguo_capture.py -q
```

预期：设置、第三方门面和抓包回归通过；既存非红果 UI 基线失败不计入本任务。

- [ ] **步骤 5：提交任务 3**

仅在可安全隔离既有修改时执行：

```powershell
git add agents/hongguo_drama.py tests/test_hongguo_drama.py tests/test_hongguo_capture.py
git commit -m "feat: 合并红果第三方与抓包设置"
```

## 任务 4：API 分组搜索与兼容请求

**文件：**
- 修改：`WebGUI.py`
- 修改：`tests/test_hongguo_drama.py`
- 测试：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写失败测试**

覆盖：

```python
@pytest.mark.asyncio
async def test_download_search_returns_provider_groups_and_flat_candidates():
    response = await search_hongguo_drama_for_download(request)
    assert response["source"] == "hongguo_providers"
    assert response["candidates"][0]["provider_id"] == "hg"
    assert response["providers"][1]["error_code"] == "provider_unavailable"

def test_job_payload_rejects_conflicting_provider_aliases():
    # provider_id=hg 与 source_platform=hm 必须返回 400
```

会让测试失败的生产缺陷：API 丢失来源分组、泄漏 raw、兼容字段冲突时静默选择其一、接口检查把第三方异常和抓包异常混为一体。

- [ ] **步骤 2：运行红灯测试**

```powershell
uv run pytest tests/test_hongguo_drama.py -q
```

- [ ] **步骤 3：实现 API 契约**

- 设置请求增加 `config_url`、`activation_code`。
- `/test` 分别返回 `third_party` 和 `capture` 检查结果；一组失败不抹掉另一组详情。
- `/download/search` 返回七源分组、扁平候选和抓包候选。
- `/episodes` 接受 `provider_id/series_ref` 及旧别名。
- Job 请求接受新旧字段并在冲突时返回 `400`。
- 所有候选删除 `raw`、URL 和鉴权材料。

- [ ] **步骤 4：运行绿灯测试**

```powershell
uv run pytest tests/test_hongguo_drama.py tests/test_webgui_ui_changes.py -q
```

预期：红果/API 定向用例通过；若五个既存非红果 UI 基线仍失败，输出必须与基线一致且不得新增红果失败。

- [ ] **步骤 5：提交任务 4**

```powershell
git add WebGUI.py tests/test_hongguo_drama.py tests/test_webgui_ui_changes.py
git commit -m "feat: 返回红果多来源检索结果"
```

## 任务 5：所选第三方来源 Worker 下载

**文件：**
- 修改：`agents/hongguo_drama_download_worker.py`
- 修改：`agents/hongguo_drama_worker.py`
- 修改：`tests/test_hongguo_drama.py`
- 修改：`tests/test_hongguo_capture.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_download_worker_uses_only_selected_provider(tmp_path):
    job = third_party_job(provider_id="hm", series_ref="hm-book")
    run(job)
    assert attempts == [("hm", "hm-book", "hm-video-1")]

def test_analysis_worker_keeps_capture_path_for_capture_jobs(tmp_path):
    # 现有 8776 start_capture 与成品接管调用仍发生
```

另覆盖批量付费跳过、地址刷新、部分失败后继续写完整 manifest、`source.json` 不含 URL。

会让测试失败的生产缺陷：所有 Job 仍强制调用抓包、第三方任务调用 8776、分析 Worker 不继续 ASR、清单写入播放地址。

- [ ] **步骤 2：运行红灯测试**

```powershell
uv run pytest tests/test_hongguo_drama.py tests/test_hongguo_capture.py -q
```

- [ ] **步骤 3：实现来源分派**

在两个 Worker 入口只做一次来源判断：

```python
if provider_id == "hongguo_capture":
    media_result = capture_download(...)
else:
    media_result = third_party_download(...)
```

将第三方和抓包实现拆成专注的内部函数，公共部分只负责目标目录、清单、事件和后续 ASR。第三方失败不自动切换来源、抓包或抖音。

- [ ] **步骤 4：运行绿灯与 Worker 回归**

```powershell
uv run pytest tests/test_drama_providers.py tests/test_hongguo_drama.py tests/test_hongguo_capture.py tests/test_hongguo_video_capture_client.py -q
```

- [ ] **步骤 5：提交任务 5**

```powershell
git add agents/hongguo_drama_download_worker.py agents/hongguo_drama_worker.py tests/test_hongguo_drama.py tests/test_hongguo_capture.py
git commit -m "feat: 按所选 Provider 下载红果剧集"
```

## 任务 6：设置页和人工选源 UI

**文件：**
- 修改：`templates/admin/config.html`
- 修改：`static/js/admin/workspace.js`
- 修改：`static/js/selection/dramas.js`
- 修改：`static/js/selection/breakdowns.js`
- 修改：`templates/common/workspace/selection_dramas.html`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写失败的浏览器可观察行为测试**

测试通过受控 API 响应初始化真实 Alpine 页面，验证：

- 两个来源分组均显示且不合并。
- 单候选不会自动触发 `/episodes`。
- 点击候选后 `/episodes` 带正确 `provider_id/series_ref`。
- Job payload 带同一 Provider 标识。
- 设置保存同时发送第三方和抓包字段。
- 激活码不从 GET 响应回填原文。

会让测试失败的生产缺陷：自动选中单候选、选中来源后仍使用默认抓包来源、保存设置覆盖另一组字段。

- [ ] **步骤 2：运行红灯测试**

```powershell
uv run pytest tests/test_webgui_ui_changes.py -k "hongguo or drama_provider" -q
```

- [ ] **步骤 3：实现最小 UI**

- 设置页使用“第三方剧目来源”和“抓包下载”两个卡片。
- 搜索结果按 `providers` 分组。
- 候选按钮显示来源徽标。
- 删除单候选自动选择逻辑。
- 当前选择区显示 Provider 名称。
- 抓包候选明确标记为抓包来源。
- 局部来源错误不覆盖其他结果。
- 保留 Bootstrap、Alpine、焦点、禁用态和移动端折行。

- [ ] **步骤 4：运行绿灯测试**

```powershell
uv run pytest tests/test_webgui_ui_changes.py -k "hongguo or drama_provider" -q
```

- [ ] **步骤 5：提交任务 6**

```powershell
git add templates/admin/config.html static/js/admin/workspace.js static/js/selection/dramas.js static/js/selection/breakdowns.js templates/common/workspace/selection_dramas.html tests/test_webgui_ui_changes.py
git commit -m "feat: 增加红果第三方人工选源界面"
```

## 任务 7：全链路验证和一期验收

**文件：**
- 修改：仅修复本期验证暴露的红果范围缺陷
- 核对：`docs/superpowers/specs/2026-08-20-hongguo-provider-phase-1-prd.md`

- [ ] **步骤 1：运行 Provider 和红果全量定向测试**

```powershell
uv run pytest tests/test_drama_providers.py tests/test_hongguo_capture.py tests/test_hongguo_drama.py tests/test_hongguo_video_capture_client.py -q
```

- [ ] **步骤 2：运行 UI 定向测试**

```powershell
uv run pytest tests/test_webgui_ui_changes.py -k "hongguo or drama_provider" -q
```

- [ ] **步骤 3：运行完整测试**

```powershell
uv run pytest tests/ -q
```

对比实施前基线：已知五个失败属于导航、选题生命周期和编剧页面资源引用。本期不得新增红果、Provider、抓包或设置失败。

- [ ] **步骤 4：运行静态检查**

```powershell
uv run pre-commit run --all-files
```

- [ ] **步骤 5：真实授权剧目验收**

使用自有或已授权剧目完成：七源检索、人工选源、剧集读取、单集下载、批量下载、成品验证、单集分析，并回归现有 8776 抓包下载。

- [ ] **步骤 6：提交剩余验证修复**

提交前检查 `git diff` 和暂存区，只包含本期文件；若既有修改无法与本期安全分离，则不创建混合提交，并在交付报告中列明未提交状态。
