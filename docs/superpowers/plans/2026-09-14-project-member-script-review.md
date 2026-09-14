# 项目成员发起剧本审核实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让任意有效项目成员都能发起对应项目的剧本审核并读取审核进度，同时保持非成员、已停用成员和非项目审核的现有权限边界。

**架构：** 在 `PermissionService` 增加一个不受 `data_scope` 限制的有效项目成员查询，保留现有分配项目和管理项目权限方法不变。剧本审核路由把项目来源请求授权改为“系统管理员或有效成员”，并继续对文本/文件审核及确认、删除等变更操作使用现有权限码。

**技术栈：** Python 3.11、FastAPI、SQLAlchemy Core、pytest、FastAPI TestClient

---

## 文件结构

- 修改：`identity/permission_service.py` — 提供只判断有效成员记录的权限原语。
- 修改：`screenplay_review/api.py` — 应用项目成员发起和读取审核的资源级授权。
- 修改：`tests/test_identity_permissions.py` — 验证成员查询不受 `data_scope` 影响且拒绝停用成员。
- 修改：`tests/test_script_review_api.py` — 覆盖有效成员创建/读取、非成员拒绝和文本审核权限保持不变。

### 任务 1：提供有效项目成员权限原语

**文件：**
- 修改：`identity/permission_service.py:145-204`
- 测试：`tests/test_identity_permissions.py`

- [x] **步骤 1：编写失败的成员权限测试**

在 `tests/test_identity_permissions.py` 增加测试。它要抓住的破坏是：成员查询错误地复用了 `data_scope >= project` 门槛，导致 `data_scope="task"` 的有效成员被拒绝。

```python
def test_active_project_membership_is_independent_of_data_scope(
    repository: IdentityRepository, permission_service: PermissionService
):
    role = repository.create_role("review_member", "审核成员", "")
    repository.replace_role_permissions(role["id"], [("rank.read", "task")])
    user = repository.create_user("review-member", "审核成员", HASH)
    repository.replace_user_roles(user["id"], [role["id"]])
    principal = permission_service.principal_for_user(user["id"], "session-1")
    assert principal is not None
    assert principal.data_scope == "task"

    with repository.connect() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO project_members
                (id, project_id, user_id, project_role, status)
            VALUES
                ('active-review-member', 'project-1', %s, 'writer', 'active'),
                ('inactive-review-member', 'project-2', %s, 'writer', 'inactive')
            """,
            (user["id"], user["id"]),
        )

    assert permission_service.is_active_project_member(principal, "project-1") is True
    assert permission_service.is_active_project_member(principal, "project-2") is False
    assert permission_service.is_active_project_member(principal, "project-3") is False
```

- [x] **步骤 2：运行测试并确认红灯**

运行：

```bash
uv run pytest tests/test_identity_permissions.py::test_active_project_membership_is_independent_of_data_scope -q
```

预期：FAIL，`PermissionService` 缺少 `is_active_project_member`。

- [x] **步骤 3：实现最小成员查询**

在 `PermissionService` 中加入：

```python
def is_active_project_member(self, principal: Principal, project_id: str) -> bool:
    if principal.is_system_admin:
        return True
    with self.repository.connect() as connection:
        row = connection.execute(
            select(project_members.c.id).where(
                project_members.c.project_id == project_id,
                project_members.c.user_id == principal.user_id,
                project_members.c.status == "active",
            )
        ).first()
    return row is not None
```

不调用 `can_access_assigned_project`，避免重新引入 `data_scope` 门槛。

- [x] **步骤 4：运行权限测试**

运行：

```bash
uv run pytest tests/test_identity_permissions.py -q
```

预期：配置 PostgreSQL 测试库时全部 PASS；未配置时数据库测试按既有规则 SKIP。

- [x] **步骤 5：提交任务 1**

```bash
git add identity/permission_service.py tests/test_identity_permissions.py
git commit -m "feat(identity): expose active project membership"
```

### 任务 2：按项目成员关系授权剧本审核

**文件：**
- 修改：`screenplay_review/api.py:76-112,205-232`
- 测试：`tests/test_script_review_api.py`

- [x] **步骤 1：编写失败的项目审核 API 测试**

新增一个 `PermissionService` 测试替身，真实运行 FastAPI 路由、创作仓库和审核记录写入，只替代外部 PostgreSQL 成员查询：

```python
class MembershipPermissionService(PermissionService):
    def __init__(self, active_project_ids: set[str]) -> None:
        self.active_project_ids = active_project_ids

    def is_active_project_member(self, principal: Principal, project_id: str) -> bool:
        return project_id in self.active_project_ids
```

新增测试：

```python
def test_active_project_member_without_review_generate_can_create_and_read_review(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": "member-review", "source_path": "member.json",
        "captured_at": "2026-09-14", "title": "成员审核项目", "description": "测试",
        "category": "都市", "rank_type": "热播榜", "score": 80,
        "confidence": "high", "score_details": {}, "recommendation": "值得拆解",
        "evidence": [],
    }])[0]
    topic = repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(
        topic["id"],
        {"protagonist": "主角", "core_conflict": "主角反击", "hooks": ["反转"]},
        {},
        [],
    )
    breakdown = repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(
        topic["id"], breakdown["id"], "成员审核项目", 1, created_by="owner-1"
    )
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], [{"relation": "对手"}]
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"], [{"episode": 1, "summary": "主角反击"}], "单集弧线"
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(
        project["id"], 1, {"beats": [{"order": 1, "goal": "反击"}]}
    )
    repository.lock_episode_beats(beats["id"])
    repository.create_episode_script(
        project["id"], 1, {"episode_title": "第 1 集", "scenes": []}
    )
    principal = Principal(
        user_id="member-1",
        username="member-1",
        display_name="项目成员",
        role_codes=("screenwriter",),
        permissions=frozenset(),
        data_scope="task",
        session_id="session-member",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: principal
    app.state.identity_permission_service = MembershipPermissionService(
        {repository.workflow_project_id(project["id"])}
    )
    app.include_router(build_script_review_router(repository, FakeJobManager()))
    client = TestClient(app)

    created = client.post(
        "/api/script-reviews",
        json={
            "source_type": "project",
            "mode": "single",
            "project_id": project["id"],
            "episode_numbers": [1],
        },
    )

    assert created.status_code == 201
    detail = client.get(f"/api/script-reviews/{created.json()['review']['id']}")
    assert detail.status_code == 200
```

同组补充两个独立断言：

- 非成员即使具有 `script.review.generate` 也返回 403；
- 无 `script.review.generate` 的账户提交文本审核仍返回 403。

- [x] **步骤 2：运行新增 API 测试并确认红灯**

运行：

```bash
uv run pytest tests/test_script_review_api.py -k "active_project_member or non_member or text_review_still" -q
```

预期：有效成员创建测试返回 403，证明旧的分配/管理项目判断和全局权限码仍在阻止请求。

- [x] **步骤 3：实现最小路由授权调整**

将项目访问判断改为：

```python
def can_access_project(request: Request, principal: Principal, project_id: int) -> bool:
    if principal.is_system_admin:
        return True
    service = getattr(request.app.state, "identity_permission_service", None)
    if not isinstance(service, PermissionService):
        return False
    return service.is_active_project_member(
        principal, repository.workflow_project_id(project_id)
    )
```

在 `create_review` 中先校验请求；文本来源继续执行 `assert_permission(principal, "script.review.generate")`，项目来源改由 `assert_project_access` 授权，不再额外要求审核权限码。

对列表、详情和导出读取接口使用现有 `can_read_review` 的资源级过滤；项目成员或文本审核创建者可以读取，确认和删除接口继续保留 `script.review.generate` 检查。

- [x] **步骤 4：运行剧本审核回归测试**

运行：

```bash
uv run pytest tests/test_script_review_api.py tests/test_script_reviews.py tests/test_script_optimizations.py -q
```

预期：全部 PASS。

- [x] **步骤 5：运行关联权限测试和格式检查**

运行：

```bash
uv run pytest tests/test_identity_permissions.py tests/test_web_security.py -q
uv run pre-commit run --files identity/permission_service.py screenplay_review/api.py tests/test_identity_permissions.py tests/test_script_review_api.py docs/superpowers/plans/2026-09-14-project-member-script-review.md
git diff --check
```

预期：测试通过或仅出现既有的未配置 PostgreSQL 测试跳过；pre-commit 与 diff 检查退出码为 0。

- [x] **步骤 6：提交任务 2**

```bash
git add screenplay_review/api.py tests/test_script_review_api.py docs/superpowers/plans/2026-09-14-project-member-script-review.md
git commit -m "fix(review): allow active project members to start reviews"
```
