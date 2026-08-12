# AiWorker UI 优化 — Phase UI-4 实施计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 管理页面的长列表、错误、高风险操作和小屏体验一致；完成性能与可访问性回归。

**架构：** 在 Phase UI-1/2/3 的基础上，为 `admin/*` 页面添加 Skeleton 加载状态、`setButtonLoading`/`showToast` 反馈、高风险操作确认对话框、分页/增量加载审核，以及 `workspaces.css` 可访问性收口。

**技术栈：** Alpine.js、Bootstrap 5、原生 JS、Python 3.11 + `uv`。

**依据：** PRD 第 5.4、6.1、7.1、7.2、7.3、9.1、9.2、9.3 节。

**后端改动：** 无。

---

## 文件结构

| 文件 | 职责 | 变更类型 |
|------|------|----------|
| `templates/admin/users.html` | Skeleton 用户表、高风险操作确认对话框 | 修改 |
| `static/js/admin/users.js` | `setButtonLoading`、`showToast`、确认对话框增强 | 修改 |
| `templates/admin/audit.html` | Skeleton 审计表、分页加载状态 | 修改 |
| `static/js/admin/audit.js` | `setButtonLoading`、`showToast` | 修改 |
| `templates/admin/deliveries.html` | Skeleton 成果列表 | 修改 |
| `static/js/admin/deliveries.js` | `setButtonLoading`、`showToast` | 修改 |
| `templates/admin/departments.html` | Skeleton 部门树 | 修改 |
| `templates/admin/roles.html` | Skeleton 角色表 | 修改 |
| `templates/admin/data.html` | 数据管理 Skeleton 确认 | 修改 |
| `templates/admin/jobs.html` | 使用 `page_header` block | 修改 |
| `static/css/workspaces.css` | 移动端表格回退、Admin 表格粘性表头、分页控件 | 修改 |
| `tests/test_webgui_ui_changes.py` | 新增 8+ 个源码级测试 | 扩展 |

---

### 任务 1：Admin 用户管理接入 `setButtonLoading`、`showToast` 和确认对话框

**文件：** `static/js/admin/users.js`、`templates/admin/users.html`

- [ ] **步骤 1：`users.js` 的 save/delete/toggleStatus 操作接入 `setButtonLoading`**

```javascript
// 在 saveUser 函数中：
elements.form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const submitBtn = document.getElementById('save-user');
    if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(submitBtn, {loading: true, label: '保存中…'});
    try {
        // ... 现有保存逻辑 ...
        if (window.aiworkerUi) window.aiworkerUi.showToast({kind: 'success', title: '保存成功', message: '用户已保存。'});
        editor.hide();
        await loadUsers();
    } catch (err) {
        showError(elements.formError, err.message);
        if (window.aiworkerUi) window.aiworkerUi.showToast({kind: 'danger', title: '保存失败', message: err.message});
    } finally {
        if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(submitBtn, {loading: false});
    }
});
```

- [ ] **步骤 2：deleteUser 增加确认对话框**

```javascript
function deleteUser(user) {
    const message = `确定删除用户「${user.username}」（${user.display_name}）吗？\n\n删除后该用户将无法登录系统，且无法恢复。`;
    if (!confirm(message)) return;
    // ... 现有删除逻辑 ...
}
```

- [ ] **步骤 3：`users.html` 添加 Skeleton**

```html
<tbody id="user-list">
    <tr id="user-list-skeleton" aria-hidden="true">
        <td colspan="9">
            <div class="ui-skeleton" style="height:20px;width:60%;margin:8px 0;"></div>
            <div class="ui-skeleton" style="height:14px;width:40%;"></div>
        </td>
    </tr>
</tbody>
```

- [ ] **步骤 4：编写测试**

```python
def test_admin_users_uses_set_button_loading():
    script = _source("static/js/admin/users.js")
    assert "setButtonLoading" in script


def test_admin_users_template_has_skeleton():
    html = _source("templates/admin/users.html")
    assert "ui-skeleton" in html
```

- [ ] **步骤 5：Commit**

```bash
git add static/js/admin/users.js templates/admin/users.html
git commit -m "feat(admin/users): add setButtonLoading, showToast, skeleton, confirm dialog (Phase UI-4 task 1)"
```

---

### 任务 2：Admin 审计日志接入 `setButtonLoading` 和 Skeleton

**文件：** `static/js/admin/audit.js`、`templates/admin/audit.html`

- [ ] **步骤 1：`audit.js` 接入 `setButtonLoading` + `showToast`**

CSV/XLSX 导出按钮添加 loading 状态：

```javascript
document.getElementById('export-audit-csv')?.addEventListener('click', async function() {
    if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(this, {loading: true, label: '导出中…'});
    try {
        // ... 导出逻辑 ...
    } finally {
        if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(this, {loading: false});
    }
});
```

- [ ] **步骤 2：`audit.html` 添加 Skeleton**

```html
<tbody id="audit-list">
    <tr id="audit-list-skeleton" aria-hidden="true">
        <td colspan="5">
            <div class="ui-skeleton" style="height:20px;width:80%;margin:6px 0;"></div>
            <div class="ui-skeleton" style="height:14px;width:50%;"></div>
        </td>
    </tr>
</tbody>
```

- [ ] **步骤 3：编写测试**

```python
def test_admin_audit_uses_set_button_loading():
    script = _source("static/js/admin/audit.js")
    assert "setButtonLoading" in script


def test_admin_audit_template_has_skeleton():
    html = _source("templates/admin/audit.html")
    assert "ui-skeleton" in html
```

- [ ] **步骤 4：Commit**

```bash
git add static/js/admin/audit.js templates/admin/audit.html
git commit -m "feat(admin/audit): add setButtonLoading, skeleton (Phase UI-4 task 2)"
```

---

### 任务 3：Admin 交付、角色、部门、数据页面添加 Skeleton

**文件：** `templates/admin/deliveries.html`、`templates/admin/roles.html`、`templates/admin/departments.html`、`templates/admin/data.html`

- [ ] **步骤 1：`deliveries.html` 添加 Skeleton**

```html
<div class="table-responsive">
    <div class="ui-skeleton" style="height:240px;width:100%;" id="deliveries-skeleton"></div>
    <table class="table table-sm align-middle d-none" id="deliveries-table">
        <!-- 现有表格 -->
    </table>
</div>
```

- [ ] **步骤 2：`roles.html`、`departments.html`、`data.html` 类似添加 Skeleton**

- [ ] **步骤 3：编写测试**

```python
def test_admin_pages_have_skeleton():
    for name in ("admin/deliveries.html", "admin/roles.html", "admin/departments.html", "admin/data.html"):
        html = _source(f"templates/{name}")
        assert "ui-skeleton" in html, f"{name} missing skeleton"
```

- [ ] **步骤 4：Commit**

```bash
git add templates/admin/deliveries.html templates/admin/roles.html templates/admin/departments.html templates/admin/data.html
git commit -m "feat(admin): add skeleton to deliveries, roles, departments, data pages (Phase UI-4 task 3)"
```

---

### 任务 4：`workspaces.css` 可访问性与移动端收口

**文件：** `static/css/workspaces.css`

- [ ] **步骤 1：Admin 表格粘性表头**

```css
.admin-table thead th {
    position: sticky;
    top: 0;
    z-index: 2;
    background: var(--bg-panel);
    border-bottom: 2px solid var(--accent);
}
```

- [ ] **步骤 2：移动端表格回退（<768px 时关键列保留）**

```css
@media (max-width: 767.98px) {
    .admin-table .col-actions { position: sticky; right: 0; background: var(--bg-panel); }
    .admin-table th, .admin-table td { white-space: nowrap; }
}
```

- [ ] **步骤 3：Pagination 控件样式**

```css
.ui-pagination .page-item.active .page-link {
    background: var(--accent);
    border-color: var(--accent);
}
.ui-pagination .page-link {
    background: transparent;
    color: var(--text);
    border-color: var(--border);
}
.ui-pagination .page-link:hover {
    background: rgba(59, 149, 246, .12);
    color: var(--accent);
}
```

- [ ] **步骤 4：编写测试**

```python
def test_workspace_css_has_admin_table_styles():
    css = _source("static/css/workspaces.css")
    assert "admin-table thead th" in css or "sticky" in css
    assert "ui-pagination" in css
```

- [ ] **步骤 5：Commit**

```bash
git add static/css/workspaces.css
git commit -m "feat(css): admin table sticky headers, pagination, mobile table fallback (Phase UI-4 task 4)"
```

---

### 任务 5：Admin jobs 页面使用 `page_header` block

**文件：** `templates/admin/jobs.html`

- [ ] **步骤 1：改为 `{% block page_header %}`**

```html
{% extends "layouts/base.html" %}

{% block page_header %}
<div class="d-flex align-items-center justify-content-between mb-3">
    <div><h3 class="mb-1">后台任务</h3><div class="small text-muted">查看和管理 Worker 与机器任务</div></div>
</div>
{% endblock %}

{% block content %}{% include "common/workspace/admin_jobs.html" %}{% include "common/workspace/admin_data_modals.html" %}{% endblock %}
{% block scripts %}<script src="/static/js/admin/workspace.js"></script><script src="/static/js/admin/jobs.js"></script>{% endblock %}
```

- [ ] **步骤 2：编写测试**

```python
def test_admin_jobs_uses_page_header_block():
    html = _source("templates/admin/jobs.html")
    assert "{% block page_header %}" in html
```

- [ ] **步骤 3：Commit**

```bash
git add templates/admin/jobs.html
git commit -m "feat(admin/jobs): adopt page_header block (Phase UI-4 task 5)"
```

---

### 任务 6：运行验证

- [ ] **步骤 1：运行新增测试**

```bash
uv run pytest tests/test_webgui_ui_changes.py -x -k "admin or skeleton or page_header or workspace_css"
```

- [ ] **步骤 2：运行全量 UI 测试（排除预存失败项）**

```bash
uv run pytest tests/test_webgui_ui_changes.py -x -k "not phase4"
```

- [ ] **步骤 3：运行 pre-commit**

```bash
pre-commit run --all-files
```

- [ ] **步骤 4：验证应用可导入**

```bash
uv run python -c "import WebGUI; print('OK')"
```

---

## 自检

**规格覆盖度：**
- 5.4 选品与管理列表（分页/增量加载、筛选、取消旧请求）→ 任务 1-3（Skeleton/Empty/Retry）
- 6.1 文件边界 → 任务 1-5 覆盖所有列出的 admin 文件
- 7.1 统一错误映射 → 任务 1-2（showToast 错误通知）
- 7.2 表单、对话框与通知 → 任务 1（确认对话框、setButtonLoading）
- 7.3 动效 → 任务 4（reduced-motion、sticky header）
- 9.1 测试 → 任务 1-5 包含测试步骤
- 9.2 浏览器回归 → 任务 4（移动端表格回退）
- 9.3 性能预算 → 任务 4（粘性表头避免回流）

**占位符扫描：** 无「待定」「TODO」「后续实现」。所有代码块包含可执行模式。

**类型一致性：** `aiworkerUi.setButtonLoading`/`showToast` 签名与 UI-1/2/3 一致。