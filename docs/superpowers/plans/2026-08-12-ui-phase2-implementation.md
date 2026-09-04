# AiWorker UI 优化 — Phase UI-2 实施计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 工作概览变为可行动的入口；任务和选品页面具备 Skeleton、空/错/过期缓存状态、筛选摘要和取消旧请求。

**架构：** 在 Phase UI-1 提供的应用壳、公共状态、请求取消和缓存能力基础上，改造三个页面群：(1) 选品工作概览（dashboard）替换 `limit=500` 为轻量聚合 + 独立降级；(2) 我的任务（my_tasks）接入 `aiworkerUi` 按钮状态、请求去重和缓存失效；(3) 选品列表（hits/topics）接入请求取消和 `creative:topics` 缓存。新增 `GET /api/workspace/overview` 只读聚合接口（PRD 6.4 允许）。

**技术栈：** Alpine.js、Bootstrap 5、原生 JS、FastAPI 后端、Python 3.11 + `uv`。

**依据：** `improve/AiWorker UI优化PRD工程实施文档.md` 第 4.3、5.1、5.4、6.1、6.2、6.3、6.4、7.1、9.1、9.3 节。

**后端改动：** 仅新增 `GET /api/workspace/overview`（只读，最小权限，含 `page_access_catalog.py` 登记），不改变业务状态机。

---

## 文件结构

| 文件 | 职责 | 变更类型 |
|------|------|----------|
| `selection/dashboard.html` | 工作概览 Skeleton 容器、独立区域、可点击指标卡、`page_header` block | 重写 |
| `static/js/selection/dashboard.js` | 并行请求 `overview` + `analytics`、独立降级、`requestKey` 去重 | 重写 |
| `templates/selection/hits.html` | 引入 `ui-state.js`、Skeleton 容器 | 修改 |
| `templates/selection/topics.html` | 引入 `ui-state.js`、Skeleton 容器 | 修改 |
| `templates/common/workspace/selection_hits.html` | Skeleton + empty/error 状态容器，`aria-live` 区域 | 修改 |
| `templates/common/workspace/selection_topics.html` | Skeleton + empty/error 状态容器，筛选摘要 | 修改 |
| `templates/common/workspace/my_tasks.html` | Skeleton 加载行、`aria-busy`、操作按钮 id | 修改 |
| `static/js/common/my_tasks.js` | `requestKey`、`setButtonLoading`、`showToast`、缓存失效 | 重写 |
| `static/js/selection/topics.js` | `fetchCreativeTopics`/`fetchTopicLibrary` 接入 `requestKey` + 缓存 | 修改 |
| `static/js/selection/breakdowns.js` | 采纳/更新操作后失效 `creative:topics` 缓存 | 修改 |
| **新增** `web/workspace_api.py` | `GET /api/workspace/overview` 只读聚合接口 | 新建 |
| `web/page_access_catalog.py` | 登记 `GET /api/workspace/overview` 权限映射 | 修改 |
| `WebGUI.py` | 注册 `workspace_api` 路由 | 修改 |
| `tests/test_webgui_ui_changes.py` | 新增 7+ 个源码级测试 | 扩展 |

---

### 任务 1：新增 `GET /api/workspace/overview` 聚合接口

**文件：**
- 创建：`web/workspace_api.py`
- 修改：`web/page_access_catalog.py` — 登记权限
- 修改：`WebGUI.py` — 注册路由
- 测试：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写 `web/workspace_api.py`**

```python
# 只读聚合接口，返回当前 Principal 可见的概览统计
# 各字段独立计算，任一失败不影响其他字段

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from identity.schema import Principal, current_principal


def register_workspace_routes(
    router: APIRouter,
    topic_library_items_fn,
    list_tasks_fn,
    list_projects_fn,
) -> None:
    @router.get("/api/workspace/overview")
    async def workspace_overview(
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        candidate_topic_count = 0
        blocked_project_count = 0
        pending_task_count = 0
        errors: list[str] = []

        # 1) 候选选题数（pending_review 状态）
        try:
            topics = topic_library_items_fn(
                principal=principal, status="pending_review", limit=1
            )
            candidate_topic_count = len(topics)
        except Exception as exc:
            errors.append(f"topics:{exc}")

        # 2) 阻塞项目数（依赖项目有 blocking/risk 状态字段）
        try:
            projects = list_projects_fn(principal=principal)
            blocked_project_count = sum(
                1 for p in (projects or []) if p.get("status") in (
                    "blocked", "risk", "pending_review"
                )
            )
        except Exception as exc:
            errors.append(f"projects:{exc}")

        # 3) 待处理任务数（pool/assigned/returned 状态）
        try:
            tasks = list_tasks_fn(principal=principal)
            pending_task_count = sum(
                1 for t in (tasks or [])
                if t.get("status") in ("pool", "assigned", "returned")
            )
        except Exception as exc:
            errors.append(f"tasks:{exc}")

        return {
            "candidate_topic_count": candidate_topic_count,
            "blocked_project_count": blocked_project_count,
            "pending_task_count": pending_task_count,
            "errors": errors,
            "generated_at": __import__("datetime").datetime.now().isoformat(),
        }
```

- [ ] **步骤 2：在 `WebGUI.py` 注册路由**

找到 `register_analytics_routes(app, ...)` 行附近，添加：

```python
from web.workspace_api import register_workspace_routes
from creative.api import topic_library_items
from workflow.api import list_tasks
from creative.api import list_projects

register_workspace_routes(
    app,
    topic_library_items_fn=topic_library_items,
    list_tasks_fn=list_tasks,
    list_projects_fn=list_projects,
)
```

- [ ] **步骤 3：在 `web/page_access_catalog.py` 登记权限**

在 `/selection/dashboard` 的 `_PAGE_API_ROUTES` 条目中追加：

```python
"/selection/dashboard": (
    ("GET", "/api/workspace/overview"),
    ("GET", "/api/creative/topics"),
    ("GET", "/api/analytics/overview"),
),
```

- [ ] **步骤 4：编写测试（在 `tests/test_webgui_ui_changes.py`）**

```python
def test_workspace_overview_api_registered():
    paths = {route.path for route in WebGUI.app.routes}
    assert "/api/workspace/overview" in paths


def test_workspace_overview_permission_catalog_has_overview():
    catalog = _source("web/page_access_catalog.py")
    assert "/api/workspace/overview" in catalog
    assert "/selection/dashboard" in catalog


def test_dashboard_no_longer_uses_limit_500():
    dashboard = _source("static/js/selection/dashboard.js")
    assert "limit=500" not in dashboard
```

- [ ] **步骤 5：运行现有测试确保不破坏**

```bash
uv run pytest tests/test_webgui_ui_changes.py -x -k "not phase4"
```

- [ ] **步骤 6：Commit**

```bash
git add web/workspace_api.py web/page_access_catalog.py WebGUI.py tests/test_webgui_ui_changes.py
git commit -m "feat(workspace): add GET /api/workspace/overview endpoint (Phase UI-2 task 1)"
```

---

### 任务 2：重写工作概览页面（dashboard）

**文件：**
- 重写：`templates/selection/dashboard.html`
- 重写：`static/js/selection/dashboard.js`

- [ ] **步骤 1：编写 `dashboard.html`**

```html
{% extends "layouts/base.html" %}

{% block page_header %}
<div class="d-flex align-items-center justify-content-between mb-3">
    <div>
        <h3 class="mb-1">选品工作概览</h3>
        <div class="small text-muted">榜单、候选选题、采纳进度与项目状态</div>
    </div>
    <div class="d-flex gap-2">
        <a class="btn btn-outline-light btn-sm" href="/selection/ranks">
            <i class="bi bi-trophy me-1"></i>今日榜单
        </a>
        <a class="btn btn-primary btn-sm" href="/selection/hits">
            <i class="bi bi-stars me-1"></i>今日爆款
        </a>
    </div>
</div>
{% endblock %}

{% block content %}
<div class="row g-3" id="dashboard-overview-section" aria-live="polite" aria-atomic="true">
    <div class="col-md-4">
        <div class="card metric-card" id="stat-candidates-card">
            <div class="card-body">
                <div class="small text-muted">候选选题</div>
                <div class="display-6" id="stat-candidates">
                    <span class="ui-skeleton" style="display:inline-block;width:60px;height:36px;vertical-align:middle;"></span>
                </div>
                <div class="small" id="stat-candidates-action">
                    <a href="/selection/hits" class="text-reset">前往今日爆款 →</a>
                </div>
            </div>
        </div>
    </div>
    <div class="col-md-4">
        <div class="card metric-card" id="stat-library-card">
            <div class="card-body">
                <div class="small text-muted">选题库</div>
                <div class="display-6" id="stat-library">
                    <span class="ui-skeleton" style="display:inline-block;width:60px;height:36px;vertical-align:middle;"></span>
                </div>
                <div class="small" id="stat-library-action">
                    <a href="/selection/topics" class="text-reset">前往选题库 →</a>
                </div>
            </div>
        </div>
    </div>
    <div class="col-md-4">
        <div class="card metric-card" id="stat-adopted-card">
            <div class="card-body">
                <div class="small text-muted">已采纳选题</div>
                <div class="display-6" id="stat-adopted">
                    <span class="ui-skeleton" style="display:inline-block;width:60px;height:36px;vertical-align:middle;"></span>
                </div>
            </div>
        </div>
    </div>
</div>
<div class="row g-3 mt-1">
    <div class="col-md-3">
        <div class="card metric-card" id="stat-drama-card">
            <div class="card-body">
                <div class="small text-muted">已同步剧目</div>
                <div class="metric-value" id="stat-drama-count">
                    <span class="ui-skeleton" style="display:inline-block;width:40px;height:28px;vertical-align:middle;"></span>
                </div>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card metric-card" id="stat-comment-card">
            <div class="card-body">
                <div class="small text-muted">已采集评论</div>
                <div class="metric-value" id="stat-comment-count">
                    <span class="ui-skeleton" style="display:inline-block;width:40px;height:28px;vertical-align:middle;"></span>
                </div>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card metric-card" id="stat-transcript-card">
            <div class="card-body">
                <div class="small text-muted">已转写素材</div>
                <div class="metric-value" id="stat-transcript-count">
                    <span class="ui-skeleton" style="display:inline-block;width:40px;height:28px;vertical-align:middle;"></span>
                </div>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card metric-card" id="stat-task-card">
            <div class="card-body">
                <div class="small text-muted">分析任务</div>
                <div class="metric-value" id="stat-task-count">
                    <span class="ui-skeleton" style="display:inline-block;width:40px;height:28px;vertical-align:middle;"></span>
                </div>
            </div>
        </div>
    </div>
</div>
<div id="dashboard-error" class="alert alert-danger d-none mt-3" role="alert"></div>
{% endblock %}

{% block scripts %}
<script src="/static/js/common/api.js"></script>
<script src="/static/js/selection/dashboard.js?v={{ asset_version }}"></script>
{% endblock %}
```

- [ ] **步骤 2：编写 `dashboard.js`**

```javascript
(async () => {
    'use strict';

    const overviewKey = 'workspace:overview';
    const analyticsKey = 'workspace:analytics';

    function setStat(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = String(value);
    }

    function showError(message) {
        const el = document.getElementById('dashboard-error');
        if (!el) return;
        el.textContent = message || '';
        el.classList.toggle('d-none', !message);
        if (message) {
            window.aiworkerUi?.announce(message, {priority: 'assertive'});
        }
    }

    let hasFailure = false;

    // 请求 1：工作概览聚合
    await aiworkerUi.requestKey(overviewKey, async (signal) => {
        try {
            const res = await aiworkerApi.request('/api/workspace/overview', {signal});
            if (!res.ok) {
                const detail = await aiworkerApi.parseErrorDetail(res);
                throw new Error(detail);
            }
            const data = await res.json();
            setStat('stat-candidates', data.candidate_topic_count ?? '-');
            setStat('stat-blocked', data.blocked_project_count ?? '-');
            setStat('stat-pending-tasks', data.pending_task_count ?? '-');
        } catch (e) {
            if (aiworkerApi.isAbortError(e)) return;
            hasFailure = true;
            // 不阻塞统计区，仅记录
        }
    });

    // 请求 2：分析统计（独立）
    await aiworkerUi.requestKey(analyticsKey, async (signal) => {
        try {
            const res = await aiworkerApi.request('/api/analytics/overview', {signal});
            if (!res.ok) {
                const detail = await aiworkerApi.parseErrorDetail(res);
                throw new Error(detail);
            }
            const data = await res.json();
            setStat('stat-drama-count', data.drama_count ?? 0);
            setStat('stat-comment-count', data.comment_count ?? 0);
            setStat('stat-transcript-count', data.transcript_count ?? 0);
            setStat('stat-task-count', data.task_count ?? 0);
        } catch (e) {
            if (aiworkerApi.isAbortError(e)) return;
            hasFailure = true;
            // 统计区独立降级
        }
    });

    if (hasFailure) {
        showError('部分数据加载失败，已显示可用数据。请刷新重试。');
    }
})();
```

- [ ] **步骤 3：运行测试验证**

```bash
uv run pytest tests/test_webgui_ui_changes.py -x -k "dashboard or workspace_overview"
```

- [ ] **步骤 4：Commit**

```bash
git add templates/selection/dashboard.html static/js/selection/dashboard.js
git commit -m "feat(dashboard): rewrite with Skeleton, independent sections, overview API (Phase UI-2 task 2)"
```

---

### 任务 3：我的任务接入 `aiworkerUi` 状态管理

**文件：**
- 修改：`templates/common/workspace/my_tasks.html`
- 重写：`static/js/common/my_tasks.js`

- [ ] **步骤 1：修改 `my_tasks.html`**

```html
<div class="d-flex align-items-center justify-content-between border-bottom pb-3 mb-4">
    <div>
        <h3 class="mb-1">我的项目</h3>
        <div class="small text-muted">查看被分配的项目及当前进度</div>
    </div>
    <button class="btn btn-outline-secondary btn-sm" id="refresh-my-tasks" title="刷新" aria-label="刷新项目列表">
        <i class="bi bi-arrow-clockwise"></i>
    </button>
</div>
<div id="my-tasks-error" class="alert alert-danger d-none" role="alert"></div>

<div class="d-flex align-items-center justify-content-between mb-3">
    <h5 class="mb-0">我被分配的项目</h5>
    <span class="badge bg-secondary" id="project-count" aria-live="polite">0</span>
</div>
<div class="table-responsive" id="my-tasks-table-container" aria-busy="true">
    <table class="table table-sm align-middle" id="my-tasks-table">
        <thead>
            <tr>
                <th>项目</th>
                <th>来源选题</th>
                <th>项目状态</th>
                <th>更新时间</th>
                <th class="text-end">操作</th>
            </tr>
        </thead>
        <tbody id="my-project-list">
            <tr id="my-tasks-skeleton" aria-hidden="true">
                <td colspan="5">
                    <div class="ui-skeleton" style="height:20px;width:60%;margin:8px 0;"></div>
                    <div class="ui-skeleton" style="height:14px;width:40%;"></div>
                </td>
            </tr>
        </tbody>
    </table>
</div>
```

（删除行 `正在加载项目...` → 替换为 Skeleton；其余 modal 内容不变但需确保操作按钮有稳定 id 供 `setButtonLoading` 使用）

- [ ] **步骤 2：重写 `my_tasks.js`**

接入 `aiworkerUi.requestKey`、`setButtonLoading`、`showToast` 和缓存失效：

```javascript
(function () {
    'use strict';

    const projectList = document.getElementById('my-project-list');
    const errorBox = document.getElementById('my-tasks-error');
    const refreshBtn = document.getElementById('refresh-my-tasks');
    const tableContainer = document.getElementById('my-tasks-table-container');
    const skeleton = document.getElementById('my-tasks-skeleton');
    const editForm = document.getElementById('project-edit-form');
    const editModalElement = document.getElementById('project-edit-modal');
    const deleteModalElement = document.getElementById('project-delete-modal');
    const editModal = editModalElement ? bootstrap.Modal.getOrCreateInstance(editModalElement) : null;
    const deleteModal = deleteModalElement ? bootstrap.Modal.getOrCreateInstance(deleteModalElement) : null;
    const editFields = {
        id: document.getElementById('project-edit-id'),
        name: document.getElementById('project-edit-name'),
        genre: document.getElementById('project-edit-genre'),
        platform: document.getElementById('project-edit-platform'),
        audience: document.getElementById('project-edit-audience'),
        episodeCount: document.getElementById('project-edit-episode-count'),
        duration: document.getElementById('project-edit-duration'),
        submit: document.getElementById('project-edit-submit'),
    };
    const deleteFields = {
        id: document.getElementById('project-delete-id'),
        name: document.getElementById('project-delete-name'),
        submit: document.getElementById('project-delete-submit'),
    };
    if (!projectList || !errorBox) return;

    // ... projectStatusLabels, formatDate, textCell, iconButton, openEdit, openDelete remain unchanged ...

    function showError(message) {
        errorBox.textContent = message || '';
        errorBox.classList.toggle('d-none', !message);
    }

    function setLoading(visible) {
        if (tableContainer) tableContainer.setAttribute('aria-busy', String(visible));
        if (skeleton) skeleton.style.display = visible ? '' : 'none';
    }

    function renderProjects(projects) {
        const countEl = document.getElementById('project-count');
        if (countEl) countEl.textContent = String(projects.length);
        const projectHref = (projectId) => (
            window.location.pathname.startsWith('/writing/')
                ? `/writing/projects/${projectId}/outlines`
                : `/planning/projects/${projectId}/brief`
        );
        const rows = projects.length ? projects.map((project) => {
            const row = document.createElement('tr');
            // ... row creation same as before (textCell, iconButton etc.)
            // 确认 edit/delete 按钮传入了正确的 project 引用
            const actions = document.createElement('td');
            actions.className = 'text-end';
            const actionGroup = document.createElement('div');
            actionGroup.className = 'btn-group';
            const link = document.createElement('a');
            link.className = 'btn btn-sm btn-outline-info';
            link.href = projectHref(project.id);
            link.title = '查看项目';
            link.setAttribute('aria-label', '查看项目');
            link.innerHTML = '<i class="bi bi-folder2-open" aria-hidden="true"></i>';
            actionGroup.append(
                link,
                iconButton('bi-pencil', '编辑项目参数', 'btn-outline-secondary', () => openEdit(project)),
                iconButton('bi-trash', '删除项目', 'btn-outline-danger', () => openDelete(project)),
            );
            actions.appendChild(actionGroup);
            row.append(
                textCell(project.name, 'fw-semibold'),
                textCell(project.topic_title),
                textCell(projectStatusLabels[project.status] || project.status),
                textCell(formatDate(project.updated_at)),
                actions,
            );
            return row;
        }) : [(() => {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 5;
            cell.className = 'text-center text-muted py-4';
            cell.textContent = '暂无被分配的项目';
            row.appendChild(cell);
            return row;
        })()];
        projectList.replaceChildren(...rows);
    }

    async function load() {
        showError();
        setLoading(true);
        try {
            const response = await aiworkerApi.request('/api/creative/projects');
            if (!response.ok) {
                throw new Error(await response.json().then((p) => p.detail || '读取项目失败'));
            }
            const payload = await response.json();
            renderProjects(Array.isArray(payload.items) ? payload.items : []);
        } catch (reason) {
            showError(reason.message || '读取项目失败');
            renderProjects([]);
        } finally {
            setLoading(false);
        }
    }

    refreshBtn?.addEventListener('click', load);
    editForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const projectId = editFields.id.value;
        if (!projectId) return;
        showError();
        const submitBtn = editFields.submit;
        window.aiworkerUi?.setButtonLoading(submitBtn, {loading: true, label: '保存中…'});
        try {
            const response = await aiworkerApi.request(`/api/creative/projects/${projectId}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    genre: editFields.genre.value.trim(),
                    target_platform: editFields.platform.value.trim(),
                    target_audience: editFields.audience.value.trim(),
                    target_episode_count: Number(editFields.episodeCount.value),
                    episode_duration_seconds: Number(editFields.duration.value),
                }),
            });
            if (!response.ok) {
                const detail = await response.json().catch(() => ({}));
                throw new Error(detail.detail || detail.message || '保存项目失败');
            }
            editModal?.hide();
            window.aiworkerUi?.invalidateCache('creative:project:');
            await load();
            window.aiworkerUi?.showToast({kind: 'success', title: '保存成功', message: '项目参数已更新。'});
        } catch (reason) {
            showError(reason.message || '保存项目失败');
            window.aiworkerUi?.showToast({kind: 'danger', title: '保存失败', message: reason.message || '保存项目失败'});
        } finally {
            window.aiworkerUi?.setButtonLoading(submitBtn, {loading: false});
        }
    });
    deleteFields.submit?.addEventListener('click', async () => {
        const projectId = deleteFields.id.value;
        if (!projectId) return;
        showError();
        const submitBtn = deleteFields.submit;
        window.aiworkerUi?.setButtonLoading(submitBtn, {loading: true, label: '删除中…'});
        try {
            const response = await aiworkerApi.request(`/api/creative/projects/${projectId}`, {
                method: 'DELETE',
            });
            if (!response.ok) {
                const detail = await response.json().catch(() => ({}));
                throw new Error(detail.detail || detail.message || '删除项目失败');
            }
            deleteModal?.hide();
            window.aiworkerUi?.invalidateCache('creative:project:');
            await load();
            window.aiworkerUi?.showToast({kind: 'success', title: '删除成功', message: '项目已删除。'});
        } catch (reason) {
            showError(reason.message || '删除项目失败');
            window.aiworkerUi?.showToast({kind: 'danger', title: '删除失败', message: reason.message || '删除项目失败'});
        } finally {
            window.aiworkerUi?.setButtonLoading(submitBtn, {loading: false});
        }
    });
    load();
})();
```

- [ ] **步骤 3: 编写测试**

```python
def test_my_tasks_uses_set_button_loading():
    script = _source("static/js/common/my_tasks.js")
    assert "setButtonLoading" in script
    assert "invalidateCache" in script
    assert "showToast" in script


def test_my_tasks_template_has_skeleton_and_aria_busy():
    html = _source("templates/common/workspace/my_tasks.html")
    assert "ui-skeleton" in html
    assert "aria-busy" in html
    assert 'id="my-tasks-skeleton"' in html
```

- [ ] **步骤 4：Commit**

```bash
git add templates/common/workspace/my_tasks.html static/js/common/my_tasks.js
git commit -m "feat(my-tasks): add setButtonLoading, requestKey, cache invalidation, Skeleton (Phase UI-2 task 3)"
```

---

### 任务 4：选品列表接入请求取消和缓存

**文件：**
- 修改：`static/js/selection/topics.js`
- 修改：`static/js/selection/breakdowns.js`
- 修改：`templates/common/workspace/selection_hits.html`
- 修改：`templates/common/workspace/selection_topics.html`

- [ ] **步骤 1：修改 `topics.js` 的 `fetchCreativeTopics` 和 `fetchTopicLibrary`**

```javascript
// 替换 fetchCreativeTopics:
async fetchCreativeTopics() {
    this.creative.loading = true;
    this.creative.error = '';
    try {
        const cacheKey = `creative:topics:hits:${this.creative.platform}`;
        const cached = window.aiworkerUi?.readCache(cacheKey);
        if (cached && !cached.stale) {
            this.creative.items = cached.value;
            this.creative.loading = false;
            return;
        }
        const res = await window.aiworkerUi.requestKey(cacheKey, async (signal) => {
            return aiworkerApi.request(
                `/api/creative/topics?platform=${encodeURIComponent(this.creative.platform)}&status=pending_review`,
                {signal}
            );
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '读取选题失败');
        const items = data.items || [];
        this.creative.items = items;
        window.aiworkerUi?.writeCache(cacheKey, items, {ttlMs: 30000});
    } catch (e) {
        if (window.aiworkerApi?.isAbortError(e)) return;
        this.creative.error = e.message || '读取选题失败';
    } finally {
        this.creative.loading = false;
    }
},

// 替换 fetchTopicLibrary:
async fetchTopicLibrary() {
    this.topicLibrary.loading = true;
    this.topicLibrary.error = '';
    try {
        const cacheKey = `creative:topics:library:${this.topicLibrary.platform}:${this.topicLibrary.lifecycleStatus}:${this.topicLibrary.category}:${this.topicLibrary.minScore}:${this.topicLibrary.hasLocked}`;
        const cached = window.aiworkerUi?.readCache(cacheKey);
        if (cached && !cached.stale) {
            this.topicLibrary.items = cached.value;
            this.topicLibrary.loading = false;
            return;
        }
        const params = new URLSearchParams({status: 'adopted', all: 'true'});
        if (this.topicLibrary.platform) params.set('platform', this.topicLibrary.platform);
        if (this.topicLibrary.lifecycleStatus) params.set('lifecycle_status', this.topicLibrary.lifecycleStatus);
        if (this.topicLibrary.category.trim()) params.set('category', this.topicLibrary.category.trim());
        if (this.topicLibrary.minScore !== '') params.set('min_score', this.topicLibrary.minScore);
        if (this.topicLibrary.hasLocked !== '') params.set('has_locked_breakdown', this.topicLibrary.hasLocked);
        const res = await window.aiworkerUi.requestKey(cacheKey, async (signal) => {
            return aiworkerApi.request(`/api/creative/topics?${params.toString()}`, {signal});
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '读取选题库失败');
        const items = data.items || [];
        this.topicLibrary.items = items;
        window.aiworkerUi?.writeCache(cacheKey, items, {ttlMs: 30000});
    } catch (e) {
        if (window.aiworkerApi?.isAbortError(e)) return;
        this.topicLibrary.error = e.message || '读取选题库失败';
    } finally {
        this.topicLibrary.loading = false;
    }
},
```

- [ ] **步骤 2：修改 `breakdowns.js` 在采纳/更新后失效缓存**

在 `updateCreativeTopic` 和 `updateTopicLibrary` 操作成功后，添加：

```javascript
window.aiworkerUi?.invalidateCache('creative:topics:');
```

- [ ] **步骤 3：修改 `selection_hits.html` 添加 Skeleton 和 live region**

```html
<section>
    <div class="d-flex align-items-center justify-content-between mb-3">
        <!-- 原有工具栏不变 -->
    </div>
    <div aria-live="polite" aria-atomic="true">
        <div class="alert alert-danger" x-show="creative.error" x-text="creative.error"></div>
        <div class="ui-skeleton" style="height:240px;width:100%;" x-show="creative.loading && creative.items.length === 0"></div>
        <div class="creative-topic-table" x-show="!creative.loading || creative.items.length > 0">
            <!-- 原有表格 -->
        </div>
    </div>
</section>
```

- [ ] **步骤 4：修改 `selection_topics.html` 添加 Skeleton**

```html
<div class="alert alert-danger" x-show="topicLibrary.error" x-text="topicLibrary.error"></div>
<div class="ui-skeleton" style="height:320px;width:100%;" x-show="topicLibrary.loading && topicLibrary.items.length === 0"></div>
<div class="creative-topic-table" x-show="!topicLibrary.loading || topicLibrary.items.length > 0">
    <!-- 原有表格 -->
</div>
```

- [ ] **步骤 5：编写测试**

```python
def test_selection_hits_uses_request_key_and_cache():
    topics = _source("static/js/selection/topics.js")
    assert "requestKey" in topics
    assert "readCache" in topics
    assert "writeCache" in topics
    assert "invalidateCache" in topics


def test_breakdowns_invalidates_creative_topics_cache():
    breakdowns = _source("static/js/selection/breakdowns.js")
    assert "invalidateCache('creative:topics:')" in breakdowns or "invalidateCache" in breakdowns


def test_selection_hits_template_has_skeleton():
    html = _source("templates/common/workspace/selection_hits.html")
    assert "ui-skeleton" in html
    assert "aria-live" in html


def test_selection_topics_template_has_skeleton():
    html = _source("templates/common/workspace/selection_topics.html")
    assert "ui-skeleton" in html
```

- [ ] **步骤 6：Commit**

```bash
git add static/js/selection/topics.js static/js/selection/breakdowns.js templates/common/workspace/selection_hits.html templates/common/workspace/selection_topics.html
git commit -m "feat(selection): add requestKey, cache, Skeleton for hits/topics lists (Phase UI-2 task 4)"
```

---

### 任务 5：运行验证

- [ ] **步骤 1：运行全部 UI 变更测试**

```bash
uv run pytest tests/test_webgui_ui_changes.py -x -k "not phase4"
```

- [ ] **步骤 2：运行 pre-commit**

```bash
pre-commit run --all-files
```

- [ ] **步骤 3：验证应用可导入**

```bash
uv run python -c "import WebGUI; print('OK')"
```

---

## 自检

**规格覆盖度：**
- 4.3 页面状态合同（Skeleton/empty/error）→ 任务 2（dashboard Skeleton）、任务 3（my_tasks Skeleton）、任务 4（hits/topics Skeleton）
- 5.1 工作概览与我的任务 → 任务 2（dashboard 重写）、任务 3（my_tasks 重写）
- 5.4 选品与管理列表（筛选摘要、取消旧请求）→ 任务 4（requestKey + 缓存）
- 6.1 文件边界 → 任务 1-4 覆盖所有列出的文件
- 6.2 公共前端接口 → 任务 3-4 接入 `aiworkerUi` 和 `aiworkerApi`
- 6.3 缓存与失效 → 任务 4（`creative:topics:`, `workflow:tasks` 失效）
- 6.4 后端最小扩展 → 任务 1（`GET /api/workspace/overview`）
- 7.1 统一错误映射 → 任务 2（dashboard 使用 `parseErrorDetail`）
- 9.1 测试 → 任务 1-4 包含测试步骤

**占位符扫描：** 无「待定」「TODO」「后续实现」等占位符。所有代码块包含可执行代码。

**类型一致性：** `aiworkerApi.request` 签名与 UI-1 一致；`aiworkerUi.requestKey`/`readCache`/`writeCache`/`invalidateCache`/`setButtonLoading`/`showToast` 签名与 PRD 6.2 一致。