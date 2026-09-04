# 下载时自动采纳实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 点击“下载剧集”时自动创建或采纳对应选题，并直接创建下载 Job，不再要求或领取 `source_package` 工作流任务。

**架构：** `CreativeRepository` 提供按规范化剧名事务化解析下载选题的单一入口；两个下载 Job API 在完成权限和请求校验后调用它，再以无工作流任务的方式创建 Job。前端搜索页只提交下载来源信息，不再读取、领取或开始素材包任务。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、原生 JavaScript/Alpine.js、Pytest、Playwright。

---

## 文件结构

- 修改：`creative/repository.py`：定义下载选题冲突和事务化的创建、采纳、复用逻辑。
- 修改：`WebGUI.py`：放宽下载请求的 `topic_id`，解析下载选题，记录审计，并创建无工作流关联的 Job。
- 修改：`static/js/selection/dramas.js`：移除素材包任务解析，直接提交下载请求。
- 修改：`static/js/common/creative.js`：删除失去调用方的旧素材包任务 Resolver。
- 修改：`static/js/common/tasks.js`：保存检索与下载共用区域的当前加载提示。
- 修改：`templates/common/workspace/selection_dramas.html`：按当前动作显示准确加载提示。
- 修改：`tests/test_selection_stage_snapshots.py`：覆盖选题状态、并发和无任务关联的后端行为。
- 修改：`tests/test_webgui_ui_changes.py`：覆盖下载按钮不再 claim/start 或发送工作流任务。

### 任务 1：事务化解析下载选题

**文件：**
- 修改：`creative/repository.py`
- 测试：`tests/test_selection_stage_snapshots.py`

- [ ] **步骤 1：编写不存在、待评估、已采纳和人工冲突的失败测试**

在 `tests/test_selection_stage_snapshots.py` 增加以下行为测试。会让测试失败的生产缺陷是：仓库目前没有下载意图入口，无法从搜索结果创建或自动采纳选题。

```python
def test_download_intent_creates_or_reuses_one_adopted_topic(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")

    created, created_before = repository.ensure_download_topic(
        title="下载短剧",
        platform="hg",
        provider_id="hongguo_capture",
        source_ref="series-1",
        actor_user_id="selector-1",
    )
    reused, reused_before = repository.ensure_download_topic(
        title=" 下载短剧 ",
        platform="hg",
        provider_id="hongguo_capture",
        source_ref="series-1",
        actor_user_id="selector-1",
    )

    assert created["status"] == "adopted"
    assert reused["id"] == created["id"]
    assert created_before is None
    assert reused_before["status"] == "adopted"
    assert len(repository.list_topics(limit=None)) == 1


@pytest.mark.parametrize("status", ["rejected", "archived"])
def test_download_intent_preserves_manual_negative_decision(
    tmp_path: Path, status: str
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = _topic(repository, "人工决策短剧")
    repository.decide_topic(topic["id"], status, "人工决定", "selector-1")

    with pytest.raises(
        DownloadTopicDecisionConflict,
        match="该剧目已被拒绝或归档，请先在选题库恢复后再下载",
    ):
        repository.ensure_download_topic(
            title=topic["title"],
            platform="hg",
            provider_id="provider-a",
            source_ref="series-2",
            actor_user_id="selector-1",
        )
```

另加一个 `pending_review` 用例，断言状态变为 `adopted`，最新 `topic_decision` 快照的 `actor_user_id` 和 `outcome` 正确。

- [ ] **步骤 2：运行测试并验证按预期失败**

运行：

```powershell
uv run pytest tests/test_selection_stage_snapshots.py -k "download_intent" -q
```

预期：测试收集或执行失败，指出 `DownloadTopicDecisionConflict` / `ensure_download_topic` 尚不存在，而不是 Fixture 或数据库初始化错误。

- [ ] **步骤 3：实现最少的事务化仓库方法**

在 `creative/repository.py` 的 `DuplicateTopicError` 附近定义：

```python
class DownloadTopicDecisionConflict(ValueError):
    """Raised when download intent conflicts with a manual topic decision."""
```

在 `CreativeRepository` 增加：

```python
def ensure_download_topic(
    self,
    *,
    title: str,
    platform: str,
    provider_id: str,
    source_ref: str,
    actor_user_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
```

实现要求：

1. 标题必须非空，平台规范为 `hg` 或 `dy`，Provider 和来源引用只保存稳定标识。
2. 使用 `BEGIN IMMEDIATE`，遍历未删除选题并用 `_title_key()` 精确查重。
3. `rejected` / `archived` 抛出 `DownloadTopicDecisionConflict`。
4. `pending_review` 更新为 `adopted` 并写入 `topic_decision` 快照，`decision_note` 固定为“下载时自动采纳”。
5. `adopted` 原样复用，不重复写快照。
6. 不存在时插入一条 `adopted` 选题；`source_path` 使用 `download-search:<provider_id>:<source_ref>`，`evidence_json` 只记录 `source_type=download_search`、Provider 和来源引用；随后写入同一事务内的决策快照。
7. 返回 `(最新选题, 变更前选题)`；新建时第二项为 `None`。

- [ ] **步骤 4：运行选题状态测试验证通过**

运行：

```powershell
uv run pytest tests/test_selection_stage_snapshots.py -k "download_intent" -q
```

预期：全部 PASS。

- [ ] **步骤 5：增加并发失败测试并验证红灯**

```python
def test_concurrent_download_intent_creates_one_topic(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")

    def ensure() -> int:
        topic, _ = repository.ensure_download_topic(
            title="并发下载短剧",
            platform="hg",
            provider_id="provider-a",
            source_ref="series-3",
            actor_user_id="selector-1",
        )
        return int(topic["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: ensure(), range(2)))

    assert len(set(ids)) == 1
    assert len(repository.list_topics(limit=None)) == 1
```

临时将事务化查重变异为事务外查重时，该测试必须失败或产生两个选题；恢复实现后重新运行并确认 PASS。

- [ ] **步骤 6：检查本任务差异**

运行：

```powershell
git diff --check -- creative/repository.py tests/test_selection_stage_snapshots.py
uv run pytest tests/test_selection_stage_snapshots.py -q
```

预期：无空白错误，测试文件 0 failures。当前文件若包含既有未提交改动，不执行整文件提交，只保留清晰 diff 供最终统一审查。

### 任务 2：下载 API 自动采纳且不关联工作流任务

**文件：**
- 修改：`WebGUI.py`
- 测试：`tests/test_selection_stage_snapshots.py`
- 测试：`tests/test_job_permissions.py`

- [ ] **步骤 1：编写红果与抖音下载接口的失败测试**

复用现有 WebGUI 测试 Fixture，新增两个请求用例：不传 `topic_id` 和
`workflow_task_id`，分别调用 `/api/hongguo/drama/download/jobs` 与
`/api/hongguo/drama/douyin-fallback/jobs`。断言：

```python
assert response.status_code == 200, response.text
body = response.json()
assert body["topic"]["status"] == "adopted"
assert body["job"]["workflow_task_id"] is None
assert body["job"]["project_id"] is None
assert repository.get_topic(body["topic"]["id"])["title"] == "自动采纳短剧"
assert workflow_repository.list_tasks(principal) == []
```

再增加一个已拒绝选题请求，断言 HTTP 409、稳定中文 `detail`，且 JobManager 没有新增 Job。增加无 `drama.download` 权限请求，断言 HTTP 403 且数据库没有写入选题。

- [ ] **步骤 2：运行接口测试验证失败**

运行：

```powershell
uv run pytest tests/test_selection_stage_snapshots.py tests/test_job_permissions.py -k "auto_adopt or download_without_topic" -q
```

预期：422（`topic_id` 仍必填）或 409（旧任务解析仍生效），证明测试命中遗留约束。

- [ ] **步骤 3：放宽请求模型并增加共享解析 Helper**

把 `HongguoDramaDownloadJobRequest` 和 `DouyinFallbackDramaJobRequest` 的
`topic_id` 调整为：

```python
topic_id: int = Field(default=0, ge=0)
```

在 `_job_task_context()` 附近增加：

```python
def _resolve_download_topic(
    *,
    topic_id: int,
    title: str,
    platform: str,
    provider_id: str,
    source_ref: str,
    actor: Principal,
    request: Request | None,
) -> dict[str, Any]:
```

Helper 行为：有 `topic_id` 时验证选题存在、剧名一致且状态为 `adopted`；无
`topic_id` 时调用 `creative_repository.ensure_download_topic()`。仅在新建或状态变化时
调用 `audit_service.record(action="selection.topic_adopt_from_download", target_type="topic")`。
将 `DownloadTopicDecisionConflict` 映射为 HTTP 409。

- [ ] **步骤 4：调整两个下载 Job 路由**

在权限校验后先完成现有剧名、来源引用、集数或 Aweme ID 校验，确保无效请求不会采纳
选题；随后调用 `_resolve_download_topic()`。创建 Job 时使用解析后的选题 ID：

```python
extra={
    "topic_id": topic["id"],
    # 保留现有下载字段
    "legacy_workflow_task_id": None,
}
```

并固定：

```python
job_state = job_manager.create_job(
    job,
    created_by_user_id=actor.user_id,
    workflow_task_id=None,
    project_id=None,
)
return {"status": "success", "topic": topic, "job": public_job}
```

不再为下载路由调用 `_job_task_context(..., expected_task_type="source_package")`、
`link_task_job()` 或解析客户端传入的旧任务 ID。字段暂时保留为兼容输入，但忽略其值。

- [ ] **步骤 5：运行接口与权限测试验证通过**

运行：

```powershell
uv run pytest tests/test_selection_stage_snapshots.py tests/test_job_permissions.py -k "download or source_package" -q
```

预期：0 failures；无权限请求没有产生选题，红果和抖音任务均无工作流关联。

- [ ] **步骤 6：运行后端相关回归集**

运行：

```powershell
uv run pytest tests/test_hongguo_drama.py tests/test_selection_stage_snapshots.py tests/test_job_permissions.py -q
```

预期：0 failures。检查既有显式 `topic_id` 的选题库全剧下载路径仍可用。

### 任务 3：下载按钮移除领取步骤

**文件：**
- 修改：`static/js/selection/dramas.js`
- 修改：`static/js/common/creative.js`
- 修改：`static/js/common/tasks.js`
- 修改：`templates/common/workspace/selection_dramas.html`
- 测试：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写前端契约失败测试**

更新 `test_drama_material_jobs_send_exact_source_topic_and_task` 为下载自动采纳契约，直接
截取 `startDramaAnalysis()` 方法并断言：

```python
assert "resolveSourcePackageWorkflowTask" not in method
assert "/api/workflow/tasks/" not in method
assert "workflow_task_id" not in method
assert "topic_id: task.topic_id" not in method
assert "正在采纳选题并创建下载任务" in method
assert 'x-text="dramaAnalysis.loadingMessage"' in template
```

同时断言 `static/js/common/creative.js` 不再定义
`resolveSourcePackageWorkflowTask`，防止遗留 Resolver 被重新调用。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py -k "drama_material_jobs" -q
```

预期：FAIL，显示旧 Resolver、`topic_id: task.topic_id` 和
`workflow_task_id: task.id` 仍存在。

- [ ] **步骤 3：实现最少前端变更**

在 `dramaAnalysis` 初始状态增加
`loadingMessage: '正在检索剧目来源…'`。`searchDrama()` 开始时恢复该提示；在
`startDramaAnalysis()` 中删除任务解析，并在请求期间设置：

```javascript
this.dramaAnalysis.loadingMessage = '正在采纳选题并创建下载任务…';
```

红果 Payload 保留 title、Provider、series_ref/book_id、episodes 和
`all_episodes=false`；抖音 Payload 保留 title、aweme_id 和 fallback_reason。两者都不发送
`topic_id` 或 `workflow_task_id`。请求成功后若响应带 `topic`，保存到
`this.dramaAnalysis.topic`，再执行现有 Job 刷新和提示。

删除 `static/js/common/creative.js` 中已无调用方的
`resolveSourcePackageWorkflowTask()`。

把 `templates/common/workspace/selection_dramas.html` 中硬编码的
“正在检索剧目来源…”替换为带 `aria-live="polite"` 的
`x-text="dramaAnalysis.loadingMessage"`，使同一稳定区域在检索与创建下载任务时显示
准确状态，不新增弹窗或改变布局。

- [ ] **步骤 4：运行前端契约测试验证通过**

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py -k "drama_material_jobs or selection_adoption_download" -q
```

预期：0 failures。

- [ ] **步骤 5：运行 JavaScript 与页面回归测试**

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py tests/test_hongguo_provider_browser.py -q
```

预期：0 failures，页面下载来源选择和下载弹窗契约没有回归。

### 任务 4：端到端验证与差异审查

**文件：**
- 验证：`creative/repository.py`
- 验证：`WebGUI.py`
- 验证：`static/js/selection/dramas.js`
- 验证：`static/js/common/creative.js`
- 验证：相关测试文件

- [ ] **步骤 1：运行完整聚焦测试集**

```powershell
uv run pytest tests/test_selection_stage_snapshots.py tests/test_job_permissions.py tests/test_hongguo_drama.py tests/test_webgui_ui_changes.py tests/test_hongguo_provider_browser.py -q
```

预期：0 failures。

- [ ] **步骤 2：运行文件头与格式检查**

```powershell
uv run pre-commit run --files creative/repository.py WebGUI.py static/js/selection/dramas.js static/js/common/creative.js static/js/common/tasks.js templates/common/workspace/selection_dramas.html tests/test_selection_stage_snapshots.py tests/test_job_permissions.py tests/test_webgui_ui_changes.py
```

预期：所有 Hook 通过；格式器若修改文件，重新运行步骤 1。

- [ ] **步骤 3：启动开发服务并做浏览器验证**

若 `8899` 已被现有 WebGUI 占用，在空闲端口启动当前代码：

```powershell
uv run uvicorn WebGUI:app --host 127.0.0.1 --port 8900
```

使用测试账号在桌面 `1440x900` 和移动 `390x844` 视口验证：搜索剧目、选择来源与剧集、
点击“下载剧集”；确认没有领取任务提示，按钮加载态稳定，后端返回冲突时提示恢复选题，
文本不重叠。不要实际重复下载已存在素材；端到端请求可使用测试数据库或 Mock Worker。

- [ ] **步骤 4：审查最终差异**

```powershell
git diff --check
git diff -- creative/repository.py WebGUI.py static/js/selection/dramas.js static/js/common/creative.js static/js/common/tasks.js templates/common/workspace/selection_dramas.html tests/test_selection_stage_snapshots.py tests/test_job_permissions.py tests/test_webgui_ui_changes.py
```

确认差异只覆盖自动采纳、下载 Job 和对应测试。由于工作区原本存在未提交改动，禁止
整文件 `git add` 或提交不属于本计划的变更；由主代理在交付时列出本次实际修改文件和
未提交状态。
