# 原创创作工作流实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让策划和编剧都能从一句话灵感创建一等原创项目，生成 3–5 个原创方向，并在选定 Brief 后复用现有人物、大纲、Beats 和场次剧本工作流。

**架构：** 在现有 `creative_projects` 上增加 `imitation|original` 模式和可空来源，使用独立的版本化 `creative_seeds` 保存原创输入；原创项目创建由幂等协调服务连接 SQLite 创作库与系统工作流库。方向生成在现有 Phase 4 Worker 中按项目模式分派到仿写或原创生成器，Brief 之后不分叉。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、SQLAlchemy/PostgreSQL、原生 JavaScript、Alpine.js、Jinja2、Playwright、pytest、uv

---

## 实施边界

本计划只实现已批准规格的首期：一句话灵感、轻量约束、可选市场参考、3–5 个原创方向以及现有后续工作流衔接。不实现“完整构想直接 Brief”和“小说/旧剧本导入”，也不展示这两个入口。

实施应在独立 worktree 中进行。当前工作区存在大量与本功能无关的修改，执行时只提交每个任务列出的文件，不整理、覆盖或提交其他人的工作。

## 文件结构与职责

### 新建文件

- `creative/original_project_service.py`：原创项目跨库幂等创建、失败协调和种子版本操作。
- `creative/market_reference_service.py`：市场参考权限校验、安全摘要和禁止词构建。
- `creative/original_direction_service.py`：原创方向提示输入、结构校验、差异度校验、修复与派生。
- `templates/common/workspace/original_project_modal.html`：策划页和编剧页共享的轻量原创创建表单。
- `tests/test_original_creative_repository.py`：项目模式迁移、种子版本和原创方向持久化测试。
- `tests/test_original_workflow_tasks.py`：无 `topic_id` 的项目任务与后续流转测试。
- `tests/test_market_reference_service.py`：参考权限、白名单和安全摘要测试。
- `tests/test_original_project_api.py`：原创创建、幂等、失败协调、种子 API 和权限测试。
- `tests/test_original_direction_generation.py`：原创提示、候选差异、部分修复、派生和 Worker 测试。
- `tests/test_original_project_browser.py`：策划/编剧入口、轻量表单、原创 Brief 差异化展示和响应式测试。
- `tests/test_original_workflow_integration.py`：原创方向锁定后进入现有后续阶段的集成回归。

### 修改文件

- `creative/repository.py`：项目表迁移、原创项目、种子版本、来源可空查询和原创方向持久化。
- `creative/schema.py`：原创创建、参考、种子更新、保留候选和方向派生请求模型。
- `creative/prompt_templates.py`：新增 `original-direction-v1` 和原创方向派生提示模板。
- `creative/phase4_worker.py`：按项目模式调用原创或仿写生成器并保存部分成功结果。
- `creative/api.py`：注册原创项目/种子 API，准备安全市场上下文，分流方向更新和生成。
- `workflow/repository.py`：允许项目任务的 `topic_id` 为空并保持后续任务来源一致。
- `runtime/job_manager.py`：把种子版本纳入原创方向任务冲突标识，同时保持同项目同种子单任务。
- `static/js/common/tasks.js`：增加原创创建、种子、参考和候选保留状态。
- `static/js/common/creative.js`：原创创建、种子版本、参考选择、补生成、派生和模式化显示。
- `templates/common/workspace/project_list.html`：共享项目创建区与项目表，支持创作模式和来源列。
- `templates/common/workspace/project_blueprints.html`：按模式展示仿写或原创字段。
- `templates/planning/projects.html`：策划页加载共享原创入口并保留选题立项。
- `templates/writing/projects.html`：编剧页加载共享原创入口和共享项目表。
- `static/css/workspaces.css`：原创表单、方向比较和移动端布局。
- `剧本生成流程.md`：记录原创与仿写两条入口汇合后的统一流程。

### 测试辅助代码约定

新测试文件必须各自定义所需 fixture/helper，不从其他测试模块跨文件导入：

- `tests/test_original_creative_repository.py` 定义 `create_legacy_creative_database()`，用 `sqlite3.executescript()` 创建最小 `creative_topics(id)`、`creative_breakdowns(id, topic_id)` 和当前旧版完整 `creative_projects` 列表，插入 ID 为 1 的父记录和项目；定义 `create_pending_original_project()` 调用 `create_original_project()`，定义 `ready_original_project()` 在其后调用 `mark_project_bootstrap_status(id, "ready")`。
- `tests/test_original_workflow_tasks.py` 使用现有系统数据库 fixture；需要后续任务时直接对 `ensure_project_task()` 的返回值调用 `create_next_task()`，不引入虚构的完成助手。
- `tests/test_market_reference_service.py` 定义 `seed_reference_material()`，创建一条题材为“都市”的已采纳选题和一张内容包含 `category/target_audience/emotional_curve/production_difficulty/protagonist_identity/core_conflict/hook_points` 的锁定拆解。
- `tests/test_original_project_api.py` 按 `tests/test_creative_phase4.py::build_phase4_client` 的方式定义 `client` fixture，并在 TestClient 上附加 `repository`、`workflow` 和 `principal`；`original_project` fixture 通过原创创建 API 返回项目；`FailOnce` 第一次调用抛出 `RuntimeError("bootstrap failed")`，第二次转发原函数。
- `tests/test_original_project_browser.py` 定义 `render_workspace_template()` 使用 Jinja2 `FileSystemLoader`；`mount_original_project_form()` 和 `mount_blueprint_workspace()` 加载共享模板、`tasks.js`、`creative.js` 并提供可记录请求的 `window.aiworkerApi` mock。
- `tests/test_original_workflow_integration.py` 的 `original_client` 复用原创 API fixture 结构；Worker helper 只把 `phase4_worker.create_provider` 替换为固定 Provider，然后调用 `asyncio.run(phase4_worker.run(job))`；锁定 helper 通过真实 Brief 任务和锁定 API 完成，不直接更新项目状态。

原创生成测试统一使用以下 Provider 和数据工厂，避免每个用例自行猜测模型契约：

```python
class FakeProvider:
    provider_name = "fake"

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.call_count = 0

    async def complete_json(self, _messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = self.payloads[self.call_count]
        self.call_count += 1
        return payload


def direction(title: str, protagonist: str, background: str, conflict: str) -> dict[str, Any]:
    return {
        "title": title, "logline": f"{protagonist}在{background}解决{conflict}",
        "theme": "人在制度中保持选择", "protagonist": protagonist,
        "protagonist_desire": "获得自主权", "protagonist_flaw": "过度独立",
        "pressure_response": "先独自承担再学习协作", "main_opposition": "僵化制度",
        "core_conflict": conflict, "main_goal": "改变制度", "background": background,
        "worldview": "情绪可以被量化但不能被定义", "story_engine": "每集解决一种情绪考核",
        "first_three_hooks": ["工资因愤怒清零", "同事出售快乐", "AI 拒绝考核"],
        "highlights": ["规则反杀", "身份反差"], "differentiators": ["情绪劳动具象化"],
        "audience": "18-35 岁职场观众", "market_fit": "创作判断：适合强设定短剧",
        "cost_level": "low", "originality_statement": "人物和冲突均由种子独立发展",
        "risks": ["设定解释成本"], "assumptions": ["观众理解情绪积分"],
        "open_questions": ["公司为何采用该制度"],
    }


PROJECT = {"id": 9, "name": "情绪公司", "creation_mode": "original"}
SEED = {"id": 7, "version": 1, "seed_text": "未来公司按情绪发工资", "constraints": {}}
PARENT_DIRECTION = {"id": 11, "content": direction("情绪公司", "林岚", "未来公司", "情绪考核")}
```

`valid_original_payloads()` 返回三个分别使用“未来公司/海上社区/虚拟学校”背景的方向；重复用例把三个方向的主角、背景和冲突设为相同；替换用例返回两个新的不同方向；禁止词用例把 `protagonist` 设为“顾清歌”；派生用例返回一个符合 `direction()` 契约的新方向。

旧项目迁移 fixture 使用以下确定的旧表，不依赖迁移后的仓储初始化：

```sql
CREATE TABLE creative_topics (id INTEGER PRIMARY KEY);
CREATE TABLE creative_breakdowns (
    id INTEGER PRIMARY KEY,
    topic_id INTEGER NOT NULL REFERENCES creative_topics(id)
);
CREATE TABLE creative_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES creative_topics(id),
    breakdown_id INTEGER NOT NULL REFERENCES creative_breakdowns(id),
    name TEXT NOT NULL,
    genre TEXT NOT NULL DEFAULT '',
    target_platform TEXT NOT NULL DEFAULT '',
    target_audience TEXT NOT NULL DEFAULT '',
    target_episode_count INTEGER NOT NULL DEFAULT 20,
    episode_duration_seconds INTEGER NOT NULL DEFAULT 120,
    status TEXT NOT NULL DEFAULT 'created',
    locked_brief_id INTEGER,
    character_batch_id TEXT,
    outline_batch_id TEXT,
    workflow_task_id TEXT,
    creation_workflow_task_id TEXT,
    creation_request_id TEXT,
    created_by TEXT NOT NULL DEFAULT 'local',
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
INSERT INTO creative_topics(id) VALUES (1);
INSERT INTO creative_breakdowns(id, topic_id) VALUES (1, 1);
INSERT INTO creative_projects(
    id, topic_id, breakdown_id, name, target_episode_count,
    episode_duration_seconds, created_at, updated_at
) VALUES (1, 1, 1, '旧仿写项目', 20, 120, 1, 1);
```

原创 API 测试使用以下固定请求和一次失败包装器：

```python
def original_project_payload(creation_request_id: str) -> dict[str, Any]:
    return {
        "seed_text": "未来公司按照员工情绪发工资",
        "name": "情绪公司",
        "genre": "都市奇幻",
        "target_platform": "hg",
        "target_audience": "18-35 岁职场观众",
        "target_episode_count": 20,
        "episode_duration_seconds": 120,
        "cost_level": "low",
        "content_boundaries": ["不美化职场霸凌"],
        "reference_refs": [],
        "creation_request_id": creation_request_id,
    }


class FailOnce:
    def __init__(self, wrapped: Callable[..., Any]) -> None:
        self.wrapped = wrapped
        self.failed = False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self.failed:
            self.failed = True
            raise RuntimeError("bootstrap failed")
        return self.wrapped(*args, **kwargs)
```

## 任务 1：项目模式、原创种子与兼容迁移

**文件：**
- 修改：`creative/repository.py`（`CreativeRepository.__init__`、`_init_schema`、项目创建/列表/详情和蓝图映射）
- 创建：`tests/test_original_creative_repository.py`

- [ ] **步骤 1：编写旧项目迁移和项目来源约束的失败测试**

```python
def test_repository_migrates_legacy_project_to_imitation(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    create_legacy_creative_database(db_path)

    CreativeRepository._migrate_project_table_if_needed(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        project = dict(connection.execute("SELECT * FROM creative_projects WHERE id = 1").fetchone())
        assert project["creation_mode"] == "imitation"
        assert project["bootstrap_status"] == "ready"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_original_project_has_no_topic_or_breakdown(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = repository.create_original_project(
        seed_text="失业女程序员进入只看情绪价值的未来公司",
        name="情绪公司",
        target_episode_count=20,
        created_by="writer-1",
        creation_request_id="request-1",
    )

    assert project["creation_mode"] == "original"
    assert project["topic_id"] is None
    assert project["breakdown_id"] is None
    assert repository.list_projects() == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_original_creative_repository.py -q`

预期：FAIL，包含 `no such column: creation_mode` 或 `AttributeError: create_original_project`。

- [ ] **步骤 3：实现项目表安全重建与一致性约束**

在 `_init_schema()` 前用独立连接完成旧表升级：关闭外键、开启显式事务、创建新表、复制旧行、替换表、提交、开启外键并执行 `PRAGMA foreign_key_check`。新表核心约束：

```sql
creation_mode TEXT NOT NULL DEFAULT 'imitation'
    CHECK (creation_mode IN ('imitation', 'original')),
bootstrap_status TEXT NOT NULL DEFAULT 'ready'
    CHECK (bootstrap_status IN ('pending', 'ready', 'failed')),
topic_id INTEGER,
breakdown_id INTEGER,
CHECK (
    (creation_mode = 'imitation' AND topic_id IS NOT NULL AND breakdown_id IS NOT NULL)
    OR
    (creation_mode = 'original' AND topic_id IS NULL AND breakdown_id IS NULL)
)
```

新数据库直接创建新结构；只有检测到 `topic_id` 仍为 `NOT NULL` 或缺少 `creation_mode` 时才重建。

- [ ] **步骤 4：编写种子冻结、克隆和方向来源的失败测试**

```python
def test_seed_is_cloned_after_it_has_been_frozen(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = create_pending_original_project(repository)
    first = repository.get_current_seed(project["id"])
    frozen = repository.freeze_seed(first["id"])
    second = repository.create_seed_version(
        project["id"], seed_text="失业女程序员训练一个会拒绝工作的 AI",
        constraints={"cost_level": "low"}, reference_refs=[], created_by="writer-1",
    )

    assert frozen["status"] == "frozen"
    assert second["version"] == 2
    assert second["parent_version_id"] == first["id"]
    assert repository.update_seed(first["id"], {"seed_text": "覆盖"}) is None


def test_original_blueprints_keep_seed_source_refs(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = ready_original_project(repository)
    seed = repository.freeze_seed(repository.get_current_seed(project["id"])["id"])
    directions = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向 A"}, "risk_score": 0, "risk_level": "low"}],
        direction_type="original",
        source_refs={"seed_id": seed["id"], "seed_version": seed["version"]},
    )

    assert directions[0]["direction_type"] == "original"
    assert directions[0]["source_refs"]["seed_id"] == seed["id"]
```

- [ ] **步骤 5：实现种子和来源感知的仓储方法**

新增 `creative_seeds` 表及以下接口：

```text
create_original_project(
    *, seed_text: str, name: str, target_episode_count: int,
    target_platform: str = "", genre: str = "", target_audience: str = "",
    episode_duration_seconds: int = 120, constraints: dict[str, Any] | None = None,
    reference_refs: list[dict[str, Any]] | None = None,
    created_by: str, creation_request_id: str,
) -> dict[str, Any]

mark_project_bootstrap_status(project_id: int, status: str) -> dict[str, Any]
get_current_seed(project_id: int) -> dict[str, Any] | None
get_seed(seed_id: int) -> dict[str, Any] | None
list_seeds(project_id: int) -> list[dict[str, Any]]
update_seed(seed_id: int, values: dict[str, Any]) -> dict[str, Any] | None
freeze_seed(seed_id: int) -> dict[str, Any]
create_seed_version(project_id: int, **values: Any) -> dict[str, Any]
```

`create_original_project()` 在同一 SQLite 事务中写入 `pending` 项目和 v1 `draft` 种子。`list_projects()` 默认增加 `bootstrap_status = 'ready'` 并使用 `LEFT JOIN creative_topics`；`get_project()` 和 `get_project_inputs()` 对原创项目返回空 `topic`/`breakdown` 以及当前 `seed`。

- [ ] **步骤 6：运行仓储测试与现有 Phase 4 回归**

运行：`uv run pytest tests/test_original_creative_repository.py tests/test_creative_phase4.py -q`

预期：全部 PASS。

- [ ] **步骤 7：提交项目持久化变更**

```powershell
git add creative/repository.py tests/test_original_creative_repository.py
git commit -m "feat: 增加原创项目与种子存储"
```

## 任务 2：无选题来源的项目工作流

**文件：**
- 修改：`workflow/repository.py`（`ensure_project_task` 及项目后续任务创建）
- 创建：`tests/test_original_workflow_tasks.py`

- [ ] **步骤 1：编写空 `topic_id` 项目任务的失败测试**

```python
def test_original_project_tasks_use_project_as_the_only_resource(
    workflow_repository: WorkflowRepository, principal: Principal
) -> None:
    first = workflow_repository.ensure_project_task(
        "project-original-1", None,
        task_type="brief", role_code="creative_planner", actor=principal,
    )
    repeated = workflow_repository.ensure_project_task(
        "project-original-1", None,
        task_type="brief", role_code="creative_planner", actor=principal,
    )

    assert repeated["id"] == first["id"]
    assert first["topic_id"] is None
    assert first["project_id"] == "project-original-1"
    with workflow_repository.identity_repository.connect() as connection:
        workflow = connection.exec_driver_sql(
            "SELECT resource_type, resource_id FROM workflow_instances WHERE id = %s",
            (first["workflow_id"],),
        ).mappings().one()
    assert workflow["resource_type"] == "project"
    assert workflow["resource_id"] == "project-original-1"


def test_next_original_task_preserves_null_topic(
    workflow_repository: WorkflowRepository, principal: Principal
) -> None:
    brief = workflow_repository.ensure_project_task(
        "project-original-2", None,
        task_type="brief", role_code="creative_planner", actor=principal,
    )
    characters = workflow_repository.create_next_task(
        brief["id"], "characters", "creative_planner", actor=principal
    )
    assert characters["topic_id"] is None
    assert characters["project_id"] == brief["project_id"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_original_workflow_tasks.py -q`

预期：FAIL，`ensure_project_task()` 的空选题调用或后续任务断言失败。

- [ ] **步骤 3：放宽项目任务签名并保持项目归属校验**

```text
ensure_project_task(
    project_id: str,
    topic_id: int | None,
    task_type: str = "brief",
    role_code: str = "creative_planner",
    *, actor: Principal | dict[str, Any],
) -> dict[str, Any]
```

查重仍使用 `workflow_id + task_type + project_id`。`create_next_task()`、任务事件、提交审批和 `_ensure_workflow()` 原样传递空选题；项目工作流的 `resource_type/resource_id` 始终是 `project/<project_id>`。

- [ ] **步骤 4：运行工作流目标测试与审批回归**

运行：`uv run pytest tests/test_original_workflow_tasks.py tests/test_creative_workflow_integration.py tests/test_approval_free_system.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交工作流兼容变更**

```powershell
git add workflow/repository.py tests/test_original_workflow_tasks.py
git commit -m "feat: 支持无选题来源的项目任务"
```

## 任务 3：市场参考安全摘要

**文件：**
- 创建：`creative/market_reference_service.py`
- 创建：`tests/test_market_reference_service.py`

- [ ] **步骤 1：编写白名单、禁止词和权限失败测试**

```python
def test_market_context_excludes_story_specific_content(tmp_path: Path) -> None:
    repository, _, breakdown = seed_reference_material(tmp_path)
    service = MarketReferenceService(repository)
    bundle = service.resolve(
        [{"resource_type": "breakdown", "resource_id": breakdown["id"]}],
        can_read=lambda _kind, _identifier: True,
    )

    serialized = json.dumps(bundle.context, ensure_ascii=False)
    assert "都市" in serialized
    assert "25-45 岁女性" in serialized
    assert "顾清歌" not in serialized
    assert "你不配进顾家" not in serialized
    assert "顾清歌" in bundle.blocked_terms


def test_market_context_rejects_unreadable_reference(tmp_path: Path) -> None:
    repository, _, breakdown = seed_reference_material(tmp_path)
    with pytest.raises(PermissionError, match="无权读取市场参考"):
        MarketReferenceService(repository).resolve(
            [{"resource_type": "breakdown", "resource_id": breakdown["id"]}],
            can_read=lambda _kind, _identifier: False,
        )
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_market_reference_service.py -q`

预期：FAIL，`ModuleNotFoundError: creative.market_reference_service`。

- [ ] **步骤 3：实现安全参考包**

```python
@dataclass(frozen=True)
class MarketReferenceBundle:
    context: list[dict[str, Any]]
    source_refs: list[dict[str, Any]]
    blocked_terms: list[str]
    captured_at: float
```

拆解 `context` 只允许 `category`、`target_audience`、`emotional_curve` 的抽象标签和 `production_difficulty`；选题只允许平台、榜单类型、题材、评分置信度和快照时间。标题、主角身份、核心冲突、钩子、反转、台词和长文本只用于构建 `blocked_terms`，不得进入模型上下文。

服务接受 `can_read(resource_type, resource_id)` 回调；资源不存在或无权访问时抛出含资源 ID 的错误。重复引用按 `(resource_type, resource_id)` 去重，最多 10 项。

- [ ] **步骤 4：运行服务测试**

运行：`uv run pytest tests/test_market_reference_service.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交市场参考服务**

```powershell
git add creative/market_reference_service.py tests/test_market_reference_service.py
git commit -m "feat: 增加原创市场参考安全摘要"
```

## 任务 4：原创项目、种子 API 与跨库协调

**文件：**
- 创建：`creative/original_project_service.py`
- 修改：`creative/schema.py`
- 修改：`creative/api.py`（路由工厂、项目列表、`_ensure_stage_tasks`）
- 创建：`tests/test_original_project_api.py`
- 修改：`tests/test_page_api_authorization.py`

- [ ] **步骤 1：定义请求契约并编写创建 API 失败测试**

```python
class MarketReferenceRequest(BaseModel):
    resource_type: Literal["topic", "breakdown"]
    resource_id: int = Field(ge=1)


class OriginalProjectCreateRequest(BaseModel):
    seed_text: str = Field(min_length=2, max_length=500)
    name: str = Field(default="", max_length=200)
    genre: str = Field(default="", max_length=100)
    target_platform: str = Field(default="", max_length=30)
    target_audience: str = Field(default="", max_length=500)
    target_episode_count: int = Field(20, ge=1, le=100)
    episode_duration_seconds: int = Field(120, ge=30, le=1800)
    cost_level: str = Field(default="", max_length=30)
    content_boundaries: list[str] = Field(default_factory=list, max_length=20)
    reference_refs: list[MarketReferenceRequest] = Field(default_factory=list, max_length=10)
    creation_request_id: str = Field(min_length=1, max_length=200)
```

```python
def test_original_project_creation_is_idempotent_and_bootstraps_tasks(client) -> None:
    payload = original_project_payload(creation_request_id="same-request")
    first = client.post("/api/creative/projects/original", json=payload)
    second = client.post("/api/creative/projects/original", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert first.json()["creation_mode"] == "original"
    tasks = client.workflow.list_project_tasks(str(first.json()["id"]))
    assert {item["task_type"] for item in tasks} == {
        "brief", "characters", "outline", "beats", "script",
    }
    assert all(item["topic_id"] is None for item in tasks)
```

- [ ] **步骤 2：编写失败协调、隐藏项目和种子版本测试**

```python
def test_failed_bootstrap_is_hidden_and_same_request_can_reconcile(client, monkeypatch) -> None:
    monkeypatch.setattr(
        client.workflow, "ensure_project_task", FailOnce(client.workflow.ensure_project_task)
    )
    payload = original_project_payload(creation_request_id="retry-request")

    failed = client.post("/api/creative/projects/original", json=payload)
    assert failed.status_code == 503
    assert client.get("/api/creative/projects").json()["items"] == []

    retried = client.post("/api/creative/projects/original", json=payload)
    assert retried.status_code == 200
    assert retried.json()["bootstrap_status"] == "ready"
    assert len(client.repository.list_projects()) == 1


def test_frozen_seed_update_creates_a_new_version(client, original_project) -> None:
    seed = original_project["seed"]
    client.repository.freeze_seed(seed["id"])
    response = client.post(
        f"/api/creative/projects/{original_project['id']}/seeds/versions",
        json={"seed_text": "新的灵感", "constraints": {}, "reference_refs": []},
    )
    assert response.status_code == 201
    assert response.json()["version"] == 2
    assert response.json()["parent_version_id"] == seed["id"]


def test_project_list_reconciles_a_crash_left_pending_project(client) -> None:
    pending = client.repository.create_original_project(
        seed_text="未来公司按照员工情绪发工资",
        name="情绪公司",
        target_episode_count=20,
        created_by=client.principal.user_id,
        creation_request_id="crash-request",
    )
    response = client.get("/api/creative/projects")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [pending["id"]]
    assert client.repository.get_project(pending["id"])["bootstrap_status"] == "ready"
```

- [ ] **步骤 3：运行 API 测试验证失败**

运行：`uv run pytest tests/test_original_project_api.py tests/test_page_api_authorization.py -q`

预期：FAIL，原创创建路由返回 404。

- [ ] **步骤 4：实现 `OriginalProjectService`**

```text
OriginalProjectService(
    creative_repository: CreativeRepository,
    workflow_repository: WorkflowRepository,
    market_reference_service: MarketReferenceService,
)

create(
    payload: OriginalProjectCreateRequest,
    actor: Principal,
    *, can_read_reference: Callable[[str, int], bool],
) -> tuple[dict[str, Any], bool]
```

顺序固定为：预校验参考 → 按 `creation_request_id + created_by` 创建或读取 pending 项目 → 幂等加入创建者为 `project_owner` → 用空 `topic_id` 建立五个阶段任务 → 标记 `ready`。异常时标记 `failed` 并抛出可重试错误；同一请求重试从已有项目继续。

增加 `reconcile_pending_for_actor(actor)`：项目列表查询前只协调当前用户创建的 `pending` 项目，补齐成员和任务后标记 `ready`；协调失败则标记 `failed` 并继续返回其他可用项目。这样进程在 SQLite 提交后崩溃留下的 pending 项目能在下次进入项目页时恢复，又不需要跨用户模拟权限。

`_ensure_stage_tasks(project_id, topic_id, principal)` 的 `topic_id` 改为 `int | None`。普通项目列表最终只返回 `ready`，服务可按创建请求读取 `pending/failed` 进行协调。

- [ ] **步骤 5：注册原创和种子 API，并接入行级权限**

新增：

```text
POST  /api/creative/projects/original
GET   /api/creative/projects/{project_id}/seeds
PATCH /api/creative/seeds/{seed_id}
POST  /api/creative/projects/{project_id}/seeds/versions
```

创建使用 `require_permission("project.create")`。种子读取和修改复用 `assert_project_read_access()` 与 `assert_project_generation_access()`；冻结种子 PATCH 返回 409。把原创路由加入 `creative_api_authorization` 的项目权限匹配。

- [ ] **步骤 6：运行原创 API、权限和仿写创建回归**

运行：`uv run pytest tests/test_original_project_api.py tests/test_page_api_authorization.py tests/test_creative_phase4.py -q`

预期：全部 PASS。

- [ ] **步骤 7：提交原创项目服务和 API**

```powershell
git add creative/original_project_service.py creative/schema.py creative/api.py tests/test_original_project_api.py tests/test_page_api_authorization.py
git commit -m "feat: 增加原创项目与种子接口"
```

## 任务 5：原创方向生成、补生成与派生

**文件：**
- 创建：`creative/original_direction_service.py`
- 修改：`creative/prompt_templates.py`
- 修改：`creative/phase4_worker.py`
- 修改：`creative/repository.py`
- 修改：`creative/schema.py`
- 修改：`creative/api.py`
- 修改：`runtime/job_manager.py`
- 创建：`tests/test_original_direction_generation.py`
- 修改：`tests/test_job_manager.py`

- [ ] **步骤 1：编写原创方向结构和候选差异失败测试**

```python
ORIGINAL_REQUIRED_FIELDS = {
    "title", "logline", "theme", "protagonist", "protagonist_desire",
    "protagonist_flaw", "pressure_response", "main_opposition",
    "core_conflict", "main_goal", "background", "worldview", "story_engine",
    "first_three_hooks", "highlights", "differentiators", "audience",
    "market_fit", "cost_level", "originality_statement", "risks",
    "assumptions", "open_questions",
}


@pytest.mark.asyncio
async def test_original_generation_returns_diverse_directions() -> None:
    provider = FakeProvider(valid_original_payloads())
    directions, meta = await generate_original_directions(
        provider,
        project={"name": "情绪公司", "target_platform": "hg"},
        seed={"id": 7, "version": 1, "seed_text": "未来公司按情绪发工资", "constraints": {}},
        market_context=[], blocked_terms=[], count=3,
    )

    assert len(directions) == 3
    assert all(ORIGINAL_REQUIRED_FIELDS <= set(item["content"]) for item in directions)
    assert meta["prompt_template_version"] == "original-direction-v1"
    assert meta["missing_count"] == 0
```

- [ ] **步骤 2：编写禁止词、部分修复和派生失败测试**

```python
@pytest.mark.asyncio
async def test_invalid_candidates_are_repaired_without_dropping_valid_ones() -> None:
    provider = FakeProvider([
        payload_with_one_valid_and_two_duplicates(), payload_with_two_replacements(),
    ])
    directions, meta = await generate_original_directions(
        provider, PROJECT, SEED, [], ["顾清歌"], count=3,
    )
    assert len(directions) == 3
    assert provider.call_count == 2
    assert meta["repair_attempted"] is True


@pytest.mark.asyncio
async def test_original_direction_rejects_blocked_source_terms() -> None:
    with pytest.raises(LLMError, match="来源专有表达"):
        await generate_original_directions(
            FakeProvider([payload_containing("顾清歌")]),
            PROJECT, SEED, [], ["顾清歌"], count=3,
        )


@pytest.mark.asyncio
async def test_derived_direction_points_to_parent() -> None:
    result, meta = await derive_original_direction(
        FakeProvider([valid_variant_payload()]), PROJECT, SEED, PARENT_DIRECTION,
        instruction="保留情绪工资设定，改成悬疑类型", market_context=[], blocked_terms=[],
    )
    assert result["source_refs"]["parent_blueprint_id"] == PARENT_DIRECTION["id"]
    assert meta["prompt_template_version"] == "original-direction-variant-v1"
```

- [ ] **步骤 3：运行生成测试验证失败**

运行：`uv run pytest tests/test_original_direction_generation.py -q`

预期：FAIL，`ModuleNotFoundError: creative.original_direction_service`。

- [ ] **步骤 4：实现原创提示、逐项校验和一次修复**

```text
ORIGINAL_PROMPT_TEMPLATE_VERSION = "original-direction-v1"
ORIGINAL_VARIANT_PROMPT_TEMPLATE_VERSION = "original-direction-variant-v1"

original_direction_messages(payload: dict[str, Any], count: int) -> list[dict[str, str]]
original_variant_messages(payload: dict[str, Any]) -> list[dict[str, str]]
```

`generate_original_directions()` 逐项验证字段，复用 `_ngrams()` 计算候选重复度，并要求主角身份、背景、冲突发动机、关系结构和情绪回报至少两个维度不同。首轮保留合格候选，修复请求只要求缺失数量；第二轮仍不足时返回合格候选和 `missing_count`，完全无合格候选时抛出 `LLMError`。

命中 `blocked_terms` 的候选直接拒绝。没有市场参考时，`market_fit` 必须标记为“创作判断”；有参考时必须带 `reference_ids`。

- [ ] **步骤 5：按项目模式分流 Worker 并保存来源快照**

生成 API 对原创项目执行：读取当前种子 → 解析安全参考包 → 冻结种子 → 把 `seed_id`、`market_context`、`blocked_terms` 和参考快照写入 Job extra。Worker 分流：

```python
if action == "blueprints" and project["creation_mode"] == "original":
    seed = repository.get_seed(int(extra["seed_id"]))
    if seed is None or seed["status"] != "frozen":
        raise ValueError("原创方向任务缺少已冻结的种子版本")
    generated, meta = await generate_original_directions(
        provider, project, seed,
        list(extra.get("market_context") or []),
        list(extra.get("blocked_terms") or []), count,
    )
else:
    generated, meta = await generate_blueprints(
        provider, project, inputs["topic"], inputs["breakdown"], count
    )
```

原创方向以 `direction_type="original"` 保存，`source_refs` 保存种子和参考 ID。Worker 必须按 Job extra 中的 `seed_id` 读取冻结版本，不能读取“当前种子”，避免排队期间新版本改变输入。`PATCH /blueprints/{id}` 对仿写继续评估相似度，对原创只运行原创结构校验。`lock_brief()` 的高相似度差异说明只约束 `imitation`。

- [ ] **步骤 6：实现保留补生成、派生 API 和任务冲突**

```python
class BlueprintGenerateRequest(BaseModel):
    count: int = Field(3, ge=3, le=5)
    preserve_blueprint_ids: list[int] = Field(default_factory=list, max_length=5)
    workflow_task_id: str | None = None


class BlueprintDeriveRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    workflow_task_id: str = Field(min_length=1)
```

`preserve_blueprint_ids` 必须属于当前项目、为原创且未锁定，实际生成数量为 `count - len(preserve)`。增加 `POST /api/creative/blueprints/{id}/derive`，只允许原创方向，异步生成一个引用父方向的新候选。

当保留数已等于目标数时 API 返回 409“已保留方向数已达到目标，无需补生成”。派生任务使用 `action="blueprint_variant"`；把该 action 加入 Phase 4 Worker 白名单并增加单候选保存分支。

原创蓝图任务冲突标识使用 `(project_id, action, seed_id, parent_blueprint_id)`；同一项目同一种子相同派生目标冲突，不同项目继续并行。

- [ ] **步骤 7：运行原创生成、Worker、Job 和仿写回归**

运行：`uv run pytest tests/test_original_direction_generation.py tests/test_creative_phase4_generation.py tests/test_job_manager.py -q`

预期：全部 PASS。

- [ ] **步骤 8：提交原创方向生成变更**

```powershell
git add creative/original_direction_service.py creative/prompt_templates.py creative/phase4_worker.py creative/repository.py creative/schema.py creative/api.py runtime/job_manager.py tests/test_original_direction_generation.py tests/test_job_manager.py
git commit -m "feat: 增加原创方向生成与派生"
```

## 任务 6：策划端、编剧端与原创 Brief 页面

**文件：**
- 创建：`templates/common/workspace/original_project_modal.html`
- 修改：`templates/common/workspace/project_list.html`
- 修改：`templates/common/workspace/project_blueprints.html`
- 修改：`templates/planning/projects.html`
- 修改：`templates/writing/projects.html`
- 修改：`static/js/common/tasks.js`
- 修改：`static/js/common/creative.js`
- 修改：`static/css/workspaces.css`
- 创建：`tests/test_original_project_browser.py`
- 修改：`tests/test_role_page_content.py`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写双入口和轻量表单的失败测试**

```python
@pytest.mark.parametrize("template_name", ["planning/projects.html", "writing/projects.html"])
def test_both_role_pages_render_original_project_entry(template_name: str) -> None:
    html = render_workspace_template(template_name)
    assert 'data-action="create-original-project"' in html
    assert 'id="original-seed-text"' in html
    assert "一句话灵感或题材" in html


def test_planning_keeps_topic_creation_but_writing_does_not_force_it() -> None:
    planning = render_workspace_template("planning/projects.html")
    writing = render_workspace_template("writing/projects.html")
    assert 'data-section="topic-project-creation"' in planning
    assert 'data-section="topic-project-creation"' not in writing
    assert 'data-action="create-original-project"' in writing


def test_original_entry_is_hidden_without_project_create_permission() -> None:
    html = render_workspace_template(
        "writing/projects.html", permissions={"project.read"}
    )
    assert 'data-action="create-original-project"' not in html
```

- [ ] **步骤 2：编写浏览器创建请求和原创模式展示失败测试**

使用 `window.aiworkerModules` 加载 `tasks.js` 与 `creative.js`，模拟 API：

```python
def test_writer_can_submit_lightweight_original_project(page: Page) -> None:
    mount_original_project_form(page)
    page.get_by_role("button", name="创建原创项目").click()
    page.locator("#original-seed-text").fill("未来公司按照员工情绪发工资")
    page.get_by_role("button", name="创建并生成方向").click()

    request = page.evaluate("window.__lastOriginalProjectRequest")
    assert request["seed_text"] == "未来公司按照员工情绪发工资"
    assert request["target_episode_count"] == 20
    assert request["creation_request_id"]


def test_original_brief_hides_imitation_language(page: Page) -> None:
    mount_blueprint_workspace(page, creation_mode="original")
    assert page.get_by_text("原创方向与 Brief").is_visible()
    assert page.get_by_text("原创性说明").is_visible()
    assert page.get_by_text("仿制原因").count() == 0
    assert page.get_by_text("借鉴元素").count() == 0
```

- [ ] **步骤 3：运行 UI 测试验证失败**

运行：`uv run pytest tests/test_original_project_browser.py tests/test_role_page_content.py tests/test_webgui_ui_changes.py -q`

预期：FAIL，编剧页找不到原创入口，原创 Brief 仍显示仿写字段。

- [ ] **步骤 4：重构共享项目创建区与列表**

策划模板设置 `show_topic_creation = true` 后包含共享 `project_list.html`；编剧模板设置 `show_topic_creation = false` 后包含同一共享模板。共享模板只在 `principal.is_system_admin` 或 `project.create in principal.permissions` 时展示原创创建按钮和 modal，并按页面变量控制选题立项区。

项目表列调整为：项目、创作模式、创作来源、目标集数、当前阶段、更新时间/操作。来源显示：

```javascript
creativeProjectSource(project) {
    if (project.creation_mode === 'original') {
        return project.seed?.seed_text || project.seed_summary || '原创灵感';
    }
    return project.topic_title || '-';
}
```

- [ ] **步骤 5：实现原创创建、参考选择和种子状态**

在 `creativeWorkspace` 增加：

```javascript
originalCreate: {
    seed_text: '', name: '', genre: '', target_platform: '', target_audience: '',
    target_episode_count: 20, episode_duration_seconds: 120,
    cost_level: '', content_boundaries: [], reference_refs: [],
    creation_request_id: '', saving: false, error: ''
},
selectedSeedId: null,
preservedBlueprintIds: [],
```

`createOriginalProject()` 调用 `/api/creative/projects/original`；成功后进入 `/planning/projects/{id}/brief?generate_original=1`。编剧创建后也进入统一 Brief 路由，该路由已经允许 `/writing/projects` 作为额外授权来源。

参考选择复用页面已加载的已采纳选题和锁定拆解，只提交 `{resource_type, resource_id}`，不把原始内容放入浏览器生成请求。

- [ ] **步骤 6：按模式渲染方向比较和编辑字段**

原创模式标题为“原创方向与 Brief”，表格显示核心命题、主角、核心冲突、差异化卖点、成本和创作风险；编辑区显示 `logline/theme/story_engine/differentiators/originality_statement/risks/assumptions/open_questions`。仿写模式保持现有仿制原因、借鉴元素、差异化元素和相似度风险。

增加“保留”复选框、“补足方向”和“派生方向”。`generateProjectBlueprints()` 在原创模式提交 `preserve_blueprint_ids`；派生操作提交用户填写的变体要求。种子冻结后修改时调用创建版本接口，不能 PATCH 旧版本。

- [ ] **步骤 7：补充响应式样式并运行浏览器测试**

桌面方向表保持可横向比较；宽度小于 768px 时，每个方向转换为纵向卡片，表单按钮换行且页面无横向溢出。

运行：`uv run pytest tests/test_original_project_browser.py tests/test_role_page_content.py tests/test_webgui_ui_changes.py -q`

预期：全部 PASS；390px 视口满足 `scrollWidth <= clientWidth`。

- [ ] **步骤 8：提交双角色原创 UI**

```powershell
git add templates/common/workspace/original_project_modal.html templates/common/workspace/project_list.html templates/common/workspace/project_blueprints.html templates/planning/projects.html templates/writing/projects.html static/js/common/tasks.js static/js/common/creative.js static/css/workspaces.css tests/test_original_project_browser.py tests/test_role_page_content.py tests/test_webgui_ui_changes.py
git commit -m "feat: 在策划和编剧工作区接入原创项目"
```

## 任务 7：全链路回归、用户文档与交付验证

**文件：**
- 创建：`tests/test_original_workflow_integration.py`
- 修改：`剧本生成流程.md`

- [ ] **步骤 1：编写原创 Brief 汇入现有后续阶段的集成测试**

测试使用固定 Provider，不调用真实模型：

```python
def test_original_project_continues_through_existing_script_workflow(
    original_client, monkeypatch, tmp_path: Path
) -> None:
    project = create_ready_original_project_via_api(original_client)
    run_original_blueprint_worker(project, monkeypatch, tmp_path)
    direction = original_client.repository.get_project(project["id"])["blueprints"][0]
    lock_brief_via_workflow(original_client, project, direction)

    characters = original_client.repository.create_character_batch(
        project["id"], [{"name": "林岚"}],
        [{"from": "林岚", "to": "AI", "relation": "互相训练"}],
    )
    original_client.repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = original_client.repository.create_outline_batch(
        project["id"], [{"episode": 1, "summary": "第一次拒绝加班"}], "学会拒绝",
    )
    original_client.repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = original_client.repository.create_episode_beats(
        project["id"], 1, {"beats": [{"goal": "拒绝加班", "hook": "AI 主动辞职"}]},
    )
    original_client.repository.lock_episode_beats(beats["id"])
    script = original_client.repository.create_episode_script(
        project["id"], 1, {"scenes": [{"scene": 1, "action": "AI 关闭工位"}]},
    )

    assert script["upstream_refs"]["brief_id"] == direction["id"]
    assert original_client.repository.get_project(project["id"])["topic_id"] is None
```

- [ ] **步骤 2：运行集成测试验证完整原创路径**

运行：`uv run pytest tests/test_original_workflow_integration.py -q`

预期：PASS。

- [ ] **步骤 3：更新用户流程文档**

在 `剧本生成流程.md` 写明：

```text
仿写：已拆解剧目 -> 立项 -> 仿写方向 -> Brief
原创：一句话灵感 -> 可选市场参考 -> 3-5 个原创方向 -> Brief
汇合：Brief -> 人物关系 -> 分集大纲 -> Beats -> 场次剧本
```

同时说明市场参考不是改编来源，策划和编剧都可从各自项目页创建原创项目。

- [ ] **步骤 4：运行首期功能目标测试集**

```powershell
uv run pytest tests/test_original_creative_repository.py tests/test_original_workflow_tasks.py tests/test_market_reference_service.py tests/test_original_project_api.py tests/test_original_direction_generation.py tests/test_original_project_browser.py tests/test_original_workflow_integration.py -q
```

预期：全部 PASS，0 failed。

- [ ] **步骤 5：运行现有创作、权限和页面回归**

```powershell
uv run pytest tests/test_creative_phase4.py tests/test_creative_phase4_generation.py tests/test_creative_lifecycle.py tests/test_creative_workflow_integration.py tests/test_role_workflow_e2e.py tests/test_page_api_authorization.py tests/test_page_authorization.py tests/test_role_page_content.py tests/test_webgui_ui_changes.py tests/test_job_manager.py -q
```

预期：全部 PASS。

- [ ] **步骤 6：运行格式和文件头检查**

```powershell
uv run pre-commit run --files creative/repository.py creative/original_project_service.py creative/market_reference_service.py creative/original_direction_service.py creative/schema.py creative/prompt_templates.py creative/phase4_worker.py creative/api.py workflow/repository.py runtime/job_manager.py static/js/common/tasks.js static/js/common/creative.js static/css/workspaces.css templates/common/workspace/original_project_modal.html templates/common/workspace/project_list.html templates/common/workspace/project_blueprints.html templates/planning/projects.html templates/writing/projects.html tests/test_original_creative_repository.py tests/test_original_workflow_tasks.py tests/test_market_reference_service.py tests/test_original_project_api.py tests/test_original_direction_generation.py tests/test_original_project_browser.py tests/test_original_workflow_integration.py 剧本生成流程.md
```

预期：所有 hooks Passed。

- [ ] **步骤 7：提交集成测试和用户文档**

```powershell
git add tests/test_original_workflow_integration.py 剧本生成流程.md
git commit -m "test: 验证原创创作全链路"
```

- [ ] **步骤 8：检查最终变更范围**

```powershell
git status --short
git log --oneline -7
git diff HEAD~7..HEAD --check
```

预期：本功能提交只包含计划列出的文件；`git diff --check` 无输出。工作区原有无关修改仍保持原状，不进入本功能提交。

## 规格覆盖核对

- 一等原创模式与空选题来源：任务 1。
- 种子版本、冻结和追溯：任务 1、任务 4。
- 无 `topic_id` 的统一任务流：任务 2、任务 4。
- 可选市场参考、权限与安全白名单：任务 3、任务 4、任务 5。
- 3–5 个差异方向、修复、补生成和派生：任务 5。
- 策划页与编剧页共同创建：任务 6。
- 原创/仿写模式化 Brief：任务 6。
- Brief 后复用人物、大纲、Beats、场次剧本：任务 7。
- 跨库幂等与失败恢复：任务 4。
- 旧项目和现有仿写零回归：任务 1、任务 5、任务 7。
- 用户文档和最终质量门禁：任务 7。
