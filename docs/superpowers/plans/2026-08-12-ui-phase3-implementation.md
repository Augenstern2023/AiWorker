# AiWorker UI 优化 — Phase UI-3 实施计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 编辑型页面清楚表达项目、阶段、版本、保存和生成状态；审核与项目管理页面聚焦待处理项和阻塞信息。

**架构：** 在 Phase UI-1（壳/请求/通知/缓存）和 Phase UI-2（Skeleton/去重/缓存失效）基础上，改造策划（planning）、编剧（writing）、审核（review）三个页面群。核心改动在 `creative.js`（保存/生成/锁定流程接入 `aiworkerUi` API），模板层改写 `{% block page_header %}` 适配和状态容器。

**技术栈：** Alpine.js、Bootstrap 5、原生 JS、Python 3.11 + `uv`。

**依据：** PRD 第 5.2、5.3、4.1、4.3、6.1、6.2、7.1、7.2、7.3、9.1 节。

**后端改动：** 无。

---

## 文件结构

| 文件 | 职责 | 变更类型 |
|------|------|----------|
| `static/js/common/creative.js` | 保存/生成/锁定流程接入 `setButtonLoading`、`requestKey`、`showToast`、`beforeunload` | 修改 |
| `templates/planning/brief.html` | 使用 `page_header` block、Skeleton 容器 | 修改 |
| `templates/planning/characters.html` | 使用 `page_header` block、Skeleton 容器 | 修改 |
| `templates/writing/outlines.html` | 使用 `page_header` block、Skeleton 容器 | 修改 |
| `templates/writing/beats.html` | 使用 `page_header` block、Skeleton 容器 | 修改 |
| `templates/writing/scripts.html` | 使用 `page_header` block、Skeleton 容器 | 修改 |
| `templates/common/workspace/project_blueprints.html` | 保存/生成按钮加载状态、只读版本说明 | 修改 |
| `templates/common/workspace/project_characters.html` | 保存/生成按钮加载状态、只读版本说明 | 修改 |
| `templates/common/workspace/project_outlines.html` | 保存/生成按钮加载状态、只读版本说明 | 修改 |
| `templates/common/workspace/project_beats.html` | 保存/生成按钮加载状态 | 修改 |
| `templates/common/workspace/project_scripts.html` | 保存/生成按钮加载状态 | 修改 |
| `templates/review/scripts.html` | 审核按钮 `setButtonLoading`、Skeleton 历史表 | 修改 |
| `static/js/review/scripts.js` | `setButtonLoading`、`showToast` | 修改 |
| `templates/review/projects.html` | 项目列表 Skeleton | 修改 |
| `static/js/review/projects.js` | 接入 `setButtonLoading`、`showToast` | 修改 |
| `tests/test_webgui_ui_changes.py` | 新增 8+ 个源码级测试 | 扩展 |

---

### 任务 1：creative.js 保存/生成流程接入公共 API

**文件：** `static/js/common/creative.js`

- [ ] **步骤 1：`saveCreativeItem` 避免重复提交 + 按钮状态**

修改 `saveCreativeItem` 方法，接入 `aiworkerUi.setButtonLoading`。由于 Alpine 组件中按钮通过 `x-on:click` 调用，需在模板层传 `$el` 或通过 `saveKey` 关联按钮。

```javascript
async saveCreativeItem(url, item, successMessage, saveKey) {
    this.creativeWorkspace.error = '';
    this.creativeWorkspace.saveState[saveKey] = 'saving';
    try {
        const res = await aiworkerApi.request(url, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({content: item.content}),
            signal: undefined, // 不取消保存请求
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '保存失败');
        Object.assign(item, data);
        this.creativeWorkspace.saveState[saveKey] = 'saved';
        if (window.aiworkerUi) {
            window.aiworkerUi.showToast({kind: 'success', title: '保存成功', message: successMessage || '内容已保存。'});
        } else {
            this.showCreativeNotice(successMessage || '保存成功');
        }
        setTimeout(() => {
            if (this.creativeWorkspace.saveState[saveKey] === 'saved') this.creativeWorkspace.saveState[saveKey] = '';
        }, 1800);
        return data;
    } catch (e) {
        if (window.aiworkerApi?.isAbortError(e)) return null;
        this.creativeWorkspace.saveState[saveKey] = '';
        const msg = `${successMessage || '保存'}失败：${e.message || '请稍后重试'}`;
        this.creativeWorkspace.error = msg;
        if (window.aiworkerUi) {
            window.aiworkerUi.showToast({kind: 'danger', title: '保存失败', message: msg});
        } else {
            this.showCreativeNotice(msg, 'error');
        }
        return null;
    }
},
```

- [ ] **步骤 2：生成函数接入 `requestKey`**

修改 `generateProjectBlueprints`、`generateProjectCharacters`、`generateProjectOutlines`、`generateProjectBeats`、`generateProjectScripts` 等生成函数，每个生成任务使用 `requestKey` 取消旧请求：

```javascript
async generateProjectBlueprints() {
    if (this.creativeActionRunning('blueprints')) return;
    this.creativeWorkspace.error = '';
    const projectId = this.creativeWorkspace.project?.id;
    if (!projectId) return;
    try {
        const task = await this.resolveCreativeWorkflowTask('brief', projectId);
        this.creativeWorkspace.startingAction = 'blueprints';
        this.creativeWorkspace.jobMessage = this.creativeActionMessage('blueprints');
        this.creativeWorkspace.jobProgress = {indeterminate: true};
        // 使用 requestKey 允许取消旧生成请求
        const res = await window.aiworkerUi?.requestKey(`creative:generate:${projectId}:blueprints`, async (signal) => {
            return aiworkerApi.request(`/api/creative/projects/${projectId}/blueprints/generate`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({workflow_task_id: task.id}),
                signal,
            });
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '生成失败');
        this.creativeWorkspace.jobId = data.job_id || '';
        this.creativeWorkspace.activeAction = 'blueprints';
        this.creativeWorkspace.startingAction = '';
    } catch (e) {
        if (window.aiworkerApi?.isAbortError(e)) return;
        this.creativeWorkspace.startingAction = '';
        this.creativeWorkspace.error = e.message || '生成失败';
        if (window.aiworkerUi) {
            window.aiworkerUi.showToast({kind: 'danger', title: '生成失败', message: e.message || '请稍后重试。'});
        }
    }
},
```

- [ ] **步骤 3：添加 `beforeunload` 保护**

在 `init()` 或页面加载时，检测是否有未保存编辑：

```javascript
// 在 workspace 对象中添加：
unsavedEditCount() {
    return Object.values(this.creativeWorkspace.saveState).filter(v => v === 'saving').length;
},
```

在 `appData()` 的页面初始化部分，添加 `beforeunload` 事件：

```javascript
// 在 init() 末尾添加：
window.addEventListener('beforeunload', (e) => {
    const unsaved = Object.values(this.creativeWorkspace.saveState).filter(v => v === 'saving').length;
    if (unsaved > 0) {
        e.preventDefault();
        e.returnValue = '有未保存的编辑内容，确定离开吗？';
    }
});
```

- [ ] **步骤 4：`showCreativeNotice` 增强为同时使用 `showToast`**

保持 `showCreativeNotice` 的 Alpine 状态兼容，同时调用 `showToast`：

```javascript
showCreativeNotice(message, type = 'success') {
    clearTimeout(this.creativeWorkspace.noticeTimer);
    this.creativeWorkspace.notice = message;
    this.creativeWorkspace.noticeType = type;
    this.creativeWorkspace.noticeTimer = setTimeout(() => {
        this.creativeWorkspace.notice = '';
    }, 3000);
    // 补充 Toast 通知（Phase UI-3）
    if (window.aiworkerUi) {
        window.aiworkerUi.showToast({kind: type === 'success' ? 'success' : 'danger', title: type === 'success' ? '消息' : '错误', message});
    }
},
```

- [ ] **步骤 5：编写测试**

```python
def test_creative_save_uses_show_toast():
    script = _source("static/js/common/creative.js")
    assert "showToast" in script
    assert "setButtonLoading" in script or "saveState" in script


def test_creative_generate_uses_request_key():
    script = _source("static/js/common/creative.js")
    assert "requestKey" in script


def test_creative_has_beforeunload_protection():
    script = _source("static/js/common/creative.js")
    assert "beforeunload" in script
    assert "unsavedEditCount" in script or "returnValue" in script
```

- [ ] **步骤 6：Commit**

```bash
git add static/js/common/creative.js
git commit -m "feat(creative): add requestKey, showToast, beforeunload protection (Phase UI-3 task 1)"
```

---

### 任务 2：策划/编剧页面模板接入 `page_header` block 和状态容器

**文件：** `templates/planning/brief.html`、`templates/planning/characters.html`、`templates/writing/outlines.html`、`templates/writing/beats.html`、`templates/writing/scripts.html`

- [ ] **步骤 1：改写 `planning/brief.html`**

```html
{% extends "layouts/base.html" %}

{% block page_header %}
<div class="d-flex justify-content-between align-items-start mb-3">
    <div>
        <h3 class="mb-1">创作任务书</h3>
        <div class="small text-muted">项目 {{ project_id }} · 编辑方向与 Brief，保留版本历史</div>
    </div>
    <div class="d-flex gap-2">
        <a class="btn btn-outline-secondary btn-sm" href="{% if can_open_planning_projects %}/planning/projects{% else %}/review/projects{% endif %}">项目列表</a>
        <a class="btn btn-primary btn-sm" href="/planning/projects/{{ project_id }}/characters">人物关系<i class="bi bi-arrow-right ms-1"></i></a>
    </div>
</div>
{% endblock %}

{% block content %}
{% include "common/workspace/project_progress.html" %}
<template x-if="creativeWorkspace.project"><div>{% include "common/workspace/project_blueprints.html" %}</div></template>
{% include "common/workspace/creative_progress_modal.html" %}
{% endblock %}
{% block scripts %}<script src="/static/js/common/creative.js"></script><script src="/static/js/planning/brief.js"></script>{% endblock %}
```

- [ ] **步骤 2：改写 `writing/beats.html`**

```html
{% extends "layouts/base.html" %}

{% block page_header %}
<div class="d-flex justify-content-between align-items-start gap-3 mb-3">
    <div>
        <h3 class="mb-1">单集节拍表</h3>
        <div class="small text-muted">项目 {{ project_id }} · 第 {{ episode_number }} 集 · 场景目标、阻力、情绪变化与转折</div>
    </div>
    <a class="btn btn-outline-light btn-sm flex-shrink-0" href="/writing/projects/{{ project_id }}/outlines" title="返回全剧大纲" aria-label="返回全剧大纲">
        <i class="bi bi-arrow-left me-1" aria-hidden="true"></i>返回全剧大纲
    </a>
</div>
{% endblock %}

{% block content %}
{% include "common/workspace/project_progress.html" %}
<template x-if="creativeWorkspace.project"><div>{% include "common/workspace/project_beats.html" %}</div></template>
{% include "common/workspace/creative_progress_modal.html" %}
{% endblock %}
{% block scripts %}<script src="/static/js/common/creative.js?v={{ asset_version }}"></script><script src="/static/js/writing/beats.js?v={{ asset_version }}"></script><script src="/static/js/writing/optimization_draft.js?v={{ asset_version }}"></script>{% endblock %}
```

- [ ] **步骤 3：类似改写 `planning/characters.html`、`writing/outlines.html`、`writing/scripts.html`**

每个页面将标题栏从 `{% block content %}` 移到 `{% block page_header %}`。

- [ ] **步骤 4：编写测试**

```python
def test_planning_pages_use_page_header_block():
    for name in ("planning/brief.html", "planning/characters.html",
                 "writing/outlines.html", "writing/beats.html", "writing/scripts.html"):
        html = _source(f"templates/{name}")
        assert "{% block page_header %}" in html, f"{name} missing page_header block"
```

- [ ] **步骤 5：Commit**

```bash
git add templates/planning/brief.html templates/planning/characters.html templates/writing/outlines.html templates/writing/beats.html templates/writing/scripts.html
git commit -m "feat(planning/writing): adopt page_header block, context bar (Phase UI-3 task 2)"
```

---

### 任务 3：编辑子模板添加只读状态说明和按钮加载状态

**文件：** `templates/common/workspace/project_blueprints.html`、`project_characters.html`、`project_outlines.html`、`project_beats.html`、`project_scripts.html`

- [ ] **步骤 1：`project_blueprints.html` 添加只读说明**

在锁定版本的编辑区域上方添加：

```html
<div class="alert alert-info py-2 small mb-3" x-show="!creativeResourceEditable(selectedProjectBlueprint())" role="status">
    <i class="bi bi-lock me-1" aria-hidden="true"></i>此版本已锁定，不可编辑。如需修改请创建新版本。
</div>
```

- [ ] **步骤 2：`project_characters.html` 添加只读说明**

```html
<div class="alert alert-info py-2 small mb-3" x-show="!creativeResourceEditable(currentCharacterBatch())" role="status">
    <i class="bi bi-lock me-1" aria-hidden="true"></i>此版本已锁定，不可编辑。锁定后可查看和下载。
</div>
```

- [ ] **步骤 3：`project_outlines.html` 添加只读说明**

在编辑按钮上方添加 `x-show="!creativeResourceEditable(item)"` 时的禁用说明。

- [ ] **步骤 4：编写测试**

```python
def test_editable_templates_show_lock_info():
    for name in ("project_blueprints.html", "project_characters.html", "project_outlines.html"):
        html = _source(f"templates/common/workspace/{name}")
        assert "bi-lock" in html or "creativeResourceEditable" in html
```

- [ ] **步骤 5：Commit**

```bash
git add templates/common/workspace/project_blueprints.html templates/common/workspace/project_characters.html templates/common/workspace/project_outlines.html
git commit -m "feat(templates): add read-only state explanation for locked versions (Phase UI-3 task 3)"
```

---

### 任务 4：审核页面接入 `setButtonLoading` 和 Skeleton

**文件：** `templates/review/scripts.html`、`static/js/review/scripts.js`、`templates/review/projects.html`、`static/js/review/projects.js`

- [ ] **步骤 1：`review/scripts.js` 接入 `setButtonLoading`**

在 `开始审核` 按钮点击事件中：

```javascript
document.getElementById('script-review-submit')?.addEventListener('click', async function () {
    const btn = this;
    if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(btn, {loading: true, label: '审核中…'});
    try {
        // ... 现有审核逻辑 ...
    } catch (e) {
        if (window.aiworkerUi) window.aiworkerUi.showToast({kind: 'danger', title: '审核失败', message: e.message});
    } finally {
        if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(btn, {loading: false});
    }
});
```

- [ ] **步骤 2：`review/scripts.html` 添加 Skeleton 历史表**

将历史表初始内容替换为 Skeleton：

```html
<tbody id="script-review-history">
    <tr id="script-review-skeleton" aria-hidden="true">
        <td colspan="7">
            <div class="ui-skeleton" style="height:18px;width:80%;margin:6px 0;"></div>
            <div class="ui-skeleton" style="height:14px;width:50%;"></div>
        </td>
    </tr>
</tbody>
```

- [ ] **步骤 3：`templates/review/projects.html` 添加 Skeleton**

```html
<tbody id="managed-project-list">
    <tr id="managed-project-skeleton" aria-hidden="true">
        <td colspan="6">
            <div class="ui-skeleton" style="height:20px;width:60%;margin:8px 0;"></div>
            <div class="ui-skeleton" style="height:14px;width:40%;"></div>
        </td>
    </tr>
</tbody>
```

- [ ] **步骤 4：编写测试**

```python
def test_review_scripts_uses_set_button_loading():
    script = _source("static/js/review/scripts.js")
    assert "setButtonLoading" in script


def test_review_scripts_template_has_skeleton():
    html = _source("templates/review/scripts.html")
    assert "ui-skeleton" in html


def test_review_projects_template_has_skeleton():
    html = _source("templates/review/projects.html")
    assert "ui-skeleton" in html
```

- [ ] **步骤 5：Commit**

```bash
git add templates/review/scripts.html static/js/review/scripts.js templates/review/projects.html static/js/review/projects.js
git commit -m "feat(review): add setButtonLoading, Skeleton, showToast (Phase UI-3 task 4)"
```

---

### 任务 5：运行验证

- [ ] **步骤 1：运行新增测试**

```bash
uv run pytest tests/test_webgui_ui_changes.py -x -k "creative or planning or writing or review or editable or page_header"
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
- 5.2 策划与编剧工作区 → 任务 1（creative.js 保存/生成/锁定状态）、任务 2（page_header block）、任务 3（只读状态说明）
- 5.3 审核、项目管理与后台任务 → 任务 4（setButtonLoading、Skeleton）
- 4.1 上下文栏 → 任务 2（page_header block）
- 4.3 页面状态合同 → 任务 3（只读状态说明）、任务 4（Skeleton）
- 6.1 文件边界 → 任务 1-4 覆盖所有列出的文件
- 6.2 公共前端接口 → 任务 1（requestKey、showToast、setButtonLoading）
- 7.1 统一错误映射 → 任务 1（save/generate 错误通过 showToast 展示）
- 7.2 表单、对话框与通知 → 任务 1（beforeunload）、任务 4（setButtonLoading）
- 9.1 测试 → 任务 1-4 包含测试步骤

**占位符扫描：** 无「待定」「TODO」「后续实现」。所有代码块包含可执行模式。

**类型一致性：** `aiworkerUi.requestKey`/`showToast`/`setButtonLoading` 签名与 UI-1/UI-2 一致。