# 分集大纲单字段 AI 重写实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在分集大纲的六个文本字段上提供同步、可输入提示词、先预览再采用的单字段 AI 重写，并保持现有批次版本、权限和锁定语义。

**架构：** 新增纯生成服务，直接复用现有 LLM Provider，一次请求只返回一个候选字段；新增窄 API 分离“生成候选”和“采用候选”。草稿采用时通过字段指纹做原子更新，锁定版采用时沿用现有 outline batch revision 语义复制整批为新草稿，并且只替换目标字段。

**技术栈：** FastAPI、Pydantic、SQLite Repository、现有 OpenAI-compatible LLM Provider、Alpine.js、Bootstrap Modal、pytest、Playwright。

---

## 实施前文件边界

### 创建

- `creative/outline_fields.py`：六个字段的唯一白名单、标签和来源指纹算法，供 service、API、repository 共用。
- `templates/common/workspace/outline_field_rewrite_modal.html`：单一字段重写弹窗，供真实页面和浏览器测试复用。

### 修改

- `creative/prompt_templates.py`：增加单字段重写提示词模板和独立模板版本。
- `creative/script_service.py`：增加一次调用、一次字段输出的纯生成函数；不写数据库，不做修复模型调用。
- `creative/repository.py`：读取合法重写上下文；在事务内按指纹更新草稿字段。
- `creative/schema.py`：定义生成与采用请求模型。
- `creative/api.py`：注入 LLM Provider factory；增加生成候选和采用候选接口；锁定版走现有 batch revision。
- `WebGUI.py`：向 creative router 注入现有 LLM Provider factory。
- `static/js/common/tasks.js`：增加字段重写弹窗状态。
- `static/js/common/creative.js`：让 `outline_draft` 项目优先展示最新兼容草稿，保证锁定版派生后刷新仍进入新草稿。
- `static/js/writing/outlines.js`：字段重写弹窗、同步请求、迟到响应隔离、采用和版本切换。
- `templates/common/workspace/project_outlines.html`：六个字段旁增加 AI 重写入口。
- `templates/writing/outlines.html`：增加唯一的重写预览弹窗。
- `static/css/workspaces.css`：字段入口和预览区域样式。
- `tests/test_creative_phase4_generation.py`：纯生成与 Prompt 契约测试。
- `tests/test_creative_phase4.py`：repository 和 API 的权限、版本、冲突及字段隔离测试。
- `tests/test_webgui_ui_changes.py`：模板和前端函数静态契约测试。
- `tests/test_outline_episode_cards_browser.py`：真实 Alpine/浏览器交互测试。

## 共享脏工作区保护规则

上述多数文件已有暂存或未暂存修改。每个执行者必须：

1. 开始任务前记录目标文件的 `git status --short`、`git diff` 和 `git diff --cached`；
2. 只用 `apply_patch` 做增量修改，不格式化整文件；
3. 不运行 `reset`、`checkout`、`restore` 或 `stash`；
4. 不用 `git add <整个脏文件>` 覆盖用户现有暂存边界；
5. 只有控制器确认本任务增量已经隔离时才执行计划中的 commit，否则保留修改并在任务报告中列出待提交文件。

### 任务 1：单字段契约、Prompt 与纯 LLM 服务

**文件：**

- 创建：`creative/outline_fields.py`
- 修改：`creative/prompt_templates.py`
- 修改：`creative/script_service.py`
- 测试：`tests/test_creative_phase4_generation.py`

- [ ] **步骤 1：编写失败的白名单、Prompt 和单次调用测试**

在 `tests/test_creative_phase4_generation.py` 增加：

```python
def test_rewrite_outline_field_calls_llm_once_and_returns_only_target_value():
    provider = SequentialProvider([{"value": "主角在旧剧院找到被烧毁账本的末页。"}])
    value, meta = asyncio.run(script_service.rewrite_outline_field(
        provider,
        project={"id": 8, "name": "旧剧院", "target_episode_count": 3},
        brief={"content": {"title": "旧剧院迷局"}},
        characters=[{"content": {"name": "林舟"}}],
        relationships=[],
        episode={"episode": 2, "core_event": "找到线索", "conflict": "是否赴约"},
        previous_episode={"episode": 1, "cliffhanger": "收到匿名票根"},
        next_episode={"episode": 3, "core_event": "旧剧院交锋"},
        field="core_event",
        instruction="强化危险感",
    ))

    assert value == "主角在旧剧院找到被烧毁账本的末页。"
    assert len(provider.calls) == 1
    payload = json.loads(provider.calls[0][1]["content"])
    assert payload["target_field"] == "core_event"
    assert payload["instruction"] == "强化危险感"
    assert payload["current_episode"]["conflict"] == "是否赴约"
    assert payload["previous_episode"]["episode"] == 1
    assert payload["next_episode"]["episode"] == 3
    assert meta["prompt_template_version"] == "outline-field-rewrite-v1"
```

同时增加参数化测试，覆盖六个合法字段、非法字段、空 `value`、额外大纲字段，以及 provider 抛错时不做第二次调用。

- [ ] **步骤 2：运行测试并确认失败**

运行：

```powershell
uv run pytest tests/test_creative_phase4_generation.py -k "rewrite_outline_field" -q
```

预期：FAIL，提示 `creative.outline_fields` 或 `rewrite_outline_field` 尚不存在。

- [ ] **步骤 3：建立唯一字段契约**

创建 `creative/outline_fields.py`，保留项目要求的 Python 文件头，并实现：

```python
OUTLINE_TEXT_FIELDS = (
    "core_event",
    "conflict",
    "character_progress",
    "highlight",
    "reversal",
    "cliffhanger",
)

OUTLINE_FIELD_LABELS = {
    "core_event": "核心事件",
    "conflict": "冲突",
    "character_progress": "人物推进",
    "highlight": "亮点",
    "reversal": "反转",
    "cliffhanger": "结尾悬念",
}
```

再实现 `outline_field_source_fingerprint(episode, field)`：校验字段白名单，将 `project_id`、`batch_id`、`episode id`、`episode_number`、`field` 和当前字段值按 `sort_keys=True` 序列化后计算 SHA-256。不得把候选文本纳入来源指纹。

- [ ] **步骤 4：实现独立 Prompt 和纯生成函数**

在 `creative/prompt_templates.py` 增加：

```python
OUTLINE_FIELD_REWRITE_PROMPT_VERSION = "outline-field-rewrite-v1"

def outline_field_rewrite_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _messages(
        "你是短剧分集大纲编辑。只重写 target_field 指定的一个字段。"
        "默认保留既定事件、人物动机、时间线和前后集衔接；只有 instruction 明确要求时才改变剧情事实。"
        "同集其他字段、相邻集、锁定 Brief 和人物关系只作为约束，不得输出或改写。"
        "只返回 {\"value\": \"目标字段新稿\"}，不得返回整集、其他字段或 Markdown。",
        payload,
    )
```

在 `creative/script_service.py` 增加 `_validate_outline_field_rewrite()` 和：

```python
async def rewrite_outline_field(
    provider: Any,
    project: dict[str, Any],
    brief: dict[str, Any],
    characters: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    episode: dict[str, Any],
    previous_episode: dict[str, Any] | None,
    next_episode: dict[str, Any] | None,
    field: str,
    instruction: str = "",
) -> tuple[str, dict[str, Any]]:
    if field not in OUTLINE_TEXT_FIELDS:
        raise ValueError("不支持的大纲字段")
    messages = outline_field_rewrite_messages({
        "project": _project_context(project),
        "locked_brief": brief,
        "characters": characters,
        "relationships": relationships,
        "current_episode": episode,
        "previous_episode": previous_episode,
        "next_episode": next_episode,
        "target_field": field,
        "instruction": str(instruction or "").strip(),
    })
    payload = await provider.complete_json(messages)
    value = _validate_outline_field_rewrite(payload)
    return value, {
        "provider": str(payload.get("provider") or getattr(provider, "provider_name", "")),
        "prompt_template_version": OUTLINE_FIELD_REWRITE_PROMPT_VERSION,
    }
```

验证器只允许 `value` 和 provider 自动附加的 `provider` 元数据；`value` 必须是非空字符串。此路径不得调用 `complete_with_one_repair()`、`_repair_outline_fields()` 或 fallback 文案。

- [ ] **步骤 5：运行聚焦测试**

运行：

```powershell
uv run pytest tests/test_creative_phase4_generation.py -k "rewrite_outline_field" -q
```

预期：新增测试全部 PASS，且每例 `provider.calls` 都为 1。

- [ ] **步骤 6：提交任务 1 增量**

在控制器确认没有混入既有修改后提交：

```powershell
git commit --only -m "feat: add outline field rewrite prompt service" -- creative/outline_fields.py creative/prompt_templates.py creative/script_service.py tests/test_creative_phase4_generation.py
```

### 任务 2：重写上下文与草稿原子保存

**文件：**

- 修改：`creative/repository.py`
- 测试：`tests/test_creative_phase4.py`

- [ ] **步骤 1：编写失败的 repository 测试**

先增加完整的测试种子助手，避免依赖省略字段的旧 fixture：

```python
def seed_complete_outline_project(tmp_path: Path, *, locked: bool):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "字段重写", 2)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "旧剧院迷局"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "林舟"}], [{"relation": "盟友"}]
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    overview = outline_series_overview()
    episodes = [
        {
            "episode": number,
            "core_event": f"事件 {number}",
            "conflict": f"冲突 {number}",
            "character_progress": f"人物推进 {number}",
            "highlight": f"亮点 {number}",
            "reversal": f"反转 {number}",
            "cliffhanger": f"悬念 {number}",
            "series_overview": overview,
        }
        for number in (1, 2)
    ]
    batch = repository.create_outline_batch(project["id"], episodes, "调查、受阻、反击")
    if locked:
        repository.lock_outline_batch(project["id"], batch["batch_id"])
        batch = repository.get_outline_batch(project["id"], batch["batch_id"])
    return repository, repository.get_project(project["id"]), batch
```

再增加以下测试：

```python
def test_repository_updates_only_one_outline_field_with_matching_fingerprint(tmp_path):
    repository, project, batch = seed_complete_outline_project(tmp_path, locked=False)
    episode = batch["episodes"][0]
    before = dict(episode["content"])
    fingerprint = outline_field_source_fingerprint(episode, "core_event")

    saved = repository.apply_outline_field_rewrite(
        episode["id"], "core_event", "重写后的核心事件", fingerprint
    )

    assert saved["content"]["core_event"] == "重写后的核心事件"
    assert saved["content"]["conflict"] == before["conflict"]
    assert saved["content"]["series_overview"] == before["series_overview"]
```

再增加：旧指纹返回 `ValueError("该字段已被更新，请重新生成")`、锁定 episode 不允许原地更新、非 outline episode 被拒绝、非当前兼容草稿被拒绝、相邻集上下文只取同一 batch。

- [ ] **步骤 2：运行测试并确认失败**

运行：

```powershell
uv run pytest tests/test_creative_phase4.py -k "outline_field_rewrite and repository" -q
```

预期：FAIL，提示 repository 方法不存在。

- [ ] **步骤 3：实现合法上下文读取**

在 `CreativeRepository` 增加 `get_outline_field_rewrite_context(episode_id)`：

- 目标必须存在且 `stage == "outline"`；
- upstream refs 必须仍匹配项目的锁定 Brief 和人物关系批次；
- draft 必须属于当前上下文的最新 draft batch；
- locked/approved 必须是项目当前 `outline_batch_id`，且不存在更高版本的兼容草稿；
- previous/next 只从同一 batch 按集号读取；
- 返回 project、锁定 Brief、当前人物与关系、目标 episode、前后 episode；
- 给 service 的 episode 值使用 `content`，但 API 响应指纹使用完整持久化 episode 元数据。

- [ ] **步骤 4：实现事务内字段 CAS 更新**

增加：

```python
def apply_outline_field_rewrite(
    self,
    episode_id: int,
    field: str,
    candidate_value: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    ...
```

方法使用 `BEGIN IMMEDIATE`，在同一事务内重新读取 episode、验证 draft/current context、重新计算指纹，再执行：

```python
content = json.loads(row["content_json"] or "{}")
content[field] = candidate_value.strip()
conn.execute(
    "UPDATE creative_episodes SET content_json = ?, updated_at = ? WHERE id = ?",
    (json.dumps(content, ensure_ascii=False), time.time(), episode_id),
)
```

不得用前端传入的整集 `content`，从而不会顺带保存其他字段。锁定资源在此方法内直接返回冲突，不做复制。

- [ ] **步骤 5：运行 repository 测试**

运行：

```powershell
uv run pytest tests/test_creative_phase4.py -k "outline_field_rewrite and repository" -q
```

预期：PASS。

- [ ] **步骤 6：提交任务 2 增量**

```powershell
git commit --only -m "feat: persist outline field rewrites atomically" -- creative/repository.py tests/test_creative_phase4.py
```

### 任务 3：同步 API、权限和锁定版派生

**文件：**

- 修改：`creative/schema.py`
- 修改：`creative/api.py`
- 修改：`WebGUI.py`
- 测试：`tests/test_creative_phase4.py`

- [ ] **步骤 1：编写失败的同步 API 测试**

让 `build_phase4_client()` 接受 `llm_provider_factory`，然后增加：

```python
def test_outline_field_rewrite_api_generates_candidate_without_writing(tmp_path):
    provider = SequentialProvider([{"value": "候选核心事件"}])
    client, repository, _ = build_phase4_client(
        tmp_path, llm_provider_factory=lambda: provider
    )
    project, batch, _ = seed_draft_outline_for_api(client, repository)
    episode = batch["episodes"][0]

    response = client.post(
        f"/api/creative/episodes/{episode['id']}/fields/core_event/rewrite",
        json={"instruction": "强化危机"},
    )

    assert response.status_code == 200
    assert response.json()["candidate_value"] == "候选核心事件"
    assert repository.get_episode(episode["id"])["content"]["core_event"] == "发现假账"
```

增加采用测试：

- draft 采用后只改目标字段并返回 `created_new_version == False`；
- locked 采用后返回新 batch、`created_new_version == True`，原 batch 全部不变；
- 新 batch 集数完整、全集概览和非目标字段继承；
- 非法字段 `422/400`；
- 无访问权限 `403`；
- 指纹过期 `409`；
- provider 未注入 `503`；
- provider 异常或非法响应 `502` 且不写数据库；
- 锁定来源之后出现新草稿时返回 `409`，不产生隐式分支。

- [ ] **步骤 2：运行 API 测试并确认失败**

运行：

```powershell
uv run pytest tests/test_creative_phase4.py -k "outline_field_rewrite and api" -q
```

预期：FAIL，两个路由返回 404。

- [ ] **步骤 3：增加请求模型和 Provider factory 注入**

在 `creative/schema.py` 增加：

```python
class OutlineFieldRewriteRequest(BaseModel):
    instruction: str = Field("", max_length=2000)


class OutlineFieldRewriteApplyRequest(BaseModel):
    candidate_value: str = Field(min_length=1, max_length=6000)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    expected_project_revision: int = Field(ge=0)
    operation_id: str | None = Field(None, max_length=200)
```

为 `build_creative_router()` 和 `register_creative_routes()` 增加可选参数：

```python
llm_provider_factory: Callable[[], Any] | None = None
```

`WebGUI.py` 注册时传入：

```python
llm_provider_factory=lambda: create_llm_provider_from_config(config),
```

测试 router 可直接注入 `lambda: SequentialProvider(...)`，不得读取真实密钥或外部网络。

- [ ] **步骤 4：实现生成候选路由**

新增 `POST /episodes/{episode_id}/fields/{field}/rewrite`。顺序必须是：

1. 获取并校验 repository context；
2. 调用 `application_service.assert_stage_write_access(project_id, "outline", principal)`；
3. draft 额外调用 `workflow_adapter.assert_editable("outline", resource_id)`；
4. 构造新 provider 并调用 `rewrite_outline_field()`；
5. 返回 original、candidate、source fingerprint 和模板元数据；
6. 不调用任何 repository 写方法。

响应包含：

```python
{
    "episode_id": episode["id"],
    "project_id": episode["project_id"],
    "batch_id": episode["batch_id"],
    "field": field,
    "original_value": episode["content"].get(field, ""),
    "candidate_value": candidate,
    "source_fingerprint": outline_field_source_fingerprint(episode, field),
    "source_status": episode["status"],
    "meta": meta,
}
```

- [ ] **步骤 5：实现采用路由与锁定版新草稿**

新增 `POST /episodes/{episode_id}/fields/{field}/apply-rewrite`：

- 对 draft 调用 `repository.apply_outline_field_rewrite()`；
- 对 locked/approved，先验证来源指纹和 `expected_project_revision`，再通过 `application_task_id(None, "outline", project_id, principal)` 获得/确保大纲任务；
- 将来源 batch 按集号复制为 `episodes=[dict(item["content"]), ...]`，只替换目标集的目标字段；
- 调用现有 `application_service.create_revision(stage="outline", replaces_resource_id=f"{project_id}:{batch_id}", ...)`；
- `payload` 必须保留原 `series_arc`、每集 `series_overview` 和完整集数；
- 使用唯一 `operation_id`，并由 `expected_project_revision` 阻止锁定权威变化；
- 返回目标 episode、batch_id、version、`created_new_version`。

将 `ValueError`/`CreativeContextConflict` 中的来源变化映射为 `409`；Provider 配置缺失映射为 `503`；模型调用失败映射为不泄漏请求内容的 `502`。

- [ ] **步骤 6：运行 API 与相关权限测试**

运行：

```powershell
uv run pytest tests/test_creative_phase4.py -k "outline_field_rewrite" -q
uv run pytest tests/test_project_member_stage_editing.py tests/test_project_creator_governance.py -q
```

预期：全部 PASS。

- [ ] **步骤 7：提交任务 3 增量**

```powershell
git commit --only -m "feat: add synchronous outline field rewrite API" -- creative/schema.py creative/api.py WebGUI.py tests/test_creative_phase4.py
```

### 任务 4：字段入口、预览弹窗和采用交互

**文件：**

- 修改：`static/js/common/tasks.js`
- 修改：`static/js/common/creative.js`
- 修改：`static/js/writing/outlines.js`
- 修改：`templates/common/workspace/project_outlines.html`
- 修改：`templates/writing/outlines.html`
- 创建：`templates/common/workspace/outline_field_rewrite_modal.html`
- 修改：`static/css/workspaces.css`
- 测试：`tests/test_webgui_ui_changes.py`
- 测试：`tests/test_outline_episode_cards_browser.py`

- [ ] **步骤 1：编写失败的模板和浏览器测试**

在 `tests/test_webgui_ui_changes.py` 增加：

```python
def test_outline_workspace_exposes_single_field_ai_rewrite_controls_and_modal():
    workspace = _source("templates/common/workspace/project_outlines.html")
    page = _source("templates/writing/outlines.html")
    script = _source("static/js/writing/outlines.js")
    for field in (
        "core_event", "conflict", "character_progress",
        "highlight", "reversal", "cliffhanger",
    ):
        assert f"'{field}'" in workspace
    assert 'id="outlineFieldRewriteModal"' in page
    assert "requestOutlineFieldRewrite" in script
    assert "applyOutlineFieldRewrite" in script
```

在 `tests/test_outline_episode_cards_browser.py` 中让 fixture 同时读取 `project_outlines.html` 和新增的 `outline_field_rewrite_modal.html`，再扩展 `aiworkerApi.request`，为 rewrite 和 apply 返回确定结果：

```python
template = (ROOT / "templates/common/workspace/project_outlines.html").read_text(encoding="utf-8")
rewrite_modal = (
    ROOT / "templates/common/workspace/outline_field_rewrite_modal.html"
).read_text(encoding="utf-8")
page.set_content(
    f'<main class="main-content" x-data="appData()">{template}{rewrite_modal}</main>'
)
```

然后增加：

```python
def test_outline_field_ai_rewrite_previews_and_applies_only_selected_field(outline_cards_page):
    page = outline_cards_page
    page.locator('[data-episode-number="1"] .outline-episode-toggle').click()
    before_conflict = page.locator('#outline-conflict-101').input_value()
    page.locator('[data-episode-number="1"] [data-outline-ai-field="core_event"]').click()
    page.locator('#outlineFieldRewriteInstruction').fill('强化危险感')
    page.get_by_role('button', name='生成新稿').click()
    page.get_by_role('button', name='采用新稿').click()

    assert page.locator('#outline-core-event-101').input_value() == 'AI 重写后的核心事件'
    assert page.locator('#outline-conflict-101').input_value() == before_conflict
```

再测试：重新生成仍只请求同一字段、关闭弹窗不采用、切换 batch 后迟到响应被忽略、生成与采用期间按钮禁用、409 错误保留候选和提示词。

- [ ] **步骤 2：运行 UI 测试并确认失败**

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py -k "outline and field and rewrite" -q
uv run pytest tests/test_outline_episode_cards_browser.py -k "field_ai_rewrite" -q
```

预期：FAIL，入口、弹窗和方法尚不存在。

- [ ] **步骤 3：增加稳定的弹窗状态**

在 `creativeWorkspace` 中增加：

```javascript
outlineFieldRewrite: {
    projectId: 0,
    batchId: '',
    episodeId: 0,
    episodeNumber: 0,
    fieldKey: '',
    fieldLabel: '',
    sourceStatus: '',
    sourceText: '',
    sourceFingerprint: '',
    instruction: '',
    candidateText: '',
    loading: false,
    applying: false,
    error: '',
    requestSequence: 0,
},
```

上下文比较必须使用 projectId、batchId、episodeId、fieldKey 和 requestSequence，不能只保留 `item` 对象引用。

- [ ] **步骤 4：实现入口和唯一弹窗**

六个 `.outline-field-group` 增加 `.outline-field-header`，在 label 旁放置按钮：

```html
<button type="button"
        class="btn btn-link btn-sm outline-field-ai-action"
        data-outline-ai-field="core_event"
        x-show="outlineFieldRewriteAllowed(item)"
        @click="openOutlineFieldRewrite(item, 'core_event', '核心事件')"
        aria-label="AI 重写核心事件">
    <i class="bi bi-stars" aria-hidden="true"></i> AI 重写
</button>
```

其他五个字段只替换 key 和 label。弹窗创建在 `templates/common/workspace/outline_field_rewrite_modal.html`，并由 `templates/writing/outlines.html` 在 `x-for` 外 include。弹窗包含只读原文、可选提示词、生成按钮、可编辑候选预览、重新生成、采用新稿、取消和 `role="alert"` 错误区。

- [ ] **步骤 5：实现同步生成、迟到响应隔离和采用**

在 `static/js/writing/outlines.js` 增加：

- `outlineFieldRewriteAllowed(item)`；
- `openOutlineFieldRewrite(item, fieldKey, fieldLabel)`；
- `outlineFieldRewriteRequestIsCurrent(snapshot)`；
- `requestOutlineFieldRewrite()`；
- `applyOutlineFieldRewrite()`；
- `resetOutlineFieldRewrite()`。

生成请求：

```javascript
const response = await aiworkerApi.request(
    `/api/creative/episodes/${state.episodeId}/fields/${encodeURIComponent(state.fieldKey)}/rewrite`,
    {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({instruction: String(state.instruction || '').trim()}),
    }
);
```

采用请求只发送 candidate、fingerprint、revision；draft 成功后仅合并响应 episode 的目标字段，不刷新整个项目，以免清掉其他尚未保存的人工编辑。locked 成功后重新读取项目，并切换到返回的新草稿 batch。

在 `latestOutlineBatchId()` 中，当 `project.status === 'outline_draft'` 时优先选择兼容 draft 的最高版本；否则优先当前 `project.outline_batch_id`。这保证锁定版派生新草稿后，即使刷新页面也显示新草稿，而不会回到旧锁定版。

- [ ] **步骤 6：增加局部样式并运行 UI 测试**

在现有 `.outline-field-group` 样式旁增加 `.outline-field-header`、`.outline-field-ai-action`、`.outline-field-rewrite-source`、`.outline-field-rewrite-candidate`，保持移动端按钮可换行且 textarea 不溢出。

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py -k "outline" -q
uv run pytest tests/test_outline_episode_cards_browser.py -q
```

预期：全部 PASS。

- [ ] **步骤 7：提交任务 4 增量**

```powershell
git commit --only -m "feat: add outline field rewrite preview UI" -- static/js/common/tasks.js static/js/common/creative.js static/js/writing/outlines.js templates/common/workspace/project_outlines.html templates/common/workspace/outline_field_rewrite_modal.html templates/writing/outlines.html static/css/workspaces.css tests/test_webgui_ui_changes.py tests/test_outline_episode_cards_browser.py
```

### 任务 5：端到端回归、脏工作区审计与运行服务更新

**文件：**

- 验证：上述所有修改文件
- 不新增数据库迁移

- [ ] **步骤 1：运行后端聚焦测试**

```powershell
uv run pytest tests/test_creative_phase4_generation.py tests/test_creative_phase4.py -q
```

预期：全部 PASS。

- [ ] **步骤 2：运行前端聚焦测试**

```powershell
uv run pytest tests/test_webgui_ui_changes.py tests/test_outline_episode_cards_browser.py -q
```

预期：全部 PASS；若 Playwright driver 缺失，只执行 `uv run playwright install chromium`，不得改用 pip。

- [ ] **步骤 3：运行权限和完整回归**

```powershell
uv run pytest tests/test_project_creator_governance.py tests/test_project_member_stage_editing.py tests/test_page_authorization.py -q
uv run pytest tests/ -q
```

预期：全部 PASS。若全量测试包含已知外部服务依赖，报告聚焦测试结果和与本功能无关的既有失败，不得修改本功能范围外代码来掩盖失败。

- [ ] **步骤 4：审计只改变目标字段和版本语义**

用测试项目快照执行一次 draft 和一次 locked 路径，确认：

- 生成接口调用后数据库零变化；
- draft 采用后仅一个 `content[field]` 变化；
- locked 采用后旧 batch 零变化，新 batch 集数完整且只有目标字段不同；
- `series_overview`、`series_arc`、其他集和其他字段均保持；
- 未创建 Job、未新增数据库表。

- [ ] **步骤 5：审计共享工作区**

```powershell
git status --short
git diff --check
git diff --cached --check
```

逐文件区分本功能增量与进入任务前已有修改。不得覆盖、回退或误提交既有 42 个暂存路径。

- [ ] **步骤 6：完成代码审查后更新 8899 服务**

仅在所有聚焦测试通过、当前生成任务已进入终态且用户此前允许更新运行服务的前提下，按项目既有启动方式重启 8899。验证：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8899/writing/projects/11/outlines
```

预期：HTTP 200。随后在页面手动验证六个字段入口、提示词、预览、重新生成、草稿采用和锁定版派生。

- [ ] **步骤 7：提交最终集成增量（若前四个任务未分别提交）**

```powershell
git commit --only -m "feat: support AI rewrite for individual outline fields" -- creative/outline_fields.py creative/prompt_templates.py creative/script_service.py creative/repository.py creative/schema.py creative/api.py WebGUI.py static/js/common/tasks.js static/js/common/creative.js static/js/writing/outlines.js templates/common/workspace/project_outlines.html templates/common/workspace/outline_field_rewrite_modal.html templates/writing/outlines.html static/css/workspaces.css tests/test_creative_phase4_generation.py tests/test_creative_phase4.py tests/test_webgui_ui_changes.py tests/test_outline_episode_cards_browser.py
```

提交前必须由控制器确认暂存区只含本功能增量；否则不提交并在交付报告中明确说明。
