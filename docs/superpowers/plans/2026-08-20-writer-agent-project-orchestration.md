# 编剧大师对话式项目创作实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让策划和编剧在编剧大师对话中创建或关联项目，确认结构化动作卡后，安全生成 Brief、人物、大纲、Beats 和剧本，同时落实“创建者全流程创作、无项目治理权”的权限规则。

**架构：** 在现有 Writer Agent API 与创作工作流之间增加持久化动作账本、材料快照、目标阶段规划器和轻量异步编排器。LLM 只提出经过 schema 校验的白名单动作；WriterProjectOrchestrator 复用 CreativeApplicationService、WorkflowRepository 和 JobManager 执行写操作。创作库通过项目 revision 和 operation_id 保证乐观并发与阶段版本幂等。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、SQLAlchemy/PostgreSQL、现有 JobManager、原生 JavaScript、Jinja2、pytest、Playwright、Alembic、uv。

---

## 交付范围与文件结构

### 新建文件

- `creative/application_service.py`：creative API 与编剧大师共同使用的项目、阶段、版本和生成应用服务。
- `creative/project_creator_migration.py`：跨创作库与系统库迁移旧创建者成员身份。
- `writer_agent/schema.py`：动作、计划、资源、材料快照和错误的 Pydantic 契约。
- `writer_agent/store.py`：会话、动作、步骤和材料快照的 SQLite 持久化。
- `writer_agent/action_service.py`：模型动作白名单校验、卡片修订、按钮与自然语言决策统一。
- `writer_agent/orchestrator.py`：项目创建/关联、依赖规划、阶段步骤执行与版本冲突检查。
- `writer_agent/runner.py`：动作线程、现有 Job 对账、部分失败、取消和恢复。
- `system_database/alembic/versions/0014_project_creator_governance.py`：新增项目删除权限及平台负责人授权。
- `tests/test_creative_operation_idempotency.py`：创作版本幂等与项目 revision。
- `tests/test_project_creator_migration.py`：旧 creator-as-owner 跨库存量迁移。
- `tests/test_creative_application_service.py`：共享创作应用服务。
- `tests/test_project_creator_governance.py`：创建负责人、平台负责人和管理员权限矩阵。
- `tests/test_writer_agent_store.py`：动作账本和历史会话迁移。
- `tests/test_writer_agent_actions.py`：动作 schema、确认匹配和材料快照。
- `tests/test_writer_agent_orchestrator.py`：项目动作、目标阶段依赖和冲突。
- `tests/test_writer_agent_runner.py`：异步执行、部分失败和恢复。
- `tests/test_writer_agent_integration.py`：可被当前 pytest 配置收集的对话创作端到端链路。
- `tests/writer_agent_harness.py`：集成测试专用的真实仓储、确定性 Provider 与同步 Job Harness。
- `tests/test_writer_agent_browser.py`：项目上下文、动作卡和独立轮询。

### 修改文件

- `identity/permission_catalog.py`：登记 `project.delete` 并授予平台项目负责人。
- `identity/permission_service.py`：集中项目创作、治理、删除判断，确保 `project_creator` 不是管理角色。
- `workflow/repository.py`：支持 `project_creator` 创建引导与精确迁移，保持普通 active 成员现有全阶段协作规则。
- `workflow/api.py`：成员管理继续只接受治理权限，不让创建负责人通过成员身份越权。
- `creative/repository.py`：项目 revision、创作 operation 账本和幂等阶段写入。
- `creative/schema.py`：阶段 operation_id、对话创作指令和直接 Brief 请求。
- `creative/original_project_service.py`：创建者写为 `project_creator`。
- `creative/api.py`：创建入口、删除/归档权限及相关阶段路由改用共享应用服务。
- `creative/phase4_worker.py`：读取 generation_instruction、source_snapshot_id 和 operation_id，幂等保存成果。
- `static/js/selection/topics.js`：创建项目时不再把创建者预填为普通策划/编剧成员。
- `static/js/review/projects.js`：显示“项目创建负责人”并使用停用语义。
- `WebGUI.py`：共享 CreativeRepository/ApplicationService，向编剧大师注入依赖。
- `writer_agent/api.py`：瘦路由、动作端点、项目绑定和异步状态读取。
- `writer_agent/service.py`：全材料、项目权威上下文与 proposed_actions 提示契约。
- `static/js/review/writer_agent.js`：项目上下文、卡片渲染、决策与按动作轮询。
- `templates/review/writer_agent.html`：移除独立诊断限制，增加项目上下文与卡片样式。
- `web/page_access_catalog.py`：登记新增 Writer Agent API。
- `tests/test_writer_agent_api.py`、`tests/test_writer_agent_service.py`：兼容旧评审并覆盖新增响应。
- `tests/test_original_project_api.py`、`tests/test_planning_assignment_api.py`：创建者角色与创建时分配权限。
- `tests/test_project_member_management_api.py`、`tests/test_project_member_stage_editing.py`：治理隔离与既有成员协作回归。
- `tests/test_system_database_alembic.py`、`tests/test_page_authorization.py`、`tests/test_webgui_ui_changes.py`：迁移、端点和模板登记。

### 工作区保护约束

本计划涉及的 `WebGUI.py`、`identity/permission_catalog.py`、`creative/repository.py`、`static/js/review/projects.js`、`static/js/selection/topics.js`、`tests/test_planning_assignment_api.py`、`tests/test_system_database_alembic.py` 等文件当前已有其他未提交修改。执行每个任务前先用 `git diff -- <目标文件>` 确认重叠 hunk，只做精确补丁并保留用户修改；禁止 reset、checkout 或整文件覆盖。`0013_screenwriter_dashboard.py` 当前未跟踪，0014 开始前必须先确认它已作为迁移前置纳入执行分支。

## 实施里程碑

1. **权限基础可用：** 所有创建入口写入 `project_creator`，创建者不能治理项目。
2. **创作写入可复用：** 阶段版本支持 operation_id 幂等和项目 revision。
3. **对话动作可持久化：** 会话能保存项目、材料快照和待确认动作。
4. **直接目标阶段可执行：** 大纲、Beats、剧本计划能补齐依赖并分批运行。
5. **界面与验收完成：** 动作卡、进度恢复、权限和端到端流程全部通过。

### 规格覆盖索引

- 四类起点、3–5 个方向、直接 Brief、全部材料与可选参考：任务 5、6、11。
- 项目创建/关联、目标阶段依赖、权威版本与新版本：任务 3、6、7。
- 项目 revision、operation_id、动作/步骤/快照数据模型：任务 2、4。
- `project_creator`、平台负责人、删除/归档/成员与任务分配：任务 1。
- 白名单动作、统一 ActionCommand、读取/确认/取消/重试 API：任务 5、9。
- 10 集分批、部分失败、并发冲突、取消、刷新和进程恢复：任务 8。
- 项目上下文、六类卡片、安全渲染、移动端与独立轮询：任务 10。
- 稳定错误、权限复检、审计、旧会话/旧评审/现有项目页兼容：任务 4、6、8、9、11。

### 任务 1：拆分项目创建负责人和平台治理权限

**文件：**
- 创建：`creative/project_creator_migration.py`
- 创建：`system_database/alembic/versions/0014_project_creator_governance.py`
- 修改：`identity/permission_catalog.py:40-55,144-160,595-617`
- 修改：`identity/permission_service.py:29-31,147-192`
- 修改：`workflow/repository.py:39-42,1098-1165,1213-1235,1414-1458`
- 修改：`workflow/api.py:164-257`
- 修改：`creative/original_project_service.py:98-120`
- 修改：`creative/repository.py:1252-1285`
- 修改：`creative/api.py:1562-1617,2042-2127,2528-2595`
- 修改：`static/js/selection/topics.js:289-327`
- 修改：`static/js/review/projects.js:23-32,159-199`
- 测试：`tests/test_project_member_management_api.py`
- 测试：`tests/test_project_member_stage_editing.py`
- 测试：`tests/test_original_project_api.py`
- 测试：`tests/test_planning_assignment_api.py`
- 测试：`tests/test_system_database_alembic.py`
- 创建：`tests/test_project_creator_migration.py`
- 创建：`tests/test_project_creator_governance.py`
- 测试：`tests/test_identity_repository.py`
- 测试：`tests/test_page_access_service.py`
- 测试：`tests/test_selection_project_creation_browser.py`
- 测试：`tests/test_project_member_management_browser.py`

- [ ] **步骤 1：先写创建负责人治理隔离的失败测试**

在 `tests/test_project_member_management_api.py` 使用现有 PostgreSQL fixture 增加：

```python
def test_project_creator_can_edit_but_cannot_manage_members(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    database, identity = _postgres_identity(monkeypatch)
    workflow = WorkflowRepository(identity)
    creator_user = _role_user(identity, "screenwriter", "项目创建负责人")
    permissions = PermissionService(identity, SessionService(identity))
    creator = permissions.principal_for_user(creator_user["id"], "creator-session")
    assert creator is not None
    project_id = f"creator-{uuid.uuid4().hex}"
    workflow.add_project_member(
        project_id,
        creator.user_id,
        "project_creator",
        creator,
        allow_creator_bootstrap=True,
    )

    assert workflow.is_active_project_member(project_id, creator)
    with pytest.raises(PermissionError):
        workflow.assert_can_manage_project(project_id, creator)
```

在 `tests/test_original_project_api.py` 把创建测试补充为：

```python
members = client.workflow.list_project_members(str(first_project["id"]))
assert [item["project_role"] for item in members] == ["project_creator"]
```

在 `tests/test_planning_assignment_api.py` 增加创建者提交成员列表返回 403、平台项目负责人仍能创建并分配的两个断言。

同时增加重放保护测试：平台负责人停用 `project_creator` 后，以相同 creation_request_id 重放创建请求，成员关系仍为 inactive，不能被自动恢复。

在 `tests/test_project_creator_governance.py` 建立三身份矩阵并固定断言：创建负责人和普通 active 成员可创建、编辑、确认和锁定五阶段成果，但创建负责人删除、归档、创建时/创建后分配成员、分配/改派任务均为 403；无 membership 的平台 `project_owner` 与 `system_admin` 可管理；停用 creator 后创作读写立即为 403，`created_by` 保持不变。在 `tests/test_identity_repository.py` 断言 `project.delete` 已登记、不可委派且只进入平台项目负责人默认权限。

- [ ] **步骤 2：运行权限测试确认失败**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_project_member_management_api.py tests/test_original_project_api.py tests/test_planning_assignment_api.py -q
```

预期：创建成员仍为 `project_owner`，创建者仍可通过旧逻辑成为管理者或在创建时分配人员，测试失败。

- [ ] **步骤 3：实现项目角色和治理权限最小改动**

在 `workflow/repository.py` 定义并使用：

```python
PROJECT_CREATOR_ROLE = "project_creator"
OWNER_PROJECT_ROLES = frozenset({"owner", "project_owner"})
_MANAGED_PROJECT_ROLES = OWNER_PROJECT_ROLES | frozenset(
    {"manager", "project_manager"}
)
```

把 `allow_creator_bootstrap` 的条件改为只允许当前账户、`project.create` 和 `project_creator`：

```python
is_creator_bootstrap = (
    allow_creator_bootstrap
    and actor_id == user_id
    and project_role == PROJECT_CREATOR_ROLE
    and "project.create" in live_actor["permissions"]
)
```

将三个项目创建入口中的本地角色从 `project_owner` 改为 `project_creator`。保留 active 项目成员的现有全阶段编辑能力，不回退 `CreativeWorkflowAdapter._actor_can_edit_task()` 和工作流领取逻辑。

在 `identity/permission_catalog.py` 增加：

```python
"project.delete": "删除项目",
```

并只把 `project.delete` 授予平台 `project_owner`；系统管理员继续由 `*` 获得。
同时将 `project.delete` 加入 `_NON_DELEGABLE_PERMISSIONS`，避免自定义角色管理员把删除能力转授给创建负责人。

在 `PermissionService` 集中三个项目级判断，API 和 CreativeApplicationService 共同调用：

```python
def can_create_project_content(
    self, principal: Principal, project_id: str, *, active_member: bool
) -> bool:
    return (
        active_member
        or self.can_access_managed_project(principal, project_id)
    )

def can_govern_project(self, principal: Principal, project_id: str) -> bool:
    return (
        principal.is_system_admin
        or (
            "project.member.manage" in principal.permissions
            and self.can_access_managed_project(principal, project_id)
        )
    )

def can_delete_project(self, principal: Principal, project_id: str) -> bool:
    return (
        principal.is_system_admin
        or (
            "project.delete" in principal.permissions
            and self.can_access_managed_project(principal, project_id)
        )
    )
```

调用方使用 `WorkflowRepository.is_active_project_member()` 计算 active_member 并传入，避免 PermissionService 反向依赖 WorkflowRepository；不得把 project_creator 写入 Principal.permissions。

在 `creative/api.py` 将归档和删除改为显式治理权限：

```python
if payload.status == "archived" and not (
    principal.is_system_admin or "project.archive" in principal.permissions
):
    raise HTTPException(403, "没有归档项目的权限")

if payload.status == "archived" and not permission_service.can_access_managed_project(
    principal, str(project_id)
):
    raise HTTPException(403, "无权管理该项目")

if not (
    principal.is_system_admin or "project.delete" in principal.permissions
):
    raise HTTPException(403, "没有删除项目的权限")

if not permission_service.can_access_managed_project(principal, str(project_id)):
    raise HTTPException(403, "无权管理该项目")
```

创建时传入非空成员列表必须先检查 `project.member.manage`；空列表不因缺少管理权限失败。授权通过后才做去重和候选校验，成员候选查询也以 `project.member.manage` 为 required_permission，不能继续借 `project.create` 暴露或分配人员。

- [ ] **步骤 4：实现精确、可重入的旧成员迁移**

在 `workflow/repository.py` 增加只允许应用迁移器调用的方法：

```python
def migrate_creator_membership(
    self, project_id: str, created_by: str
) -> bool:
    """Only rename the creator's local project_owner membership."""
```

SQL 条件必须同时包含 `project_id`、`user_id=created_by` 和 `project_role='project_owner'`；若不存在新角色则原地改名，若已存在 `project_creator` 则以新角色记录及其 active/inactive 状态为准，只删除精确匹配的旧记录，不得修改用户全局角色。

在 `creative/repository.py` 增加 `list_project_creator_audit_rows()`，返回所有 `deleted=0` 项目的 `(id, created_by)`，不按 bootstrap_status 过滤。在 `creative/project_creator_migration.py` 遍历这些审计事实并调用该方法。迁移已有 inactive `project_creator` 时保持 inactive；已有新旧两条关系时以新关系为准，只删除精确匹配的旧 creator-as-owner 行。`build_creative_router()` 初始化时执行一次；重复执行结果不变。

系统迁移 `0014_project_creator_governance.py` 使用 `down_revision = "0013_screenwriter_dashboard"`，只负责登记 `project.delete` 及平台项目负责人授权，因为 Alembic 所在系统库不能直接读取创作 SQLite。实施前先确认当前未跟踪的 `0013_screenwriter_dashboard.py` 已纳入同一迁移分支，不得改写其 revision。

在 `workflow/repository.py` 提供 `ensure_project_creator()`；仅允许 actor_id 等于 creator_user_id 且 live actor 拥有 `project.create` 时建立缺失关系。已存在 active 关系时直接返回，已存在 inactive 关系时原样返回，创建请求重放不得重新激活。创建负责人不能停用自己；平台负责人或系统管理员移除创建负责人时改为停用关系并保留审计行，该角色不参与“最后一个项目 owner”保护。

删除 `static/js/selection/topics.js` 中把当前用户自动加入 `planner_member_ids` 或 `writer_member_ids` 的逻辑。`static/js/review/projects.js` 增加 `project_creator: '项目创建负责人'`，对该角色显示“停用”而不是“删除”。

- [ ] **步骤 5：运行权限、迁移和既有成员协作回归**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_identity_repository.py tests/test_project_creator_governance.py tests/test_project_member_management_api.py tests/test_project_member_stage_editing.py tests/test_page_access_service.py tests/test_original_project_api.py tests/test_planning_assignment_api.py tests/test_system_database_alembic.py tests/test_project_creator_migration.py tests/test_selection_project_creation_browser.py tests/test_project_member_management_browser.py -q
```

预期：全部 PASS；active 普通项目成员现有全阶段协作仍通过，创建负责人治理操作均为 403。

- [ ] **步骤 6：提交权限基础**

```powershell
git add identity/permission_catalog.py identity/permission_service.py workflow/repository.py workflow/api.py creative/repository.py creative/project_creator_migration.py creative/original_project_service.py creative/api.py static/js/selection/topics.js static/js/review/projects.js system_database/alembic/versions/0014_project_creator_governance.py tests/test_identity_repository.py tests/test_project_creator_governance.py tests/test_project_creator_migration.py tests/test_project_member_management_api.py tests/test_project_member_stage_editing.py tests/test_page_access_service.py tests/test_original_project_api.py tests/test_planning_assignment_api.py tests/test_system_database_alembic.py tests/test_selection_project_creation_browser.py tests/test_project_member_management_browser.py
git commit -m "feat: 拆分项目创建负责人治理权限"
```

### 任务 2：为创作项目和阶段版本增加并发与幂等基础

**文件：**
- 修改：`creative/repository.py:193-275,480-560,1287-1387,1600-2435`
- 修改：`creative/schema.py:69-130`
- 创建：`tests/test_creative_operation_idempotency.py`
- 测试：`tests/test_original_creative_repository.py`
- 测试：`tests/test_creative_lifecycle.py`

- [ ] **步骤 1：编写项目 revision 和 operation_id 的失败测试**

先在 `tests/test_creative_operation_idempotency.py` 定义本地数据构造器：

```python
def valid_original_brief(title: str) -> dict[str, Any]:
    return {
        "title": title, "logline": "未来公司按员工情绪发工资",
        "theme": "人在制度中保持选择", "protagonist": "林岚",
        "protagonist_desire": "获得自主权", "protagonist_flaw": "过度独立",
        "pressure_response": "先独自承担再学习协作", "main_opposition": "僵化制度",
        "core_conflict": "情绪考核", "main_goal": "改变制度",
        "background": "未来公司", "worldview": "情绪可量化但不能定义人",
        "story_engine": "每集解决一种情绪考核", "first_three_hooks": ["工资清零"],
        "highlights": ["规则反杀"], "differentiators": ["情绪劳动具象化"],
        "audience": "18-35岁", "market_fit": "创作判断：适合强设定短剧",
        "cost_level": "low", "originality_statement": "从种子独立发展",
        "risks": ["设定解释成本"], "assumptions": ["观众理解积分"],
        "open_questions": ["制度由谁建立"],
    }


def seed_original_project(repository: CreativeRepository, episodes: int = 1) -> dict:
    project, _ = repository.ensure_original_project(
        seed_text="未来公司按照员工情绪发工资", name="情绪公司",
        target_episode_count=episodes, target_platform="hg", genre="都市奇幻",
        target_audience="18-35岁", episode_duration_seconds=120,
        constraints={}, reference_refs=[], created_by="writer-1",
        creation_request_id=f"project-{episodes}",
    )
    return project


def seed_locked_outline_project(repository: CreativeRepository) -> dict:
    project = seed_original_project(repository)
    brief = repository.create_blueprints(
        project["id"], [{"content": valid_original_brief("情绪公司")}],
        direction_type="original",
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "林岚", "desire": "夺回选择权"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outline = repository.create_outline_batch(
        project["id"], [{"episode": 1, "summary": "工资因愤怒清零"}], "反抗制度"
    )
    repository.lock_outline_batch(project["id"], outline["batch_id"])
    return repository.get_project(project["id"])
```

给 `CreativeRepository` 增加 `list_episode_versions(project_id, stage, episode_number)`，按 version 升序返回该集历史，供幂等和后续验收复用。

```python
def test_same_operation_id_returns_the_same_beats_version(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_locked_outline_project(repository)

    first = repository.create_episode_beats(
        project["id"], 1, {"title": "第1集", "beats": [{"beat": "开场"}]},
        operation_id="writer-action-1:beats:1",
    )
    second = repository.create_episode_beats(
        project["id"], 1, {"title": "第1集", "beats": [{"beat": "开场"}]},
        operation_id="writer-action-1:beats:1",
    )

    assert second["id"] == first["id"]
    versions = repository.list_episode_versions(project["id"], "beats", 1)
    assert [item["version"] for item in versions] == [1]


def test_project_revision_rejects_a_stale_pointer_change(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_original_project(repository)
    revision = project["revision"]
    repository.update_project(
        project["id"], {"name": "新项目名"}, expected_revision=revision
    )

    with pytest.raises(ValueError, match="project revision changed"):
        repository.update_project(
            project["id"], {"genre": "悬疑"}, expected_revision=revision
        )
```

- [ ] **步骤 2：运行测试确认 schema 和方法签名尚不存在**

运行：

```powershell
uv run pytest tests/test_creative_operation_idempotency.py -q
```

预期：FAIL，报告缺少 `revision`、`operation_id` 或对应仓储方法。

- [ ] **步骤 3：增加 SQLite 安全迁移和 operation 结果表**

在 `CreativeRepository` 初始化中为旧库补列，并创建：

```sql
CREATE TABLE IF NOT EXISTS creative_operation_results (
    operation_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    resource_ref_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(project_id) REFERENCES creative_projects(id)
);
```

`creative_projects` 增加 `revision INTEGER NOT NULL DEFAULT 0`，语义限定为“权威创作上下文 revision”。项目 Brief 参数，以及 `locked_brief_id`、`character_batch_id`、`outline_batch_id` 等活动权威指针变化时递增；草稿生成进度、`*_draft` / `*_in_progress` 展示状态和 Job 进度不得递增此 revision，避免同一多步骤动作在完成首步后自行过期。需要记录普通行更新时间时继续使用 `updated_at`，不复用 authority revision。

权威字段变化时使用：

```sql
UPDATE creative_projects
SET name = ?, revision = revision + 1, updated_at = ?
WHERE id = ? AND revision = ?
```

`rowcount != 1` 时抛出 `ValueError("project revision changed")`。

`update_project()` 保持旧调用兼容：

```python
def update_project(
    self, project_id: int, values: dict[str, Any], *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
```

旧 API 不传 expected_revision 时维持现有行为；编排器必须传。所有会切换活动 Brief、人物和大纲指针的 mutator 同样增加可选 keyword `expected_revision`，并返回更新后的 revision；同一动作成功切换指针后，runner 把新 revision 写回后续步骤的 expected_context。

- [ ] **步骤 4：让阶段创建在同一事务中保存 operation 结果**

增加仓储私有方法：

```python
def _run_idempotent_operation(
    self,
    conn: sqlite3.Connection,
    *,
    operation_id: str,
    project_id: int,
    stage: str,
    request_hash: str,
    create: Callable[[sqlite3.Connection], dict[str, Any]],
) -> dict[str, Any]:
```

同时提供锁定步骤复用的公开窄接口：

```python
def record_operation_result(
    self,
    *,
    operation_id: str,
    project_id: int,
    stage: str,
    request_hash: str,
    resource_ref: dict[str, Any],
) -> dict[str, Any]:
```

它复用同一张 `creative_operation_results` 表：同 operation_id、同 request_hash 返回已有结果；hash 不同抛出 `IDEMPOTENCY_KEY_REUSED`。资源创建必须由 `_run_idempotent_operation()` 在同一事务内登记；锁定结果只在 `TransitionService` 成功后登记，用于跨库故障后的恢复读取。

同 operation_id、同 hash 返回已存结果；同 operation_id、不同 hash 抛出 `IDEMPOTENCY_KEY_REUSED`。`_run_idempotent_operation()` 在独立连接上先执行 `BEGIN IMMEDIATE`，进入事务后再检查 operation_id、计算版本号、写资源和 operation 行。request hash 使用 UTF-8、`sort_keys=True`、紧凑分隔符的稳定 JSON，且包含 project_id、stage、scope 和完整业务 payload；operation 唯一键竞争时回滚并重读，同 hash 返回已有结果，不同 hash 报错。

幂等包装必须保持原仓储方法的返回形状：`create_blueprints()` 仍返回 blueprint 列表，人物和大纲仍返回 batch，单集方法仍返回 episode。operation 表保存规范化 `ResourceRef` 或 Brief 资源 ID 数组；重放通过 `hydrate_operation_result()` 查询完整资源并恢复原返回形状，不能把 `ResourceRef` 直接返回给旧 API。把 Brief、人物批次、大纲批次、单集 Beats、单集剧本的新版本写入改为可选 `operation_id`，并保证资源 INSERT 与 operation 结果 INSERT 在同一个 SQLite 事务内。另增加 `create_blueprint_revision(blueprint_id, content, workflow_task_id, operation_id)`，在同一事务中沿用 candidate_group、设置 parent_version_id、写入新内容和 operation 结果，避免先 clone 再 PATCH 的非原子窗口。

在 `creative/schema.py` 为直接写入请求增加 `operation_id: str | None = Field(None, max_length=200)`；旧 API 不传时保持原行为。

- [ ] **步骤 5：运行仓储和生命周期回归**

运行：

```powershell
uv run pytest tests/test_creative_operation_idempotency.py tests/test_original_creative_repository.py tests/test_creative_lifecycle.py -q
```

预期：全部 PASS，旧资源版本行为不变。

- [ ] **步骤 6：提交并发与幂等基础**

```powershell
git add creative/repository.py creative/schema.py tests/test_creative_operation_idempotency.py tests/test_original_creative_repository.py tests/test_creative_lifecycle.py
git commit -m "feat: 增加创作版本幂等与并发校验"
```

### 任务 3：抽取共享 CreativeApplicationService

**文件：**
- 创建：`creative/application_service.py`
- 修改：`creative/api.py:702-1010,1192-1327,2160-3194`
- 修改：`creative/schema.py:79-130`
- 修改：`creative/phase4_worker.py:180-425`
- 修改：`creative/repository.py:1460-2435`
- 修改：`creative/workflow_adapter.py:445-468`
- 修改：`WebGUI.py:510-620`
- 创建：`tests/test_creative_application_service.py`
- 测试：`tests/test_creative_phase4_generation.py`
- 测试：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写共享服务失败测试**

在 `tests/test_creative_application_service.py` 定义可运行的本地 fixture：

```python
@pytest.fixture
def creative_context(tmp_path: Path) -> SimpleNamespace:
    repository = CreativeRepository(tmp_path / "creative.db")
    workflow = MagicMock()
    workflow.ensure_project_task.return_value = {
        "id": "brief-task", "task_type": "brief", "status": "in_progress",
        "project_id": "1", "assignee_user_id": "writer-1",
    }
    transitions = MagicMock()
    jobs = MagicMock()
    workflow_adapter = MagicMock()
    permission_service = MagicMock()
    permission_service.can_read_project.return_value = True
    actor = Principal(
        user_id="writer-1", username="writer", display_name="编剧",
        role_codes=("screenwriter",), permissions=frozenset({"project.create"}),
        data_scope="project", session_id="writer-session",
    )
    service = CreativeApplicationService(
        repository, workflow, transitions, jobs, tmp_path,
        original_project_service=MagicMock(),
        workflow_adapter=workflow_adapter,
        permission_service=permission_service,
    )

    def create_original_project() -> dict:
        project, _ = repository.ensure_original_project(
            seed_text="未来公司按照员工情绪发工资", name="情绪公司",
            target_episode_count=1, created_by=actor.user_id,
            creation_request_id="creative-service-test",
        )
        workflow.ensure_project_task.return_value["project_id"] = str(project["id"])
        return project

    return SimpleNamespace(
        application_service=service, repository=repository,
        workflow=workflow, transitions=transitions, jobs=jobs,
        actor=actor, create_original_project=create_original_project,
    )


def valid_original_brief(title: str) -> dict[str, Any]:
    return {
        "title": title, "logline": "未来公司按员工情绪发工资",
        "theme": "人在制度中保持选择", "protagonist": "林岚",
        "protagonist_desire": "获得自主权", "protagonist_flaw": "过度独立",
        "pressure_response": "先独自承担再学习协作", "main_opposition": "僵化制度",
        "core_conflict": "情绪考核", "main_goal": "改变制度",
        "background": "未来公司", "worldview": "情绪可量化但不能定义人",
        "story_engine": "每集解决一种情绪考核", "first_three_hooks": ["工资清零"],
        "highlights": ["规则反杀"], "differentiators": ["情绪劳动具象化"],
        "audience": "18-35岁", "market_fit": "创作判断：适合强设定短剧",
        "cost_level": "low", "originality_statement": "从种子独立发展",
        "risks": ["设定解释成本"], "assumptions": ["观众理解积分"],
        "open_questions": ["制度由谁建立"],
    }
```

```python
def test_application_service_creates_first_original_brief_idempotently(
    creative_context,
) -> None:
    service = creative_context.application_service
    project = creative_context.create_original_project()
    task = service.ensure_stage_task(project["id"], "brief", creative_context.actor)
    brief = valid_original_brief(title="情绪公司")

    first = service.persist_draft(
        project_id=project["id"], stage="brief", payload=brief,
        workflow_task_id=task["id"], operation_id="act-1:brief", actor=creative_context.actor,
    )
    second = service.persist_draft(
        project_id=project["id"], stage="brief", payload=brief,
        workflow_task_id=task["id"], operation_id="act-1:brief", actor=creative_context.actor,
    )

    assert second == first
    assert first["resource_type"] == "brief"
    assert first["status"] == "draft"
```

再增加生成任务测试，断言 job payload 包含：

```python
assert job.job.extra["generation_instruction"] == "加强第3集反转"
assert job.job.extra["source_snapshot_id"] == "snapshot-7"
assert job.job.extra["operation_id"] == "act-7:outline"
```

- [ ] **步骤 2：运行服务测试确认模块不存在**

运行：

```powershell
uv run pytest tests/test_creative_application_service.py -q
```

预期：FAIL，`creative.application_service` 尚不存在。

- [ ] **步骤 3：实现窄职责应用服务**

创建：

```python
class CreativeContextConflict(RuntimeError):
    """Raised when the expected creative authority revision is stale."""


class CreativeApplicationService:
    def __init__(
        self,
        repository: CreativeRepository,
        workflow_repository: WorkflowRepository,
        transitions: TransitionService,
        job_manager: JobManager | None,
        runtime_dir: Path,
        original_project_service: OriginalProjectService,
        workflow_adapter: CreativeWorkflowAdapter,
        permission_service: PermissionService,
    ) -> None:
        self.repository = repository
        self.workflow_repository = workflow_repository
        self.transitions = transitions
        self.job_manager = job_manager
        self.runtime_dir = runtime_dir
        self.original_project_service = original_project_service
        self.workflow_adapter = workflow_adapter
        self.permission_service = permission_service

    def get_project(self, project_id: int) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        return project

    def create_original_project(
        self, payload: dict[str, Any], actor: Principal, *, creation_request_id: str,
        can_read_reference: Callable[[str, int], bool],
    ) -> dict[str, Any]:
        request = OriginalProjectCreateRequest.model_validate(
            {**payload, "creation_request_id": creation_request_id}
        )
        project, _ = self.original_project_service.create(
            request, actor, can_read_reference=can_read_reference
        )
        return project

    def ensure_stage_task(
        self, project_id: int, stage: str, actor: Principal
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        role_code = "creative_planner" if stage in {"brief", "characters"} else "screenwriter"
        self.assert_stage_write_access(project_id, stage, actor)
        task = self.workflow_repository.ensure_project_task(
            str(project_id), project.get("topic_id"),
            task_type=stage, role_code=role_code, actor=actor,
        )
        if task["status"] not in {"pool", "assigned", "in_progress", "returned"}:
            task = self.workflow_repository.create_next_task(
                task["id"], stage, role_code, actor=actor
            )
        if task["status"] == "pool":
            if not self.workflow_repository.can_claim_task(task["id"], actor):
                raise PermissionError("该创作阶段已由其他人员处理")
            task = self.workflow_repository.claim_task(task["id"], actor)
        if (
            task["status"] in {"assigned", "in_progress", "returned"}
            and task.get("assignee_user_id") not in {None, actor.user_id}
            and not self.workflow_repository.is_active_project_member(
                str(project_id), actor
            )
        ):
            raise PermissionError("该创作阶段已由其他人员处理")
        return task

    def assert_stage_write_access(
        self, project_id: int, stage: str, actor: Principal
    ) -> None:
        if not self.permission_service.can_create_project_content(
            actor,
            str(project_id),
            active_member=self.workflow_repository.is_active_project_member(
                str(project_id), actor
            ),
        ):
            raise PermissionError("无权创作该项目")

    def validate_task_for_creation(
        self, workflow_task_id: str, project_id: int, stage: str, actor: Principal
    ) -> dict[str, Any]:
        return self.workflow_adapter.assert_task_for_creation(
            workflow_task_id, stage, actor, project_id=project_id
        )

    def persist_draft(
        self, *, project_id: int, stage: str, payload: dict[str, Any],
        workflow_task_id: str, operation_id: str, actor: Principal,
        episode_number: int | None = None,
    ) -> dict[str, Any]:
        self.assert_stage_write_access(project_id, stage, actor)
        self.validate_task_for_creation(
            workflow_task_id, project_id, stage, actor
        )
        if stage == "brief":
            validate_original_direction_content(payload)
            resource = self.repository.create_blueprints(
                project_id, [{"content": payload}], workflow_task_id=workflow_task_id,
                direction_type="original", operation_id=operation_id,
            )[0]
        elif stage == "characters":
            if not payload.get("characters"):
                raise ValueError("characters must contain at least one item")
            resource = self.repository.create_character_batch(
                project_id, payload["characters"], payload.get("relationships", []),
                workflow_task_id=workflow_task_id, operation_id=operation_id,
            )
        elif stage == "outline":
            resource = self.repository.create_outline_batch(
                project_id, payload["episodes"], payload.get("series_arc", ""),
                workflow_task_id=workflow_task_id, operation_id=operation_id,
            )
        elif stage == "beats" and episode_number is not None:
            validate_beats(payload)
            resource = self.repository.create_episode_beats(
                project_id, episode_number, payload,
                workflow_task_id=workflow_task_id, operation_id=operation_id,
            )
        elif stage == "script" and episode_number is not None:
            validate_script(payload)
            resource = self.repository.create_episode_script(
                project_id, episode_number, payload,
                workflow_task_id=workflow_task_id, operation_id=operation_id,
            )
        else:
            raise ValueError("unsupported creative stage payload")
        return self.resource_ref(stage, resource)

    def resource_ref(self, stage: str, resource: dict[str, Any]) -> dict[str, Any]:
        if stage == "characters":
            items = resource["characters"]
            project_id = int(items[0]["project_id"])
            version = max(int(item["version"]) for item in items)
            resource_id = f"{project_id}:{resource['batch_id']}"
        elif stage == "outline":
            items = resource["episodes"]
            project_id = int(items[0]["project_id"])
            version = max(int(item["version"]) for item in items)
            resource_id = f"{project_id}:{resource['batch_id']}"
        else:
            project_id = int(resource["project_id"])
            version = int(resource["version"])
            resource_id = str(resource["id"])
        return {
            "resource_type": stage, "resource_id": resource_id,
            "project_id": project_id, "version": version,
            "status": resource.get("status", "draft"),
            "parent_version_id": resource.get("parent_version_id"),
            "batch_id": resource.get("batch_id"),
            "episode_number": resource.get("episode_number"),
        }

    def lock_resource(
        self, *, project_id: int, stage: str, resource_id: str,
        workflow_task_id: str, operation_id: str, actor: Principal,
    ) -> dict[str, Any]:
        result = self.transitions.complete_creative_without_review(
            workflow_task_id, stage, resource_id, actor,
        )
        self.repository.record_operation_result(
            operation_id=operation_id,
            project_id=project_id,
            stage=f"{stage}.lock",
            request_hash=canonical_request_hash({
                "workflow_task_id": workflow_task_id,
                "resource_id": resource_id,
            }),
            resource_ref=result["resource"],
        )
        return result

    def create_revision(
        self, *, project_id: int, stage: str, replaces_resource_id: str,
        payload: dict[str, Any], workflow_task_id: str, operation_id: str,
        expected_project_revision: int, actor: Principal,
        episode_number: int | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if int(project["revision"]) != expected_project_revision:
            raise CreativeContextConflict("project authority revision changed")
        if stage == "brief":
            validate_original_direction_content(payload)
            resource = self.repository.create_blueprint_revision(
                int(replaces_resource_id), payload, workflow_task_id, operation_id
            )
            return self.resource_ref(stage, resource)
        return self.persist_draft(
            project_id=project_id, stage=stage, payload=payload,
            workflow_task_id=workflow_task_id, operation_id=operation_id,
            actor=actor, episode_number=episode_number,
        )

    def start_generation(
        self, *, project_id: int, stage: str, actor: Principal,
        workflow_task_id: str, operation_id: str,
        generation_instruction: str, source_snapshot_id: str,
        source_snapshot: dict[str, Any], episode_number: int | None = None,
        episode_start: int | None = None, episode_end: int | None = None,
        series: bool = False, count: int | None = None,
    ) -> dict[str, Any]:
        if self.job_manager is None:
            raise RuntimeError("creative job manager is unavailable")
        worker_action = {
            "brief": "blueprints", "characters": "characters",
            "outline": "outlines", "beats": "beats", "script": "script",
        }[stage]
        payload = {
            "platform": "creative", "crawler_type": "phase4_generate",
            "keywords": [], "save_data_option": "json",
            "get_comments": False, "get_sub_comments": False,
            "extra": {
                "action": worker_action, "normalized_stage": stage,
                "project_id": project_id,
                "created_by_user_id": actor.user_id,
                "workflow_task_id": workflow_task_id,
                "operation_id": operation_id,
                "generation_instruction": generation_instruction,
                "source_snapshot_id": source_snapshot_id,
                "source_snapshot": source_snapshot,
                "episode_number": episode_number,
                "episode_start": episode_start, "episode_end": episode_end,
                "series": series, "count": count, "chunk_size": 10,
                "db_path": str(self.repository.db_path.resolve()),
                "runtime_dir": str(self.runtime_dir.resolve()),
            },
        }
        state = self.job_manager.create_job(
            payload,
            created_by_user_id=actor.user_id,
            workflow_task_id=workflow_task_id,
            project_id=project_id,
        )
        return {
            "job_id": state.job_id, "status": state.status,
            "project_id": project_id, "stage": stage,
            "workflow_task_id": workflow_task_id,
            "operation_id": operation_id,
        }
```

服务端根据 `stage` 映射仓储方法，返回统一 `ResourceRef`。`resource_ref()` 集中处理 episode、人物 batch、大纲 batch 的 ID、version 和 parent_version_id，避免调用方自行猜测。`create_original_project()` 必须委托现有 `OriginalProjectService`，因此引用解析、`project_creator` 建立和五阶段任务 bootstrap 不会被对话入口绕过。`ensure_stage_task()` 抽取并复用现有 API 的续建 completed task、领取 pool task、拒绝他人任务和 active membership 校验；`persist_draft()` 也必须调用同一任务校验，actor 不能成为未使用参数。`create_revision()` 是重做 locked 成果的唯一入口：Brief 沿用 candidate_group 并设置 parent_version_id，人物/大纲提交完整新 batch，Beats/剧本按集创建带 parent 的新版本。为原创项目补充直接创建首个 Brief 草稿的方法；内容先通过真实函数 `validate_original_direction_content()`，不要求先启动 3–5 个方向生成任务。`canonical_request_hash()` 对排序后的 UTF-8 JSON 计算 SHA-256，Task 2 的仓储幂等接口和本服务统一使用它。

- [ ] **步骤 4：让 creative API 和 Worker 使用共享服务参数**

`build_creative_router()` 接收可选的 `repository` 和 `application_service`；`WebGUI.py` 中已有 `creative_repository` 作为共享实例，传给 creative 和 writer-agent 路由。

新增直接 Brief 路由：

```http
POST /api/creative/projects/{project_id}/blueprints
```

请求使用 `BlueprintCreateRequest(content, workflow_task_id, operation_id)`。现有生成、直接保存和锁定路由逐步改为调用 `CreativeApplicationService`，保持原 URL 和响应兼容。

`creative/phase4_worker.py` 从 job extra 读取 `generation_instruction`、`source_snapshot_id`、不可变 `source_snapshot` 和 `operation_id`，校验 snapshot ID 与内容指纹后将材料、项目权威上下文和指令加入模型输入，并用 operation_id 幂等写入成果。不能只传 Worker 无法读取的 snapshot ID。Job payload 对快照内容设置 2 MiB 上限；超限时快照保存分块内容文件，并在 extra 中传 runtime 根目录内的相对只读引用和 SHA-256，Worker 拒绝越界路径或指纹不符的文件。

`WebGUI.py` 的初始化顺序固定为：共享 `CreativeRepository` → `CreativeWorkflowAdapter(creative_repository, workflow_repository)` → `TransitionService(workflow_repository, creative_adapter=adapter)` → `OriginalProjectService` → `CreativeApplicationService`。creative router 和 writer-agent router 注入同一组实例，不能使用没有 creative adapter 的默认 TransitionService。

- [ ] **步骤 5：运行共享服务、生成和 API 回归**

运行：

```powershell
uv run pytest tests/test_creative_application_service.py tests/test_creative_phase4_generation.py tests/test_webgui_ui_changes.py -q
```

预期：全部 PASS；旧 creative 路由仍存在，新增直接 Brief 路由可用。

- [ ] **步骤 6：提交共享服务**

```powershell
git add creative/application_service.py creative/api.py creative/schema.py creative/phase4_worker.py creative/repository.py creative/workflow_adapter.py WebGUI.py tests/test_creative_application_service.py tests/test_creative_phase4_generation.py tests/test_webgui_ui_changes.py
git commit -m "refactor: 共享创作阶段应用服务"
```

### 任务 4：拆出 WriterAgentStore 并持久化动作账本

**文件：**
- 创建：`writer_agent/store.py`
- 创建：`writer_agent/schema.py`
- 修改：`writer_agent/api.py:48-155,270-405`
- 创建：`tests/test_writer_agent_store.py`
- 测试：`tests/test_writer_agent_api.py`

- [ ] **步骤 1：编写历史会话迁移、动作 CAS 和步骤幂等测试**

```python
def seed_action(
    store: WriterAgentStore, *, key: str, request_hash: str
) -> dict[str, Any]:
    session = store.create("writer-1", "测试动作", "一句话灵感")
    return store.create_action(
        session_id=session["id"], actor_user_id="writer-1",
        action_type="project.link", project_id=9,
        idempotency_key=key, request_hash=request_hash,
        payload={"project_id": 9},
        expected_context={"session_revision": session["context_revision"]},
        source_snapshot_id=None,
    )


def seed_run_plan(
    store: WriterAgentStore, *, key: str, project_id: int,
    episodes: tuple[int, int],
) -> dict[str, Any]:
    session = store.create("writer-1", key, "生成剧本")
    payload = {
        "target_stage": "script",
        "episode_start": episodes[0], "episode_end": episodes[1],
    }
    return store.create_action(
        session_id=session["id"], actor_user_id="writer-1",
        action_type="creative.run_plan", project_id=project_id,
        idempotency_key=key, request_hash=canonical_request_hash(payload),
        payload=payload,
        expected_context={"session_revision": session["context_revision"]},
        source_snapshot_id=None,
    )


def test_store_migrates_session_and_claims_action_once(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    session = store.create("writer-1", "旧剧本", "测试")
    action = store.create_action(
        session_id=session["id"], actor_user_id="writer-1",
        action_type="project.link", project_id=9,
        idempotency_key="session-1-link-9", request_hash="hash-1",
        payload={"project_id": 9}, expected_context={}, source_snapshot_id=None,
    )

    claimed = store.claim_pending_action(action["id"], expected_revision=1)
    assert claimed["status"] == "queued"
    with pytest.raises(WriterActionError, match="ACTION_NOT_PENDING"):
        store.claim_pending_action(action["id"], expected_revision=1)


def test_store_replays_the_same_decision_request(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    action = seed_action(store, key="decision", request_hash="decision")
    first = store.claim_pending_action(
        action["id"], expected_revision=1, client_request_id="confirm-1"
    )
    second = store.claim_pending_action(
        action["id"], expected_revision=1, client_request_id="confirm-1"
    )
    assert second["id"] == first["id"]
    assert second["status"] == "queued"


def test_store_reuses_same_idempotency_key_and_rejects_new_payload(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    first = seed_action(store, key="same", request_hash="a")
    assert seed_action(store, key="same", request_hash="a")["id"] == first["id"]
    with pytest.raises(WriterActionError, match="IDEMPOTENCY_KEY_REUSED"):
        seed_action(store, key="same", request_hash="b")


def test_store_expires_only_unconfirmed_actions(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    pending = seed_action(store, key="pending", request_hash="pending")
    queued = seed_action(store, key="queued", request_hash="queued")
    store.claim_pending_action(queued["id"], expected_revision=1)
    store.expire_pending_actions(older_than=time.time() + 1)
    assert store.require_action(pending["id"], "writer-1")["status"] == "expired"
    assert store.require_action(queued["id"], "writer-1")["status"] == "queued"


def test_two_store_instances_cannot_claim_overlapping_targets(tmp_path: Path) -> None:
    path = tmp_path / "writer-agent.db"
    first_store, second_store = WriterAgentStore(path), WriterAgentStore(path)
    first = seed_run_plan(first_store, key="first", project_id=17, episodes=(1, 10))
    second = seed_run_plan(second_store, key="second", project_id=17, episodes=(10, 20))
    first_store.claim_pending_action(first["id"], expected_revision=1)
    with pytest.raises(WriterActionError) as exc:
        second_store.claim_pending_action(second["id"], expected_revision=1)
    assert exc.value.code == "ACTIVE_ACTION_CONFLICT"
    assert exc.value.conflicting_action_id == first["id"]
```

- [ ] **步骤 2：运行存储测试确认失败**

运行：

```powershell
uv run pytest tests/test_writer_agent_store.py tests/test_writer_agent_api.py::test_writer_agent_migrates_legacy_sessions_to_report_history -q
```

预期：FAIL，store 模块和动作表尚不存在。

- [ ] **步骤 3：迁移现有会话存储并增加新表**

把 `WriterAgentStore` 从 `writer_agent/api.py` 移到 `writer_agent/store.py`，保留旧 import 兼容：

```python
from .store import WriterAgentStore
```

在初始化事务中：

- 给 `writer_agent_sessions` 增加 `project_id`、`context_revision`。
- 创建 `writer_agent_actions`。
- 创建 `writer_agent_action_steps`。
- 创建 `writer_agent_source_snapshots`。
- 给新消息生成稳定 `message_id`，助手消息允许 `cards` 和 `action_ids`；读取旧消息时允许这些字段缺失。

`writer_agent_actions` 增加 `decision_request_id`；同 action、同 client_request_id 的确认网络重放直接返回当前动作，不能再次 enqueue。不同 client_request_id 仍必须满足 pending + revision CAS。

在 `writer_agent/schema.py` 先定义全模块统一错误：

```python
class WriterActionError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, retryable: bool = False,
        conflicting_action_id: str | None = None,
        current_revision: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.retryable = retryable
        self.conflicting_action_id = conflicting_action_id
        self.current_revision = current_revision
```

实现以下完整仓储边界：

- 会话：`get(session_id, actor_user_id)`、`bind_project(session_id, project_id, expected_context_revision)`。
- 动作：`create_action()`、`require_action(action_id, actor_user_id)`、`revise_action()`、`claim_pending_action()`、`start_action()`、`set_action_phase()`、`advance_expected_context()`、`finish_action()`、`fail_action()`、`record_action_result()`、`expire_pending_actions()`、`list_session_actions()`、`list_active_actions()`。
- 步骤：`upsert_step()`、`list_runnable_steps()`、`finish_step()`、`fail_step()`、`list_steps()`、`active_job_ids()`、`refresh_job_steps()`。
- 恢复：`require_retryable_action()` 和 `create_retry_action()`；新动作写入 `retry_of_action_id` 和原始 `root_action_id`，只复制 failed 或尚未开始的步骤，保留每步首次生成的 operation_id，成功步骤作为已满足依赖引用而不再执行。
- 快照：`save_source_snapshot()` 和 `require_source_snapshot()`。

所有状态转换使用 `WHERE status=? AND revision=?` 检查 rowcount。`claim_pending_action()` 使用 `BEGIN IMMEDIATE`，在同一事务中重新计算并检查活动 target scope：整体阶段和区间相交规则与 Task 8 的 `creative_job_scope()` 一致；发现 queued/running 冲突则返回 `ACTIVE_ACTION_CONFLICT` 和 conflicting_action_id，否则才做 pending → queued CAS，确保多 Web 进程不能同时抢占。`record_action_result()` 对同 action 的相同规范化结果幂等；不同结果返回 `IDEMPOTENCY_KEY_REUSED`。`list_runnable_steps()` 只返回依赖全部成功且自身为 pending 的步骤，并按计划序号排序；failed 步骤只能由 retry action 复制后重新成为 pending。

- [ ] **步骤 4：让 session 响应带项目和动作但保持旧字段**

`WriterAgentStore._row()` 返回：

```python
item["project_id"] = item.get("project_id")
item["context_revision"] = int(item.get("context_revision") or 0)
item["actions"] = self.list_session_actions(int(item["id"]))
```

上传材料或切换项目时递增 `context_revision`。`GET /sessions` 暂时保持完整响应，避免破坏旧客户端。

- [ ] **步骤 5：运行 store 和现有 Writer Agent API 回归**

运行：

```powershell
uv run pytest tests/test_writer_agent_store.py tests/test_writer_agent_api.py -q
```

预期：全部 PASS，旧报告和上传行为不变。

- [ ] **步骤 6：提交动作持久化**

```powershell
git add writer_agent/store.py writer_agent/schema.py writer_agent/api.py tests/test_writer_agent_store.py tests/test_writer_agent_api.py
git commit -m "feat: 持久化编剧大师项目动作"
```

### 任务 5：定义结构化创作意图、材料快照和确认匹配

**文件：**
- 修改：`writer_agent/schema.py`
- 创建：`writer_agent/action_service.py`
- 修改：`writer_agent/service.py:24-110`
- 修改：`writer_agent/api.py:330-365`
- 创建：`tests/test_writer_agent_actions.py`
- 修改：`tests/test_writer_agent_service.py`

- [ ] **步骤 1：编写动作白名单、全部材料和自然语言匹配测试**

```python
def pending_action(
    action_id: str, action_type: str, *, target_stage: str | None = None
) -> dict[str, Any]:
    return {
        "id": action_id, "action_type": action_type, "status": "pending",
        "revision": 1, "target_stage": target_stage,
    }


def direction(
    title: str, protagonist: str, background: str, conflict: str
) -> dict[str, Any]:
    return {
        "title": title, "protagonist": protagonist, "background": background,
        "core_conflict": conflict, "relationship_engine": f"{protagonist}与同盟互相制衡",
        "emotional_payoff": f"{protagonist}夺回选择权",
        "difference_dimensions": ["protagonist", "background", "conflict"],
    }


def test_model_cannot_propose_an_unknown_write_action() -> None:
    with pytest.raises(ValidationError):
        ProposedAction.model_validate({
            "action_type": "project.delete",
            "title": "删除项目",
            "payload": {"project_id": 7},
        })


def test_confirmation_requires_one_matching_pending_action() -> None:
    actions = [
        pending_action("a", "creative.run_plan", target_stage="outline"),
        pending_action("b", "creative.lock", target_stage="brief"),
    ]
    assert match_natural_language_decision("确认生成大纲", actions).action_id == "a"
    with pytest.raises(WriterActionError, match="ACTION_AMBIGUOUS"):
        match_natural_language_decision("继续", actions)


def test_prompt_contains_all_materials_and_project_authority() -> None:
    messages = writer_messages(
        materials=[
            {"name": "旧剧本.md", "content": "第一版"},
            {"name": "人物补充.txt", "content": "主角怕水"},
        ],
        history=[], user_message="生成第1集剧本", review_requested=False,
        project_context={"locked_brief": {"id": 9, "version": 2}},
    )
    payload = json.loads(messages[-1]["content"])
    assert [item["name"] for item in payload["全部材料"]] == ["旧剧本.md", "人物补充.txt"]
    assert payload["项目权威上下文"]["locked_brief"]["id"] == 9


def test_direction_card_requires_three_to_five_materially_distinct_items() -> None:
    card = DirectionCard.model_validate({
        "card_id": "card-directions-1",
        "items": [
            direction("情绪公司", "林岚", "未来公司", "情绪考核"),
            direction("潮汐公约", "周屿", "海上社区", "资源配给"),
            direction("满分之外", "许真", "虚拟学校", "人格评分"),
        ]
    })
    assert len(card.items) == 3
    assert all(len(item.difference_dimensions) >= 2 for item in card.items)


def test_source_snapshot_marks_optional_references_and_permission_result() -> None:
    snapshot = build_source_snapshot(
        materials=[], selected_messages=[], project_context={},
        reference_refs=[{
            "resource_type": "topic", "resource_id": 7, "name": "职场榜单",
            "version": 3, "readable": True,
        }], assumptions=[], model_version="writer-v1", prompt_version="creative-v1",
    )
    assert snapshot["references"][0]["readable"] is True
    assert snapshot["references"][0]["version"] == 3
```

- [ ] **步骤 2：运行动作服务测试确认失败**

运行：

```powershell
uv run pytest tests/test_writer_agent_actions.py tests/test_writer_agent_service.py -q
```

预期：FAIL，缺少 ProposedAction、确认匹配和全材料提示契约。

- [ ] **步骤 3：实现 Pydantic 白名单契约**

在 `writer_agent/schema.py` 定义：

```python
ActionType = Literal[
    "project.create_original", "project.link", "creative.run_plan", "creative.lock"
]
Stage = Literal["brief", "characters", "outline", "beats", "script"]

class ProposedAction(BaseModel):
    action_type: ActionType
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=1000)
    source_card_id: str | None = None
    project_id: int | None = None
    target_stage: Stage | None = None
    episode_start: int | None = Field(None, ge=1, le=100)
    episode_end: int | None = Field(None, ge=1, le=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list, max_length=20)


class DirectionCandidate(BaseModel):
    title: str
    protagonist: str
    background: str
    core_conflict: str
    relationship_engine: str
    emotional_payoff: str
    difference_dimensions: list[
        Literal["protagonist", "background", "conflict", "relationship", "emotional_payoff"]
    ] = Field(min_length=2)


class DirectionCard(BaseModel):
    card_id: str
    card_type: Literal["directions"] = "directions"
    items: list[DirectionCandidate] = Field(min_length=3, max_length=5)


class BriefCard(BaseModel):
    card_id: str
    card_type: Literal["brief"] = "brief"
    content: dict[str, Any]
    assumptions: list[str] = Field(default_factory=list)


class CreativePlanCard(BaseModel):
    card_id: str
    card_type: Literal["plan"] = "plan"
    title: str
    summary: str
    steps: list[dict[str, Any]]


class ProgressCard(BaseModel):
    card_id: str
    card_type: Literal["progress"] = "progress"
    action_id: str
    status: str
    completed_steps: int
    total_steps: int


class ResultCard(BaseModel):
    card_id: str
    card_type: Literal["result"] = "result"
    project_id: int
    resource_refs: list[dict[str, Any]]
    open_url: str


class ConflictCard(BaseModel):
    card_id: str
    card_type: Literal["conflict"] = "conflict"
    code: str
    message: str
    current_revision: int | None = None


class ActionCommand(BaseModel):
    session_id: int
    action_id: str
    decision: Literal["confirm", "cancel"]
    actor: Principal
    expected_revision: int
    client_request_id: str
```

给 `ProposedAction` 增加 model validator：`project.create_original` 校验创建前最低信息；`project.link` 必须有 project_id；`creative.run_plan` 必须有 target_stage，Beats/剧本的集数范围必须完整且 start <= end；`creative.lock` 必须有项目、阶段和资源 ID。服务端覆盖 `target_key`、actor、权限字段和 idempotency_key；模型不能提供这些可信字段。方向卡除了数量约束，还要求每个候选声明至少两个差异维度；服务端对候选成对比较主角、背景、核心冲突、人物关系和情绪回报，少于两个实际差异维度时返回 `RESULT_VALIDATION_FAILED`，不展示伪差异候选。

- [ ] **步骤 4：实现材料快照和自然语言决策**

`build_source_snapshot()` 按固定优先级保存项目锁定成果、全部材料、用户选中消息、可选参考和 AI 假设，并保存模型/提示模板版本。每条参考必须记录资源 ID、名称、版本、确认时权限结果；未选择参考时允许空列表。内容指纹使用规范化 UTF-8 文本 SHA-256。

`match_natural_language_decision()` 只在当前会话 pending 动作中匹配：

- “采用/确认/开始/生成”加阶段词可精确匹配。
- “继续”只在唯一 pending 动作时确认。
- 消息包含新约束时返回 `revision_required=True`，不执行。
- “取消”只取消唯一匹配动作。

- [ ] **步骤 5：修改 LLM 契约并保留评审模式**

非评审模式 contract 增加：

```python
{
    "reply": "直接答复",
    "cards": [{
        "card_id": "card-1", "card_type": "brief",
        "content": valid_original_brief("情绪公司"), "assumptions": [],
    }],
    "proposed_actions": [{
        "action_type": "creative.run_plan",
        "title": "生成第1-5集剧本",
        "target_stage": "script",
        "episode_start": 1,
        "episode_end": 5,
        "payload": {},
        "assumptions": [],
    }],
}
```

评审请求仍使用原 report contract；非评审回复的 cards 使用 `DirectionCard | BriefCard | CreativePlanCard | ProgressCard | ResultCard | ConflictCard` 判别联合校验，并作为助手消息的结构化字段随 `message_id/content/action_ids` 保存。动作通过 `source_card_id` 引用卡片，不把动作本体嵌入 messages_json。模型返回的动作先经 `ProposedAction.model_validate()`，非法动作只作为普通文本答复，不写入动作表。

- [ ] **步骤 6：运行动作和评审回归并提交**

运行：

```powershell
uv run pytest tests/test_writer_agent_actions.py tests/test_writer_agent_service.py tests/test_writer_agent_api.py -q
```

预期：全部 PASS。

```powershell
git add writer_agent/schema.py writer_agent/action_service.py writer_agent/service.py writer_agent/api.py tests/test_writer_agent_actions.py tests/test_writer_agent_service.py tests/test_writer_agent_api.py
git commit -m "feat: 生成并确认结构化创作动作"
```

### 任务 6：实现创建项目和关联已有项目动作

**文件：**
- 创建：`writer_agent/orchestrator.py`
- 修改：`writer_agent/schema.py`
- 修改：`writer_agent/store.py`
- 修改：`writer_agent/action_service.py`
- 创建：`tests/test_writer_agent_orchestrator.py`
- 测试：`tests/test_original_project_api.py`

- [ ] **步骤 1：编写创建、关联和越权失败测试**

```python
@pytest.fixture
def orchestrator_context(tmp_path: Path) -> SimpleNamespace:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    actor = Principal(
        user_id="writer-1", username="writer", display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create", "script.review.generate"}),
        data_scope="project", session_id="writer-session",
    )
    session = store.create(actor.user_id, "原创会话", "一句话灵感")
    creative_service = MagicMock()
    creative_service.create_original_project.return_value = {
        "id": 17, "name": "情绪公司", "creation_mode": "original",
    }
    creative_service.get_project.side_effect = lambda project_id: {
        "id": project_id, "name": f"项目 {project_id}", "revision": 0,
    }
    permission_service = MagicMock()
    permission_service.can_read_project.side_effect = (
        lambda _actor, project_id: project_id != "404"
    )
    orchestrator = WriterProjectOrchestrator(
        store, creative_service, permission_service, MagicMock(),
        can_read_reference=lambda _actor, _resource_type, resource_id: resource_id != 404,
    )

    def create_action(action_type: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return store.create_action(
            session_id=session["id"], actor_user_id=actor.user_id,
            action_type=action_type, project_id=payload.get("project_id"),
            idempotency_key=f"{action_type}:{hashlib.sha256(normalized.encode()).hexdigest()}",
            request_hash=hashlib.sha256(normalized.encode()).hexdigest(),
            payload=payload,
            expected_context={"session_revision": session["context_revision"]},
            source_snapshot_id=None,
        )

    return SimpleNamespace(
        store=store, actor=actor, session=session, orchestrator=orchestrator,
        creative_service=creative_service, permission_service=permission_service,
        create_action=create_action,
    )


def test_create_action_binds_idempotent_original_project(orchestrator_context) -> None:
    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "未来公司按照员工情绪发工资",
            "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg",
            "target_audience": "18-35岁", "target_episode_count": 20,
            "episode_duration_seconds": 120, "cost_level": "low",
            "content_boundaries": ["不美化职场霸凌"], "reference_refs": [],
        },
    )

    first = orchestrator_context.orchestrator.execute_project_action(
        action["id"], orchestrator_context.actor
    )
    second = orchestrator_context.orchestrator.execute_project_action(
        action["id"], orchestrator_context.actor
    )

    assert second["project_id"] == first["project_id"]
    session = orchestrator_context.store.get(action["session_id"], "writer-1")
    assert session["project_id"] == first["project_id"]


def test_link_action_rechecks_project_read_access(orchestrator_context) -> None:
    action = orchestrator_context.create_action(
        "project.link", payload={"project_id": 404}
    )
    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )
    assert exc.value.code == "PROJECT_ACCESS_DENIED"


def test_create_action_rejects_reference_revoked_after_card_creation(
    orchestrator_context,
) -> None:
    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "情绪工资", "name": "情绪公司", "genre": "都市奇幻",
            "target_platform": "hg", "target_audience": "18-35岁",
            "target_episode_count": 20, "episode_duration_seconds": 120,
            "cost_level": "low", "content_boundaries": [],
            "reference_refs": [{"resource_type": "topic", "resource_id": 404}],
        },
    )
    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )
    assert exc.value.code == "REFERENCE_UNAVAILABLE"
```

- [ ] **步骤 2：运行项目动作测试确认失败**

运行：

```powershell
uv run pytest tests/test_writer_agent_orchestrator.py -q
```

预期：FAIL，orchestrator 尚不存在。

- [ ] **步骤 3：实现项目动作执行边界**

创建：

```python
class WriterProjectOrchestrator:
    def __init__(
        self,
        store: WriterAgentStore,
        creative_service: CreativeApplicationService,
        permission_service: PermissionService,
        workflow_repository: WorkflowRepository,
        can_read_reference: Callable[[Principal, str, int], bool],
    ) -> None:
        self.store = store
        self.creative_service = creative_service
        self.permission_service = permission_service
        self.workflow_repository = workflow_repository
        self.can_read_reference = can_read_reference

    @staticmethod
    def project_url(
        project_id: int, stage: str = "brief", episode_number: int | None = None
    ) -> str:
        if stage == "brief":
            return f"/planning/projects/{project_id}/brief"
        if stage == "characters":
            return f"/planning/projects/{project_id}/characters"
        if stage == "outline":
            return f"/writing/projects/{project_id}/outlines"
        if stage in {"beats", "script"} and episode_number is not None:
            suffix = "beats" if stage == "beats" else "scripts"
            return f"/writing/projects/{project_id}/episodes/{episode_number}/{suffix}"
        return "/writing/projects"

    def execute_project_action(
        self, action_id: str, actor: Principal
    ) -> dict[str, Any]:
        action = self.store.require_action(action_id, actor.user_id)
        if action.get("result"):
            return action["result"]
        payload = action["payload"]
        if action["action_type"] == "project.link":
            project_id = int(payload["project_id"])
            project = self.creative_service.get_project(project_id)
            if not self.permission_service.can_read_project(actor, str(project_id)):
                raise WriterActionError("PROJECT_ACCESS_DENIED", "无权关联该项目")
        elif action["action_type"] == "project.create_original":
            for reference in payload.get("reference_refs", []):
                if not self.can_read_reference(
                    actor,
                    str(reference["resource_type"]),
                    int(reference["resource_id"]),
                ):
                    raise WriterActionError(
                        "REFERENCE_UNAVAILABLE", "参考已失效或无权读取"
                    )
            project = self.creative_service.create_original_project(
                payload, actor,
                creation_request_id=f"writer-session:{action['session_id']}:action:{action_id}",
                can_read_reference=lambda resource_type, resource_id:
                    self.can_read_reference(actor, resource_type, resource_id),
            )
            project_id = int(project["id"])
        else:
            raise WriterActionError("ACTION_TYPE_INVALID", "该动作不是项目动作")
        session = self.store.get(int(action["session_id"]), actor.user_id)
        if session.get("project_id") != project_id:
            self.store.bind_project(
                int(action["session_id"]), project_id,
                expected_context_revision=int(action["expected_context"]["session_revision"]),
            )
        result = {
            "project_id": project_id, "name": project["name"],
            "creation_mode": project.get("creation_mode"),
            "open_url": self.project_url(project_id),
        }
        self.store.record_action_result(action_id, result)
        return result
```

创建动作要求 `project.create`，并用 `writer-session:{session_id}:action:{action_id}` 作为 `creation_request_id`。关联动作读取项目后调用 `PermissionService.can_read_project()`；成功后通过 `store.bind_project(session_id, project_id, expected_context_revision)` 更新单一当前项目。若进程在 bind 成功、record_action_result 前退出，重放发现 session 已绑定同一 project_id 时跳过旧 revision CAS 并补记结果；若已绑定其他项目仍返回 `STALE_PROJECT_CONTEXT`。

项目字段必须完整：项目名、故事核心、类型、平台、受众、集数、时长、成本、内容边界和假设。缺字段时动作保持 pending 并返回 `RESULT_VALIDATION_FAILED`，不能创建半成品项目。

- [ ] **步骤 4：实现引用二次权限校验和审计结果**

确认时逐个重新解析 `reference_refs`；无权或失效返回 `REFERENCE_UNAVAILABLE`。动作结果保存 project_id、项目名、creation_mode 和打开项目 URL，不复制项目完整内容。

- [ ] **步骤 5：运行创建、关联和原创项目回归**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_writer_agent_orchestrator.py tests/test_original_project_api.py -q
```

预期：全部 PASS。

- [ ] **步骤 6：提交项目动作**

```powershell
git add writer_agent/orchestrator.py writer_agent/schema.py writer_agent/store.py writer_agent/action_service.py tests/test_writer_agent_orchestrator.py tests/test_original_project_api.py
git commit -m "feat: 支持对话创建和关联项目"
```

### 任务 7：实现目标阶段规划器和创作基线包

**文件：**
- 修改：`writer_agent/schema.py`
- 修改：`writer_agent/orchestrator.py`
- 修改：`creative/application_service.py`
- 修改：`tests/test_writer_agent_orchestrator.py`
- 创建：`tests/test_writer_agent_integration.py`

- [ ] **步骤 1：编写目标阶段依赖计划失败测试**

```python
def empty_original_project() -> dict[str, Any]:
    return {
        "id": 17, "revision": 0, "target_episode_count": 2,
        "locked_brief_id": None, "character_batch_id": None,
        "outline_batch_id": None, "blueprints": [], "characters": [],
        "relationships": [], "episodes": [],
    }


def project_with_locked_outline() -> dict[str, Any]:
    project = empty_original_project()
    project.update({
        "revision": 3, "locked_brief_id": 101,
        "character_batch_id": "characters-v1",
        "outline_batch_id": "outlines-v1",
        "blueprints": [{"id": 101, "version": 1, "status": "brief_locked"}],
        "characters": [{"id": 201, "batch_id": "characters-v1", "version": 1,
                        "status": "locked"}],
        "episodes": [
            {"id": 301, "stage": "outline", "episode_number": 1,
             "batch_id": "outlines-v1", "version": 1, "status": "locked"},
            {"id": 302, "stage": "outline", "episode_number": 2,
             "batch_id": "outlines-v1", "version": 1, "status": "locked"},
        ],
    })
    return project


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("brief", ["brief.persist"]),
        ("characters", ["brief.persist", "brief.lock", "characters.generate"]),
        ("outline", [
            "brief.persist", "brief.lock", "characters.generate",
            "characters.lock", "outline.generate",
        ]),
        ("script", [
            "brief.persist", "brief.lock", "characters.generate",
            "characters.lock", "outline.generate", "outline.lock",
            "beats.generate:1", "beats.lock:1", "script.generate:1",
        ]),
    ],
)
def test_plan_fills_only_missing_dependencies(target, expected, orchestrator_context) -> None:
    plan = orchestrator_context.orchestrator.build_plan(
        project=empty_original_project(), target_stage=target,
        episode_start=1, episode_end=1, source_snapshot_id="snapshot-1",
        confirmed_brief_payload={"title": "情绪公司", "logline": "情绪决定工资"},
    )
    assert [step.step_key for step in plan.steps] == expected


def test_plan_reuses_locked_authority(orchestrator_context) -> None:
    plan = orchestrator_context.orchestrator.build_plan(
        project=project_with_locked_outline(), target_stage="script",
        episode_start=1, episode_end=2, source_snapshot_id="snapshot-2",
    )
    assert [step.step_key for step in plan.steps] == [
        "beats.generate:1", "beats.lock:1", "script.generate:1",
        "beats.generate:2", "beats.lock:2", "script.generate:2",
    ]


def test_plan_pauses_for_selection_after_multi_direction_generation(
    orchestrator_context,
) -> None:
    plan = orchestrator_context.orchestrator.build_plan(
        project=empty_original_project(), target_stage="outline",
        episode_start=1, episode_end=1, source_snapshot_id="snapshot-3",
        confirmed_brief_payload=None,
    )
    assert [step.step_key for step in plan.steps] == ["brief.generate"]
    assert plan.requires_selection is True
    assert plan.selection_stage == "brief"
```

再增加 `test_execute_step_rejects_task_assigned_to_another_user`、`test_reworking_locked_outline_calls_create_revision_and_marks_downstream_historical`、`test_locked_resource_is_never_patched_in_place`，分别断言稳定错误码、parent/replaces 引用和仓储 PATCH 调用次数为 0。

- [ ] **步骤 2：运行规划测试确认失败**

运行：

```powershell
uv run pytest tests/test_writer_agent_orchestrator.py -k "plan" -q
```

预期：FAIL，缺少 `build_plan()` 和计划类型。

- [ ] **步骤 3：实现纯函数式依赖规划器**

在 `writer_agent/schema.py` 定义：

```python
class PlanStep(BaseModel):
    step_key: str
    stage: Stage
    kind: Literal["generate", "persist", "lock"]
    episode_number: int | None = None
    depends_on: list[str] = Field(default_factory=list)
    expected_context: dict[str, Any]
    replaces_resource_id: str | None = None
    invalidates_downstream: bool = False

class CreativePlan(BaseModel):
    project_id: int | None
    target_stage: Stage
    episode_start: int | None
    episode_end: int | None
    source_snapshot_id: str
    assumptions: list[str]
    reused_resources: list[dict[str, Any]] = Field(default_factory=list)
    requires_selection: bool = False
    selection_stage: Stage | None = None
    steps: list[PlanStep]
```

`build_plan()` 不执行写操作，只读取项目活动指针和当前兼容资源，按 `brief → characters → outline → beats → script` 生成最短步骤链。Brief、人物、大纲整体；Beats、剧本按集。每步显式写入 `depends_on`，使某集失败不阻止其他无依赖集执行，同时上游失败会阻止所有依赖它的下游步骤。若已有已确认的单张 Brief 卡，使用 `brief.persist` 并可在同一计划中继续 lock；若只有灵感，`brief.generate` 产生 3–5 个候选后计划进入 `requires_selection`，生成新的方向选择卡，用户选择确定 resource_id 前不得自动执行 `brief.lock` 或下游步骤。

`execute_step()` 调用 CreativeApplicationService 的 ensure/persist/create_revision/lock/start_generation；把 `CreativeContextConflict` 映射为不可重试的 `WriterActionError("STALE_PROJECT_CONTEXT", "项目权威版本已变化", retryable=False)`，把任务占用映射为 `TASK_ASSIGNED_TO_OTHER`，把校验失败映射为 `RESULT_VALIDATION_FAILED`，不把原始仓储异常或路径信息直接返回前端。

- [ ] **步骤 4：落实权威版本和新版本规则**

计划的 `expected_context` 包含项目 revision、`locked_brief_id`、`character_batch_id`、`outline_batch_id`，以及对应集 Beats ID/版本/指纹。

已有兼容 locked 资源写入 `CreativePlan.reused_resources`，不生成可执行步骤。用户明确重做 locked 上游时，通过 `CreativeApplicationService.create_revision()` 创建新版本，计划卡必须携带 `replaces_resource_id` 和 `invalidates_downstream=True`；不允许 PATCH locked 资源。

依赖锁定可以包含在一次已确认的创作计划中，但每个 lock 都必须作为可见步骤写入卡片与审计。

- [ ] **步骤 5：运行规划和工作流依赖回归**

运行：

```powershell
uv run pytest tests/test_writer_agent_orchestrator.py tests/test_writer_agent_integration.py -q
```

预期：全部 PASS。

- [ ] **步骤 6：提交目标阶段规划器**

```powershell
git add writer_agent/schema.py writer_agent/orchestrator.py creative/application_service.py tests/test_writer_agent_orchestrator.py tests/test_writer_agent_integration.py
git commit -m "feat: 规划对话目标阶段创作链路"
```

### 任务 8：实现轻量异步执行、部分失败和恢复

**文件：**
- 创建：`writer_agent/runner.py`
- 修改：`writer_agent/orchestrator.py`
- 修改：`writer_agent/store.py`
- 修改：`runtime/job_manager.py:125-172,295-436`
- 修改：`agents/storage_agent.py:27-104`
- 修改：`WebGUI.py:510-620`
- 创建：`tests/test_writer_agent_runner.py`
- 测试：`tests/test_job_manager.py`

- [ ] **步骤 1：编写分步执行、失败集重试和恢复测试**

```python
def seed_runner_action(
    store: WriterAgentStore, session: dict[str, Any], actor: Principal,
    *, stage: str, episodes: range, project_revision: int,
) -> dict[str, Any]:
    action = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="creative.run_plan", project_id=17,
        idempotency_key=f"{stage}:{project_revision}:{episodes.start}:{episodes.stop}",
        request_hash=f"{stage}:{project_revision}:{episodes.start}:{episodes.stop}",
        payload={"target_stage": stage},
        expected_context={"project_revision": project_revision},
        source_snapshot_id="snapshot-1",
    )
    store.claim_pending_action(action["id"], expected_revision=1)
    for episode in episodes:
        store.upsert_step(
            action_id=action["id"], step_key=f"{stage}.generate:{episode}",
            stage=stage, episode_start=episode, episode_end=episode,
            status="pending", operation_id=f"{action['id']}:{stage}.generate:{episode}",
            depends_on=[], attempt=1,
        )
    return store.require_action(action["id"], actor.user_id)


@pytest.fixture
def runner_context(tmp_path: Path) -> SimpleNamespace:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    actor = Principal(
        user_id="writer-1", username="writer", display_name="编剧",
        role_codes=("screenwriter",), permissions=frozenset({"project.create"}),
        data_scope="project", session_id="writer-session",
    )
    session = store.create(actor.user_id, "执行测试", "生成剧本")
    context = SimpleNamespace(
        store=store, actor=actor, session=session, project_revision=4,
        fail_once=set(), failed_once=set(), executed=[], creative_calls=[],
    )
    orchestrator = MagicMock()

    def assert_expected(action: dict[str, Any], _actor: Principal) -> None:
        expected = int(action["expected_context"]["project_revision"])
        if expected != context.project_revision:
            raise WriterActionError(
                "STALE_PROJECT_CONTEXT", "项目上下文已变化", retryable=False
            )

    def execute_step(
        _action: dict[str, Any], step: dict[str, Any], _actor: Principal,
        *, operation_id: str,
    ) -> dict[str, Any]:
        key = step["step_key"]
        context.executed.append(key)
        if key in context.fail_once and key not in context.failed_once:
            context.failed_once.add(key)
            raise WriterActionError("GENERATION_FAILED", key, retryable=True)
        context.creative_calls.append({"step_key": key, "operation_id": operation_id})
        return {"resource_refs": [{"resource_id": key, "status": "draft"}]}

    orchestrator.assert_expected_context.side_effect = assert_expected
    orchestrator.execute_step.side_effect = execute_step
    jobs = MagicMock()
    runner = WriterActionRunner(
        store=store, orchestrator=orchestrator, job_manager=jobs,
        principal_loader=lambda user_id: actor if user_id == actor.user_id else None,
    )
    context.orchestrator = orchestrator
    context.jobs = jobs
    context.runner = runner
    return context


def test_runner_keeps_successful_episodes_and_retries_only_failures(runner_context) -> None:
    action = seed_runner_action(
        runner_context.store, runner_context.session, runner_context.actor,
        stage="script", episodes=range(1, 4), project_revision=4,
    )
    runner_context.fail_once.add("script.generate:2")

    runner_context.runner.run(action["id"], runner_context.actor)
    failed = runner_context.store.require_action(action["id"], runner_context.actor.user_id)
    assert failed["status"] == "failed"
    assert failed["result"]["partial"] is True
    assert {step["step_key"] for step in runner_context.store.list_steps(action["id"])
            if step["status"] == "succeeded"} == {
        "script.generate:1", "script.generate:3"
    }

    retry = runner_context.runner.retry_failed(action["id"], runner_context.actor)
    runner_context.runner.run(retry["id"], runner_context.actor)
    assert [step["step_key"] for step in runner_context.store.list_steps(retry["id"])] == [
        "script.generate:2"
    ]


def test_runner_marks_stale_upstream_without_writing(runner_context) -> None:
    action = seed_runner_action(
        runner_context.store, runner_context.session, runner_context.actor,
        stage="outline", episodes=range(1, 2), project_revision=4,
    )
    runner_context.project_revision = 5
    runner_context.runner.run(action["id"], runner_context.actor)
    result = runner_context.store.require_action(action["id"], runner_context.actor.user_id)
    assert result["error_code"] == "STALE_PROJECT_CONTEXT"
    assert runner_context.creative_calls == []


def test_series_generation_is_partitioned_into_ten_episode_batches() -> None:
    assert partition_episode_range(1, 23, batch_size=10) == [
        (1, 10), (11, 20), (21, 23)
    ]


def test_retry_preserves_failed_step_operation_id(runner_context) -> None:
    action = seed_runner_action(
        runner_context.store, runner_context.session, runner_context.actor,
        stage="script", episodes=range(1, 2), project_revision=4,
    )
    original = runner_context.store.list_steps(action["id"])[0]
    runner_context.fail_once.add(original["step_key"])
    runner_context.runner.run(action["id"], runner_context.actor)
    retry = runner_context.runner.retry_failed(action["id"], runner_context.actor)
    copied = runner_context.store.list_steps(retry["id"])[0]
    assert copied["operation_id"] == original["operation_id"]
```

在 `tests/test_job_manager.py` 增加 `test_agent_job_store_migrates_operation_id_column`、`test_get_or_create_job_reuses_operation_id_after_state_reload`、`test_creative_series_conflicts_with_overlapping_episode`、`test_creative_non_overlapping_ranges_can_run`、`test_worker_action_names_share_normalized_scope`；通过伪 launcher 和状态事件推进，不启动真实 Worker。

- [ ] **步骤 2：运行 runner 测试确认失败**

运行：

```powershell
uv run pytest tests/test_writer_agent_runner.py -q
```

预期：FAIL，runner 模块尚不存在。

- [ ] **步骤 3：实现持久化步骤执行器**

```python
class WriterActionRunner:
    def __init__(
        self,
        *,
        store: WriterAgentStore,
        orchestrator: WriterProjectOrchestrator,
        job_manager: JobManager,
        principal_loader: Callable[[str], Principal | None],
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.job_manager = job_manager
        self.principal_loader = principal_loader

    def enqueue(self, action_id: str, actor: Principal) -> None:
        thread = threading.Thread(target=self.run, args=(action_id, actor), daemon=True)
        thread.start()

    def run(self, action_id: str, actor: Principal) -> None:
        action = self.store.require_action(action_id, actor.user_id)
        self.store.start_action(action_id)
        failures: list[WriterActionError] = []
        succeeded = 0
        while True:
            runnable = self.store.list_runnable_steps(action_id)
            if not runnable:
                break
            for step in runnable:
                try:
                    self.orchestrator.assert_expected_context(action, actor)
                    result = self.orchestrator.execute_step(
                        action, step, actor,
                        operation_id=step["operation_id"],
                    )
                    self.store.finish_step(action_id, step["step_key"], result)
                    if result.get("authority_revision") is not None:
                        self.store.advance_expected_context(
                            action_id,
                            project_revision=int(result["authority_revision"]),
                            authority_refs=result.get("authority_refs", {}),
                        )
                        action = self.store.require_action(action_id, actor.user_id)
                    succeeded += 1
                except WriterActionError as exc:
                    self.store.fail_step(
                        action_id, step["step_key"], code=exc.code,
                        message=str(exc), retryable=exc.retryable,
                    )
                    failures.append(exc)
        if failures:
            first = failures[0]
            self.store.fail_action(
                action_id, code=first.code, message=str(first),
                retryable=any(item.retryable for item in failures),
                result={"partial": succeeded > 0},
            )
        else:
            self.store.finish_action(action_id, status="succeeded")

    def reconcile(self, action_id: str, actor: Principal) -> dict[str, Any]:
        self.store.require_action(action_id, actor.user_id)
        return self.store.refresh_job_steps(action_id, self.job_manager)

    def retry_failed(self, action_id: str, actor: Principal) -> dict[str, Any]:
        source = self.store.require_retryable_action(action_id, actor.user_id)
        return self.store.create_retry_action(source, actor.user_id)

    def cancel(self, action_id: str, actor: Principal) -> dict[str, Any]:
        action = self.store.require_action(action_id, actor.user_id)
        active_jobs = self.store.active_job_ids(action_id)
        for job_id in active_jobs:
            self.job_manager.stop(job_id)
        if active_jobs:
            return self.store.set_action_phase(action["id"], phase="cancelling")
        return self.store.finish_action(action["id"], status="cancelled")

    def resume_active_actions(self) -> None:
        for action in self.store.list_active_actions():
            actor = self.principal_loader(action["actor_user_id"])
            if actor is not None:
                self.enqueue(action["id"], actor)
```

`enqueue()` 创建 daemon 线程执行 action；动作和步骤状态先持久化再产生副作用。operation_id 在步骤首次落库时生成并保持不可变；重试动作复制失败步骤时必须保留原 operation_id 和 root_action_id，不能按新的 retry action ID 重算。生成步骤调用 JobManager，保存 job_id 并轮询终态；进程重启后 `resume_active_actions()` 从账本和 JobManager 状态恢复。

`start_action()` 只允许 queued → running。循环按 `depends_on` 选择可运行步骤；单集步骤失败时继续执行其他无依赖单集，依赖失败步骤的后续步骤标为 blocked 并保留在失败范围。生成步骤启动 Job 后立即保存 job_id；`reconcile()` 读取 JobManager 状态并把 succeeded/failed/cancelled 映射到步骤。取消有活动 Job 时先调用 `JobManager.stop()` 并把 phase 设为 cancelling，只有对账确认全部 Job 进入 cancelled/failed/succeeded 后才把动作置为 cancelled。runner 线程使用 `threading.Event.wait(0.2)` 做可中断状态轮询，测试通过伪 Job 状态推进，不使用固定 sleep。

每个成功切换 Brief/人物/大纲活动指针的本动作步骤返回 `authority_revision` 和最新 authority refs；runner 原子调用 `advance_expected_context()` 后重新加载 action，后续步骤据此校验。外部变化仍导致 `STALE_PROJECT_CONTEXT`，本动作自己的已审计指针推进不会误判为过期。

- [ ] **步骤 4：修正全剧与单集任务冲突范围**

在 JobManager 增加纯函数 `creative_job_scope(job)`，先把 Worker action 归一为 `brief|characters|outline|beats|script`（`blueprints → brief`、`outlines → outline`）；整体 Brief/人物/大纲范围为 `None`，单集范围为 `[n,n]`，series 使用明确的 episode_start/episode_end，缺范围的 series 请求直接拒绝。冲突判断按相同 project_id、相同 normalized stage、区间相交判定；同项目同阶段边界相接、不同项目、不同阶段或不相交单集按矩阵测试决定并发，其中 `[1,10]` 与 `[10,20]` 视为相交。

JobManager 同时增加：

```python
def get_or_create_job(
    self, operation_id: str, data: dict[str, Any], *,
    created_by_user_id: str, workflow_task_id: str, project_id: str | int,
) -> JobState:
```

`agents/storage_agent.py` 为 `agent_jobs` 安全补充 nullable `operation_id` 并建立唯一索引。`get_or_create_job()` 在 `BEGIN IMMEDIATE` 中调用 storage 的 `reserve_job_operation()`：已有 operation_id（包括终态）读取并返回，只有成功插入 pending 保留行的进程能启动 Worker；启动失败把同一 job 标记 failed，不删除保留行。`CreativeApplicationService.start_generation()` 在本任务改用该方法，runner 在任何重启或重试时先对账旧 job_id / operation_id，再决定是否启动，封住“Job 已创建但 job_id 尚未写入动作表”的重复生成窗口。

在 `writer_agent/runner.py` 实现纯函数 `partition_episode_range(start, end, batch_size=10)`；Beats/剧本全剧计划用它创建连续、无重叠的 10 集批次，每集仍保存独立步骤和 operation_id，批次只是 Job 调度单位。

错误保存稳定 `error_code`、`retryable`、`conflicting_action_id`。动作对外状态只使用 queued/running/succeeded/failed/cancelled；部分完成保存在 `result.partial` 和步骤状态。

锁定步骤不宣称由 creative operation 表跨 SQLite/PostgreSQL 提供原子 exactly-once。重放先检查 domain resource 与 workflow task：两者均完成时重建成功结果；仅 domain 已锁定时调用 `TransitionService.complete_creative_without_review()` 的既有 partial-state recovery；两者均未完成时正常执行。增加测试覆盖“domain lock 后 workflow 写失败，重启后只补 workflow 且不创建新版本”。

- [ ] **步骤 5：运行 runner 和 JobManager 回归**

运行：

```powershell
uv run pytest tests/test_writer_agent_runner.py tests/test_job_manager.py -q
```

预期：全部 PASS，无基于固定 sleep 的测试。

- [ ] **步骤 6：提交异步执行器**

```powershell
git add writer_agent/runner.py writer_agent/orchestrator.py writer_agent/store.py runtime/job_manager.py agents/storage_agent.py WebGUI.py tests/test_writer_agent_runner.py tests/test_job_manager.py
git commit -m "feat: 执行并恢复对话创作计划"
```

### 任务 9：开放 Writer Agent 动作 API 并统一按钮与自然语言确认

**文件：**
- 修改：`writer_agent/api.py:270-405`
- 修改：`writer_agent/action_service.py`
- 修改：`writer_agent/runner.py`
- 修改：`WebGUI.py:608-612`
- 修改：`web/page_access_catalog.py:117-124`
- 修改：`tests/test_writer_agent_api.py`
- 修改：`tests/test_page_authorization.py`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写动作端点和自然语言同路径测试**

```python
@pytest.fixture
def writer_api_context(tmp_path: Path) -> SimpleNamespace:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    commands: list[ActionCommand] = []
    action_service = MagicMock()
    action_service.decide.side_effect = lambda command: (
        commands.append(command) or {"id": command.action_id, "status": "queued"}
    )

    def principal(user_id: str) -> Principal:
        return Principal(
            user_id=user_id, username=user_id, display_name=user_id,
            role_codes=("screenwriter",),
            permissions=frozenset({"script.review.generate", "project.create"}),
            data_scope="project", session_id=f"session-{user_id}",
        )

    def client_for(user_id: str) -> TestClient:
        app = FastAPI()
        app.dependency_overrides[current_principal] = lambda: principal(user_id)
        app.include_router(build_writer_agent_router(
            tmp_path, store=store, action_service=action_service
        ))
        return TestClient(app)

    def seed_pending(session_id: int, actor_user_id: str) -> dict[str, Any]:
        return store.create_action(
            session_id=session_id, actor_user_id=actor_user_id,
            action_type="creative.run_plan", project_id=17,
            idempotency_key=f"pending:{session_id}", request_hash=f"hash:{session_id}",
            payload={"target_stage": "script", "episode_start": 1, "episode_end": 5},
            expected_context={"session_revision": 0, "project_revision": 1},
            source_snapshot_id="snapshot-1",
        )

    return SimpleNamespace(
        store=store, commands=commands, writer=client_for("writer-1"),
        other=client_for("writer-2"), seed_pending=seed_pending,
    )


def test_button_and_natural_language_use_the_same_action_command(
    writer_api_context,
) -> None:
    button_session = writer_api_context.store.create(
        "writer-1", "按钮确认", "生成第1-5集剧本"
    )
    button_action = writer_api_context.seed_pending(button_session["id"], "writer-1")

    button = writer_api_context.writer.post(
        f"/api/writer-agent/sessions/{button_session['id']}/actions/{button_action['id']}/decision",
        json={"decision": "confirm", "expected_revision": 1, "client_request_id": "button-1"},
    )
    natural_session = writer_api_context.store.create(
        "writer-1", "自然语言确认", "生成第1-5集剧本"
    )
    natural_action = writer_api_context.seed_pending(natural_session["id"], "writer-1")
    natural = writer_api_context.writer.post(
        f"/api/writer-agent/sessions/{natural_session['id']}/messages",
        json={"message": "开始生成"},
    )

    assert button.status_code == 202
    assert natural.status_code == 200
    assert all(isinstance(item, ActionCommand) for item in writer_api_context.commands)
    assert writer_api_context.commands[0].action_id == button_action["id"]
    assert writer_api_context.commands[1].action_id == natural_action["id"]
    assert [item.decision for item in writer_api_context.commands] == ["confirm", "confirm"]


def test_action_decision_rechecks_session_owner(writer_api_context) -> None:
    session = writer_api_context.store.create("writer-1", "私有动作", "生成剧本")
    action = writer_api_context.seed_pending(session["id"], "writer-1")
    response = writer_api_context.other.post(
        f"/api/writer-agent/sessions/{session['id']}/actions/{action['id']}/decision",
        json={"decision": "confirm", "expected_revision": 1, "client_request_id": "attack"},
    )
    assert response.status_code == 404
```

再增加 `test_repeated_decision_client_request_returns_same_action_and_enqueues_once` 和 `test_retry_client_request_is_idempotent`；两次请求响应的 action ID 相同，Recording Runner 的 enqueue/create_retry 调用次数均为 1。

- [ ] **步骤 2：运行 API 测试确认路由不存在**

运行：

```powershell
uv run pytest tests/test_writer_agent_api.py tests/test_page_authorization.py -q
```

预期：FAIL，动作 decision/read 路由未登记。

- [ ] **步骤 3：增加动作读取和决策路由**

新增：

```http
GET  /api/writer-agent/sessions/{session_id}/actions/{action_id}
POST /api/writer-agent/sessions/{session_id}/actions/{action_id}/decision
POST /api/writer-agent/sessions/{session_id}/actions/{action_id}/retry
```

Pydantic 请求：

```python
class ActionDecisionRequest(BaseModel):
    decision: Literal["confirm", "cancel"]
    expected_revision: int = Field(ge=1)
    client_request_id: str = Field(min_length=1, max_length=200)


class ActionRetryRequest(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=200)
```

确认使用 `store.claim_pending_action()` 后调用 runner；异步返回 202。取消使用相同 ActionCommand。retry 路由只接受 failed 且 retryable 的动作，调用 `runner.retry_failed()` 创建新动作并返回 202；重复 client_request_id 返回同一 retry action。错误码映射为 401/403/404/409/422，基础设施失败持久化到 action 后返回可查询状态。

- [ ] **步骤 4：将自然语言确认接到同一 ActionCommand**

`POST /messages` 先检查当前 pending 动作。匹配为确认/取消时调用 `ActionService.decide()`，而不是在 chat 路由复制执行逻辑；匹配为修订时生成新 revision；普通创作讨论和评审继续调用 LLM。

按钮路由和消息路由都必须实例化 `ActionCommand` 后调用同一个 `ActionService.decide(command)`。自然语言命令的 `client_request_id` 使用稳定 message_id；同一消息重放产生同一个 key。ActionService 先校验 session/action owner、revision 和 pending 状态，再 claim 和 enqueue；不能因为管理员可读会话就跳过项目写权限。

更新 `register_writer_agent_routes()` 接收并注入 store、creative service、permission service、workflow repository、job manager 和 audit service。ActionService 在 claim、cancel、执行成功/失败和重试时调用 `AuditService.record()`，after 只保存动作 ID、项目、阶段、资源/Job 引用和稳定错误码，不保存材料正文、Cookie 或模型凭据。登记 `web/page_access_catalog.py` 中的新 API 路径。

- [ ] **步骤 5：运行 Writer Agent API 和授权回归**

运行：

```powershell
uv run pytest tests/test_writer_agent_api.py tests/test_page_authorization.py tests/test_webgui_ui_changes.py -q
```

预期：全部 PASS。

- [ ] **步骤 6：提交动作 API**

```powershell
git add writer_agent/api.py writer_agent/action_service.py writer_agent/runner.py WebGUI.py web/page_access_catalog.py tests/test_writer_agent_api.py tests/test_page_authorization.py tests/test_webgui_ui_changes.py
git commit -m "feat: 开放编剧大师创作动作接口"
```

### 任务 10：实现项目上下文、成果卡和独立进度轮询界面

**文件：**
- 修改：`templates/review/writer_agent.html:5-74`
- 修改：`static/js/review/writer_agent.js:4-307`
- 创建：`tests/test_writer_agent_browser.py`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写浏览器失败测试**

```python
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        current = browser.new_page(viewport={"width": 1280, "height": 900})
        current.set_default_timeout(2_000)
        yield current
        browser.close()


def mount_writer_agent(page: Page, *, session: dict[str, Any]) -> None:
    page.set_content(
        '<main id="writer-agent-workbench">'
        '<section id="writer-agent-project-context">'
        '<button id="writer-agent-create-project" type="button">创建原创项目</button>'
        '<button id="writer-agent-link-project" type="button">关联已有项目</button>'
        '<div id="writer-agent-project-picker" hidden></div></section>'
        '<section id="writer-agent-messages"></section>'
        '<form id="writer-agent-message-form"><textarea id="writer-agent-message"></textarea>'
        '<button type="submit">发送</button></form></main>'
    )
    page.add_script_tag(content="""
      window.__writerActionRequests = [];
      window.aiworkerApi = {
        async request(url, options = {}) {
          window.__writerActionRequests.push({
            url, method: options.method || 'GET',
            body: JSON.parse(options.body || '{}')
          });
          return {id: url.split('/').at(-2), status: 'queued'};
        }
      };
    """)
    page.add_script_tag(path=ROOT / "static/js/review/writer_agent.js")
    page.evaluate("session => window.writerAgentWorkbench.mount(session)", session)


def test_writer_agent_renders_project_context_and_confirms_action(page) -> None:
    mount_writer_agent(page, session={
        "id": 1,
        "project_id": 9,
        "project": {"id": 9, "name": "情绪公司", "current_stage": "outline"},
        "messages": [{"message_id": "m1", "role": "assistant", "content": "计划如下", "action_ids": ["a1"]}],
        "actions": [{
            "id": "a1", "action_type": "creative.run_plan", "status": "pending",
            "revision": 2, "title": "生成第1-5集剧本", "summary": "复用已锁定大纲",
            "payload": {"target_stage": "script", "episode_start": 1, "episode_end": 5},
        }],
    })

    assert page.locator("#writer-agent-project-context").inner_text().startswith("情绪公司")
    page.get_by_role("button", name="确认生成").click()
    request = page.evaluate("window.__writerActionRequests[0]")
    assert request["url"].endswith("/actions/a1/decision")
    assert request["body"]["expected_revision"] == 2
```

再增加：

- 未关联时显示“创建原创项目”和“关联已有项目”，关联候选只来自当前账户可读项目。
- 选择候选后只生成 `project.link` 待确认卡，不直接修改 session.project_id。
- 多个卡片时各自按钮只禁用本卡片。
- queued/running 卡片独立轮询，输入框仍可发送消息。
- partial failed 卡只提交“重试失败项”。
- 模型文本按 `textContent` 渲染，`<img src=x onerror=alert(1)>` 不执行。
- 390px 宽度无横向溢出。

- [ ] **步骤 2：运行浏览器测试确认失败**

运行：

```powershell
uv run pytest tests/test_writer_agent_browser.py tests/test_webgui_ui_changes.py -q
```

预期：FAIL，模板仍显示“独立诊断工作台”，没有项目上下文和动作卡。

- [ ] **步骤 3：修改模板结构和文案**

将副标题改为“对话评审、原创立项与项目创作”。增加：

```html
<section id="writer-agent-project-context" aria-label="当前创作项目">
  <button id="writer-agent-create-project" type="button">创建原创项目</button>
  <button id="writer-agent-link-project" type="button">关联已有项目</button>
  <div id="writer-agent-project-picker" hidden></div>
</section>
```

未关联时显示两个按钮：创建按钮向当前会话发送“创建原创项目”引导消息；关联按钮请求 `GET /api/creative/projects?participating_only=true` 并安全渲染候选。点击候选只向消息接口提交明确 project_id，由后端创建 `project.link` 卡，确认后才切换。已关联时显示项目名、创作模式、当前阶段和打开项目链接；切换项目仍走相同 link 卡。卡片样式覆盖方向、Brief、计划、进度、成果和冲突；移动端改为单栏。保留评审报告侧栏和上传区。

- [ ] **步骤 4：实现安全卡片渲染和单动作状态**

在 JS 中新增纯 DOM 方法：

```javascript
const actionPollers = new Map();

function renderActionCard(action) {
  const card = document.createElement('article');
  card.className = `writer-action-card status-${action.status}`;
  card.dataset.actionId = action.id;
  const title = document.createElement('h3');
  title.textContent = action.title || action.action_type;
  const summary = document.createElement('p');
  summary.textContent = action.summary || '';
  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.textContent = action.action_type === 'project.link' ? '确认关联' : '确认生成';
  confirm.disabled = action.status !== 'pending';
  confirm.addEventListener('click', () => decideAction(action, 'confirm', card));
  card.append(title, summary, confirm);
  if (action.status === 'failed' && action.retryable) {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = action.result?.partial ? '仅重试失败项' : '重试';
    retry.addEventListener('click', () => retryAction(action, card));
    card.append(retry);
  }
  return card;
}

async function decideAction(action, decision, card) {
  const buttons = card.querySelectorAll('button');
  buttons.forEach(button => { button.disabled = true; });
  let updated = null;
  try {
    updated = await window.aiworkerApi.request(
      `/api/writer-agent/sessions/${state.session.id}/actions/${action.id}/decision`,
      {method: 'POST', body: JSON.stringify({
        decision, expected_revision: action.revision,
        client_request_id: crypto.randomUUID()
      })}
    );
    replaceAction(updated);
    if (['queued', 'running'].includes(updated.status)) startActionPolling(action.id);
  } finally {
    if (!updated || updated.status === 'pending') {
      buttons.forEach(button => { button.disabled = false; });
    }
  }
}

function replaceAction(updated) {
  const index = state.session.actions.findIndex(item => item.id === updated.id);
  if (index >= 0) {
    state.session.actions[index] = Object.assign({}, state.session.actions[index], updated);
  }
  renderSession(state.session);
}

async function refreshAction(actionId) {
  const updated = await window.aiworkerApi.request(
    `/api/writer-agent/sessions/${state.session.id}/actions/${actionId}`
  );
  replaceAction(updated);
  if (!['queued', 'running'].includes(updated.status)) stopActionPolling(actionId);
}

async function retryAction(action, card) {
  const button = card.querySelector('button:last-child');
  button.disabled = true;
  try {
    const retry = await window.aiworkerApi.request(
      `/api/writer-agent/sessions/${state.session.id}/actions/${action.id}/retry`,
      {method: 'POST', body: JSON.stringify({client_request_id: crypto.randomUUID()})}
    );
    state.session.actions.push(retry);
    renderSession(state.session);
    startActionPolling(retry.id);
  } finally {
    button.disabled = false;
  }
}

function startActionPolling(actionId) {
  if (actionPollers.has(actionId)) return;
  const timer = window.setInterval(() => refreshAction(actionId), 1000);
  actionPollers.set(actionId, timer);
}

function stopActionPolling(actionId) {
  const timer = actionPollers.get(actionId);
  if (timer !== undefined) window.clearInterval(timer);
  actionPollers.delete(actionId);
}
```

模块导出 `window.writerAgentWorkbench.mount(session)` 供浏览器测试使用；生产初始化也调用同一 mount，不复制渲染路径。移除长任务期间的全局 `isBusy`；只在当前消息请求期间禁用发送按钮。动作入队后立即恢复输入，queued/running 卡片单独轮询。成功卡显示按目标阶段计算的项目 URL；冲突卡显示重新生成计划按钮。所有模型字段只写 `textContent`，不得传入 `innerHTML`。

- [ ] **步骤 5：运行桌面、移动端和旧评审界面回归**

运行：

```powershell
uv run pytest tests/test_writer_agent_browser.py tests/test_webgui_ui_changes.py tests/test_writer_agent_api.py -q
```

预期：全部 PASS。

- [ ] **步骤 6：提交对话界面**

```powershell
git add templates/review/writer_agent.html static/js/review/writer_agent.js tests/test_writer_agent_browser.py tests/test_webgui_ui_changes.py tests/test_writer_agent_api.py
git commit -m "feat: 展示编剧大师项目创作卡片"
```

### 任务 11：完成端到端验收、迁移回归和交付检查

**文件：**
- 修改：`tests/test_original_workflow_integration.py`
- 修改：`tests/test_writer_agent_integration.py`
- 创建：`tests/writer_agent_harness.py`
- 修改：`tests/test_original_project_browser.py`
- 修改：`tests/test_screenwriter_dashboard_browser.py`
- 修改：`tests/test_writer_agent_browser.py`
- 修改：`tests/test_page_authorization.py`
- 修改：`docs/superpowers/specs/2026-08-20-writer-agent-project-orchestration-design.md`（仅当实现发现必须澄清的契约时）

- [ ] **步骤 1：建立可执行集成夹具并增加四类起点及两个核心链路测试**

当前 `tests/conftest.py` 明确忽略了退役审批流的 `test_creative_workflow_integration.py`；本功能不得把验收写进该文件。把测试支持集中在 `tests/writer_agent_harness.py`，定义 `WriterAgentE2EHarness` 和同名 fixture：

```python
@pytest.fixture
def e2e_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[WriterAgentE2EHarness]:
    harness = WriterAgentE2EHarness.create(tmp_path, monkeypatch)
    yield harness
    harness.close()
```

`WriterAgentE2EHarness.create()` 必须实际构建隔离的 IdentityRepository、WorkflowRepository、CreativeRepository、CreativeWorkflowAdapter、creative-aware TransitionService、OriginalProjectService、CreativeApplicationService、WriterAgentStore、WriterProjectOrchestrator、WriterActionRunner、FastAPI 和 TestClient；登录用户只授予 `screenwriter` 与 `project.create` 所需权限。注入的确定性 Provider 按输入返回 5 个有明确差异维度的方向、完整 Brief、人物、全集大纲、逐集 Beats/剧本；测试 JobManager 同步发出与真实 Job 相同的 running/succeeded/failed 状态，第二集可配置首轮失败。Harness 的全部公开方法在该文件实现为真实 HTTP 或 repository 调用，签名固定为：

```python
class WriterAgentE2EHarness:
    creative_repository: CreativeRepository
    creative_service: CreativeApplicationService
    original_project_service: OriginalProjectService
    store: WriterAgentStore
    actor: Principal
    provider: DeterministicCreativeProvider
    job_manager: DeterministicJobManager
    workflow_repository: WorkflowRepository
    database: SystemDatabase

    def create_session(self, content: str) -> dict[str, Any]:
        return self._ok(self.client.post(
            "/api/writer-agent/sessions", json={"title": "集成测试", "content": content}
        ))

    def upload_material(self, session: dict[str, Any], name: str, content: str) -> dict[str, Any]:
        return self._ok(self.client.post(
            f"/api/writer-agent/sessions/{session['id']}/materials/file",
            content=content.encode("utf-8"), headers={"X-Writer-Filename": quote(name)},
        ))

    def chat(self, session: dict[str, Any], message: str) -> dict[str, Any]:
        return self._ok(self.client.post(
            f"/api/writer-agent/sessions/{session['id']}/messages",
            json={"message": message},
        ))

    def confirm_and_wait(self, response: dict[str, Any]) -> dict[str, Any]:
        action = response["actions"][-1]
        self._accepted(self.client.post(
            f"/api/writer-agent/sessions/{action['session_id']}/actions/{action['id']}/decision",
            json={"decision": "confirm", "expected_revision": action["revision"],
                  "client_request_id": f"confirm:{action['id']}"},
        ))
        return self._terminal(action["session_id"], action["id"])

    def retry_and_wait(self, session_id: int, action_id: str) -> dict[str, Any]:
        retry = self._accepted(self.client.post(
            f"/api/writer-agent/sessions/{session_id}/actions/{action_id}/retry",
            json={"client_request_id": f"retry:{action_id}"},
        ))
        return self._terminal(session_id, retry["id"])

    def project_with_locked_outline_and_beats(self, episodes: range) -> dict[str, Any]:
        count = len(list(episodes))
        project, _ = self.original_project_service.create(
            OriginalProjectCreateRequest(
                seed_text="情绪公司", name="情绪公司", genre="都市奇幻",
                target_platform="hg", target_audience="18-35岁",
                target_episode_count=count, episode_duration_seconds=120,
                cost_level="low", content_boundaries=[], reference_refs=[],
                creation_request_id=f"seed:{uuid.uuid4().hex}",
            ),
            self.actor, can_read_reference=lambda _kind, _id: True,
        )
        for stage, payload, episode_number in [
            ("brief", self.provider.brief(), None),
            ("characters", self.provider.characters(), None),
            ("outline", self.provider.outline(count), None),
        ]:
            task = self.creative_service.ensure_stage_task(project["id"], stage, self.actor)
            resource = self.creative_service.persist_draft(
                project_id=project["id"], stage=stage, payload=payload,
                workflow_task_id=task["id"], operation_id=f"seed:{stage}",
                actor=self.actor, episode_number=episode_number,
            )
            self.creative_service.lock_resource(
                project_id=project["id"], stage=stage,
                resource_id=resource["resource_id"], workflow_task_id=task["id"],
                operation_id=f"seed:{stage}:lock", actor=self.actor,
            )
        beats_task = self.creative_service.ensure_stage_task(
            project["id"], "beats", self.actor
        )
        for episode in episodes:
            resource = self.creative_service.persist_draft(
                project_id=project["id"], stage="beats",
                payload=self.provider.beats(episode), workflow_task_id=beats_task["id"],
                operation_id=f"seed:beats:{episode}", actor=self.actor,
                episode_number=episode,
            )
            self.creative_service.lock_resource(
                project_id=project["id"], stage="beats",
                resource_id=resource["resource_id"], workflow_task_id=beats_task["id"],
                operation_id=f"seed:beats:{episode}:lock", actor=self.actor,
            )
        return self.creative_repository.get_project(project["id"])

    def link_project(self, session: dict[str, Any], project_id: int) -> dict[str, Any]:
        return self.confirm_and_wait(self.chat(session, f"关联已有项目 {project_id}"))

    def fail_script_episode_once(self, episode_number: int) -> None:
        self.job_manager.fail_once(f"script.generate:{episode_number}")

    def script_versions(self, project_id: int) -> dict[int, int]:
        project = self.creative_repository.get_project(project_id)
        episode_numbers = sorted({
            int(item["episode_number"]) for item in project["episodes"]
            if item["stage"] == "script"
        })
        return {
            episode: len(self.creative_repository.list_episode_versions(
                project_id, "script", episode
            ))
            for episode in episode_numbers
        }

    def seed_readable_topic_reference(self, title: str) -> dict[str, Any]:
        return self.creative_repository.upsert_topics([{
            "platform": "hg", "job_id": f"ref:{uuid.uuid4().hex}",
            "source_path": "integration.json", "captured_at": "2026-08-20",
            "title": title, "description": "", "category": "都市",
            "rank_type": "热播榜", "score": 90, "confidence": "high",
            "score_details": {}, "recommendation": "推荐", "evidence": [],
        }])[0]

    def source_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return self.store.require_source_snapshot(snapshot_id)

    @staticmethod
    def _ok(response: Response) -> dict[str, Any]:
        assert response.status_code in {200, 201}, response.text
        return response.json()

    @staticmethod
    def _accepted(response: Response) -> dict[str, Any]:
        assert response.status_code in {200, 202}, response.text
        return response.json()

    def _terminal(self, session_id: int, action_id: str) -> dict[str, Any]:
        for _attempt in range(100):
            response = self.client.get(
                f"/api/writer-agent/sessions/{session_id}/actions/{action_id}"
            )
            action = self._ok(response)
            if action["status"] in {"succeeded", "failed", "cancelled"}:
                return action
            self.job_manager.advance()
        raise AssertionError({
            "action": action,
            "steps": self.store.list_steps(action_id),
        })

    def close(self) -> None:
        for project in self.creative_repository.list_projects_for_bootstrap(
            self.actor.user_id
        ):
            self.workflow_repository.delete_project_data(str(project["id"]))
        self.client.close()
        self.database.dispose()
```

`_terminal()` 不用 `sleep`；同步测试 JobManager 每次 GET 推进一步状态，最多 100 次后立即失败并输出动作/步骤状态。`project_with_locked_outline_and_beats()`、`link_project()`、`fail_script_episode_once()` 和 `script_versions()` 也在 Harness 中实现：前者用真实仓储写入并锁定完整上游，后两者分别走 link 动作 API 与测试 JobManager 开关，版本读取使用 Task 2 的 `list_episode_versions()`。`close()` 逐项目删除 workflow 测试数据、关闭 TestClient 并 dispose 数据库。

场景一：一句话灵感到全剧大纲。

```python
def test_conversation_creates_project_and_generates_twenty_episode_outline(e2e_context) -> None:
    session = e2e_context.create_session("未来公司按照员工情绪发工资")
    directions = e2e_context.chat(session, "给我5个不同方向")
    direction_card = directions["messages"][-1]["cards"][0]
    assert len(direction_card["items"]) == 5
    assert all(
        len(item["difference_dimensions"]) >= 2
        for item in direction_card["items"]
    )
    action = e2e_context.chat(session, "采用第2个，创建20集项目并生成全剧大纲")
    completed = e2e_context.confirm_and_wait(action)
    project = e2e_context.creative_repository.get_project(completed["project_id"])

    assert project["locked_brief_id"]
    assert project["character_batch_id"]
    outlines = [item for item in project["episodes"] if item["stage"] == "outline"]
    assert len(outlines) == 20
```

场景二：关联已有项目到第 1–5 集剧本，第二集失败后恢复。

```python
def test_conversation_retries_only_failed_script_episode(e2e_context) -> None:
    project = e2e_context.project_with_locked_outline_and_beats(episodes=range(1, 6))
    session = e2e_context.create_session("继续写剧本")
    e2e_context.link_project(session, project["id"])
    e2e_context.fail_script_episode_once(2)

    first = e2e_context.confirm_and_wait(
        e2e_context.chat(session, "生成第1到5集剧本")
    )
    assert first["result"]["partial"] is True
    retry = e2e_context.retry_and_wait(session["id"], first["id"])
    assert retry["result"]["executed_episodes"] == [2]
    assert e2e_context.script_versions(project["id"]) == {1: 1, 2: 1, 3: 1, 4: 1, 5: 1}
```

同一文件再增加两项四类起点覆盖：

```python
def test_complete_story_goes_directly_to_confirmed_brief(e2e_context) -> None:
    session = e2e_context.create_session("完整故事：林岚发现公司用情绪积分决定工资并组织同事反抗")
    proposed = e2e_context.chat(session, "跳过方向比较，整理 Brief 并创建项目")
    assert proposed["messages"][-1]["cards"][0]["card_type"] == "brief"
    completed = e2e_context.confirm_and_wait(proposed)
    project = e2e_context.creative_repository.get_project(completed["project_id"])
    assert project["blueprints"][0]["direction_type"] == "original"


def test_imported_story_and_optional_reference_are_snapshotted(e2e_context) -> None:
    session = e2e_context.create_session("继续创作")
    e2e_context.upload_material(session, "旧剧本.md", "第一集：林岚的工资因愤怒清零")
    reference = e2e_context.seed_readable_topic_reference("职场热播样本")
    proposed = e2e_context.chat(
        session, f"参考选题 {reference['id']}，基于全部材料生成大纲"
    )
    snapshot = e2e_context.source_snapshot(proposed["actions"][-1]["source_snapshot_id"])
    assert [item["name"] for item in snapshot["materials"]] == ["旧剧本.md"]
    assert snapshot["references"][0]["resource_id"] == reference["id"]
    assert snapshot["references"][0]["readable"] is True
```

- [ ] **步骤 2：运行端到端测试并修正真实集成缺口**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_original_workflow_integration.py tests/test_writer_agent_integration.py tests/test_writer_agent_api.py tests/test_writer_agent_runner.py -q
```

预期：全部 PASS。只能修正测试暴露的契约、权限、幂等或恢复缺口，不扩展首版范围。

- [ ] **步骤 3：运行权限、页面和浏览器验收**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_project_member_management_api.py tests/test_project_member_stage_editing.py tests/test_original_project_api.py tests/test_page_authorization.py tests/test_original_project_browser.py tests/test_screenwriter_dashboard_browser.py tests/test_writer_agent_browser.py -q
```

预期：全部 PASS；创建负责人能全阶段创作但治理操作为 403，策划页与编剧页原创入口仍可用。

- [ ] **步骤 4：运行完整单元测试和文件头检查**

运行：

```powershell
uv run pytest tests/ -q
uv run pre-commit run --all-files
```

预期：测试全部 PASS 或仅有明确记录的环境型 skip；pre-commit 全部 PASS。不得通过删除测试、放宽权限断言或增加固定 sleep 获得通过。

- [ ] **步骤 5：检查数据库与 API 兼容性**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_system_database_alembic.py tests/test_original_creative_repository.py tests/test_writer_agent_store.py tests/test_webgui_ui_changes.py -q
```

预期：旧 creative SQLite、旧 Writer Agent 会话和系统 Alembic 均可升级；旧 API 路径和评审响应仍存在。

- [ ] **步骤 6：提交端到端验收**

```powershell
git add tests/writer_agent_harness.py tests/test_writer_agent_integration.py tests/test_original_workflow_integration.py tests/test_original_project_browser.py tests/test_screenwriter_dashboard_browser.py tests/test_writer_agent_browser.py tests/test_page_authorization.py
git commit -m "test: 验收对话式项目创作全链路"
```

## 完成定义

- 所有 11 个任务分别完成测试循环并形成独立 commit。
- 编剧大师可评审、创建、关联和继续项目创作，旧能力无回归。
- 一句话、直接 Brief、文档导入、已有项目四种起点均有自动化覆盖。
- 大纲、Beats、剧本可直接作为目标阶段，依赖补齐行为可见且可审计。
- 创建者自动成为 `project_creator`，全阶段可编辑但没有删除、归档、成员和任务分配权限。
- 项目负责人（平台角色）和系统管理员可管理策划、编剧创建的项目。
- 锁定成果不被覆盖；重做只生成新版本。
- 按钮和自然语言确认统一，重复确认不产生重复项目或版本。
- 全剧任务可部分失败、只重试失败项，并在刷新或进程恢复后继续对账。
- 全部相关 pytest、Playwright 测试和 pre-commit 通过。
