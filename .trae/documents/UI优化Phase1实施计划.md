# AiWorker UI 优化 — Phase UI-1 实施计划

**依据**：`improve/AiWorker UI优化PRD工程实施文档.md` 第 8.1 节（Phase UI-1）+ 第 4.1、6.1、6.2、7、9.1、9.3 节
**范围**：仅 Phase UI-1（基础工作台壳与请求能力），不涉及 UI-2/3/4 的页面迁移
**后端改动**：无（不新增接口、不改权限与业务状态机）

---

## 1. 交付目标与验收（来自 PRD 8.1）

| # | 验收项 | 落地位置 |
|---|--------|----------|
| 1 | 侧栏在手机端（<768px）可键盘打开/关闭，焦点行为正确 | `base.html` + `workspaces.css` |
| 2 | 401/403 保持既有安全行为；接口错误可被页面捕获并结束 loading | `api.js` |
| 3 | 任何页面不再无条件执行 `fetchJobs()`；仅任务/后台需要时加载 | `tasks.js` |
| 4 | 页面包含一个 `aria-live="polite"` 状态区域和可复用 Toast 容器 | `base.html` + `ui-state.js` |

---

## 2. 现状分析（已核实）

### 2.1 应用壳 `templates/layouts/base.html`（61 行）
- `<body x-data="appData()" x-init="init()" data-workspace-page="{{ workspace_page }}">`；`<aside class="sidebar">` 内为品牌区 + `<nav class="nav flex-column py-3" aria-label="主导航">`（`show_tasks` + `navigation` 循环，`current_route` 高亮）。
- 已有 `#permission-alert`（`aria-live="assertive"`，固定右上角）与 `#permission-alert-dismiss`。
- `<main class="main-content">` 内仅 `{% block content %}`；脚本顺序：`bootstrap.bundle.min.js` → `api.js`/`session.js`（有 principal 时）→ `tasks.js`（workspace 页）→ `{% block scripts %}`。
- **缺失**：导航抽屉开关按钮、`aria-live="polite"` 全局状态区、Toast 容器、统一上下文栏 block。

### 2.2 样式 `static/css/workspaces.css`（747 行）
- 深色 token（`--bg-panel`、`--border`、`--accent`、`--text-primary` 等）已定义。
- 现有 `@media (max-width: 767.98px)`（L719-742）把侧栏压成 58px 图标栏（无抽屉、无焦点管理）。
- 仅 `.project-name-button:focus-visible`、`.score-help:focus` 有焦点样式；无全局 `:focus-visible` 规则、无 Skeleton/Empty/Error 基础类、无 reduced-motion 处理。
- 已有 `.creative-save-toast`、`.download-progress-toast` 为局部实现，可被新 Toast 体系兼容共存。

### 2.3 请求封装 `static/js/common/api.js`（45 行）
- `WRITE_METHODS` + CSRF Meta 注入；`credentials: 'same-origin'`。
- 401：`window.location.assign('/login')` 后 `throw new Error('登录状态已失效')`（普通 Error）。
- 403：构造 `PermissionError` 并派发 `aiworker:permission-denied` 事件，**随后返回 response（不抛出）**——PRD 6.2 要求改为"派发事件 + 抛出 PermissionError"。
- **无** `ApiError`、无 `X-Request-ID` 读取、无 `detail/message` 解析、无 `isAbortError` 辅助。
- **兼容性核实**：全库无任何调用方检查 `res.status === 403`，403 改为抛出不破坏现有页面。

### 2.4 页面状态 `static/js/common/tasks.js`（797 行）
- `init()`（L181）在 L218 **无条件** `await this.fetchJobs();`；L273 对除 `admin-analytics` 外**所有**页面 `setInterval(() => this.fetchJobs(), 2000)`。
- `fetchJobs()`（L591）消费方（已核实 `this.jobs`/`this.isRunning` 引用）：
  - `admin/workspace.js` L664 → `admin-jobs`；
  - `selection/breakdowns.js`（拆解生成 + 剧目下载进度）→ `selection-hits`、`selection-topics`；
  - `selection/dramas.js`（剧目分析）→ `selection-dramas`；
  - `common/creative.js` L1138（创作任务进度）→ `planning-*`、`writing-*`。
- `selection-ranks`：`pollTodayRankRefresh` 直连 `/api/jobs/{id}`，成功后显式调用 `this.fetchJobs()`（L99）——不依赖全局轮询，但 `fetchJobs` 方法必须保留。
- `tasks`/`review-tasks`、`selection-dashboard`、`admin-config/data/drama-player/analytics`、其余 admin 页、`review-*` 均不消费 `this.jobs`。
- 测试约束：`test_selection_rank_template_has_today_rank_page` 断言 `"async fetchJobs" in html`（方法定义保留即可）。

### 2.5 模板上下文 `web/page_router.py`
- `_template_context`（L267 起）已提供 `principal`、`navigation`、`current_route`、`page_title`、`workspace_page`、`show_tasks`、`asset_version` 等；上下文栏 block 与抽屉无需新增后端上下文。

### 2.6 测试基线 `tests/test_webgui_ui_changes.py`
- 大量"源码内容断言"式测试（`_workspace_source()` 聚合全部模板/JS/CSS）。UI-1 改动需保证既有断言不破（尤其 `"async fetchJobs" in html`、`<nav class="nav flex-column py-3"` 拆分、`test_removed_assets_and_rag_pages_are_not_in_sidebar`）。

---

## 3. 变更方案（按文件）

### 3.1 `templates/layouts/base.html`
目标：抽屉化响应式壳 + 全局可访问通知 + 统一上下文栏 block。

1. **移动端顶栏 + 抽屉开关**（放在 `<aside>` 之前，仅 <768px 显示，CSS 控制显隐）：
   ```html
   <header class="mobile-topbar" id="mobile-topbar">
       <button type="button" class="btn btn-outline-light btn-sm sidebar-toggle" id="sidebar-toggle"
               aria-label="打开导航菜单" aria-expanded="false" aria-controls="workspace-sidebar">
           <i class="bi bi-list" aria-hidden="true"></i>
       </button>
       <span class="small text-muted">{{ page_title }}</span>
   </header>
   <div class="sidebar-backdrop" id="sidebar-backdrop" aria-hidden="true"></div>
   ```
2. **侧栏加 id 与标签**：`<aside class="sidebar" id="workspace-sidebar" aria-label="工作台侧栏">`（nav 的 `aria-label="主导航"` 保留）。
3. **统一上下文栏 block**（放在 `<main>` 内 `{% block content %}` 之前）：
   ```html
   {% block page_header %}{% endblock %}
   ```
   UI-1 不迁移任何页面，仅提供空 block 契约；页面迁移在 UI-2/3 进行。
4. **全局 live region + Toast 容器**（放在 `</body>` 前、`{% if principal %}` 内，`#permission-alert` 之后）：
   ```html
   <div id="ui-live-region" class="visually-hidden" aria-live="polite" aria-atomic="true"></div>
   <div id="ui-toast-region" class="toast-container position-fixed top-0 end-0 p-3" style="z-index: 1100;"></div>
   ```
   注意：Toast 容器**不加** `aria-live`，文本通知统一走 `ui-state.announce()` → `#ui-live-region`，避免与 `#permission-alert`（assertive）双播报。
5. **脚本**：在 `{% if principal %}` 块内 `api.js` 之后插入 `ui-state.js`：
   ```html
   <script src="/static/js/common/ui-state.js?v={{ asset_version }}"></script>
   ```
6. **抽屉内联脚本**（放 `</body>` 前，约 25 行，无外部依赖）：
   - 点击 `#sidebar-toggle` → `body.sidebar-open` 切换；同步 `aria-expanded` 与 `aria-label`。
   - 打开时焦点移入抽屉内第一个可聚焦元素（nav 链接/按钮）；关闭（Escape / 背板点击 / 再次点击按钮）时焦点返回 `#sidebar-toggle`。
   - `document.addEventListener('keydown', e => e.key === 'Escape' && ...)` 关闭并回焦。
   - 不依赖 hover；关闭后背板 `pointer-events: none` 不遮挡主内容。
   - 仅当 `#sidebar-toggle` 与 `#sidebar-backdrop` 存在时执行（非登录页自动跳过）。

> 决策：抽屉 JS 用内联脚本而非新建 `shell.js`，符合 PRD 6.1 的 UI-1 文件边界（只列了 base.html/workspaces.css/api.js/ui-state.js/tasks.js/测试）。

### 3.2 `static/css/workspaces.css`
目标：应用壳断点、抽屉、焦点、基础状态类、reduced-motion。

1. **全局焦点**：
   ```css
   :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
   .sidebar :focus-visible { outline-color: #fff; }
   ```
   保留既有 `.project-name-button:focus-visible`、`.score-help:focus`。
2. **断点重构**（核心）：
   - 原 `@media (max-width: 767.98px)`（L719-742）拆为两段：
     - **平板图标栏** `@media (min-width: 768px) and (max-width: 1023.98px)`：移入原图标栏规则（`--sidebar-width: 58px`、`.sidebar > .p-4` 收窄、`.nav-link span` 隐藏、`.nav-link` 居中）。
     - **移动抽屉** `@media (max-width: 767.98px)`：保留原 `.main-content` 内边距与 creative 表格转卡片规则；新增：
       ```css
       .sidebar { position: fixed; top: 0; bottom: 0; left: 0; z-index: 1045;
                  width: min(280px, 84vw); transform: translateX(-100%);
                  transition: transform .18s ease; overflow-y: auto; }
       body.sidebar-open .sidebar { transform: translateX(0); box-shadow: 0 0 40px rgba(0,0,0,.5); }
       .sidebar-backdrop { position: fixed; inset: 0; z-index: 1040;
                           background: rgba(0,0,0,.45); opacity: 0; pointer-events: none;
                           transition: opacity .18s ease; }
       body.sidebar-open .sidebar-backdrop { opacity: 1; pointer-events: auto; }
       body.sidebar-open { overflow: hidden; }
       .mobile-topbar { display: flex; align-items: center; gap: 8px;
                        padding: 8px 12px; border-bottom: 1px solid var(--border);
                        background: var(--bg-panel); }
       ```
   - 默认 `.mobile-topbar { display: none; }`；桌面/平板不显示。
3. **Toast 基础样式**：`.ui-toast`（复用 `--bg-panel`/`--border` 深色样式，`min-width: 280px`，`max-width: min(360px, calc(100vw - 32px))`）+ kind 变体（success/danger/warning/info 用现有状态色）；与既有 `.download-progress-toast`、`.creative-save-toast` 并存。
4. **基础状态类**（供 ui-state.js 与后续阶段复用）：
   ```css
   .ui-skeleton { position: relative; overflow: hidden; background: var(--bg-panel-2); border-radius: 6px; }
   .ui-skeleton::after { content: ''; position: absolute; inset: 0;
       background: linear-gradient(90deg, transparent, rgba(255,255,255,.06), transparent);
       animation: ui-shimmer 1.4s infinite; }
   @keyframes ui-shimmer { 100% { transform: translateX(100%); } }
   .ui-state-card { padding: 16px; border: 1px solid var(--border); border-radius: 8px;
                    background: var(--bg-panel); }
   ```
5. **reduced-motion**：
   ```css
   @media (prefers-reduced-motion: reduce) {
     *, *::before, *::after { transition-duration: 0.01ms !important;
                              animation-duration: 0.01ms !important;
                              animation-iteration-count: 1 !important;
                              scroll-behavior: auto !important; }
     .ui-skeleton::after { animation: none; }
   }
   ```
6. 动效约束：仅 opacity/transform，时长 ≤180ms（抽屉位移 ≤ 侧栏宽度，符合 PRD 7.3；进入位移上限对抽屉场景放宽为容器自身宽度）。

### 3.3 `static/js/common/api.js`
目标：结构化错误 + request ID + 标准错误解析 + 403 抛出，保持既有调用方式兼容。

1. **`ApiError`**（PRD 6.2 字段契约）：
   ```js
   class ApiError extends Error {
       constructor(message, {status = 0, requestId = null, response = null} = {}) {
           super(message); this.name = 'ApiError';
           this.status = status; this.requestId = requestId; this.response = response;
       }
   }
   ```
2. **`PermissionError` 改为继承 `ApiError`**（`status = 403`），`name` 保持 `'PermissionError'`，导出不变。
3. **辅助函数**：
   - `readRequestId(response)` → `response.headers.get('X-Request-ID')`。
   - `async parseErrorDetail(response)` → 克隆 JSON 后优先读 `detail`（string，或数组取 `detail[0].msg`），再读 `message`；非 JSON 返回 `请求失败（HTTP {status}）`。
   - `isAbortError(error)` → `error?.name === 'AbortError'`。
4. **`request()` 调整**：
   - 401：保持 `window.location.assign('/login')`，改抛 `new ApiError('登录已失效，请重新登录后继续。', {status: 401, requestId, response})`。
   - 403：保留 `aiworker:permission-denied` 派发（session.js 提示不变），随后 **`throw`** `PermissionError`（`requestId` 一并携带），使页面 catch 可收束 loading（PRD 6.2）。
   - 其余状态码仍原样返回 response（调用方既有 `res.ok` 判断不受影响）。
   - signal 透传：`fetch` 已通过 `...options` 展开透传 `signal`，显式补 `signal: options.signal` 保持契约可读。
5. **导出**：`window.aiworkerApi = {ApiError, PermissionError, request, isAbortError, readRequestId, parseErrorDetail}`（超集，向后兼容）。

### 3.4 `static/js/common/ui-state.js`（新建）
按 PRD 6.2 实现 `window.aiworkerUi` 全部 7 个无框架 API；IIFE 包裹，`'use strict'`，无依赖（`bootstrap.Toast` 存在时增强，缺失时降级自管理）。

```js
window.aiworkerUi = {
  requestKey(key, factory),   // 同 key 维护一个 AbortController；新请求先 abort 旧请求；
                              // factory(signal) 返回 Promise，由调用方把 signal 传给 aiworkerApi.request
  cancelRequest(key),         // abort 并移除
  readCache(key),             // 返回 {value, stale} 或 null；stale = 已过 TTL 仍有旧值（SWR 语义）
  writeCache(key, value, {ttlMs = 30000} = {}),  // 内存 Map，仅放非敏感、会话内已授权数据
  invalidateCache(prefix),    // 删除所有以 prefix 开头的键
  announce(message, {priority = 'polite'} = {}), // 写入 #ui-live-region；清空后延迟写入以支持重复文案
  showToast({kind, title, message}),            // 视觉 Toast + 文本 announce；kind: success|danger|warning|info
  setButtonLoading(button, {loading, label}),   // WeakMap 保存/恢复原 disabled、文案、aria-busy
};
```

实现要点：
- `requestKey` 内部 `Map<key, AbortController>`；`finally` 中仅当控制器仍为当前项时删除，避免竞态。
- `readCache` 过期不删除（SWR 留旧值），命中 TTL 内返回 `{value, stale: false}`，过期返回 `{value, stale: true}`，无记录返回 `null`。
- `showToast`：在 `#ui-toast-region` 内建 `.toast`（Bootstrap 结构，`role="status"`）；有 `window.bootstrap` 用 `bootstrap.Toast.getOrCreateInstance(el, {delay: 4000}).show()`，否则手动加 `show` 类并在 4s 后移除；`message` 非空时同时 `announce(message)`。
- `announce`：region 上设置 `aria-live=priority` 后清空 textContent，`setTimeout(..., 50)` 再写入，保证相同文案可重复播报。
- `setButtonLoading`：保存 `{disabled, text, ariaBusy}` 到 WeakMap；loading=true 时 `disabled=true; aria-busy=true; text=label || '处理中…'`；恢复时还原。
- `cancelRequest` 与 `requestKey` 的 AbortError：由调用方用 `aiworkerApi.isAbortError` 判断后静默（PRD 7.1 AbortError 行）。
- 缓存键约定：调用方键名按 PRD 6.3 前缀（`creative:topics`、`workflow:tasks`、`jobs` 等）；sessionStorage 偏好写入能力推迟到 UI-2（筛选偏好）。

### 3.5 `static/js/common/tasks.js`
目标：`fetchJobs` 仅在消费 job 状态的页面执行；接入公共请求能力；轮询频率符合 PRD 9.3。

1. **新增页面判定**（依据 2.4 的消费方证据）：
   ```js
   JOB_POLL_INTERVAL_MS: 5000,
   pageNeedsJobPolling() {
       return this.page === 'admin-jobs'
           || ['selection-hits', 'selection-topics', 'selection-dramas'].includes(this.page)
           || this.page.startsWith('planning-')
           || this.page.startsWith('writing-');
   },
   ```
2. **`init()` 改造**：
   - L218 `await this.fetchJobs();` → `if (this.pageNeedsJobPolling()) await this.fetchJobs();`
   - L273 `if (this.page !== 'admin-analytics') setInterval(() => this.fetchJobs(), 2000);` → `if (this.pageNeedsJobPolling()) setInterval(() => this.fetchJobs(), JOB_POLL_INTERVAL_MS);`
3. **`fetchJobs()` 方法体不变**（保留 `async fetchJobs` 定义以满足既有测试；`selection-ranks` 刷新成功后的显式 `this.fetchJobs()`、creative/breakdowns 内的显式调用全部保留）。
4. **受影响页面确认**：
   - 移除轮询：`tasks`/`review-tasks`、`selection-dashboard`、`selection-ranks`、`admin-config/data/drama-player/analytics`、`admin-*` 管理页、`review-*` —— 这些页面不消费 `this.jobs`，其自身数据流（workflow tasks、analytics、文件列表、config 表单）均走专属 API。
   - 保留轮询：`admin-jobs`、`selection-hits/topics/dramas`、`planning-*`、`writing-*`。
5. 本轮暂不把 2s 轮询改为指数退避（PRD 9.3 的退避策略归入 UI-2/UI-4 与任务进度收口一起做），仅以 5s 恒定基线达标"不高频轮询"；在代码中以 `JOB_POLL_INTERVAL_MS` 常量体现。

### 3.6 `tests/test_webgui_ui_changes.py`（扩展，不破坏既有断言）
新增 6 个源码级测试（沿用 `_source`/`_workspace_source` 模式）：

```python
def test_workspace_shell_has_live_region_and_toast_container():
    base = _source("templates/layouts/base.html")
    assert 'id="ui-live-region"' in base and 'aria-live="polite"' in base
    assert 'id="ui-toast-region"' in base
    assert "ui-state.js" in base

def test_workspace_shell_has_accessible_mobile_drawer_controls():
    base = _source("templates/layouts/base.html")
    assert 'id="sidebar-toggle"' in base
    assert 'aria-controls="workspace-sidebar"' in base
    assert 'aria-expanded' in base
    assert 'id="sidebar-backdrop"' in base
    assert "Escape" in base

def test_workspace_shell_has_unified_context_bar_block():
    base = _source("templates/layouts/base.html")
    assert "{% block page_header %}{% endblock %}" in base

def test_api_layer_exposes_structured_errors_and_request_ids():
    api = _source("static/js/common/api.js")
    assert "class ApiError" in api
    assert "X-Request-ID" in api
    assert "isAbortError" in api
    assert "window.location.assign('/login')" in api
    assert "aiworker:permission-denied" in api
    assert "throw error" in api

def test_ui_state_module_exposes_the_shared_api():
    ui = _source("static/js/common/ui-state.js")
    for fn in ("requestKey", "cancelRequest", "readCache", "writeCache",
               "invalidateCache", "announce", "showToast", "setButtonLoading"):
        assert fn in ui

def test_jobs_polling_is_scoped_to_pages_that_consume_job_state():
    tasks = _source("static/js/common/tasks.js")
    assert "pageNeedsJobPolling()" in tasks
    assert "if (this.pageNeedsJobPolling())" in tasks
```

既有断言兼容性核查（必须保持）：
- `test_selection_rank_template_has_today_rank_page` 的 `"async fetchJobs" in html` —— 方法定义保留，✅。
- `test_removed_assets_and_rag_pages_are_not_in_sidebar` 按 `<nav class="nav flex-column py-3"` 拆分 —— 该行不改，✅。
- `_workspace_source()` 聚合含新增 `ui-state.js` —— 不影响既有断言。

---

## 4. 关键决策记录

| # | 决策 | 理由 |
|---|------|------|
| D1 | 抽屉 JS 内联在 base.html，不新建 shell.js | PRD 6.1 UI-1 文件边界未列 shell.js；逻辑 ≤30 行 |
| D2 | 403 派发事件后**抛出** PermissionError | PRD 6.2 明确要求；已核实无调用方依赖 403 响应对象 |
| D3 | 断点：≥1024px 全侧栏 / 768–1023.98px 图标栏 / <768px 抽屉 | PRD 4.1 "平板收窄、<768 抽屉"；原 767.98px 图标栏规则上移 |
| D4 | Toast 容器不加 aria-live，文本通知统一走 `#ui-live-region` | 避免与 `#permission-alert`(assertive) 双播报 |
| D5 | job 轮询 allowlist = `admin-jobs` + `selection-hits/topics/dramas` + `planning-*` + `writing-*` | 依据 `this.jobs`/`this.isRunning` 消费方证据（2.4） |
| D6 | 轮询间隔 2000ms → 5000ms 常量，退避推迟 | PRD 9.3 "不高频轮询，5 秒起始"；退避归 UI-2/4 |
| D7 | ui-state.js 本轮仅内存缓存；sessionStorage 偏好写入推迟 UI-2 | PRD 6.2 "仅筛选偏好可写 sessionStorage"，筛选在 UI-2 |
| D8 | 上下文栏仅新增 `{% block page_header %}` 空契约，页面不迁移 | UI-1 只交付壳；页面迁移在 UI-2/3 |
| D9 | 零后端改动、零新接口 | PRD 6.4 "P0 不强制新接口"（overview 聚合归 UI-2 再评估） |

---

## 5. 实施步骤（顺序）

1. **base.html**：顶栏/开关/背板 → 侧栏 id/aria → live region + Toast 容器 → `page_header` block → `ui-state.js` 引入 → 抽屉内联脚本。
2. **workspaces.css**：全局 focus-visible → 断点重构（图标栏上移 + 抽屉/背板/顶栏）→ Toast 基础样式 → Skeleton/state-card → reduced-motion。
3. **api.js**：`ApiError`/`PermissionError` 重构 → `readRequestId`/`parseErrorDetail`/`isAbortError` → request() 401/403 调整 → 导出。
4. **ui-state.js**（新建）：按 3.4 实现 7 个 API。
5. **tasks.js**：`pageNeedsJobPolling()` + `JOB_POLL_INTERVAL_MS` → init() 两处门控。
6. **测试**：向 `tests/test_webgui_ui_changes.py` 追加 3.6 的 6 个测试。

---

## 6. 验证

```bash
uv run pytest tests/test_webgui_ui_changes.py -x        # 新增 + 既有源码级断言
uv run pytest tests/                                     # 全量单测（含权限/工作流回归）
pre-commit run --all-files                               # 文件头检查（新增为 .js，确认规则）
uv run python -c "import WebGUI"                        # 应用可导入
```

手工浏览器冒烟（可选，登录后）：
- 390px 宽度：抽屉开/关、Escape 回焦、背板点击关闭、`aria-expanded` 同步；
- 1024px：图标栏；1440px：完整侧栏；
- 触发一次 403（如无权限操作）确认 `#permission-alert` 出现且页面 loading 收束；
- DevTools 慢网下触发接口失败，确认无全页白屏、可复现错误卡（UI-1 只保证请求可捕获 + toast/live-region 就位，错误卡具体页面接入归 UI-2）。

浏览器回归脚本（`tests/test_*_browser.py` 模式）：如仓库已有该模式则按既有命名补一条抽屉/断点用例，否则归入 UI-4 的浏览器回归批次，UI-1 以手工冒烟为准。

---

## 7. 风险与回退

- **403 抛出行为变更**：已核实无调用方依赖；若个别页面异常，可保留 dispatch 后返回 response 的旧行为并仅新增 `PermissionError` 抛出开关（如 `aiworkerApi.throwOn403`）。PRD 10 要求：出现权限展示与服务端拒绝不一致时立即回退为直接请求模式。
- **断点变化**（图标栏上移到平板段）：768–1024px 设备观感变化属预期（PRD 4.1）；异常时仅需回退该 media query。
- **轮询门控误伤**：若某页面依赖 `this.jobs` 但未进 allowlist，症状为进度不再更新；回退方式为在该页加回 allowlist 条目（代码中一行）。
- 全量 `tests/` 与权限/工作流测试必须保持通过（PRD 9.1）。
