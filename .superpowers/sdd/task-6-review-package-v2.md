# Commits

a2a253b fix: complete role workspace navigation and admin flows
5b1b5b3 refactor: split web gui into role workspaces

# Stat

 static/css/workspaces.css                          |  545 +++
 static/js/admin/audit.js                           |   25 +
 static/js/admin/deliveries.js                      |    1 +
 static/js/admin/jobs.js                            |    1 +
 static/js/admin/roles.js                           |  217 +
 static/js/admin/users.js                           |  194 +
 static/js/admin/workspace.js                       |  714 +++
 static/js/common/api.js                            |   46 +
 static/js/common/creative.js                       |  543 +++
 static/js/common/session.js                        |   13 +
 static/js/common/tasks.js                          |  460 ++
 static/js/planning/brief.js                        |    1 +
 static/js/planning/characters.js                   |    1 +
 static/js/planning/projects.js                     |    1 +
 static/js/review/approvals.js                      |    1 +
 static/js/review/tasks.js                          |    3 +
 static/js/selection/breakdowns.js                  |  618 +++
 static/js/selection/dramas.js                      |  169 +
 static/js/selection/ranks.js                       |  340 ++
 static/js/selection/topics.js                      |   94 +
 static/js/writing/beats.js                         |    1 +
 static/js/writing/outlines.js                      |    1 +
 templates/admin/analytics.html                     |    4 +
 templates/admin/audit.html                         |    7 +
 templates/admin/config.html                        |   14 +
 templates/admin/data.html                          |    4 +
 templates/admin/deliveries.html                    |    7 +
 templates/admin/jobs.html                          |    4 +
 templates/admin/roles.html                         |   16 +
 templates/admin/users.html                         |   17 +
 templates/common/workspace/admin_analytics.html    |  107 +
 templates/common/workspace/admin_config.html       |  117 +
 templates/common/workspace/admin_data.html         |   73 +
 templates/common/workspace/admin_data_modals.html  |   59 +
 templates/common/workspace/admin_jobs.html         |  263 ++
 templates/common/workspace/breakdown_modals.html   |   63 +
 .../common/workspace/creative_progress_modal.html  |   29 +
 templates/common/workspace/project_beats.html      |    9 +
 templates/common/workspace/project_blueprints.html |   30 +
 templates/common/workspace/project_characters.html |    6 +
 templates/common/workspace/project_list.html       |   18 +
 templates/common/workspace/project_outlines.html   |    4 +
 templates/common/workspace/project_progress.html   |   12 +
 templates/common/workspace/selection_dramas.html   |   91 +
 templates/common/workspace/selection_modals.html   |  182 +
 templates/common/workspace/selection_ranks.html    |  216 +
 templates/common/workspace/selection_topics.html   |  109 +
 templates/index.html                               | 4831 +-------------------
 templates/layouts/base.html                        |   69 +-
 templates/planning/brief.html                      |    9 +
 templates/planning/characters.html                 |    9 +
 templates/planning/directions.html                 |    9 +
 templates/planning/projects.html                   |    7 +
 templates/review/approvals.html                    |    7 +
 templates/review/projects.html                     |    6 +
 templates/review/tasks.html                        |    7 +
 templates/review/versions.html                     |    6 +
 templates/selection/breakdowns.html                |    9 +
 templates/selection/dashboard.html                 |   14 +
 templates/selection/dramas.html                    |    7 +
 templates/selection/ranks.html                     |    4 +
 templates/selection/topics.html                    |    9 +
 templates/writing/beats.html                       |    9 +
 templates/writing/outlines.html                    |    9 +
 templates/writing/projects.html                    |    7 +
 tests/test_role_page_content.py                    |  192 +
 tests/test_webgui_ui_changes.py                    |  177 +-
 web/page_router.py                                 |   52 +-
 68 files changed, 5989 insertions(+), 4910 deletions(-)

# Diff

diff --git a/static/css/workspaces.css b/static/css/workspaces.css
new file mode 100644
index 0000000..8cd48aa
--- /dev/null
+++ b/static/css/workspaces.css
@@ -0,0 +1,545 @@
+:root {
+            --sidebar-width: 248px;
+            --bg-page: #12121d;
+            --bg-panel: #1d1d2b;
+            --bg-panel-2: #252936;
+            --border: #33364a;
+            --text: #f1f4f8;
+            --muted: #9aa4b2;
+            --accent: #3b95f6;
+        }
+        body {
+            margin: 0;
+            overflow: hidden;
+            background: var(--bg-page);
+            color: var(--text);
+            font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
+        }
+        [x-cloak] { display: none !important; }
+        .sidebar {
+            position: fixed;
+            inset: 0 auto 0 0;
+            width: var(--sidebar-width);
+            background: var(--bg-panel);
+            border-right: 1px solid var(--border);
+            display: flex;
+            flex-direction: column;
+        }
+        .main-content {
+            margin-left: var(--sidebar-width);
+            height: 100vh;
+            overflow-y: auto;
+            padding: 22px;
+        }
+        .nav-link {
+            color: var(--muted);
+            border-radius: 0;
+            padding: 13px 18px;
+            display: flex;
+            align-items: center;
+            gap: 10px;
+        }
+        .nav-link:hover,
+        .nav-link.active {
+            color: var(--accent);
+            background: rgba(59, 149, 246, .12);
+            box-shadow: inset 3px 0 0 var(--accent);
+        }
+        .card,
+        .modal-content {
+            background: var(--bg-panel);
+            color: var(--text);
+            border: 1px solid var(--border);
+            border-radius: 8px;
+        }
+        .card-header,
+        .modal-header,
+        .modal-footer {
+            border-color: var(--border);
+        }
+        .form-control,
+        .form-select {
+            background: #151722;
+            border-color: var(--border);
+            color: var(--text);
+        }
+        .form-control:focus,
+        .form-select:focus {
+            background: #151722;
+            color: var(--text);
+            border-color: var(--accent);
+            box-shadow: none;
+        }
+        .form-control:disabled,
+        .form-select:disabled {
+            background-color: #151722;
+            border-color: var(--border);
+            color: var(--muted);
+            opacity: 1;
+        }
+        .creative-inline-textarea {
+            min-height: 38px;
+            line-height: 1.35;
+            resize: vertical;
+        }
+        .creative-save-status {
+            display: block;
+            margin-top: 4px;
+            color: #7dd3a8;
+            font-size: 11px;
+            white-space: nowrap;
+        }
+        .creative-save-toast {
+            position: fixed;
+            top: 18px;
+            right: 24px;
+            z-index: 1080;
+            display: flex;
+            align-items: center;
+            gap: 8px;
+            max-width: min(380px, calc(100vw - 32px));
+            padding: 10px 14px;
+            background: #1d1d2b;
+            color: var(--text);
+            border: 1px solid #36a269;
+            border-radius: 6px;
+            box-shadow: 0 10px 28px rgba(0, 0, 0, .32);
+        }
+        .table {
+            --bs-table-bg: transparent;
+            --bs-table-color: var(--text);
+            --bs-table-striped-bg: rgba(255, 255, 255, .045);
+            --bs-table-striped-color: var(--text);
+            --bs-table-hover-bg: rgba(59, 149, 246, .1);
+            --bs-table-hover-color: var(--text);
+            --bs-table-active-bg: rgba(59, 149, 246, .16);
+            --bs-table-active-color: var(--text);
+        }
+        .job-row {
+            cursor: pointer;
+        }
+        .job-row.active {
+            outline: 1px solid var(--accent);
+            background: rgba(59, 149, 246, .16) !important;
+        }
+        .creative-project-row {
+            cursor: pointer;
+            transition: background-color .15s ease, box-shadow .15s ease;
+        }
+        .creative-project-row.table-active {
+            box-shadow: inset 3px 0 0 var(--accent);
+        }
+        .project-name-button {
+            appearance: none;
+            border: 0;
+            background: transparent;
+            color: var(--text);
+            padding: 0;
+            font: inherit;
+            font-weight: 700;
+            text-align: left;
+            text-decoration: none;
+            display: inline-block;
+        }
+        .project-name-button:hover,
+        .creative-project-row.table-active .project-name-button {
+            color: #fff;
+        }
+        .project-name-button:focus-visible {
+            outline: 2px solid var(--accent);
+            outline-offset: 3px;
+        }
+        .scroll-pane {
+            overflow: auto;
+            max-height: 420px;
+        }
+        .breakdown-editor-pane {
+            height: min(62vh, 640px);
+            min-height: 360px;
+        }
+        .breakdown-editor-textarea {
+            flex: 1 1 auto;
+            min-height: 0;
+            resize: none;
+        }
+        .breakdown-field-guide {
+            flex: 1 1 auto;
+            min-height: 0;
+            overflow-y: auto;
+            padding-right: 6px;
+        }
+
+        /* 平台切换按钮 */
+        .rank-platform-toggle .btn {
+            min-width: 64px;
+            font-weight: 600;
+            border-color: var(--border);
+            color: var(--muted);
+        }
+        /* 榜单表格——精简后样式 */
+        .today-rank-table .ranking-heading {
+            display: grid;
+            grid-template-columns: 1fr auto 1fr;
+            align-items: center;
+            gap: 12px;
+            min-height: 42px;
+        }
+        .today-rank-table .ranking-title {
+            justify-self: center;
+            text-align: center;
+            font-weight: 800;
+            font-size: 16px;
+            letter-spacing: 0;
+        }
+        .today-rank-table .card-header .btn.active,
+        .today-rank-table .card-header .btn:hover {
+            color: var(--accent) !important;
+        }
+        .today-rank-route-nav {
+            display: flex;
+            align-items: center;
+            gap: 8px;
+            flex-wrap: wrap;
+        }
+        .today-rank-route-link {
+            border: 1px solid var(--border);
+            color: var(--muted);
+            background: rgba(255, 255, 255, .02);
+            font-weight: 700;
+            padding-inline: 20px;
+        }
+        .today-rank-route-link:hover,
+        .today-rank-route-link.active {
+            color: #eaf4ff;
+            border-color: var(--accent);
+            background: rgba(59, 149, 246, .16);
+            box-shadow: 0 0 0 1px rgba(59, 149, 246, .36);
+        }
+        .today-rank-table .rank-filter-btn {
+            display: inline-flex;
+            align-items: center;
+            justify-content: center;
+            font-weight: 600;
+        }
+        .today-rank-table .rank-filter-btn.active {
+            font-weight: 800;
+        }
+        .today-rank-table .card-header .btn .badge {
+            font-size: 0.7em;
+            background: rgba(122, 139, 164, .22) !important;
+            border: 1px solid rgba(154, 164, 178, .36);
+            color: #c9d3e5;
+        }
+        .today-rank-table .rank-number {
+            display: inline-flex;
+            align-items: center;
+            justify-content: center;
+            width: 28px;
+            height: 28px;
+            border-radius: 6px;
+            font-weight: 800;
+            font-size: 15px;
+            color: #eaf1ff;
+            background: rgba(154, 164, 178, .14);
+        }
+        .today-rank-table .rank-number.rank-top-1 {
+            color: #fff3c6;
+            background: rgba(234, 179, 8, .2);
+            border: 1px solid rgba(234, 179, 8, .38);
+        }
+        .today-rank-table .rank-number.rank-top-2 {
+            color: #e7edf8;
+            background: rgba(148, 163, 184, .2);
+            border: 1px solid rgba(148, 163, 184, .38);
+        }
+        .today-rank-table .rank-number.rank-top-3 {
+            color: #ffd9bd;
+            background: rgba(217, 119, 6, .18);
+            border: 1px solid rgba(217, 119, 6, .34);
+        }
+        .today-rank-table .rank-drama-title {
+            font-weight: 800;
+            color: #f7f9ff;
+        }
+        .today-rank-table .rank-subtitle {
+            color: #8793a8 !important;
+        }
+        .today-rank-table .rank-metric {
+            text-align: right;
+            font-variant-numeric: tabular-nums;
+            white-space: nowrap;
+        }
+        .today-rank-table .status-pill {
+            display: inline-flex;
+            align-items: center;
+            justify-content: center;
+            min-width: 58px;
+            border-radius: 999px;
+            padding: 3px 8px;
+            font-weight: 700;
+            font-size: 12px;
+            white-space: nowrap;
+        }
+        .today-rank-table .status-new {
+            color: #f9d98a;
+            background: rgba(245, 158, 11, .15);
+            border: 1px solid rgba(245, 158, 11, .28);
+        }
+        .today-rank-table .status-up {
+            color: #f5a3a8;
+            background: rgba(239, 68, 68, .14);
+            border: 1px solid rgba(239, 68, 68, .28);
+        }
+        .today-rank-table .status-down {
+            color: #8ddfcc;
+            background: rgba(20, 184, 166, .13);
+            border: 1px solid rgba(20, 184, 166, .27);
+        }
+        .today-rank-table .status-stable {
+            color: #b8c4d6;
+            background: rgba(100, 116, 139, .18);
+            border: 1px solid rgba(148, 163, 184, .24);
+        }
+        .today-rank-table .status-dropped {
+            color: #c4b5fd;
+            background: rgba(124, 58, 237, .13);
+            border: 1px solid rgba(139, 92, 246, .25);
+        }
+
+        .rank-platform-toggle .btn.active {
+            background: rgba(59, 149, 246, .18);
+            color: var(--accent);
+            border-color: var(--accent);
+            box-shadow: 0 0 0 1px var(--accent);
+        }
+
+                /* 今日榜单——固定表头 */
+        .today-rank-table .scroll-pane thead th {
+            position: sticky;
+            top: 0;
+            z-index: 2;
+            background: var(--bg-panel);
+            border-bottom: 2px solid var(--accent);
+        }
+        .today-rank-table .scroll-pane {
+            max-height: none;
+            overflow: visible;
+        }
+        .data-section {
+            min-height: calc(100vh - 44px);
+            display: flex;
+            flex-direction: column;
+        }
+        .data-card {
+            flex: 1 1 auto;
+            min-height: 0;
+            display: flex;
+            flex-direction: column;
+        }
+        .data-table-pane {
+            flex: 1 1 auto;
+            min-height: 0;
+            max-height: none;
+        }
+        .data-table-pane thead th {
+            position: sticky;
+            top: 0;
+            z-index: 1;
+            background: var(--bg-panel);
+        }
+        .data-drama-group {
+            border-top: 1px solid var(--border);
+            margin-top: 18px;
+        }
+        .data-drama-heading {
+            background: rgba(59, 149, 246, .08);
+            border-bottom: 1px solid var(--border);
+            padding: 10px 12px;
+        }
+        .data-type-heading {
+            border-bottom: 1px solid rgba(255, 255, 255, .08);
+            color: var(--muted);
+            font-size: .82rem;
+            font-weight: 600;
+            padding: 9px 12px 5px;
+        }
+        .file-preview-media {
+            display: block;
+            width: 100%;
+            max-height: 72vh;
+            background: #05070b;
+        }
+        .file-preview-text {
+            margin: 0;
+            min-height: 240px;
+            white-space: pre-wrap;
+            overflow-wrap: anywhere;
+            color: var(--text);
+        }
+        .topic-transcript-text {
+            max-height: 62vh;
+            overflow: auto;
+            padding: 16px;
+            border: 1px solid var(--border);
+            background: #10131a;
+        }
+        .log-pane {
+            height: 420px;
+            overflow: auto;
+            background: #10131a;
+            border-radius: 0 0 8px 8px;
+        }
+        .log-line {
+            font-family: Consolas, "Courier New", monospace;
+            font-size: 13px;
+            padding: 3px 8px;
+            white-space: pre-wrap;
+            border-bottom: 1px solid rgba(255, 255, 255, .04);
+        }
+        .text-muted {
+            color: var(--muted) !important;
+        }
+        .btn-icon {
+            width: 34px;
+            height: 34px;
+            display: inline-flex;
+            align-items: center;
+            justify-content: center;
+            padding: 0;
+        }
+        .metric-card {
+            min-height: 96px;
+        }
+        .metric-value {
+            font-size: 28px;
+            font-weight: 700;
+        }
+        .chart-box {
+            height: 280px;
+            width: 100%;
+            min-width: 360px;
+        }
+        .selling-point-list {
+            max-height: 280px;
+            overflow: auto;
+        }
+        .selling-point-item {
+            display: flex;
+            justify-content: space-between;
+            gap: 14px;
+            padding: 8px 0;
+            border-bottom: 1px solid rgba(255, 255, 255, .08);
+        }
+        .selling-point-text {
+            white-space: normal;
+            overflow-wrap: anywhere;
+            line-height: 1.5;
+        }
+        .download-progress-toast {
+            position: fixed;
+            right: 24px;
+            bottom: 24px;
+            z-index: 1060;
+            width: min(360px, calc(100vw - 32px));
+            padding: 14px;
+            background: var(--bg-panel);
+            border: 1px solid var(--border);
+            border-radius: 8px;
+            box-shadow: 0 14px 34px rgba(0, 0, 0, .34);
+        }
+        .score-help {
+            line-height: 1;
+            vertical-align: text-bottom;
+            text-decoration: none;
+        }
+        .score-help:hover,
+        .score-help:focus {
+            color: var(--accent) !important;
+        }
+
+        /* 选题库表格固定列宽 */
+        .creative-topic-table {
+            overflow-x: auto;
+        }
+        .creative-topic-table table {
+            table-layout: fixed;
+            width: 100%;
+            min-width: 1080px;
+        }
+        .creative-topic-table .col-topic { width: 15%; }
+        .creative-topic-table .col-desc { width: 18%; }
+        .creative-topic-table .col-source { width: 9%; }
+        .creative-topic-table .col-rank { width: 7%; }
+        .creative-topic-table .col-category { width: 7%; }
+        .creative-topic-table .col-score { width: 6%; }
+        .creative-topic-table .col-status { width: 11%; }
+        .creative-topic-table .col-breakdown { width: 9%; }
+        .creative-topic-table .col-actions { width: 18%; }
+        .creative-topic-table .lifecycle-badge,
+        .creative-topic-table .breakdown-version-badge {
+            max-width: 100%;
+            white-space: normal;
+            line-height: 1.35;
+        }
+        .creative-topic-table .lifecycle-badge {
+            color: #fff !important;
+            border: 1px solid rgba(255, 255, 255, .16);
+        }
+        .lifecycle-pending-review { background: #6b7280; }
+        .lifecycle-adopted { background: #0f766e; }
+        .lifecycle-pending-download { background: #b45309; }
+        .lifecycle-downloaded { background: #0369a1; }
+        .lifecycle-pending-breakdown { background: #c2410c; }
+        .lifecycle-breakdown-completed { background: #0e7490; }
+        .lifecycle-pending-imitation { background: #a21caf; }
+        .lifecycle-imitation-generated { background: #6d28d9; }
+        .lifecycle-pending-character-relationships { background: #be185d; }
+        .lifecycle-character-relationships-generated { background: #15803d; }
+        .lifecycle-pending-episode-outlines { background: #9f1239; }
+        .lifecycle-episode-outlines-generated { background: #4d7c0f; }
+        .lifecycle-pending-beats { background: #4338ca; }
+        .lifecycle-beats-generated { background: #047857; }
+        .lifecycle-beats-exported { background: #a16207; }
+        .lifecycle-rejected { background: #b91c1c; }
+        .lifecycle-archived { background: #4b5563; }
+
+        /* 简介和核心冲突列悬停展开 */
+        .topic-desc-cell,
+        .blueprint-conflict-cell {
+            transition: all 0.15s ease;
+        }
+        .topic-desc-cell:hover,
+        .blueprint-conflict-cell:hover {
+            overflow: visible !important;
+            -webkit-line-clamp: unset !important;
+            display: table-cell !important;
+            background: var(--bg-panel-2);
+            border-radius: 4px;
+            box-shadow: 0 2px 12px rgba(0,0,0,0.35);
+            position: relative;
+            z-index: 10;
+        }
+        @media (max-width: 767.98px) {
+            :root { --sidebar-width: 58px; }
+            .sidebar > .p-4 { padding: 14px 8px !important; text-align: center; }
+            .sidebar h4 { font-size: 0; }
+            .sidebar h4 i { font-size: 22px; margin: 0 !important; }
+            .sidebar > .p-4 .small,
+            .sidebar > .p-4 a,
+            .sidebar .nav-link span { display: none !important; }
+            .sidebar .nav-link { justify-content: center; padding: 13px 8px; }
+            .sidebar .nav-link i { font-size: 19px; }
+            .main-content { padding: 14px 12px; }
+            .creative-project-workspace .d-flex { flex-wrap: wrap; }
+            .creative-project-workspace table,
+            .creative-project-workspace thead,
+            .creative-project-workspace tbody,
+            .creative-project-workspace tr,
+            .creative-project-workspace th,
+            .creative-project-workspace td { display: block; width: 100% !important; }
+            .creative-project-workspace thead { display: none; }
+            .creative-project-workspace tbody tr { padding: 10px 0; border-bottom: 1px solid var(--border); }
+            .creative-project-workspace tbody td { border: 0; padding: 4px 0; white-space: normal !important; }
+            .creative-project-workspace .btn-group { width: 100%; margin-top: 12px; }
+            .creative-project-workspace .btn-group .btn { flex: 1 1 50%; }
+        }
diff --git a/static/js/admin/audit.js b/static/js/admin/audit.js
new file mode 100644
index 0000000..0dd9f1d
--- /dev/null
+++ b/static/js/admin/audit.js
@@ -0,0 +1,25 @@
+(async function () {
+    const body = document.getElementById('audit-list');
+    if (!body) return;
+
+    async function loadAudit() {
+        try {
+            const response = await window.aiworkerApi.request('/api/admin/audit?limit=100');
+            const events = await response.json();
+            body.replaceChildren(...events.map((event) => {
+                const row = document.createElement('tr');
+                for (const value of [event.created_at || '-', event.actor_username || event.actor_user_id || '-', event.action || '-', `${event.target_type || '-'} ${event.target_id || ''}`.trim(), event.result || '-']) {
+                    const cell = document.createElement('td');
+                    cell.textContent = value;
+                    row.appendChild(cell);
+                }
+                return row;
+            }));
+        } catch (error) {
+            body.firstElementChild.firstElementChild.textContent = error.message || '审计日志加载失败';
+        }
+    }
+
+    document.getElementById('refresh-audit')?.addEventListener('click', loadAudit);
+    await loadAudit();
+})();
diff --git a/static/js/admin/deliveries.js b/static/js/admin/deliveries.js
new file mode 100644
index 0000000..c9cba30
--- /dev/null
+++ b/static/js/admin/deliveries.js
@@ -0,0 +1 @@
+document.querySelector('[data-workspace-page="admin-deliveries"]')?.setAttribute('data-ready', 'true');
diff --git a/static/js/admin/jobs.js b/static/js/admin/jobs.js
new file mode 100644
index 0000000..0a42874
--- /dev/null
+++ b/static/js/admin/jobs.js
@@ -0,0 +1 @@
+document.querySelector('[data-workspace-page="admin-jobs"]')?.setAttribute('data-ready', 'true');
diff --git a/static/js/admin/roles.js b/static/js/admin/roles.js
new file mode 100644
index 0000000..a436a60
--- /dev/null
+++ b/static/js/admin/roles.js
@@ -0,0 +1,217 @@
+(function () {
+    'use strict';
+
+    const elements = {
+        body: document.getElementById('role-list'),
+        error: document.getElementById('role-error'),
+        formError: document.getElementById('role-form-error'),
+        form: document.getElementById('role-editor-form'),
+        title: document.getElementById('role-editor-title'),
+        id: document.getElementById('role-id'),
+        code: document.getElementById('role-code'),
+        name: document.getElementById('role-name'),
+        description: document.getElementById('role-description'),
+        permissions: document.getElementById('permission-list'),
+    };
+    if (!elements.body) return;
+
+    const editor = bootstrap.Modal.getOrCreateInstance(document.getElementById('role-editor-modal'));
+    let roles = [];
+    let permissionCatalog = [];
+
+    function showError(target, message = '') {
+        target.textContent = message;
+        target.classList.toggle('d-none', !message);
+    }
+
+    async function responseError(response, fallback) {
+        const data = await response.json().catch(() => ({}));
+        return data.detail || data.message || fallback;
+    }
+
+    function actionButton(icon, label, style, handler, disabled = false) {
+        const button = document.createElement('button');
+        button.type = 'button';
+        button.className = `btn btn-sm ${style}`;
+        button.title = label;
+        button.setAttribute('aria-label', label);
+        button.innerHTML = `<i class="bi ${icon}"></i>`;
+        button.disabled = disabled;
+        button.addEventListener('click', handler);
+        return button;
+    }
+
+    function renderPermissionEditor(grants = []) {
+        const grantMap = new Map(grants.map((grant) => [grant.permission_code, grant.data_scope]));
+        elements.permissions.replaceChildren(...permissionCatalog.map((permission) => {
+            const row = document.createElement('tr');
+            const toggleCell = document.createElement('td');
+            const toggle = document.createElement('input');
+            toggle.type = 'checkbox';
+            toggle.className = 'form-check-input permission-toggle';
+            toggle.dataset.permissionCode = permission.code;
+            toggle.checked = grantMap.has(permission.code);
+            toggleCell.appendChild(toggle);
+
+            const labelCell = document.createElement('td');
+            labelCell.textContent = permission.label;
+            if (permission.dependencies?.length) {
+                const dependencies = document.createElement('small');
+                dependencies.className = 'd-block text-muted';
+                dependencies.textContent = `依赖：${permission.dependencies.join('、')}`;
+                labelCell.appendChild(dependencies);
+            }
+            const codeCell = document.createElement('td');
+            codeCell.textContent = permission.code;
+            const scopeCell = document.createElement('td');
+            const scope = document.createElement('select');
+            scope.className = 'form-select form-select-sm permission-scope';
+            scope.dataset.permissionCode = permission.code;
+            for (const [value, label] of [['task', '本人任务'], ['project', '所在项目'], ['managed_projects', '负责项目'], ['all', '全部数据']]) {
+                const option = document.createElement('option');
+                option.value = value;
+                option.textContent = label;
+                option.selected = (grantMap.get(permission.code) || 'task') === value;
+                scope.appendChild(option);
+            }
+            scope.disabled = !toggle.checked;
+            toggle.addEventListener('change', () => { scope.disabled = !toggle.checked; });
+            scopeCell.appendChild(scope);
+            row.append(toggleCell, labelCell, codeCell, scopeCell);
+            return row;
+        }));
+    }
+
+    function selectedPermissions() {
+        return Array.from(elements.permissions.querySelectorAll('.permission-toggle:checked'), (toggle) => {
+            const scope = elements.permissions.querySelector(`.permission-scope[data-permission-code="${CSS.escape(toggle.dataset.permissionCode)}"]`);
+            return {permission_code: toggle.dataset.permissionCode, data_scope: scope.value};
+        });
+    }
+
+    function openRoleEditor(role = null) {
+        elements.form.reset();
+        showError(elements.formError);
+        elements.id.value = role?.id || '';
+        elements.title.textContent = role ? '编辑角色权限' : '新建角色';
+        elements.code.value = role?.code || '';
+        elements.code.disabled = Boolean(role);
+        elements.name.value = role?.name || '';
+        elements.description.value = role?.description || '';
+        renderPermissionEditor(role?.permissions || []);
+        editor.show();
+    }
+
+    function renderRoles() {
+        if (!roles.length) {
+            const row = document.createElement('tr');
+            row.innerHTML = '<td colspan="5" class="text-center text-muted py-4">暂无角色</td>';
+            elements.body.replaceChildren(row);
+            return;
+        }
+        elements.body.replaceChildren(...roles.map((role) => {
+            const row = document.createElement('tr');
+            for (const value of [role.name, role.code, String((role.permissions || []).length), role.is_enabled ? '启用' : '停用']) {
+                const cell = document.createElement('td');
+                cell.textContent = value;
+                row.appendChild(cell);
+            }
+            const actions = document.createElement('td');
+            const group = document.createElement('div');
+            group.className = 'd-flex gap-1';
+            group.append(
+                actionButton('bi-pencil', '编辑权限', 'btn-outline-light', () => openRoleEditor(role)),
+                actionButton('bi-copy', '克隆角色', 'btn-outline-info', () => cloneRole(role)),
+                actionButton(
+                    role.is_enabled ? 'bi-toggle-off' : 'bi-toggle-on',
+                    role.is_enabled ? '停用角色' : '启用角色',
+                    role.is_enabled ? 'btn-outline-warning' : 'btn-outline-success',
+                    () => window.toggleRoleStatus(role),
+                    role.is_system,
+                ),
+            );
+            actions.appendChild(group);
+            row.appendChild(actions);
+            return row;
+        }));
+    }
+
+    async function loadRolesAndPermissions() {
+        showError(elements.error);
+        try {
+            const [rolesResponse, permissionsResponse] = await Promise.all([
+                window.aiworkerApi.request('/api/admin/roles'),
+                window.aiworkerApi.request('/api/admin/permissions'),
+            ]);
+            if (!rolesResponse.ok) throw new Error(await responseError(rolesResponse, '角色加载失败'));
+            if (!permissionsResponse.ok) throw new Error(await responseError(permissionsResponse, '权限目录加载失败'));
+            roles = await rolesResponse.json();
+            permissionCatalog = await permissionsResponse.json();
+            renderRoles();
+        } catch (error) {
+            showError(elements.error, error.message || '角色加载失败');
+        }
+    }
+
+    window.toggleRoleStatus = async function toggleRoleStatus(role) {
+        try {
+            const response = await window.aiworkerApi.request(`/api/admin/roles/${role.id}`, {
+                method: 'PATCH',
+                headers: {'Content-Type': 'application/json'},
+                body: JSON.stringify({is_enabled: !role.is_enabled}),
+            });
+            if (!response.ok) throw new Error(await responseError(response, '角色状态更新失败'));
+            await loadRolesAndPermissions();
+        } catch (error) {
+            showError(elements.error, error.message || '角色状态更新失败');
+        }
+    };
+
+    async function cloneRole(role) {
+        const code = window.prompt('新角色代码');
+        if (!code) return;
+        const name = window.prompt('新角色名称', `${role.name}副本`);
+        if (!name) return;
+        try {
+            const response = await window.aiworkerApi.request(`/api/admin/roles/${role.id}/clone`, {
+                method: 'POST',
+                headers: {'Content-Type': 'application/json'},
+                body: JSON.stringify({code: code.trim(), name: name.trim()}),
+            });
+            if (!response.ok) throw new Error(await responseError(response, '角色克隆失败'));
+            await loadRolesAndPermissions();
+        } catch (error) {
+            showError(elements.error, error.message || '角色克隆失败');
+        }
+    }
+
+    elements.form.addEventListener('submit', async (event) => {
+        event.preventDefault();
+        showError(elements.formError);
+        const roleId = elements.id.value;
+        const payload = {
+            name: elements.name.value.trim(),
+            description: elements.description.value.trim(),
+            permissions: selectedPermissions(),
+        };
+        if (!roleId) payload.code = elements.code.value.trim();
+        try {
+            const response = await window.aiworkerApi.request(
+                roleId ? `/api/admin/roles/${roleId}` : '/api/admin/roles',
+                {
+                    method: roleId ? 'PATCH' : 'POST',
+                    headers: {'Content-Type': 'application/json'},
+                    body: JSON.stringify(payload),
+                },
+            );
+            if (!response.ok) throw new Error(await responseError(response, roleId ? '角色更新失败' : '角色创建失败'));
+            editor.hide();
+            await loadRolesAndPermissions();
+        } catch (error) {
+            showError(elements.formError, error.message || '角色保存失败');
+        }
+    });
+
+    document.getElementById('create-role')?.addEventListener('click', () => openRoleEditor());
+    loadRolesAndPermissions();
+})();
diff --git a/static/js/admin/users.js b/static/js/admin/users.js
new file mode 100644
index 0000000..a06961a
--- /dev/null
+++ b/static/js/admin/users.js
@@ -0,0 +1,194 @@
+(function () {
+    'use strict';
+
+    const elements = {
+        body: document.getElementById('user-list'),
+        error: document.getElementById('user-error'),
+        formError: document.getElementById('user-form-error'),
+        form: document.getElementById('user-editor-form'),
+        title: document.getElementById('user-editor-title'),
+        id: document.getElementById('user-id'),
+        username: document.getElementById('user-username'),
+        displayName: document.getElementById('user-display-name'),
+        password: document.getElementById('user-password'),
+        passwordField: document.getElementById('user-password-field'),
+        roles: document.getElementById('user-role-select'),
+    };
+    if (!elements.body) return;
+
+    const editor = bootstrap.Modal.getOrCreateInstance(document.getElementById('user-editor-modal'));
+    let users = [];
+    let roles = [];
+
+    function showError(target, message = '') {
+        target.textContent = message;
+        target.classList.toggle('d-none', !message);
+    }
+
+    async function responseError(response, fallback) {
+        const data = await response.json().catch(() => ({}));
+        return data.detail || data.message || fallback;
+    }
+
+    function selectedRoleIds() {
+        return Array.from(elements.roles.selectedOptions, (option) => option.value);
+    }
+
+    function actionButton(icon, label, style, handler) {
+        const button = document.createElement('button');
+        button.type = 'button';
+        button.className = `btn btn-sm ${style}`;
+        button.title = label;
+        button.setAttribute('aria-label', label);
+        button.innerHTML = `<i class="bi ${icon}"></i>`;
+        button.addEventListener('click', handler);
+        return button;
+    }
+
+    function renderRoleOptions(selectedCodes = []) {
+        const selected = new Set(selectedCodes);
+        elements.roles.replaceChildren(...roles.map((role) => {
+            const option = document.createElement('option');
+            option.value = role.id;
+            option.textContent = `${role.name} (${role.code})`;
+            option.selected = selected.has(role.code);
+            return option;
+        }));
+    }
+
+    function openUserEditor(user = null) {
+        elements.form.reset();
+        showError(elements.formError);
+        elements.id.value = user?.id || '';
+        elements.title.textContent = user ? '编辑用户' : '新建用户';
+        elements.username.value = user?.username || '';
+        elements.username.disabled = Boolean(user);
+        elements.displayName.value = user?.display_name || '';
+        elements.password.required = !user;
+        elements.passwordField.classList.toggle('d-none', Boolean(user));
+        renderRoleOptions(user?.role_codes || []);
+        editor.show();
+    }
+
+    function renderUsers() {
+        if (!users.length) {
+            const row = document.createElement('tr');
+            row.innerHTML = '<td colspan="6" class="text-center text-muted py-4">暂无用户</td>';
+            elements.body.replaceChildren(row);
+            return;
+        }
+        elements.body.replaceChildren(...users.map((user) => {
+            const row = document.createElement('tr');
+            const values = [
+                user.username,
+                user.display_name,
+                (user.role_codes || []).join('、') || '-',
+                user.status === 'active' ? '启用' : '停用',
+                user.last_login_at || '-',
+            ];
+            values.forEach((value) => {
+                const cell = document.createElement('td');
+                cell.textContent = value;
+                row.appendChild(cell);
+            });
+            const actions = document.createElement('td');
+            const group = document.createElement('div');
+            group.className = 'd-flex gap-1';
+            group.append(
+                actionButton('bi-pencil', '编辑用户', 'btn-outline-light', () => openUserEditor(user)),
+                actionButton(
+                    user.status === 'active' ? 'bi-person-dash' : 'bi-person-check',
+                    user.status === 'active' ? '停用用户' : '启用用户',
+                    user.status === 'active' ? 'btn-outline-warning' : 'btn-outline-success',
+                    () => window.toggleUserStatus(user),
+                ),
+                actionButton('bi-key', '重置密码', 'btn-outline-info', () => resetUserPassword(user)),
+            );
+            actions.appendChild(group);
+            row.appendChild(actions);
+            return row;
+        }));
+    }
+
+    async function loadUsersAndRoles() {
+        showError(elements.error);
+        try {
+            const [usersResponse, rolesResponse] = await Promise.all([
+                window.aiworkerApi.request('/api/admin/users'),
+                window.aiworkerApi.request('/api/admin/roles'),
+            ]);
+            if (!usersResponse.ok) throw new Error(await responseError(usersResponse, '用户加载失败'));
+            if (!rolesResponse.ok) throw new Error(await responseError(rolesResponse, '角色加载失败'));
+            users = await usersResponse.json();
+            roles = await rolesResponse.json();
+            renderUsers();
+        } catch (error) {
+            showError(elements.error, error.message || '用户加载失败');
+        }
+    }
+
+    window.toggleUserStatus = async function toggleUserStatus(user) {
+        const status = user.status === 'active' ? 'disabled' : 'active';
+        try {
+            const response = await window.aiworkerApi.request(`/api/admin/users/${user.id}`, {
+                method: 'PATCH',
+                headers: {'Content-Type': 'application/json'},
+                body: JSON.stringify({status}),
+            });
+            if (!response.ok) throw new Error(await responseError(response, '状态更新失败'));
+            await loadUsersAndRoles();
+        } catch (error) {
+            showError(elements.error, error.message || '状态更新失败');
+        }
+    };
+
+    async function resetUserPassword(user) {
+        const password = window.prompt(`为 ${user.username} 设置新密码`);
+        if (!password) return;
+        try {
+            const response = await window.aiworkerApi.request(`/api/admin/users/${user.id}/reset-password`, {
+                method: 'POST',
+                headers: {'Content-Type': 'application/json'},
+                body: JSON.stringify({password}),
+            });
+            if (!response.ok) throw new Error(await responseError(response, '密码重置失败'));
+            showError(elements.error);
+            window.alert('密码已重置，该用户现有会话已退出');
+        } catch (error) {
+            showError(elements.error, error.message || '密码重置失败');
+        }
+    }
+
+    elements.form.addEventListener('submit', async (event) => {
+        event.preventDefault();
+        showError(elements.formError);
+        const userId = elements.id.value;
+        const role_ids = selectedRoleIds();
+        const payload = userId
+            ? {display_name: elements.displayName.value.trim(), role_ids}
+            : {
+                username: elements.username.value.trim(),
+                display_name: elements.displayName.value.trim(),
+                password: elements.password.value,
+                role_ids,
+            };
+        try {
+            const response = await window.aiworkerApi.request(
+                userId ? `/api/admin/users/${userId}` : '/api/admin/users',
+                {
+                    method: userId ? 'PATCH' : 'POST',
+                    headers: {'Content-Type': 'application/json'},
+                    body: JSON.stringify(payload),
+                },
+            );
+            if (!response.ok) throw new Error(await responseError(response, userId ? '用户更新失败' : '用户创建失败'));
+            editor.hide();
+            await loadUsersAndRoles();
+        } catch (error) {
+            showError(elements.formError, error.message || '用户保存失败');
+        }
+    });
+
+    document.getElementById('create-user')?.addEventListener('click', () => openUserEditor());
+    loadUsersAndRoles();
+})();
diff --git a/static/js/admin/workspace.js b/static/js/admin/workspace.js
new file mode 100644
index 0000000..c2562ab
--- /dev/null
+++ b/static/js/admin/workspace.js
@@ -0,0 +1,714 @@
+window.aiworkerModules = window.aiworkerModules || [];
+window.aiworkerModules.push(() => ({
+                async fetchFiles() {
+                    try {
+                        const res = await aiworkerApi.request(`/api/files?filter=${this.fileFilter}`);
+                        this.files = await res.json();
+                        this.selectedFilePaths = this.selectedFilePaths.filter(path =>
+                            this.files.some(file => file.path === path)
+                        );
+                        this.applyFileSearch();
+                    } catch (e) {
+                        this.files = [];
+                        this.filteredFiles = [];
+                        this.selectedFilePaths = [];
+                    }
+                },
+
+                scrollToCrawlerSettings() {
+                    document.getElementById('crawlerSettings')?.scrollIntoView({behavior: 'smooth', block: 'start'});
+                },
+
+                selectedJobPlatform() {
+                    const job = this.jobs.find(item => item.job_id === this.selectedJobId);
+                    if (job && ['dy', 'hg'].includes(job.platform)) return job.platform;
+                    return 'dy';
+                },
+
+                async syncAnalyticsJob() {
+                    if (!this.selectedJobId) {
+                        this.analyticsError = '请选择一个 Job';
+                        return;
+                    }
+                    this.analyticsLoading = true;
+                    this.analyticsError = '';
+                    this.analyticsSyncResult = null;
+                    try {
+                        const res = await aiworkerApi.request('/api/analytics/sync', {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify({
+                                job_id: this.selectedJobId,
+                                platform: this.selectedJobPlatform(),
+                                rebuild: true
+                            })
+                        });
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || data.message || '同步失败');
+                        this.analyticsSyncResult = data;
+                        await this.reloadAnalyticsDashboard();
+                    } catch (e) {
+                        this.analyticsError = e.message || '同步失败';
+                    } finally {
+                        this.analyticsLoading = false;
+                    }
+                },
+
+                async fetchAnalyticsAssets() {
+                    try {
+                        const res = await aiworkerApi.request('/api/analytics/assets');
+                        this.analyticsAssets = await res.json();
+                    } catch (e) {
+                        this.analyticsAssets = {overview: {}, jobs: [], platform_distribution: []};
+                    }
+                },
+
+                async refreshAnalyticsDashboard(syncCurrent = true) {
+                    this.analyticsError = '';
+                    if (syncCurrent) {
+                        this.analyticsLoading = true;
+                        try {
+                            const platforms = this.analyticsPlatform ? [this.analyticsPlatform] : ['dy', 'hg'];
+                            const res = await aiworkerApi.request('/api/analytics/sync-all', {
+                                method: 'POST',
+                                headers: {'Content-Type': 'application/json'},
+                                body: JSON.stringify({platforms})
+                            });
+                            const data = await res.json();
+                            if (!res.ok) throw new Error(data.detail || data.message || '同步失败');
+                            this.analyticsSyncResult = {
+                                status: data.status,
+                                job_id: this.analyticsPlatform ? `${this.analyticsPlatform} 全部任务 ${data.synced_jobs || 0} 个` : `全部任务 ${data.synced_jobs || 0} 个`,
+                                drama_count: data.drama_count || 0,
+                                artifact_count: data.artifact_count || 0
+                            };
+                        } catch (e) {
+                            this.analyticsError = e.message || '同步失败';
+                        } finally {
+                            this.analyticsLoading = false;
+                        }
+                    }
+                    await this.reloadAnalyticsDashboard();
+                },
+
+                async syncAllAnalytics() {
+                    this.analyticsLoading = true;
+                    this.analyticsError = '';
+                    try {
+                        const res = await aiworkerApi.request('/api/analytics/sync-all', {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify({platforms: ['dy', 'hg']})
+                        });
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || data.message || '同步全部失败');
+                        this.analyticsSyncResult = {
+                            status: data.status,
+                            job_id: `全部任务 ${data.synced_jobs || 0} 个`,
+                            drama_count: data.drama_count || 0,
+                            artifact_count: data.artifact_count || 0
+                        };
+                        this.analyticsPlatform = '';
+                        await this.reloadAnalyticsDashboard();
+                    } catch (e) {
+                        this.analyticsError = e.message || '同步全部失败';
+                    } finally {
+                        this.analyticsLoading = false;
+                    }
+                },
+
+                async reloadAnalyticsDashboard() {
+                    const baseParams = [];
+                    if (this.analyticsPlatform) baseParams.push(`platform=${encodeURIComponent(this.analyticsPlatform)}`);
+                    const nonce = `_=${Date.now()}`;
+                    const commonSuffix = `?${[...baseParams, nonce].join('&')}`;
+                    const dramaParams = [...baseParams, nonce];
+                    if (this.analyticsDate) dramaParams.push(`date=${encodeURIComponent(this.analyticsDate)}`);
+                    const dramaSuffix = `?${dramaParams.join('&')}`;
+                    try {
+                        const [overviewRes, tagsRes, trendsRes, sentimentRes, dramasRes] = await Promise.all([
+                            aiworkerApi.request(`/api/analytics/overview${commonSuffix}`),
+                            aiworkerApi.request(`/api/analytics/tags${commonSuffix}`),
+                            aiworkerApi.request(`/api/analytics/trends${commonSuffix}&metric=heat`),
+                            aiworkerApi.request(`/api/analytics/sentiment${commonSuffix}`),
+                            aiworkerApi.request(`/api/analytics/dramas${dramaSuffix}&sort=heat&limit=30`)
+                        ]);
+                        this.analyticsOverview = await overviewRes.json();
+                        this.analyticsTags = await tagsRes.json();
+                        this.analyticsTrends = await trendsRes.json();
+                        const sentiment = await sentimentRes.json();
+                        const dramas = await dramasRes.json();
+                        this.analyticsSentiment = sentiment.items || [];
+                        this.analyticsDramas = dramas.items || [];
+                        this.$nextTick(() => {
+                            this.renderAnalyticsCharts();
+                            setTimeout(() => this.resizeAnalyticsCharts(), 80);
+                        });
+                    } catch (e) {
+                        this.analyticsOverview = {};
+                        this.analyticsTags = {categories: [], selling_points: [], hooks: [], plot_tags: []};
+                        this.analyticsTrends = {points: []};
+                        this.analyticsSentiment = [];
+                        this.analyticsDramas = [];
+                    }
+                },
+
+                renderAnalyticsCharts() {
+                    if (!window.echarts) return;
+                    this.renderPieChart('categoryChart', this.analyticsTags.categories || [], '题材');
+                    this.renderSentimentChart();
+                    this.renderTrendChart();
+                },
+
+                resizeAnalyticsCharts() {
+                    ['categoryChart', 'trendChart', 'sentimentChart'].forEach(id => {
+                        const el = document.getElementById(id);
+                        if (!el || !echarts.getInstanceByDom(el)) return;
+                        echarts.getInstanceByDom(el).resize();
+                    });
+                },
+
+                renderPieChart(elementId, rows, name) {
+                    const el = document.getElementById(elementId);
+                    if (!el) return;
+                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
+                    chart.clear();
+                    chart.setOption({
+                        backgroundColor: 'transparent',
+                        tooltip: {trigger: 'item'},
+                        series: [{
+                            name,
+                            type: 'pie',
+                            radius: ['40%', '70%'],
+                            data: rows.slice(0, 12).map(item => ({name: item.tag || '-', value: item.count || 0})),
+                            label: {color: '#f1f4f8'}
+                        }]
+                    });
+                },
+
+                renderBarChart(elementId, rows, name) {
+                    const el = document.getElementById(elementId);
+                    if (!el) return;
+                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
+                    chart.clear();
+                    const data = rows.slice(0, 10).reverse();
+                    chart.setOption({
+                        backgroundColor: 'transparent',
+                        tooltip: {trigger: 'axis'},
+                        grid: {left: 80, right: 20, top: 20, bottom: 30},
+                        xAxis: {type: 'value', axisLabel: {color: '#9aa4b2'}},
+                        yAxis: {type: 'category', data: data.map(item => this.shortLabel(item.tag || '-')), axisLabel: {color: '#f1f4f8', width: 72, overflow: 'truncate'}},
+                        series: [{name, type: 'bar', data: data.map(item => item.count || 0), itemStyle: {color: '#3b95f6'}}]
+                    });
+                },
+
+                renderSentimentChart() {
+                    const el = document.getElementById('sentimentChart');
+                    if (!el) return;
+                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
+                    chart.clear();
+                    const labels = {positive: '正面', neutral: '中性', negative: '负面'};
+                    chart.setOption({
+                        backgroundColor: 'transparent',
+                        tooltip: {trigger: 'item'},
+                        series: [{
+                            type: 'pie',
+                            radius: '65%',
+                            data: (this.analyticsSentiment || []).map(item => ({name: labels[item.sentiment] || item.sentiment, value: item.count || 0})),
+                            label: {color: '#f1f4f8'}
+                        }]
+                    });
+                },
+
+                renderTrendChart() {
+                    const el = document.getElementById('trendChart');
+                    if (!el) return;
+                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
+                    chart.clear();
+                    const points = this.analyticsTrends.points || [];
+                    chart.setOption({
+                        backgroundColor: 'transparent',
+                        tooltip: {trigger: 'axis'},
+                        grid: {left: 55, right: 20, top: 20, bottom: 35},
+                        xAxis: {type: 'category', data: points.map(item => item.date), axisLabel: {color: '#9aa4b2'}},
+                        yAxis: {type: 'value', axisLabel: {color: '#9aa4b2'}},
+                        series: [{name: this.analyticsTrends.metric || 'value', type: 'line', smooth: true, data: points.map(item => item.value || 0), itemStyle: {color: '#20c997'}, areaStyle: {opacity: .12}}]
+                    });
+                },
+
+                applyFileSearch() {
+                    const query = String(this.fileSearch || '').trim().toLowerCase();
+                    if (!query) {
+                        this.filteredFiles = this.files;
+                        return;
+                    }
+                    this.filteredFiles = this.files.filter(file =>
+                        String(file.name || '').toLowerCase().includes(query) ||
+                        String(file.path || '').toLowerCase().includes(query)
+                    );
+                },
+
+                groupedFiles() {
+                    const groupOrder = ['视频', '音频', '剧本与文稿', '转写数据', '元数据', '其他文件'];
+                    const dramas = new Map();
+                    for (const file of this.filteredFiles) {
+                        const dramaName = file.drama_name || '其他数据';
+                        if (!dramas.has(dramaName)) dramas.set(dramaName, {drama_name: dramaName, count: 0, groups: new Map()});
+                        const drama = dramas.get(dramaName);
+                        const typeName = file.file_group || '其他文件';
+                        if (!drama.groups.has(typeName)) drama.groups.set(typeName, []);
+                        drama.groups.get(typeName).push(file);
+                        drama.count += 1;
+                    }
+                    return [...dramas.values()]
+                        .sort((left, right) => left.drama_name === '其他数据' ? 1 : (right.drama_name === '其他数据' ? -1 : 0))
+                        .map(group => ({
+                            drama_name: group.drama_name,
+                            count: group.count,
+                            file_groups: [...group.groups.entries()]
+                                .sort((left, right) => groupOrder.indexOf(left[0]) - groupOrder.indexOf(right[0]))
+                                .map(([name, files]) => ({name, files})),
+                        }));
+                },
+
+                isFileSelected(path) {
+                    return this.selectedFilePaths.includes(path);
+                },
+
+                allVisibleFilesSelected() {
+                    return this.filteredFiles.length > 0 && this.filteredFiles.every(file => this.isFileSelected(file.path));
+                },
+
+                startBatchDelete() {
+                    this.selectedFilePaths = [];
+                    this.batchDeleteMode = true;
+                },
+
+                cancelBatchDelete() {
+                    this.selectedFilePaths = [];
+                    this.batchDeleteMode = false;
+                },
+
+                toggleFileSelection(path, selected) {
+                    if (selected && !this.isFileSelected(path)) {
+                        this.selectedFilePaths.push(path);
+                    }
+                    if (!selected) {
+                        this.selectedFilePaths = this.selectedFilePaths.filter(item => item !== path);
+                    }
+                },
+
+                toggleAllVisibleFiles(selected) {
+                    const visiblePaths = this.filteredFiles.map(file => file.path);
+                    if (selected) {
+                        this.selectedFilePaths = [...new Set([...this.selectedFilePaths, ...visiblePaths])];
+                    } else {
+                        this.selectedFilePaths = this.selectedFilePaths.filter(path => !visiblePaths.includes(path));
+                    }
+                },
+
+                async deleteSelectedFiles() {
+                    await this.deleteFiles(this.selectedFilePaths);
+                },
+
+                async deleteFiles(paths) {
+                    const selectedPaths = [...new Set(paths || [])];
+                    if (!selectedPaths.length) return;
+                    const label = selectedPaths.length === 1 ? '该文件' : `已选的 ${selectedPaths.length} 个文件`;
+                    if (!confirm(`确认删除${label}吗？本地文件将永久移除。`)) return;
+                    try {
+                        const res = await aiworkerApi.request('/api/files', {
+                            method: 'DELETE',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify({paths: selectedPaths})
+                        });
+                        const data = await res.json().catch(() => ({}));
+                        if (!res.ok || data.status === 'error') {
+                            alert(data.message || '删除失败，请刷新后重试');
+                            return;
+                        }
+                        const deletedPaths = data.deleted_paths || [];
+                        this.selectedFilePaths = this.selectedFilePaths.filter(path => !deletedPaths.includes(path));
+                        await this.fetchFiles();
+                        if (data.status === 'partial_success') {
+                            alert(`已删除 ${deletedPaths.length} 个文件，${(data.failed || []).length} 个文件删除失败`);
+                        } else {
+                            alert(`已删除 ${deletedPaths.length} 个文件`);
+                        }
+                    } catch (e) {
+                        alert('删除失败，请检查服务状态后重试');
+                    }
+                },
+
+                async openFilePreview(file) {
+                    const kind = file.preview_kind || 'none';
+                    this.previewFile = {status: 'loading', file, kind, url: '', content: '', message: ''};
+                    this.filePreviewModal.show();
+                    if (['video', 'audio'].includes(kind)) {
+                        this.previewFile.url = `/api/file/preview?path=${encodeURIComponent(file.path)}`;
+                        this.previewFile.status = 'success';
+                        return;
+                    }
+                    if (!['text', 'json'].includes(kind)) {
+                        this.previewFile.status = 'error';
+                        this.previewFile.message = '该文件格式暂不支持在线预览';
+                        return;
+                    }
+                    try {
+                        const res = await aiworkerApi.request(`/api/file/content?path=${encodeURIComponent(file.path)}`);
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.message || '读取文件失败');
+                        this.previewFile.content = data.content || '';
+                        this.previewFile.status = 'success';
+                    } catch (e) {
+                        this.previewFile.status = 'error';
+                        this.previewFile.message = e.message || '读取文件失败';
+                    }
+                },
+
+                closeFilePreview() {
+                    this.previewFile = {status: 'idle', file: null, kind: '', url: '', content: '', message: ''};
+                },
+
+                async analyzeAudio(file) {
+                    this.audioAnalysis = {status: 'loading', file_name: file.name};
+                    this.audioModal.show();
+                    try {
+                        const res = await aiworkerApi.request('/api/audio/analyze', {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify({file_path: file.path})
+                        });
+                        const data = await res.json();
+                        if (!res.ok || data.status === 'error') {
+                            this.audioAnalysis = {status: 'error', message: data.message || '分析失败'};
+                            return;
+                        }
+                        this.audioAnalysis = data;
+                    } catch (e) {
+                        this.audioAnalysis = {status: 'error', message: '分析失败'};
+                    }
+                },
+
+                downloadAudioAnalysis() {
+                    if (this.audioAnalysis.status !== 'success') return;
+                    const lines = [
+                        `文件：${this.audioAnalysis.file_name}`,
+                        `时间：${this.audioAnalysis.analysis_time}`,
+                        '',
+                        '## 摘要',
+                        this.audioAnalysis.summary || '',
+                        '',
+                        '## 关键点',
+                        ...(this.audioAnalysis.key_points || []).map(item => `- ${item}`),
+                        '',
+                        '## 转写文本',
+                        this.audioAnalysis.transcript || ''
+                    ];
+                    const blob = new Blob([lines.join('\n')], {type: 'text/markdown;charset=utf-8'});
+                    const url = URL.createObjectURL(blob);
+                    const link = document.createElement('a');
+                    const stem = String(this.audioAnalysis.file_name || 'audio').replace(/\.[^.]+$/, '');
+                    link.href = url;
+                    link.download = `${stem}_analysis.md`;
+                    document.body.appendChild(link);
+                    link.click();
+                    document.body.removeChild(link);
+                    URL.revokeObjectURL(url);
+                },
+
+                async fetchConfigForm() {
+                    try {
+                        const res = await aiworkerApi.request('/api/config/form');
+                        const data = await res.json();
+                        if (data.status === 'error') {
+                            alert(data.message || '加载配置失败');
+                            return;
+                        }
+                        this.configForm = {
+                            root: data.root || {},
+                            items: (data.items || []).map(item => this.normalizeConfigItem(item))
+                        };
+                    } catch (e) {
+                        alert('加载配置失败');
+                    }
+                },
+
+                normalizeConfigItem(item = {}) {
+                    return {
+                        project: item.project || '',
+                        '评论不少于': Number(item['评论不少于'] || 0),
+                        '搜索关键词': item['搜索关键词'] || '',
+                        '屏蔽关键词': item['屏蔽关键词'] || '',
+                        '有关视频判定': item['有关视频判定'] || '',
+                        '黑名单视频判定': item['黑名单视频判定'] || '',
+                        '作品天数': Number(item['作品天数'] || 0),
+                        '白名单地区': item['白名单地区'] || '',
+                        '黑名单地区': item['黑名单地区'] || ''
+                    };
+                },
+
+                addConfigItem() {
+                    this.configForm.items.push(this.normalizeConfigItem({
+                        '评论不少于': 3,
+                        '作品天数': 90
+                    }));
+                },
+
+                removeConfigItem(index) {
+                    if (!confirm('删除该项目配置？保存后会写入 data.json。')) return;
+                    this.configForm.items.splice(index, 1);
+                },
+
+                async saveConfigForm() {
+                    const crawlerType = String(this.configForm.root['默认爬取类型'] || '').toLowerCase();
+                    const isRank = crawlerType.includes('rank') || crawlerType.includes('榜单');
+                    if (!isRank) {
+                        if (this.configForm.items.length === 0) {
+                            alert('搜索类型至少需要一个项目配置');
+                            return;
+                        }
+                        const missing = this.configForm.items.findIndex(item => !String(item['搜索关键词'] || '').trim());
+                        if (missing >= 0) {
+                            alert(`第 ${missing + 1} 个项目缺少搜索关键词`);
+                            return;
+                        }
+                    }
+                    const res = await aiworkerApi.request('/api/config/form', {
+                        method: 'POST',
+                        headers: {'Content-Type': 'application/json'},
+                        body: JSON.stringify(this.configForm)
+                    });
+                    const data = await res.json();
+                    if (!res.ok || data.status !== 'success') {
+                        alert(data.message || '保存失败');
+                        return;
+                    }
+                    alert('保存成功');
+                    await this.loadConfig();
+                    await this.fetchConfigForm();
+                    await this.fetchBackups();
+                },
+
+                async fetchBackups() {
+                    try {
+                        const res = await aiworkerApi.request('/api/config/backups');
+                        this.backups = await res.json();
+                    } catch (e) {
+                        this.backups = [];
+                    }
+                },
+
+                async createBackup() {
+                    const res = await aiworkerApi.request('/api/config/backup/create', {method: 'POST'});
+                    const data = await res.json();
+                    if (data.status !== 'success') alert(data.message || '创建失败');
+                    await this.fetchBackups();
+                },
+
+                async restoreBackup(filename) {
+                    if (!confirm(`恢复备份 ${filename}？当前配置会被覆盖。`)) return;
+                    const res = await aiworkerApi.request('/api/config/backup/restore', {
+                        method: 'POST',
+                        headers: {'Content-Type': 'application/json'},
+                        body: JSON.stringify({filename})
+                    });
+                    const data = await res.json();
+                    if (data.status !== 'success') alert(data.message || '恢复失败');
+                    await this.fetchConfigForm();
+                    await this.loadConfig();
+                },
+
+                async deleteBackup(filename) {
+                    if (!confirm(`删除备份 ${filename}？`)) return;
+                    const res = await aiworkerApi.request('/api/config/backup/delete', {
+                        method: 'POST',
+                        headers: {'Content-Type': 'application/json'},
+                        body: JSON.stringify({filename})
+                    });
+                    const data = await res.json();
+                    if (data.status !== 'success') alert(data.message || '删除失败');
+                    await this.fetchBackups();
+                },
+
+                async openProjectModal(filePath, mode) {
+                    this.modalMode = mode;
+                    this.modalFile = filePath;
+                    this.selectedModalProjects = [];
+                    this.modalProjects = [];
+                    try {
+                        const res = await aiworkerApi.request('/api/file/analyze', {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify({file_path: filePath})
+                        });
+                        const data = await res.json();
+                        this.modalProjects = Object.entries(data)
+                            .map(([name, count]) => ({name, count}))
+                            .sort((a, b) => a.name.localeCompare(b.name));
+                        this.selectedModalProjects = this.modalProjects.map(item => item.name);
+                        this.projectModal.show();
+                    } catch (e) {
+                        alert('分析文件失败');
+                    }
+                },
+
+                moveProjectUp(index) {
+                    if (index <= 0) return;
+                    const item = this.modalProjects[index];
+                    this.modalProjects[index] = this.modalProjects[index - 1];
+                    this.modalProjects[index - 1] = item;
+                },
+
+                moveProjectDown(index) {
+                    if (index >= this.modalProjects.length - 1) return;
+                    const item = this.modalProjects[index];
+                    this.modalProjects[index] = this.modalProjects[index + 1];
+                    this.modalProjects[index + 1] = item;
+                },
+
+                async confirmBatchAction() {
+                    if (this.selectedModalProjects.length === 0) {
+                        alert('请至少选择一个项目');
+                        return;
+                    }
+                    const projects = this.modalProjects
+                        .filter(item => this.selectedModalProjects.includes(item.name))
+                        .map(item => item.name);
+                    const endpoint = this.modalMode === 'crawl' ? '/api/batch/run' : '/api/export/excel';
+                    const res = await aiworkerApi.request(endpoint, {
+                        method: 'POST',
+                        headers: {'Content-Type': 'application/json'},
+                        body: JSON.stringify({
+                            file_path: this.modalFile,
+                            projects,
+                            auto_upload: this.autoUpload
+                        })
+                    });
+                    const data = await res.json();
+                    alert(data.message || '已提交');
+                    this.projectModal.hide();
+                },
+
+                async fetchQrcodeStatus() {
+                    try {
+                        const res = await aiworkerApi.request('/api/qrcode/status');
+                        this.qrcodeStatus = await res.json();
+                    } catch (e) {}
+                },
+
+                async onLoginTypeChange() {
+                    if (this.config.login_type === 'qrcode') await this.fetchQrcodeStatus();
+                },
+
+                onPlatformChange() {
+                    if (this.config.crawler_type === 'rank' && !['dy', 'hg'].includes(this.config.platform)) {
+                        this.config.crawler_type = 'search';
+                        alert('榜单爬取仅支持抖音和红果，已切换为搜索类型');
+                    }
+                },
+
+                async startQrcodeLogin() {
+                    if (this.isRunning) {
+                        alert('爬虫正在运行，请先停止');
+                        return;
+                    }
+                    this.qrcodeLoggingIn = true;
+                    try {
+                        const res = await aiworkerApi.request('/api/qrcode/login', {method: 'POST'});
+                        const data = await res.json();
+                        if (data.status === 'success') {
+                            this.pollQrcodeStatus();
+                        } else {
+                            alert(data.message || '启动扫码失败');
+                            this.qrcodeLoggingIn = false;
+                        }
+                    } catch (e) {
+                        alert('启动扫码失败');
+                        this.qrcodeLoggingIn = false;
+                    }
+                },
+
+                pollQrcodeStatus() {
+                    const timer = setInterval(async () => {
+                        await this.fetchQrcodeStatus();
+                        if (this.qrcodeStatus.has_state) {
+                            clearInterval(timer);
+                            this.qrcodeLoggingIn = false;
+                            alert('扫码登录成功');
+                        }
+                    }, 3000);
+                    setTimeout(() => {
+                        clearInterval(timer);
+                        this.qrcodeLoggingIn = false;
+                    }, 300000);
+                },
+
+                async clearQrcodeLogin() {
+                    if (!confirm('清除保存的登录状态？')) return;
+                    const res = await aiworkerApi.request('/api/qrcode/clear', {method: 'POST'});
+                    const data = await res.json();
+                    if (data.status !== 'success') alert(data.message || '清除失败');
+                    await this.fetchQrcodeStatus();
+                },
+
+                async fetchDramaSettings() {
+                    try {
+                        const response = await aiworkerApi.request('/api/hongguo/drama/settings');
+                        const data = await response.json();
+                        if (!response.ok) throw new Error(data.message || '读取设置失败');
+                        this.dramaAnalysis.settings = data;
+                        this.dramaAnalysis.activationCode = '';
+                    } catch (error) {
+                        this.dramaAnalysis.error = error.message || '读取剧目检索设置失败';
+                    }
+                },
+
+                async saveDramaSettings() {
+                    this.dramaAnalysis.loading = true;
+                    this.dramaAnalysis.error = '';
+                    try {
+                        const payload = {config_url: this.dramaAnalysis.settings.config_url};
+                        if (this.dramaAnalysis.activationCode) payload.activation_code = this.dramaAnalysis.activationCode;
+                        const response = await aiworkerApi.request('/api/hongguo/drama/settings', {
+                            method: 'PUT',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify(payload),
+                        });
+                        const data = await response.json();
+                        if (!response.ok || data.status === 'error') throw new Error(data.message || '保存设置失败');
+                        this.dramaAnalysis.settings = data;
+                        this.dramaAnalysis.activationCode = '';
+                        alert('设置已保存');
+                    } catch (error) {
+                        this.dramaAnalysis.error = error.message || '保存设置失败';
+                    } finally {
+                        this.dramaAnalysis.loading = false;
+                    }
+                },
+
+                async testDramaInterface() {
+                    this.dramaAnalysis.loading = true;
+                    this.dramaAnalysis.error = '';
+                    this.dramaAnalysis.interfaceTest = null;
+                    try {
+                        const payload = {config_url: this.dramaAnalysis.settings.config_url};
+                        if (this.dramaAnalysis.activationCode) payload.activation_code = this.dramaAnalysis.activationCode;
+                        const response = await aiworkerApi.request('/api/hongguo/drama/test', {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify(payload),
+                        });
+                        const data = await response.json();
+                        this.dramaAnalysis.interfaceTest = {
+                            status: data.status || (response.ok ? 'success' : 'error'),
+                            message: data.message || (response.ok ? '接口可用' : '接口不可用'),
+                        };
+                    } catch (error) {
+                        this.dramaAnalysis.interfaceTest = {status: 'error', message: error.message || '接口测试失败'};
+                    } finally {
+                        this.dramaAnalysis.loading = false;
+                    }
+                },
+
+}));
diff --git a/static/js/common/api.js b/static/js/common/api.js
new file mode 100644
index 0000000..f15ba64
--- /dev/null
+++ b/static/js/common/api.js
@@ -0,0 +1,46 @@
+(function () {
+    'use strict';
+
+    const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
+
+    class PermissionError extends Error {
+        constructor(message = '无权执行此操作') {
+            super(message);
+            this.name = 'PermissionError';
+        }
+    }
+
+    function csrfToken() {
+        return document.querySelector('meta[name="csrf-token"]')?.content || '';
+    }
+
+    async function request(input, options = {}) {
+        const method = String(options.method || 'GET').toUpperCase();
+        const headers = new Headers(options.headers || {});
+        if (WRITE_METHODS.has(method)) {
+            const token = csrfToken();
+            if (token) headers.set('X-CSRF-Token', token);
+        }
+
+        const response = await fetch(input, {
+            ...options,
+            method,
+            headers,
+            credentials: options.credentials || 'same-origin',
+        });
+        if (response.status === 401) {
+            window.location.assign('/login');
+            throw new Error('登录状态已失效');
+        }
+        if (response.status === 403) {
+            const error = new PermissionError();
+            window.dispatchEvent(new CustomEvent('aiworker:permission-denied', {
+                detail: {message: error.message},
+            }));
+            throw error;
+        }
+        return response;
+    }
+
+    window.aiworkerApi = {PermissionError, request};
+})();
diff --git a/static/js/common/creative.js b/static/js/common/creative.js
new file mode 100644
index 0000000..55c48a1
--- /dev/null
+++ b/static/js/common/creative.js
@@ -0,0 +1,543 @@
+window.aiworkerModules = window.aiworkerModules || [];
+window.aiworkerModules.push(() => ({
+                creativeTopicOptionLabel(topic) {
+                    if (!topic?.latest_breakdown_id) return `${topic.title}（未拆解）`;
+                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
+                    if (!topic.locked_breakdown_id) return `${topic.title}（草稿${version}，需先锁定）`;
+                    if (topic.latest_breakdown_status === 'draft') return `${topic.title}（已有锁定版，最新草稿${version}）`;
+                    return `${topic.title}（已锁定${version}）`;
+                },
+
+                async fetchCreativeProjects(selectId = null) {
+                    this.creativeWorkspace.loading = true;
+                    this.creativeWorkspace.error = '';
+                    try {
+                        const [projectsResponse, topicsResponse] = await Promise.all([
+                            aiworkerApi.request('/api/creative/projects'),
+                            aiworkerApi.request('/api/creative/topics?status=adopted&limit=500')
+                        ]);
+                        const projectsData = await projectsResponse.json();
+                        const topicsData = await topicsResponse.json();
+                        if (!projectsResponse.ok) throw new Error(projectsData.detail || '读取创作项目失败');
+                        if (!topicsResponse.ok) throw new Error(topicsData.detail || '读取可用选题失败');
+                        this.creativeWorkspace.projects = projectsData.items || [];
+                        this.creativeWorkspace.eligibleTopics = topicsData.items || [];
+                        const currentId = this.creativeWorkspace.project?.id;
+                        const targetId = selectId
+                            || (this.creativeWorkspace.projects.some(item => item.id === currentId) ? currentId : this.creativeWorkspace.projects[0]?.id);
+                        if (targetId) await this.selectCreativeProject(targetId);
+                        else this.creativeWorkspace.project = null;
+                    } catch (e) {
+                        this.creativeWorkspace.error = e.message || '读取创作项目失败';
+                    } finally {
+                        this.creativeWorkspace.loading = false;
+                    }
+                },
+
+                async loadCreativeProjectBreakdowns(topicId) {
+                    const response = await aiworkerApi.request(`/api/creative/topics/${topicId}/breakdowns?status=locked`);
+                    const data = await response.json();
+                    if (!response.ok) throw new Error(data.detail || '读取拆解版本失败');
+                    this.creativeWorkspace.lockedBreakdowns = data.items || [];
+                    const selected = Number(this.creativeWorkspace.create.breakdown_id);
+                    if (!this.creativeWorkspace.lockedBreakdowns.some(item => Number(item.id) === selected)) {
+                        this.creativeWorkspace.create.breakdown_id = this.creativeWorkspace.lockedBreakdowns[0]?.id || '';
+                    }
+                },
+
+                async syncCreativeProjectSource() {
+                    const topic = this.creativeWorkspace.eligibleTopics.find(item => Number(item.id) === Number(this.creativeWorkspace.create.topic_id));
+                    if (!topic) {
+                        this.creativeWorkspace.lockedBreakdowns = [];
+                        this.creativeWorkspace.create.breakdown_id = '';
+                        return;
+                    }
+                    try {
+                        await this.loadCreativeProjectBreakdowns(topic.id);
+                    } catch (e) {
+                        this.creativeWorkspace.lockedBreakdowns = [];
+                        this.creativeWorkspace.create.breakdown_id = '';
+                        this.creativeWorkspace.error = e.message || '读取拆解版本失败';
+                        return;
+                    }
+                    if (!this.creativeWorkspace.create.name) this.creativeWorkspace.create.name = `${topic.title}创作项目`;
+                },
+
+                async openCreativeProjectFromTopic(topic) {
+                    if (topic.status !== 'adopted' || !topic.locked_breakdown_id) {
+                        this.topicLibrary.error = '请先采纳选题并锁定拆解版本';
+                        return;
+                    }
+                    window.location.assign(`/planning/projects?topic_id=${encodeURIComponent(topic.id)}`);
+                },
+
+                async createCreativeProject() {
+                    await this.syncCreativeProjectSource();
+                    const source = this.creativeWorkspace.create;
+                    if (!source.topic_id || !source.breakdown_id) return;
+                    try {
+                        const res = await aiworkerApi.request('/api/creative/projects', {
+                            method: 'POST', headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify(source)
+                        });
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '创建项目失败');
+                        this.creativeWorkspace.create = {topic_id: '', breakdown_id: '', name: '', target_platform: '', target_episode_count: 20};
+                        window.location.assign(`/planning/projects/${data.id}/directions`);
+                    } catch (e) {
+                        this.creativeWorkspace.error = e.message || '创建项目失败';
+                    }
+                },
+
+                async selectCreativeProject(projectId) {
+                    try {
+                        const res = await aiworkerApi.request(`/api/creative/projects/${projectId}`);
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '读取项目失败');
+                        this.creativeWorkspace.project = data;
+                        this.reconcileCreativeGenerationState(data);
+                        const candidates = this.latestProjectBlueprints();
+                        if (!candidates.some(item => item.id === this.creativeWorkspace.selectedBlueprintId)) {
+                            this.creativeWorkspace.selectedBlueprintId = data.locked_brief_id || candidates[0]?.id || null;
+                        }
+                        this.creativeWorkspace.selectedEpisode = Math.min(
+                            Math.max(1, Number(this.creativeWorkspace.selectedEpisode || 1)),
+                            Number(data.target_episode_count || 1)
+                        );
+                    } catch (e) {
+                        this.creativeWorkspace.error = e.message || '读取项目失败';
+                    }
+                },
+
+                creativeProjectStatusLabel(status) {
+                    return {
+                        created: '待生成方向', direction_review: '方向评审', brief_locked: 'Brief 已锁定',
+                        characters_draft: '人物草稿', characters_locked: '人物已锁定', outline_draft: '大纲草稿',
+                        outline_locked: '大纲已锁定', beats_in_progress: 'Beats 制作中', archived: '已归档'
+                    }[status] || status || '-';
+                },
+
+                riskLabel(level) { return {low: '低', medium: '中', high: '高'}[level] || level || '-'; },
+                riskBadgeClass(level) { return level === 'high' ? 'bg-danger' : level === 'medium' ? 'bg-warning text-dark' : 'bg-success'; },
+                listText(value) { return Array.isArray(value) ? value.join('\n') : String(value || ''); },
+                setListField(target, key, value) { target[key] = String(value || '').split(/[,，\n]/).map(item => item.trim()).filter(Boolean); },
+                textareaRows(value, charactersPerLine = 28) {
+                    const lines = String(value || '').split('\n');
+                    return Math.max(2, lines.reduce((total, line) => total + Math.max(1, Math.ceil(line.length / charactersPerLine)), 0));
+                },
+                creativeItemSaveLabel(item, saveKey) {
+                    const state = this.creativeWorkspace.saveState[saveKey];
+                    if (state === 'saving') return '保存中';
+                    return item?.updated_at ? '已保存' : '未保存';
+                },
+                showCreativeNotice(message, type = 'success') {
+                    clearTimeout(this.creativeWorkspace.noticeTimer);
+                    this.creativeWorkspace.notice = message;
+                    this.creativeWorkspace.noticeType = type;
+                    this.creativeWorkspace.noticeTimer = setTimeout(() => {
+                        this.creativeWorkspace.notice = '';
+                    }, 3000);
+                },
+
+                latestProjectBlueprints() {
+                    const items = this.creativeWorkspace.project?.blueprints || [];
+                    const latest = new Map();
+                    items.forEach(item => {
+                        const current = latest.get(item.candidate_group_id);
+                        if (!current || Number(item.version) > Number(current.version)) latest.set(item.candidate_group_id, item);
+                    });
+                    return [...latest.values()];
+                },
+
+                selectedProjectBlueprint() {
+                    return (this.creativeWorkspace.project?.blueprints || []).find(item => item.id === this.creativeWorkspace.selectedBlueprintId) || null;
+                },
+
+                latestCharacterBatchId() {
+                    const project = this.creativeWorkspace.project;
+                    if (!project) return '';
+                    if (project.character_batch_id) return project.character_batch_id;
+                    const items = project.characters || [];
+                    return items.length ? items[items.length - 1].batch_id : '';
+                },
+
+                currentProjectCharacters() {
+                    const batch = this.latestCharacterBatchId();
+                    return (this.creativeWorkspace.project?.characters || []).filter(item => item.batch_id === batch);
+                },
+
+                currentProjectRelationships() {
+                    const batch = this.latestCharacterBatchId();
+                    return (this.creativeWorkspace.project?.relationships || []).filter(item => item.batch_id === batch);
+                },
+
+                latestOutlineBatchId() {
+                    const project = this.creativeWorkspace.project;
+                    if (!project) return '';
+                    if (project.outline_batch_id) return project.outline_batch_id;
+                    const items = (project.episodes || []).filter(item => item.stage === 'outline');
+                    return items.length ? items[items.length - 1].batch_id : '';
+                },
+
+                currentProjectOutlines() {
+                    const batch = this.latestOutlineBatchId();
+                    return (this.creativeWorkspace.project?.episodes || [])
+                        .filter(item => item.stage === 'outline' && item.batch_id === batch)
+                        .sort((a, b) => Number(a.episode_number) - Number(b.episode_number));
+                },
+
+                selectedProjectBeats() {
+                    const items = (this.creativeWorkspace.project?.episodes || [])
+                        .filter(item => item.stage === 'beats'
+                            && Number(item.episode_number) === Number(this.creativeWorkspace.selectedEpisode)
+                            && item.upstream_refs?.outline_batch_id === this.creativeWorkspace.project?.outline_batch_id)
+                        .sort((a, b) => Number(b.version) - Number(a.version));
+                    return items[0] || null;
+                },
+
+                async startCreativeGeneration(url, body = {}, action = '') {
+                    this.creativeWorkspace.error = '';
+                    this.creativeWorkspace.startingAction = action;
+                    this.creativeWorkspace.jobMessage = '正在创建生成任务';
+                    this.creativeWorkspace.jobId = '';
+                    this.creativeWorkspace.jobStatus = '';
+                    this.creativeWorkspace.jobProgress = {indeterminate: true};
+                    this.creativeWorkspace.jobStartedAt = Date.now();
+                    this.creativeWorkspace.jobElapsedSeconds = 0;
+                    this.creativeWorkspace.paused = false;
+                    this.creativeWorkspace.notifiedStatus = '';
+                    this.creativeWorkspace.activeAction = action;
+                    this.creativeGenerationProgressModal.show();
+                    try {
+                        const res = await aiworkerApi.request(url, {
+                            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
+                        });
+                        const data = await res.json();
+                        if (!res.ok || data.status === 'error') throw new Error(data.detail || data.message || '创建生成任务失败');
+                        this.creativeWorkspace.jobId = data.job.job_id;
+                        this.creativeWorkspace.jobStatus = data.job.status;
+                        this.creativeWorkspace.jobMessage = this.creativeGenerationStartedMessage(action);
+                        this.creativeWorkspace.jobProgress = data.job.progress || {};
+                        if (Number(data.job.started_at || 0) > 0) {
+                            this.creativeWorkspace.jobStartedAt = Number(data.job.started_at) * 1000;
+                        }
+                        await this.fetchJobs();
+                    } catch (e) {
+                        this.creativeWorkspace.error = e.message || '创建生成任务失败';
+                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
+                    } finally {
+                        this.creativeWorkspace.startingAction = '';
+                    }
+                },
+
+                generateProjectBlueprints() {
+                    return this.startCreativeGeneration(
+                        `/api/creative/projects/${this.creativeWorkspace.project.id}/blueprints/generate`,
+                        {count: Number(this.creativeWorkspace.directionCount || 3)},
+                        'blueprints'
+                    );
+                },
+
+                generateProjectCharacters() {
+                    return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/characters/generate`, {}, 'characters');
+                },
+
+                generateProjectOutlines() {
+                    return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/outlines/generate`, {}, 'outlines');
+                },
+
+                generateProjectBeats() {
+                    const episode = Number(this.creativeWorkspace.selectedEpisode || 0);
+                    return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/${episode}/beats/generate`, {}, 'beats');
+                },
+
+                creativeActionLabel(action) {
+                    return {
+                        blueprints: '仿制方向', characters: '人物关系', outlines: '分集大纲', beats: 'Beats 脚本'
+                    }[action] || '创作内容';
+                },
+
+                creativeGenerationStartedMessage(action) {
+                    return {
+                        blueprints: '仿制方向生成任务已发起，正在后台处理',
+                        characters: '人物关系生成任务已发起，正在后台处理',
+                        outlines: '分集大纲生成任务已发起，正在后台处理',
+                        beats: 'Beats 脚本生成任务已发起，正在后台处理'
+                    }[action] || '创作内容生成任务已发起，正在后台处理';
+                },
+
+                creativeActionRunning(action) {
+                    return this.creativeWorkspace.startingAction === action
+                        || (this.creativeWorkspace.activeAction === action && this.creativeJobRunning());
+                },
+
+                creativeActionDisabled(action) {
+                    return Boolean(this.creativeWorkspace.startingAction)
+                        || (this.creativeWorkspace.activeAction === action && this.creativeJobRunning());
+                },
+
+                creativeProjectHasJobResult(project, action, jobId) {
+                    if (!project || !action || !jobId) return false;
+                    const generatedByJob = item => item.generation_meta?.job_id === jobId;
+                    if (action === 'blueprints') return (project.blueprints || []).some(generatedByJob);
+                    if (action === 'characters') {
+                        return (project.characters || []).some(generatedByJob)
+                            || (project.relationships || []).some(generatedByJob);
+                    }
+                    if (action === 'outlines') {
+                        return (project.episodes || []).some(item => item.stage === 'outline' && item.generation_meta?.job_id === jobId);
+                    }
+                    if (action === 'beats') {
+                        return (project.episodes || []).some(item => item.stage === 'beats' && item.generation_meta?.job_id === jobId);
+                    }
+                    return false;
+                },
+
+                finishCreativeGeneration(message = '生成完成，结果已回填到当前项目') {
+                    this.creativeWorkspace.jobStatus = 'succeeded';
+                    this.creativeWorkspace.jobMessage = message;
+                    this.creativeWorkspace.jobProgress = {completed: 1, total: 1};
+                    this.creativeWorkspace.startingAction = '';
+                    this.creativeWorkspace.activeAction = '';
+                    this.creativeWorkspace.paused = false;
+                    this.creativeWorkspace.notifiedStatus = 'succeeded';
+                },
+
+                reconcileCreativeGenerationState(project) {
+                    const action = this.creativeWorkspace.activeAction;
+                    const jobId = this.creativeWorkspace.jobId;
+                    if (!this.creativeProjectHasJobResult(project, action, jobId)) return false;
+                    this.finishCreativeGeneration();
+                    this.showCreativeNotice('生成完成，结果已回填到当前项目。');
+                    return true;
+                },
+
+                async syncCreativeJobProgress() {
+                    if (!this.creativeWorkspace.jobId) return;
+                    const job = this.jobs.find(item => item.job_id === this.creativeWorkspace.jobId);
+                    if (!job) return;
+                    this.creativeWorkspace.jobStatus = job.status;
+                    this.creativeWorkspace.jobMessage = job.message || this.creativeWorkspace.jobMessage;
+                    this.creativeWorkspace.jobProgress = job.progress || {};
+                    this.creativeWorkspace.paused = Boolean(job.paused);
+                    if (Number(job.started_at || 0) > 0) {
+                        this.creativeWorkspace.jobStartedAt = Number(job.started_at) * 1000;
+                    }
+                    this.creativeWorkspace.jobElapsedSeconds = Math.max(
+                        Number(this.creativeWorkspace.jobElapsedSeconds || 0),
+                        Number(job.progress?.elapsed_seconds || 0)
+                    );
+                    // 先尝试通过项目数据尽早检测完成（job 可能还在 running 但结果已入库）
+                    if (this.reconcileCreativeGenerationState(this.creativeWorkspace.project)) return;
+                    // job 已到终态：先刷新项目数据（让后端生成的结果可见），再尝试 reconcile
+                    if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
+                        if (this.creativeWorkspace.notifiedStatus === job.status) return;
+                        this.creativeWorkspace.notifiedStatus = job.status;
+                        if (this.creativeWorkspace.project?.id) {
+                            await this.selectCreativeProject(this.creativeWorkspace.project.id);
+                            if (this.reconcileCreativeGenerationState(this.creativeWorkspace.project)) return;
+                        }
+                        if (job.status === 'succeeded') {
+                            this.finishCreativeGeneration(job.message || '生成完成，结果已回填到当前项目');
+                            this.showCreativeNotice('生成完成，项目内容已刷新。');
+                        } else {
+                            this.creativeWorkspace.startingAction = '';
+                            this.creativeWorkspace.activeAction = '';
+                            this.creativeWorkspace.paused = false;
+                            alert(`创作任务${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || ''}`);
+                        }
+                    }
+                },
+
+                creativeJobStatusRunning() {
+                    return ['pending', 'running', 'stopping'].includes(this.creativeWorkspace.jobStatus);
+                },
+                creativeJobRunning() {
+                    if (!this.creativeJobStatusRunning()) return false;
+                    return !this.creativeProjectHasJobResult(
+                        this.creativeWorkspace.project,
+                        this.creativeWorkspace.activeAction,
+                        this.creativeWorkspace.jobId
+                    );
+                },
+                creativeJobRetryable() { return ['failed', 'cancelled'].includes(this.creativeWorkspace.jobStatus); },
+                creativeProgressIndeterminate() {
+                    const progress = this.creativeWorkspace.jobProgress || {};
+                    return Boolean(this.creativeWorkspace.startingAction || this.creativeJobRunning())
+                        && (progress.indeterminate || (Number(progress.completed || 0) === 0 && Number(progress.total || 0) <= 1));
+                },
+                creativeProgressPercent() {
+                    const progress = this.creativeWorkspace.jobProgress || {};
+                    const total = Number(progress.total || 0);
+                    return total ? Math.min(100, Math.round(Number(progress.completed || 0) * 100 / total)) : 0;
+                },
+                creativeProgressText() {
+                    const progress = this.creativeWorkspace.jobProgress || {};
+                    if (this.creativeWorkspace.startingAction && !this.creativeWorkspace.jobId) return '正在启动';
+                    if (this.creativeProgressIndeterminate()) return this.creativeWorkspace.paused ? '已暂停' : '处理中';
+                    return progress.total ? `${progress.completed || 0}/${progress.total}` : this.creativeWorkspace.jobStatus;
+                },
+                creativeProgressElapsedText() {
+                    const seconds = Number(this.creativeWorkspace.jobElapsedSeconds || 0);
+                    const prefix = this.creativeWorkspace.startingAction || this.creativeJobRunning() ? '已等待' : '用时';
+                    return `${prefix} ${this.formatBreakdownGenerationElapsed(seconds)}`;
+                },
+                tickCreativeGenerationElapsed() {
+                    if (!this.creativeWorkspace.startingAction && !this.creativeJobRunning()) return;
+                    const startedAt = Number(this.creativeWorkspace.jobStartedAt || 0);
+                    if (!startedAt) return;
+                    this.creativeWorkspace.jobElapsedSeconds = Math.max(
+                        Number(this.creativeWorkspace.jobElapsedSeconds || 0),
+                        Math.floor((Date.now() - startedAt) / 1000)
+                    );
+                },
+
+                async pauseCreativeJob() {
+                    const res = await aiworkerApi.request(`/api/jobs/${this.creativeWorkspace.jobId}/pause`, {method: 'POST'});
+                    const data = await res.json();
+                    if (!res.ok || data.status === 'error') { this.creativeWorkspace.error = data.message || data.detail || '暂停失败'; return; }
+                    this.creativeWorkspace.paused = true;
+                },
+
+                async resumeCreativeJob() {
+                    const res = await aiworkerApi.request(`/api/jobs/${this.creativeWorkspace.jobId}/resume`, {method: 'POST'});
+                    const data = await res.json();
+                    if (!res.ok || data.status === 'error') { this.creativeWorkspace.error = data.message || data.detail || '继续失败'; return; }
+                    this.creativeWorkspace.paused = false;
+                },
+
+                async cancelCreativeJob() {
+                    if (!confirm('确定取消当前创作任务吗？尚未完成的版本不会写入项目。')) return;
+                    await aiworkerApi.request(`/api/jobs/${this.creativeWorkspace.jobId}/stop`, {method: 'POST'});
+                    await this.fetchJobs();
+                },
+
+                async retryCreativeJob() {
+                    const res = await aiworkerApi.request(`/api/jobs/${this.creativeWorkspace.jobId}/retry`, {method: 'POST'});
+                    const data = await res.json();
+                    if (!res.ok || data.status === 'error') { this.creativeWorkspace.error = data.detail || data.message || '重试失败'; return; }
+                    this.creativeWorkspace.jobId = data.job.job_id;
+                    this.creativeWorkspace.jobStatus = data.job.status;
+                    this.creativeWorkspace.jobMessage = `${this.creativeActionLabel(this.creativeWorkspace.activeAction)}重试任务已发起，正在后台处理`;
+                    this.creativeWorkspace.jobProgress = {indeterminate: true};
+                    this.creativeWorkspace.jobStartedAt = Number(data.job.started_at || 0) > 0 ? Number(data.job.started_at) * 1000 : Date.now();
+                    this.creativeWorkspace.jobElapsedSeconds = 0;
+                    this.creativeWorkspace.paused = false;
+                    this.creativeWorkspace.notifiedStatus = '';
+                    this.creativeGenerationProgressModal.show();
+                    await this.fetchJobs();
+                },
+
+                async saveCreativeProjectSettings() {
+                    const project = this.creativeWorkspace.project;
+                    if (!project) return;
+                    const payload = {
+                        name: project.name, genre: project.genre || '', target_platform: project.target_platform || '',
+                        target_audience: project.target_audience || '', target_episode_count: Number(project.target_episode_count),
+                        episode_duration_seconds: Number(project.episode_duration_seconds)
+                    };
+                    const res = await aiworkerApi.request(`/api/creative/projects/${project.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
+                    const data = await res.json();
+                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '保存 Brief 参数失败'; return; }
+                    this.creativeWorkspace.project = data;
+                    await this.fetchCreativeProjects(data.id);
+                },
+
+                async saveProjectBlueprint() {
+                    const item = this.selectedProjectBlueprint();
+                    if (!item) return;
+                    const res = await aiworkerApi.request(`/api/creative/blueprints/${item.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content: item.content, difference_note: item.difference_note || ''})});
+                    const data = await res.json();
+                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '保存方向失败'; return; }
+                    Object.assign(item, data);
+                },
+
+                async createProjectBlueprintVersion() {
+                    const item = this.selectedProjectBlueprint();
+                    if (!item) return;
+                    const res = await aiworkerApi.request(`/api/creative/blueprints/${item.id}/versions`, {method: 'POST'});
+                    const data = await res.json();
+                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '创建新版本失败'; return; }
+                    await this.selectCreativeProject(this.creativeWorkspace.project.id);
+                    this.creativeWorkspace.selectedBlueprintId = data.id;
+                },
+
+                async lockProjectBrief() {
+                    const item = this.selectedProjectBlueprint();
+                    if (!item) return;
+                    const res = await aiworkerApi.request(`/api/creative/blueprints/${item.id}/lock-brief`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({difference_note: item.difference_note || ''})});
+                    const data = await res.json();
+                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '锁定 Brief 失败'; return; }
+                    await this.selectCreativeProject(this.creativeWorkspace.project.id);
+                    this.creativeWorkspace.step = 'characters';
+                },
+
+                async createBlankCharacterBatch() {
+                    const projectId = this.creativeWorkspace.project.id;
+                    const blank = {name: '新角色', age_range: '', identity: '', external_tags: [], desire: '', secret: '', growth_arc: '', dialogue_style: '', appearance_prompt: '', voice_prompt: ''};
+                    const res = await aiworkerApi.request(`/api/creative/projects/${projectId}/characters`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({characters: [blank], relationships: []})});
+                    const data = await res.json();
+                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '创建空白人物版本失败'; return; }
+                    await this.selectCreativeProject(projectId);
+                },
+
+                async saveProjectCharacter(item) { await this.saveCreativeItem(`/api/creative/characters/${item.id}`, item, '人物卡已保存', `character-${item.id}`); },
+                async saveProjectRelationship(item) { await this.saveCreativeItem(`/api/creative/relationships/${item.id}`, item, '人物关系已保存', `relationship-${item.id}`); },
+                async saveProjectEpisode(item) { await this.saveCreativeItem(`/api/creative/episodes/${item.id}`, item, '分集内容已保存', `episode-${item.id}`); },
+                async saveCreativeItem(url, item, successMessage, saveKey) {
+                    this.creativeWorkspace.error = '';
+                    this.creativeWorkspace.saveState[saveKey] = 'saving';
+                    try {
+                        const res = await aiworkerApi.request(url, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content: item.content})});
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '保存失败');
+                        Object.assign(item, data);
+                        this.creativeWorkspace.saveState[saveKey] = 'saved';
+                        this.showCreativeNotice(successMessage || '保存成功');
+                        setTimeout(() => {
+                            if (this.creativeWorkspace.saveState[saveKey] === 'saved') this.creativeWorkspace.saveState[saveKey] = '';
+                        }, 1800);
+                    } catch (e) {
+                        this.creativeWorkspace.saveState[saveKey] = '';
+                        this.creativeWorkspace.error = `${successMessage || '保存'}失败：${e.message || '请稍后重试'}`;
+                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
+                    }
+                },
+
+                async lockProjectCharacters() {
+                    const projectId = this.creativeWorkspace.project.id;
+                    const batchId = this.latestCharacterBatchId();
+                    if (!batchId) return;
+                    const res = await aiworkerApi.request(`/api/creative/projects/${projectId}/characters/${batchId}/lock`, {method: 'POST'});
+                    const data = await res.json();
+                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '锁定人物关系失败'; return; }
+                    await this.selectCreativeProject(projectId);
+                    this.creativeWorkspace.step = 'outlines';
+                },
+
+                async lockProjectOutlines() {
+                    const projectId = this.creativeWorkspace.project.id;
+                    const batchId = this.latestOutlineBatchId();
+                    if (!batchId) return;
+                    const res = await aiworkerApi.request(`/api/creative/projects/${projectId}/outlines/${batchId}/lock`, {method: 'POST'});
+                    const data = await res.json();
+                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '锁定分集大纲失败'; return; }
+                    await this.selectCreativeProject(projectId);
+                    this.showCreativeNotice('分集大纲已锁定，可从每集操作进入单集节拍表');
+                },
+
+                async lockProjectBeats() {
+                    const item = this.selectedProjectBeats();
+                    if (!item) return;
+                    const res = await aiworkerApi.request(`/api/creative/episodes/${item.id}/lock`, {method: 'POST'});
+                    const data = await res.json();
+                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '锁定 Beats 失败'; return; }
+                    await this.selectCreativeProject(this.creativeWorkspace.project.id);
+                },
+
+                creativeExportUrl(stage, format = 'md') {
+                    const id = this.creativeWorkspace.project?.id || '';
+                    return `/api/creative/projects/${id}/export?stage=${encodeURIComponent(stage)}&format=${encodeURIComponent(format)}`;
+                },
+
+}));
diff --git a/static/js/common/session.js b/static/js/common/session.js
new file mode 100644
index 0000000..a31391e
--- /dev/null
+++ b/static/js/common/session.js
@@ -0,0 +1,13 @@
+(function () {
+    'use strict';
+
+    document.addEventListener('DOMContentLoaded', () => {
+        document.getElementById('logout-button')?.addEventListener('click', async () => {
+            try {
+                await window.aiworkerApi.request('/api/auth/logout', {method: 'POST'});
+            } finally {
+                window.location.assign('/login');
+            }
+        });
+    });
+})();
diff --git a/static/js/common/tasks.js b/static/js/common/tasks.js
new file mode 100644
index 0000000..612cceb
--- /dev/null
+++ b/static/js/common/tasks.js
@@ -0,0 +1,460 @@
+function localDateString(date = new Date()) {
+            const year = date.getFullYear();
+            const month = String(date.getMonth() + 1).padStart(2, '0');
+            const day = String(date.getDate()).padStart(2, '0');
+            return `${year}-${month}-${day}`;
+        }
+
+        function appData() {
+            const workspace = {
+                page: document.body.dataset.workspacePage || '',
+                isRunning: false,
+                jobs: [],
+                selectedJobId: '',
+                logs: [],
+                batchProgress: '',
+                fileFilter: 'all',
+                fileSearch: '',
+                files: [],
+                filteredFiles: [],
+                selectedFilePaths: [],
+                batchDeleteMode: false,
+                filePreviewModal: null,
+                previewFile: {status: 'idle', file: null, kind: '', url: '', content: '', message: ''},
+                topicTranscriptPreviewModal: null,
+                topicTranscriptPreview: {topic: null, episodes: [], selectedEpisode: null, loading: false, error: ''},
+                configForm: {root: {}, items: []},
+                configRootFields: [
+                    {key: 'host', label: 'host', type: 'text'},
+                    {key: 'video_filter', label: 'video_filter', type: 'checkbox'},
+                    {key: 'sleep', label: 'sleep', type: 'number'},
+                    {key: 'autoRun', label: 'autoRun', type: 'checkbox'},
+                    {key: '默认平台', label: '默认平台', type: 'text'},
+                    {key: '默认爬取类型', label: '默认爬取类型', type: 'text'},
+                    {key: '默认起始页码', label: '默认起始页码', type: 'number'},
+                    {key: '默认登录方式', label: '默认登录方式', type: 'text'},
+                    {key: '默认保存方式', label: '默认保存方式', type: 'text'}
+                ],
+                backups: [],
+                projectItems: [],
+                serverIp: '',
+                qrcodeStatus: { has_state: false, message: '' },
+                qrcodeLoggingIn: false,
+                audioModal: null,
+                dramaDownloadModal: null,
+                audioAnalysis: {status: 'idle'},
+                dramaAnalysis: {
+                    title: '',
+                    candidates: [],
+                    selected: null,
+                    episodes: [],
+                    episode: 0,
+                    suggestions: [],
+                    suggestionLoading: false,
+                    suggestionTimer: null,
+                    suggestionRequestId: 0,
+                    searchSource: '',
+                    fallbackReason: '',
+                    loading: false,
+                    error: '',
+                    jobId: '',
+                    notifiedStatus: '',
+                    interfaceTest: null,
+                    settings: {config_url: '', has_activation_code: false},
+                    activationCode: ''
+                },
+                creative: {platform: 'dy', items: [], loading: false, error: '', breakdown: null},
+                topicLibrary: {platform: '', lifecycleStatus: '', category: '', minScore: '', hasLocked: '', items: [], loading: false, error: '', breakdown: null},
+                breakdownEditor: {source: '', title: '', record: null, content: {}, fieldGuide: [], saving: false, error: ''},
+                breakdownGeneration: {topic: null, source: '', jobId: '', status: '', message: '', progress: {}, coverage: [], requiresTranscription: false, notifiedStatus: '', error: '', startedAt: 0, elapsedSeconds: 0},
+                creativeWorkspace: {
+                    projects: [], eligibleTopics: [], lockedBreakdowns: [], project: null, loading: false, error: '', step: 'blueprints',
+                    create: {topic_id: '', breakdown_id: '', name: '', target_platform: '', target_episode_count: 20},
+                    directionCount: 3, selectedBlueprintId: null, selectedEpisode: 1,
+                    jobId: '', jobMessage: '', jobProgress: {}, jobStatus: '', paused: false, notifiedStatus: '',
+                    jobStartedAt: 0, jobElapsedSeconds: 0,
+                    startingAction: '', activeAction: '',
+                    saveState: {}, notice: '', noticeType: 'success', noticeTimer: null
+                },
+                dramaDownload: {title: '', topic: null, source: 'hongguo', fallbackReason: '', candidates: [], selectedCandidate: null, selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], selectedEpisodes: [], allEpisodes: false, searching: false, episodesLoading: false, starting: false, error: '', jobId: '', jobStatus: '', progress: {}, progressMessage: '', paused: false, finished: false, background: false},
+                analyticsOverview: {},
+                analyticsTags: {categories: [], selling_points: [], hooks: [], plot_tags: []},
+                analyticsTrends: {points: []},
+                analyticsSentiment: [],
+                analyticsDramas: [],
+                analyticsPlatform: '',
+                analyticsDate: localDateString(),
+                analyticsLoading: false,
+                analyticsError: '',
+                analyticsSyncResult: null,
+                todayRankPlatform: 'dy',
+                todayRankDate: '',
+                todayRankSectionKey: '',
+                todayRankRankingKey: '',
+                todayRankData: null,
+                todayRankLoading: false,
+                todayRankError: '',
+                rankingActiveTables: {},
+                modalMode: 'crawl',
+                modalFile: '',
+                modalProjects: [],
+                selectedModalProjects: [],
+                projectModal: null,
+                autoUpload: true,
+                config: {
+                    platform: 'dy',
+                    login_type: 'qrcode',
+                    crawler_type: 'search',
+                    keywords: '',
+                    start_page: 1,
+                    headless: false,
+                    save_option: 'json',
+                    get_comment: false,
+                    get_sub_comment: false,
+                    specified_ids: '',
+                    creator_ids: '',
+                    cookie: '',
+                    dy_channel: 'video',
+                    dy_sort: 'latest',
+                    dy_publish_time: '1_week',
+                    dy_rank_limit: 50,
+                    dy_rank_comment_limit: 10,
+                    dy_rank_login_wait_seconds: 0,
+                    autoRun: false,
+                    projects: {}
+                },
+
+                modal(id) {
+                    const element = document.getElementById(id);
+                    return element ? bootstrap.Modal.getOrCreateInstance(element) : null;
+                },
+
+                async init() {
+                    this.projectModal = this.modal('projectModal');
+                    this.audioModal = this.modal('audioModal');
+                    this.filePreviewModal = this.modal('filePreviewModal');
+                    document.getElementById('filePreviewModal')?.addEventListener('hidden.bs.modal', () => this.closeFilePreview());
+                    this.topicTranscriptPreviewModal = this.modal('topicTranscriptPreviewModal');
+                    this.dramaDownloadModal = this.modal('dramaDownloadModal');
+                    document.getElementById('dramaDownloadModal')?.addEventListener('hidden.bs.modal', () => {
+                        if (this.dramaDownload.jobId) this.dramaDownload.background = true;
+                    });
+                    this.breakdownEditorModal = this.modal('breakdownEditorModal');
+                    this.transcriptRequiredModal = this.modal('transcriptRequiredModal');
+                    this.breakdownGenerationProgressModal = this.modal('breakdownGenerationProgressModal');
+                    this.creativeGenerationProgressModal = this.modal('creativeGenerationProgressModal');
+
+                    await this.fetchJobs();
+                    if (this.page === 'selection-ranks') {
+                        this.parseTodayRankHash(false);
+                        window.addEventListener('hashchange', () => this.parseTodayRankHash(true));
+                        await this.fetchTodayRank();
+                    }
+                    if (['selection-dashboard', 'selection-topics', 'selection-breakdowns'].includes(this.page)) {
+                        await this.fetchCreativeTopics();
+                        await this.fetchTopicLibrary();
+                        this.restoreLatestActiveDramaDownload?.();
+                    }
+                    if (this.page.startsWith('planning-') || this.page.startsWith('writing-') || this.page === 'admin-deliveries') {
+                        const queryProjectId = new URLSearchParams(window.location.search).get('project_id');
+                        const pathProjectId = window.location.pathname.match(/\/projects\/([^/]+)/)?.[1];
+                        await this.fetchCreativeProjects(queryProjectId || pathProjectId || null);
+                        const topicId = new URLSearchParams(window.location.search).get('topic_id');
+                        if (this.page === 'planning-projects' && topicId) {
+                            this.creativeWorkspace.create.topic_id = Number(topicId);
+                            await this.syncCreativeProjectSource();
+                        }
+                        const episode = window.location.pathname.match(/\/episodes\/(\d+)\/beats/)?.[1];
+                        if (episode) this.creativeWorkspace.selectedEpisode = Number(episode);
+                    }
+                    if (this.page === 'admin-config') {
+                        await this.loadConfig();
+                        await this.fetchConfigForm();
+                        await this.fetchBackups();
+                        await this.fetchDramaSettings();
+                    }
+                    if (this.page === 'admin-data') await this.fetchFiles();
+                    if (this.page === 'admin-analytics') await this.refreshAnalyticsDashboard(false);
+                    if (this.page === 'admin-jobs') {
+                        await this.loadConfig();
+                        await this.fetchQrcodeStatus();
+                        const jobId = new URLSearchParams(window.location.search).get('job_id');
+                        if (jobId) await this.selectJob(jobId);
+                    }
+                    this.initTooltips();
+                    if (this.page === 'admin-jobs') setInterval(() => this.fetchLogs(), 1000);
+                    setInterval(() => this.fetchJobs(), 2000);
+                    if (this.tickCreativeGenerationElapsed) setInterval(() => this.tickCreativeGenerationElapsed(), 1000);
+                    if (this.tickBreakdownGenerationElapsed) setInterval(() => this.tickBreakdownGenerationElapsed(), 1000);
+                },
+
+                initTooltips() {
+                    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(element => {
+                        bootstrap.Tooltip.getOrCreateInstance(element);
+                    });
+                },
+
+                async fetchServerIp() {
+                    try {
+                        const res = await aiworkerApi.request('/api/server/ip');
+                        const data = await res.json();
+                        if (data.ip) this.serverIp = `${data.ip}:${data.port}`;
+                    } catch (e) {}
+                },
+
+                async loadConfig() {
+                    try {
+                        const res = await aiworkerApi.request('/api/config');
+                        const data = await res.json();
+                        const source = Array.isArray(data) ? (data[0] || {}) : (data || {});
+                        this.projectItems = source.items || [];
+                        this.config.projects = {};
+                        this.projectItems.forEach(item => {
+                            if (this.config.projects[item.project] === undefined) {
+                                this.config.projects[item.project] = true;
+                            }
+                        });
+                        if (source.autoRun !== undefined) this.config.autoRun = source.autoRun;
+                        this.jobs = data.jobs || [];
+                        this.isRunning = this.jobs.some(job => this.isJobRunning(job));
+                        if (!this.selectedJobId && this.jobs.length > 0) this.selectedJobId = this.jobs[0].job_id;
+                        if (this.selectedJobId && !this.jobs.some(job => job.job_id === this.selectedJobId)) {
+                            this.selectedJobId = this.jobs.length > 0 ? this.jobs[0].job_id : '';
+                            if (!this.selectedJobId) this.logs = [];
+                        }
+                    } catch (e) {}
+                },
+
+                async fetchJobs() {
+                    try {
+                        const res = await aiworkerApi.request('/api/jobs');
+                        const data = await res.json();
+                        this.jobs = data.jobs || [];
+                        this.isRunning = this.jobs.some(job => this.isJobRunning(job));
+                        if (!this.selectedJobId && this.jobs.length > 0) {
+                            this.selectedJobId = this.jobs[0].job_id;
+                        }
+                        if (this.selectedJobId && !this.jobs.some(job => job.job_id === this.selectedJobId)) {
+                            this.selectedJobId = this.jobs.length > 0 ? this.jobs[0].job_id : '';
+                            if (!this.selectedJobId) this.logs = [];
+                        }
+                        this.notifyDramaJobCompletion?.();
+                        this.syncDramaDownloadProgress?.();
+                        if (this.syncCreativeJobProgress) await this.syncCreativeJobProgress();
+                        this.syncBreakdownGenerationProgress?.();
+                    } catch (e) {
+                        this.jobs = [];
+                        this.isRunning = false;
+                    }
+                },
+
+                async fetchLogs() {
+                    try {
+                        if (this.selectedJobId) {
+                            const res = await aiworkerApi.request(`/api/jobs/${this.selectedJobId}/logs`);
+                            if (res.status === 404) {
+                                this.logs = [];
+                                this.selectedJobId = '';
+                                await this.fetchJobs();
+                                if (!this.selectedJobId) {
+                                    const latest = await aiworkerApi.request('/api/logs');
+                                    const data = await latest.json();
+                                    this.logs = data.logs || [];
+                                    if (data.job_id) this.selectedJobId = data.job_id;
+                                    this.batchProgress = data.batch_progress || '';
+                                    this.isRunning = data.is_running || false;
+                                }
+                                return;
+                            }
+                            const data = await res.json();
+                            this.logs = data.logs || [];
+                            if (data.job_id && data.job_id !== this.selectedJobId) this.selectedJobId = data.job_id;
+                        } else {
+                            const res = await aiworkerApi.request('/api/logs');
+                            const data = await res.json();
+                            this.logs = data.logs || [];
+                            if (data.job_id) this.selectedJobId = data.job_id;
+                            this.batchProgress = data.batch_progress || '';
+                            this.isRunning = data.is_running || false;
+                        }
+                        this.$nextTick(() => {
+                            const el = document.getElementById('logConsole');
+                            if (el) el.scrollTop = el.scrollHeight;
+                        });
+                    } catch (e) {}
+                },
+
+                async selectJob(jobId) {
+                    this.selectedJobId = jobId;
+                    await this.fetchLogs();
+                },
+
+                isJobRunning(job) {
+                    return ['pending', 'running', 'stopping'].includes(job.status);
+                },
+
+                jobStatusClass(status) {
+                    if (status === 'succeeded') return 'bg-success';
+                    if (status === 'failed') return 'bg-danger';
+                    if (status === 'cancelled' || status === 'stopping') return 'bg-warning';
+                    if (status === 'running') return 'bg-primary';
+                    return 'bg-secondary';
+                },
+
+                logClass(log) {
+                    if (String(log).includes('[ERROR]')) return 'text-danger';
+                    if (String(log).includes('[WARNING]')) return 'text-warning';
+                    if (String(log).includes('[SUCCESS]')) return 'text-success';
+                    return 'text-light';
+                },
+
+                logsText() {
+                    return (this.logs || []).join('\n');
+                },
+
+                async copyLogs() {
+                    const text = this.logsText();
+                    if (!text) return;
+                    try {
+                        await navigator.clipboard.writeText(text);
+                        alert(`日志已复制，共 ${this.logs.length} 行`);
+                    } catch (e) {
+                        try {
+                            const textarea = document.createElement('textarea');
+                            textarea.value = text;
+                            textarea.style.position = 'fixed';
+                            textarea.style.opacity = '0';
+                            document.body.appendChild(textarea);
+                            textarea.select();
+                            const copied = document.execCommand('copy');
+                            document.body.removeChild(textarea);
+                            if (!copied) throw new Error('copy command failed');
+                            alert(`日志已复制，共 ${this.logs.length} 行`);
+                        } catch (fallbackError) {
+                            alert('复制失败，请手动选择日志内容复制');
+                        }
+                    }
+                },
+
+                downloadLogs() {
+                    const text = this.logsText();
+                    if (!text) return;
+                    const blob = new Blob([text + '\n'], {type: 'text/plain;charset=utf-8'});
+                    const url = URL.createObjectURL(blob);
+                    const link = document.createElement('a');
+                    const jobId = this.selectedJobId || 'latest';
+                    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
+                    link.href = url;
+                    link.download = `${jobId}-${stamp}.log`;
+                    document.body.appendChild(link);
+                    link.click();
+                    document.body.removeChild(link);
+                    URL.revokeObjectURL(url);
+                    alert('日志下载已开始');
+                },
+
+                selectedProjectCount() {
+                    return Object.values(this.config.projects || {}).filter(Boolean).length;
+                },
+
+                async runCrawler() {
+                    if (this.config.crawler_type === 'rank' && !['dy', 'hg'].includes(this.config.platform)) {
+                        alert('榜单爬取仅支持抖音和红果');
+                        return;
+                    }
+                    if (this.config.crawler_type === 'search') {
+                        const keywords = this.projectItems
+                            .filter(item => this.config.projects[item.project])
+                            .map(item => item['搜索关键词'] || item.keywords || '')
+                            .join(',');
+                        this.config.keywords = keywords;
+                    }
+                    try {
+                        const res = await aiworkerApi.request('/api/run', {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify(this.config)
+                        });
+                        const data = await res.json();
+                        if (!res.ok || data.status === 'error') {
+                            alert(data.message);
+                            return;
+                        }
+                        if (data.job && data.job.job_id) this.selectedJobId = data.job.job_id;
+                        this.logs = ['[SYSTEM] 正在启动...'];
+                    } catch (e) {
+                        alert('启动失败');
+                        return;
+                    }
+                    await this.fetchJobs();
+                    await this.fetchLogs();
+                },
+
+                async runRankCrawler(platform) {
+                    const previousPlatform = this.config.platform;
+                    const previousType = this.config.crawler_type;
+                    this.config.platform = platform;
+                    this.config.crawler_type = 'rank';
+                    await this.runCrawler();
+                    this.config.platform = previousPlatform;
+                    this.config.crawler_type = previousType;
+                },
+
+                async stopJob(jobId) {
+                    await aiworkerApi.request(`/api/jobs/${jobId}/stop`, {method: 'POST'});
+                    await this.fetchJobs();
+                    await this.fetchLogs();
+                },
+
+                async stopCrawler() {
+                    if (this.selectedJobId) {
+                        await this.stopJob(this.selectedJobId);
+                        return;
+                    }
+                    await aiworkerApi.request('/api/stop', {method: 'POST'});
+                    await this.fetchJobs();
+                    await this.fetchLogs();
+                },
+
+                async deleteJob(jobId) {
+                    const job = this.jobs.find(item => item.job_id === jobId);
+                    if (job && this.isJobRunning(job)) {
+                        alert('Job is still running. Stop it first.');
+                        return;
+                    }
+                    if (!confirm(`Delete job ${jobId}? Logs will be removed from the task list.`)) return;
+                    const res = await aiworkerApi.request(`/api/jobs/${jobId}/delete`, {method: 'POST'});
+                    const data = await res.json().catch(() => ({}));
+                    if (!res.ok || data.status === 'error') {
+                        alert(data.message || 'Delete failed');
+                        return;
+                    }
+                    if (this.selectedJobId === jobId) {
+                        this.selectedJobId = '';
+                        this.logs = [];
+                    }
+                    this.jobs = this.jobs.filter(item => item.job_id !== jobId);
+                    await this.fetchJobs();
+                    await this.fetchLogs();
+                },
+
+                formatSize(bytes) {
+                    if (!bytes) return '0 B';
+                    const units = ['B', 'KB', 'MB', 'GB'];
+                    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
+                    return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
+                },
+
+                formatDate(timestamp) {
+                    if (!timestamp) return '';
+                    return new Date(timestamp * 1000).toLocaleString();
+                },
+
+                downloadFile(path) {
+                    window.open(`/api/file/download?path=${encodeURIComponent(path)}`, '_blank');
+                },
+
+            };
+            const modules = window.aiworkerModules || [];
+            return Object.assign(workspace, ...modules.map((factory) => factory()));
+        }
diff --git a/static/js/planning/brief.js b/static/js/planning/brief.js
new file mode 100644
index 0000000..c5daede
--- /dev/null
+++ b/static/js/planning/brief.js
@@ -0,0 +1 @@
+document.querySelector('[data-workspace-page="planning-brief"]')?.setAttribute('data-ready', 'true');
diff --git a/static/js/planning/characters.js b/static/js/planning/characters.js
new file mode 100644
index 0000000..b45dfed
--- /dev/null
+++ b/static/js/planning/characters.js
@@ -0,0 +1 @@
+document.querySelector('[data-workspace-page="planning-characters"]')?.setAttribute('data-ready', 'true');
diff --git a/static/js/planning/projects.js b/static/js/planning/projects.js
new file mode 100644
index 0000000..dca3744
--- /dev/null
+++ b/static/js/planning/projects.js
@@ -0,0 +1 @@
+document.querySelector('[data-workspace-page="planning-projects"]')?.setAttribute('data-ready', 'true');
diff --git a/static/js/review/approvals.js b/static/js/review/approvals.js
new file mode 100644
index 0000000..7192a9f
--- /dev/null
+++ b/static/js/review/approvals.js
@@ -0,0 +1 @@
+document.querySelector('[data-workspace-page="review-approvals"]')?.setAttribute('data-ready', 'true');
diff --git a/static/js/review/tasks.js b/static/js/review/tasks.js
new file mode 100644
index 0000000..c384c35
--- /dev/null
+++ b/static/js/review/tasks.js
@@ -0,0 +1,3 @@
+document.getElementById('refresh-review-tasks')?.addEventListener('click', () => {
+    document.getElementById('review-task-list').textContent = '暂无待处理审批任务。';
+});
diff --git a/static/js/selection/breakdowns.js b/static/js/selection/breakdowns.js
new file mode 100644
index 0000000..720cd31
--- /dev/null
+++ b/static/js/selection/breakdowns.js
@@ -0,0 +1,618 @@
+window.aiworkerModules = window.aiworkerModules || [];
+window.aiworkerModules.push(() => ({
+                async updateTopicLibrary(topic, status) {
+                    const res = await aiworkerApi.request(`/api/creative/topics/${topic.id}`, {
+                        method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status})
+                    });
+                    const data = await res.json();
+                    if (!res.ok) { this.topicLibrary.error = data.detail || '更新选题状态失败'; return; }
+                    Object.assign(topic, data);
+                },
+
+                createTopicLibraryBreakdown(topic) {
+                    return this.startTranscriptBreakdown(topic, 'topic-library');
+                },
+
+                async openTopicTranscriptPreview(topic) {
+                    this.topicTranscriptPreview = {topic, episodes: [], selectedEpisode: null, loading: true, error: ''};
+                    this.topicTranscriptPreviewModal.show();
+                    try {
+                        const res = await aiworkerApi.request(`/api/creative/topics/${topic.id}/transcripts`);
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '读取剧目转写失败');
+                        this.topicTranscriptPreview.episodes = data.episodes || [];
+                        this.topicTranscriptPreview.selectedEpisode = this.topicTranscriptPreview.episodes[0]?.episode_number || null;
+                    } catch (e) {
+                        this.topicTranscriptPreview.error = e.message || '读取剧目转写失败';
+                    } finally {
+                        this.topicTranscriptPreview.loading = false;
+                    }
+                },
+
+                selectedTopicTranscript() {
+                    return this.topicTranscriptPreview.episodes.find(
+                        item => Number(item.episode_number) === Number(this.topicTranscriptPreview.selectedEpisode)
+                    ) || null;
+                },
+
+                canMoveTopicTranscript(offset) {
+                    const episodes = this.topicTranscriptPreview.episodes;
+                    const index = episodes.findIndex(
+                        item => Number(item.episode_number) === Number(this.topicTranscriptPreview.selectedEpisode)
+                    );
+                    const target = index + Number(offset || 0);
+                    return index >= 0 && target >= 0 && target < episodes.length;
+                },
+
+                moveTopicTranscript(offset) {
+                    if (!this.canMoveTopicTranscript(offset)) return;
+                    const episodes = this.topicTranscriptPreview.episodes;
+                    const index = episodes.findIndex(
+                        item => Number(item.episode_number) === Number(this.topicTranscriptPreview.selectedEpisode)
+                    );
+                    this.topicTranscriptPreview.selectedEpisode = episodes[index + Number(offset)].episode_number;
+                },
+
+                transcriptSourceLabel(source) {
+                    return {script: '剧本转写', transcript: '语音转写', text: '文本转写'}[source] || '剧目转写';
+                },
+
+                async startTranscriptBreakdown(topic, source, forceRegenerate = false) {
+                    if (!topic?.id) return;
+                    this.breakdownGeneration = {
+                        topic,
+                        source,
+                        jobId: '',
+                        status: 'pending',
+                        message: '正在检查已下载的剧目脚本',
+                        progress: {completed: 0, total: 1},
+                        coverage: [],
+                        requiresTranscription: false,
+                        notifiedStatus: '',
+                        error: '',
+                        startedAt: Date.now(),
+                        elapsedSeconds: 0
+                    };
+                    try {
+                        const res = await aiworkerApi.request('/api/creative/breakdowns', {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify({topic_id: topic.id, force_regenerate: forceRegenerate})
+                        });
+                        const data = await res.json();
+                        const detail = data.detail;
+                        if (res.status === 409 && detail?.code === 'transcript_required') {
+                            this.breakdownGeneration.status = 'blocked';
+                            this.breakdownGeneration.message = detail.message;
+                            this.transcriptRequiredModal.show();
+                            return;
+                        }
+                        if (!res.ok || data.status === 'error') {
+                            throw new Error(typeof detail === 'string' ? detail : detail?.message || data.message || '生成拆解失败');
+                        }
+                        if (data.status === 'existing' && data.breakdown) {
+                            this.breakdownGeneration.status = 'existing';
+                            this.breakdownGeneration.message = '已加载最新拆解版本';
+                            this.breakdownGeneration.startedAt = 0;
+                            if (source === 'topic-library') this.topicLibrary.breakdown = data.breakdown;
+                            else this.creative.breakdown = data.breakdown;
+                            this.openBreakdownEditor(source, topic.title, data.breakdown);
+                            return;
+                        }
+                        this.breakdownGeneration.jobId = data.job.job_id;
+                        this.breakdownGeneration.status = data.job.status || 'pending';
+                        this.breakdownGeneration.message = data.requires_transcription
+                            ? '已发现下载视频，正在生成剧本转写'
+                            : (data.job.message || '已加载剧目脚本，正在生成个性化拆解');
+                        this.breakdownGeneration.progress = data.job.progress || {completed: 0, total: 1};
+                        this.breakdownGeneration.coverage = data.coverage || [];
+                        this.breakdownGeneration.requiresTranscription = Boolean(data.requires_transcription);
+                        this.breakdownGeneration.startedAt = Number(data.job.started_at || 0) > 0
+                            ? Number(data.job.started_at) * 1000
+                            : Date.now();
+                        this.breakdownGenerationProgressModal.show();
+                        if (source === 'topic-library') await this.fetchTopicLibrary();
+                        if (source === 'creative') await this.fetchCreativeTopics();
+                        await this.fetchJobs();
+                    } catch (e) {
+                        const message = e.message || '生成拆解失败';
+                        this.breakdownGeneration.status = 'failed';
+                        this.breakdownGeneration.message = message;
+                        this.breakdownGeneration.error = message;
+                        if (source === 'topic-library') this.topicLibrary.error = message;
+                        else this.creative.error = message;
+                        this.breakdownGenerationProgressModal.show();
+                    }
+                },
+
+                async syncBreakdownGenerationProgress() {
+                    if (!this.breakdownGeneration.jobId) return;
+                    const job = this.jobs.find(item => item.job_id === this.breakdownGeneration.jobId);
+                    if (!job) return;
+                    this.breakdownGeneration.status = job.status;
+                    this.breakdownGeneration.message = job.message || this.breakdownGeneration.message;
+                    this.breakdownGeneration.progress = job.progress || this.breakdownGeneration.progress || {};
+                    if (!this.breakdownGeneration.startedAt && Number(job.started_at || 0) > 0) {
+                        this.breakdownGeneration.startedAt = Number(job.started_at) * 1000;
+                    }
+                    this.breakdownGeneration.elapsedSeconds = Math.max(
+                        Number(this.breakdownGeneration.elapsedSeconds || 0),
+                        Number(job.progress?.elapsed_seconds || 0)
+                    );
+                    if (job.progress?.coverage?.length) this.breakdownGeneration.coverage = job.progress.coverage;
+                    if (!['succeeded', 'failed', 'cancelled'].includes(job.status) || this.breakdownGeneration.notifiedStatus === job.status) return;
+                    this.breakdownGeneration.notifiedStatus = job.status;
+                    if (job.status !== 'succeeded') {
+                        this.breakdownGeneration.error = `拆解生成${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || '请查看任务日志'}`;
+                        return;
+                    }
+                    let completedJob = job;
+                    if (!(completedJob.events || []).length) {
+                        const detailResponse = await aiworkerApi.request(`/api/jobs/${job.job_id}`);
+                        if (detailResponse.ok) completedJob = await detailResponse.json();
+                    }
+                    const finished = [...(completedJob.events || [])].reverse().find(event => event.event === 'job_finished' && event.payload?.breakdown_id);
+                    const breakdownId = finished?.payload?.breakdown_id;
+                    if (!breakdownId) {
+                        this.breakdownGeneration.error = '生成任务已结束，但未返回拆解版本，请查看任务日志。';
+                        return;
+                    }
+                    try {
+                        const res = await aiworkerApi.request(`/api/creative/breakdowns/${breakdownId}`);
+                        const record = await res.json();
+                        if (!res.ok) throw new Error(record.detail || '读取生成结果失败');
+                        if (this.breakdownGeneration.source === 'topic-library') {
+                            this.topicLibrary.breakdown = record;
+                            await this.fetchTopicLibrary();
+                        } else {
+                            this.creative.breakdown = record;
+                            await this.fetchCreativeTopics();
+                        }
+                        const source = this.breakdownGeneration.source;
+                        const title = this.breakdownGeneration.topic?.title || record.content?.title;
+                        this.breakdownGenerationProgressModal.hide();
+                        setTimeout(() => this.openBreakdownEditor(source, title, record), 200);
+                    } catch (e) {
+                        this.breakdownGeneration.error = e.message || '读取生成结果失败';
+                    }
+                },
+
+                breakdownGenerationRunning() {
+                    return ['pending', 'running', 'stopping'].includes(this.breakdownGeneration.status);
+                },
+
+                breakdownGenerationRetryable() {
+                    return ['failed', 'cancelled'].includes(this.breakdownGeneration.status);
+                },
+
+                breakdownGenerationIndeterminate() {
+                    if (!this.breakdownGenerationRunning()) return false;
+                    const progress = this.breakdownGeneration.progress || {};
+                    if (progress.indeterminate) return true;
+                    return Number(progress.completed || 0) === 0 && Number(progress.total || 0) <= 1;
+                },
+
+                breakdownGenerationProgressLabel() {
+                    if (!this.breakdownGenerationIndeterminate()) return `${this.breakdownGenerationProgressPercent()}%`;
+                    const seconds = Math.max(
+                        Number(this.breakdownGeneration.elapsedSeconds || 0),
+                        Number(this.breakdownGeneration.progress?.elapsed_seconds || 0)
+                    );
+                    return `已等待 ${this.formatBreakdownGenerationElapsed(seconds)}`;
+                },
+
+                formatBreakdownGenerationElapsed(seconds) {
+                    const safeSeconds = Math.max(0, Math.floor(Number(seconds || 0)));
+                    const minutes = Math.floor(safeSeconds / 60);
+                    const remainder = safeSeconds % 60;
+                    return minutes ? `${minutes}分${remainder}秒` : `${remainder}秒`;
+                },
+
+                tickBreakdownGenerationElapsed() {
+                    if (!this.breakdownGenerationRunning()) return;
+                    if (!this.breakdownGeneration.startedAt) this.breakdownGeneration.startedAt = Date.now();
+                    this.breakdownGeneration.elapsedSeconds = Math.max(
+                        Number(this.breakdownGeneration.elapsedSeconds || 0),
+                        Math.floor((Date.now() - this.breakdownGeneration.startedAt) / 1000)
+                    );
+                },
+
+                breakdownGenerationProgressPercent() {
+                    const progress = this.breakdownGeneration.progress || {};
+                    const total = Number(progress.total || 0);
+                    if (total) return Math.min(100, Math.round(Number(progress.completed || 0) * 100 / total));
+                    return this.breakdownGeneration.status === 'succeeded' ? 100 : 0;
+                },
+
+                async openBreakdownTaskLog() {
+                    const jobId = this.breakdownGeneration.jobId;
+                    this.breakdownGenerationProgressModal.hide();
+                    window.location.assign(`/tasks${jobId ? `?job_id=${encodeURIComponent(jobId)}` : ''}`);
+                },
+
+                regenerateCurrentBreakdown() {
+                    const record = this.breakdownEditor.record;
+                    if (!record?.topic_id || this.breakdownGenerationRunning()) return;
+                    if (!confirm('确定重新分析已下载脚本并生成一个新拆解版本吗？')) return;
+                    const topic = this.breakdownGeneration.topic?.id === record.topic_id
+                        ? this.breakdownGeneration.topic
+                        : {id: record.topic_id, title: this.breakdownEditor.title};
+                    const source = this.breakdownEditor.source || 'topic-library';
+                    this.breakdownEditorModal.hide();
+                    setTimeout(() => this.startTranscriptBreakdown(topic, source, true), 350);
+                },
+
+                dramaDownloadTitleKey(value) {
+                    return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
+                },
+
+                findDramaDownloadJob(title = '') {
+                    const titleKey = this.dramaDownloadTitleKey(title);
+                    return this.jobs.find(job => {
+                        const crawlerType = job.crawler_type || job.job?.crawler_type || '';
+                        const jobTitle = job.job?.extra?.title || '';
+                        return ['drama_download', 'douyin_fallback_analysis'].includes(crawlerType)
+                            && ['pending', 'running', 'stopping'].includes(job.status)
+                            && (!titleKey || this.dramaDownloadTitleKey(jobTitle) === titleKey);
+                    }) || null;
+                },
+
+                restoreDramaDownloadJob(topic, job, showModal = true) {
+                    if (!job) return false;
+                    const crawlerType = job.crawler_type || job.job?.crawler_type || '';
+                    const extra = job.job?.extra || {};
+                    const source = crawlerType === 'douyin_fallback_analysis' ? 'douyin_fallback' : 'hongguo';
+                    this.dramaDownload = {
+                        title: topic?.title || extra.title || '', topic: topic || null, source,
+                        fallbackReason: extra.fallback_reason || '', candidates: [], selectedCandidate: null,
+                        selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], selectedEpisodes: [],
+                        allEpisodes: Boolean(extra.all_episodes), searching: false, episodesLoading: false,
+                        starting: false, error: '', jobId: job.job_id, jobStatus: job.status || 'running',
+                        progress: job.progress || {}, progressMessage: job.message || '下载任务正在运行',
+                        paused: Boolean(job.paused), finished: false, background: !showModal
+                    };
+                    if (showModal) this.dramaDownloadModal.show();
+                    this.scrollDramaDownloadToTop();
+                    return true;
+                },
+
+                restoreLatestActiveDramaDownload() {
+                    if (this.dramaDownload.jobId) return;
+                    const job = this.findDramaDownloadJob();
+                    if (job) this.restoreDramaDownloadJob(null, job, false);
+                },
+
+                scrollDramaDownloadToTop() {
+                    this.$nextTick(() => {
+                        const body = document.querySelector('#dramaDownloadModal .modal-body');
+                        if (body) body.scrollTop = 0;
+                    });
+                },
+
+                dramaDownloadStatusLabel() {
+                    if (this.dramaDownload.starting) return '正在启动';
+                    if (this.dramaDownload.paused) return '已暂停';
+                    return {pending: '等待执行', running: '下载中', stopping: '正在停止', succeeded: '已完成', failed: '失败', cancelled: '已取消'}[this.dramaDownload.jobStatus] || '下载中';
+                },
+
+                dramaDownloadStatusClass() {
+                    if (this.dramaDownload.paused) return 'bg-warning text-dark';
+                    return {pending: 'bg-secondary', running: 'bg-primary', stopping: 'bg-warning text-dark', succeeded: 'bg-success', failed: 'bg-danger', cancelled: 'bg-secondary'}[this.dramaDownload.jobStatus] || 'bg-primary';
+                },
+
+                dramaDownloadCurrentDescription() {
+                    const current = Number(this.dramaDownload.progress?.current || 0);
+                    if (current > 0) return this.dramaDownload.source === 'douyin_fallback' ? `当前处理第 ${current} 个视频` : `当前处理第 ${current} 集`;
+                    const completed = Number(this.dramaDownload.progress?.completed || 0);
+                    const total = Number(this.dramaDownload.progress?.total || 0);
+                    return total > 0 ? `已完成 ${completed} / ${total}` : '';
+                },
+
+                async openTopicDownload(topic) {
+                    await this.fetchJobs();
+                    const runningJob = this.findDramaDownloadJob(topic?.title || '');
+                    if (runningJob) {
+                        this.restoreDramaDownloadJob(topic, runningJob, true);
+                        return;
+                    }
+                    this.dramaDownload = {title: topic.title || '', topic, source: 'hongguo', fallbackReason: '', candidates: [], selectedCandidate: null, selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], selectedEpisodes: [], allEpisodes: false, searching: true, episodesLoading: false, starting: false, error: '', jobId: '', jobStatus: '', progress: {}, progressMessage: '', paused: false, finished: false, background: false};
+                    this.dramaDownloadModal.show();
+                    try {
+                        const res = await aiworkerApi.request('/api/hongguo/drama/download/search', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: this.dramaDownload.title})});
+                        const data = await res.json();
+                        this.dramaDownload.source = data.source || 'hongguo';
+                        this.dramaDownload.fallbackReason = data.fallback_reason || '';
+                        if (!res.ok || data.status === 'error') throw new Error(data.message || '剧目检索失败');
+                        this.dramaDownload.candidates = data.candidates || [];
+                        if (this.dramaDownload.source === 'douyin_fallback' && this.dramaDownload.candidates.length) {
+                            this.dramaDownload.selectedCandidate = this.dramaDownload.candidates[0];
+                            const firstDownloadable = this.dramaDownload.candidates.find(candidate => !candidate.is_paid);
+                            this.dramaDownload.selectedAwemeIds = firstDownloadable ? [firstDownloadable.aweme_id] : [];
+                            this.syncDouyinDownloadSelection();
+                        }
+                        if (!this.dramaDownload.candidates.length) this.dramaDownload.error = this.dramaDownload.source === 'douyin_fallback' ? '红果和抖音都没有找到可用视频，请确认剧名后重试' : '没有找到剧名完全匹配的红果剧目，请确认剧名后重试';
+                        this.dramaDownload.searching = false;
+                        // Hongguo has a book/episode flow, but Douyin fallback always
+                        // requires an explicit user choice to avoid downloading an
+                        // unrelated promotion or clip that happens to match the title.
+                        if (this.dramaDownload.candidates.length === 1 && this.dramaDownload.source !== 'douyin_fallback') await this.selectDramaDownloadCandidate(this.dramaDownload.candidates[0]);
+                    } catch (e) {
+                        this.dramaDownload.error = e.message || '剧目检索失败';
+                    } finally {
+                        this.dramaDownload.searching = false;
+                    }
+                },
+
+                async selectDramaDownloadCandidate(candidate) {
+                    this.dramaDownload.selectedCandidate = candidate;
+                    this.dramaDownload.episodes = [];
+                    this.dramaDownload.selectedEpisodes = [];
+                    this.dramaDownload.allEpisodes = false;
+                    if (this.dramaDownload.source === 'douyin_fallback') {
+                        this.dramaDownload.episodesLoading = false;
+                        this.dramaDownload.error = '';
+                        return;
+                    }
+                    this.dramaDownload.episodesLoading = true;
+                    this.dramaDownload.error = '';
+                    try {
+                        const res = await aiworkerApi.request('/api/hongguo/drama/episodes', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({book_id: candidate.book_id})});
+                        const data = await res.json();
+                        if (!res.ok || data.status === 'error') throw new Error(data.message || '读取剧集失败');
+                        this.dramaDownload.episodes = data.episodes || [];
+                    } catch (e) {
+                        this.dramaDownload.error = e.message || '读取剧集失败';
+                    } finally {
+                        this.dramaDownload.episodesLoading = false;
+                    }
+                },
+
+                toggleAllDramaEpisodes() {
+                    if (this.dramaDownload.allEpisodes) this.dramaDownload.selectedEpisodes = this.downloadableDramaEpisodes().map(item => item.episode);
+                    else this.dramaDownload.selectedEpisodes = [];
+                },
+
+                isDramaEpisodeDownloaded(episode) {
+                    return episode.download_status?.status === 'downloaded';
+                },
+
+                downloadableDramaEpisodes() {
+                    return this.dramaDownload.episodes.filter(episode => !this.isDramaEpisodeDownloaded(episode));
+                },
+
+                dramaSelectedEpisodesForDownload() {
+                    if (this.dramaDownload.allEpisodes) return this.downloadableDramaEpisodes().map(item => item.episode);
+                    return this.dramaDownload.selectedEpisodes.filter(episode => !this.dramaDownload.episodes.find(item => Number(item.episode) === Number(episode) && this.isDramaEpisodeDownloaded(item)));
+                },
+
+                toggleAllDouyinDownloadCandidates() {
+                    const downloadableIds = this.dramaDownload.candidates.filter(candidate => !candidate.is_paid).map(candidate => candidate.aweme_id);
+                    this.dramaDownload.selectedAwemeIds = this.dramaDownload.allDouyinCandidates ? downloadableIds : [];
+                },
+
+                syncDouyinDownloadSelection() {
+                    const downloadableIds = this.dramaDownload.candidates.filter(candidate => !candidate.is_paid).map(candidate => candidate.aweme_id);
+                    this.dramaDownload.allDouyinCandidates = downloadableIds.length > 0 && downloadableIds.every(id => this.dramaDownload.selectedAwemeIds.includes(id));
+                },
+
+                selectedDouyinEpisodeSummary() {
+                    const labels = this.dramaDownload.candidates
+                        .filter(candidate => this.dramaDownload.selectedAwemeIds.includes(candidate.aweme_id) && candidate.episode_label)
+                        .map(candidate => candidate.episode_label);
+                    const unique = [...new Set(labels)];
+                    return unique.length ? `集数：${unique.join('、')}` : '';
+                },
+
+                async startDramaDownload() {
+                    const isDouyinFallback = this.dramaDownload.source === 'douyin_fallback';
+                    const episodeNumbers = this.dramaSelectedEpisodesForDownload();
+                    if (isDouyinFallback ? !this.dramaDownload.selectedAwemeIds.length : !this.dramaDownload.selectedCandidate || !episodeNumbers.length) return;
+                    this.dramaDownload.starting = true;
+                    this.dramaDownload.error = '';
+                    this.dramaDownload.progressMessage = '正在创建下载任务…';
+                    this.dramaDownload.progress = {completed: 0, total: isDouyinFallback ? this.dramaDownload.selectedAwemeIds.length : episodeNumbers.length, percent: 0, downloaded: 0, skipped_paid: 0, failed: 0};
+                    try {
+                        const endpoint = isDouyinFallback ? '/api/hongguo/drama/douyin-fallback/jobs' : '/api/hongguo/drama/download/jobs';
+                        const payload = isDouyinFallback
+                            ? {title: this.dramaDownload.title, aweme_ids: this.dramaDownload.selectedAwemeIds, fallback_reason: this.dramaDownload.fallbackReason || ''}
+                            : {title: this.dramaDownload.title, book_id: this.dramaDownload.selectedCandidate.book_id, episodes: episodeNumbers, all_episodes: this.dramaDownload.allEpisodes && episodeNumbers.length === this.dramaDownload.episodes.length};
+                        const res = await aiworkerApi.request(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
+                        const data = await res.json();
+                        if (!res.ok || data.status === 'error') throw new Error(data.message || '创建下载任务失败');
+                        this.dramaDownload.jobId = data.job.job_id;
+                        this.dramaDownload.jobStatus = data.job.status || 'pending';
+                        this.dramaDownload.progress = {...this.dramaDownload.progress, ...(data.job.progress || {})};
+                        this.dramaDownload.progressMessage = isDouyinFallback ? '抖音降级下载任务已开始' : '下载任务已开始';
+                        this.scrollDramaDownloadToTop();
+                        await this.fetchTopicLibrary();
+                        await this.fetchCreativeTopics();
+                        await this.fetchJobs();
+                    } catch (e) {
+                        this.dramaDownload.error = e.message || '创建下载任务失败';
+                    } finally {
+                        this.dramaDownload.starting = false;
+                    }
+                },
+
+                syncDramaDownloadProgress() {
+                    if (!this.dramaDownload.jobId) return;
+                    const job = this.jobs.find(item => item.job_id === this.dramaDownload.jobId);
+                    if (!job) return;
+                    this.dramaDownload.jobStatus = job.status || this.dramaDownload.jobStatus;
+                    this.dramaDownload.paused = Boolean(job.paused);
+                    this.dramaDownload.progress = {...(this.dramaDownload.progress || {}), ...(job.progress || {})};
+                    this.dramaDownload.progressMessage = job.message || this.dramaDownload.progressMessage;
+                    if (job.status === 'succeeded') {
+                        if (!this.dramaDownload.finished) {
+                            alert(`${job.message || (this.dramaDownload.source === 'douyin_fallback' ? '抖音降级视频解析完成' : '剧集下载完成')}，文件已保存到数据管理。`);
+                            this.fetchTopicLibrary();
+                            this.fetchCreativeTopics();
+                        }
+                        this.dramaDownload.finished = true;
+                    } else if (['failed', 'cancelled'].includes(job.status)) {
+                        if (!this.dramaDownload.finished) alert(`剧集下载${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || ''}`);
+                        this.dramaDownload.finished = true;
+                    }
+                },
+
+                async pauseDramaDownload() {
+                    if (!this.dramaDownload.jobId) return;
+                    const res = await aiworkerApi.request(`/api/jobs/${this.dramaDownload.jobId}/pause`, {method: 'POST'});
+                    const data = await res.json();
+                    if (!res.ok || data.status === 'error') { this.dramaDownload.error = data.message || '暂停失败'; return; }
+                    this.dramaDownload.paused = true;
+                },
+
+                async resumeDramaDownload() {
+                    if (!this.dramaDownload.jobId) return;
+                    const res = await aiworkerApi.request(`/api/jobs/${this.dramaDownload.jobId}/resume`, {method: 'POST'});
+                    const data = await res.json();
+                    if (!res.ok || data.status === 'error') { this.dramaDownload.error = data.message || '继续失败'; return; }
+                    this.dramaDownload.paused = false;
+                },
+
+                async cancelDramaDownload() {
+                    if (!this.dramaDownload.jobId) return;
+                    if (!confirm('确定取消当前下载任务吗？已完成的集数会保留。')) return;
+                    await aiworkerApi.request(`/api/jobs/${this.dramaDownload.jobId}/stop`, {method: 'POST'});
+                },
+
+                async openDramaDownloadFiles() {
+                    this.dramaDownloadModal.hide();
+                    window.location.assign('/selection/dramas');
+                },
+
+                openBreakdownEditor(source, title, record) {
+                    const fieldGuide = record?.field_guide || [];
+                    this.breakdownEditor = {
+                        source,
+                        title: title || record?.content?.title || '未命名选题',
+                        record,
+                        content: this.normalizeBreakdownEditorContent(record?.content, fieldGuide),
+                        fieldGuide,
+                        saving: false,
+                        error: ''
+                    };
+                    this.breakdownEditorModal.show();
+                },
+
+                syncBreakdownEditorRecord(record) {
+                    const fieldGuide = record?.field_guide || this.breakdownEditor.fieldGuide || [];
+                    this.breakdownEditor.record = record;
+                    this.breakdownEditor.content = this.normalizeBreakdownEditorContent(record?.content, fieldGuide);
+                    this.breakdownEditor.fieldGuide = fieldGuide;
+                    if (this.breakdownEditor.source === 'topic-library') {
+                        this.topicLibrary.breakdown = record;
+                    } else {
+                        this.creative.breakdown = record;
+                    }
+                },
+
+                normalizeBreakdownEditorContent(content, fieldGuide) {
+                    const source = content && typeof content === 'object' ? content : {};
+                    return (fieldGuide || []).reduce((result, item) => {
+                        const value = source[item.key];
+                        result[item.key] = item.type === '数组'
+                            ? (Array.isArray(value) ? value.map(entry => String(entry ?? '')).filter(Boolean) : [])
+                            : String(value ?? '');
+                        return result;
+                    }, {});
+                },
+
+                addBreakdownArrayItem(key) {
+                    if (!Array.isArray(this.breakdownEditor.content[key])) this.breakdownEditor.content[key] = [];
+                    this.breakdownEditor.content[key].push('');
+                },
+
+                removeBreakdownArrayItem(key, index) {
+                    this.breakdownEditor.content[key]?.splice(index, 1);
+                },
+
+                async saveBreakdownEditor() {
+                    const record = this.breakdownEditor.record;
+                    if (!record || record.status === 'locked') return false;
+                    const content = this.normalizeBreakdownEditorContent(this.breakdownEditor.content, this.breakdownEditor.fieldGuide);
+                    this.breakdownEditor.saving = true;
+                    this.breakdownEditor.error = '';
+                    try {
+                        const res = await aiworkerApi.request(`/api/creative/breakdowns/${record.id}`, {
+                            method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content})
+                        });
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '保存拆解失败');
+                        this.syncBreakdownEditorRecord(data);
+                        return true;
+                    } catch (e) {
+                        this.breakdownEditor.error = e.message || '保存拆解失败';
+                        return false;
+                    } finally {
+                        this.breakdownEditor.saving = false;
+                    }
+                },
+
+                async lockBreakdownEditor() {
+                    const record = this.breakdownEditor.record;
+                    if (!record || record.status === 'locked') return;
+                    if (!await this.saveBreakdownEditor()) return;
+                    this.breakdownEditor.saving = true;
+                    try {
+                        const res = await aiworkerApi.request(`/api/creative/breakdowns/${record.id}/lock`, {method: 'POST'});
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '锁定拆解失败');
+                        this.syncBreakdownEditorRecord(data);
+                        if (this.breakdownEditor.source === 'topic-library') await this.fetchTopicLibrary();
+                        if (this.breakdownEditor.source === 'creative') await this.fetchCreativeTopics();
+                        alert('拆解版本已锁定，后续修改需要创建新版本。');
+                    } catch (e) {
+                        this.breakdownEditor.error = e.message || '锁定拆解失败';
+                    } finally {
+                        this.breakdownEditor.saving = false;
+                    }
+                },
+
+                async saveTopicLibraryBreakdown() {
+                    return this.saveBreakdownEditor();
+                },
+
+                async lockTopicLibraryBreakdown() {
+                    return this.lockBreakdownEditor();
+                },
+
+                async generateCreativeTopics() {
+                    this.creative.loading = true;
+                    this.creative.error = '';
+                    try {
+                        const res = await aiworkerApi.request('/api/creative/topics/generate', {
+                            method: 'POST', headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify({platform: this.creative.platform, limit: 10})
+                        });
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '生成选题失败');
+                        this.creative.items = data.items || [];
+                    } catch (e) {
+                        this.creative.error = e.message || '生成选题失败';
+                    } finally {
+                        this.creative.loading = false;
+                    }
+                },
+
+                async updateCreativeTopic(topic, status) {
+                    const res = await aiworkerApi.request(`/api/creative/topics/${topic.id}`, {
+                        method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status})
+                    });
+                    const data = await res.json();
+                    if (!res.ok) { alert(data.detail || '更新失败'); return; }
+                    Object.assign(topic, data);
+                },
+
+                createCreativeBreakdown(topic) {
+                    return this.startTranscriptBreakdown(topic, 'creative');
+                },
+
+                async lockCreativeBreakdown() {
+                    return this.lockBreakdownEditor();
+                },
+
+}));
+
+document.querySelector('[data-workspace-page="selection-breakdowns"]')?.setAttribute('data-ready', 'true');
diff --git a/static/js/selection/dramas.js b/static/js/selection/dramas.js
new file mode 100644
index 0000000..4e2a3a9
--- /dev/null
+++ b/static/js/selection/dramas.js
@@ -0,0 +1,169 @@
+window.aiworkerModules = window.aiworkerModules || [];
+window.aiworkerModules.push(() => ({
+                async searchDrama() {
+                    const title = this.dramaAnalysis.title.trim();
+                    if (!title) return;
+                    clearTimeout(this.dramaAnalysis.suggestionTimer);
+                    this.dramaAnalysis.suggestionRequestId += 1;
+                    this.dramaAnalysis.suggestions = [];
+                    this.dramaAnalysis.loading = true;
+                    this.dramaAnalysis.error = '';
+                    this.dramaAnalysis.candidates = [];
+                    this.dramaAnalysis.selected = null;
+                    this.dramaAnalysis.episodes = [];
+                    this.dramaAnalysis.episode = 0;
+                    this.dramaAnalysis.searchSource = '';
+                    this.dramaAnalysis.fallbackReason = '';
+                    try {
+                        const res = await aiworkerApi.request('/api/hongguo/drama/search', {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify({title})
+                        });
+                        const data = await res.json();
+                        if (!res.ok || data.status === 'error') throw new Error(data.message || '搜索失败');
+                        this.dramaAnalysis.candidates = data.candidates || [];
+                        this.dramaAnalysis.searchSource = data.source || 'hongguo';
+                        this.dramaAnalysis.fallbackReason = data.fallback_reason || '';
+                        if (!this.dramaAnalysis.candidates.length) this.dramaAnalysis.error = '未找到匹配剧目';
+                    } catch (e) {
+                        this.dramaAnalysis.error = e.message || '搜索剧目失败';
+                    } finally {
+                        this.dramaAnalysis.loading = false;
+                    }
+                },
+
+                onDramaTitleInput() {
+                    clearTimeout(this.dramaAnalysis.suggestionTimer);
+                    const requestId = ++this.dramaAnalysis.suggestionRequestId;
+                    this.dramaAnalysis.suggestions = [];
+                    const title = this.dramaAnalysis.title.trim();
+                    if (title.length < 2) return;
+                    this.dramaAnalysis.suggestionTimer = setTimeout(async () => {
+                        this.dramaAnalysis.suggestionLoading = true;
+                        try {
+                            const res = await aiworkerApi.request('/api/hongguo/drama/suggest', {
+                                method: 'POST',
+                                headers: {'Content-Type': 'application/json'},
+                                body: JSON.stringify({title})
+                            });
+                            const data = await res.json();
+                            if (requestId !== this.dramaAnalysis.suggestionRequestId) return;
+                            this.dramaAnalysis.suggestions = res.ok && data.status !== 'error' ? (data.candidates || []) : [];
+                        } catch (e) {
+                            if (requestId === this.dramaAnalysis.suggestionRequestId) this.dramaAnalysis.suggestions = [];
+                        } finally {
+                            if (requestId === this.dramaAnalysis.suggestionRequestId) this.dramaAnalysis.suggestionLoading = false;
+                        }
+                    }, 350);
+                },
+
+                chooseDramaSuggestion(suggestion) {
+                    this.dramaAnalysis.title = suggestion.title || this.dramaAnalysis.title;
+                    this.dramaAnalysis.suggestions = [];
+                    this.searchDrama();
+                },
+
+                async selectDramaCandidate(candidate) {
+                    this.dramaAnalysis.error = '';
+                    this.dramaAnalysis.selected = candidate;
+                    this.dramaAnalysis.episodes = [];
+                    this.dramaAnalysis.episode = 0;
+                    if (this.dramaAnalysis.searchSource === 'douyin_fallback') return;
+                    this.dramaAnalysis.loading = true;
+                    try {
+                        const res = await aiworkerApi.request('/api/hongguo/drama/episodes', {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify({book_id: candidate.book_id})
+                        });
+                        const data = await res.json();
+                        if (!res.ok || data.status === 'error') throw new Error(data.message || '读取剧集失败');
+                        this.dramaAnalysis.episodes = data.episodes || [];
+                        this.dramaAnalysis.episode = this.dramaAnalysis.episodes[0]?.episode || 0;
+                    } catch (e) {
+                        this.dramaAnalysis.error = e.message || '读取剧集失败';
+                        this.dramaAnalysis.selected = null;
+                    } finally {
+                        this.dramaAnalysis.loading = false;
+                    }
+                },
+
+                async startDramaAnalysis() {
+                    const selected = this.dramaAnalysis.selected;
+                    const isDouyinFallback = this.dramaAnalysis.searchSource === 'douyin_fallback';
+                    if (!selected || (isDouyinFallback ? !selected.aweme_id : !this.dramaAnalysis.episode)) return;
+                    this.dramaAnalysis.loading = true;
+                    this.dramaAnalysis.error = '';
+                    try {
+                        const endpoint = isDouyinFallback
+                            ? '/api/hongguo/drama/douyin-fallback/jobs'
+                            : '/api/hongguo/drama/jobs';
+                        const jobPayload = isDouyinFallback
+                            ? {
+                                title: this.dramaAnalysis.title || selected.title,
+                                aweme_id: selected.aweme_id,
+                                fallback_reason: this.dramaAnalysis.fallbackReason
+                            }
+                            : {
+                                title: selected.title || this.dramaAnalysis.title,
+                                book_id: selected.book_id,
+                                episode: this.dramaAnalysis.episode
+                            };
+                        const res = await aiworkerApi.request(endpoint, {
+                            method: 'POST',
+                            headers: {'Content-Type': 'application/json'},
+                            body: JSON.stringify(jobPayload)
+                        });
+                        const data = await res.json();
+                        if (!res.ok || data.status === 'error') throw new Error(data.message || '创建下载任务失败');
+                        this.dramaAnalysis.jobId = data.job.job_id;
+                        this.dramaAnalysis.notifiedStatus = '';
+                        this.selectedJobId = data.job.job_id;
+                        await this.fetchJobs();
+                        await this.fetchLogs();
+                        alert(isDouyinFallback
+                            ? '抖音降级下载任务已创建，正在下载已确认的视频并生成转写。完成后会弹窗提醒。'
+                            : '剧集下载任务已创建，正在下载并生成转写。完成后会弹窗提醒。');
+                    } catch (e) {
+                        this.dramaAnalysis.error = e.message || '创建下载任务失败';
+                    } finally {
+                        this.dramaAnalysis.loading = false;
+                    }
+                },
+
+                dramaJobArtifacts() {
+                    return this.jobs.find(job => job.job_id === this.dramaAnalysis.jobId)?.artifacts || [];
+                },
+
+                formatDramaDuration(value) {
+                    const seconds = Math.max(0, Math.round(Number(value) / 1000));
+                    if (!seconds) return '未知';
+                    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
+                },
+
+                formatDramaBytes(value) {
+                    const bytes = Math.max(0, Number(value || 0));
+                    if (bytes < 1024) return `${bytes} B`;
+                    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
+                    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
+                },
+
+                notifyDramaJobCompletion() {
+                    const job = this.jobs.find(item => item.job_id === this.dramaAnalysis.jobId);
+                    if (!job || !['succeeded', 'failed', 'cancelled'].includes(job.status)) return;
+                    const marker = `${job.job_id}:${job.status}`;
+                    if (this.dramaAnalysis.notifiedStatus === marker) return;
+                    this.dramaAnalysis.notifiedStatus = marker;
+                    if (job.status === 'succeeded') {
+                        const artifactCount = (job.artifacts || []).length;
+                        const source = job.job?.crawler_type === 'douyin_fallback_analysis' ? '抖音降级视频' : '剧目';
+                        alert(`${source}下载与解析完成，已生成 ${artifactCount} 个本地文件。`);
+                        return;
+                    }
+                    alert(`剧目下载未完成：${job.message || job.status}`);
+                },
+
+}));
+
+document.querySelector('[data-workspace-page="selection-dramas"]')?.setAttribute('data-ready', 'true');
diff --git a/static/js/selection/ranks.js b/static/js/selection/ranks.js
new file mode 100644
index 0000000..5dea368
--- /dev/null
+++ b/static/js/selection/ranks.js
@@ -0,0 +1,340 @@
+window.aiworkerModules = window.aiworkerModules || [];
+window.aiworkerModules.push(() => ({
+                async fetchTodayRank() {
+                    this.todayRankLoading = true;
+                    this.todayRankError = '';
+                    const params = new URLSearchParams({
+                        platform: this.todayRankPlatform || 'dy',
+                        _: String(Date.now())
+                    });
+                    if (this.todayRankDate) params.set('date', this.todayRankDate);
+                    try {
+                        const res = await aiworkerApi.request(`/api/rank/today?${params.toString()}`);
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '加载今日榜单失败');
+                        if (this.todayRankDate && !(data.available_dates || []).includes(this.todayRankDate)) {
+                            this.todayRankDate = '';
+                            this.todayRankData = data;
+                            await this.fetchTodayRank();
+                            return;
+                        }
+                        this.todayRankData = data;
+                        if (this.todayRankPlatform === 'hg') {
+                            this.ensureHongguoRoute(true);
+                        }
+                    } catch (e) {
+                        this.todayRankData = null;
+                        this.todayRankError = e.message || '加载今日榜单失败';
+                    } finally {
+                        this.todayRankLoading = false;
+                    }
+                },
+
+                parseTodayRankHash(shouldFetch = false) {
+                    const raw = window.location.hash || '';
+                    const path = raw.replace(/^#\/?/, '');
+                    if (!path) return false;
+                    const parts = path.split('/').map(part => decodeURIComponent(part || ''));
+                    if (parts[0] !== 'today-rank') return false;
+                    const nextPlatform = parts[1] === 'hg' ? 'hg' : 'dy';
+                    const platformChanged = this.todayRankPlatform !== nextPlatform;
+                    this.todayRankPlatform = nextPlatform;
+                    if (nextPlatform === 'hg') {
+                        this.todayRankSectionKey = parts[2] || this.todayRankSectionKey;
+                        this.todayRankRankingKey = parts[3] || this.todayRankRankingKey;
+                        this.ensureHongguoRoute(false);
+                    }
+                    if (shouldFetch && platformChanged) this.fetchTodayRank();
+                    return true;
+                },
+
+                setTodayRankRoute(platform = 'dy', sectionKey = '', rankingKey = '') {
+                    const nextPlatform = platform === 'hg' ? 'hg' : 'dy';
+                    const platformChanged = this.todayRankPlatform !== nextPlatform;
+                    this.todayRankPlatform = nextPlatform;
+                    if (nextPlatform === 'hg') {
+                        const section = this.resolveHongguoSection(sectionKey || this.todayRankSectionKey);
+                        this.todayRankSectionKey = section?.key || sectionKey || '';
+                        const ranking = this.resolveHongguoRanking(this.todayRankSectionKey, rankingKey);
+                        this.todayRankRankingKey = ranking?.key || rankingKey || '';
+                    }
+                    const target = this.todayRankRouteHash();
+                    if (window.location.hash !== target) {
+                        window.location.hash = target;
+                        if (platformChanged) this.fetchTodayRank();
+                    } else {
+                        this.fetchTodayRank();
+                    }
+                },
+
+                todayRankRouteHash() {
+                    if (this.todayRankPlatform !== 'hg') return '#/today-rank/dy';
+                    const parts = ['#/today-rank', 'hg'];
+                    const section = this.selectedHongguoSection();
+                    const ranking = this.selectedHongguoRanking();
+                    const sectionSlug = section?.label || this.todayRankSectionKey;
+                    const rankingSlug = ranking?.label || this.todayRankRankingKey;
+                    if (sectionSlug) parts.push(encodeURIComponent(sectionSlug));
+                    if (rankingSlug) parts.push(encodeURIComponent(rankingSlug));
+                    return parts.join('/');
+                },
+
+                todayRankTables() {
+                    const data = this.todayRankData || {};
+                    return [
+                        {key: 'new', title: '新上榜', mode: 'base', items: data.new_entries || []},
+                        {key: 'dropped', title: '下榜', mode: 'base', items: data.dropped_entries || []},
+                        {key: 'rank-up', title: '排名上升', mode: 'rank', items: data.rank_up || []},
+                        {key: 'rank-down', title: '排名下降', mode: 'rank', items: data.rank_down || []},
+                        {key: 'heat-growth', title: '热度增长 Top10', mode: 'heat', items: data.heat_growth_top || []},
+                        {key: 'like-growth', title: '点赞增长 Top10', mode: 'like', items: data.like_growth_top || []}
+                    ];
+                },
+
+                todayRankSections() {
+                    const data = this.todayRankData || {};
+                    if (Array.isArray(data.sections) && data.sections.length) return data.sections;
+                    return [{
+                        key: 'all',
+                        label: '全部',
+                        summary: data.summary || {},
+                        rankings: [{
+                            key: 'all-default',
+                            label: '综合',
+                            summary: data.summary || {},
+                            new_entries: data.new_entries || [],
+                            dropped_entries: data.dropped_entries || [],
+                            rank_up: data.rank_up || [],
+                            rank_down: data.rank_down || [],
+                            current_entries: data.current_entries || [],
+                            columns: data.columns || {}
+                        }]
+                    }];
+                },
+
+                hongguoSections() {
+                    const data = this.todayRankData || {};
+                    return Array.isArray(data.sections) ? data.sections : [];
+                },
+
+                hongguoRankings() {
+                    return this.selectedHongguoSection()?.rankings || [];
+                },
+
+                selectedHongguoSection() {
+                    return this.resolveHongguoSection(this.todayRankSectionKey);
+                },
+
+                selectedHongguoRanking() {
+                    return this.resolveHongguoRanking(this.todayRankSectionKey, this.todayRankRankingKey);
+                },
+
+                resolveHongguoSection(sectionKey = '') {
+                    const sections = this.hongguoSections();
+                    return sections.find(section => section.key === sectionKey || section.label === sectionKey) || sections[0] || null;
+                },
+
+                resolveHongguoRanking(sectionKey = '', rankingKey = '') {
+                    const section = this.resolveHongguoSection(sectionKey);
+                    const rankings = section?.rankings || [];
+                    return rankings.find(ranking => ranking.key === rankingKey || ranking.label === rankingKey) || rankings[0] || null;
+                },
+
+                ensureHongguoRoute(updateHash = false) {
+                    if (this.todayRankPlatform !== 'hg') return;
+                    const section = this.resolveHongguoSection(this.todayRankSectionKey);
+                    this.todayRankSectionKey = section?.key || '';
+                    const ranking = this.resolveHongguoRanking(this.todayRankSectionKey, this.todayRankRankingKey);
+                    this.todayRankRankingKey = ranking?.key || '';
+                    if (updateHash) {
+                        const target = this.todayRankRouteHash();
+                        if (window.location.hash !== target) window.location.hash = target;
+                    }
+                },
+
+                rankingTables(ranking) {
+                    const columns = ranking.columns || {};
+                    return [
+                        {key: 'current', title: '当前榜单', mode: 'current', items: ranking.current_entries || [], columns: this.rankColumnsForTable(columns.current, ranking.current_entries || [], 'current')},
+                        {key: 'new', title: '新上榜', mode: 'base', items: ranking.new_entries || [], columns: this.rankColumnsForTable(columns.new, ranking.new_entries || [], 'base')},
+                        {key: 'dropped', title: '下榜', mode: 'base', items: ranking.dropped_entries || [], columns: this.rankColumnsForTable(columns.dropped, ranking.dropped_entries || [], 'base')},
+                        {key: 'rank-up', title: '排名上升', mode: 'rank', items: ranking.rank_up || [], columns: this.rankColumnsForTable(columns.rank_up, ranking.rank_up || [], 'rank')},
+                        {key: 'rank-down', title: '排名下降', mode: 'rank', items: ranking.rank_down || [], columns: this.rankColumnsForTable(columns.rank_down, ranking.rank_down || [], 'rank')}
+                    ];
+                },
+
+                getRankingActiveTable(rankingKey) {
+                    return this.rankingActiveTables[rankingKey] || 'current';
+                },
+
+                setRankingActiveTable(rankingKey, tableKey) {
+                    this.rankingActiveTables = { ...this.rankingActiveTables, [rankingKey]: tableKey };
+                },
+
+                summaryText(summary = {}) {
+                    return `上榜 ${summary.new_count || 0} / 下榜 ${summary.dropped_count || 0} / 上升 ${summary.rank_up_count || 0} / 下降 ${summary.rank_down_count || 0}`;
+                },
+
+                entryStatus(item = {}, tableKey = '') {
+                    if (tableKey === 'dropped') return '下榜';
+                    if (item.rank_delta > 0) return `上升 ${item.rank_delta}`;
+                    if (item.rank_delta < 0) return `下降 ${Math.abs(item.rank_delta)}`;
+                    if (item.previous_rank) return '在榜';
+                    return '新上榜';
+                },
+
+                entryStatusClass(item = {}, tableKey = '') {
+                    if (tableKey === 'dropped') return 'status-dropped';
+                    if (item.rank_delta > 0) return 'status-up';
+                    if (item.rank_delta < 0) return 'status-down';
+                    if (item.previous_rank) return 'status-stable';
+                    return 'status-new';
+                },
+
+                rankNumberClass(rank) {
+                    const number = Number(rank || 0);
+                    if (number >= 1 && number <= 3) return `rank-top-${number}`;
+                    return '';
+                },
+
+                rankColumnsForTable(columns = [], items = [], mode = '') {
+                    if (Array.isArray(columns) && columns.length) return columns;
+                    const common = [
+                        {key: 'rank', label: '排名', kind: 'rank', width: 55},
+                        {key: 'title', label: '剧名', kind: 'title', width: 220},
+                        {key: 'category', label: '分类', kind: 'category', width: 100},
+                        {key: 'status', label: '状态', kind: 'status', width: 100}
+                    ];
+                    const candidates = [
+                        {key: 'subject', label: '标签', kind: 'text', width: 150},
+                        {key: 'description', label: '简介', kind: 'text', width: 260},
+                        {key: 'score', label: '评分', kind: 'metric', width: 90},
+                        {key: 'share_count', label: '分享', kind: 'metric', width: 90},
+                        {key: 'like_count', label: '点赞', kind: 'metric', width: 95, icon: 'bi-hand-thumbs-up'},
+                        {key: 'favorite_count', label: '收藏', kind: 'metric', width: 95, icon: 'bi-star'},
+                        {key: 'heat', label: '热度', kind: 'metric', width: 95, icon: 'bi-fire'},
+                        {key: 'comment_count', label: '评论', kind: 'metric', width: 95, icon: 'bi-chat-dots'},
+                        {key: 'recommend_index', label: '推荐指数', kind: 'metric', width: 105},
+                        {key: 'rating_count', label: '评分人数', kind: 'metric', width: 105},
+                        {key: 'expectation_index', label: '期待指数', kind: 'metric', width: 105},
+                        {key: 'search_index', label: '热搜指数', kind: 'metric', width: 105},
+                        {key: 'max_heat', label: '最高热度', kind: 'metric', width: 105},
+                        {key: 'hot_comment', label: '热评', kind: 'text', width: 240},
+                        {key: 'rank_duration', label: '在榜时长', kind: 'rank_duration', width: 210}
+                    ];
+                    const dynamic = candidates.filter(column => items.some(item => {
+                        const value = column.key === 'subject' ? item.subject || item.topic_tags : item[column.key];
+                        return this.hasRankColumnValue(value);
+                    }));
+                    const rankChange = mode === 'rank'
+                        ? [{key: 'previous_rank', label: '上期排名', kind: 'metric', width: 90}, {key: 'rank_delta', label: '排名变化', kind: 'rank_delta', width: 105}]
+                        : [];
+                    return [...common, ...dynamic, ...rankChange];
+                },
+
+                hasRankColumnValue(value) {
+                    if (value === null || value === undefined) return false;
+                    if (typeof value === 'string') return !['', '-', '无', '暂无', 'null', 'None'].includes(value.trim());
+                    if (Array.isArray(value)) return value.length > 0;
+                    return true;
+                },
+
+                rankColumnHeaderClass(column = {}) {
+                    return column.kind === 'metric' ? 'rank-metric' : '';
+                },
+
+                rankColumnCellClass(column = {}) {
+                    return column.kind === 'metric' ? 'rank-metric' : '';
+                },
+
+                rankColumnStyle(column = {}) {
+                    return column.width ? `min-width: ${column.width}px;` : '';
+                },
+
+                rankColumnValue(item = {}, column = {}) {
+                    const value = item[column.key];
+                    if (column.key === 'subject') return value || this.shortTags(item.topic_tags);
+                    if (column.kind === 'rank_duration') return this.formatRankDuration(item);
+                    if (column.kind === 'text') return this.shortText(value, 64);
+                    return this.formatMetric(value);
+                },
+
+                formatRankDuration(item = {}) {
+                    if (item.rank_duration) return item.rank_duration;
+                    const continuous = Number(item.continuous_rank_days || 0);
+                    const cumulative = Number(item.cumulative_rank_days || 0);
+                    if (!continuous || !cumulative) return '-';
+                    return `连续在榜${continuous}天 / 累计在榜${cumulative}天`;
+                },
+
+                rankColumnTooltip(item = {}, column = {}, tbl = {}) {
+                    if (column.kind === 'status') return this.entryStatus(item, tbl.key);
+                    if (column.kind === 'rank_delta') return this.formatRankDelta(item.rank_delta);
+                    if (column.kind === 'rank') return String(item.rank || '-');
+                    if (column.kind === 'category') return item.board_type || item.category_label || '-';
+                    if (column.key === 'subject') return item.subject || this.shortTags(item.topic_tags);
+                    const value = item[column.key];
+                    return value === null || value === undefined || value === '' ? '-' : String(value);
+                },
+
+                rankingTableColspan(tbl = {}) {
+                    return Math.max((tbl.columns || []).length, 4);
+                },
+
+                todayRankDates() {
+                    return this.todayRankData?.available_dates || [];
+                },
+
+                formatMetric(value) {
+                    if (value === null || value === undefined || value === '') return '缺失';
+                    const number = Number(value || 0);
+                    if (!Number.isFinite(number)) return '0';
+                    if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)}亿`;
+                    if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}万`;
+                    return Number.isInteger(number) ? String(number) : number.toFixed(2);
+                },
+
+                formatDelta(value) {
+                    const number = Number(value || 0);
+                    if (!Number.isFinite(number) || number === 0) return '0';
+                    return `${number > 0 ? '+' : ''}${this.formatMetric(number)}`;
+                },
+
+                formatRankDelta(value) {
+                    const number = Number(value || 0);
+                    if (!Number.isFinite(number) || number === 0) return '0';
+                    return number > 0 ? `上升 ${number}` : `下降 ${Math.abs(number)}`;
+                },
+
+                shortTags(tags) {
+                    const values = Array.isArray(tags) ? tags.filter(Boolean) : [];
+                    return values.length ? values.slice(0, 3).join(' / ') : '-';
+                },
+
+                shortText(value, maxLength = 18) {
+                    const text = String(value || '').trim();
+                    return text ? (text.length > maxLength ? `${text.slice(0, maxLength)}...` : text) : '-';
+                },
+
+                warningText(warnings) {
+                    const labels = {
+                        heat_missing: '热度缺失',
+                        favorite_missing: '收藏未采集',
+                        like_missing: '点赞缺失',
+                        comment_missing: '评论缺失',
+                        title_missing: '标题缺失',
+                        category_missing: '分类缺失'
+                    };
+                    return (warnings || []).map(item => labels[item] || item).join('；');
+                },
+
+                shortLabel(value) {
+                    const text = String(value || '');
+                    return text.length > 8 ? `${text.slice(0, 8)}...` : text;
+                },
+
+}));
+
+document.addEventListener('visibilitychange', () => {
+    if (!document.hidden) window.dispatchEvent(new Event('resize'));
+});
diff --git a/static/js/selection/topics.js b/static/js/selection/topics.js
new file mode 100644
index 0000000..e4f8311
--- /dev/null
+++ b/static/js/selection/topics.js
@@ -0,0 +1,94 @@
+window.aiworkerModules = window.aiworkerModules || [];
+window.aiworkerModules.push(() => ({
+                async fetchCreativeTopics() {
+                    this.creative.loading = true;
+                    this.creative.error = '';
+                    try {
+                        const res = await aiworkerApi.request(`/api/creative/topics?platform=${encodeURIComponent(this.creative.platform)}`);
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '读取选题失败');
+                        this.creative.items = data.items || [];
+                    } catch (e) {
+                        this.creative.error = e.message || '读取选题失败';
+                    } finally {
+                        this.creative.loading = false;
+                    }
+                },
+
+                async fetchTopicLibrary() {
+                    this.topicLibrary.loading = true;
+                    this.topicLibrary.error = '';
+                    try {
+                        const params = new URLSearchParams({limit: '500'});
+                        if (this.topicLibrary.platform) params.set('platform', this.topicLibrary.platform);
+                        if (this.topicLibrary.lifecycleStatus) params.set('lifecycle_status', this.topicLibrary.lifecycleStatus);
+                        if (this.topicLibrary.category.trim()) params.set('category', this.topicLibrary.category.trim());
+                        if (this.topicLibrary.minScore !== '') params.set('min_score', this.topicLibrary.minScore);
+                        if (this.topicLibrary.hasLocked !== '') params.set('has_locked_breakdown', this.topicLibrary.hasLocked);
+                        const res = await aiworkerApi.request(`/api/creative/topics?${params.toString()}`);
+                        const data = await res.json();
+                        if (!res.ok) throw new Error(data.detail || '读取选题库失败');
+                        this.topicLibrary.items = data.items || [];
+                    } catch (e) {
+                        this.topicLibrary.error = e.message || '读取选题库失败';
+                    } finally {
+                        this.topicLibrary.loading = false;
+                    }
+                },
+
+                topicStatusLabel(status) {
+                    return {pending_review: '待评估', adopted: '已采纳', rejected: '不跟进', archived: '已归档'}[status] || status || '-';
+                },
+
+                topicStatusClass(status) {
+                    return {adopted: 'bg-success', rejected: 'bg-danger', archived: 'bg-secondary', pending_review: 'bg-warning text-dark'}[status] || 'bg-secondary';
+                },
+
+                topicLifecycleLabel(topic) {
+                    const status = topic?.lifecycle_status || topic?.status || 'pending_review';
+                    if (status === 'downloaded') return `已下载 ${Number(topic?.downloaded_episode_count || 0)} 集`;
+                    return {
+                        pending_review: '待评估', adopted: '已采纳', pending_download: '待下载',
+                        pending_breakdown: '待拆解', breakdown_completed: '已拆解',
+                        pending_imitation: '待仿制', imitation_generated: '已仿制',
+                        pending_character_relationships: '人物关系待生成', character_relationships_generated: '人物关系已生成',
+                        pending_episode_outlines: '分集大纲待生成', episode_outlines_generated: '分集大纲已生成',
+                        pending_beats: 'Beats脚本待生成', beats_generated: 'Beats脚本已生成',
+                        beats_exported: 'Beats脚本已导出', rejected: '不跟进', archived: '已归档'
+                    }[status] || status;
+                },
+
+                topicLifecycleClass(status) {
+                    const value = status || 'pending_review';
+                    return `lifecycle-${value.replaceAll('_', '-')}`;
+                },
+
+                breakdownStatusLabel(topic) {
+                    if (!topic?.latest_breakdown_id) return '未拆解';
+                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
+                    if (topic.latest_breakdown_status === 'locked') return `已锁定${version}`;
+                    return topic.locked_breakdown_id
+                        ? `草稿${version} · 已有锁定版`
+                        : `草稿${version} · 待锁定`;
+                },
+
+                breakdownStatusClass(topic) {
+                    if (!topic?.latest_breakdown_id) return 'bg-secondary';
+                    if (topic.latest_breakdown_status === 'locked') return 'bg-success';
+                    return topic.locked_breakdown_id ? 'bg-info text-dark' : 'bg-warning text-dark';
+                },
+
+                creativeTopicOptionLabel(topic) {
+                    if (!topic?.latest_breakdown_id) return `${topic.title}（未拆解）`;
+                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
+                    if (!topic.locked_breakdown_id) return `${topic.title}（草稿${version}，需先锁定）`;
+                    if (topic.latest_breakdown_status === 'draft') return `${topic.title}（已有锁定版，最新草稿${version}）`;
+                    return `${topic.title}（已锁定${version}）`;
+                },
+
+}));
+
+window.addEventListener('aiworker:permission-denied', (event) => {
+    const target = document.querySelector('[x-data]');
+    if (target) target.setAttribute('data-permission-message', event.detail.message);
+});
diff --git a/static/js/writing/beats.js b/static/js/writing/beats.js
new file mode 100644
index 0000000..256f534
--- /dev/null
+++ b/static/js/writing/beats.js
@@ -0,0 +1 @@
+document.querySelector('[data-workspace-page="writing-beats"]')?.setAttribute('data-ready', 'true');
diff --git a/static/js/writing/outlines.js b/static/js/writing/outlines.js
new file mode 100644
index 0000000..46b931f
--- /dev/null
+++ b/static/js/writing/outlines.js
@@ -0,0 +1 @@
+document.querySelector('[data-workspace-page="writing-outlines"]')?.setAttribute('data-ready', 'true');
diff --git a/templates/admin/analytics.html b/templates/admin/analytics.html
new file mode 100644
index 0000000..18b11da
--- /dev/null
+++ b/templates/admin/analytics.html
@@ -0,0 +1,4 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}{% include "common/workspace/admin_analytics.html" %}{% endblock %}
+{% block scripts %}<script src="/static/js/admin/workspace.js"></script>{% endblock %}
diff --git a/templates/admin/audit.html b/templates/admin/audit.html
new file mode 100644
index 0000000..47912fb
--- /dev/null
+++ b/templates/admin/audit.html
@@ -0,0 +1,7 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between mb-3"><div><h3 class="mb-1">审计日志</h3><div class="small text-muted">查询关键操作、操作者和结果</div></div><button class="btn btn-outline-secondary btn-sm" id="refresh-audit" title="刷新"><i class="bi bi-arrow-clockwise"></i></button></div>
+<div class="table-responsive"><table class="table table-sm align-middle"><thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>资源</th><th>结果</th></tr></thead><tbody id="audit-list"><tr><td colspan="5" class="text-center text-muted py-4">正在加载审计日志...</td></tr></tbody></table></div>
+{% endblock %}
+{% block scripts %}<script src="/static/js/admin/audit.js"></script>{% endblock %}
diff --git a/templates/admin/config.html b/templates/admin/config.html
new file mode 100644
index 0000000..86e1acd
--- /dev/null
+++ b/templates/admin/config.html
@@ -0,0 +1,14 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<section class="mb-4">
+    <div class="d-flex align-items-center justify-content-between mb-3"><div><h3 class="mb-1">剧目接口设置</h3><div class="small text-muted">仅系统管理员可修改红果配置中心与激活凭据</div></div><button class="btn btn-outline-secondary btn-sm" @click="fetchDramaSettings" title="刷新"><i class="bi bi-arrow-clockwise"></i></button></div>
+    <div class="card"><div class="card-body"><div class="row g-3 align-items-end">
+        <div class="col-lg-6"><label class="form-label">配置中心地址</label><input class="form-control" x-model="dramaAnalysis.settings.config_url"></div>
+        <div class="col-lg-4"><label class="form-label">激活码</label><input class="form-control" type="password" x-model="dramaAnalysis.activationCode" placeholder="留空则保留已保存的激活码"><div class="small text-muted mt-1" x-text="dramaAnalysis.settings.has_activation_code ? '已保存激活码' : '未保存激活码'"></div></div>
+        <div class="col-lg-2"><div class="d-grid gap-2"><button class="btn btn-outline-info" @click="testDramaInterface" :disabled="dramaAnalysis.loading"><i class="bi bi-plug me-1"></i>测试接口</button><button class="btn btn-primary" @click="saveDramaSettings" :disabled="dramaAnalysis.loading"><i class="bi bi-save me-1"></i>保存设置</button></div></div>
+    </div><div class="alert mt-3 mb-0 py-2" x-show="dramaAnalysis.interfaceTest" :class="dramaAnalysis.interfaceTest?.status === 'success' ? 'alert-success' : 'alert-danger'" x-text="dramaAnalysis.interfaceTest?.message"></div></div></div>
+</section>
+{% include "common/workspace/admin_config.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/admin/workspace.js"></script>{% endblock %}
diff --git a/templates/admin/data.html b/templates/admin/data.html
new file mode 100644
index 0000000..356cf8f
--- /dev/null
+++ b/templates/admin/data.html
@@ -0,0 +1,4 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}{% include "common/workspace/admin_data.html" %}{% include "common/workspace/admin_data_modals.html" %}{% endblock %}
+{% block scripts %}<script src="/static/js/admin/workspace.js"></script>{% endblock %}
diff --git a/templates/admin/deliveries.html b/templates/admin/deliveries.html
new file mode 100644
index 0000000..bed28dd
--- /dev/null
+++ b/templates/admin/deliveries.html
@@ -0,0 +1,7 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between mb-3"><div><h3 class="mb-1">成果交付</h3><div class="small text-muted">按已批准版本生成阶段交付快照</div></div><button class="btn btn-outline-secondary btn-sm" @click="fetchCreativeProjects()"><i class="bi bi-arrow-clockwise"></i></button></div>
+<table class="table table-sm align-middle"><thead><tr><th>项目</th><th>状态</th><th>Brief</th><th>人物关系</th><th>大纲</th><th>Beats</th></tr></thead><tbody><template x-for="project in creativeWorkspace.projects" :key="project.id"><tr><td x-text="project.name"></td><td x-text="creativeProjectStatusLabel(project.status)"></td><td><a :href="`/api/creative/projects/${project.id}/export?stage=brief&format=md`">导出</a></td><td><a :href="`/api/creative/projects/${project.id}/export?stage=characters&format=md`">导出</a></td><td><a :href="`/api/creative/projects/${project.id}/export?stage=outline&format=md`">导出</a></td><td><a :href="`/api/creative/projects/${project.id}/export?stage=beats&format=md`">导出</a></td></tr></template><tr x-show="!creativeWorkspace.projects.length"><td colspan="6" class="text-center text-muted py-4">暂无可交付项目</td></tr></tbody></table>
+{% endblock %}
+{% block scripts %}<script src="/static/js/common/creative.js"></script><script src="/static/js/admin/deliveries.js"></script>{% endblock %}
diff --git a/templates/admin/jobs.html b/templates/admin/jobs.html
new file mode 100644
index 0000000..4492c7b
--- /dev/null
+++ b/templates/admin/jobs.html
@@ -0,0 +1,4 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}{% include "common/workspace/admin_jobs.html" %}{% include "common/workspace/admin_data_modals.html" %}{% endblock %}
+{% block scripts %}<script src="/static/js/admin/workspace.js"></script><script src="/static/js/admin/jobs.js"></script>{% endblock %}
diff --git a/templates/admin/roles.html b/templates/admin/roles.html
new file mode 100644
index 0000000..eee931c
--- /dev/null
+++ b/templates/admin/roles.html
@@ -0,0 +1,16 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between mb-3"><div><h3 class="mb-1">角色权限</h3><div class="small text-muted">维护角色权限与数据范围</div></div><button class="btn btn-primary btn-sm" id="create-role"><i class="bi bi-plus-lg me-1"></i>新建角色</button></div>
+<div class="alert alert-danger d-none" id="role-error" role="alert"></div>
+<div class="table-responsive"><table class="table table-sm align-middle"><thead><tr><th>角色</th><th>代码</th><th>权限数</th><th>状态</th><th>操作</th></tr></thead><tbody id="role-list"><tr><td colspan="5" class="text-center text-muted py-4">正在加载角色...</td></tr></tbody></table></div>
+
+<div class="modal fade" id="role-editor-modal" tabindex="-1" aria-labelledby="role-editor-title" aria-hidden="true"><div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable"><form class="modal-content" id="role-editor-form"><div class="modal-header"><h5 class="modal-title" id="role-editor-title">新建角色</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button></div><div class="modal-body">
+    <input type="hidden" id="role-id">
+    <div class="row g-3 mb-3"><div class="col-md-4"><label class="form-label" for="role-code">角色代码</label><input class="form-control" id="role-code" pattern="[a-z][a-z0-9_.-]*" required></div><div class="col-md-4"><label class="form-label" for="role-name">角色名称</label><input class="form-control" id="role-name" required></div><div class="col-md-4"><label class="form-label" for="role-description">说明</label><input class="form-control" id="role-description"></div></div>
+    <div class="d-flex align-items-center justify-content-between mb-2"><h6 class="mb-0">权限与数据范围</h6><span class="small text-muted">勾选高级权限时，服务端会自动补齐依赖权限</span></div>
+    <div class="table-responsive border" id="permission-editor"><table class="table table-sm align-middle mb-0"><thead><tr><th style="width:48px"></th><th>权限</th><th>代码</th><th style="width:180px">数据范围</th></tr></thead><tbody id="permission-list"></tbody></table></div>
+    <div class="alert alert-danger d-none mt-3 mb-0" id="role-form-error" role="alert"></div>
+</div><div class="modal-footer"><button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">取消</button><button class="btn btn-primary" id="save-role" type="submit"><i class="bi bi-save me-1"></i>保存</button></div></form></div></div></div>
+{% endblock %}
+{% block scripts %}<script src="/static/js/admin/roles.js"></script>{% endblock %}
diff --git a/templates/admin/users.html b/templates/admin/users.html
new file mode 100644
index 0000000..3632c87
--- /dev/null
+++ b/templates/admin/users.html
@@ -0,0 +1,17 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between mb-3"><div><h3 class="mb-1">用户管理</h3><div class="small text-muted">管理本地账号、状态与角色分配</div></div><button class="btn btn-primary btn-sm" id="create-user"><i class="bi bi-person-plus me-1"></i>新建用户</button></div>
+<div class="alert alert-danger d-none" id="user-error" role="alert"></div>
+<div class="table-responsive"><table class="table table-sm align-middle"><thead><tr><th>用户名</th><th>显示名称</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody id="user-list"><tr><td colspan="6" class="text-center text-muted py-4">正在加载用户...</td></tr></tbody></table></div>
+
+<div class="modal fade" id="user-editor-modal" tabindex="-1" aria-labelledby="user-editor-title" aria-hidden="true"><div class="modal-dialog modal-dialog-centered"><form class="modal-content" id="user-editor-form"><div class="modal-header"><h5 class="modal-title" id="user-editor-title">新建用户</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button></div><div class="modal-body">
+    <input type="hidden" id="user-id">
+    <div class="mb-3"><label class="form-label" for="user-username">用户名</label><input class="form-control" id="user-username" autocomplete="off" required></div>
+    <div class="mb-3"><label class="form-label" for="user-display-name">显示名称</label><input class="form-control" id="user-display-name" required></div>
+    <div class="mb-3" id="user-password-field"><label class="form-label" for="user-password">初始密码</label><input class="form-control" id="user-password" type="password" autocomplete="new-password" minlength="1"></div>
+    <div><label class="form-label" for="user-role-select">角色，可多选</label><select class="form-select" id="user-role-select" multiple size="6"></select></div>
+    <div class="alert alert-danger d-none mt-3 mb-0" id="user-form-error" role="alert"></div>
+</div><div class="modal-footer"><button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">取消</button><button class="btn btn-primary" id="save-user" type="submit"><i class="bi bi-save me-1"></i>保存</button></div></form></div></div>
+{% endblock %}
+{% block scripts %}<script src="/static/js/admin/users.js"></script>{% endblock %}
diff --git a/templates/common/workspace/admin_analytics.html b/templates/common/workspace/admin_analytics.html
new file mode 100644
index 0000000..8461f56
--- /dev/null
+++ b/templates/common/workspace/admin_analytics.html
@@ -0,0 +1,107 @@
+<section>
+            <div class="d-flex align-items-center justify-content-between mb-3">
+                <h3 class="mb-0">分析看板</h3>
+                <div class="d-flex gap-2">
+                    <select class="form-select form-select-sm" style="width: 130px;" x-model="analyticsPlatform" @change="reloadAnalyticsDashboard">
+                        <option value="">全部平台</option>
+                        <option value="dy">抖音</option>
+                        <option value="hg">红果</option>
+                    </select>
+                    <input class="form-control form-control-sm" style="width: 145px;" type="date" x-model="analyticsDate" @change="reloadAnalyticsDashboard">
+                    <button class="btn btn-outline-light btn-sm" @click="analyticsDate = ''; reloadAnalyticsDashboard()">全部日期</button>
+                    <button class="btn btn-outline-secondary btn-sm" @click="refreshAnalyticsDashboard(true)" :disabled="analyticsLoading">
+                        <i class="bi bi-arrow-clockwise me-1"></i>刷新
+                    </button>
+                    <button class="btn btn-primary btn-sm" @click="syncAllAnalytics" :disabled="analyticsLoading">
+                        <i class="bi bi-database-add me-1"></i>同步全部
+                    </button>
+                </div>
+            </div>
+            <div class="alert alert-danger" x-show="analyticsError" x-text="analyticsError"></div>
+            <div class="alert alert-secondary" x-show="analyticsSyncResult">
+                <span>最近同步：</span><strong x-text="analyticsSyncResult?.job_id || '-'"></strong>
+                <span class="ms-3">剧目：</span><span x-text="analyticsSyncResult?.drama_count || 0"></span>
+                <span class="ms-3">文件：</span><span x-text="analyticsSyncResult?.artifact_count || 0"></span>
+            </div>
+
+            <div class="row g-3 mb-3">
+                <div class="col-md-3">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">剧目数</div><div class="metric-value" x-text="analyticsOverview.drama_count || 0"></div></div></div>
+                </div>
+                <div class="col-md-3">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">评论数</div><div class="metric-value" x-text="analyticsOverview.comment_count || 0"></div></div></div>
+                </div>
+                <div class="col-md-3">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">转写数</div><div class="metric-value" x-text="analyticsOverview.transcript_count || 0"></div></div></div>
+                </div>
+                <div class="col-md-3">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">任务数</div><div class="metric-value" x-text="analyticsOverview.task_count || 0"></div></div></div>
+                </div>
+            </div>
+
+            <div class="row g-3 mb-3">
+                <div class="col-xl-6">
+                    <div class="card"><div class="card-header">题材分布</div><div class="card-body"><div id="categoryChart" class="chart-box"></div></div></div>
+                </div>
+                <div class="col-xl-6">
+                    <div class="card"><div class="card-header">热度趋势</div><div class="card-body"><div id="trendChart" class="chart-box"></div></div></div>
+                </div>
+                <div class="col-xl-6">
+                    <div class="card"><div class="card-header">评论情绪</div><div class="card-body"><div id="sentimentChart" class="chart-box"></div></div></div>
+                </div>
+                <div class="col-xl-6">
+                    <div class="card">
+                        <div class="card-header">高频卖点</div>
+                        <div class="card-body">
+                            <div class="selling-point-list">
+                                <template x-for="item in (analyticsTags.selling_points || []).slice(0, 20)" :key="item.tag">
+                                    <div class="selling-point-item">
+                                        <div class="selling-point-text" x-text="item.tag"></div>
+                                        <span class="badge bg-primary align-self-start" x-text="item.count"></span>
+                                    </div>
+                                </template>
+                                <div class="text-muted text-center py-4" x-show="!(analyticsTags.selling_points || []).length">暂无卖点数据</div>
+                            </div>
+                        </div>
+                    </div>
+                </div>
+            </div>
+
+            <div class="card">
+                <div class="card-header">剧目指标</div>
+                <div class="table-responsive scroll-pane">
+                    <table class="table table-sm table-hover align-middle mb-0">
+                        <thead>
+                            <tr>
+                                <th>剧名</th>
+                                <th style="width: 80px;">平台</th>
+                                <th style="width: 110px;">时间</th>
+                                <th style="width: 90px;">题材</th>
+                                <th style="width: 80px;">排名</th>
+                                <th style="width: 110px;">点赞</th>
+                                <th style="width: 110px;">评论</th>
+                            </tr>
+                        </thead>
+                        <tbody>
+                            <template x-for="item in analyticsDramas" :key="`${item.platform}-${item.job_id}-${item.source_path}-${item.record_id}-${item.title}`">
+                                <tr>
+                                    <td>
+                                        <div class="fw-semibold" x-text="item.title"></div>
+                                        <small class="text-muted" x-text="(item.selling_points || []).slice(0, 2).join(' / ')"></small>
+                                    </td>
+                                    <td x-text="item.platform"></td>
+                                    <td x-text="item.captured_at || '-'"></td>
+                                    <td x-text="item.category || '-'"></td>
+                                    <td x-text="item.rank || '-'"></td>
+                                    <td x-text="item.like_count || 0"></td>
+                                    <td x-text="item.comment_count || 0"></td>
+                                </tr>
+                            </template>
+                            <tr x-show="analyticsDramas.length === 0">
+                                <td colspan="7" class="text-center text-muted py-4">暂无剧目指标</td>
+                            </tr>
+                        </tbody>
+                    </table>
+                </div>
+            </div>
+        </section>
diff --git a/templates/common/workspace/admin_config.html b/templates/common/workspace/admin_config.html
new file mode 100644
index 0000000..b77b6ae
--- /dev/null
+++ b/templates/common/workspace/admin_config.html
@@ -0,0 +1,117 @@
+<section id="configEditor">
+            <div class="d-flex align-items-center justify-content-between mb-3">
+                <h3 class="mb-0">配置编辑</h3>
+                <div>
+                    <button class="btn btn-outline-secondary me-2" @click="fetchConfigForm">重新加载</button>
+                    <button class="btn btn-success" @click="saveConfigForm">保存修改</button>
+                </div>
+            </div>
+            <div class="row g-3">
+                <div class="col-xl-8">
+                    <div class="card">
+                        <div class="card-header">基础参数</div>
+                        <div class="card-body">
+                            <div class="row g-3">
+                                <template x-for="field in configRootFields" :key="field.key">
+                                    <div class="col-md-6">
+                                        <label class="form-label" x-text="field.label"></label>
+                                        <template x-if="field.type === 'checkbox'">
+                                            <div class="form-check form-switch mt-2">
+                                                <input class="form-check-input" type="checkbox" x-model="configForm.root[field.key]">
+                                            </div>
+                                        </template>
+                                        <template x-if="field.type === 'number'">
+                                            <input type="number" class="form-control" x-model.number="configForm.root[field.key]">
+                                        </template>
+                                        <template x-if="field.type === 'text'">
+                                            <input type="text" class="form-control" x-model="configForm.root[field.key]">
+                                        </template>
+                                    </div>
+                                </template>
+                            </div>
+                        </div>
+                    </div>
+
+                    <div class="card mt-3">
+                        <div class="card-header d-flex align-items-center justify-content-between">
+                            <span>项目配置</span>
+                            <button class="btn btn-outline-success btn-sm" @click="addConfigItem">项目添加</button>
+                        </div>
+                        <div class="card-body">
+                            <template x-for="(item, index) in configForm.items" :key="index">
+                                <div class="border border-secondary rounded p-3 mb-3">
+                                    <div class="d-flex align-items-center justify-content-between mb-3">
+                                        <strong x-text="item.project || `项目 ${index + 1}`"></strong>
+                                        <button class="btn btn-outline-danger btn-sm" @click="removeConfigItem(index)">删除</button>
+                                    </div>
+                                    <div class="row g-3">
+                                        <div class="col-md-6">
+                                            <label class="form-label">project</label>
+                                            <input class="form-control" x-model="item.project">
+                                        </div>
+                                        <div class="col-md-3">
+                                            <label class="form-label">评论不少于</label>
+                                            <input type="number" class="form-control" x-model.number="item['评论不少于']">
+                                        </div>
+                                        <div class="col-md-3">
+                                            <label class="form-label">作品天数</label>
+                                            <input type="number" class="form-control" x-model.number="item['作品天数']">
+                                        </div>
+                                        <div class="col-12">
+                                            <label class="form-label">搜索关键词</label>
+                                            <textarea class="form-control" rows="2" x-model="item['搜索关键词']"></textarea>
+                                        </div>
+                                        <div class="col-12">
+                                            <label class="form-label">屏蔽关键词</label>
+                                            <textarea class="form-control" rows="2" x-model="item['屏蔽关键词']"></textarea>
+                                        </div>
+                                        <div class="col-12">
+                                            <label class="form-label">有关视频判定</label>
+                                            <textarea class="form-control" rows="3" x-model="item['有关视频判定']"></textarea>
+                                        </div>
+                                        <div class="col-12">
+                                            <label class="form-label">黑名单视频判定</label>
+                                            <textarea class="form-control" rows="3" x-model="item['黑名单视频判定']"></textarea>
+                                        </div>
+                                        <div class="col-md-6">
+                                            <label class="form-label">白名单地区</label>
+                                            <input class="form-control" x-model="item['白名单地区']">
+                                        </div>
+                                        <div class="col-md-6">
+                                            <label class="form-label">黑名单地区</label>
+                                            <input class="form-control" x-model="item['黑名单地区']">
+                                        </div>
+                                    </div>
+                                </div>
+                            </template>
+                            <div x-show="configForm.items.length === 0" class="text-center text-muted py-4">暂无项目配置</div>
+                        </div>
+                    </div>
+                </div>
+                <div class="col-xl-4">
+                    <div class="card">
+                        <div class="card-header d-flex align-items-center justify-content-between">
+                            <span>备份</span>
+                            <button class="btn btn-outline-success btn-sm" @click="createBackup">创建</button>
+                        </div>
+                        <div class="list-group list-group-flush">
+                            <template x-for="backup in backups" :key="backup.name">
+                                <div class="list-group-item bg-transparent text-light border-secondary">
+                                    <div class="d-flex justify-content-between">
+                                        <span x-text="formatDate(backup.created)"></span>
+                                        <span class="badge bg-secondary" x-text="formatSize(backup.size)"></span>
+                                    </div>
+                                    <div class="small text-muted my-2" x-text="backup.name"></div>
+                                    <div class="d-flex gap-2">
+                                        <button class="btn btn-sm btn-outline-warning flex-fill" @click="restoreBackup(backup.name)">恢复</button>
+                                        <button class="btn btn-sm btn-outline-danger flex-fill" @click="deleteBackup(backup.name)">删除</button>
+                                    </div>
+                                </div>
+                            </template>
+                            <div x-show="backups.length === 0" class="text-center text-muted py-4">暂无备份</div>
+                        </div>
+                    </div>
+                </div>
+            </div>
+        </section>
+    </main>
diff --git a/templates/common/workspace/admin_data.html b/templates/common/workspace/admin_data.html
new file mode 100644
index 0000000..9e30409
--- /dev/null
+++ b/templates/common/workspace/admin_data.html
@@ -0,0 +1,73 @@
+<section class="data-section">
+            <div class="d-flex align-items-center justify-content-between mb-3">
+                <h3 class="mb-0">数据管理</h3>
+                <div class="d-flex gap-2 flex-wrap">
+                    <button class="btn btn-outline-danger btn-sm" x-show="!batchDeleteMode" @click="startBatchDelete">
+                        <i class="bi bi-trash me-1"></i>批量删除
+                    </button>
+                    <button class="btn btn-outline-secondary btn-sm" x-show="batchDeleteMode" @click="cancelBatchDelete" title="退出批量删除">
+                        <i class="bi bi-x-lg"></i><span class="visually-hidden">退出批量删除</span>
+                    </button>
+                    <button class="btn btn-outline-danger btn-sm" x-show="batchDeleteMode && selectedFilePaths.length > 0" @click="deleteSelectedFiles" :title="`删除已选 ${selectedFilePaths.length} 个文件`">
+                        <i class="bi bi-trash me-1"></i><span x-text="`删除 (${selectedFilePaths.length})`"></span>
+                    </button>
+                    <input class="form-control form-control-sm" style="width: 260px;" x-model="fileSearch" @input="applyFileSearch" placeholder="搜索文件名或路径">
+                    <select class="form-select form-select-sm" style="width: 150px;" x-model="fileFilter" @change="fetchFiles">
+                        <option value="all">全部</option>
+                        <option value="内容数据">内容数据</option>
+                        <option value="媒体文件">媒体文件</option>
+                        <option value="任务文件">任务文件</option>
+                        <option value="audio">音频</option>
+                        <option value="video">视频</option>
+                        <option value="json">JSON</option>
+                        <option value="md">Markdown</option>
+                        <option value="剧本与文稿">剧本与文稿</option>
+                        <option value="转写数据">转写数据</option>
+                        <option value="元数据">元数据</option>
+                        <option value="Comments">评论</option>
+                        <option value="Contents">内容</option>
+                    </select>
+                    <button class="btn btn-outline-light btn-sm" @click="fetchFiles">刷新</button>
+                </div>
+            </div>
+            <div class="d-flex justify-content-end" x-show="batchDeleteMode && filteredFiles.length">
+                <label class="form-check small"><input class="form-check-input me-1" type="checkbox" :checked="allVisibleFilesSelected()" @change="toggleAllVisibleFiles($event.target.checked)">全选当前结果</label>
+            </div>
+            <template x-for="group in groupedFiles()" :key="group.drama_name">
+                <section class="data-drama-group">
+                    <div class="data-drama-heading d-flex align-items-center justify-content-between">
+                        <h5 class="mb-0"><i class="bi bi-collection-play me-2"></i><span x-text="group.drama_name"></span></h5>
+                        <span class="badge bg-secondary" x-text="`${group.count} 个文件`"></span>
+                    </div>
+                    <template x-for="typeGroup in group.file_groups" :key="`${group.drama_name}:${typeGroup.name}`">
+                        <div>
+                            <div class="data-type-heading d-flex justify-content-between"><span x-text="typeGroup.name"></span><span x-text="typeGroup.files.length"></span></div>
+                            <div class="table-responsive">
+                                <table class="table table-sm table-hover align-middle mb-0">
+                                    <tbody>
+                                        <template x-for="file in typeGroup.files" :key="file.path">
+                                            <tr>
+                                                <td x-show="batchDeleteMode" style="width: 44px;"><input class="form-check-input" type="checkbox" :checked="isFileSelected(file.path)" @change="toggleFileSelection(file.path, $event.target.checked)" :aria-label="`选择 ${file.name}`"></td>
+                                                <td><div class="fw-semibold"><span x-text="file.name"></span><span class="badge bg-secondary ms-2" x-show="file.episode_label" x-text="file.episode_label"></span></div><small class="text-muted" x-text="file.path"></small></td>
+                                                <td style="width: 110px;" x-text="formatSize(file.size)"></td>
+                                                <td style="width: 180px;" x-text="formatDate(file.modified)"></td>
+                                                <td style="width: 110px;"><span class="badge" :class="file.media_type ? 'bg-primary' : 'bg-success'" x-text="file.file_group"></span></td>
+                                                <td style="width: 220px;">
+                                                    <button class="btn btn-sm btn-outline-primary me-1" x-show="file.preview_kind !== 'none'" @click="openFilePreview(file)" title="在线预览" aria-label="在线预览"><i class="bi bi-eye"></i></button>
+                                                    <button class="btn btn-sm btn-outline-light me-1" @click="downloadFile(file.path)" title="下载" aria-label="下载"><i class="bi bi-download"></i></button>
+                                                    <button class="btn btn-sm btn-outline-info me-1" x-show="file.media_type === 'audio'" @click="analyzeAudio(file)" title="分析音频"><i class="bi bi-soundwave"></i></button>
+                                                    <button class="btn btn-sm btn-outline-warning me-1" x-show="file.legacy_category === 'Contents' && file.type === 'json'" @click="openProjectModal(file.path, 'crawl')">爬评论</button>
+                                                    <button class="btn btn-sm btn-outline-success" x-show="file.legacy_category === 'Comments' && file.type === 'json'" @click="openProjectModal(file.path, 'export')">导出</button>
+                                                    <button class="btn btn-sm btn-outline-danger ms-1" @click="deleteFiles([file.path])" title="删除文件" aria-label="删除文件"><i class="bi bi-trash"></i></button>
+                                                </td>
+                                            </tr>
+                                        </template>
+                                    </tbody>
+                                </table>
+                            </div>
+                        </div>
+                    </template>
+                </section>
+            </template>
+            <div x-show="filteredFiles.length === 0" class="text-center text-muted py-5">暂无文件</div>
+        </section>
diff --git a/templates/common/workspace/admin_data_modals.html b/templates/common/workspace/admin_data_modals.html
new file mode 100644
index 0000000..f15ea15
--- /dev/null
+++ b/templates/common/workspace/admin_data_modals.html
@@ -0,0 +1,59 @@
+<div class="modal fade" id="projectModal" tabindex="-1">
+        <div class="modal-dialog modal-dialog-centered">
+            <div class="modal-content">
+                <div class="modal-header">
+                    <h5 class="modal-title" x-text="modalMode === 'crawl' ? '选择爬评论项目' : '选择导出项目'"></h5>
+                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
+                </div>
+                <div class="modal-body">
+                    <div class="form-check mb-3" x-show="modalMode === 'export'">
+                        <input class="form-check-input" type="checkbox" id="autoUploadCheck" x-model="autoUpload">
+                        <label class="form-check-label" for="autoUploadCheck">导出后自动上传</label>
+                    </div>
+                    <template x-for="(item, index) in modalProjects" :key="item.name">
+                        <div class="d-flex align-items-center justify-content-between border border-secondary rounded p-2 mb-2">
+                            <label class="form-check-label">
+                                <input class="form-check-input me-2" type="checkbox" :value="item.name" x-model="selectedModalProjects">
+                                <span x-text="item.name"></span>
+                                <span class="badge bg-secondary ms-2" x-text="item.count"></span>
+                            </label>
+                            <div class="btn-group btn-group-sm">
+                                <button class="btn btn-outline-secondary" @click="moveProjectUp(index)" :disabled="index === 0"><i class="bi bi-arrow-up"></i></button>
+                                <button class="btn btn-outline-secondary" @click="moveProjectDown(index)" :disabled="index === modalProjects.length - 1"><i class="bi bi-arrow-down"></i></button>
+                            </div>
+                        </div>
+                    </template>
+                </div>
+                <div class="modal-footer">
+                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
+                    <button type="button" class="btn btn-primary" @click="confirmBatchAction">确认</button>
+                </div>
+            </div>
+        </div>
+    </div>
+
+    <div class="modal fade" id="filePreviewModal" tabindex="-1">
+        <div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
+            <div class="modal-content">
+                <div class="modal-header">
+                    <div>
+                        <h5 class="modal-title"><i class="bi bi-eye me-2"></i><span x-text="previewFile.file?.name || '文件预览'"></span></h5>
+                        <div class="small text-muted mt-1"><span x-text="previewFile.file?.drama_name || ''"></span><span x-show="previewFile.file?.episode_label" x-text="` · ${previewFile.file?.episode_label}`"></span><span x-show="previewFile.file" x-text="` · ${formatSize(previewFile.file?.size || 0)}`"></span></div>
+                    </div>
+                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
+                </div>
+                <div class="modal-body">
+                    <div x-show="previewFile.status === 'loading'" class="py-5 text-center"><span class="spinner-border"></span></div>
+                    <div x-show="previewFile.status === 'error'" class="alert alert-warning" x-text="previewFile.message"></div>
+                    <video class="file-preview-media" x-show="previewFile.status === 'success' && previewFile.kind === 'video'" :src="previewFile.url" controls preload="metadata" @error="previewFile.status = 'error'; previewFile.message = '浏览器无法播放该视频编码，请下载后查看。'"></video>
+                    <audio class="w-100" x-show="previewFile.status === 'success' && previewFile.kind === 'audio'" :src="previewFile.url" controls preload="metadata" @error="previewFile.status = 'error'; previewFile.message = '浏览器无法播放该音频格式，请下载后查看。'"></audio>
+                    <pre class="file-preview-text" x-show="previewFile.status === 'success' && ['text', 'json'].includes(previewFile.kind)" x-text="previewFile.content"></pre>
+                    <div class="small text-muted mt-3" x-show="previewFile.file" x-text="previewFile.file?.path"></div>
+                </div>
+                <div class="modal-footer">
+                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
+                    <button type="button" class="btn btn-outline-light" x-show="previewFile.file" @click="downloadFile(previewFile.file.path)"><i class="bi bi-download me-1"></i>下载</button>
+                </div>
+            </div>
+        </div>
+    </div>
diff --git a/templates/common/workspace/admin_jobs.html b/templates/common/workspace/admin_jobs.html
new file mode 100644
index 0000000..0f3a381
--- /dev/null
+++ b/templates/common/workspace/admin_jobs.html
@@ -0,0 +1,263 @@
+<section>
+            <div class="d-flex align-items-center justify-content-between mb-3">
+                <h3 class="mb-0">爬虫控制</h3>
+                <button class="btn btn-outline-secondary btn-sm" @click="fetchJobs">
+                    <i class="bi bi-arrow-clockwise me-1"></i>刷新
+                </button>
+            </div>
+
+            <div class="card mb-3">
+                <div class="card-header d-flex align-items-center justify-content-between">
+                    <span><i class="bi bi-list-task me-2"></i>任务列表</span>
+                    <span class="badge bg-primary" x-show="batchProgress" x-text="'批量：' + batchProgress"></span>
+                </div>
+                <div class="table-responsive scroll-pane">
+                    <table class="table table-sm table-hover align-middle mb-0">
+                        <thead>
+                            <tr>
+                                <th>Job</th>
+                                <th style="width: 90px;">平台</th>
+                                <th style="width: 100px;">类型</th>
+                                <th style="width: 110px;">状态</th>
+                                <th style="width: 110px;">操作</th>
+                            </tr>
+                        </thead>
+                        <tbody>
+                            <template x-for="job in jobs" :key="job.job_id">
+                                <tr class="job-row" :class="{active: selectedJobId === job.job_id}" @click="selectJob(job.job_id)">
+                                    <td>
+                                        <div class="fw-semibold" x-text="job.job_id"></div>
+                                        <small class="text-muted" x-text="job.message || ''"></small>
+                                    </td>
+                                    <td x-text="job.platform"></td>
+                                    <td x-text="job.crawler_type"></td>
+                                    <td><span class="badge" :class="jobStatusClass(job.status)" x-text="job.status"></span></td>
+                                    <td>
+                                        <button class="btn btn-sm btn-outline-danger btn-icon" @click.stop="stopJob(job.job_id)" :disabled="!isJobRunning(job)" title="停止">
+                                            <i class="bi bi-stop-circle"></i>
+                                        </button>
+                                        <button class="btn btn-sm btn-outline-secondary btn-icon ms-1" @click.stop="deleteJob(job.job_id)" :disabled="isJobRunning(job)" title="删除">
+                                            <i class="bi bi-trash"></i>
+                                        </button>
+                                    </td>
+                                </tr>
+                            </template>
+                            <tr x-show="jobs.length === 0">
+                                <td colspan="5" class="text-center text-muted py-4">暂无任务</td>
+                            </tr>
+                        </tbody>
+                    </table>
+                </div>
+            </div>
+
+            <div class="row g-3">
+                <div class="col-xl-8">
+                    <div class="card">
+                        <div class="card-header d-flex align-items-center justify-content-between">
+                            <span><i class="bi bi-terminal me-2"></i>运行日志</span>
+                            <div class="btn-group btn-group-sm">
+                                <button class="btn btn-outline-light" @click="copyLogs" :disabled="logs.length === 0" title="复制日志">
+                                    <i class="bi bi-clipboard me-1"></i>复制
+                                </button>
+                                <button class="btn btn-outline-light" @click="downloadLogs" :disabled="logs.length === 0" title="下载日志">
+                                    <i class="bi bi-download me-1"></i>下载
+                                </button>
+                                <button class="btn btn-outline-secondary" @click="logs = []">清空</button>
+                            </div>
+                        </div>
+                        <div class="log-pane" id="logConsole">
+                            <template x-for="(log, index) in logs" :key="index">
+                                <div class="log-line" :class="logClass(log)" x-text="log"></div>
+                            </template>
+                            <div x-show="logs.length === 0" class="text-muted p-3">暂无日志</div>
+                        </div>
+                    </div>
+                </div>
+                <div class="col-xl-4">
+                    <div class="card mb-3">
+                        <div class="card-header">快捷操作</div>
+                        <div class="card-body d-grid gap-2">
+                            <button class="btn btn-primary btn-lg" @click="runCrawler">
+                                <i class="bi bi-play-circle-fill me-1"></i>开始爬取
+                            </button>
+                            <button class="btn btn-outline-light" @click="scrollToCrawlerSettings">
+                                <i class="bi bi-sliders me-1"></i>修改配置
+                            </button>
+                        </div>
+                    </div>
+                    <div class="card">
+                        <div class="card-header">系统状态</div>
+                        <div class="card-body">
+                            <p class="mb-2">平台：<span x-text="config.platform"></span></p>
+                            <p class="mb-0">配置项目：<span x-text="configForm.items.length"></span></p>
+                        </div>
+                    </div>
+                </div>
+            </div>
+        </section>
+
+        <section id="crawlerSettings">
+            <h3 class="mb-3">参数配置</h3>
+            <form @submit.prevent="runCrawler">
+                <div class="row g-3">
+                    <div class="col-xl-6">
+                        <div class="card">
+                            <div class="card-header">基础配置</div>
+                            <div class="card-body">
+                                <div class="mb-3">
+                                    <label class="form-label">媒体平台</label>
+                                    <select class="form-select" x-model="config.platform" @change="onPlatformChange">
+                                        <option value="dy">抖音 (dy)</option>
+                                        <option value="xhs">小红书 (xhs)</option>
+                                        <option value="ks">快手 (ks)</option>
+                                        <option value="bili">哔哩哔哩 (bili)</option>
+                                        <option value="wb">微博 (wb)</option>
+                                        <option value="tieba">百度贴吧 (tieba)</option>
+                                        <option value="zhihu">知乎 (zhihu)</option>
+                                        <option value="hg">红果短剧 (hg)</option>
+                                    </select>
+                                </div>
+                                <div class="mb-3">
+                                    <label class="form-label">爬取类型</label>
+                                    <select class="form-select" x-model="config.crawler_type">
+                                        <option value="search">搜索 (search)</option>
+                                        <option value="detail">详情 (detail)</option>
+                                        <option value="creator">创作者 (creator)</option>
+                                        <option value="rank" :disabled="!['dy', 'hg'].includes(config.platform)">榜单 (rank)</option>
+                                    </select>
+                                </div>
+                                <div class="mb-3">
+                                    <label class="form-label">登录方式</label>
+                                    <select class="form-select" x-model="config.login_type" @change="onLoginTypeChange">
+                                        <option value="qrcode">二维码 (qrcode)</option>
+                                        <option value="phone">手机号 (phone)</option>
+                                        <option value="cookie">Cookie</option>
+                                    </select>
+                                </div>
+                                <div x-show="config.login_type === 'qrcode'" class="mb-3 p-3 border border-secondary rounded">
+                                    <div class="d-flex gap-2 align-items-center flex-wrap">
+                                        <button type="button" class="btn btn-primary btn-sm" @click="startQrcodeLogin" :disabled="isRunning || qrcodeLoggingIn">
+                                            <i class="bi bi-camera me-1"></i>扫码登录
+                                        </button>
+                                        <span x-show="qrcodeLoggingIn" class="spinner-border spinner-border-sm"></span>
+                                        <span class="badge" :class="qrcodeStatus.has_state ? 'bg-success' : 'bg-secondary'" x-text="qrcodeStatus.message || '未登录'"></span>
+                                        <button type="button" x-show="qrcodeStatus.has_state" class="btn btn-outline-danger btn-sm" @click="clearQrcodeLogin">
+                                            <i class="bi bi-trash"></i>
+                                        </button>
+                                    </div>
+                                </div>
+                                <div class="mb-3">
+                                    <label class="form-label">Cookie</label>
+                                    <input type="text" class="form-control" x-model="config.cookie" placeholder="可选">
+                                </div>
+                            </div>
+                        </div>
+
+                        <div class="card mt-3">
+                            <div class="card-header">运行配置</div>
+                            <div class="card-body">
+                                <div class="row g-3">
+                                    <div class="col-md-6">
+                                        <label class="form-label">起始页</label>
+                                        <input type="number" class="form-control" x-model.number="config.start_page" min="1">
+                                    </div>
+                                    <div class="col-md-6">
+                                        <label class="form-label">保存方式</label>
+                                        <select class="form-select" x-model="config.save_option">
+                                            <option value="json">JSON</option>
+                                            <option value="csv">CSV</option>
+                                            <option value="sqlite">SQLite</option>
+                                            <option value="db">MySQL</option>
+                                            <option value="mongodb">MongoDB</option>
+                                            <option value="excel">Excel</option>
+                                        </select>
+                                    </div>
+                                </div>
+                                <div class="form-check form-switch mt-3">
+                                    <input class="form-check-input" type="checkbox" x-model="config.headless" id="headlessSwitch">
+                                    <label class="form-check-label" for="headlessSwitch">无头模式</label>
+                                </div>
+                                <div class="form-check form-switch mt-2">
+                                    <input class="form-check-input" type="checkbox" x-model="config.get_comment" id="commentSwitch">
+                                    <label class="form-check-label" for="commentSwitch">爬取一级评论</label>
+                                </div>
+                                <div class="form-check form-switch mt-2">
+                                    <input class="form-check-input" type="checkbox" x-model="config.get_sub_comment" id="subCommentSwitch">
+                                    <label class="form-check-label" for="subCommentSwitch">爬取二级评论</label>
+                                </div>
+                                <div class="form-check form-switch mt-2">
+                                    <input class="form-check-input" type="checkbox" x-model="config.autoRun" id="autoRunSwitch">
+                                    <label class="form-check-label" for="autoRunSwitch">自动托管</label>
+                                </div>
+                            </div>
+                        </div>
+                    </div>
+
+                    <div class="col-xl-6">
+                        <div class="card" x-show="config.platform === 'dy'">
+                            <div class="card-header">抖音专用配置</div>
+                            <div class="card-body">
+                                <div class="mb-3">
+                                    <label class="form-label">搜索频道</label>
+                                    <select class="form-select" x-model="config.dy_channel">
+                                        <option value="general">综合</option>
+                                        <option value="video">视频</option>
+                                        <option value="user">用户</option>
+                                        <option value="live">直播</option>
+                                    </select>
+                                </div>
+                                <div class="row g-3">
+                                    <div class="col-md-6">
+                                        <label class="form-label">排序</label>
+                                        <select class="form-select" x-model="config.dy_sort">
+                                            <option value="latest">最新发布</option>
+                                            <option value="most_like">最多点赞</option>
+                                            <option value="general">综合排序</option>
+                                        </select>
+                                    </div>
+                                    <div class="col-md-6">
+                                        <label class="form-label">发布时间</label>
+                                        <select class="form-select" x-model="config.dy_publish_time">
+                                            <option value="unlimited">不限</option>
+                                            <option value="1_day">一天内</option>
+                                            <option value="1_week">一周内</option>
+                                            <option value="6_months">半年内</option>
+                                        </select>
+                                    </div>
+                                </div>
+                                <div class="row g-3 mt-1">
+                                    <div class="col-md-4">
+                                        <label class="form-label">榜单数量</label>
+                                        <input type="number" class="form-control" x-model.number="config.dy_rank_limit" min="1">
+                                    </div>
+                                    <div class="col-md-4">
+                                        <label class="form-label">评论数量</label>
+                                        <input type="number" class="form-control" x-model.number="config.dy_rank_comment_limit" min="1">
+                                    </div>
+                                    <div class="col-md-4">
+                                        <label class="form-label">登录等待秒数</label>
+                                        <input type="number" class="form-control" x-model.number="config.dy_rank_login_wait_seconds" min="0">
+                                    </div>
+                                </div>
+                                <button type="button" class="btn btn-outline-light mt-3" @click="runRankCrawler('dy')">
+                                    <i class="bi bi-trophy me-1"></i>抓取短剧榜单
+                                </button>
+                            </div>
+                        </div>
+
+                        <div class="card mt-3" x-show="config.platform === 'hg'">
+                            <div class="card-header">红果短剧抓取</div>
+                            <div class="card-body">
+                                <div class="form-text mb-2">榜单从本地 CSV 导入：固定读取 E:\hg\exports 最新的 top10_rankings.csv。</div>
+                                <div class="d-flex gap-2 flex-wrap">
+                                    <button type="button" class="btn btn-primary mt-3" @click="runRankCrawler('hg')">
+                                        <i class="bi bi-trophy me-1"></i>抓取红果榜单
+                                    </button>
+                                </div>
+                            </div>
+                        </div>
+
+                    </div>
+                </div>
+            </form>
+        </section>
diff --git a/templates/common/workspace/breakdown_modals.html b/templates/common/workspace/breakdown_modals.html
new file mode 100644
index 0000000..bb92dc4
--- /dev/null
+++ b/templates/common/workspace/breakdown_modals.html
@@ -0,0 +1,63 @@
+<div class="modal fade" id="breakdownGenerationProgressModal" tabindex="-1" data-bs-backdrop="static" aria-labelledby="breakdownGenerationProgressModalLabel" aria-hidden="true">
+        <div class="modal-dialog modal-dialog-centered">
+            <div class="modal-content">
+                <div class="modal-header">
+                    <div>
+                        <h5 class="modal-title" id="breakdownGenerationProgressModalLabel"><i class="bi bi-diagram-3 me-2"></i>生成剧目拆解</h5>
+                        <div class="small text-muted" x-text="breakdownGeneration.topic?.title || ''"></div>
+                    </div>
+                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
+                </div>
+                <div class="modal-body">
+                    <div class="small text-info mb-2" x-show="breakdownGeneration.coverage.length" x-text="breakdownGeneration.requiresTranscription ? `已发现第 ${breakdownGeneration.coverage.join('、')} 集下载视频` : `已加载第 ${breakdownGeneration.coverage.join('、')} 集剧目脚本`"></div>
+                    <div class="d-flex justify-content-between small mb-1"><span x-text="breakdownGeneration.message || '正在创建生成任务'"></span><strong x-text="breakdownGenerationProgressLabel()"></strong></div>
+                    <div class="progress" role="progressbar" aria-label="拆解生成进度">
+                        <div class="progress-bar progress-bar-striped" :class="{'progress-bar-animated': breakdownGenerationRunning(), 'bg-danger': breakdownGeneration.status === 'failed'}" :style="`width: ${breakdownGenerationIndeterminate() ? 100 : breakdownGenerationProgressPercent()}%`"></div>
+                    </div>
+                    <div class="alert alert-danger py-2 mt-3 mb-0" x-show="breakdownGeneration.error" x-text="breakdownGeneration.error"></div>
+                </div>
+                <div class="modal-footer">
+                    <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">后台运行</button>
+                    <button type="button" class="btn btn-outline-light" x-show="breakdownGeneration.jobId" @click="openBreakdownTaskLog"><i class="bi bi-list-ul me-1"></i>任务日志</button>
+                    <button type="button" class="btn btn-primary" x-show="breakdownGenerationRetryable()" @click="startTranscriptBreakdown(breakdownGeneration.topic, breakdownGeneration.source)"><i class="bi bi-arrow-clockwise me-1"></i>重试</button>
+                </div>
+            </div>
+        </div>
+    </div>
+
+    <div class="modal fade" id="breakdownEditorModal" tabindex="-1" aria-labelledby="breakdownEditorModalLabel" aria-hidden="true">
+        <div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
+            <div class="modal-content">
+                <div class="modal-header">
+                    <div>
+                        <h5 class="modal-title" id="breakdownEditorModalLabel">爆款拆解编辑</h5>
+                        <div class="small text-muted"><span x-text="breakdownEditor.title || '未命名选题'"></span><span class="ms-2" x-text="`v${breakdownEditor.record?.version || 1} · ${breakdownEditor.record?.status || 'draft'}`"></span></div>
+                    </div>
+                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
+                </div>
+                <div class="modal-body">
+                    <div class="alert alert-danger py-2" x-show="breakdownEditor.error" x-text="breakdownEditor.error"></div>
+                    <div class="row g-3">
+                        <template x-for="item in breakdownEditor.fieldGuide" :key="item.key">
+                            <div class="col-12 border-bottom border-secondary-subtle pb-3">
+                                <div class="d-flex align-items-center justify-content-between gap-2 mb-2"><label class="form-label fw-semibold mb-0" x-text="item.label"></label><span class="badge bg-secondary" x-text="item.type"></span></div>
+                                <template x-if="item.type !== '数组'"><textarea class="form-control" rows="3" x-model="breakdownEditor.content[item.key]" :disabled="breakdownEditor.record?.status === 'locked'"></textarea></template>
+                                <template x-if="item.type === '数组'"><div class="vstack gap-2"><template x-for="(entry, index) in breakdownEditor.content[item.key]" :key="`${item.key}-${index}`"><div class="input-group"><textarea class="form-control" rows="2" x-model="breakdownEditor.content[item.key][index]" :disabled="breakdownEditor.record?.status === 'locked'"></textarea><button type="button" class="btn btn-outline-danger" @click="removeBreakdownArrayItem(item.key, index)" :disabled="breakdownEditor.record?.status === 'locked'" title="删除此项" aria-label="删除此项"><i class="bi bi-trash"></i></button></div></template><button type="button" class="btn btn-sm btn-outline-light align-self-start" @click="addBreakdownArrayItem(item.key)" :disabled="breakdownEditor.record?.status === 'locked'" title="添加一项" aria-label="添加一项"><i class="bi bi-plus-lg"></i></button></div></template>
+                                <div class="small text-muted mt-2" x-text="item.description"></div>
+                                <div class="small text-info mt-1"><span>写法：</span><span x-text="item.writing_prompt"></span></div>
+                                <div class="small text-warning mt-1"><span>示例：</span><span x-text="item.example"></span></div>
+                                <div class="small text-success mt-1"><span>证据：</span><span x-text="item.evidence_rule"></span></div>
+                            </div>
+                        </template>
+                    </div>
+                    <div class="small text-muted mt-2">内容字段来源和风险信息保留在拆解版本中。锁定后不可覆盖，只能创建新版本。</div>
+                </div>
+                <div class="modal-footer">
+                    <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">关闭</button>
+                    <button type="button" class="btn btn-outline-info" @click="regenerateCurrentBreakdown" :disabled="breakdownEditor.saving || breakdownGenerationRunning()"><i class="bi bi-arrow-clockwise me-1"></i>重新生成</button>
+                    <button type="button" class="btn btn-outline-light" @click="saveBreakdownEditor" :disabled="breakdownEditor.saving || breakdownEditor.record?.status === 'locked'"><i class="bi bi-save me-1"></i>保存草稿</button>
+                    <button type="button" class="btn btn-warning" @click="lockBreakdownEditor" :disabled="breakdownEditor.saving || breakdownEditor.record?.status === 'locked'"><i class="bi bi-lock me-1"></i>锁定版本</button>
+                </div>
+            </div>
+        </div>
+    </div>
diff --git a/templates/common/workspace/creative_progress_modal.html b/templates/common/workspace/creative_progress_modal.html
new file mode 100644
index 0000000..e8c8850
--- /dev/null
+++ b/templates/common/workspace/creative_progress_modal.html
@@ -0,0 +1,29 @@
+<div class="modal fade" id="creativeGenerationProgressModal" tabindex="-1" data-bs-backdrop="static" aria-labelledby="creativeGenerationProgressModalLabel" aria-hidden="true">
+        <div class="modal-dialog modal-dialog-centered">
+            <div class="modal-content">
+                <div class="modal-header">
+                    <div>
+                        <h5 class="modal-title" id="creativeGenerationProgressModalLabel"><i class="bi bi-stars me-2"></i><span x-text="`${creativeActionLabel(creativeWorkspace.activeAction)}生成进度`"></span></h5>
+                        <div class="small text-muted" x-text="creativeWorkspace.project?.name || ''"></div>
+                    </div>
+                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
+                </div>
+                <div class="modal-body">
+                    <div class="alert alert-info py-2" x-show="creativeWorkspace.startingAction || creativeJobRunning()" x-text="creativeGenerationStartedMessage(creativeWorkspace.activeAction)"></div>
+                    <div class="d-flex justify-content-between gap-3 small mb-1"><span x-text="creativeWorkspace.jobMessage || '正在创建生成任务'"></span><strong class="text-nowrap" x-text="creativeProgressText()"></strong></div>
+                    <div class="progress" role="progressbar" aria-label="创作内容生成进度">
+                        <div class="progress-bar progress-bar-striped" :class="{'progress-bar-animated': creativeProgressIndeterminate(), 'bg-danger': creativeWorkspace.jobStatus === 'failed'}" :style="`width:${creativeProgressIndeterminate() ? 100 : creativeProgressPercent()}%`"></div>
+                    </div>
+                    <div class="small text-muted mt-2" x-text="creativeProgressElapsedText()"></div>
+                    <div class="small text-warning mt-2" x-show="creativeWorkspace.jobProgress?.retry_attempt" x-text="`模型服务波动，正在自动重试 ${creativeWorkspace.jobProgress?.retry_attempt}/${creativeWorkspace.jobProgress?.max_retries}`"></div>
+                    <div class="alert alert-danger py-2 mt-3 mb-0" x-show="creativeWorkspace.jobStatus === 'failed' || creativeWorkspace.error" x-text="creativeWorkspace.error || creativeWorkspace.jobMessage"></div>
+                    <div class="small text-muted mt-3"><i class="bi bi-info-circle me-1"></i>关闭弹窗不会中断生成任务。</div>
+                </div>
+                <div class="modal-footer">
+                    <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">后台运行</button>
+                    <button type="button" class="btn btn-outline-light" x-show="creativeWorkspace.jobId" @click="creativeGenerationProgressModal.hide(); window.location.assign('/tasks?job_id=' + encodeURIComponent(creativeWorkspace.jobId))"><i class="bi bi-list-ul me-1"></i>任务日志</button>
+                    <button type="button" class="btn btn-primary" x-show="creativeJobRetryable()" @click="retryCreativeJob"><i class="bi bi-arrow-clockwise me-1"></i>重试</button>
+                </div>
+            </div>
+        </div>
+    </div>
diff --git a/templates/common/workspace/project_beats.html b/templates/common/workspace/project_beats.html
new file mode 100644
index 0000000..d16e313
--- /dev/null
+++ b/templates/common/workspace/project_beats.html
@@ -0,0 +1,9 @@
+<div>
+                    <div class="d-flex justify-content-between align-items-end mb-3"><div><h5 class="mb-1">单集 Beats</h5><div class="small text-muted">只生成场景节拍、目标、阻力和转折，不生成完整正文或台词</div></div><div class="d-flex gap-2 align-items-end"><div><label class="form-label small">集数</label><input class="form-control form-control-sm" style="width:90px" type="number" min="1" :max="creativeWorkspace.project.target_episode_count" x-model.number="creativeWorkspace.selectedEpisode"></div><button class="btn btn-primary btn-sm" @click="generateProjectBeats" :disabled="creativeActionDisabled('beats')"><span class="spinner-border spinner-border-sm me-1" x-show="creativeActionRunning('beats')"></span><i class="bi bi-magic me-1" x-show="!creativeActionRunning('beats')"></i><span x-text="creativeActionRunning('beats') ? '生成中' : '生成 Beats'"></span></button><a class="btn btn-outline-secondary btn-sm" :href="creativeExportUrl('beats', 'md')"><i class="bi bi-download"></i></a></div></div>
+                    <div class="beats-generation-status border-top border-bottom py-2 mb-3" x-show="creativeWorkspace.startingAction === 'beats' || creativeWorkspace.activeAction === 'beats'" x-cloak>
+                        <div class="d-flex justify-content-between align-items-center"><span class="small" x-text="creativeWorkspace.jobMessage || '正在创建生成任务'"></span><span class="small text-muted" x-text="creativeProgressText()"></span></div>
+                        <div class="progress mt-2" style="height:6px"><div class="progress-bar" :class="creativeWorkspace.jobStatus === 'failed' ? 'bg-danger' : ''" :style="`width:${creativeProgressPercent()}%`"></div></div>
+                    </div>
+                    <template x-if="selectedProjectBeats()"><div><div class="d-flex justify-content-between align-items-center mb-2"><span class="fw-semibold" x-text="`第 ${creativeWorkspace.selectedEpisode} 集 · v${selectedProjectBeats().version}`"></span><div class="d-flex gap-2"><button class="btn btn-outline-light btn-sm" @click="saveProjectEpisode(selectedProjectBeats())" :disabled="selectedProjectBeats().status === 'locked'"><i class="bi bi-save me-1"></i>保存</button><button class="btn btn-warning btn-sm" @click="lockProjectBeats" :disabled="selectedProjectBeats().status === 'locked'"><i class="bi bi-lock me-1"></i>锁定</button></div></div><table class="table table-sm align-middle"><thead><tr><th style="width:55px">序号</th><th>场景</th><th>人物</th><th>行动目标</th><th>阻力</th><th>情绪变化</th><th>转折</th><th>结尾钩子</th></tr></thead><tbody><template x-for="beat in selectedProjectBeats().content.beats" :key="beat.order"><tr><td x-text="beat.order"></td><td><input class="form-control form-control-sm" x-model="beat.scene" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" :value="listText(beat.characters)" @input="setListField(beat, 'characters', $event.target.value)" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.goal" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.obstacle" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.emotion_change" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.turn" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.hook" :disabled="selectedProjectBeats().status === 'locked'"></td></tr></template></tbody></table></div></template>
+                    <div class="text-center text-muted py-4" x-show="!selectedProjectBeats()">当前集尚未生成 Beats</div>
+                </div>
diff --git a/templates/common/workspace/project_blueprints.html b/templates/common/workspace/project_blueprints.html
new file mode 100644
index 0000000..43ee93f
--- /dev/null
+++ b/templates/common/workspace/project_blueprints.html
@@ -0,0 +1,30 @@
+<div>
+                    <div class="d-flex justify-content-between align-items-end mb-3"><div><h5 class="mb-1">仿制方向与 Brief</h5><div class="small text-muted">只借鉴结构和爽点，不复制剧情、人设、台词或具体表达</div></div><div class="d-flex gap-2 align-items-end"><div><label class="form-label small">方向数量</label><select class="form-select form-select-sm" x-model.number="creativeWorkspace.directionCount"><option :value="3">3</option><option :value="4">4</option><option :value="5">5</option></select></div><button class="btn btn-primary btn-sm" @click="generateProjectBlueprints" :disabled="creativeActionDisabled('blueprints')"><span class="spinner-border spinner-border-sm me-1" x-show="creativeActionRunning('blueprints')"></span><i class="bi bi-stars me-1" x-show="!creativeActionRunning('blueprints')"></i><span x-text="creativeActionRunning('blueprints') ? (creativeWorkspace.startingAction === 'blueprints' ? '正在启动' : '生成中') : '生成方向'"></span></button></div></div>
+                    <table class="table table-sm table-hover align-middle mb-3"><thead><tr><th>方向</th><th>主角</th><th>核心冲突</th><th>背景</th><th>成本</th><th>风险</th><th>版本</th><th>操作</th></tr></thead><tbody><template x-for="item in latestProjectBlueprints()" :key="item.id"><tr :class="{'table-active': creativeWorkspace.selectedBlueprintId === item.id}"><td class="fw-semibold" x-text="item.content.title || '未命名方向'"></td><td x-text="item.content.protagonist || '-'"></td><td class="blueprint-conflict-cell" style="overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;" :title="item.content.core_conflict || '-'"><span x-text="item.content.core_conflict || '-'"></span></td><td x-text="item.content.background || '-'"></td><td x-text="item.content.cost_level || '-'"></td><td><span class="badge" :class="riskBadgeClass(item.risk_level)" x-text="`${riskLabel(item.risk_level)} ${Number(item.risk_score || 0).toFixed(0)}`"></span></td><td x-text="`v${item.version} · ${item.status === 'brief_locked' ? '已锁定' : '草稿'}`"></td><td><button class="btn btn-sm btn-outline-light" @click="creativeWorkspace.selectedBlueprintId = item.id"><i class="bi bi-pencil"></i></button></td></tr></template><tr x-show="!latestProjectBlueprints().length"><td colspan="8" class="text-center text-muted py-4">尚未生成仿制方向</td></tr></tbody></table>
+                    <template x-if="selectedProjectBlueprint()"><div class="border-top pt-3">
+                        <div class="row g-2 align-items-end border-bottom pb-3 mb-3">
+                            <div class="col-md-3"><label class="form-label small">项目名称</label><input class="form-control form-control-sm" x-model="creativeWorkspace.project.name" :disabled="creativeWorkspace.project.locked_brief_id"></div>
+                            <div class="col-md-2"><label class="form-label small">题材</label><input class="form-control form-control-sm" x-model="creativeWorkspace.project.genre" :disabled="creativeWorkspace.project.locked_brief_id"></div>
+                            <div class="col-md-2"><label class="form-label small">目标平台</label><select class="form-select form-select-sm" x-model="creativeWorkspace.project.target_platform" :disabled="creativeWorkspace.project.locked_brief_id"><option value="">待定</option><option value="dy">抖音</option><option value="hg">红果</option></select></div>
+                            <div class="col-md-2"><label class="form-label small">目标受众</label><input class="form-control form-control-sm" x-model="creativeWorkspace.project.target_audience" :disabled="creativeWorkspace.project.locked_brief_id"></div>
+                            <div class="col-md-1"><label class="form-label small">集数</label><input class="form-control form-control-sm" type="number" min="1" max="100" x-model.number="creativeWorkspace.project.target_episode_count" :disabled="creativeWorkspace.project.locked_brief_id"></div>
+                            <div class="col-md-1"><label class="form-label small">秒/集</label><input class="form-control form-control-sm" type="number" min="30" max="1800" x-model.number="creativeWorkspace.project.episode_duration_seconds" :disabled="creativeWorkspace.project.locked_brief_id"></div>
+                            <div class="col-md-1 d-grid"><button class="btn btn-outline-light btn-sm" @click="saveCreativeProjectSettings" :disabled="creativeWorkspace.project.locked_brief_id" title="保存 Brief 参数"><i class="bi bi-save"></i></button></div>
+                        </div>
+                        <div class="row g-3">
+                            <div class="col-md-3"><label class="form-label small">方向名称</label><input class="form-control form-control-sm" x-model="selectedProjectBlueprint().content.title" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></div>
+                            <div class="col-md-3"><label class="form-label small">新主角</label><input class="form-control form-control-sm" x-model="selectedProjectBlueprint().content.protagonist" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></div>
+                            <div class="col-md-3"><label class="form-label small">新背景</label><input class="form-control form-control-sm" x-model="selectedProjectBlueprint().content.background" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></div>
+                            <div class="col-md-3"><label class="form-label small">方向目标受众</label><input class="form-control form-control-sm" x-model="selectedProjectBlueprint().content.audience" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></div>
+                            <div class="col-md-4"><label class="form-label small">核心冲突</label><textarea class="form-control form-control-sm" rows="3" x-model="selectedProjectBlueprint().content.core_conflict" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
+                            <div class="col-md-4"><label class="form-label small">主线目标</label><textarea class="form-control form-control-sm" rows="3" x-model="selectedProjectBlueprint().content.main_goal" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
+                            <div class="col-md-4"><label class="form-label small">世界观</label><textarea class="form-control form-control-sm" rows="3" x-model="selectedProjectBlueprint().content.worldview" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
+                            <div class="col-md-6"><label class="form-label small">借鉴元素</label><textarea class="form-control form-control-sm" rows="3" :value="listText(selectedProjectBlueprint().content.borrowed_elements)" @input="setListField(selectedProjectBlueprint().content, 'borrowed_elements', $event.target.value)" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
+                            <div class="col-md-6"><label class="form-label small">必须差异化元素</label><textarea class="form-control form-control-sm" rows="3" :value="listText(selectedProjectBlueprint().content.different_elements)" @input="setListField(selectedProjectBlueprint().content, 'different_elements', $event.target.value)" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
+                            <div class="col-md-6"><label class="form-label small">前三集钩子</label><textarea class="form-control form-control-sm" rows="3" :value="listText(selectedProjectBlueprint().content.first_three_hooks)" @input="setListField(selectedProjectBlueprint().content, 'first_three_hooks', $event.target.value)" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
+                            <div class="col-md-6"><label class="form-label small">整体爽点</label><textarea class="form-control form-control-sm" rows="3" :value="listText(selectedProjectBlueprint().content.highlights)" @input="setListField(selectedProjectBlueprint().content, 'highlights', $event.target.value)" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
+                            <div class="col-12"><label class="form-label small">差异化修改说明 <span class="text-warning" x-show="selectedProjectBlueprint().risk_level === 'high'">高风险方向锁定前必填</span></label><textarea class="form-control form-control-sm" rows="2" x-model="selectedProjectBlueprint().difference_note" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
+                        </div>
+                        <div class="d-flex gap-2 mt-3"><button class="btn btn-outline-light btn-sm" @click="saveProjectBlueprint" :disabled="selectedProjectBlueprint().status === 'brief_locked'"><i class="bi bi-save me-1"></i>保存草稿</button><button class="btn btn-warning btn-sm" @click="lockProjectBrief" :disabled="selectedProjectBlueprint().status === 'brief_locked'"><i class="bi bi-lock me-1"></i>锁定 Brief</button><button class="btn btn-outline-info btn-sm" x-show="selectedProjectBlueprint().status === 'brief_locked'" @click="createProjectBlueprintVersion"><i class="bi bi-files me-1"></i>基于此版本新建</button><a class="btn btn-outline-secondary btn-sm" :href="creativeExportUrl('brief', 'md')"><i class="bi bi-download me-1"></i>导出</a></div>
+                    </div></template>
+                </div>
diff --git a/templates/common/workspace/project_characters.html b/templates/common/workspace/project_characters.html
new file mode 100644
index 0000000..ece3052
--- /dev/null
+++ b/templates/common/workspace/project_characters.html
@@ -0,0 +1,6 @@
+<div>
+                    <div class="d-flex justify-content-between align-items-center mb-3"><div><h5 class="mb-1">人物与关系</h5><div class="small text-muted">生成后可逐字段编辑，整组锁定后才开放分集大纲</div></div><div class="d-flex gap-2"><button class="btn btn-primary btn-sm" @click="generateProjectCharacters" :disabled="creativeActionDisabled('characters')"><span class="spinner-border spinner-border-sm me-1" x-show="creativeActionRunning('characters')"></span><i class="bi bi-people me-1" x-show="!creativeActionRunning('characters')"></i><span x-text="creativeActionRunning('characters') ? (creativeWorkspace.startingAction === 'characters' ? '正在启动' : '生成中') : '生成人物关系'"></span></button><button class="btn btn-outline-light btn-sm" @click="createBlankCharacterBatch"><i class="bi bi-plus-lg me-1"></i>空白版本</button><button class="btn btn-warning btn-sm" @click="lockProjectCharacters" :disabled="!latestCharacterBatchId()"><i class="bi bi-lock me-1"></i>锁定整组</button><a class="btn btn-outline-secondary btn-sm" :href="creativeExportUrl('characters', 'md')"><i class="bi bi-download"></i></a></div></div>
+                    <h6>人物卡</h6><table class="table table-sm align-middle mb-3"><thead><tr><th>角色名</th><th>年龄段</th><th>身份</th><th>内在欲望</th><th>秘密</th><th>成长线</th><th>操作</th></tr></thead><tbody><template x-for="item in currentProjectCharacters()" :key="item.id"><tr><td><input class="form-control form-control-sm" x-model="item.content.name" :disabled="item.status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="item.content.age_range" :disabled="item.status === 'locked'"></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.identity, 24)" x-model="item.content.identity" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.desire, 24)" x-model="item.content.desire" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.secret, 24)" x-model="item.content.secret" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.growth_arc, 24)" x-model="item.content.growth_arc" :disabled="item.status === 'locked'"></textarea></td><td><button class="btn btn-sm btn-outline-light" @click="saveProjectCharacter(item)" :disabled="item.status === 'locked' || creativeWorkspace.saveState['character-' + item.id] === 'saving'" :title="item.status === 'locked' ? '人物版本已锁定' : '保存人物卡'" :aria-label="item.status === 'locked' ? '人物版本已锁定' : '保存人物卡'"><i class="bi bi-save" x-show="creativeWorkspace.saveState['character-' + item.id] !== 'saving' && creativeWorkspace.saveState['character-' + item.id] !== 'saved'"></i><span class="spinner-border spinner-border-sm" x-show="creativeWorkspace.saveState['character-' + item.id] === 'saving'"></span><i class="bi bi-check2 text-success" x-show="creativeWorkspace.saveState['character-' + item.id] === 'saved'"></i></button><small class="creative-save-status" :title="item.updated_at ? `保存时间：${formatDate(item.updated_at)}` : ''" x-text="creativeItemSaveLabel(item, 'character-' + item.id)"></small></td></tr></template><tr x-show="!currentProjectCharacters().length"><td colspan="7" class="text-center text-muted py-4">暂无人物卡</td></tr></tbody></table>
+                    <h6 class="small text-muted">角色表现设置</h6><table class="table table-sm align-middle mb-4"><thead><tr><th>角色名</th><th>外在标签</th><th>台词风格</th><th>造型 Prompt</th><th>声音 Prompt</th><th>操作</th></tr></thead><tbody><template x-for="item in currentProjectCharacters()" :key="`style-${item.id}`"><tr><td class="fw-semibold" x-text="item.content.name"></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(listText(item.content.external_tags), 28)" :value="listText(item.content.external_tags)" @input="setListField(item.content, 'external_tags', $event.target.value)" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.dialogue_style, 28)" x-model="item.content.dialogue_style" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.appearance_prompt, 28)" x-model="item.content.appearance_prompt" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.voice_prompt, 28)" x-model="item.content.voice_prompt" :disabled="item.status === 'locked'"></textarea></td><td><button class="btn btn-sm btn-outline-light" @click="saveProjectCharacter(item)" :disabled="item.status === 'locked' || creativeWorkspace.saveState['character-' + item.id] === 'saving'" :title="item.status === 'locked' ? '人物版本已锁定' : '保存角色表现设置'" :aria-label="item.status === 'locked' ? '人物版本已锁定' : '保存角色表现设置'"><i class="bi bi-save" x-show="creativeWorkspace.saveState['character-' + item.id] !== 'saving' && creativeWorkspace.saveState['character-' + item.id] !== 'saved'"></i><span class="spinner-border spinner-border-sm" x-show="creativeWorkspace.saveState['character-' + item.id] === 'saving'"></span><i class="bi bi-check2 text-success" x-show="creativeWorkspace.saveState['character-' + item.id] === 'saved'"></i></button><small class="creative-save-status" :title="item.updated_at ? `保存时间：${formatDate(item.updated_at)}` : ''" x-text="creativeItemSaveLabel(item, 'character-' + item.id)"></small></td></tr></template></tbody></table>
+                    <h6>人物关系</h6><table class="table table-sm align-middle"><thead><tr><th>角色 A</th><th>角色 B</th><th>关系</th><th>冲突</th><th>变化弧线</th><th>操作</th></tr></thead><tbody><template x-for="item in currentProjectRelationships()" :key="item.id"><tr><td><input class="form-control form-control-sm" x-model="item.content.from" :disabled="item.status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="item.content.to" :disabled="item.status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="item.content.relation" :disabled="item.status === 'locked'"></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.conflict, 28)" x-model="item.content.conflict" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.change_arc, 28)" x-model="item.content.change_arc" :disabled="item.status === 'locked'"></textarea></td><td><button class="btn btn-sm btn-outline-light" @click="saveProjectRelationship(item)" :disabled="item.status === 'locked' || creativeWorkspace.saveState['relationship-' + item.id] === 'saving'" :title="item.status === 'locked' ? '人物版本已锁定' : '保存人物关系'" :aria-label="item.status === 'locked' ? '人物版本已锁定' : '保存人物关系'"><i class="bi bi-save" x-show="creativeWorkspace.saveState['relationship-' + item.id] !== 'saving' && creativeWorkspace.saveState['relationship-' + item.id] !== 'saved'"></i><span class="spinner-border spinner-border-sm" x-show="creativeWorkspace.saveState['relationship-' + item.id] === 'saving'"></span><i class="bi bi-check2 text-success" x-show="creativeWorkspace.saveState['relationship-' + item.id] === 'saved'"></i></button><small class="creative-save-status" :title="item.updated_at ? `保存时间：${formatDate(item.updated_at)}` : ''" x-text="creativeItemSaveLabel(item, 'relationship-' + item.id)"></small></td></tr></template><tr x-show="!currentProjectRelationships().length"><td colspan="6" class="text-center text-muted py-4">暂无人物关系</td></tr></tbody></table>
+                </div>
diff --git a/templates/common/workspace/project_list.html b/templates/common/workspace/project_list.html
new file mode 100644
index 0000000..2932233
--- /dev/null
+++ b/templates/common/workspace/project_list.html
@@ -0,0 +1,18 @@
+<div class="border-top border-bottom py-3 mb-3">
+                <div class="row g-2 align-items-end">
+                    <div class="col-lg-3"><label class="form-label small">已采纳选题</label><select class="form-select form-select-sm" x-model.number="creativeWorkspace.create.topic_id" @change="syncCreativeProjectSource()"><option value="">选择已采纳选题</option><template x-for="topic in creativeWorkspace.eligibleTopics" :key="topic.id"><option :value="topic.id" :disabled="!topic.locked_breakdown_id" x-text="creativeTopicOptionLabel(topic)"></option></template></select></div>
+                    <div class="col-lg-2"><label class="form-label small">拆解版本</label><select class="form-select form-select-sm" x-model.number="creativeWorkspace.create.breakdown_id" :disabled="!creativeWorkspace.create.topic_id"><option value="">选择拆解版本</option><template x-for="breakdown in creativeWorkspace.lockedBreakdowns" :key="breakdown.id"><option :value="breakdown.id" x-text="`v${breakdown.version} · 已锁定`"></option></template></select></div>
+                    <div class="col-lg-3"><label class="form-label small">项目名称</label><input class="form-control form-control-sm" x-model="creativeWorkspace.create.name" placeholder="例如：都市女性成长方向"></div>
+                    <div class="col-lg-1"><label class="form-label small">目标平台</label><select class="form-select form-select-sm" x-model="creativeWorkspace.create.target_platform"><option value="">待定</option><option value="dy">抖音</option><option value="hg">红果</option></select></div>
+                    <div class="col-lg-1"><label class="form-label small">目标集数</label><input class="form-control form-control-sm" type="number" min="1" max="100" x-model.number="creativeWorkspace.create.target_episode_count"></div>
+                    <div class="col-lg-2 d-grid"><button class="btn btn-primary btn-sm" @click="createCreativeProject" :disabled="!creativeWorkspace.create.topic_id || !creativeWorkspace.create.breakdown_id"><i class="bi bi-plus-lg me-1"></i>创建项目</button></div>
+                </div>
+            </div>
+
+            <table class="table table-sm align-middle mb-4">
+                <thead><tr><th>项目</th><th>来源选题</th><th>目标集数</th><th>当前阶段</th><th>更新时间</th></tr></thead>
+                <tbody>
+                    <template x-for="project in creativeWorkspace.projects" :key="project.id"><tr class="creative-project-row"><td><a class="project-name-button" :href="`/planning/projects/${project.id}/directions`" x-text="project.name"></a></td><td x-text="project.topic_title"></td><td x-text="project.target_episode_count"></td><td><span class="badge bg-secondary" x-text="creativeProjectStatusLabel(project.status)"></span></td><td x-text="formatDate(project.updated_at)"></td></tr></template>
+                    <tr x-show="!creativeWorkspace.projects.length"><td colspan="5" class="text-center text-muted py-4">暂无创作项目，请先从已采纳且已锁定拆解的选题创建</td></tr>
+                </tbody>
+            </table>
diff --git a/templates/common/workspace/project_outlines.html b/templates/common/workspace/project_outlines.html
new file mode 100644
index 0000000..603970f
--- /dev/null
+++ b/templates/common/workspace/project_outlines.html
@@ -0,0 +1,4 @@
+<div>
+                    <div class="d-flex justify-content-between align-items-center mb-3"><div><h5 class="mb-1">分集大纲</h5><div class="small text-muted">先规划全剧阶段弧线，再按每 10 集分批生成</div></div><div class="d-flex gap-2"><button class="btn btn-primary btn-sm" @click="generateProjectOutlines" :disabled="creativeActionDisabled('outlines')"><span class="spinner-border spinner-border-sm me-1" x-show="creativeActionRunning('outlines')"></span><i class="bi bi-list-ol me-1" x-show="!creativeActionRunning('outlines')"></i><span x-text="creativeActionRunning('outlines') ? (creativeWorkspace.startingAction === 'outlines' ? '正在启动' : '生成中') : '生成大纲'"></span></button><button class="btn btn-warning btn-sm" @click="lockProjectOutlines" :disabled="!latestOutlineBatchId()"><i class="bi bi-lock me-1"></i>锁定整批</button><a class="btn btn-outline-secondary btn-sm" :href="creativeExportUrl('outline', 'md')"><i class="bi bi-download"></i></a></div></div>
+                    <table class="table table-sm align-middle"><thead><tr><th style="width:55px">集</th><th>核心事件</th><th>冲突</th><th>人物推进</th><th>爽点</th><th>反转</th><th>结尾悬念</th><th style="width:105px">操作</th></tr></thead><tbody><template x-for="item in currentProjectOutlines()" :key="item.id"><tr><td class="fw-semibold" x-text="item.episode_number"></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.core_event" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.conflict" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.character_progress" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.highlight" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.reversal" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.cliffhanger" :disabled="item.status === 'locked'"></textarea></td><td><div class="d-flex gap-1"><button class="btn btn-sm btn-outline-light" @click="saveProjectEpisode(item)" :disabled="item.status === 'locked'" title="保存大纲"><i class="bi bi-save"></i></button><a class="btn btn-sm btn-outline-primary" :href="`/writing/projects/${creativeWorkspace.project.id}/episodes/${item.episode_number}/beats`" title="打开单集节拍表"><i class="bi bi-arrow-right"></i></a></div></td></tr></template><tr x-show="!currentProjectOutlines().length"><td colspan="8" class="text-center text-muted py-4">暂无分集大纲</td></tr></tbody></table>
+                </div>
diff --git a/templates/common/workspace/project_progress.html b/templates/common/workspace/project_progress.html
new file mode 100644
index 0000000..1aec1d2
--- /dev/null
+++ b/templates/common/workspace/project_progress.html
@@ -0,0 +1,12 @@
+<div class="border-bottom pb-3 mb-3" x-show="creativeWorkspace.startingAction || creativeWorkspace.jobId" x-cloak role="status" aria-live="polite">
+                    <div class="d-flex justify-content-between align-items-center mb-1"><span class="small fw-semibold" x-text="creativeWorkspace.jobMessage || '后台生成中'"></span><span class="small text-muted" x-text="creativeProgressText()"></span></div>
+                    <div class="small text-muted mb-2" x-text="creativeProgressElapsedText()"></div>
+                    <div class="progress mb-2" style="height:8px" role="progressbar" :aria-valuenow="creativeProgressIndeterminate() ? null : creativeProgressPercent()"><div class="progress-bar" :class="{'progress-bar-striped progress-bar-animated': creativeProgressIndeterminate(), 'bg-danger': creativeWorkspace.jobStatus === 'failed'}" :style="`width:${creativeProgressIndeterminate() ? 100 : creativeProgressPercent()}%`"></div></div>
+                    <div class="d-flex gap-2">
+                        <button class="btn btn-sm btn-outline-warning" x-show="!creativeWorkspace.paused && creativeJobRunning()" @click="pauseCreativeJob"><i class="bi bi-pause-fill me-1"></i>暂停</button>
+                        <button class="btn btn-sm btn-outline-success" x-show="creativeWorkspace.paused && creativeJobRunning()" @click="resumeCreativeJob"><i class="bi bi-play-fill me-1"></i>继续</button>
+                        <button class="btn btn-sm btn-outline-danger" x-show="creativeJobRunning()" @click="cancelCreativeJob"><i class="bi bi-stop-fill me-1"></i>取消</button>
+                        <button class="btn btn-sm btn-outline-light" x-show="creativeJobRetryable()" @click="retryCreativeJob"><i class="bi bi-arrow-repeat me-1"></i>重试</button>
+                        <button class="btn btn-sm btn-outline-secondary" @click="window.location.assign('/tasks?job_id=' + encodeURIComponent(creativeWorkspace.jobId))"><i class="bi bi-list-ul me-1"></i>任务日志</button>
+                    </div>
+                </div>
diff --git a/templates/common/workspace/selection_dramas.html b/templates/common/workspace/selection_dramas.html
new file mode 100644
index 0000000..781ba13
--- /dev/null
+++ b/templates/common/workspace/selection_dramas.html
@@ -0,0 +1,91 @@
+<section>
+            <div class="d-flex align-items-center justify-content-between mb-3">
+                <h3 class="mb-0">剧目检索下载</h3>
+            </div>
+            <div class="row g-3">
+                <div class="col-12">
+                    <div class="card mb-3">
+                        <div class="card-header">检索并选择剧集</div>
+                        <div class="card-body">
+                            <div class="position-relative mb-3">
+                                <div class="input-group">
+                                    <input class="form-control" x-model="dramaAnalysis.title" @input="onDramaTitleInput" @keydown.enter="searchDrama" autocomplete="off" placeholder="输入剧名，自动联想红果剧目">
+                                    <button class="btn btn-primary" @click="searchDrama" :disabled="dramaAnalysis.loading || !dramaAnalysis.title.trim()">
+                                        <i class="bi bi-search me-1"></i>搜索
+                                    </button>
+                                </div>
+                                <div class="list-group position-absolute w-100 shadow" style="top: calc(100% + 2px); z-index: 20;" x-show="dramaAnalysis.suggestionLoading || dramaAnalysis.suggestions.length" @click.outside="dramaAnalysis.suggestions = []">
+                                    <div class="list-group-item bg-dark text-muted border-secondary py-2" x-show="dramaAnalysis.suggestionLoading">
+                                        <i class="bi bi-search me-1"></i>正在联想剧目…
+                                    </div>
+                                    <template x-for="suggestion in dramaAnalysis.suggestions" :key="suggestion.book_id">
+                                        <button type="button" class="list-group-item list-group-item-action bg-dark text-light border-secondary text-start" @click="chooseDramaSuggestion(suggestion)">
+                                            <span class="fw-semibold" x-text="suggestion.title || '未命名剧目'"></span>
+                                            <span class="small text-muted ms-2" x-text="`${suggestion.episode_count || '-'} 集 · ${suggestion.summary || '暂无简介'}`"></span>
+                                        </button>
+                                    </template>
+                                </div>
+                            </div>
+                            <div class="alert alert-danger" x-show="dramaAnalysis.error" x-text="dramaAnalysis.error"></div>
+                            <div class="alert alert-warning" x-show="dramaAnalysis.searchSource === 'douyin_fallback'">
+                                <div class="fw-semibold">红果检索已降级至抖音</div>
+                                <div class="small" x-text="dramaAnalysis.fallbackReason"></div>
+                                <div class="small mt-1">请手动确认视频。候选可能是推广或片段，并非完整剧集。</div>
+                            </div>
+                            <div class="list-group mb-3" x-show="dramaAnalysis.candidates.length">
+                                <template x-for="candidate in dramaAnalysis.candidates" :key="candidate.book_id || candidate.aweme_id">
+                                    <button type="button" class="list-group-item list-group-item-action bg-transparent text-light border-secondary" :class="{active: (candidate.book_id && dramaAnalysis.selected?.book_id === candidate.book_id) || (candidate.aweme_id && dramaAnalysis.selected?.aweme_id === candidate.aweme_id)}" @click="selectDramaCandidate(candidate)" :disabled="dramaAnalysis.loading">
+                                        <div class="d-flex gap-3 align-items-center">
+                                            <img x-show="candidate.cover" :src="candidate.cover" alt="" style="width: 42px; height: 56px; object-fit: cover;">
+                                            <div class="text-start">
+                                                <div class="fw-semibold" x-text="candidate.title || '未命名剧目'"></div>
+                                                <div class="small text-muted" x-show="dramaAnalysis.searchSource !== 'douyin_fallback'" x-text="`${candidate.episode_count || '-'} 集 · ${candidate.summary || '暂无简介'}`"></div>
+                                                <div class="small text-muted" x-show="dramaAnalysis.searchSource === 'douyin_fallback'" x-text="`${candidate.author || '未知作者'} · 时长 ${formatDramaDuration(candidate.duration)} · 点赞 ${candidate.like_count || 0} · 评论 ${candidate.comment_count || 0}`"></div>
+                                            </div>
+                                        </div>
+                                    </button>
+                                </template>
+                            </div>
+                            <div x-show="dramaAnalysis.selected" class="border border-secondary p-3">
+                                <div class="fw-semibold mb-3" x-text="dramaAnalysis.selected?.title"></div>
+                                <div x-show="dramaAnalysis.searchSource !== 'douyin_fallback'" class="row g-3 align-items-end">
+                                    <div class="col-md-8">
+                                        <label class="form-label">集数</label>
+                                        <select class="form-select" x-model.number="dramaAnalysis.episode">
+                                            <template x-for="episode in dramaAnalysis.episodes" :key="episode.episode">
+                                                <option :value="episode.episode" x-text="`${episode.episode}. ${episode.title}`"></option>
+                                            </template>
+                                        </select>
+                                    </div>
+                                    <div class="col-md-4 d-grid">
+                                        <button class="btn btn-success" @click="startDramaAnalysis" :disabled="dramaAnalysis.loading || !dramaAnalysis.episode">
+                                            <i class="bi bi-download me-1"></i>下载剧集
+                                        </button>
+                                    </div>
+                                </div>
+                                <div x-show="dramaAnalysis.searchSource === 'douyin_fallback'">
+                                    <div class="alert alert-warning py-2">
+                                        <span x-text="`来源：抖音 · 作者：${dramaAnalysis.selected?.author || '未知作者'} · 视频：${dramaAnalysis.selected?.title || ''}`"></span>
+                                        <div class="small mt-1">该视频可能是推广或片段，非完整剧集；确认后仅下载这一个视频。</div>
+                                    </div>
+                                    <button class="btn btn-success" @click="startDramaAnalysis" :disabled="dramaAnalysis.loading || !dramaAnalysis.selected?.aweme_id">
+                                        <i class="bi bi-download me-1"></i>下载抖音视频并解析
+                                    </button>
+                                </div>
+                            </div>
+                        </div>
+                    </div>
+                    <div class="card" x-show="dramaAnalysis.jobId">
+                        <div class="card-header">下载产物</div>
+                        <div class="card-body">
+                            <div class="mb-2">任务：<span x-text="dramaAnalysis.jobId"></span></div>
+                            <div class="d-flex gap-2 flex-wrap">
+                                <template x-for="item in dramaJobArtifacts()" :key="item.path">
+                                    <button class="btn btn-sm btn-outline-light" @click="downloadFile(item.path)" x-text="item.path.replace(/\\/g, '/').split('/').pop()"></button>
+                                </template>
+                            </div>
+                        </div>
+                    </div>
+                </div>
+            </div>
+        </section>
diff --git a/templates/common/workspace/selection_modals.html b/templates/common/workspace/selection_modals.html
new file mode 100644
index 0000000..e7de79c
--- /dev/null
+++ b/templates/common/workspace/selection_modals.html
@@ -0,0 +1,182 @@
+<div class="modal fade" id="topicTranscriptPreviewModal" tabindex="-1" aria-labelledby="topicTranscriptPreviewModalLabel" aria-hidden="true">
+        <div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
+            <div class="modal-content">
+                <div class="modal-header">
+                    <div>
+                        <h5 class="modal-title" id="topicTranscriptPreviewModalLabel"><i class="bi bi-file-earmark-text me-2"></i>剧目转写预览</h5>
+                        <div class="small text-muted mt-1"><span x-text="topicTranscriptPreview.topic?.title || ''"></span><span x-show="topicTranscriptPreview.episodes.length" x-text="` · 共 ${topicTranscriptPreview.episodes.length} 集`"></span></div>
+                    </div>
+                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
+                </div>
+                <div class="modal-body">
+                    <div x-show="topicTranscriptPreview.loading" class="py-5 text-center"><span class="spinner-border"></span><div class="small text-muted mt-2">正在读取剧目转写</div></div>
+                    <div x-show="topicTranscriptPreview.error" class="alert alert-warning" x-text="topicTranscriptPreview.error"></div>
+                    <div x-show="!topicTranscriptPreview.loading && selectedTopicTranscript()">
+                        <div class="d-flex flex-wrap justify-content-between align-items-end gap-2 mb-3">
+                            <div>
+                                <label class="form-label small">预览集数</label>
+                                <select class="form-select form-select-sm" style="min-width: 180px" x-model.number="topicTranscriptPreview.selectedEpisode">
+                                    <template x-for="episode in topicTranscriptPreview.episodes" :key="episode.episode_number"><option :value="episode.episode_number" x-text="`第 ${episode.episode_number} 集`"></option></template>
+                                </select>
+                            </div>
+                            <div class="d-flex align-items-center gap-2">
+                                <span class="small text-muted" x-text="selectedTopicTranscript() ? `${transcriptSourceLabel(selectedTopicTranscript().transcript_source)} · ${selectedTopicTranscript().character_count} 字` : ''"></span>
+                                <div class="btn-group btn-group-sm">
+                                    <button type="button" class="btn btn-outline-secondary" @click="moveTopicTranscript(-1)" :disabled="!canMoveTopicTranscript(-1)" title="上一集"><i class="bi bi-chevron-left"></i><span class="ms-1">上一集</span></button>
+                                    <button type="button" class="btn btn-outline-secondary" @click="moveTopicTranscript(1)" :disabled="!canMoveTopicTranscript(1)" title="下一集"><span class="me-1">下一集</span><i class="bi bi-chevron-right"></i></button>
+                                </div>
+                            </div>
+                        </div>
+                        <pre class="file-preview-text topic-transcript-text" x-text="selectedTopicTranscript()?.text || ''"></pre>
+                    </div>
+                </div>
+                <div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button></div>
+            </div>
+        </div>
+    </div>
+
+    <div class="modal fade" id="audioModal" tabindex="-1">
+        <div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
+            <div class="modal-content">
+                <div class="modal-header">
+                    <h5 class="modal-title">音频分析预览</h5>
+                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
+                </div>
+                <div class="modal-body">
+                    <div x-show="audioAnalysis.status === 'loading'" class="d-flex align-items-center gap-2">
+                        <span class="spinner-border spinner-border-sm"></span>
+                        <span>分析中...</span>
+                    </div>
+                    <div x-show="audioAnalysis.status === 'error'" class="alert alert-danger" x-text="audioAnalysis.message"></div>
+                    <div x-show="audioAnalysis.status === 'success'">
+                        <div class="mb-3">
+                            <div class="fw-semibold" x-text="audioAnalysis.file_name"></div>
+                            <small class="text-muted" x-text="audioAnalysis.analysis_time"></small>
+                        </div>
+                        <h6>摘要</h6>
+                        <div class="border border-secondary rounded p-3 mb-3" x-text="audioAnalysis.summary || '无摘要'"></div>
+                        <h6>关键点</h6>
+                        <ul>
+                            <template x-for="point in audioAnalysis.key_points || []" :key="point">
+                                <li x-text="point"></li>
+                            </template>
+                        </ul>
+                        <h6>转写文本</h6>
+                        <pre class="border border-secondary rounded p-3 text-light" style="white-space: pre-wrap; max-height: 360px;" x-text="audioAnalysis.transcript"></pre>
+                    </div>
+                </div>
+                <div class="modal-footer">
+                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
+                    <button type="button" class="btn btn-primary" x-show="audioAnalysis.status === 'success'" @click="downloadAudioAnalysis">下载结果</button>
+                </div>
+            </div>
+        </div>
+    </div>
+
+    <div class="download-progress-toast" x-show="dramaDownload.background && dramaDownload.jobId" x-cloak>
+        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
+            <button type="button" class="btn btn-sm btn-link p-0 text-decoration-none text-start flex-grow-1" @click="dramaDownload.background = false; dramaDownloadModal.show()">
+                <span class="fw-semibold text-light" x-text="dramaDownload.title || '剧集下载' "></span>
+                <span class="small text-muted ms-2" x-text="`${Number(dramaDownload.progress?.percent || 0).toFixed(0)}%`"></span>
+            </button>
+            <button type="button" class="btn btn-sm btn-outline-secondary" @click="dramaDownload.background = false" title="关闭下载进度" aria-label="关闭下载进度"><i class="bi bi-x-lg"></i></button>
+        </div>
+        <div class="small text-muted mb-2" x-text="dramaDownload.progressMessage || '正在下载' "></div>
+        <div class="progress mb-2" role="progressbar"><div class="progress-bar progress-bar-striped progress-bar-animated" :class="dramaDownload.paused ? 'bg-warning' : ''" :style="`width: ${dramaDownload.progress?.percent || 0}%`"></div></div>
+        <div class="d-flex justify-content-between align-items-center gap-2"><span class="small text-muted" x-text="`${dramaDownload.progress?.completed || 0} / ${dramaDownload.progress?.total || 0}`"></span><div class="d-flex gap-1"><button type="button" class="btn btn-sm btn-outline-warning" x-show="!dramaDownload.paused && !dramaDownload.finished" @click="pauseDramaDownload" title="暂停下载" aria-label="暂停下载"><i class="bi bi-pause-fill"></i></button><button type="button" class="btn btn-sm btn-outline-success" x-show="dramaDownload.paused && !dramaDownload.finished" @click="resumeDramaDownload" title="继续下载" aria-label="继续下载"><i class="bi bi-play-fill"></i></button><button type="button" class="btn btn-sm btn-outline-primary" @click="dramaDownload.background = false; dramaDownloadModal.show()" title="查看下载详情" aria-label="查看下载详情"><i class="bi bi-arrow-up-right"></i></button></div></div>
+    </div>
+
+    <div class="modal fade" id="dramaDownloadModal" tabindex="-1">
+        <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
+            <div class="modal-content">
+                <div class="modal-header">
+                    <h5 class="modal-title"><i class="bi bi-download me-2"></i>下载剧集</h5>
+                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
+                </div>
+                <div class="modal-body">
+                    <div class="mb-3"><span class="text-muted">剧名：</span><strong x-text="dramaDownload.title"></strong></div>
+                    <div class="alert alert-danger py-2" x-show="dramaDownload.error" x-text="dramaDownload.error"></div>
+                    <div class="mb-3" x-show="dramaDownload.searching"><div class="d-flex align-items-center gap-2"><span class="spinner-border spinner-border-sm"></span><span>正在检索剧目与集数…</span></div></div>
+                    <div class="alert alert-warning py-2" x-show="dramaDownload.source === 'douyin_fallback'" x-text="`${dramaDownload.fallbackReason}。请手动确认抖音视频；该视频可能是片段或推广内容。`"></div>
+                    <div class="mb-3 border border-secondary rounded p-3" x-show="dramaDownload.starting || dramaDownload.jobId">
+                        <div class="d-flex justify-content-between align-items-center gap-2 mb-2">
+                            <div class="d-flex align-items-center gap-2"><span class="badge" :class="dramaDownloadStatusClass()" x-text="dramaDownloadStatusLabel()"></span><span class="small" x-text="dramaDownload.progressMessage || '正在创建下载任务…'"></span></div>
+                            <strong x-text="`${Number(dramaDownload.progress?.percent || 0).toFixed(0)}%`"></strong>
+                        </div>
+                        <div class="progress" role="progressbar"><div class="progress-bar progress-bar-striped progress-bar-animated" :class="dramaDownload.paused ? 'bg-warning' : ''" :style="`width: ${dramaDownload.progress?.percent || 0}%`"></div></div>
+                        <div class="small text-info mt-2" x-show="dramaDownloadCurrentDescription()" x-text="dramaDownloadCurrentDescription()"></div>
+                        <div class="small text-muted mt-1" x-text="`${dramaDownload.progress?.completed || 0} / ${dramaDownload.progress?.total || 0} ${dramaDownload.source === 'douyin_fallback' ? '个视频' : '集'}`"></div>
+                        <div class="small text-muted mt-1" x-show="Number(dramaDownload.progress?.total_bytes || 0) > 0" x-text="`${formatDramaBytes(dramaDownload.progress?.downloaded_bytes || 0)} / ${formatDramaBytes(dramaDownload.progress?.total_bytes || 0)}`"></div>
+                        <div class="small text-muted mt-1" x-show="dramaDownload.source === 'douyin_fallback'" x-text="`已下载 ${dramaDownload.progress?.downloaded || 0} · 付费跳过 ${dramaDownload.progress?.skipped_paid || 0} · 失败 ${dramaDownload.progress?.failed || 0}`"></div>
+                        <div class="small text-muted mt-2"><i class="bi bi-info-circle me-1"></i>关闭窗口不会中断下载任务，右下角会继续显示下载进度。</div>
+                    </div>
+                    <div x-show="!dramaDownload.searching && !dramaDownload.jobId && dramaDownload.candidates.length">
+                        <label class="form-label" x-text="dramaDownload.source === 'douyin_fallback' ? '确认抖音视频' : '确认剧目'"></label>
+                        <div x-show="dramaDownload.source !== 'douyin_fallback'">
+                            <template x-for="candidate in dramaDownload.candidates" :key="candidate.book_id">
+                                <button type="button" class="list-group-item list-group-item-action bg-transparent text-light border-secondary mb-2" :class="{active: dramaDownload.selectedCandidate?.book_id === candidate.book_id}" @click="selectDramaDownloadCandidate(candidate)">
+                                    <div class="fw-semibold text-start" x-text="candidate.title"></div><div class="small text-muted text-start" x-text="`${candidate.episode_count || '-'} 集 · ${candidate.summary || '暂无简介'}`"></div>
+                                </button>
+                            </template>
+                        </div>
+                        <div x-show="dramaDownload.source === 'douyin_fallback'">
+                            <div class="d-flex justify-content-between align-items-center mb-2">
+                                <div><span class="small text-muted" x-text="`已选择 ${dramaDownload.selectedAwemeIds.length} 个视频`"></span><span class="small text-info ms-2" x-show="selectedDouyinEpisodeSummary()" x-text="selectedDouyinEpisodeSummary()"></span></div>
+                                <label class="form-check small mb-0"><input class="form-check-input me-1" type="checkbox" x-model="dramaDownload.allDouyinCandidates" @change="toggleAllDouyinDownloadCandidates">全选当前结果</label>
+                            </div>
+                            <template x-for="candidate in dramaDownload.candidates" :key="candidate.aweme_id">
+                                <label class="list-group-item bg-transparent text-light border-secondary mb-2" :class="{'border-primary': dramaDownload.selectedAwemeIds.includes(candidate.aweme_id), 'opacity-75': candidate.is_paid}">
+                                    <div class="d-flex gap-2 align-items-start">
+                                        <input class="form-check-input mt-1" type="checkbox" :value="candidate.aweme_id" x-model="dramaDownload.selectedAwemeIds" :disabled="candidate.is_paid" @change="syncDouyinDownloadSelection">
+                                        <div class="flex-grow-1 min-w-0">
+                                            <div class="fw-semibold text-start"><span x-text="candidate.title"></span><span class="badge bg-primary ms-2" x-show="candidate.is_primary">首条</span><span class="badge bg-warning text-dark ms-2" x-show="candidate.is_paid">需付费</span></div>
+                                            <div class="small text-muted text-start"><span x-text="`${candidate.author || '未知作者'} · 时长 ${formatDramaDuration(candidate.duration)} · 点赞 ${candidate.like_count || 0} · 评论 ${candidate.comment_count || 0}`"></span><span class="badge bg-secondary ms-2" x-show="candidate.episode_label" x-text="candidate.episode_label"></span></div>
+                                            <div class="small text-warning text-start" x-show="candidate.is_paid" x-text="candidate.paid_reason || '该视频需要付费，下载时将自动跳过'"></div>
+                                        </div>
+                                    </div>
+                                </label>
+                            </template>
+                        </div>
+                        <div class="alert alert-info py-2 mt-2" x-show="dramaDownload.source === 'douyin_fallback' && dramaDownload.candidates.some(candidate => dramaDownload.selectedAwemeIds.includes(candidate.aweme_id) && Number(candidate.episode_count || 0) > 1)">标注多集的抖音结果是一个合集视频，将保存为一个视频文件，并在剧本和清单中记录覆盖集数。</div>
+                    </div>
+                    <div class="mt-3" x-show="dramaDownload.episodesLoading"><div class="d-flex align-items-center gap-2"><span class="spinner-border spinner-border-sm"></span><span>正在读取集数…</span></div></div>
+                    <div x-show="dramaDownload.selectedCandidate && dramaDownload.episodes.length" class="mt-3">
+                        <div class="d-flex justify-content-between align-items-center mb-2"><label class="form-label mb-0">选择集数</label><label class="form-check small"><input class="form-check-input me-1" type="checkbox" x-model="dramaDownload.allEpisodes" @change="toggleAllDramaEpisodes">全剧下载</label></div>
+                        <div class="row row-cols-2 row-cols-md-4 g-2">
+                            <template x-for="episode in dramaDownload.episodes" :key="episode.episode">
+                                <label class="form-check border rounded p-2" :class="isDramaEpisodeDownloaded(episode) ? 'border-success opacity-75' : 'border-secondary'"><template x-if="!isDramaEpisodeDownloaded(episode)"><input class="form-check-input ms-0 me-1" type="checkbox" :value="episode.episode" x-model="dramaDownload.selectedEpisodes" :disabled="dramaDownload.allEpisodes"></template><template x-if="isDramaEpisodeDownloaded(episode)"><i class="bi bi-check-circle-fill text-success me-1" title="已下载"></i></template><span x-text="`${episode.episode}. ${episode.title}`"></span><span class="badge bg-success ms-1" x-show="episode.download_status?.status === 'downloaded'">已下载</span></label>
+                            </template>
+                        </div>
+                    </div>
+                    <div class="alert alert-warning mt-3" x-show="dramaDownload.source === 'douyin_fallback' && dramaDownload.selectedAwemeIds.length">来源：抖音降级。将按选择顺序逐个下载和转写，明确需要付费的视频会说明原因并跳过。</div>
+                </div>
+                <div class="modal-footer">
+                    <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">后台下载</button>
+                    <button type="button" class="btn btn-outline-primary" x-show="dramaDownload.finished" @click="openDramaDownloadFiles"><i class="bi bi-folder2-open me-1"></i>查看文件</button>
+                    <button type="button" class="btn btn-outline-warning" x-show="dramaDownload.jobId && !dramaDownload.finished && !dramaDownload.paused" @click="pauseDramaDownload"><i class="bi bi-pause-circle me-1"></i>暂停</button>
+                    <button type="button" class="btn btn-outline-info" x-show="dramaDownload.jobId && !dramaDownload.finished && dramaDownload.paused" @click="resumeDramaDownload"><i class="bi bi-play-circle me-1"></i>继续</button>
+                    <button type="button" class="btn btn-outline-danger" x-show="dramaDownload.jobId && !dramaDownload.finished" @click="cancelDramaDownload"><i class="bi bi-x-circle me-1"></i>取消</button>
+                    <button type="button" class="btn btn-primary" x-show="((dramaDownload.source === 'douyin_fallback' && dramaDownload.selectedAwemeIds.length) || (dramaDownload.source !== 'douyin_fallback' && dramaDownload.selectedCandidate && dramaDownload.episodes.length)) && !dramaDownload.jobId" @click="startDramaDownload" :disabled="dramaDownload.starting || (dramaDownload.source === 'douyin_fallback' ? !dramaDownload.selectedAwemeIds.length : !dramaSelectedEpisodesForDownload().length)"><i class="bi bi-download me-1"></i><span x-text="dramaDownload.source === 'douyin_fallback' ? '批量下载抖音视频并解析' : '开始下载'"></span></button>
+                </div>
+            </div>
+        </div>
+    </div>
+
+    <div class="modal fade" id="transcriptRequiredModal" tabindex="-1" aria-labelledby="transcriptRequiredModalLabel" aria-hidden="true">
+        <div class="modal-dialog modal-dialog-centered">
+            <div class="modal-content">
+                <div class="modal-header">
+                    <h5 class="modal-title" id="transcriptRequiredModalLabel"><i class="bi bi-file-earmark-text me-2"></i>缺少剧目脚本</h5>
+                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
+                </div>
+                <div class="modal-body">
+                    <p class="mb-2">请先下载剧目脚本再拆解</p>
+                    <div class="small text-muted">至少完成一集剧目脚本转写后，系统才能根据真实剧情生成个性化拆解。</div>
+                </div>
+                <div class="modal-footer">
+                    <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">取消</button>
+                    <button type="button" class="btn btn-primary" @click="transcriptRequiredModal.hide(); openTopicDownload(breakdownGeneration.topic)"><i class="bi bi-download me-1"></i>去下载</button>
+                </div>
+            </div>
+        </div>
+    </div>
diff --git a/templates/common/workspace/selection_ranks.html b/templates/common/workspace/selection_ranks.html
new file mode 100644
index 0000000..e9adf10
--- /dev/null
+++ b/templates/common/workspace/selection_ranks.html
@@ -0,0 +1,216 @@
+<section>
+            <div class="d-flex align-items-center justify-content-between mb-3">
+                <div>
+                    <h3 class="mb-1">今日榜单</h3>
+                    <div class="text-muted small">
+                        <span x-show="todayRankData?.selected_snapshot">
+                            当前：<span x-text="todayRankData?.selected_snapshot?.captured_at || '-'"></span>
+                            <span class="ms-2">上一期：<span x-text="todayRankData?.previous_snapshot?.captured_at || '暂无'"></span></span>
+                        </span>
+                        <span x-show="!todayRankData?.selected_snapshot">默认展示本地最新榜单快照</span>
+                    </div>
+                </div>
+                <div class="d-flex gap-2">
+                    <div class="btn-group btn-group-sm rank-platform-toggle me-2" role="group">
+                        <button type="button" class="btn btn-outline-info" :class="{active: todayRankPlatform === 'dy'}" @click="setTodayRankRoute('dy')">抖音</button>
+                        <button type="button" class="btn btn-outline-info" :class="{active: todayRankPlatform === 'hg'}" @click="setTodayRankRoute('hg')">红果</button>
+                    </div>
+                    <select class="form-select form-select-sm d-none" style="width: 130px;" x-model="todayRankPlatform" @change="setTodayRankRoute(todayRankPlatform)">
+                        <option value="dy">抖音</option>
+                        <option value="hg">红果</option>
+                    </select>
+                    <select class="form-select form-select-sm" style="width: 150px;" x-model="todayRankDate" @change="fetchTodayRank">
+                        <option value="">最新日期</option>
+                        <template x-for="date in todayRankDates()" :key="date">
+                            <option :value="date" x-text="date"></option>
+                        </template>
+                    </select>
+                    <button class="btn btn-outline-secondary btn-sm" @click="fetchTodayRank" :disabled="todayRankLoading">
+                        <i class="bi bi-arrow-clockwise me-1"></i>刷新
+                    </button>
+                </div>
+            </div>
+
+            <div class="alert alert-danger" x-show="todayRankError" x-text="todayRankError"></div>
+            <div class="alert alert-secondary" x-show="!todayRankLoading && todayRankData?.message" x-text="todayRankData?.message"></div>
+
+            <div class="row g-3 mb-3">
+                <div class="col-md-2">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">今日榜单数</div><div class="metric-value" x-text="todayRankData?.summary?.current_count || 0"></div></div></div>
+                </div>
+                <div class="col-md-2">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">上一期榜单数</div><div class="metric-value" x-text="todayRankData?.summary?.previous_count || 0"></div></div></div>
+                </div>
+                <div class="col-md-2">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">共同在榜</div><div class="metric-value" x-text="todayRankData?.summary?.common_count || 0"></div></div></div>
+                </div>
+                <div class="col-md-2">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">新上榜</div><div class="metric-value" x-text="todayRankData?.summary?.new_count || 0"></div></div></div>
+                </div>
+                <div class="col-md-2">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">下榜</div><div class="metric-value" x-text="todayRankData?.summary?.dropped_count || 0"></div></div></div>
+                </div>
+                <div class="col-md-2">
+                    <div class="card metric-card"><div class="card-body"><div class="text-muted">排名变动剧目</div><div class="metric-value fs-5" x-text="`上升 ${todayRankData?.summary?.rank_up_count || 0} / 下降 ${todayRankData?.summary?.rank_down_count || 0}`"></div></div></div>
+                </div>
+            </div>
+
+            <div x-show="todayRankPlatform === 'hg'" class="mb-3">
+                <div class="today-rank-route-nav mb-2">
+                    <template x-for="section in hongguoSections()" :key="section.key">
+                        <a href="#" class="btn btn-sm today-rank-route-link"
+                            :class="{active: section.key === todayRankSectionKey}"
+                            @click.prevent="setTodayRankRoute('hg', section.key)">
+                            <span x-text="section.label"></span>
+                        </a>
+                    </template>
+                </div>
+                <div class="today-rank-route-nav hongguo-ranking-nav">
+                    <template x-for="ranking in hongguoRankings()" :key="ranking.key">
+                        <a href="#" class="btn btn-sm today-rank-route-link"
+                            :class="{active: ranking.key === todayRankRankingKey}"
+                            @click.prevent="setTodayRankRoute('hg', todayRankSectionKey, ranking.key)">
+                            <span x-text="ranking.label"></span>
+                        </a>
+                    </template>
+                </div>
+            </div>
+
+            <div class="row g-3" x-show="todayRankPlatform !== 'hg'">
+                <template x-for="section in todayRankSections()" :key="section.key">
+                    <div class="col-12">
+                        <div class="d-flex align-items-center justify-content-between mt-2 mb-2">
+                            <h5 class="mb-0" x-text="section.label"></h5>
+                            <span class="text-muted small" x-text="summaryText(section.summary)"></span>
+                        </div>
+                        <div class="row g-3">
+                            <template x-for="ranking in section.rankings" :key="ranking.key">
+                                <div class="col-12">
+                                    <div class="card today-rank-table">
+                                        <div class="card-header ranking-heading">
+                                                <div class="ranking-title" x-text="ranking.label"></div>
+                                            <span></span>
+                                        </div>
+                                        <div class="card-header border-top py-1 d-flex gap-1 flex-wrap">
+                                            <template x-for="tbl in rankingTables(ranking)" :key="tbl.key">
+                                                <button class="btn btn-sm border-0 rounded-1 small px-2 rank-filter-btn"
+                                                    :class="getRankingActiveTable(ranking.key) === tbl.key ? 'bg-info bg-opacity-10 text-info' : 'text-muted'"
+                                                    @click="setRankingActiveTable(ranking.key, tbl.key)">
+                                                    <span x-text="tbl.title"></span>
+                                                    <span class="badge ms-1 rounded-pill"
+                                                        :class="tbl.items.length > 0 ? 'bg-secondary' : 'bg-transparent border-secondary border'"
+                                                        x-text="tbl.items.length"></span>
+                                                </button>
+                                            </template>
+                                        </div>
+                                        <div class="card-body p-0">
+                                            <template x-for="tbl in rankingTables(ranking)" :key="tbl.key">
+                                                <div x-show="getRankingActiveTable(ranking.key) === tbl.key" class="scroll-pane">
+                                                    <table class="table table-sm table-hover align-middle mb-0">
+                                                        <thead>
+                                                            <tr>
+                                                                <template x-for="column in tbl.columns" :key="`${ranking.key}-${tbl.key}-${column.key}`">
+                                                                    <th :class="rankColumnHeaderClass(column)" :style="rankColumnStyle(column)">
+                                                                        <i x-show="column.icon" class="bi me-1" :class="column.icon"></i><span x-text="column.label"></span>
+                                                                    </th>
+                                                                </template>
+                                                            </tr>
+                                                        </thead>
+                                                        <tbody>
+                                                            <template x-for="item in tbl.items" :key="`${ranking.key}-${tbl.key}-${item.record_id}-${item.rank}`">
+                                                                <tr>
+                                                                    <template x-for="column in tbl.columns" :key="`${ranking.key}-${tbl.key}-${item.record_id}-${column.key}`">
+                                                                        <td :class="rankColumnCellClass(column)" :title="rankColumnTooltip(item, column, tbl)">
+                                                                            <template x-if="column.kind === 'rank'"><span class="rank-number" :class="rankNumberClass(item.rank)" x-text="item.rank || '-'"></span></template>
+                                                                            <template x-if="column.kind === 'title'"><div class="rank-drama-title" x-text="item.title || '-'"></div></template>
+                                                                            <template x-if="column.kind === 'category'"><span class="badge bg-secondary bg-opacity-25 text-light" x-text="item.board_type || item.category_label || '-'"></span></template>
+                                                                            <template x-if="column.kind === 'status'"><span class="status-pill" :class="entryStatusClass(item, tbl.key)" x-text="entryStatus(item, tbl.key)"></span></template>
+                                                                            <template x-if="column.kind === 'rank_delta'"><span class="status-pill" :class="entryStatusClass(item, tbl.key)" x-text="formatRankDelta(item.rank_delta)"></span></template>
+                                                                            <template x-if="column.kind === 'metric' || column.kind === 'text' || column.kind === 'rank_duration'"><span x-text="rankColumnValue(item, column, tbl)"></span></template>
+                                                                        </td>
+                                                                    </template>
+                                                                </tr>
+                                                            </template>
+                                                            <tr x-show="tbl.items.length === 0">
+                                                                <td :colspan="rankingTableColspan(tbl)" class="text-center text-muted py-3">暂无数据</td>
+                                                            </tr>
+                                                        </tbody>
+                                                    </table>
+                                                </div>
+                                            </template>
+                                        </div>
+                                    </div>
+                                </div>
+                            </template>
+                            <div class="col-12" x-show="section.rankings.length === 0">
+                                <div class="text-muted py-4">暂无数据</div>
+                            </div>
+                        </div>
+                    </div>
+                </template>
+            </div>
+
+            <div class="row g-3" x-show="todayRankPlatform === 'hg'">
+                <template x-if="selectedHongguoRanking()">
+                    <div class="col-12">
+                        <div class="card today-rank-table">
+                            <div class="card-header ranking-heading">
+                                <div class="ranking-title" x-text="selectedHongguoRanking().label"></div>
+                                <span></span>
+                            </div>
+                            <div class="card-header border-top py-1 d-flex gap-1 flex-wrap">
+                                <template x-for="tbl in rankingTables(selectedHongguoRanking())" :key="tbl.key">
+                                    <button class="btn btn-sm border-0 rounded-1 small px-2 rank-filter-btn"
+                                        :class="getRankingActiveTable(selectedHongguoRanking().key) === tbl.key ? 'bg-info bg-opacity-10 text-info' : 'text-muted'"
+                                        @click="setRankingActiveTable(selectedHongguoRanking().key, tbl.key)">
+                                        <span x-text="tbl.title"></span>
+                                        <span class="badge ms-1 rounded-pill"
+                                            :class="tbl.items.length > 0 ? 'bg-secondary' : 'bg-transparent border-secondary border'"
+                                            x-text="tbl.items.length"></span>
+                                    </button>
+                                </template>
+                            </div>
+                            <div class="card-body p-0">
+                                <template x-for="tbl in rankingTables(selectedHongguoRanking())" :key="tbl.key">
+                                    <div x-show="getRankingActiveTable(selectedHongguoRanking().key) === tbl.key" class="scroll-pane">
+                                        <table class="table table-sm table-hover align-middle mb-0">
+                                            <thead>
+                                                <tr>
+                                                    <template x-for="column in tbl.columns" :key="`${selectedHongguoRanking().key}-${tbl.key}-${column.key}`">
+                                                        <th :class="rankColumnHeaderClass(column)" :style="rankColumnStyle(column)">
+                                                            <i x-show="column.icon" class="bi me-1" :class="column.icon"></i><span x-text="column.label"></span>
+                                                        </th>
+                                                    </template>
+                                                </tr>
+                                            </thead>
+                                            <tbody>
+                                                <template x-for="item in tbl.items" :key="`${selectedHongguoRanking().key}-${tbl.key}-${item.record_id}-${item.rank}`">
+                                                    <tr>
+                                                        <template x-for="column in tbl.columns" :key="`${selectedHongguoRanking().key}-${tbl.key}-${item.record_id}-${column.key}`">
+                                                            <td :class="rankColumnCellClass(column)" :title="rankColumnTooltip(item, column, tbl)">
+                                                                <template x-if="column.kind === 'rank'"><span class="rank-number" :class="rankNumberClass(item.rank)" x-text="item.rank || '-'"></span></template>
+                                                                <template x-if="column.kind === 'title'"><div class="rank-drama-title" x-text="item.title || '-'"></div></template>
+                                                                <template x-if="column.kind === 'category'"><span class="badge bg-secondary bg-opacity-25 text-light" x-text="item.board_type || item.category_label || '-'"></span></template>
+                                                                <template x-if="column.kind === 'status'"><span class="status-pill" :class="entryStatusClass(item, tbl.key)" x-text="entryStatus(item, tbl.key)"></span></template>
+                                                                <template x-if="column.kind === 'rank_delta'"><span class="status-pill" :class="entryStatusClass(item, tbl.key)" x-text="formatRankDelta(item.rank_delta)"></span></template>
+                                                                            <template x-if="column.kind === 'metric' || column.kind === 'text' || column.kind === 'rank_duration'"><span x-text="rankColumnValue(item, column, tbl)"></span></template>
+                                                            </td>
+                                                        </template>
+                                                    </tr>
+                                                </template>
+                                                <tr x-show="tbl.items.length === 0">
+                                                    <td :colspan="rankingTableColspan(tbl)" class="text-center text-muted py-3">暂无数据</td>
+                                                </tr>
+                                            </tbody>
+                                        </table>
+                                    </div>
+                                </template>
+                            </div>
+                        </div>
+                    </div>
+                </template>
+                <div class="col-12" x-show="!selectedHongguoRanking()">
+                    <div class="text-muted py-4">暂无数据</div>
+                </div>
+            </div>
+        </section>
diff --git a/templates/common/workspace/selection_topics.html b/templates/common/workspace/selection_topics.html
new file mode 100644
index 0000000..96bcd84
--- /dev/null
+++ b/templates/common/workspace/selection_topics.html
@@ -0,0 +1,109 @@
+<section>
+            <div class="d-flex align-items-center justify-content-between mb-3">
+                <h3 class="mb-0">今日爆款</h3>
+                <div class="d-flex gap-2">
+                    <select class="form-select form-select-sm" style="width: 110px;" x-model="creative.platform" @change="generateCreativeTopics">
+                        <option value="dy">抖音</option><option value="hg">红果</option>
+                    </select>
+                    <button class="btn btn-outline-light btn-sm" onclick="window.location.assign('/selection/topics')" title="打开选题库"><i class="bi bi-journal-bookmark me-1"></i>选题库</button>
+                    <button class="btn btn-primary btn-sm" @click="generateCreativeTopics" :disabled="creative.loading">
+                        <i class="bi bi-lightning-charge me-1"></i>生成 Top10
+                    </button>
+                </div>
+            </div>
+            <div class="alert alert-danger" x-show="creative.error" x-text="creative.error"></div>
+            <div class="creative-topic-table">
+                <table class="table table-sm table-hover align-middle mb-0">
+                    <thead><tr><th>剧目</th><th>平台</th><th>题材</th><th>推荐分 <button type="button" class="btn btn-link btn-sm p-0 text-muted score-help" data-bs-toggle="tooltip" data-bs-placement="top" title="由热度、增长、新鲜度和平台适配加权计算；数据缺失时会动态归一化" aria-label="推荐分计算说明"><i class="bi bi-info-circle"></i></button></th><th>状态</th><th style="width: 270px;">操作</th></tr></thead>
+                    <tbody>
+                        <template x-for="topic in creative.items" :key="topic.id">
+                            <tr>
+                                <td><div class="fw-semibold" x-text="topic.title"></div><small class="text-muted" x-text="topic.recommendation"></small></td>
+                                <td x-text="topic.platform"></td><td x-text="topic.category || '-' "></td>
+                                <td x-text="topic.score.toFixed(1)"></td>
+                                <td>
+                                    <span class="badge lifecycle-badge" :class="topicLifecycleClass(topic.lifecycle_status)" x-text="topic.lifecycle_label || topicLifecycleLabel(topic)"></span>
+                                    <span class="badge d-block mt-1" :class="breakdownStatusClass(topic)" x-text="breakdownStatusLabel(topic)"></span>
+                                </td>
+                                <td>
+                                    <button class="btn btn-sm btn-outline-success me-1" x-show="!topic.latest_breakdown_id && topic.status === 'pending_review'" @click="updateCreativeTopic(topic, 'adopted')">采纳</button>
+                                    <button class="btn btn-sm btn-outline-light me-1" x-show="!topic.latest_breakdown_id && ['pending_review', 'adopted'].includes(topic.status)" @click="createCreativeBreakdown(topic)">拆解</button>
+                                    <button class="btn btn-sm btn-outline-secondary" x-show="!topic.latest_breakdown_id && topic.status === 'pending_review'" @click="updateCreativeTopic(topic, 'rejected')">不跟进</button>
+                                    <button class="btn btn-sm btn-outline-success" x-show="!topic.latest_breakdown_id && topic.status === 'rejected'" @click="updateCreativeTopic(topic, 'adopted')"><i class="bi bi-arrow-counterclockwise me-1"></i>重新采纳</button>
+                                    <button class="btn btn-sm btn-outline-light me-1" x-show="topic.latest_breakdown_id && topic.latest_breakdown_status === 'draft' && topic.status !== 'archived'" @click="createCreativeBreakdown(topic)">继续拆解</button>
+                                    <button class="btn btn-sm btn-outline-light me-1" x-show="topic.latest_breakdown_id && topic.latest_breakdown_status !== 'draft' && topic.status !== 'archived'" @click="createCreativeBreakdown(topic)">再次拆解</button>
+                                    <button class="btn btn-sm btn-outline-warning" x-show="topic.latest_breakdown_id && topic.status !== 'archived'" @click="updateCreativeTopic(topic, 'archived')">归档</button>
+                                </td>
+                            </tr>
+                        </template>
+                        <tr x-show="!creative.loading && creative.items.length === 0"><td colspan="6" class="text-center text-muted py-4">暂无候选选题，先生成 Top10</td></tr>
+                    </tbody>
+                </table>
+            </div>
+        </section>
+
+        <section>
+            <div class="d-flex align-items-center justify-content-between mb-3">
+                <div>
+                    <h3 class="mb-1">选题库</h3>
+                    <div class="small text-muted">沉淀榜单候选与人工决策，作为后续创作的唯一选题入口</div>
+                </div>
+                <button class="btn btn-outline-secondary btn-sm" @click="fetchTopicLibrary()" :disabled="topicLibrary.loading">
+                    <i class="bi bi-arrow-clockwise me-1"></i>刷新
+                </button>
+            </div>
+            <div class="card mb-3">
+                <div class="card-body">
+                    <div class="row g-2 align-items-end">
+                        <div class="col-md-2"><label class="form-label small">平台</label><select class="form-select form-select-sm" x-model="topicLibrary.platform"><option value="">全部平台</option><option value="dy">抖音</option><option value="hg">红果</option></select></div>
+                        <div class="col-md-2"><label class="form-label small">状态</label><select class="form-select form-select-sm" x-model="topicLibrary.lifecycleStatus"><option value="">全部状态</option><option value="pending_review">待评估</option><option value="adopted">已采纳</option><option value="pending_download">待下载</option><option value="downloaded">已下载 X 集</option><option value="pending_breakdown">待拆解</option><option value="breakdown_completed">已拆解</option><option value="pending_imitation">待仿制</option><option value="imitation_generated">已仿制</option><option value="pending_character_relationships">人物关系待生成</option><option value="character_relationships_generated">人物关系已生成</option><option value="pending_episode_outlines">分集大纲待生成</option><option value="episode_outlines_generated">分集大纲已生成</option><option value="pending_beats">Beats脚本待生成</option><option value="beats_generated">Beats脚本已生成</option><option value="beats_exported">Beats脚本已导出</option><option value="rejected">不跟进</option><option value="archived">已归档</option></select></div>
+                        <div class="col-md-2"><label class="form-label small">题材</label><input class="form-control form-control-sm" x-model="topicLibrary.category" placeholder="筛选题材"></div>
+                        <div class="col-md-2"><label class="form-label small">最低推荐分</label><input class="form-control form-control-sm" type="number" min="0" max="100" x-model="topicLibrary.minScore" placeholder="0"></div>
+                        <div class="col-md-2"><label class="form-label small">拆解版本</label><select class="form-select form-select-sm" x-model="topicLibrary.hasLocked"><option value="">全部</option><option value="true">已有锁定拆解</option><option value="false">无锁定拆解</option></select></div>
+                        <div class="col-md-2 d-grid"><button class="btn btn-primary btn-sm" @click="fetchTopicLibrary()"><i class="bi bi-funnel me-1"></i>应用筛选</button></div>
+                    </div>
+                </div>
+            </div>
+            <div class="alert alert-danger" x-show="topicLibrary.error" x-text="topicLibrary.error"></div>
+            <div class="d-flex justify-content-between align-items-center mb-2 small text-muted"><span x-text="`共 ${topicLibrary.items.length} 条选题`"></span><span>评分、来源和状态会持续保留</span></div>
+            <div class="creative-topic-table">
+                <table class="table table-sm table-hover align-middle mb-0">
+                    <colgroup>
+                        <col class="col-topic">
+                        <col class="col-desc">
+                        <col class="col-source">
+                        <col class="col-rank">
+                        <col class="col-category">
+                        <col class="col-score">
+                        <col class="col-status">
+                        <col class="col-breakdown">
+                        <col class="col-actions">
+                    </colgroup>
+                    <thead><tr><th>选题</th><th>简介</th><th>来源</th><th>榜单</th><th>题材</th><th>推荐分 <button type="button" class="btn btn-link btn-sm p-0 text-muted score-help" data-bs-toggle="tooltip" data-bs-placement="top" title="由热度、增长、新鲜度和平台适配加权计算；数据缺失时会动态归一化" aria-label="推荐分计算说明"><i class="bi bi-info-circle"></i></button></th><th>状态</th><th>拆解版本</th><th>操作</th></tr></thead>
+                    <tbody>
+                        <template x-for="topic in topicLibrary.items" :key="topic.id">
+                            <tr>
+                                <td><div class="fw-semibold" x-text="topic.title"></div><small class="text-muted" x-text="topic.recommendation || '暂无推荐理由'"></small></td>
+                                <td class="topic-desc-cell" style="overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;" :title="topic.description">
+                                    <span x-text="topic.description || '暂无简介'"></span>
+                                </td>
+                                <td><span x-text="(topic.source_platforms || [topic.platform]).join('、')"></span><small class="d-block text-muted" x-text="topic.captured_at || '-' "></small></td>
+                                <td x-text="topic.rank_type || '-' "></td>
+                                <td x-text="topic.category || '-' "></td>
+                                <td><span class="fw-semibold" x-text="Number(topic.score || 0).toFixed(1)"></span></td>
+                                <td><span class="badge lifecycle-badge" :class="topicLifecycleClass(topic.lifecycle_status)" x-text="topic.lifecycle_label || topicLifecycleLabel(topic)"></span></td>
+                                <td><span class="badge breakdown-version-badge" :class="breakdownStatusClass(topic)" x-text="breakdownStatusLabel(topic)"></span></td>
+                                <td class="text-nowrap">
+                                    <button class="btn btn-sm btn-outline-light me-1" @click="createTopicLibraryBreakdown(topic)" title="生成拆解" aria-label="生成拆解"><i class="bi bi-diagram-3"></i></button>
+                                    <button class="btn btn-sm btn-outline-primary me-1" @click="openTopicDownload(topic)" title="下载剧集" aria-label="下载剧集"><i class="bi bi-download"></i></button>
+                                    <button class="btn btn-sm btn-outline-info me-1" x-show="Number(topic.downloaded_episode_count || 0) > 0" @click="openTopicTranscriptPreview(topic)" title="预览剧目转写" aria-label="预览剧目转写"><i class="bi bi-eye"></i></button>
+                                    <button class="btn btn-sm btn-outline-success me-1" x-show="topic.status !== 'adopted'" @click="updateTopicLibrary(topic, 'adopted')" title="采纳" aria-label="采纳"><i class="bi bi-check2"></i></button>
+                                    <button class="btn btn-sm btn-outline-warning" x-show="topic.status === 'adopted'" @click="updateTopicLibrary(topic, 'archived')" title="归档" aria-label="归档"><i class="bi bi-archive"></i></button>
+                                </td>
+                            </tr>
+                        </template>
+                        <tr x-show="!topicLibrary.loading && topicLibrary.items.length === 0"><td colspan="9" class="text-center text-muted py-5">暂无符合条件的选题，请先在“今日爆款”生成候选</td></tr>
+                    </tbody>
+                </table>
+            </div>
+        </section>
diff --git a/templates/index.html b/templates/index.html
index 86fdc47..aa42b35 100644
--- a/templates/index.html
+++ b/templates/index.html
@@ -1,4831 +1,12 @@
-<!DOCTYPE html>
+<!doctype html>
 <html lang="zh-CN">
 <head>
-    <meta charset="UTF-8">
-    <meta name="viewport" content="width=device-width, initial-scale=1.0">
+    <meta charset="utf-8">
+    <meta name="viewport" content="width=device-width, initial-scale=1">
     <title>AiWorker</title>
-    <link href="/static/css/bootstrap.min.css" rel="stylesheet">
-    <link href="/static/css/bootstrap-icons.css" rel="stylesheet">
-    <script src="/static/js/echarts.min.js"></script>
-    <script defer src="/static/js/alpine.min.js"></script>
-    <style>
-        :root {
-            --sidebar-width: 248px;
-            --bg-page: #12121d;
-            --bg-panel: #1d1d2b;
-            --bg-panel-2: #252936;
-            --border: #33364a;
-            --text: #f1f4f8;
-            --muted: #9aa4b2;
-            --accent: #3b95f6;
-        }
-        body {
-            margin: 0;
-            overflow: hidden;
-            background: var(--bg-page);
-            color: var(--text);
-            font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
-        }
-        [x-cloak] { display: none !important; }
-        .sidebar {
-            position: fixed;
-            inset: 0 auto 0 0;
-            width: var(--sidebar-width);
-            background: var(--bg-panel);
-            border-right: 1px solid var(--border);
-            display: flex;
-            flex-direction: column;
-        }
-        .main-content {
-            margin-left: var(--sidebar-width);
-            height: 100vh;
-            overflow-y: auto;
-            padding: 22px;
-        }
-        .nav-link {
-            color: var(--muted);
-            border-radius: 0;
-            padding: 13px 18px;
-            display: flex;
-            align-items: center;
-            gap: 10px;
-        }
-        .nav-link:hover,
-        .nav-link.active {
-            color: var(--accent);
-            background: rgba(59, 149, 246, .12);
-            box-shadow: inset 3px 0 0 var(--accent);
-        }
-        .card,
-        .modal-content {
-            background: var(--bg-panel);
-            color: var(--text);
-            border: 1px solid var(--border);
-            border-radius: 8px;
-        }
-        .card-header,
-        .modal-header,
-        .modal-footer {
-            border-color: var(--border);
-        }
-        .form-control,
-        .form-select {
-            background: #151722;
-            border-color: var(--border);
-            color: var(--text);
-        }
-        .form-control:focus,
-        .form-select:focus {
-            background: #151722;
-            color: var(--text);
-            border-color: var(--accent);
-            box-shadow: none;
-        }
-        .form-control:disabled,
-        .form-select:disabled {
-            background-color: #151722;
-            border-color: var(--border);
-            color: var(--muted);
-            opacity: 1;
-        }
-        .creative-inline-textarea {
-            min-height: 38px;
-            line-height: 1.35;
-            resize: vertical;
-        }
-        .creative-save-status {
-            display: block;
-            margin-top: 4px;
-            color: #7dd3a8;
-            font-size: 11px;
-            white-space: nowrap;
-        }
-        .creative-save-toast {
-            position: fixed;
-            top: 18px;
-            right: 24px;
-            z-index: 1080;
-            display: flex;
-            align-items: center;
-            gap: 8px;
-            max-width: min(380px, calc(100vw - 32px));
-            padding: 10px 14px;
-            background: #1d1d2b;
-            color: var(--text);
-            border: 1px solid #36a269;
-            border-radius: 6px;
-            box-shadow: 0 10px 28px rgba(0, 0, 0, .32);
-        }
-        .table {
-            --bs-table-bg: transparent;
-            --bs-table-color: var(--text);
-            --bs-table-striped-bg: rgba(255, 255, 255, .045);
-            --bs-table-striped-color: var(--text);
-            --bs-table-hover-bg: rgba(59, 149, 246, .1);
-            --bs-table-hover-color: var(--text);
-            --bs-table-active-bg: rgba(59, 149, 246, .16);
-            --bs-table-active-color: var(--text);
-        }
-        .job-row {
-            cursor: pointer;
-        }
-        .job-row.active {
-            outline: 1px solid var(--accent);
-            background: rgba(59, 149, 246, .16) !important;
-        }
-        .creative-project-row {
-            cursor: pointer;
-            transition: background-color .15s ease, box-shadow .15s ease;
-        }
-        .creative-project-row.table-active {
-            box-shadow: inset 3px 0 0 var(--accent);
-        }
-        .project-name-button {
-            appearance: none;
-            border: 0;
-            background: transparent;
-            color: var(--text);
-            padding: 0;
-            font: inherit;
-            font-weight: 700;
-            text-align: left;
-        }
-        .project-name-button:hover,
-        .creative-project-row.table-active .project-name-button {
-            color: #fff;
-        }
-        .project-name-button:focus-visible {
-            outline: 2px solid var(--accent);
-            outline-offset: 3px;
-        }
-        .scroll-pane {
-            overflow: auto;
-            max-height: 420px;
-        }
-        .breakdown-editor-pane {
-            height: min(62vh, 640px);
-            min-height: 360px;
-        }
-        .breakdown-editor-textarea {
-            flex: 1 1 auto;
-            min-height: 0;
-            resize: none;
-        }
-        .breakdown-field-guide {
-            flex: 1 1 auto;
-            min-height: 0;
-            overflow-y: auto;
-            padding-right: 6px;
-        }
-
-        /* 平台切换按钮 */
-        .rank-platform-toggle .btn {
-            min-width: 64px;
-            font-weight: 600;
-            border-color: var(--border);
-            color: var(--muted);
-        }
-        /* 榜单表格——精简后样式 */
-        .today-rank-table .ranking-heading {
-            display: grid;
-            grid-template-columns: 1fr auto 1fr;
-            align-items: center;
-            gap: 12px;
-            min-height: 42px;
-        }
-        .today-rank-table .ranking-title {
-            justify-self: center;
-            text-align: center;
-            font-weight: 800;
-            font-size: 16px;
-            letter-spacing: 0;
-        }
-        .today-rank-table .card-header .btn.active,
-        .today-rank-table .card-header .btn:hover {
-            color: var(--accent) !important;
-        }
-        .today-rank-route-nav {
-            display: flex;
-            align-items: center;
-            gap: 8px;
-            flex-wrap: wrap;
-        }
-        .today-rank-route-link {
-            border: 1px solid var(--border);
-            color: var(--muted);
-            background: rgba(255, 255, 255, .02);
-            font-weight: 700;
-            padding-inline: 20px;
-        }
-        .today-rank-route-link:hover,
-        .today-rank-route-link.active {
-            color: #eaf4ff;
-            border-color: var(--accent);
-            background: rgba(59, 149, 246, .16);
-            box-shadow: 0 0 0 1px rgba(59, 149, 246, .36);
-        }
-        .today-rank-table .rank-filter-btn {
-            display: inline-flex;
-            align-items: center;
-            justify-content: center;
-            font-weight: 600;
-        }
-        .today-rank-table .rank-filter-btn.active {
-            font-weight: 800;
-        }
-        .today-rank-table .card-header .btn .badge {
-            font-size: 0.7em;
-            background: rgba(122, 139, 164, .22) !important;
-            border: 1px solid rgba(154, 164, 178, .36);
-            color: #c9d3e5;
-        }
-        .today-rank-table .rank-number {
-            display: inline-flex;
-            align-items: center;
-            justify-content: center;
-            width: 28px;
-            height: 28px;
-            border-radius: 6px;
-            font-weight: 800;
-            font-size: 15px;
-            color: #eaf1ff;
-            background: rgba(154, 164, 178, .14);
-        }
-        .today-rank-table .rank-number.rank-top-1 {
-            color: #fff3c6;
-            background: rgba(234, 179, 8, .2);
-            border: 1px solid rgba(234, 179, 8, .38);
-        }
-        .today-rank-table .rank-number.rank-top-2 {
-            color: #e7edf8;
-            background: rgba(148, 163, 184, .2);
-            border: 1px solid rgba(148, 163, 184, .38);
-        }
-        .today-rank-table .rank-number.rank-top-3 {
-            color: #ffd9bd;
-            background: rgba(217, 119, 6, .18);
-            border: 1px solid rgba(217, 119, 6, .34);
-        }
-        .today-rank-table .rank-drama-title {
-            font-weight: 800;
-            color: #f7f9ff;
-        }
-        .today-rank-table .rank-subtitle {
-            color: #8793a8 !important;
-        }
-        .today-rank-table .rank-metric {
-            text-align: right;
-            font-variant-numeric: tabular-nums;
-            white-space: nowrap;
-        }
-        .today-rank-table .status-pill {
-            display: inline-flex;
-            align-items: center;
-            justify-content: center;
-            min-width: 58px;
-            border-radius: 999px;
-            padding: 3px 8px;
-            font-weight: 700;
-            font-size: 12px;
-            white-space: nowrap;
-        }
-        .today-rank-table .status-new {
-            color: #f9d98a;
-            background: rgba(245, 158, 11, .15);
-            border: 1px solid rgba(245, 158, 11, .28);
-        }
-        .today-rank-table .status-up {
-            color: #f5a3a8;
-            background: rgba(239, 68, 68, .14);
-            border: 1px solid rgba(239, 68, 68, .28);
-        }
-        .today-rank-table .status-down {
-            color: #8ddfcc;
-            background: rgba(20, 184, 166, .13);
-            border: 1px solid rgba(20, 184, 166, .27);
-        }
-        .today-rank-table .status-stable {
-            color: #b8c4d6;
-            background: rgba(100, 116, 139, .18);
-            border: 1px solid rgba(148, 163, 184, .24);
-        }
-        .today-rank-table .status-dropped {
-            color: #c4b5fd;
-            background: rgba(124, 58, 237, .13);
-            border: 1px solid rgba(139, 92, 246, .25);
-        }
-
-        .rank-platform-toggle .btn.active {
-            background: rgba(59, 149, 246, .18);
-            color: var(--accent);
-            border-color: var(--accent);
-            box-shadow: 0 0 0 1px var(--accent);
-        }
-
-                /* 今日榜单——固定表头 */
-        .today-rank-table .scroll-pane thead th {
-            position: sticky;
-            top: 0;
-            z-index: 2;
-            background: var(--bg-panel);
-            border-bottom: 2px solid var(--accent);
-        }
-        .today-rank-table .scroll-pane {
-            max-height: none;
-            overflow: visible;
-        }
-        .data-section {
-            min-height: calc(100vh - 44px);
-            display: flex;
-            flex-direction: column;
-        }
-        .data-card {
-            flex: 1 1 auto;
-            min-height: 0;
-            display: flex;
-            flex-direction: column;
-        }
-        .data-table-pane {
-            flex: 1 1 auto;
-            min-height: 0;
-            max-height: none;
-        }
-        .data-table-pane thead th {
-            position: sticky;
-            top: 0;
-            z-index: 1;
-            background: var(--bg-panel);
-        }
-        .data-drama-group {
-            border-top: 1px solid var(--border);
-            margin-top: 18px;
-        }
-        .data-drama-heading {
-            background: rgba(59, 149, 246, .08);
-            border-bottom: 1px solid var(--border);
-            padding: 10px 12px;
-        }
-        .data-type-heading {
-            border-bottom: 1px solid rgba(255, 255, 255, .08);
-            color: var(--muted);
-            font-size: .82rem;
-            font-weight: 600;
-            padding: 9px 12px 5px;
-        }
-        .file-preview-media {
-            display: block;
-            width: 100%;
-            max-height: 72vh;
-            background: #05070b;
-        }
-        .file-preview-text {
-            margin: 0;
-            min-height: 240px;
-            white-space: pre-wrap;
-            overflow-wrap: anywhere;
-            color: var(--text);
-        }
-        .log-pane {
-            height: 420px;
-            overflow: auto;
-            background: #10131a;
-            border-radius: 0 0 8px 8px;
-        }
-        .log-line {
-            font-family: Consolas, "Courier New", monospace;
-            font-size: 13px;
-            padding: 3px 8px;
-            white-space: pre-wrap;
-            border-bottom: 1px solid rgba(255, 255, 255, .04);
-        }
-        .text-muted {
-            color: var(--muted) !important;
-        }
-        .btn-icon {
-            width: 34px;
-            height: 34px;
-            display: inline-flex;
-            align-items: center;
-            justify-content: center;
-            padding: 0;
-        }
-        .metric-card {
-            min-height: 96px;
-        }
-        .metric-value {
-            font-size: 28px;
-            font-weight: 700;
-        }
-        .chart-box {
-            height: 280px;
-            width: 100%;
-            min-width: 360px;
-        }
-        .selling-point-list {
-            max-height: 280px;
-            overflow: auto;
-        }
-        .selling-point-item {
-            display: flex;
-            justify-content: space-between;
-            gap: 14px;
-            padding: 8px 0;
-            border-bottom: 1px solid rgba(255, 255, 255, .08);
-        }
-        .selling-point-text {
-            white-space: normal;
-            overflow-wrap: anywhere;
-            line-height: 1.5;
-        }
-        .download-progress-toast {
-            position: fixed;
-            right: 24px;
-            bottom: 24px;
-            z-index: 1060;
-            width: min(360px, calc(100vw - 32px));
-            padding: 14px;
-            background: var(--bg-panel);
-            border: 1px solid var(--border);
-            border-radius: 8px;
-            box-shadow: 0 14px 34px rgba(0, 0, 0, .34);
-        }
-        .score-help {
-            line-height: 1;
-            vertical-align: text-bottom;
-            text-decoration: none;
-        }
-        .score-help:hover,
-        .score-help:focus {
-            color: var(--accent) !important;
-        }
-
-        /* 选题库表格固定列宽 */
-        .creative-topic-table table {
-            table-layout: fixed;
-            width: 100%;
-        }
-        .creative-topic-table .col-topic { width: 18%; }
-        .creative-topic-table .col-desc { width: 24%; }
-        .creative-topic-table .col-source { width: 10%; }
-        .creative-topic-table .col-rank { width: 8%; }
-        .creative-topic-table .col-category { width: 8%; }
-        .creative-topic-table .col-score { width: 7%; }
-        .creative-topic-table .col-status { width: 7%; }
-        .creative-topic-table .col-breakdown { width: 7%; }
-        .creative-topic-table .col-actions { width: 11%; }
-
-        /* 简介和核心冲突列悬停展开 */
-        .topic-desc-cell,
-        .blueprint-conflict-cell {
-            transition: all 0.15s ease;
-        }
-        .topic-desc-cell:hover,
-        .blueprint-conflict-cell:hover {
-            overflow: visible !important;
-            -webkit-line-clamp: unset !important;
-            display: table-cell !important;
-            background: var(--bg-panel-2);
-            border-radius: 4px;
-            box-shadow: 0 2px 12px rgba(0,0,0,0.35);
-            position: relative;
-            z-index: 10;
-        }
-        @media (max-width: 767.98px) {
-            :root { --sidebar-width: 58px; }
-            .sidebar > .p-4 { padding: 14px 8px !important; text-align: center; }
-            .sidebar h4 { font-size: 0; }
-            .sidebar h4 i { font-size: 22px; margin: 0 !important; }
-            .sidebar > .p-4 .small,
-            .sidebar > .p-4 a,
-            .sidebar .nav-link span { display: none !important; }
-            .sidebar .nav-link { justify-content: center; padding: 13px 8px; }
-            .sidebar .nav-link i { font-size: 19px; }
-            .main-content { padding: 14px 12px; }
-            .creative-project-workspace .d-flex { flex-wrap: wrap; }
-            .creative-project-workspace table,
-            .creative-project-workspace thead,
-            .creative-project-workspace tbody,
-            .creative-project-workspace tr,
-            .creative-project-workspace th,
-            .creative-project-workspace td { display: block; width: 100% !important; }
-            .creative-project-workspace thead { display: none; }
-            .creative-project-workspace tbody tr { padding: 10px 0; border-bottom: 1px solid var(--border); }
-            .creative-project-workspace tbody td { border: 0; padding: 4px 0; white-space: normal !important; }
-            .creative-project-workspace .btn-group { width: 100%; margin-top: 12px; }
-            .creative-project-workspace .btn-group .btn { flex: 1 1 50%; }
-        }
-    </style>
 </head>
-<body x-data="appData()" x-init="init()">
-    <aside class="sidebar">
-        <div class="p-4 border-bottom border-secondary-subtle">
-            <h4 class="mb-1 fw-bold"><i class="bi bi-robot me-2"></i>AiWorker</h4>
-            <div class="small text-muted">壹凡短剧选品平台</div>
-            <a x-show="serverIp" :href="'http://' + serverIp" target="_blank" class="small text-decoration-none" x-text="serverIp"></a>
-        </div>
-
-        <nav class="nav flex-column py-3">
-            <a href="#" class="nav-link" :class="{active: currentTab === 'dashboard'}" @click.prevent="currentTab = 'dashboard'">
-                <i class="bi bi-speedometer2"></i><span>爬虫控制</span>
-            </a>
-            <a href="#" class="nav-link" :class="{active: currentTab === 'data'}" @click.prevent="currentTab = 'data'; fetchFiles()">
-                <i class="bi bi-folder2-open"></i><span>数据管理</span>
-            </a>
-            <a href="#" class="nav-link" :class="{active: currentTab === 'drama-analysis'}" @click.prevent="currentTab = 'drama-analysis'; fetchDramaSettings()">
-                <i class="bi bi-film"></i><span>剧目检索下载</span>
-            </a>
-            <a href="#" class="nav-link" :class="{active: currentTab === 'analytics'}" @click.prevent="currentTab = 'analytics'; refreshAnalyticsDashboard(false)">
-                <i class="bi bi-bar-chart"></i><span>分析看板</span>
-            </a>
-            <a href="#" class="nav-link" :class="{active: currentTab === 'today-rank'}" @click.prevent="currentTab = 'today-rank'; fetchTodayRank()">
-                <i class="bi bi-trophy"></i><span>今日榜单</span>
-            </a>
-            <a href="#" class="nav-link" :class="{active: currentTab === 'creative'}" @click.prevent="currentTab = 'creative'; fetchCreativeTopics()">
-                <i class="bi bi-stars"></i><span>今日爆款</span>
-            </a>
-            <a href="#" class="nav-link" :class="{active: currentTab === 'topic-library'}" @click.prevent="currentTab = 'topic-library'; fetchTopicLibrary()">
-                <i class="bi bi-journal-bookmark"></i><span>选题库</span>
-            </a>
-            <a href="#" class="nav-link" :class="{active: currentTab === 'creative-projects'}" @click.prevent="currentTab = 'creative-projects'; fetchCreativeProjects()">
-                <i class="bi bi-pencil-square"></i><span>创作项目</span>
-            </a>
-        </nav>
-
-    </aside>
-
-    <main class="main-content">
-        <section x-show="currentTab === 'dashboard'">
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <h3 class="mb-0">爬虫控制</h3>
-                <button class="btn btn-outline-secondary btn-sm" @click="fetchJobs">
-                    <i class="bi bi-arrow-clockwise me-1"></i>刷新
-                </button>
-            </div>
-
-            <div class="card mb-3">
-                <div class="card-header d-flex align-items-center justify-content-between">
-                    <span><i class="bi bi-list-task me-2"></i>任务列表</span>
-                    <span class="badge bg-primary" x-show="batchProgress" x-text="'批量：' + batchProgress"></span>
-                </div>
-                <div class="table-responsive scroll-pane">
-                    <table class="table table-sm table-hover align-middle mb-0">
-                        <thead>
-                            <tr>
-                                <th>Job</th>
-                                <th style="width: 90px;">平台</th>
-                                <th style="width: 100px;">类型</th>
-                                <th style="width: 110px;">状态</th>
-                                <th style="width: 110px;">操作</th>
-                            </tr>
-                        </thead>
-                        <tbody>
-                            <template x-for="job in jobs" :key="job.job_id">
-                                <tr class="job-row" :class="{active: selectedJobId === job.job_id}" @click="selectJob(job.job_id)">
-                                    <td>
-                                        <div class="fw-semibold" x-text="job.job_id"></div>
-                                        <small class="text-muted" x-text="job.message || ''"></small>
-                                    </td>
-                                    <td x-text="job.platform"></td>
-                                    <td x-text="job.crawler_type"></td>
-                                    <td><span class="badge" :class="jobStatusClass(job.status)" x-text="job.status"></span></td>
-                                    <td>
-                                        <button class="btn btn-sm btn-outline-danger btn-icon" @click.stop="stopJob(job.job_id)" :disabled="!isJobRunning(job)" title="停止">
-                                            <i class="bi bi-stop-circle"></i>
-                                        </button>
-                                        <button class="btn btn-sm btn-outline-secondary btn-icon ms-1" @click.stop="deleteJob(job.job_id)" :disabled="isJobRunning(job)" title="删除">
-                                            <i class="bi bi-trash"></i>
-                                        </button>
-                                    </td>
-                                </tr>
-                            </template>
-                            <tr x-show="jobs.length === 0">
-                                <td colspan="5" class="text-center text-muted py-4">暂无任务</td>
-                            </tr>
-                        </tbody>
-                    </table>
-                </div>
-            </div>
-
-            <div class="row g-3">
-                <div class="col-xl-8">
-                    <div class="card">
-                        <div class="card-header d-flex align-items-center justify-content-between">
-                            <span><i class="bi bi-terminal me-2"></i>运行日志</span>
-                            <div class="btn-group btn-group-sm">
-                                <button class="btn btn-outline-light" @click="copyLogs" :disabled="logs.length === 0" title="复制日志">
-                                    <i class="bi bi-clipboard me-1"></i>复制
-                                </button>
-                                <button class="btn btn-outline-light" @click="downloadLogs" :disabled="logs.length === 0" title="下载日志">
-                                    <i class="bi bi-download me-1"></i>下载
-                                </button>
-                                <button class="btn btn-outline-secondary" @click="logs = []">清空</button>
-                            </div>
-                        </div>
-                        <div class="log-pane" id="logConsole">
-                            <template x-for="(log, index) in logs" :key="index">
-                                <div class="log-line" :class="logClass(log)" x-text="log"></div>
-                            </template>
-                            <div x-show="logs.length === 0" class="text-muted p-3">暂无日志</div>
-                        </div>
-                    </div>
-                </div>
-                <div class="col-xl-4">
-                    <div class="card mb-3">
-                        <div class="card-header">快捷操作</div>
-                        <div class="card-body d-grid gap-2">
-                            <button class="btn btn-primary btn-lg" @click="runCrawler">
-                                <i class="bi bi-play-circle-fill me-1"></i>开始爬取
-                            </button>
-                            <button class="btn btn-outline-light" @click="scrollToCrawlerSettings">
-                                <i class="bi bi-sliders me-1"></i>修改配置
-                            </button>
-                        </div>
-                    </div>
-                    <div class="card">
-                        <div class="card-header">系统状态</div>
-                        <div class="card-body">
-                            <p class="mb-2">平台：<span x-text="config.platform"></span></p>
-                            <p class="mb-0">配置项目：<span x-text="configForm.items.length"></span></p>
-                        </div>
-                    </div>
-                </div>
-            </div>
-        </section>
-
-        <section x-show="currentTab === 'dashboard'" id="crawlerSettings">
-            <h3 class="mb-3">参数配置</h3>
-            <form @submit.prevent="runCrawler">
-                <div class="row g-3">
-                    <div class="col-xl-6">
-                        <div class="card">
-                            <div class="card-header">基础配置</div>
-                            <div class="card-body">
-                                <div class="mb-3">
-                                    <label class="form-label">媒体平台</label>
-                                    <select class="form-select" x-model="config.platform" @change="onPlatformChange">
-                                        <option value="dy">抖音 (dy)</option>
-                                        <option value="xhs">小红书 (xhs)</option>
-                                        <option value="ks">快手 (ks)</option>
-                                        <option value="bili">哔哩哔哩 (bili)</option>
-                                        <option value="wb">微博 (wb)</option>
-                                        <option value="tieba">百度贴吧 (tieba)</option>
-                                        <option value="zhihu">知乎 (zhihu)</option>
-                                        <option value="hg">红果短剧 (hg)</option>
-                                    </select>
-                                </div>
-                                <div class="mb-3">
-                                    <label class="form-label">爬取类型</label>
-                                    <select class="form-select" x-model="config.crawler_type">
-                                        <option value="search">搜索 (search)</option>
-                                        <option value="detail">详情 (detail)</option>
-                                        <option value="creator">创作者 (creator)</option>
-                                        <option value="rank" :disabled="!['dy', 'hg'].includes(config.platform)">榜单 (rank)</option>
-                                    </select>
-                                </div>
-                                <div class="mb-3">
-                                    <label class="form-label">登录方式</label>
-                                    <select class="form-select" x-model="config.login_type" @change="onLoginTypeChange">
-                                        <option value="qrcode">二维码 (qrcode)</option>
-                                        <option value="phone">手机号 (phone)</option>
-                                        <option value="cookie">Cookie</option>
-                                    </select>
-                                </div>
-                                <div x-show="config.login_type === 'qrcode'" class="mb-3 p-3 border border-secondary rounded">
-                                    <div class="d-flex gap-2 align-items-center flex-wrap">
-                                        <button type="button" class="btn btn-primary btn-sm" @click="startQrcodeLogin" :disabled="isRunning || qrcodeLoggingIn">
-                                            <i class="bi bi-camera me-1"></i>扫码登录
-                                        </button>
-                                        <span x-show="qrcodeLoggingIn" class="spinner-border spinner-border-sm"></span>
-                                        <span class="badge" :class="qrcodeStatus.has_state ? 'bg-success' : 'bg-secondary'" x-text="qrcodeStatus.message || '未登录'"></span>
-                                        <button type="button" x-show="qrcodeStatus.has_state" class="btn btn-outline-danger btn-sm" @click="clearQrcodeLogin">
-                                            <i class="bi bi-trash"></i>
-                                        </button>
-                                    </div>
-                                </div>
-                                <div class="mb-3">
-                                    <label class="form-label">Cookie</label>
-                                    <input type="text" class="form-control" x-model="config.cookie" placeholder="可选">
-                                </div>
-                            </div>
-                        </div>
-
-                        <div class="card mt-3">
-                            <div class="card-header">运行配置</div>
-                            <div class="card-body">
-                                <div class="row g-3">
-                                    <div class="col-md-6">
-                                        <label class="form-label">起始页</label>
-                                        <input type="number" class="form-control" x-model.number="config.start_page" min="1">
-                                    </div>
-                                    <div class="col-md-6">
-                                        <label class="form-label">保存方式</label>
-                                        <select class="form-select" x-model="config.save_option">
-                                            <option value="json">JSON</option>
-                                            <option value="csv">CSV</option>
-                                            <option value="sqlite">SQLite</option>
-                                            <option value="db">MySQL</option>
-                                            <option value="mongodb">MongoDB</option>
-                                            <option value="excel">Excel</option>
-                                        </select>
-                                    </div>
-                                </div>
-                                <div class="form-check form-switch mt-3">
-                                    <input class="form-check-input" type="checkbox" x-model="config.headless" id="headlessSwitch">
-                                    <label class="form-check-label" for="headlessSwitch">无头模式</label>
-                                </div>
-                                <div class="form-check form-switch mt-2">
-                                    <input class="form-check-input" type="checkbox" x-model="config.get_comment" id="commentSwitch">
-                                    <label class="form-check-label" for="commentSwitch">爬取一级评论</label>
-                                </div>
-                                <div class="form-check form-switch mt-2">
-                                    <input class="form-check-input" type="checkbox" x-model="config.get_sub_comment" id="subCommentSwitch">
-                                    <label class="form-check-label" for="subCommentSwitch">爬取二级评论</label>
-                                </div>
-                                <div class="form-check form-switch mt-2">
-                                    <input class="form-check-input" type="checkbox" x-model="config.autoRun" id="autoRunSwitch">
-                                    <label class="form-check-label" for="autoRunSwitch">自动托管</label>
-                                </div>
-                            </div>
-                        </div>
-                    </div>
-
-                    <div class="col-xl-6">
-                        <div class="card" x-show="config.platform === 'dy'">
-                            <div class="card-header">抖音专用配置</div>
-                            <div class="card-body">
-                                <div class="mb-3">
-                                    <label class="form-label">搜索频道</label>
-                                    <select class="form-select" x-model="config.dy_channel">
-                                        <option value="general">综合</option>
-                                        <option value="video">视频</option>
-                                        <option value="user">用户</option>
-                                        <option value="live">直播</option>
-                                    </select>
-                                </div>
-                                <div class="row g-3">
-                                    <div class="col-md-6">
-                                        <label class="form-label">排序</label>
-                                        <select class="form-select" x-model="config.dy_sort">
-                                            <option value="latest">最新发布</option>
-                                            <option value="most_like">最多点赞</option>
-                                            <option value="general">综合排序</option>
-                                        </select>
-                                    </div>
-                                    <div class="col-md-6">
-                                        <label class="form-label">发布时间</label>
-                                        <select class="form-select" x-model="config.dy_publish_time">
-                                            <option value="unlimited">不限</option>
-                                            <option value="1_day">一天内</option>
-                                            <option value="1_week">一周内</option>
-                                            <option value="6_months">半年内</option>
-                                        </select>
-                                    </div>
-                                </div>
-                                <div class="row g-3 mt-1">
-                                    <div class="col-md-4">
-                                        <label class="form-label">榜单数量</label>
-                                        <input type="number" class="form-control" x-model.number="config.dy_rank_limit" min="1">
-                                    </div>
-                                    <div class="col-md-4">
-                                        <label class="form-label">评论数量</label>
-                                        <input type="number" class="form-control" x-model.number="config.dy_rank_comment_limit" min="1">
-                                    </div>
-                                    <div class="col-md-4">
-                                        <label class="form-label">登录等待秒数</label>
-                                        <input type="number" class="form-control" x-model.number="config.dy_rank_login_wait_seconds" min="0">
-                                    </div>
-                                </div>
-                                <button type="button" class="btn btn-outline-light mt-3" @click="runRankCrawler('dy')">
-                                    <i class="bi bi-trophy me-1"></i>抓取短剧榜单
-                                </button>
-                            </div>
-                        </div>
-
-                        <div class="card mt-3" x-show="config.platform === 'hg'">
-                            <div class="card-header">红果短剧抓取</div>
-                            <div class="card-body">
-                                <div class="form-text mb-2">榜单从本地 CSV 导入：固定读取 E:\hg\exports 最新的 top10_rankings.csv。</div>
-                                <div class="d-flex gap-2 flex-wrap">
-                                    <button type="button" class="btn btn-primary mt-3" @click="runRankCrawler('hg')">
-                                        <i class="bi bi-trophy me-1"></i>抓取红果榜单
-                                    </button>
-                                </div>
-                            </div>
-                        </div>
-
-                    </div>
-                </div>
-            </form>
-        </section>
-
-        <section x-show="currentTab === 'data'" class="data-section">
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <h3 class="mb-0">数据管理</h3>
-                <div class="d-flex gap-2 flex-wrap">
-                    <button class="btn btn-outline-danger btn-sm" x-show="!batchDeleteMode" @click="startBatchDelete">
-                        <i class="bi bi-trash me-1"></i>批量删除
-                    </button>
-                    <button class="btn btn-outline-secondary btn-sm" x-show="batchDeleteMode" @click="cancelBatchDelete" title="退出批量删除">
-                        <i class="bi bi-x-lg"></i><span class="visually-hidden">退出批量删除</span>
-                    </button>
-                    <button class="btn btn-outline-danger btn-sm" x-show="batchDeleteMode && selectedFilePaths.length > 0" @click="deleteSelectedFiles" :title="`删除已选 ${selectedFilePaths.length} 个文件`">
-                        <i class="bi bi-trash me-1"></i><span x-text="`删除 (${selectedFilePaths.length})`"></span>
-                    </button>
-                    <input class="form-control form-control-sm" style="width: 260px;" x-model="fileSearch" @input="applyFileSearch" placeholder="搜索文件名或路径">
-                    <select class="form-select form-select-sm" style="width: 150px;" x-model="fileFilter" @change="fetchFiles">
-                        <option value="all">全部</option>
-                        <option value="内容数据">内容数据</option>
-                        <option value="媒体文件">媒体文件</option>
-                        <option value="任务文件">任务文件</option>
-                        <option value="audio">音频</option>
-                        <option value="video">视频</option>
-                        <option value="json">JSON</option>
-                        <option value="md">Markdown</option>
-                        <option value="剧本与文稿">剧本与文稿</option>
-                        <option value="转写数据">转写数据</option>
-                        <option value="元数据">元数据</option>
-                        <option value="Comments">评论</option>
-                        <option value="Contents">内容</option>
-                    </select>
-                    <button class="btn btn-outline-light btn-sm" @click="fetchFiles">刷新</button>
-                </div>
-            </div>
-            <div class="d-flex justify-content-end" x-show="batchDeleteMode && filteredFiles.length">
-                <label class="form-check small"><input class="form-check-input me-1" type="checkbox" :checked="allVisibleFilesSelected()" @change="toggleAllVisibleFiles($event.target.checked)">全选当前结果</label>
-            </div>
-            <template x-for="group in groupedFiles()" :key="group.drama_name">
-                <section class="data-drama-group">
-                    <div class="data-drama-heading d-flex align-items-center justify-content-between">
-                        <h5 class="mb-0"><i class="bi bi-collection-play me-2"></i><span x-text="group.drama_name"></span></h5>
-                        <span class="badge bg-secondary" x-text="`${group.count} 个文件`"></span>
-                    </div>
-                    <template x-for="typeGroup in group.file_groups" :key="`${group.drama_name}:${typeGroup.name}`">
-                        <div>
-                            <div class="data-type-heading d-flex justify-content-between"><span x-text="typeGroup.name"></span><span x-text="typeGroup.files.length"></span></div>
-                            <div class="table-responsive">
-                                <table class="table table-sm table-hover align-middle mb-0">
-                                    <tbody>
-                                        <template x-for="file in typeGroup.files" :key="file.path">
-                                            <tr>
-                                                <td x-show="batchDeleteMode" style="width: 44px;"><input class="form-check-input" type="checkbox" :checked="isFileSelected(file.path)" @change="toggleFileSelection(file.path, $event.target.checked)" :aria-label="`选择 ${file.name}`"></td>
-                                                <td><div class="fw-semibold"><span x-text="file.name"></span><span class="badge bg-secondary ms-2" x-show="file.episode_label" x-text="file.episode_label"></span></div><small class="text-muted" x-text="file.path"></small></td>
-                                                <td style="width: 110px;" x-text="formatSize(file.size)"></td>
-                                                <td style="width: 180px;" x-text="formatDate(file.modified)"></td>
-                                                <td style="width: 110px;"><span class="badge" :class="file.media_type ? 'bg-primary' : 'bg-success'" x-text="file.file_group"></span></td>
-                                                <td style="width: 220px;">
-                                                    <button class="btn btn-sm btn-outline-primary me-1" x-show="file.preview_kind !== 'none'" @click="openFilePreview(file)" title="在线预览" aria-label="在线预览"><i class="bi bi-eye"></i></button>
-                                                    <button class="btn btn-sm btn-outline-light me-1" @click="downloadFile(file.path)" title="下载" aria-label="下载"><i class="bi bi-download"></i></button>
-                                                    <button class="btn btn-sm btn-outline-info me-1" x-show="file.media_type === 'audio'" @click="analyzeAudio(file)" title="分析音频"><i class="bi bi-soundwave"></i></button>
-                                                    <button class="btn btn-sm btn-outline-warning me-1" x-show="file.legacy_category === 'Contents' && file.type === 'json'" @click="openProjectModal(file.path, 'crawl')">爬评论</button>
-                                                    <button class="btn btn-sm btn-outline-success" x-show="file.legacy_category === 'Comments' && file.type === 'json'" @click="openProjectModal(file.path, 'export')">导出</button>
-                                                    <button class="btn btn-sm btn-outline-danger ms-1" @click="deleteFiles([file.path])" title="删除文件" aria-label="删除文件"><i class="bi bi-trash"></i></button>
-                                                </td>
-                                            </tr>
-                                        </template>
-                                    </tbody>
-                                </table>
-                            </div>
-                        </div>
-                    </template>
-                </section>
-            </template>
-            <div x-show="filteredFiles.length === 0" class="text-center text-muted py-5">暂无文件</div>
-        </section>
-
-        <section x-show="currentTab === 'drama-analysis'">
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <h3 class="mb-0">剧目检索下载</h3>
-                <button class="btn btn-outline-secondary btn-sm" @click="fetchDramaSettings">
-                    <i class="bi bi-arrow-clockwise me-1"></i>刷新设置
-                </button>
-            </div>
-            <div class="row g-3">
-                <div class="col-xl-4">
-                    <div class="card">
-                        <div class="card-header">接口设置</div>
-                        <div class="card-body">
-                            <label class="form-label">配置中心地址</label>
-                            <input class="form-control mb-3" x-model="dramaAnalysis.settings.config_url">
-                            <label class="form-label">激活码</label>
-                            <input class="form-control mb-2" type="password" x-model="dramaAnalysis.activationCode" placeholder="留空则保留已保存的激活码">
-                            <div class="small text-muted mb-3" x-text="dramaAnalysis.settings.has_activation_code ? '已保存激活码' : '未保存激活码'"></div>
-                            <div class="d-flex gap-2">
-                                <button class="btn btn-outline-info flex-fill" @click="testDramaInterface" :disabled="dramaAnalysis.loading">
-                                    <i class="bi bi-plug me-1"></i>测试接口
-                                </button>
-                                <button class="btn btn-outline-light flex-fill" @click="saveDramaSettings" :disabled="dramaAnalysis.loading">
-                                    <i class="bi bi-save me-1"></i>保存设置
-                                </button>
-                            </div>
-                            <div class="alert mt-3 mb-0 py-2" x-show="dramaAnalysis.interfaceTest" :class="dramaAnalysis.interfaceTest?.status === 'success' ? 'alert-success' : 'alert-danger'" x-text="dramaAnalysis.interfaceTest?.message"></div>
-                        </div>
-                    </div>
-                </div>
-                <div class="col-xl-8">
-                    <div class="card mb-3">
-                        <div class="card-header">检索并选择剧集</div>
-                        <div class="card-body">
-                            <div class="position-relative mb-3">
-                                <div class="input-group">
-                                    <input class="form-control" x-model="dramaAnalysis.title" @input="onDramaTitleInput" @keydown.enter="searchDrama" autocomplete="off" placeholder="输入剧名，自动联想红果剧目">
-                                    <button class="btn btn-primary" @click="searchDrama" :disabled="dramaAnalysis.loading || !dramaAnalysis.title.trim()">
-                                        <i class="bi bi-search me-1"></i>搜索
-                                    </button>
-                                </div>
-                                <div class="list-group position-absolute w-100 shadow" style="top: calc(100% + 2px); z-index: 20;" x-show="dramaAnalysis.suggestionLoading || dramaAnalysis.suggestions.length" @click.outside="dramaAnalysis.suggestions = []">
-                                    <div class="list-group-item bg-dark text-muted border-secondary py-2" x-show="dramaAnalysis.suggestionLoading">
-                                        <i class="bi bi-search me-1"></i>正在联想剧目…
-                                    </div>
-                                    <template x-for="suggestion in dramaAnalysis.suggestions" :key="suggestion.book_id">
-                                        <button type="button" class="list-group-item list-group-item-action bg-dark text-light border-secondary text-start" @click="chooseDramaSuggestion(suggestion)">
-                                            <span class="fw-semibold" x-text="suggestion.title || '未命名剧目'"></span>
-                                            <span class="small text-muted ms-2" x-text="`${suggestion.episode_count || '-'} 集 · ${suggestion.summary || '暂无简介'}`"></span>
-                                        </button>
-                                    </template>
-                                </div>
-                            </div>
-                            <div class="alert alert-danger" x-show="dramaAnalysis.error" x-text="dramaAnalysis.error"></div>
-                            <div class="alert alert-warning" x-show="dramaAnalysis.searchSource === 'douyin_fallback'">
-                                <div class="fw-semibold">红果检索已降级至抖音</div>
-                                <div class="small" x-text="dramaAnalysis.fallbackReason"></div>
-                                <div class="small mt-1">请手动确认视频。候选可能是推广或片段，并非完整剧集。</div>
-                            </div>
-                            <div class="list-group mb-3" x-show="dramaAnalysis.candidates.length">
-                                <template x-for="candidate in dramaAnalysis.candidates" :key="candidate.book_id || candidate.aweme_id">
-                                    <button type="button" class="list-group-item list-group-item-action bg-transparent text-light border-secondary" :class="{active: (candidate.book_id && dramaAnalysis.selected?.book_id === candidate.book_id) || (candidate.aweme_id && dramaAnalysis.selected?.aweme_id === candidate.aweme_id)}" @click="selectDramaCandidate(candidate)" :disabled="dramaAnalysis.loading">
-                                        <div class="d-flex gap-3 align-items-center">
-                                            <img x-show="candidate.cover" :src="candidate.cover" alt="" style="width: 42px; height: 56px; object-fit: cover;">
-                                            <div class="text-start">
-                                                <div class="fw-semibold" x-text="candidate.title || '未命名剧目'"></div>
-                                                <div class="small text-muted" x-show="dramaAnalysis.searchSource !== 'douyin_fallback'" x-text="`${candidate.episode_count || '-'} 集 · ${candidate.summary || '暂无简介'}`"></div>
-                                                <div class="small text-muted" x-show="dramaAnalysis.searchSource === 'douyin_fallback'" x-text="`${candidate.author || '未知作者'} · 时长 ${formatDramaDuration(candidate.duration)} · 点赞 ${candidate.like_count || 0} · 评论 ${candidate.comment_count || 0}`"></div>
-                                            </div>
-                                        </div>
-                                    </button>
-                                </template>
-                            </div>
-                            <div x-show="dramaAnalysis.selected" class="border border-secondary p-3">
-                                <div class="fw-semibold mb-3" x-text="dramaAnalysis.selected?.title"></div>
-                                <div x-show="dramaAnalysis.searchSource !== 'douyin_fallback'" class="row g-3 align-items-end">
-                                    <div class="col-md-8">
-                                        <label class="form-label">集数</label>
-                                        <select class="form-select" x-model.number="dramaAnalysis.episode">
-                                            <template x-for="episode in dramaAnalysis.episodes" :key="episode.episode">
-                                                <option :value="episode.episode" x-text="`${episode.episode}. ${episode.title}`"></option>
-                                            </template>
-                                        </select>
-                                    </div>
-                                    <div class="col-md-4 d-grid">
-                                        <button class="btn btn-success" @click="startDramaAnalysis" :disabled="dramaAnalysis.loading || !dramaAnalysis.episode">
-                                            <i class="bi bi-download me-1"></i>下载剧集
-                                        </button>
-                                    </div>
-                                </div>
-                                <div x-show="dramaAnalysis.searchSource === 'douyin_fallback'">
-                                    <div class="alert alert-warning py-2">
-                                        <span x-text="`来源：抖音 · 作者：${dramaAnalysis.selected?.author || '未知作者'} · 视频：${dramaAnalysis.selected?.title || ''}`"></span>
-                                        <div class="small mt-1">该视频可能是推广或片段，非完整剧集；确认后仅下载这一个视频。</div>
-                                    </div>
-                                    <button class="btn btn-success" @click="startDramaAnalysis" :disabled="dramaAnalysis.loading || !dramaAnalysis.selected?.aweme_id">
-                                        <i class="bi bi-download me-1"></i>下载抖音视频并解析
-                                    </button>
-                                </div>
-                            </div>
-                        </div>
-                    </div>
-                    <div class="card" x-show="dramaAnalysis.jobId">
-                        <div class="card-header">下载产物</div>
-                        <div class="card-body">
-                            <div class="mb-2">任务：<span x-text="dramaAnalysis.jobId"></span></div>
-                            <div class="d-flex gap-2 flex-wrap">
-                                <template x-for="item in dramaJobArtifacts()" :key="item.path">
-                                    <button class="btn btn-sm btn-outline-light" @click="downloadFile(item.path)" x-text="item.path.replace(/\\/g, '/').split('/').pop()"></button>
-                                </template>
-                            </div>
-                        </div>
-                    </div>
-                </div>
-            </div>
-        </section>
-
-        <template x-if="false"><section>
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <h3 class="mb-0">数据资产</h3>
-                <button class="btn btn-outline-secondary btn-sm" @click="fetchAnalyticsAssets">
-                    <i class="bi bi-arrow-clockwise me-1"></i>刷新
-                </button>
-            </div>
-
-            <div class="row g-3 mb-3">
-                <div class="col-md-2"><div class="card metric-card"><div class="card-body"><div class="text-muted">任务数</div><div class="metric-value" x-text="analyticsAssets.overview?.task_count || 0"></div></div></div></div>
-                <div class="col-md-2"><div class="card metric-card"><div class="card-body"><div class="text-muted">剧目数</div><div class="metric-value" x-text="analyticsAssets.overview?.drama_count || 0"></div></div></div></div>
-                <div class="col-md-2"><div class="card metric-card"><div class="card-body"><div class="text-muted">评论数</div><div class="metric-value" x-text="analyticsAssets.overview?.comment_count || 0"></div></div></div></div>
-                <div class="col-md-2"><div class="card metric-card"><div class="card-body"><div class="text-muted">转写数</div><div class="metric-value" x-text="analyticsAssets.overview?.transcript_count || 0"></div></div></div></div>
-                <div class="col-md-2"><div class="card metric-card"><div class="card-body"><div class="text-muted">已索引任务</div><div class="metric-value" x-text="analyticsAssets.overview?.indexed_jobs || 0"></div></div></div></div>
-                <div class="col-md-2"><div class="card metric-card"><div class="card-body"><div class="text-muted">待索引任务</div><div class="metric-value" x-text="analyticsAssets.overview?.unindexed_jobs || 0"></div></div></div></div>
-            </div>
-            <div class="row g-3">
-                <div class="col-lg-4">
-                    <div class="card h-100">
-                        <div class="card-header">平台资产</div>
-                        <div class="table-responsive">
-                            <table class="table table-sm table-hover align-middle mb-0">
-                                <thead><tr><th>平台</th><th class="text-end">任务</th><th class="text-end">剧目</th></tr></thead>
-                                <tbody>
-                                    <template x-for="item in analyticsAssets.platform_distribution || []" :key="item.platform">
-                                        <tr><td x-text="item.platform"></td><td class="text-end" x-text="item.jobs || 0"></td><td class="text-end" x-text="item.dramas || 0"></td></tr>
-                                    </template>
-                                    <tr x-show="!(analyticsAssets.platform_distribution || []).length"><td colspan="3" class="text-center text-muted py-4">暂无资产数据</td></tr>
-                                </tbody>
-                            </table>
-                        </div>
-                    </div>
-                </div>
-                <div class="col-lg-8">
-                    <div class="card">
-                        <div class="card-header">任务资产</div>
-                        <div class="table-responsive scroll-pane data-table-pane">
-                            <table class="table table-sm table-hover align-middle mb-0">
-                                <thead><tr><th>任务</th><th>平台</th><th class="text-end">产物</th><th class="text-end">剧目</th><th class="text-end">评论</th><th class="text-end">转写</th><th>索引</th><th>更新时间</th></tr></thead>
-                                <tbody>
-                                    <template x-for="job in analyticsAssets.jobs || []" :key="`${job.platform}-${job.job_id}`">
-                                        <tr>
-                                            <td class="text-nowrap" x-text="job.job_id"></td><td x-text="job.platform"></td>
-                                            <td class="text-end" x-text="job.artifact_count || 0"></td><td class="text-end" x-text="job.drama_count || 0"></td>
-                                            <td class="text-end" x-text="job.comment_count || 0"></td><td class="text-end" x-text="job.transcript_count || 0"></td>
-                                            <td><span class="badge" :class="job.indexed ? 'bg-success' : 'bg-secondary'" x-text="job.indexed ? '已索引' : '待索引'"></span></td>
-                                            <td class="text-nowrap" x-text="formatDate(job.synced_at)"></td>
-                                        </tr>
-                                    </template>
-                                    <tr x-show="!(analyticsAssets.jobs || []).length"><td colspan="8" class="text-center text-muted py-4">暂无任务资产</td></tr>
-                                </tbody>
-                            </table>
-                        </div>
-                    </div>
-                </div>
-            </div>
-        </section></template>
-
-        <section x-show="currentTab === 'creative'">
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <h3 class="mb-0">今日爆款</h3>
-                <div class="d-flex gap-2">
-                    <select class="form-select form-select-sm" style="width: 110px;" x-model="creative.platform" @change="generateCreativeTopics">
-                        <option value="dy">抖音</option><option value="hg">红果</option>
-                    </select>
-                    <button class="btn btn-outline-light btn-sm" @click="currentTab = 'topic-library'; fetchTopicLibrary()" title="打开选题库"><i class="bi bi-journal-bookmark me-1"></i>选题库</button>
-                    <button class="btn btn-primary btn-sm" @click="generateCreativeTopics" :disabled="creative.loading">
-                        <i class="bi bi-lightning-charge me-1"></i>生成 Top10
-                    </button>
-                </div>
-            </div>
-            <div class="alert alert-danger" x-show="creative.error" x-text="creative.error"></div>
-            <div class="creative-topic-table">
-                <table class="table table-sm table-hover align-middle mb-0">
-                    <thead><tr><th>剧目</th><th>平台</th><th>题材</th><th>推荐分 <button type="button" class="btn btn-link btn-sm p-0 text-muted score-help" data-bs-toggle="tooltip" data-bs-placement="top" title="由热度、增长、新鲜度和平台适配加权计算；数据缺失时会动态归一化" aria-label="推荐分计算说明"><i class="bi bi-info-circle"></i></button></th><th>状态</th><th style="width: 270px;">操作</th></tr></thead>
-                    <tbody>
-                        <template x-for="topic in creative.items" :key="topic.id">
-                            <tr>
-                                <td><div class="fw-semibold" x-text="topic.title"></div><small class="text-muted" x-text="topic.recommendation"></small></td>
-                                <td x-text="topic.platform"></td><td x-text="topic.category || '-' "></td>
-                                <td x-text="topic.score.toFixed(1)"></td>
-                                <td>
-                                    <span class="badge" :class="topic.status === 'adopted' ? 'bg-success' : 'bg-secondary'" x-text="topic.status"></span>
-                                    <span class="badge d-block mt-1" :class="breakdownStatusClass(topic)" x-text="breakdownStatusLabel(topic)"></span>
-                                </td>
-                                <td>
-                                    <button class="btn btn-sm btn-outline-success me-1" x-show="!topic.latest_breakdown_id && topic.status === 'pending_review'" @click="updateCreativeTopic(topic, 'adopted')">采纳</button>
-                                    <button class="btn btn-sm btn-outline-light me-1" x-show="!topic.latest_breakdown_id && ['pending_review', 'adopted'].includes(topic.status)" @click="createCreativeBreakdown(topic)">拆解</button>
-                                    <button class="btn btn-sm btn-outline-secondary" x-show="!topic.latest_breakdown_id && topic.status === 'pending_review'" @click="updateCreativeTopic(topic, 'rejected')">不跟进</button>
-                                    <button class="btn btn-sm btn-outline-success" x-show="!topic.latest_breakdown_id && topic.status === 'rejected'" @click="updateCreativeTopic(topic, 'adopted')"><i class="bi bi-arrow-counterclockwise me-1"></i>重新采纳</button>
-                                    <button class="btn btn-sm btn-outline-light me-1" x-show="topic.latest_breakdown_id && topic.latest_breakdown_status === 'draft' && topic.status !== 'archived'" @click="createCreativeBreakdown(topic)">继续拆解</button>
-                                    <button class="btn btn-sm btn-outline-light me-1" x-show="topic.latest_breakdown_id && topic.latest_breakdown_status !== 'draft' && topic.status !== 'archived'" @click="createCreativeBreakdown(topic)">再次拆解</button>
-                                    <button class="btn btn-sm btn-outline-warning" x-show="topic.latest_breakdown_id && topic.status !== 'archived'" @click="updateCreativeTopic(topic, 'archived')">归档</button>
-                                </td>
-                            </tr>
-                        </template>
-                        <tr x-show="!creative.loading && creative.items.length === 0"><td colspan="6" class="text-center text-muted py-4">暂无候选选题，先生成 Top10</td></tr>
-                    </tbody>
-                </table>
-            </div>
-        </section>
-
-        <section x-show="currentTab === 'topic-library'">
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <div>
-                    <h3 class="mb-1">选题库</h3>
-                    <div class="small text-muted">沉淀榜单候选与人工决策，作为后续创作的唯一选题入口</div>
-                </div>
-                <button class="btn btn-outline-secondary btn-sm" @click="fetchTopicLibrary()" :disabled="topicLibrary.loading">
-                    <i class="bi bi-arrow-clockwise me-1"></i>刷新
-                </button>
-            </div>
-            <div class="card mb-3">
-                <div class="card-body">
-                    <div class="row g-2 align-items-end">
-                        <div class="col-md-2"><label class="form-label small">平台</label><select class="form-select form-select-sm" x-model="topicLibrary.platform"><option value="">全部平台</option><option value="dy">抖音</option><option value="hg">红果</option></select></div>
-                        <div class="col-md-2"><label class="form-label small">状态</label><select class="form-select form-select-sm" x-model="topicLibrary.status"><option value="">全部状态</option><option value="pending_review">待评估</option><option value="adopted">已采纳</option><option value="rejected">不跟进</option><option value="archived">已归档</option></select></div>
-                        <div class="col-md-2"><label class="form-label small">题材</label><input class="form-control form-control-sm" x-model="topicLibrary.category" placeholder="筛选题材"></div>
-                        <div class="col-md-2"><label class="form-label small">最低推荐分</label><input class="form-control form-control-sm" type="number" min="0" max="100" x-model="topicLibrary.minScore" placeholder="0"></div>
-                        <div class="col-md-2"><label class="form-label small">拆解版本</label><select class="form-select form-select-sm" x-model="topicLibrary.hasLocked"><option value="">全部</option><option value="true">已有锁定拆解</option><option value="false">无锁定拆解</option></select></div>
-                        <div class="col-md-2 d-grid"><button class="btn btn-primary btn-sm" @click="fetchTopicLibrary()"><i class="bi bi-funnel me-1"></i>应用筛选</button></div>
-                    </div>
-                </div>
-            </div>
-            <div class="alert alert-danger" x-show="topicLibrary.error" x-text="topicLibrary.error"></div>
-            <div class="d-flex justify-content-between align-items-center mb-2 small text-muted"><span x-text="`共 ${topicLibrary.items.length} 条选题`"></span><span>评分、来源和状态会持续保留</span></div>
-            <div class="creative-topic-table">
-                <table class="table table-sm table-hover align-middle mb-0">
-                    <colgroup>
-                        <col class="col-topic">
-                        <col class="col-desc">
-                        <col class="col-source">
-                        <col class="col-rank">
-                        <col class="col-category">
-                        <col class="col-score">
-                        <col class="col-status">
-                        <col class="col-breakdown">
-                        <col class="col-actions">
-                    </colgroup>
-                    <thead><tr><th>选题</th><th>简介</th><th>来源</th><th>榜单</th><th>题材</th><th>推荐分 <button type="button" class="btn btn-link btn-sm p-0 text-muted score-help" data-bs-toggle="tooltip" data-bs-placement="top" title="由热度、增长、新鲜度和平台适配加权计算；数据缺失时会动态归一化" aria-label="推荐分计算说明"><i class="bi bi-info-circle"></i></button></th><th>状态</th><th>拆解</th><th>操作</th></tr></thead>
-                    <tbody>
-                        <template x-for="topic in topicLibrary.items" :key="topic.id">
-                            <tr>
-                                <td><div class="fw-semibold" x-text="topic.title"></div><small class="text-muted" x-text="topic.recommendation || '暂无推荐理由'"></small></td>
-                                <td class="topic-desc-cell" style="overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;" :title="topic.description">
-                                    <span x-text="topic.description || '暂无简介'"></span>
-                                </td>
-                                <td><span x-text="(topic.source_platforms || [topic.platform]).join('、')"></span><small class="d-block text-muted" x-text="topic.captured_at || '-' "></small></td>
-                                <td x-text="topic.rank_type || '-' "></td>
-                                <td x-text="topic.category || '-' "></td>
-                                <td><span class="fw-semibold" x-text="Number(topic.score || 0).toFixed(1)"></span></td>
-                                <td><span class="badge" :class="topicStatusClass(topic.status)" x-text="topicStatusLabel(topic.status)"></span></td>
-                                <td><span class="badge" :class="breakdownStatusClass(topic)" x-text="breakdownStatusLabel(topic)"></span></td>
-                                <td class="text-nowrap">
-                                    <button class="btn btn-sm btn-outline-light me-1" @click="createTopicLibraryBreakdown(topic)" title="生成拆解" aria-label="生成拆解"><i class="bi bi-diagram-3"></i></button>
-                                    <button class="btn btn-sm btn-outline-primary me-1" @click="openTopicDownload(topic)" title="下载剧集" aria-label="下载剧集"><i class="bi bi-download"></i></button>
-                                    <button class="btn btn-sm btn-outline-info me-1" x-show="topic.status === 'adopted' && topic.locked_breakdown_id" @click="openCreativeProjectFromTopic(topic)" title="创建创作项目" aria-label="创建创作项目"><i class="bi bi-pencil-square"></i></button>
-                                    <button class="btn btn-sm btn-outline-success me-1" x-show="topic.status !== 'adopted'" @click="updateTopicLibrary(topic, 'adopted')" title="采纳" aria-label="采纳"><i class="bi bi-check2"></i></button>
-                                    <button class="btn btn-sm btn-outline-warning" x-show="topic.status === 'adopted'" @click="updateTopicLibrary(topic, 'archived')" title="归档" aria-label="归档"><i class="bi bi-archive"></i></button>
-                                </td>
-                            </tr>
-                        </template>
-                        <tr x-show="!topicLibrary.loading && topicLibrary.items.length === 0"><td colspan="9" class="text-center text-muted py-5">暂无符合条件的选题，请先在“今日爆款”生成候选</td></tr>
-                    </tbody>
-                </table>
-            </div>
-        </section>
-
-        <section class="creative-project-workspace" x-show="currentTab === 'creative-projects'">
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <div>
-                    <h3 class="mb-1">创作项目</h3>
-                    <div class="small text-muted">从锁定拆解开始，分步完成仿制方向、Brief、人物关系、分集大纲和单集 Beats</div>
-                </div>
-                <button class="btn btn-outline-secondary btn-sm" @click="fetchCreativeProjects()" :disabled="creativeWorkspace.loading" title="刷新项目"><i class="bi bi-arrow-clockwise"></i></button>
-            </div>
-            <div class="alert alert-danger" x-show="creativeWorkspace.error" x-text="creativeWorkspace.error"></div>
-            <div class="creative-save-toast" x-show="creativeWorkspace.notice" x-transition.opacity x-cloak :class="creativeWorkspace.noticeType === 'error' ? 'border-danger' : 'border-success'">
-                <i class="bi" :class="creativeWorkspace.noticeType === 'error' ? 'bi-exclamation-circle text-danger' : 'bi-check-circle text-success'"></i>
-                <span x-text="creativeWorkspace.notice"></span>
-            </div>
-
-            <div class="border-top border-bottom py-3 mb-3">
-                <div class="row g-2 align-items-end">
-                    <div class="col-lg-3"><label class="form-label small">已采纳选题</label><select class="form-select form-select-sm" x-model.number="creativeWorkspace.create.topic_id" @change="syncCreativeProjectSource()"><option value="">选择已采纳选题</option><template x-for="topic in creativeWorkspace.eligibleTopics" :key="topic.id"><option :value="topic.id" :disabled="!topic.locked_breakdown_id" x-text="creativeTopicOptionLabel(topic)"></option></template></select></div>
-                    <div class="col-lg-2"><label class="form-label small">拆解版本</label><select class="form-select form-select-sm" x-model.number="creativeWorkspace.create.breakdown_id" :disabled="!creativeWorkspace.create.topic_id"><option value="">选择拆解版本</option><template x-for="breakdown in creativeWorkspace.lockedBreakdowns" :key="breakdown.id"><option :value="breakdown.id" x-text="`v${breakdown.version} · 已锁定`"></option></template></select></div>
-                    <div class="col-lg-3"><label class="form-label small">项目名称</label><input class="form-control form-control-sm" x-model="creativeWorkspace.create.name" placeholder="例如：都市女性成长方向"></div>
-                    <div class="col-lg-1"><label class="form-label small">目标平台</label><select class="form-select form-select-sm" x-model="creativeWorkspace.create.target_platform"><option value="">待定</option><option value="dy">抖音</option><option value="hg">红果</option></select></div>
-                    <div class="col-lg-1"><label class="form-label small">目标集数</label><input class="form-control form-control-sm" type="number" min="1" max="100" x-model.number="creativeWorkspace.create.target_episode_count"></div>
-                    <div class="col-lg-2 d-grid"><button class="btn btn-primary btn-sm" @click="createCreativeProject" :disabled="!creativeWorkspace.create.topic_id || !creativeWorkspace.create.breakdown_id"><i class="bi bi-plus-lg me-1"></i>创建项目</button></div>
-                </div>
-            </div>
-
-            <table class="table table-sm align-middle mb-4">
-                <thead><tr><th>项目</th><th>来源选题</th><th>目标集数</th><th>当前阶段</th><th>更新时间</th></tr></thead>
-                <tbody>
-                    <template x-for="project in creativeWorkspace.projects" :key="project.id"><tr class="creative-project-row" :class="{'table-active': creativeWorkspace.project?.id === project.id}" @click="selectCreativeProject(project.id)"><td><button type="button" class="project-name-button" @click.stop="selectCreativeProject(project.id)" :aria-current="creativeWorkspace.project?.id === project.id ? 'page' : null" x-text="project.name"></button></td><td x-text="project.topic_title"></td><td x-text="project.target_episode_count"></td><td><span class="badge bg-secondary" x-text="creativeProjectStatusLabel(project.status)"></span></td><td x-text="formatDate(project.updated_at)"></td></tr></template>
-                    <tr x-show="!creativeWorkspace.projects.length"><td colspan="5" class="text-center text-muted py-4">暂无创作项目，请先从已采纳且已锁定拆解的选题创建</td></tr>
-                </tbody>
-            </table>
-
-            <template x-if="creativeWorkspace.project"><div>
-                <div class="d-flex flex-wrap justify-content-between align-items-center border-bottom pb-3 mb-3">
-                    <div><h4 class="mb-1" x-text="creativeWorkspace.project.name"></h4><span class="small text-muted" x-text="`${creativeWorkspace.project.topic_title} · ${creativeWorkspace.project.target_episode_count} 集 · 拆解 v${creativeWorkspace.project.breakdown_version}`"></span></div>
-                    <div class="btn-group btn-group-sm" role="group" aria-label="创作步骤">
-                        <button class="btn" :class="creativeWorkspace.step === 'blueprints' ? 'btn-primary' : 'btn-outline-secondary'" @click="creativeWorkspace.step = 'blueprints'">仿制方向</button>
-                        <button class="btn" :class="creativeWorkspace.step === 'characters' ? 'btn-primary' : 'btn-outline-secondary'" @click="creativeWorkspace.step = 'characters'" :disabled="!creativeWorkspace.project.locked_brief_id">人物关系</button>
-                        <button class="btn" :class="creativeWorkspace.step === 'outlines' ? 'btn-primary' : 'btn-outline-secondary'" @click="creativeWorkspace.step = 'outlines'" :disabled="!creativeWorkspace.project.character_batch_id">分集大纲</button>
-                        <button class="btn" :class="creativeWorkspace.step === 'beats' ? 'btn-primary' : 'btn-outline-secondary'" @click="creativeWorkspace.step = 'beats'" :disabled="!creativeWorkspace.project.outline_batch_id">单集 Beats</button>
-                    </div>
-                </div>
-
-                <div class="border-bottom pb-3 mb-3" x-show="creativeWorkspace.startingAction || creativeWorkspace.jobId" x-cloak role="status" aria-live="polite">
-                    <div class="d-flex justify-content-between align-items-center mb-1"><span class="small fw-semibold" x-text="creativeWorkspace.jobMessage || '后台生成中'"></span><span class="small text-muted" x-text="creativeProgressText()"></span></div>
-                    <div class="small text-muted mb-2" x-text="creativeProgressElapsedText()"></div>
-                    <div class="progress mb-2" style="height:8px" role="progressbar" :aria-valuenow="creativeProgressIndeterminate() ? null : creativeProgressPercent()"><div class="progress-bar" :class="{'progress-bar-striped progress-bar-animated': creativeProgressIndeterminate(), 'bg-danger': creativeWorkspace.jobStatus === 'failed'}" :style="`width:${creativeProgressIndeterminate() ? 100 : creativeProgressPercent()}%`"></div></div>
-                    <div class="d-flex gap-2">
-                        <button class="btn btn-sm btn-outline-warning" x-show="!creativeWorkspace.paused && creativeJobRunning()" @click="pauseCreativeJob"><i class="bi bi-pause-fill me-1"></i>暂停</button>
-                        <button class="btn btn-sm btn-outline-success" x-show="creativeWorkspace.paused && creativeJobRunning()" @click="resumeCreativeJob"><i class="bi bi-play-fill me-1"></i>继续</button>
-                        <button class="btn btn-sm btn-outline-danger" x-show="creativeJobRunning()" @click="cancelCreativeJob"><i class="bi bi-stop-fill me-1"></i>取消</button>
-                        <button class="btn btn-sm btn-outline-light" x-show="creativeJobRetryable()" @click="retryCreativeJob"><i class="bi bi-arrow-repeat me-1"></i>重试</button>
-                        <button class="btn btn-sm btn-outline-secondary" @click="currentTab = 'dashboard'; selectJob(creativeWorkspace.jobId)"><i class="bi bi-list-ul me-1"></i>任务日志</button>
-                    </div>
-                </div>
-
-                <div x-show="creativeWorkspace.step === 'blueprints'">
-                    <div class="d-flex justify-content-between align-items-end mb-3"><div><h5 class="mb-1">仿制方向与 Brief</h5><div class="small text-muted">只借鉴结构和爽点，不复制剧情、人设、台词或具体表达</div></div><div class="d-flex gap-2 align-items-end"><div><label class="form-label small">方向数量</label><select class="form-select form-select-sm" x-model.number="creativeWorkspace.directionCount"><option :value="3">3</option><option :value="4">4</option><option :value="5">5</option></select></div><button class="btn btn-primary btn-sm" @click="generateProjectBlueprints" :disabled="creativeActionDisabled('blueprints')"><span class="spinner-border spinner-border-sm me-1" x-show="creativeActionRunning('blueprints')"></span><i class="bi bi-stars me-1" x-show="!creativeActionRunning('blueprints')"></i><span x-text="creativeActionRunning('blueprints') ? (creativeWorkspace.startingAction === 'blueprints' ? '正在启动' : '生成中') : '生成方向'"></span></button></div></div>
-                    <table class="table table-sm table-hover align-middle mb-3"><thead><tr><th>方向</th><th>主角</th><th>核心冲突</th><th>背景</th><th>成本</th><th>风险</th><th>版本</th><th>操作</th></tr></thead><tbody><template x-for="item in latestProjectBlueprints()" :key="item.id"><tr :class="{'table-active': creativeWorkspace.selectedBlueprintId === item.id}"><td class="fw-semibold" x-text="item.content.title || '未命名方向'"></td><td x-text="item.content.protagonist || '-'"></td><td class="blueprint-conflict-cell" style="overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;" :title="item.content.core_conflict || '-'"><span x-text="item.content.core_conflict || '-'"></span></td><td x-text="item.content.background || '-'"></td><td x-text="item.content.cost_level || '-'"></td><td><span class="badge" :class="riskBadgeClass(item.risk_level)" x-text="`${riskLabel(item.risk_level)} ${Number(item.risk_score || 0).toFixed(0)}`"></span></td><td x-text="`v${item.version} · ${item.status === 'brief_locked' ? '已锁定' : '草稿'}`"></td><td><button class="btn btn-sm btn-outline-light" @click="creativeWorkspace.selectedBlueprintId = item.id"><i class="bi bi-pencil"></i></button></td></tr></template><tr x-show="!latestProjectBlueprints().length"><td colspan="8" class="text-center text-muted py-4">尚未生成仿制方向</td></tr></tbody></table>
-                    <template x-if="selectedProjectBlueprint()"><div class="border-top pt-3">
-                        <div class="row g-2 align-items-end border-bottom pb-3 mb-3">
-                            <div class="col-md-3"><label class="form-label small">项目名称</label><input class="form-control form-control-sm" x-model="creativeWorkspace.project.name" :disabled="creativeWorkspace.project.locked_brief_id"></div>
-                            <div class="col-md-2"><label class="form-label small">题材</label><input class="form-control form-control-sm" x-model="creativeWorkspace.project.genre" :disabled="creativeWorkspace.project.locked_brief_id"></div>
-                            <div class="col-md-2"><label class="form-label small">目标平台</label><select class="form-select form-select-sm" x-model="creativeWorkspace.project.target_platform" :disabled="creativeWorkspace.project.locked_brief_id"><option value="">待定</option><option value="dy">抖音</option><option value="hg">红果</option></select></div>
-                            <div class="col-md-2"><label class="form-label small">目标受众</label><input class="form-control form-control-sm" x-model="creativeWorkspace.project.target_audience" :disabled="creativeWorkspace.project.locked_brief_id"></div>
-                            <div class="col-md-1"><label class="form-label small">集数</label><input class="form-control form-control-sm" type="number" min="1" max="100" x-model.number="creativeWorkspace.project.target_episode_count" :disabled="creativeWorkspace.project.locked_brief_id"></div>
-                            <div class="col-md-1"><label class="form-label small">秒/集</label><input class="form-control form-control-sm" type="number" min="30" max="1800" x-model.number="creativeWorkspace.project.episode_duration_seconds" :disabled="creativeWorkspace.project.locked_brief_id"></div>
-                            <div class="col-md-1 d-grid"><button class="btn btn-outline-light btn-sm" @click="saveCreativeProjectSettings" :disabled="creativeWorkspace.project.locked_brief_id" title="保存 Brief 参数"><i class="bi bi-save"></i></button></div>
-                        </div>
-                        <div class="row g-3">
-                            <div class="col-md-4"><label class="form-label small">方向名称</label><input class="form-control form-control-sm" x-model="selectedProjectBlueprint().content.title" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></div>
-                            <div class="col-md-4"><label class="form-label small">新主角</label><input class="form-control form-control-sm" x-model="selectedProjectBlueprint().content.protagonist" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></div>
-                            <div class="col-md-4"><label class="form-label small">新背景</label><input class="form-control form-control-sm" x-model="selectedProjectBlueprint().content.background" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></div>
-                            <div class="col-md-4"><label class="form-label small">核心冲突</label><textarea class="form-control form-control-sm" rows="3" x-model="selectedProjectBlueprint().content.core_conflict" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
-                            <div class="col-md-4"><label class="form-label small">主线目标</label><textarea class="form-control form-control-sm" rows="3" x-model="selectedProjectBlueprint().content.main_goal" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
-                            <div class="col-md-4"><label class="form-label small">世界观</label><textarea class="form-control form-control-sm" rows="3" x-model="selectedProjectBlueprint().content.worldview" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
-                            <div class="col-md-6"><label class="form-label small">借鉴元素</label><textarea class="form-control form-control-sm" rows="3" :value="listText(selectedProjectBlueprint().content.borrowed_elements)" @input="setListField(selectedProjectBlueprint().content, 'borrowed_elements', $event.target.value)" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
-                            <div class="col-md-6"><label class="form-label small">必须差异化元素</label><textarea class="form-control form-control-sm" rows="3" :value="listText(selectedProjectBlueprint().content.different_elements)" @input="setListField(selectedProjectBlueprint().content, 'different_elements', $event.target.value)" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
-                            <div class="col-md-6"><label class="form-label small">前三集钩子</label><textarea class="form-control form-control-sm" rows="3" :value="listText(selectedProjectBlueprint().content.first_three_hooks)" @input="setListField(selectedProjectBlueprint().content, 'first_three_hooks', $event.target.value)" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
-                            <div class="col-md-6"><label class="form-label small">整体爽点</label><textarea class="form-control form-control-sm" rows="3" :value="listText(selectedProjectBlueprint().content.highlights)" @input="setListField(selectedProjectBlueprint().content, 'highlights', $event.target.value)" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
-                            <div class="col-12"><label class="form-label small">差异化修改说明 <span class="text-warning" x-show="selectedProjectBlueprint().risk_level === 'high'">高风险方向锁定前必填</span></label><textarea class="form-control form-control-sm" rows="2" x-model="selectedProjectBlueprint().difference_note" :disabled="selectedProjectBlueprint().status === 'brief_locked'"></textarea></div>
-                        </div>
-                        <div class="d-flex gap-2 mt-3"><button class="btn btn-outline-light btn-sm" @click="saveProjectBlueprint" :disabled="selectedProjectBlueprint().status === 'brief_locked'"><i class="bi bi-save me-1"></i>保存草稿</button><button class="btn btn-warning btn-sm" @click="lockProjectBrief" :disabled="selectedProjectBlueprint().status === 'brief_locked'"><i class="bi bi-lock me-1"></i>锁定 Brief</button><button class="btn btn-outline-info btn-sm" x-show="selectedProjectBlueprint().status === 'brief_locked'" @click="createProjectBlueprintVersion"><i class="bi bi-files me-1"></i>基于此版本新建</button><a class="btn btn-outline-secondary btn-sm" :href="creativeExportUrl('brief', 'md')"><i class="bi bi-download me-1"></i>导出</a></div>
-                    </div></template>
-                </div>
-
-                <div x-show="creativeWorkspace.step === 'characters'">
-                    <div class="d-flex justify-content-between align-items-center mb-3"><div><h5 class="mb-1">人物与关系</h5><div class="small text-muted">生成后可逐字段编辑，整组锁定后才开放分集大纲</div></div><div class="d-flex gap-2"><button class="btn btn-primary btn-sm" @click="generateProjectCharacters" :disabled="creativeActionDisabled('characters')"><span class="spinner-border spinner-border-sm me-1" x-show="creativeActionRunning('characters')"></span><i class="bi bi-people me-1" x-show="!creativeActionRunning('characters')"></i><span x-text="creativeActionRunning('characters') ? (creativeWorkspace.startingAction === 'characters' ? '正在启动' : '生成中') : '生成人物关系'"></span></button><button class="btn btn-outline-light btn-sm" @click="createBlankCharacterBatch"><i class="bi bi-plus-lg me-1"></i>空白版本</button><button class="btn btn-warning btn-sm" @click="lockProjectCharacters" :disabled="!latestCharacterBatchId()"><i class="bi bi-lock me-1"></i>锁定整组</button><a class="btn btn-outline-secondary btn-sm" :href="creativeExportUrl('characters', 'md')"><i class="bi bi-download"></i></a></div></div>
-                    <h6>人物卡</h6><table class="table table-sm align-middle mb-3"><thead><tr><th>角色名</th><th>年龄段</th><th>身份</th><th>内在欲望</th><th>秘密</th><th>成长线</th><th>操作</th></tr></thead><tbody><template x-for="item in currentProjectCharacters()" :key="item.id"><tr><td><input class="form-control form-control-sm" x-model="item.content.name" :disabled="item.status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="item.content.age_range" :disabled="item.status === 'locked'"></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.identity, 24)" x-model="item.content.identity" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.desire, 24)" x-model="item.content.desire" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.secret, 24)" x-model="item.content.secret" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.growth_arc, 24)" x-model="item.content.growth_arc" :disabled="item.status === 'locked'"></textarea></td><td><button class="btn btn-sm btn-outline-light" @click="saveProjectCharacter(item)" :disabled="item.status === 'locked' || creativeWorkspace.saveState['character-' + item.id] === 'saving'" :title="item.status === 'locked' ? '人物版本已锁定' : '保存人物卡'" :aria-label="item.status === 'locked' ? '人物版本已锁定' : '保存人物卡'"><i class="bi bi-save" x-show="creativeWorkspace.saveState['character-' + item.id] !== 'saving' && creativeWorkspace.saveState['character-' + item.id] !== 'saved'"></i><span class="spinner-border spinner-border-sm" x-show="creativeWorkspace.saveState['character-' + item.id] === 'saving'"></span><i class="bi bi-check2 text-success" x-show="creativeWorkspace.saveState['character-' + item.id] === 'saved'"></i></button><small class="creative-save-status" :title="item.updated_at ? `保存时间：${formatDate(item.updated_at)}` : ''" x-text="creativeItemSaveLabel(item, 'character-' + item.id)"></small></td></tr></template><tr x-show="!currentProjectCharacters().length"><td colspan="7" class="text-center text-muted py-4">暂无人物卡</td></tr></tbody></table>
-                    <h6 class="small text-muted">角色表现设置</h6><table class="table table-sm align-middle mb-4"><thead><tr><th>角色名</th><th>外在标签</th><th>台词风格</th><th>造型 Prompt</th><th>声音 Prompt</th><th>操作</th></tr></thead><tbody><template x-for="item in currentProjectCharacters()" :key="`style-${item.id}`"><tr><td class="fw-semibold" x-text="item.content.name"></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(listText(item.content.external_tags), 28)" :value="listText(item.content.external_tags)" @input="setListField(item.content, 'external_tags', $event.target.value)" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.dialogue_style, 28)" x-model="item.content.dialogue_style" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.appearance_prompt, 28)" x-model="item.content.appearance_prompt" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.voice_prompt, 28)" x-model="item.content.voice_prompt" :disabled="item.status === 'locked'"></textarea></td><td><button class="btn btn-sm btn-outline-light" @click="saveProjectCharacter(item)" :disabled="item.status === 'locked' || creativeWorkspace.saveState['character-' + item.id] === 'saving'" :title="item.status === 'locked' ? '人物版本已锁定' : '保存角色表现设置'" :aria-label="item.status === 'locked' ? '人物版本已锁定' : '保存角色表现设置'"><i class="bi bi-save" x-show="creativeWorkspace.saveState['character-' + item.id] !== 'saving' && creativeWorkspace.saveState['character-' + item.id] !== 'saved'"></i><span class="spinner-border spinner-border-sm" x-show="creativeWorkspace.saveState['character-' + item.id] === 'saving'"></span><i class="bi bi-check2 text-success" x-show="creativeWorkspace.saveState['character-' + item.id] === 'saved'"></i></button><small class="creative-save-status" :title="item.updated_at ? `保存时间：${formatDate(item.updated_at)}` : ''" x-text="creativeItemSaveLabel(item, 'character-' + item.id)"></small></td></tr></template></tbody></table>
-                    <h6>人物关系</h6><table class="table table-sm align-middle"><thead><tr><th>角色 A</th><th>角色 B</th><th>关系</th><th>冲突</th><th>变化弧线</th><th>操作</th></tr></thead><tbody><template x-for="item in currentProjectRelationships()" :key="item.id"><tr><td><input class="form-control form-control-sm" x-model="item.content.from" :disabled="item.status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="item.content.to" :disabled="item.status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="item.content.relation" :disabled="item.status === 'locked'"></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.conflict, 28)" x-model="item.content.conflict" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm creative-inline-textarea" :rows="textareaRows(item.content.change_arc, 28)" x-model="item.content.change_arc" :disabled="item.status === 'locked'"></textarea></td><td><button class="btn btn-sm btn-outline-light" @click="saveProjectRelationship(item)" :disabled="item.status === 'locked' || creativeWorkspace.saveState['relationship-' + item.id] === 'saving'" :title="item.status === 'locked' ? '人物版本已锁定' : '保存人物关系'" :aria-label="item.status === 'locked' ? '人物版本已锁定' : '保存人物关系'"><i class="bi bi-save" x-show="creativeWorkspace.saveState['relationship-' + item.id] !== 'saving' && creativeWorkspace.saveState['relationship-' + item.id] !== 'saved'"></i><span class="spinner-border spinner-border-sm" x-show="creativeWorkspace.saveState['relationship-' + item.id] === 'saving'"></span><i class="bi bi-check2 text-success" x-show="creativeWorkspace.saveState['relationship-' + item.id] === 'saved'"></i></button><small class="creative-save-status" :title="item.updated_at ? `保存时间：${formatDate(item.updated_at)}` : ''" x-text="creativeItemSaveLabel(item, 'relationship-' + item.id)"></small></td></tr></template><tr x-show="!currentProjectRelationships().length"><td colspan="6" class="text-center text-muted py-4">暂无人物关系</td></tr></tbody></table>
-                </div>
-
-                <div x-show="creativeWorkspace.step === 'outlines'">
-                    <div class="d-flex justify-content-between align-items-center mb-3"><div><h5 class="mb-1">分集大纲</h5><div class="small text-muted">先规划全剧阶段弧线，再按每 10 集分批生成</div></div><div class="d-flex gap-2"><button class="btn btn-primary btn-sm" @click="generateProjectOutlines" :disabled="creativeActionDisabled('outlines')"><span class="spinner-border spinner-border-sm me-1" x-show="creativeActionRunning('outlines')"></span><i class="bi bi-list-ol me-1" x-show="!creativeActionRunning('outlines')"></i><span x-text="creativeActionRunning('outlines') ? (creativeWorkspace.startingAction === 'outlines' ? '正在启动' : '生成中') : '生成大纲'"></span></button><button class="btn btn-warning btn-sm" @click="lockProjectOutlines" :disabled="!latestOutlineBatchId()"><i class="bi bi-lock me-1"></i>锁定整批</button><a class="btn btn-outline-secondary btn-sm" :href="creativeExportUrl('outline', 'md')"><i class="bi bi-download"></i></a></div></div>
-                    <table class="table table-sm align-middle"><thead><tr><th style="width:55px">集</th><th>核心事件</th><th>冲突</th><th>人物推进</th><th>爽点</th><th>反转</th><th>结尾悬念</th><th style="width:55px">操作</th></tr></thead><tbody><template x-for="item in currentProjectOutlines()" :key="item.id"><tr><td class="fw-semibold" x-text="item.episode_number"></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.core_event" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.conflict" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.character_progress" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.highlight" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.reversal" :disabled="item.status === 'locked'"></textarea></td><td><textarea class="form-control form-control-sm" rows="2" x-model="item.content.cliffhanger" :disabled="item.status === 'locked'"></textarea></td><td><button class="btn btn-sm btn-outline-light" @click="saveProjectEpisode(item)" :disabled="item.status === 'locked'"><i class="bi bi-save"></i></button></td></tr></template><tr x-show="!currentProjectOutlines().length"><td colspan="8" class="text-center text-muted py-4">暂无分集大纲</td></tr></tbody></table>
-                </div>
-
-                <div x-show="creativeWorkspace.step === 'beats'">
-                    <div class="d-flex justify-content-between align-items-end mb-3"><div><h5 class="mb-1">单集 Beats</h5><div class="small text-muted">只生成场景节拍、目标、阻力和转折，不生成完整正文或台词</div></div><div class="d-flex gap-2 align-items-end"><div><label class="form-label small">集数</label><input class="form-control form-control-sm" style="width:90px" type="number" min="1" :max="creativeWorkspace.project.target_episode_count" x-model.number="creativeWorkspace.selectedEpisode"></div><button class="btn btn-primary btn-sm" @click="generateProjectBeats" :disabled="creativeActionDisabled('beats')"><span class="spinner-border spinner-border-sm me-1" x-show="creativeActionRunning('beats')"></span><i class="bi bi-magic me-1" x-show="!creativeActionRunning('beats')"></i><span x-text="creativeActionRunning('beats') ? '生成中' : '生成 Beats'"></span></button><a class="btn btn-outline-secondary btn-sm" :href="creativeExportUrl('beats', 'md')"><i class="bi bi-download"></i></a></div></div>
-                    <div class="beats-generation-status border-top border-bottom py-2 mb-3" x-show="creativeWorkspace.startingAction === 'beats' || creativeWorkspace.activeAction === 'beats'" x-cloak>
-                        <div class="d-flex justify-content-between align-items-center"><span class="small" x-text="creativeWorkspace.jobMessage || '正在创建生成任务'"></span><span class="small text-muted" x-text="creativeProgressText()"></span></div>
-                        <div class="progress mt-2" style="height:6px"><div class="progress-bar" :class="creativeWorkspace.jobStatus === 'failed' ? 'bg-danger' : ''" :style="`width:${creativeProgressPercent()}%`"></div></div>
-                    </div>
-                    <template x-if="selectedProjectBeats()"><div><div class="d-flex justify-content-between align-items-center mb-2"><span class="fw-semibold" x-text="`第 ${creativeWorkspace.selectedEpisode} 集 · v${selectedProjectBeats().version}`"></span><div class="d-flex gap-2"><button class="btn btn-outline-light btn-sm" @click="saveProjectEpisode(selectedProjectBeats())" :disabled="selectedProjectBeats().status === 'locked'"><i class="bi bi-save me-1"></i>保存</button><button class="btn btn-warning btn-sm" @click="lockProjectBeats" :disabled="selectedProjectBeats().status === 'locked'"><i class="bi bi-lock me-1"></i>锁定</button></div></div><table class="table table-sm align-middle"><thead><tr><th style="width:55px">序号</th><th>场景</th><th>人物</th><th>行动目标</th><th>阻力</th><th>情绪变化</th><th>转折</th><th>结尾钩子</th></tr></thead><tbody><template x-for="beat in selectedProjectBeats().content.beats" :key="beat.order"><tr><td x-text="beat.order"></td><td><input class="form-control form-control-sm" x-model="beat.scene" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" :value="listText(beat.characters)" @input="setListField(beat, 'characters', $event.target.value)" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.goal" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.obstacle" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.emotion_change" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.turn" :disabled="selectedProjectBeats().status === 'locked'"></td><td><input class="form-control form-control-sm" x-model="beat.hook" :disabled="selectedProjectBeats().status === 'locked'"></td></tr></template></tbody></table></div></template>
-                    <div class="text-center text-muted py-4" x-show="!selectedProjectBeats()">当前集尚未生成 Beats</div>
-                </div>
-            </div></template>
-        </section>
-
-        <section x-show="currentTab === 'analytics'">
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <h3 class="mb-0">分析看板</h3>
-                <div class="d-flex gap-2">
-                    <select class="form-select form-select-sm" style="width: 130px;" x-model="analyticsPlatform" @change="reloadAnalyticsDashboard">
-                        <option value="">全部平台</option>
-                        <option value="dy">抖音</option>
-                        <option value="hg">红果</option>
-                    </select>
-                    <input class="form-control form-control-sm" style="width: 145px;" type="date" x-model="analyticsDate" @change="reloadAnalyticsDashboard">
-                    <button class="btn btn-outline-light btn-sm" @click="analyticsDate = ''; reloadAnalyticsDashboard()">全部日期</button>
-                    <button class="btn btn-outline-secondary btn-sm" @click="refreshAnalyticsDashboard(true)" :disabled="analyticsLoading">
-                        <i class="bi bi-arrow-clockwise me-1"></i>刷新
-                    </button>
-                    <button class="btn btn-primary btn-sm" @click="syncAllAnalytics" :disabled="analyticsLoading">
-                        <i class="bi bi-database-add me-1"></i>同步全部
-                    </button>
-                </div>
-            </div>
-            <div class="alert alert-danger" x-show="analyticsError" x-text="analyticsError"></div>
-            <div class="alert alert-secondary" x-show="analyticsSyncResult">
-                <span>最近同步：</span><strong x-text="analyticsSyncResult?.job_id || '-'"></strong>
-                <span class="ms-3">剧目：</span><span x-text="analyticsSyncResult?.drama_count || 0"></span>
-                <span class="ms-3">文件：</span><span x-text="analyticsSyncResult?.artifact_count || 0"></span>
-            </div>
-
-            <div class="row g-3 mb-3">
-                <div class="col-md-3">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">剧目数</div><div class="metric-value" x-text="analyticsOverview.drama_count || 0"></div></div></div>
-                </div>
-                <div class="col-md-3">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">评论数</div><div class="metric-value" x-text="analyticsOverview.comment_count || 0"></div></div></div>
-                </div>
-                <div class="col-md-3">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">转写数</div><div class="metric-value" x-text="analyticsOverview.transcript_count || 0"></div></div></div>
-                </div>
-                <div class="col-md-3">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">任务数</div><div class="metric-value" x-text="analyticsOverview.task_count || 0"></div></div></div>
-                </div>
-            </div>
-
-            <div class="row g-3 mb-3">
-                <div class="col-xl-6">
-                    <div class="card"><div class="card-header">题材分布</div><div class="card-body"><div id="categoryChart" class="chart-box"></div></div></div>
-                </div>
-                <div class="col-xl-6">
-                    <div class="card"><div class="card-header">热度趋势</div><div class="card-body"><div id="trendChart" class="chart-box"></div></div></div>
-                </div>
-                <div class="col-xl-6">
-                    <div class="card"><div class="card-header">评论情绪</div><div class="card-body"><div id="sentimentChart" class="chart-box"></div></div></div>
-                </div>
-                <div class="col-xl-6">
-                    <div class="card">
-                        <div class="card-header">高频卖点</div>
-                        <div class="card-body">
-                            <div class="selling-point-list">
-                                <template x-for="item in (analyticsTags.selling_points || []).slice(0, 20)" :key="item.tag">
-                                    <div class="selling-point-item">
-                                        <div class="selling-point-text" x-text="item.tag"></div>
-                                        <span class="badge bg-primary align-self-start" x-text="item.count"></span>
-                                    </div>
-                                </template>
-                                <div class="text-muted text-center py-4" x-show="!(analyticsTags.selling_points || []).length">暂无卖点数据</div>
-                            </div>
-                        </div>
-                    </div>
-                </div>
-            </div>
-
-            <div class="card">
-                <div class="card-header">剧目指标</div>
-                <div class="table-responsive scroll-pane">
-                    <table class="table table-sm table-hover align-middle mb-0">
-                        <thead>
-                            <tr>
-                                <th>剧名</th>
-                                <th style="width: 80px;">平台</th>
-                                <th style="width: 110px;">时间</th>
-                                <th style="width: 90px;">题材</th>
-                                <th style="width: 80px;">排名</th>
-                                <th style="width: 110px;">点赞</th>
-                                <th style="width: 110px;">评论</th>
-                            </tr>
-                        </thead>
-                        <tbody>
-                            <template x-for="item in analyticsDramas" :key="`${item.platform}-${item.job_id}-${item.source_path}-${item.record_id}-${item.title}`">
-                                <tr>
-                                    <td>
-                                        <div class="fw-semibold" x-text="item.title"></div>
-                                        <small class="text-muted" x-text="(item.selling_points || []).slice(0, 2).join(' / ')"></small>
-                                    </td>
-                                    <td x-text="item.platform"></td>
-                                    <td x-text="item.captured_at || '-'"></td>
-                                    <td x-text="item.category || '-'"></td>
-                                    <td x-text="item.rank || '-'"></td>
-                                    <td x-text="item.like_count || 0"></td>
-                                    <td x-text="item.comment_count || 0"></td>
-                                </tr>
-                            </template>
-                            <tr x-show="analyticsDramas.length === 0">
-                                <td colspan="7" class="text-center text-muted py-4">暂无剧目指标</td>
-                            </tr>
-                        </tbody>
-                    </table>
-                </div>
-            </div>
-        </section>
-
-        <section x-show="currentTab === 'today-rank'">
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <div>
-                    <h3 class="mb-1">今日榜单</h3>
-                    <div class="text-muted small">
-                        <span x-show="todayRankData?.selected_snapshot">
-                            当前：<span x-text="todayRankData?.selected_snapshot?.captured_at || '-'"></span>
-                            <span class="ms-2">上一期：<span x-text="todayRankData?.previous_snapshot?.captured_at || '暂无'"></span></span>
-                        </span>
-                        <span x-show="!todayRankData?.selected_snapshot">默认展示本地最新榜单快照</span>
-                    </div>
-                </div>
-                <div class="d-flex gap-2">
-                    <div class="btn-group btn-group-sm rank-platform-toggle me-2" role="group">
-                        <button type="button" class="btn btn-outline-info" :class="{active: todayRankPlatform === 'dy'}" @click="setTodayRankRoute('dy')">抖音</button>
-                        <button type="button" class="btn btn-outline-info" :class="{active: todayRankPlatform === 'hg'}" @click="setTodayRankRoute('hg')">红果</button>
-                    </div>
-                    <select class="form-select form-select-sm d-none" style="width: 130px;" x-model="todayRankPlatform" @change="setTodayRankRoute(todayRankPlatform)">
-                        <option value="dy">抖音</option>
-                        <option value="hg">红果</option>
-                    </select>
-                    <select class="form-select form-select-sm" style="width: 150px;" x-model="todayRankDate" @change="fetchTodayRank">
-                        <option value="">最新日期</option>
-                        <template x-for="date in todayRankDates()" :key="date">
-                            <option :value="date" x-text="date"></option>
-                        </template>
-                    </select>
-                    <button class="btn btn-outline-secondary btn-sm" @click="fetchTodayRank" :disabled="todayRankLoading">
-                        <i class="bi bi-arrow-clockwise me-1"></i>刷新
-                    </button>
-                </div>
-            </div>
-
-            <div class="alert alert-danger" x-show="todayRankError" x-text="todayRankError"></div>
-            <div class="alert alert-secondary" x-show="!todayRankLoading && todayRankData?.message" x-text="todayRankData?.message"></div>
-
-            <div class="row g-3 mb-3">
-                <div class="col-md-2">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">今日榜单数</div><div class="metric-value" x-text="todayRankData?.summary?.current_count || 0"></div></div></div>
-                </div>
-                <div class="col-md-2">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">上一期榜单数</div><div class="metric-value" x-text="todayRankData?.summary?.previous_count || 0"></div></div></div>
-                </div>
-                <div class="col-md-2">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">共同在榜</div><div class="metric-value" x-text="todayRankData?.summary?.common_count || 0"></div></div></div>
-                </div>
-                <div class="col-md-2">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">新上榜</div><div class="metric-value" x-text="todayRankData?.summary?.new_count || 0"></div></div></div>
-                </div>
-                <div class="col-md-2">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">下榜</div><div class="metric-value" x-text="todayRankData?.summary?.dropped_count || 0"></div></div></div>
-                </div>
-                <div class="col-md-2">
-                    <div class="card metric-card"><div class="card-body"><div class="text-muted">排名变动剧目</div><div class="metric-value fs-5" x-text="`上升 ${todayRankData?.summary?.rank_up_count || 0} / 下降 ${todayRankData?.summary?.rank_down_count || 0}`"></div></div></div>
-                </div>
-            </div>
-
-            <div x-show="todayRankPlatform === 'hg'" class="mb-3">
-                <div class="today-rank-route-nav mb-2">
-                    <template x-for="section in hongguoSections()" :key="section.key">
-                        <a href="#" class="btn btn-sm today-rank-route-link"
-                            :class="{active: section.key === todayRankSectionKey}"
-                            @click.prevent="setTodayRankRoute('hg', section.key)">
-                            <span x-text="section.label"></span>
-                        </a>
-                    </template>
-                </div>
-                <div class="today-rank-route-nav hongguo-ranking-nav">
-                    <template x-for="ranking in hongguoRankings()" :key="ranking.key">
-                        <a href="#" class="btn btn-sm today-rank-route-link"
-                            :class="{active: ranking.key === todayRankRankingKey}"
-                            @click.prevent="setTodayRankRoute('hg', todayRankSectionKey, ranking.key)">
-                            <span x-text="ranking.label"></span>
-                        </a>
-                    </template>
-                </div>
-            </div>
-
-            <div class="row g-3" x-show="todayRankPlatform !== 'hg'">
-                <template x-for="section in todayRankSections()" :key="section.key">
-                    <div class="col-12">
-                        <div class="d-flex align-items-center justify-content-between mt-2 mb-2">
-                            <h5 class="mb-0" x-text="section.label"></h5>
-                            <span class="text-muted small" x-text="summaryText(section.summary)"></span>
-                        </div>
-                        <div class="row g-3">
-                            <template x-for="ranking in section.rankings" :key="ranking.key">
-                                <div class="col-12">
-                                    <div class="card today-rank-table">
-                                        <div class="card-header ranking-heading">
-                                                <div class="ranking-title" x-text="ranking.label"></div>
-                                            <span></span>
-                                        </div>
-                                        <div class="card-header border-top py-1 d-flex gap-1 flex-wrap">
-                                            <template x-for="tbl in rankingTables(ranking)" :key="tbl.key">
-                                                <button class="btn btn-sm border-0 rounded-1 small px-2 rank-filter-btn"
-                                                    :class="getRankingActiveTable(ranking.key) === tbl.key ? 'bg-info bg-opacity-10 text-info' : 'text-muted'"
-                                                    @click="setRankingActiveTable(ranking.key, tbl.key)">
-                                                    <span x-text="tbl.title"></span>
-                                                    <span class="badge ms-1 rounded-pill"
-                                                        :class="tbl.items.length > 0 ? 'bg-secondary' : 'bg-transparent border-secondary border'"
-                                                        x-text="tbl.items.length"></span>
-                                                </button>
-                                            </template>
-                                        </div>
-                                        <div class="card-body p-0">
-                                            <template x-for="tbl in rankingTables(ranking)" :key="tbl.key">
-                                                <div x-show="getRankingActiveTable(ranking.key) === tbl.key" class="scroll-pane">
-                                                    <table class="table table-sm table-hover align-middle mb-0">
-                                                        <thead>
-                                                            <tr>
-                                                                <template x-for="column in tbl.columns" :key="`${ranking.key}-${tbl.key}-${column.key}`">
-                                                                    <th :class="rankColumnHeaderClass(column)" :style="rankColumnStyle(column)">
-                                                                        <i x-show="column.icon" class="bi me-1" :class="column.icon"></i><span x-text="column.label"></span>
-                                                                    </th>
-                                                                </template>
-                                                            </tr>
-                                                        </thead>
-                                                        <tbody>
-                                                            <template x-for="item in tbl.items" :key="`${ranking.key}-${tbl.key}-${item.record_id}-${item.rank}`">
-                                                                <tr>
-                                                                    <template x-for="column in tbl.columns" :key="`${ranking.key}-${tbl.key}-${item.record_id}-${column.key}`">
-                                                                        <td :class="rankColumnCellClass(column)" :title="rankColumnTooltip(item, column, tbl)">
-                                                                            <template x-if="column.kind === 'rank'"><span class="rank-number" :class="rankNumberClass(item.rank)" x-text="item.rank || '-'"></span></template>
-                                                                            <template x-if="column.kind === 'title'"><div class="rank-drama-title" x-text="item.title || '-'"></div></template>
-                                                                            <template x-if="column.kind === 'category'"><span class="badge bg-secondary bg-opacity-25 text-light" x-text="item.board_type || item.category_label || '-'"></span></template>
-                                                                            <template x-if="column.kind === 'status'"><span class="status-pill" :class="entryStatusClass(item, tbl.key)" x-text="entryStatus(item, tbl.key)"></span></template>
-                                                                            <template x-if="column.kind === 'rank_delta'"><span class="status-pill" :class="entryStatusClass(item, tbl.key)" x-text="formatRankDelta(item.rank_delta)"></span></template>
-                                                                            <template x-if="column.kind === 'metric' || column.kind === 'text' || column.kind === 'rank_duration'"><span x-text="rankColumnValue(item, column, tbl)"></span></template>
-                                                                        </td>
-                                                                    </template>
-                                                                </tr>
-                                                            </template>
-                                                            <tr x-show="tbl.items.length === 0">
-                                                                <td :colspan="rankingTableColspan(tbl)" class="text-center text-muted py-3">暂无数据</td>
-                                                            </tr>
-                                                        </tbody>
-                                                    </table>
-                                                </div>
-                                            </template>
-                                        </div>
-                                    </div>
-                                </div>
-                            </template>
-                            <div class="col-12" x-show="section.rankings.length === 0">
-                                <div class="text-muted py-4">暂无数据</div>
-                            </div>
-                        </div>
-                    </div>
-                </template>
-            </div>
-
-            <div class="row g-3" x-show="todayRankPlatform === 'hg'">
-                <template x-if="selectedHongguoRanking()">
-                    <div class="col-12">
-                        <div class="card today-rank-table">
-                            <div class="card-header ranking-heading">
-                                <div class="ranking-title" x-text="selectedHongguoRanking().label"></div>
-                                <span></span>
-                            </div>
-                            <div class="card-header border-top py-1 d-flex gap-1 flex-wrap">
-                                <template x-for="tbl in rankingTables(selectedHongguoRanking())" :key="tbl.key">
-                                    <button class="btn btn-sm border-0 rounded-1 small px-2 rank-filter-btn"
-                                        :class="getRankingActiveTable(selectedHongguoRanking().key) === tbl.key ? 'bg-info bg-opacity-10 text-info' : 'text-muted'"
-                                        @click="setRankingActiveTable(selectedHongguoRanking().key, tbl.key)">
-                                        <span x-text="tbl.title"></span>
-                                        <span class="badge ms-1 rounded-pill"
-                                            :class="tbl.items.length > 0 ? 'bg-secondary' : 'bg-transparent border-secondary border'"
-                                            x-text="tbl.items.length"></span>
-                                    </button>
-                                </template>
-                            </div>
-                            <div class="card-body p-0">
-                                <template x-for="tbl in rankingTables(selectedHongguoRanking())" :key="tbl.key">
-                                    <div x-show="getRankingActiveTable(selectedHongguoRanking().key) === tbl.key" class="scroll-pane">
-                                        <table class="table table-sm table-hover align-middle mb-0">
-                                            <thead>
-                                                <tr>
-                                                    <template x-for="column in tbl.columns" :key="`${selectedHongguoRanking().key}-${tbl.key}-${column.key}`">
-                                                        <th :class="rankColumnHeaderClass(column)" :style="rankColumnStyle(column)">
-                                                            <i x-show="column.icon" class="bi me-1" :class="column.icon"></i><span x-text="column.label"></span>
-                                                        </th>
-                                                    </template>
-                                                </tr>
-                                            </thead>
-                                            <tbody>
-                                                <template x-for="item in tbl.items" :key="`${selectedHongguoRanking().key}-${tbl.key}-${item.record_id}-${item.rank}`">
-                                                    <tr>
-                                                        <template x-for="column in tbl.columns" :key="`${selectedHongguoRanking().key}-${tbl.key}-${item.record_id}-${column.key}`">
-                                                            <td :class="rankColumnCellClass(column)" :title="rankColumnTooltip(item, column, tbl)">
-                                                                <template x-if="column.kind === 'rank'"><span class="rank-number" :class="rankNumberClass(item.rank)" x-text="item.rank || '-'"></span></template>
-                                                                <template x-if="column.kind === 'title'"><div class="rank-drama-title" x-text="item.title || '-'"></div></template>
-                                                                <template x-if="column.kind === 'category'"><span class="badge bg-secondary bg-opacity-25 text-light" x-text="item.board_type || item.category_label || '-'"></span></template>
-                                                                <template x-if="column.kind === 'status'"><span class="status-pill" :class="entryStatusClass(item, tbl.key)" x-text="entryStatus(item, tbl.key)"></span></template>
-                                                                <template x-if="column.kind === 'rank_delta'"><span class="status-pill" :class="entryStatusClass(item, tbl.key)" x-text="formatRankDelta(item.rank_delta)"></span></template>
-                                                                            <template x-if="column.kind === 'metric' || column.kind === 'text' || column.kind === 'rank_duration'"><span x-text="rankColumnValue(item, column, tbl)"></span></template>
-                                                            </td>
-                                                        </template>
-                                                    </tr>
-                                                </template>
-                                                <tr x-show="tbl.items.length === 0">
-                                                    <td :colspan="rankingTableColspan(tbl)" class="text-center text-muted py-3">暂无数据</td>
-                                                </tr>
-                                            </tbody>
-                                        </table>
-                                    </div>
-                                </template>
-                            </div>
-                        </div>
-                    </div>
-                </template>
-                <div class="col-12" x-show="!selectedHongguoRanking()">
-                    <div class="text-muted py-4">暂无数据</div>
-                </div>
-            </div>
-        </section>
-
-        <template x-if="false"><section>
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <h3 class="mb-0">RAG 问答</h3>
-                <button class="btn btn-outline-secondary btn-sm" @click="fetchRagStatus">
-                    <i class="bi bi-arrow-clockwise me-1"></i>刷新
-                </button>
-            </div>
-            <div class="row g-3">
-                <div class="col-xl-4">
-                    <div class="card mb-3">
-                        <div class="card-header">索引任务</div>
-                        <div class="card-body">
-                            <div class="mb-3">
-                                <label class="form-label">Job ID</label>
-                                <select class="form-select" x-model="ragIndex.job_id" @change="syncRagPlatform">
-                                    <option value="">选择任务</option>
-                                    <template x-for="job in jobs" :key="job.job_id">
-                                        <option :value="job.job_id" x-text="`${job.job_id} (${job.platform})`"></option>
-                                    </template>
-                                </select>
-                            </div>
-                            <div class="mb-3">
-                                <label class="form-label">平台</label>
-                                <select class="form-select" x-model="ragIndex.platform">
-                                    <option value="dy">抖音 dy</option>
-                                    <option value="hg">红果 hg</option>
-                                </select>
-                            </div>
-                            <div class="form-check mb-3">
-                                <input class="form-check-input" id="ragRebuild" type="checkbox" x-model="ragIndex.rebuild">
-                                <label class="form-check-label" for="ragRebuild">重建 Job 索引</label>
-                            </div>
-                            <button class="btn btn-primary w-100" @click="indexRagJob" :disabled="ragLoading || !ragIndex.job_id">
-                                <i class="bi bi-database-add me-1"></i>执行索引
-                            </button>
-                            <div class="alert alert-secondary mt-3 mb-0" x-show="ragIndexResult">
-                                <div>状态：<strong x-text="ragIndexResult?.status || '-'"></strong></div>
-                                <div>Chunk：<span x-text="ragIndexResult?.chunk_count || 0"></span></div>
-                                <div>文件：<span x-text="ragIndexResult?.artifact_count || 0"></span></div>
-                            </div>
-                        </div>
-                    </div>
-                    <div class="card">
-                        <div class="card-header">索引状态</div>
-                        <div class="card-body">
-                            <p class="mb-2">已索引 Job：<strong x-text="ragStatus.indexed_jobs || 0"></strong></p>
-                            <p class="mb-2">Chunk 数：<strong x-text="ragStatus.chunk_count || 0"></strong></p>
-                            <p class="mb-0">后端：<span x-text="ragStatus.vector_store?.backend || '-'"></span></p>
-                        </div>
-                    </div>
-                </div>
-                <div class="col-xl-8">
-                    <div class="card">
-                        <div class="card-header">知识库提问</div>
-                        <div class="card-body">
-                            <div class="row g-2 mb-3">
-                                <div class="col-md-8">
-                                    <input class="form-control" x-model="ragQuestion" @keydown.enter="queryRag" placeholder="最近爆的复仇类短剧有哪些？">
-                                </div>
-                                <div class="col-md-2">
-                                    <input class="form-control" type="number" min="1" max="20" x-model.number="ragTopK">
-                                </div>
-                                <div class="col-md-2 d-grid">
-                                    <button class="btn btn-success" @click="queryRag" :disabled="ragLoading || !ragQuestion.trim()">
-                                        <i class="bi bi-send me-1"></i>提问
-                                    </button>
-                                </div>
-                            </div>
-                            <div class="alert alert-danger" x-show="ragError" x-text="ragError"></div>
-                            <div x-show="ragAnswer.answer">
-                                <h6>回答</h6>
-                                <pre class="border border-secondary rounded p-3 text-light" style="white-space: pre-wrap;" x-text="ragAnswer.answer"></pre>
-                                <h6>引用</h6>
-                                <div class="list-group list-group-flush">
-                                    <template x-for="(citation, index) in ragAnswer.citations || []" :key="index">
-                                        <div class="list-group-item bg-transparent text-light border-secondary">
-                                            <div class="fw-semibold" x-text="citation.title || citation.source_path"></div>
-                                            <div class="small text-muted" x-text="`${citation.platform}/${citation.job_id} · ${citation.source_type} #${citation.chunk_index}`"></div>
-                                            <div class="small text-muted" x-text="citation.source_path"></div>
-                                        </div>
-                                    </template>
-                                </div>
-                            </div>
-                            <div class="text-muted" x-show="!ragAnswer.answer && !ragError">索引任务后即可提问。</div>
-                        </div>
-                    </div>
-                </div>
-            </div>
-        </section></template>
-
-        <section x-show="currentTab === 'dashboard'" id="configEditor">
-            <div class="d-flex align-items-center justify-content-between mb-3">
-                <h3 class="mb-0">配置编辑</h3>
-                <div>
-                    <button class="btn btn-outline-secondary me-2" @click="fetchConfigForm">重新加载</button>
-                    <button class="btn btn-success" @click="saveConfigForm">保存修改</button>
-                </div>
-            </div>
-            <div class="row g-3">
-                <div class="col-xl-8">
-                    <div class="card">
-                        <div class="card-header">基础参数</div>
-                        <div class="card-body">
-                            <div class="row g-3">
-                                <template x-for="field in configRootFields" :key="field.key">
-                                    <div class="col-md-6">
-                                        <label class="form-label" x-text="field.label"></label>
-                                        <template x-if="field.type === 'checkbox'">
-                                            <div class="form-check form-switch mt-2">
-                                                <input class="form-check-input" type="checkbox" x-model="configForm.root[field.key]">
-                                            </div>
-                                        </template>
-                                        <template x-if="field.type === 'number'">
-                                            <input type="number" class="form-control" x-model.number="configForm.root[field.key]">
-                                        </template>
-                                        <template x-if="field.type === 'text'">
-                                            <input type="text" class="form-control" x-model="configForm.root[field.key]">
-                                        </template>
-                                    </div>
-                                </template>
-                            </div>
-                        </div>
-                    </div>
-
-                    <div class="card mt-3">
-                        <div class="card-header d-flex align-items-center justify-content-between">
-                            <span>项目配置</span>
-                            <button class="btn btn-outline-success btn-sm" @click="addConfigItem">项目添加</button>
-                        </div>
-                        <div class="card-body">
-                            <template x-for="(item, index) in configForm.items" :key="index">
-                                <div class="border border-secondary rounded p-3 mb-3">
-                                    <div class="d-flex align-items-center justify-content-between mb-3">
-                                        <strong x-text="item.project || `项目 ${index + 1}`"></strong>
-                                        <button class="btn btn-outline-danger btn-sm" @click="removeConfigItem(index)">删除</button>
-                                    </div>
-                                    <div class="row g-3">
-                                        <div class="col-md-6">
-                                            <label class="form-label">project</label>
-                                            <input class="form-control" x-model="item.project">
-                                        </div>
-                                        <div class="col-md-3">
-                                            <label class="form-label">评论不少于</label>
-                                            <input type="number" class="form-control" x-model.number="item['评论不少于']">
-                                        </div>
-                                        <div class="col-md-3">
-                                            <label class="form-label">作品天数</label>
-                                            <input type="number" class="form-control" x-model.number="item['作品天数']">
-                                        </div>
-                                        <div class="col-12">
-                                            <label class="form-label">搜索关键词</label>
-                                            <textarea class="form-control" rows="2" x-model="item['搜索关键词']"></textarea>
-                                        </div>
-                                        <div class="col-12">
-                                            <label class="form-label">屏蔽关键词</label>
-                                            <textarea class="form-control" rows="2" x-model="item['屏蔽关键词']"></textarea>
-                                        </div>
-                                        <div class="col-12">
-                                            <label class="form-label">有关视频判定</label>
-                                            <textarea class="form-control" rows="3" x-model="item['有关视频判定']"></textarea>
-                                        </div>
-                                        <div class="col-12">
-                                            <label class="form-label">黑名单视频判定</label>
-                                            <textarea class="form-control" rows="3" x-model="item['黑名单视频判定']"></textarea>
-                                        </div>
-                                        <div class="col-md-6">
-                                            <label class="form-label">白名单地区</label>
-                                            <input class="form-control" x-model="item['白名单地区']">
-                                        </div>
-                                        <div class="col-md-6">
-                                            <label class="form-label">黑名单地区</label>
-                                            <input class="form-control" x-model="item['黑名单地区']">
-                                        </div>
-                                    </div>
-                                </div>
-                            </template>
-                            <div x-show="configForm.items.length === 0" class="text-center text-muted py-4">暂无项目配置</div>
-                        </div>
-                    </div>
-                </div>
-                <div class="col-xl-4">
-                    <div class="card">
-                        <div class="card-header d-flex align-items-center justify-content-between">
-                            <span>备份</span>
-                            <button class="btn btn-outline-success btn-sm" @click="createBackup">创建</button>
-                        </div>
-                        <div class="list-group list-group-flush">
-                            <template x-for="backup in backups" :key="backup.name">
-                                <div class="list-group-item bg-transparent text-light border-secondary">
-                                    <div class="d-flex justify-content-between">
-                                        <span x-text="formatDate(backup.created)"></span>
-                                        <span class="badge bg-secondary" x-text="formatSize(backup.size)"></span>
-                                    </div>
-                                    <div class="small text-muted my-2" x-text="backup.name"></div>
-                                    <div class="d-flex gap-2">
-                                        <button class="btn btn-sm btn-outline-warning flex-fill" @click="restoreBackup(backup.name)">恢复</button>
-                                        <button class="btn btn-sm btn-outline-danger flex-fill" @click="deleteBackup(backup.name)">删除</button>
-                                    </div>
-                                </div>
-                            </template>
-                            <div x-show="backups.length === 0" class="text-center text-muted py-4">暂无备份</div>
-                        </div>
-                    </div>
-                </div>
-            </div>
-        </section>
-    </main>
-
-    <div class="modal fade" id="projectModal" tabindex="-1">
-        <div class="modal-dialog modal-dialog-centered">
-            <div class="modal-content">
-                <div class="modal-header">
-                    <h5 class="modal-title" x-text="modalMode === 'crawl' ? '选择爬评论项目' : '选择导出项目'"></h5>
-                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
-                </div>
-                <div class="modal-body">
-                    <div class="form-check mb-3" x-show="modalMode === 'export'">
-                        <input class="form-check-input" type="checkbox" id="autoUploadCheck" x-model="autoUpload">
-                        <label class="form-check-label" for="autoUploadCheck">导出后自动上传</label>
-                    </div>
-                    <template x-for="(item, index) in modalProjects" :key="item.name">
-                        <div class="d-flex align-items-center justify-content-between border border-secondary rounded p-2 mb-2">
-                            <label class="form-check-label">
-                                <input class="form-check-input me-2" type="checkbox" :value="item.name" x-model="selectedModalProjects">
-                                <span x-text="item.name"></span>
-                                <span class="badge bg-secondary ms-2" x-text="item.count"></span>
-                            </label>
-                            <div class="btn-group btn-group-sm">
-                                <button class="btn btn-outline-secondary" @click="moveProjectUp(index)" :disabled="index === 0"><i class="bi bi-arrow-up"></i></button>
-                                <button class="btn btn-outline-secondary" @click="moveProjectDown(index)" :disabled="index === modalProjects.length - 1"><i class="bi bi-arrow-down"></i></button>
-                            </div>
-                        </div>
-                    </template>
-                </div>
-                <div class="modal-footer">
-                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
-                    <button type="button" class="btn btn-primary" @click="confirmBatchAction">确认</button>
-                </div>
-            </div>
-        </div>
-    </div>
-
-    <div class="modal fade" id="filePreviewModal" tabindex="-1">
-        <div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
-            <div class="modal-content">
-                <div class="modal-header">
-                    <div>
-                        <h5 class="modal-title"><i class="bi bi-eye me-2"></i><span x-text="previewFile.file?.name || '文件预览'"></span></h5>
-                        <div class="small text-muted mt-1"><span x-text="previewFile.file?.drama_name || ''"></span><span x-show="previewFile.file?.episode_label" x-text="` · ${previewFile.file?.episode_label}`"></span><span x-show="previewFile.file" x-text="` · ${formatSize(previewFile.file?.size || 0)}`"></span></div>
-                    </div>
-                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
-                </div>
-                <div class="modal-body">
-                    <div x-show="previewFile.status === 'loading'" class="py-5 text-center"><span class="spinner-border"></span></div>
-                    <div x-show="previewFile.status === 'error'" class="alert alert-warning" x-text="previewFile.message"></div>
-                    <video class="file-preview-media" x-show="previewFile.status === 'success' && previewFile.kind === 'video'" :src="previewFile.url" controls preload="metadata" @error="previewFile.status = 'error'; previewFile.message = '浏览器无法播放该视频编码，请下载后查看。'"></video>
-                    <audio class="w-100" x-show="previewFile.status === 'success' && previewFile.kind === 'audio'" :src="previewFile.url" controls preload="metadata" @error="previewFile.status = 'error'; previewFile.message = '浏览器无法播放该音频格式，请下载后查看。'"></audio>
-                    <pre class="file-preview-text" x-show="previewFile.status === 'success' && ['text', 'json'].includes(previewFile.kind)" x-text="previewFile.content"></pre>
-                    <div class="small text-muted mt-3" x-show="previewFile.file" x-text="previewFile.file?.path"></div>
-                </div>
-                <div class="modal-footer">
-                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
-                    <button type="button" class="btn btn-outline-light" x-show="previewFile.file" @click="downloadFile(previewFile.file.path)"><i class="bi bi-download me-1"></i>下载</button>
-                </div>
-            </div>
-        </div>
-    </div>
-
-    <div class="modal fade" id="audioModal" tabindex="-1">
-        <div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
-            <div class="modal-content">
-                <div class="modal-header">
-                    <h5 class="modal-title">音频分析预览</h5>
-                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
-                </div>
-                <div class="modal-body">
-                    <div x-show="audioAnalysis.status === 'loading'" class="d-flex align-items-center gap-2">
-                        <span class="spinner-border spinner-border-sm"></span>
-                        <span>分析中...</span>
-                    </div>
-                    <div x-show="audioAnalysis.status === 'error'" class="alert alert-danger" x-text="audioAnalysis.message"></div>
-                    <div x-show="audioAnalysis.status === 'success'">
-                        <div class="mb-3">
-                            <div class="fw-semibold" x-text="audioAnalysis.file_name"></div>
-                            <small class="text-muted" x-text="audioAnalysis.analysis_time"></small>
-                        </div>
-                        <h6>摘要</h6>
-                        <div class="border border-secondary rounded p-3 mb-3" x-text="audioAnalysis.summary || '无摘要'"></div>
-                        <h6>关键点</h6>
-                        <ul>
-                            <template x-for="point in audioAnalysis.key_points || []" :key="point">
-                                <li x-text="point"></li>
-                            </template>
-                        </ul>
-                        <h6>转写文本</h6>
-                        <pre class="border border-secondary rounded p-3 text-light" style="white-space: pre-wrap; max-height: 360px;" x-text="audioAnalysis.transcript"></pre>
-                    </div>
-                </div>
-                <div class="modal-footer">
-                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
-                    <button type="button" class="btn btn-primary" x-show="audioAnalysis.status === 'success'" @click="downloadAudioAnalysis">下载结果</button>
-                </div>
-            </div>
-        </div>
-    </div>
-
-    <div class="download-progress-toast" x-show="dramaDownload.background && dramaDownload.jobId" x-cloak>
-        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
-            <button type="button" class="btn btn-sm btn-link p-0 text-decoration-none text-start flex-grow-1" @click="dramaDownload.background = false; dramaDownloadModal.show()">
-                <span class="fw-semibold text-light" x-text="dramaDownload.title || '剧集下载' "></span>
-                <span class="small text-muted ms-2" x-text="`${Number(dramaDownload.progress?.percent || 0).toFixed(0)}%`"></span>
-            </button>
-            <button type="button" class="btn btn-sm btn-outline-secondary" @click="dramaDownload.background = false" title="关闭下载进度" aria-label="关闭下载进度"><i class="bi bi-x-lg"></i></button>
-        </div>
-        <div class="small text-muted mb-2" x-text="dramaDownload.progressMessage || '正在下载' "></div>
-        <div class="progress mb-2" role="progressbar"><div class="progress-bar progress-bar-striped progress-bar-animated" :class="dramaDownload.paused ? 'bg-warning' : ''" :style="`width: ${dramaDownload.progress?.percent || 0}%`"></div></div>
-        <div class="d-flex justify-content-between align-items-center gap-2"><span class="small text-muted" x-text="`${dramaDownload.progress?.completed || 0} / ${dramaDownload.progress?.total || 0}`"></span><div class="d-flex gap-1"><button type="button" class="btn btn-sm btn-outline-warning" x-show="!dramaDownload.paused && !dramaDownload.finished" @click="pauseDramaDownload" title="暂停下载" aria-label="暂停下载"><i class="bi bi-pause-fill"></i></button><button type="button" class="btn btn-sm btn-outline-success" x-show="dramaDownload.paused && !dramaDownload.finished" @click="resumeDramaDownload" title="继续下载" aria-label="继续下载"><i class="bi bi-play-fill"></i></button><button type="button" class="btn btn-sm btn-outline-primary" @click="dramaDownload.background = false; dramaDownloadModal.show()" title="查看下载详情" aria-label="查看下载详情"><i class="bi bi-arrow-up-right"></i></button></div></div>
-    </div>
-
-    <div class="modal fade" id="dramaDownloadModal" tabindex="-1">
-        <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
-            <div class="modal-content">
-                <div class="modal-header">
-                    <h5 class="modal-title"><i class="bi bi-download me-2"></i>下载剧集</h5>
-                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
-                </div>
-                <div class="modal-body">
-                    <div class="mb-3"><span class="text-muted">剧名：</span><strong x-text="dramaDownload.title"></strong></div>
-                    <div class="alert alert-danger py-2" x-show="dramaDownload.error" x-text="dramaDownload.error"></div>
-                    <div class="mb-3" x-show="dramaDownload.searching"><div class="d-flex align-items-center gap-2"><span class="spinner-border spinner-border-sm"></span><span>正在检索剧目与集数…</span></div></div>
-                    <div class="alert alert-warning py-2" x-show="dramaDownload.source === 'douyin_fallback'" x-text="`${dramaDownload.fallbackReason}。请手动确认抖音视频；该视频可能是片段或推广内容。`"></div>
-                    <div class="mb-3" x-show="dramaDownload.starting || dramaDownload.jobId">
-                        <div class="d-flex justify-content-between small mb-1"><span x-text="dramaDownload.progressMessage || '正在创建下载任务…'"></span><strong x-text="`${Number(dramaDownload.progress?.percent || 0).toFixed(0)}%`"></strong></div>
-                        <div class="progress" role="progressbar"><div class="progress-bar progress-bar-striped progress-bar-animated" :class="dramaDownload.paused ? 'bg-warning' : ''" :style="`width: ${dramaDownload.progress?.percent || 0}%`"></div></div>
-                        <div class="small text-muted mt-1" x-text="`${dramaDownload.progress?.completed || 0} / ${dramaDownload.progress?.total || 0} ${dramaDownload.source === 'douyin_fallback' ? '个视频' : '集'}`"></div>
-                        <div class="small text-muted mt-1" x-show="Number(dramaDownload.progress?.total_bytes || 0) > 0" x-text="`${formatDramaBytes(dramaDownload.progress?.downloaded_bytes || 0)} / ${formatDramaBytes(dramaDownload.progress?.total_bytes || 0)}`"></div>
-                        <div class="small text-muted mt-1" x-show="dramaDownload.source === 'douyin_fallback'" x-text="`已下载 ${dramaDownload.progress?.downloaded || 0} · 付费跳过 ${dramaDownload.progress?.skipped_paid || 0} · 失败 ${dramaDownload.progress?.failed || 0}`"></div>
-                    </div>
-                    <div x-show="!dramaDownload.searching && !dramaDownload.jobId && dramaDownload.candidates.length">
-                        <label class="form-label" x-text="dramaDownload.source === 'douyin_fallback' ? '确认抖音视频' : '确认剧目'"></label>
-                        <div x-show="dramaDownload.source !== 'douyin_fallback'">
-                            <template x-for="candidate in dramaDownload.candidates" :key="candidate.book_id">
-                                <button type="button" class="list-group-item list-group-item-action bg-transparent text-light border-secondary mb-2" :class="{active: dramaDownload.selectedCandidate?.book_id === candidate.book_id}" @click="selectDramaDownloadCandidate(candidate)">
-                                    <div class="fw-semibold text-start" x-text="candidate.title"></div><div class="small text-muted text-start" x-text="`${candidate.episode_count || '-'} 集 · ${candidate.summary || '暂无简介'}`"></div>
-                                </button>
-                            </template>
-                        </div>
-                        <div x-show="dramaDownload.source === 'douyin_fallback'">
-                            <div class="d-flex justify-content-between align-items-center mb-2">
-                                <div><span class="small text-muted" x-text="`已选择 ${dramaDownload.selectedAwemeIds.length} 个视频`"></span><span class="small text-info ms-2" x-show="selectedDouyinEpisodeSummary()" x-text="selectedDouyinEpisodeSummary()"></span></div>
-                                <label class="form-check small mb-0"><input class="form-check-input me-1" type="checkbox" x-model="dramaDownload.allDouyinCandidates" @change="toggleAllDouyinDownloadCandidates">全选当前结果</label>
-                            </div>
-                            <template x-for="candidate in dramaDownload.candidates" :key="candidate.aweme_id">
-                                <label class="list-group-item bg-transparent text-light border-secondary mb-2" :class="{'border-primary': dramaDownload.selectedAwemeIds.includes(candidate.aweme_id), 'opacity-75': candidate.is_paid}">
-                                    <div class="d-flex gap-2 align-items-start">
-                                        <input class="form-check-input mt-1" type="checkbox" :value="candidate.aweme_id" x-model="dramaDownload.selectedAwemeIds" :disabled="candidate.is_paid" @change="syncDouyinDownloadSelection">
-                                        <div class="flex-grow-1 min-w-0">
-                                            <div class="fw-semibold text-start"><span x-text="candidate.title"></span><span class="badge bg-primary ms-2" x-show="candidate.is_primary">首条</span><span class="badge bg-warning text-dark ms-2" x-show="candidate.is_paid">需付费</span></div>
-                                            <div class="small text-muted text-start"><span x-text="`${candidate.author || '未知作者'} · 时长 ${formatDramaDuration(candidate.duration)} · 点赞 ${candidate.like_count || 0} · 评论 ${candidate.comment_count || 0}`"></span><span class="badge bg-secondary ms-2" x-show="candidate.episode_label" x-text="candidate.episode_label"></span></div>
-                                            <div class="small text-warning text-start" x-show="candidate.is_paid" x-text="candidate.paid_reason || '该视频需要付费，下载时将自动跳过'"></div>
-                                        </div>
-                                    </div>
-                                </label>
-                            </template>
-                        </div>
-                        <div class="alert alert-info py-2 mt-2" x-show="dramaDownload.source === 'douyin_fallback' && dramaDownload.candidates.some(candidate => dramaDownload.selectedAwemeIds.includes(candidate.aweme_id) && Number(candidate.episode_count || 0) > 1)">标注多集的抖音结果是一个合集视频，将保存为一个视频文件，并在剧本和清单中记录覆盖集数。</div>
-                    </div>
-                    <div class="mt-3" x-show="dramaDownload.episodesLoading"><div class="d-flex align-items-center gap-2"><span class="spinner-border spinner-border-sm"></span><span>正在读取集数…</span></div></div>
-                    <div x-show="dramaDownload.selectedCandidate && dramaDownload.episodes.length" class="mt-3">
-                        <div class="d-flex justify-content-between align-items-center mb-2"><label class="form-label mb-0">选择集数</label><label class="form-check small"><input class="form-check-input me-1" type="checkbox" x-model="dramaDownload.allEpisodes" @change="toggleAllDramaEpisodes">全剧下载</label></div>
-                        <div class="row row-cols-2 row-cols-md-4 g-2">
-                            <template x-for="episode in dramaDownload.episodes" :key="episode.episode">
-                                <label class="form-check border rounded p-2" :class="isDramaEpisodeDownloaded(episode) ? 'border-success opacity-75' : 'border-secondary'"><template x-if="!isDramaEpisodeDownloaded(episode)"><input class="form-check-input ms-0 me-1" type="checkbox" :value="episode.episode" x-model="dramaDownload.selectedEpisodes" :disabled="dramaDownload.allEpisodes"></template><template x-if="isDramaEpisodeDownloaded(episode)"><i class="bi bi-check-circle-fill text-success me-1" title="已下载"></i></template><span x-text="`${episode.episode}. ${episode.title}`"></span><span class="badge bg-success ms-1" x-show="episode.download_status?.status === 'downloaded'">已下载</span></label>
-                            </template>
-                        </div>
-                    </div>
-                    <div class="alert alert-warning mt-3" x-show="dramaDownload.source === 'douyin_fallback' && dramaDownload.selectedAwemeIds.length">来源：抖音降级。将按选择顺序逐个下载和转写，明确需要付费的视频会说明原因并跳过。</div>
-                </div>
-                <div class="modal-footer">
-                    <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">后台下载</button>
-                    <button type="button" class="btn btn-outline-primary" x-show="dramaDownload.finished" @click="openDramaDownloadFiles"><i class="bi bi-folder2-open me-1"></i>查看文件</button>
-                    <button type="button" class="btn btn-outline-warning" x-show="dramaDownload.jobId && !dramaDownload.finished && !dramaDownload.paused" @click="pauseDramaDownload"><i class="bi bi-pause-circle me-1"></i>暂停</button>
-                    <button type="button" class="btn btn-outline-info" x-show="dramaDownload.jobId && !dramaDownload.finished && dramaDownload.paused" @click="resumeDramaDownload"><i class="bi bi-play-circle me-1"></i>继续</button>
-                    <button type="button" class="btn btn-outline-danger" x-show="dramaDownload.jobId && !dramaDownload.finished" @click="cancelDramaDownload"><i class="bi bi-x-circle me-1"></i>取消</button>
-                    <button type="button" class="btn btn-primary" x-show="((dramaDownload.source === 'douyin_fallback' && dramaDownload.selectedAwemeIds.length) || (dramaDownload.source !== 'douyin_fallback' && dramaDownload.selectedCandidate && dramaDownload.episodes.length)) && !dramaDownload.jobId" @click="startDramaDownload" :disabled="dramaDownload.starting || (dramaDownload.source === 'douyin_fallback' ? !dramaDownload.selectedAwemeIds.length : !dramaSelectedEpisodesForDownload().length)"><i class="bi bi-download me-1"></i><span x-text="dramaDownload.source === 'douyin_fallback' ? '批量下载抖音视频并解析' : '开始下载'"></span></button>
-                </div>
-            </div>
-        </div>
-    </div>
-
-    <div class="modal fade" id="transcriptRequiredModal" tabindex="-1" aria-labelledby="transcriptRequiredModalLabel" aria-hidden="true">
-        <div class="modal-dialog modal-dialog-centered">
-            <div class="modal-content">
-                <div class="modal-header">
-                    <h5 class="modal-title" id="transcriptRequiredModalLabel"><i class="bi bi-file-earmark-text me-2"></i>缺少剧目脚本</h5>
-                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
-                </div>
-                <div class="modal-body">
-                    <p class="mb-2">请先下载剧目脚本再拆解</p>
-                    <div class="small text-muted">至少完成一集剧目脚本转写后，系统才能根据真实剧情生成个性化拆解。</div>
-                </div>
-                <div class="modal-footer">
-                    <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">取消</button>
-                    <button type="button" class="btn btn-primary" @click="transcriptRequiredModal.hide(); openTopicDownload(breakdownGeneration.topic)"><i class="bi bi-download me-1"></i>去下载</button>
-                </div>
-            </div>
-        </div>
-    </div>
-
-    <div class="modal fade" id="breakdownGenerationProgressModal" tabindex="-1" data-bs-backdrop="static" aria-labelledby="breakdownGenerationProgressModalLabel" aria-hidden="true">
-        <div class="modal-dialog modal-dialog-centered">
-            <div class="modal-content">
-                <div class="modal-header">
-                    <div>
-                        <h5 class="modal-title" id="breakdownGenerationProgressModalLabel"><i class="bi bi-diagram-3 me-2"></i>生成剧目拆解</h5>
-                        <div class="small text-muted" x-text="breakdownGeneration.topic?.title || ''"></div>
-                    </div>
-                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
-                </div>
-                <div class="modal-body">
-                    <div class="small text-info mb-2" x-show="breakdownGeneration.coverage.length" x-text="`已加载第 ${breakdownGeneration.coverage.join('、')} 集剧目脚本`"></div>
-                    <div class="d-flex justify-content-between small mb-1"><span x-text="breakdownGeneration.message || '正在创建生成任务'"></span><strong x-text="breakdownGenerationProgressLabel()"></strong></div>
-                    <div class="progress" role="progressbar" aria-label="拆解生成进度">
-                        <div class="progress-bar progress-bar-striped" :class="{'progress-bar-animated': breakdownGenerationRunning(), 'bg-danger': breakdownGeneration.status === 'failed'}" :style="`width: ${breakdownGenerationIndeterminate() ? 100 : breakdownGenerationProgressPercent()}%`"></div>
-                    </div>
-                    <div class="alert alert-danger py-2 mt-3 mb-0" x-show="breakdownGeneration.error" x-text="breakdownGeneration.error"></div>
-                </div>
-                <div class="modal-footer">
-                    <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">后台运行</button>
-                    <button type="button" class="btn btn-outline-light" x-show="breakdownGeneration.jobId" @click="openBreakdownTaskLog"><i class="bi bi-list-ul me-1"></i>任务日志</button>
-                    <button type="button" class="btn btn-primary" x-show="breakdownGenerationRetryable()" @click="startTranscriptBreakdown(breakdownGeneration.topic, breakdownGeneration.source)"><i class="bi bi-arrow-clockwise me-1"></i>重试</button>
-                </div>
-            </div>
-        </div>
-    </div>
-
-    <div class="modal fade" id="breakdownEditorModal" tabindex="-1" aria-labelledby="breakdownEditorModalLabel" aria-hidden="true">
-        <div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
-            <div class="modal-content">
-                <div class="modal-header">
-                    <div>
-                        <h5 class="modal-title" id="breakdownEditorModalLabel">爆款拆解编辑</h5>
-                        <div class="small text-muted"><span x-text="breakdownEditor.title || '未命名选题'"></span><span class="ms-2" x-text="`v${breakdownEditor.record?.version || 1} · ${breakdownEditor.record?.status || 'draft'}`"></span></div>
-                    </div>
-                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
-                </div>
-                <div class="modal-body">
-                    <div class="alert alert-danger py-2" x-show="breakdownEditor.error" x-text="breakdownEditor.error"></div>
-                    <div class="row g-3">
-                        <template x-for="item in breakdownEditor.fieldGuide" :key="item.key">
-                            <div class="col-12 border-bottom border-secondary-subtle pb-3">
-                                <div class="d-flex align-items-center justify-content-between gap-2 mb-2"><label class="form-label fw-semibold mb-0" x-text="item.label"></label><span class="badge bg-secondary" x-text="item.type"></span></div>
-                                <template x-if="item.type !== '数组'"><textarea class="form-control" rows="3" x-model="breakdownEditor.content[item.key]" :disabled="breakdownEditor.record?.status === 'locked'"></textarea></template>
-                                <template x-if="item.type === '数组'"><div class="vstack gap-2"><template x-for="(entry, index) in breakdownEditor.content[item.key]" :key="`${item.key}-${index}`"><div class="input-group"><textarea class="form-control" rows="2" x-model="breakdownEditor.content[item.key][index]" :disabled="breakdownEditor.record?.status === 'locked'"></textarea><button type="button" class="btn btn-outline-danger" @click="removeBreakdownArrayItem(item.key, index)" :disabled="breakdownEditor.record?.status === 'locked'" title="删除此项" aria-label="删除此项"><i class="bi bi-trash"></i></button></div></template><button type="button" class="btn btn-sm btn-outline-light align-self-start" @click="addBreakdownArrayItem(item.key)" :disabled="breakdownEditor.record?.status === 'locked'" title="添加一项" aria-label="添加一项"><i class="bi bi-plus-lg"></i></button></div></template>
-                                <div class="small text-muted mt-2" x-text="item.description"></div>
-                                <div class="small text-info mt-1"><span>写法：</span><span x-text="item.writing_prompt"></span></div>
-                                <div class="small text-warning mt-1"><span>示例：</span><span x-text="item.example"></span></div>
-                                <div class="small text-success mt-1"><span>证据：</span><span x-text="item.evidence_rule"></span></div>
-                            </div>
-                        </template>
-                    </div>
-                    <div class="small text-muted mt-2">内容字段来源和风险信息保留在拆解版本中。锁定后不可覆盖，只能创建新版本。</div>
-                </div>
-                <div class="modal-footer">
-                    <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">关闭</button>
-                    <button type="button" class="btn btn-outline-info" @click="regenerateCurrentBreakdown" :disabled="breakdownEditor.saving || breakdownGenerationRunning()"><i class="bi bi-arrow-clockwise me-1"></i>重新生成</button>
-                    <button type="button" class="btn btn-outline-light" @click="saveBreakdownEditor" :disabled="breakdownEditor.saving || breakdownEditor.record?.status === 'locked'"><i class="bi bi-save me-1"></i>保存草稿</button>
-                    <button type="button" class="btn btn-warning" @click="lockBreakdownEditor" :disabled="breakdownEditor.saving || breakdownEditor.record?.status === 'locked'"><i class="bi bi-lock me-1"></i>锁定版本</button>
-                </div>
-            </div>
-        </div>
-    </div>
-
-    <script>
-        function localDateString(date = new Date()) {
-            const year = date.getFullYear();
-            const month = String(date.getMonth() + 1).padStart(2, '0');
-            const day = String(date.getDate()).padStart(2, '0');
-            return `${year}-${month}-${day}`;
-        }
-
-        function appData() {
-            return {
-                currentTab: 'dashboard',
-                isRunning: false,
-                jobs: [],
-                selectedJobId: '',
-                logs: [],
-                batchProgress: '',
-                fileFilter: 'all',
-                fileSearch: '',
-                files: [],
-                filteredFiles: [],
-                selectedFilePaths: [],
-                batchDeleteMode: false,
-                filePreviewModal: null,
-                previewFile: {status: 'idle', file: null, kind: '', url: '', content: '', message: ''},
-                configForm: {root: {}, items: []},
-                configRootFields: [
-                    {key: 'host', label: 'host', type: 'text'},
-                    {key: 'video_filter', label: 'video_filter', type: 'checkbox'},
-                    {key: 'sleep', label: 'sleep', type: 'number'},
-                    {key: 'autoRun', label: 'autoRun', type: 'checkbox'},
-                    {key: '默认平台', label: '默认平台', type: 'text'},
-                    {key: '默认爬取类型', label: '默认爬取类型', type: 'text'},
-                    {key: '默认起始页码', label: '默认起始页码', type: 'number'},
-                    {key: '默认登录方式', label: '默认登录方式', type: 'text'},
-                    {key: '默认保存方式', label: '默认保存方式', type: 'text'}
-                ],
-                backups: [],
-                projectItems: [],
-                serverIp: '',
-                qrcodeStatus: { has_state: false, message: '' },
-                qrcodeLoggingIn: false,
-                audioModal: null,
-                dramaDownloadModal: null,
-                audioAnalysis: {status: 'idle'},
-                dramaAnalysis: {
-                    title: '',
-                    candidates: [],
-                    selected: null,
-                    episodes: [],
-                    episode: 0,
-                    suggestions: [],
-                    suggestionLoading: false,
-                    suggestionTimer: null,
-                    suggestionRequestId: 0,
-                    searchSource: '',
-                    fallbackReason: '',
-                    loading: false,
-                    error: '',
-                    jobId: '',
-                    notifiedStatus: '',
-                    interfaceTest: null,
-                    settings: {config_url: '', has_activation_code: false},
-                    activationCode: ''
-                },
-                creative: {platform: 'dy', items: [], loading: false, error: '', breakdown: null},
-                topicLibrary: {platform: '', status: '', category: '', minScore: '', hasLocked: '', items: [], loading: false, error: '', breakdown: null},
-                breakdownEditor: {source: '', title: '', record: null, content: {}, fieldGuide: [], saving: false, error: ''},
-                breakdownGeneration: {topic: null, source: '', jobId: '', status: '', message: '', progress: {}, coverage: [], notifiedStatus: '', error: '', startedAt: 0, elapsedSeconds: 0},
-                creativeWorkspace: {
-                    projects: [], eligibleTopics: [], lockedBreakdowns: [], project: null, loading: false, error: '', step: 'blueprints',
-                    create: {topic_id: '', breakdown_id: '', name: '', target_platform: '', target_episode_count: 20},
-                    directionCount: 3, selectedBlueprintId: null, selectedEpisode: 1,
-                    jobId: '', jobMessage: '', jobProgress: {}, jobStatus: '', paused: false, notifiedStatus: '',
-                    jobStartedAt: 0, jobElapsedSeconds: 0,
-                    startingAction: '', activeAction: '',
-                    saveState: {}, notice: '', noticeType: 'success', noticeTimer: null
-                },
-                dramaDownload: {title: '', topic: null, source: 'hongguo', fallbackReason: '', candidates: [], selectedCandidate: null, selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], selectedEpisodes: [], allEpisodes: false, searching: false, episodesLoading: false, starting: false, error: '', jobId: '', progress: {}, progressMessage: '', paused: false, finished: false, background: false},
-                analyticsOverview: {},
-                analyticsTags: {categories: [], selling_points: [], hooks: [], plot_tags: []},
-                analyticsTrends: {points: []},
-                analyticsSentiment: [],
-                analyticsDramas: [],
-                analyticsPlatform: '',
-                analyticsDate: localDateString(),
-                analyticsLoading: false,
-                analyticsError: '',
-                analyticsSyncResult: null,
-                todayRankPlatform: 'dy',
-                todayRankDate: '',
-                todayRankSectionKey: '',
-                todayRankRankingKey: '',
-                todayRankData: null,
-                todayRankLoading: false,
-                todayRankError: '',
-                rankingActiveTables: {},
-                modalMode: 'crawl',
-                modalFile: '',
-                modalProjects: [],
-                selectedModalProjects: [],
-                projectModal: null,
-                autoUpload: true,
-                config: {
-                    platform: 'dy',
-                    login_type: 'qrcode',
-                    crawler_type: 'search',
-                    keywords: '',
-                    start_page: 1,
-                    headless: false,
-                    save_option: 'json',
-                    get_comment: false,
-                    get_sub_comment: false,
-                    specified_ids: '',
-                    creator_ids: '',
-                    cookie: '',
-                    dy_channel: 'video',
-                    dy_sort: 'latest',
-                    dy_publish_time: '1_week',
-                    dy_rank_limit: 50,
-                    dy_rank_comment_limit: 10,
-                    dy_rank_login_wait_seconds: 0,
-                    autoRun: false,
-                    projects: {}
-                },
-
-                async init() {
-                    this.projectModal = new bootstrap.Modal(document.getElementById('projectModal'));
-                    this.audioModal = new bootstrap.Modal(document.getElementById('audioModal'));
-                    this.filePreviewModal = new bootstrap.Modal(document.getElementById('filePreviewModal'));
-                    document.getElementById('filePreviewModal')?.addEventListener('hidden.bs.modal', () => this.closeFilePreview());
-                    this.dramaDownloadModal = new bootstrap.Modal(document.getElementById('dramaDownloadModal'));
-                    document.getElementById('dramaDownloadModal')?.addEventListener('hidden.bs.modal', () => {
-                        if (this.dramaDownload.jobId) this.dramaDownload.background = true;
-                    });
-                    this.breakdownEditorModal = new bootstrap.Modal(document.getElementById('breakdownEditorModal'));
-                    this.transcriptRequiredModal = new bootstrap.Modal(document.getElementById('transcriptRequiredModal'));
-                    this.breakdownGenerationProgressModal = new bootstrap.Modal(document.getElementById('breakdownGenerationProgressModal'));
-                    this.parseTodayRankHash(false);
-                    window.addEventListener('hashchange', () => this.parseTodayRankHash(true));
-                    await this.loadConfig();
-                    await this.fetchConfigForm();
-                    await this.fetchJobs();
-                    await this.fetchFiles();
-                    await this.fetchDramaSettings();
-                    await this.fetchServerIp();
-                    await this.fetchTodayRank();
-                    await this.fetchQrcodeStatus();
-                    this.initTooltips();
-                    setInterval(() => this.fetchLogs(), 1000);
-                    setInterval(() => this.fetchJobs(), 2000);
-                    setInterval(() => this.tickCreativeGenerationElapsed(), 1000);
-                    setInterval(() => this.tickBreakdownGenerationElapsed(), 1000);
-                },
-
-                initTooltips() {
-                    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(element => {
-                        bootstrap.Tooltip.getOrCreateInstance(element);
-                    });
-                },
-
-                async fetchServerIp() {
-                    try {
-                        const res = await fetch('/api/server/ip');
-                        const data = await res.json();
-                        if (data.ip) this.serverIp = `${data.ip}:${data.port}`;
-                    } catch (e) {}
-                },
-
-                async loadConfig() {
-                    try {
-                        const res = await fetch('/api/config');
-                        const data = await res.json();
-                        const source = Array.isArray(data) ? (data[0] || {}) : (data || {});
-                        this.projectItems = source.items || [];
-                        this.config.projects = {};
-                        this.projectItems.forEach(item => {
-                            if (this.config.projects[item.project] === undefined) {
-                                this.config.projects[item.project] = true;
-                            }
-                        });
-                        if (source.autoRun !== undefined) this.config.autoRun = source.autoRun;
-                        this.jobs = data.jobs || [];
-                        this.isRunning = this.jobs.some(job => this.isJobRunning(job));
-                        if (!this.selectedJobId && this.jobs.length > 0) this.selectedJobId = this.jobs[0].job_id;
-                        if (this.selectedJobId && !this.jobs.some(job => job.job_id === this.selectedJobId)) {
-                            this.selectedJobId = this.jobs.length > 0 ? this.jobs[0].job_id : '';
-                            if (!this.selectedJobId) this.logs = [];
-                        }
-                    } catch (e) {}
-                },
-
-                async fetchJobs() {
-                    try {
-                        const res = await fetch('/api/jobs');
-                        const data = await res.json();
-                        this.jobs = data.jobs || [];
-                        this.isRunning = this.jobs.some(job => this.isJobRunning(job));
-                        if (!this.selectedJobId && this.jobs.length > 0) {
-                            this.selectedJobId = this.jobs[0].job_id;
-                        }
-                        if (this.selectedJobId && !this.jobs.some(job => job.job_id === this.selectedJobId)) {
-                            this.selectedJobId = this.jobs.length > 0 ? this.jobs[0].job_id : '';
-                            if (!this.selectedJobId) this.logs = [];
-                        }
-                        this.notifyDramaJobCompletion();
-                        this.syncDramaDownloadProgress();
-                        this.syncCreativeJobProgress();
-                        this.syncBreakdownGenerationProgress();
-                    } catch (e) {
-                        this.jobs = [];
-                        this.isRunning = false;
-                    }
-                },
-
-                async fetchLogs() {
-                    try {
-                        if (this.selectedJobId) {
-                            const res = await fetch(`/api/jobs/${this.selectedJobId}/logs`);
-                            if (res.status === 404) {
-                                this.logs = [];
-                                this.selectedJobId = '';
-                                await this.fetchJobs();
-                                if (!this.selectedJobId) {
-                                    const latest = await fetch('/api/logs');
-                                    const data = await latest.json();
-                                    this.logs = data.logs || [];
-                                    if (data.job_id) this.selectedJobId = data.job_id;
-                                    this.batchProgress = data.batch_progress || '';
-                                    this.isRunning = data.is_running || false;
-                                }
-                                return;
-                            }
-                            const data = await res.json();
-                            this.logs = data.logs || [];
-                            if (data.job_id && data.job_id !== this.selectedJobId) this.selectedJobId = data.job_id;
-                        } else {
-                            const res = await fetch('/api/logs');
-                            const data = await res.json();
-                            this.logs = data.logs || [];
-                            if (data.job_id) this.selectedJobId = data.job_id;
-                            this.batchProgress = data.batch_progress || '';
-                            this.isRunning = data.is_running || false;
-                        }
-                        this.$nextTick(() => {
-                            const el = document.getElementById('logConsole');
-                            if (el) el.scrollTop = el.scrollHeight;
-                        });
-                    } catch (e) {}
-                },
-
-                async selectJob(jobId) {
-                    this.selectedJobId = jobId;
-                    await this.fetchLogs();
-                },
-
-                isJobRunning(job) {
-                    return ['pending', 'running', 'stopping'].includes(job.status);
-                },
-
-                jobStatusClass(status) {
-                    if (status === 'succeeded') return 'bg-success';
-                    if (status === 'failed') return 'bg-danger';
-                    if (status === 'cancelled' || status === 'stopping') return 'bg-warning';
-                    if (status === 'running') return 'bg-primary';
-                    return 'bg-secondary';
-                },
-
-                logClass(log) {
-                    if (String(log).includes('[ERROR]')) return 'text-danger';
-                    if (String(log).includes('[WARNING]')) return 'text-warning';
-                    if (String(log).includes('[SUCCESS]')) return 'text-success';
-                    return 'text-light';
-                },
-
-                logsText() {
-                    return (this.logs || []).join('\n');
-                },
-
-                async copyLogs() {
-                    const text = this.logsText();
-                    if (!text) return;
-                    try {
-                        await navigator.clipboard.writeText(text);
-                        alert(`日志已复制，共 ${this.logs.length} 行`);
-                    } catch (e) {
-                        try {
-                            const textarea = document.createElement('textarea');
-                            textarea.value = text;
-                            textarea.style.position = 'fixed';
-                            textarea.style.opacity = '0';
-                            document.body.appendChild(textarea);
-                            textarea.select();
-                            const copied = document.execCommand('copy');
-                            document.body.removeChild(textarea);
-                            if (!copied) throw new Error('copy command failed');
-                            alert(`日志已复制，共 ${this.logs.length} 行`);
-                        } catch (fallbackError) {
-                            alert('复制失败，请手动选择日志内容复制');
-                        }
-                    }
-                },
-
-                downloadLogs() {
-                    const text = this.logsText();
-                    if (!text) return;
-                    const blob = new Blob([text + '\n'], {type: 'text/plain;charset=utf-8'});
-                    const url = URL.createObjectURL(blob);
-                    const link = document.createElement('a');
-                    const jobId = this.selectedJobId || 'latest';
-                    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
-                    link.href = url;
-                    link.download = `${jobId}-${stamp}.log`;
-                    document.body.appendChild(link);
-                    link.click();
-                    document.body.removeChild(link);
-                    URL.revokeObjectURL(url);
-                    alert('日志下载已开始');
-                },
-
-                selectedProjectCount() {
-                    return Object.values(this.config.projects || {}).filter(Boolean).length;
-                },
-
-                async runCrawler() {
-                    if (this.config.crawler_type === 'rank' && !['dy', 'hg'].includes(this.config.platform)) {
-                        alert('榜单爬取仅支持抖音和红果');
-                        return;
-                    }
-                    if (this.config.crawler_type === 'search') {
-                        const keywords = this.projectItems
-                            .filter(item => this.config.projects[item.project])
-                            .map(item => item['搜索关键词'] || item.keywords || '')
-                            .join(',');
-                        this.config.keywords = keywords;
-                    }
-                    this.currentTab = 'dashboard';
-                    try {
-                        const res = await fetch('/api/run', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify(this.config)
-                        });
-                        const data = await res.json();
-                        if (!res.ok || data.status === 'error') {
-                            alert(data.message);
-                            return;
-                        }
-                        if (data.job && data.job.job_id) this.selectedJobId = data.job.job_id;
-                        this.logs = ['[SYSTEM] 正在启动...'];
-                    } catch (e) {
-                        alert('启动失败');
-                        return;
-                    }
-                    await this.fetchJobs();
-                    await this.fetchLogs();
-                },
-
-                async runRankCrawler(platform) {
-                    const previousPlatform = this.config.platform;
-                    const previousType = this.config.crawler_type;
-                    this.config.platform = platform;
-                    this.config.crawler_type = 'rank';
-                    await this.runCrawler();
-                    this.config.platform = previousPlatform;
-                    this.config.crawler_type = previousType;
-                },
-
-                async stopJob(jobId) {
-                    await fetch(`/api/jobs/${jobId}/stop`, {method: 'POST'});
-                    await this.fetchJobs();
-                    await this.fetchLogs();
-                },
-
-                async stopCrawler() {
-                    if (this.selectedJobId) {
-                        await this.stopJob(this.selectedJobId);
-                        return;
-                    }
-                    await fetch('/api/stop', {method: 'POST'});
-                    await this.fetchJobs();
-                    await this.fetchLogs();
-                },
-
-                async deleteJob(jobId) {
-                    const job = this.jobs.find(item => item.job_id === jobId);
-                    if (job && this.isJobRunning(job)) {
-                        alert('Job is still running. Stop it first.');
-                        return;
-                    }
-                    if (!confirm(`Delete job ${jobId}? Logs will be removed from the task list.`)) return;
-                    const res = await fetch(`/api/jobs/${jobId}/delete`, {method: 'POST'});
-                    const data = await res.json().catch(() => ({}));
-                    if (!res.ok || data.status === 'error') {
-                        alert(data.message || 'Delete failed');
-                        return;
-                    }
-                    if (this.selectedJobId === jobId) {
-                        this.selectedJobId = '';
-                        this.logs = [];
-                    }
-                    this.jobs = this.jobs.filter(item => item.job_id !== jobId);
-                    await this.fetchJobs();
-                    await this.fetchLogs();
-                },
-
-                async fetchFiles() {
-                    try {
-                        const res = await fetch(`/api/files?filter=${this.fileFilter}`);
-                        this.files = await res.json();
-                        this.selectedFilePaths = this.selectedFilePaths.filter(path =>
-                            this.files.some(file => file.path === path)
-                        );
-                        this.applyFileSearch();
-                    } catch (e) {
-                        this.files = [];
-                        this.filteredFiles = [];
-                        this.selectedFilePaths = [];
-                    }
-                },
-
-                scrollToCrawlerSettings() {
-                    document.getElementById('crawlerSettings')?.scrollIntoView({behavior: 'smooth', block: 'start'});
-                },
-
-                async fetchDramaSettings() {
-                    try {
-                        const res = await fetch('/api/hongguo/drama/settings');
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.message || '读取设置失败');
-                        this.dramaAnalysis.settings = data;
-                        this.dramaAnalysis.activationCode = '';
-                    } catch (e) {
-                        this.dramaAnalysis.error = e.message || '读取剧目检索设置失败';
-                    }
-                },
-
-                async fetchCreativeTopics() {
-                    this.creative.loading = true;
-                    this.creative.error = '';
-                    try {
-                        const res = await fetch(`/api/creative/topics?platform=${encodeURIComponent(this.creative.platform)}`);
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || '读取选题失败');
-                        this.creative.items = data.items || [];
-                    } catch (e) {
-                        this.creative.error = e.message || '读取选题失败';
-                    } finally {
-                        this.creative.loading = false;
-                    }
-                },
-
-                async fetchTopicLibrary() {
-                    this.topicLibrary.loading = true;
-                    this.topicLibrary.error = '';
-                    try {
-                        const params = new URLSearchParams({limit: '500'});
-                        if (this.topicLibrary.platform) params.set('platform', this.topicLibrary.platform);
-                        if (this.topicLibrary.status) params.set('status', this.topicLibrary.status);
-                        if (this.topicLibrary.category.trim()) params.set('category', this.topicLibrary.category.trim());
-                        if (this.topicLibrary.minScore !== '') params.set('min_score', this.topicLibrary.minScore);
-                        if (this.topicLibrary.hasLocked !== '') params.set('has_locked_breakdown', this.topicLibrary.hasLocked);
-                        const res = await fetch(`/api/creative/topics?${params.toString()}`);
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || '读取选题库失败');
-                        this.topicLibrary.items = data.items || [];
-                    } catch (e) {
-                        this.topicLibrary.error = e.message || '读取选题库失败';
-                    } finally {
-                        this.topicLibrary.loading = false;
-                    }
-                },
-
-                topicStatusLabel(status) {
-                    return {pending_review: '待评估', adopted: '已采纳', rejected: '不跟进', archived: '已归档'}[status] || status || '-';
-                },
-
-                topicStatusClass(status) {
-                    return {adopted: 'bg-success', rejected: 'bg-danger', archived: 'bg-secondary', pending_review: 'bg-warning text-dark'}[status] || 'bg-secondary';
-                },
-
-                breakdownStatusLabel(topic) {
-                    if (!topic?.latest_breakdown_id) return '未拆解';
-                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
-                    if (topic.latest_breakdown_status === 'locked') return `已锁定${version}`;
-                    return topic.locked_breakdown_id
-                        ? `草稿${version} · 已有锁定版`
-                        : `草稿${version} · 待锁定`;
-                },
-
-                breakdownStatusClass(topic) {
-                    if (!topic?.latest_breakdown_id) return 'bg-secondary';
-                    if (topic.latest_breakdown_status === 'locked') return 'bg-success';
-                    return topic.locked_breakdown_id ? 'bg-info text-dark' : 'bg-warning text-dark';
-                },
-
-                creativeTopicOptionLabel(topic) {
-                    if (!topic?.latest_breakdown_id) return `${topic.title}（未拆解）`;
-                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
-                    if (!topic.locked_breakdown_id) return `${topic.title}（草稿${version}，需先锁定）`;
-                    if (topic.latest_breakdown_status === 'draft') return `${topic.title}（已有锁定版，最新草稿${version}）`;
-                    return `${topic.title}（已锁定${version}）`;
-                },
-
-                async fetchCreativeProjects(selectId = null) {
-                    this.creativeWorkspace.loading = true;
-                    this.creativeWorkspace.error = '';
-                    try {
-                        const [projectsResponse, topicsResponse] = await Promise.all([
-                            fetch('/api/creative/projects'),
-                            fetch('/api/creative/topics?status=adopted&limit=500')
-                        ]);
-                        const projectsData = await projectsResponse.json();
-                        const topicsData = await topicsResponse.json();
-                        if (!projectsResponse.ok) throw new Error(projectsData.detail || '读取创作项目失败');
-                        if (!topicsResponse.ok) throw new Error(topicsData.detail || '读取可用选题失败');
-                        this.creativeWorkspace.projects = projectsData.items || [];
-                        this.creativeWorkspace.eligibleTopics = topicsData.items || [];
-                        const currentId = this.creativeWorkspace.project?.id;
-                        const targetId = selectId
-                            || (this.creativeWorkspace.projects.some(item => item.id === currentId) ? currentId : this.creativeWorkspace.projects[0]?.id);
-                        if (targetId) await this.selectCreativeProject(targetId);
-                        else this.creativeWorkspace.project = null;
-                    } catch (e) {
-                        this.creativeWorkspace.error = e.message || '读取创作项目失败';
-                    } finally {
-                        this.creativeWorkspace.loading = false;
-                    }
-                },
-
-                async loadCreativeProjectBreakdowns(topicId) {
-                    const response = await fetch(`/api/creative/topics/${topicId}/breakdowns?status=locked`);
-                    const data = await response.json();
-                    if (!response.ok) throw new Error(data.detail || '读取拆解版本失败');
-                    this.creativeWorkspace.lockedBreakdowns = data.items || [];
-                    const selected = Number(this.creativeWorkspace.create.breakdown_id);
-                    if (!this.creativeWorkspace.lockedBreakdowns.some(item => Number(item.id) === selected)) {
-                        this.creativeWorkspace.create.breakdown_id = this.creativeWorkspace.lockedBreakdowns[0]?.id || '';
-                    }
-                },
-
-                async syncCreativeProjectSource() {
-                    const topic = this.creativeWorkspace.eligibleTopics.find(item => Number(item.id) === Number(this.creativeWorkspace.create.topic_id));
-                    if (!topic) {
-                        this.creativeWorkspace.lockedBreakdowns = [];
-                        this.creativeWorkspace.create.breakdown_id = '';
-                        return;
-                    }
-                    try {
-                        await this.loadCreativeProjectBreakdowns(topic.id);
-                    } catch (e) {
-                        this.creativeWorkspace.lockedBreakdowns = [];
-                        this.creativeWorkspace.create.breakdown_id = '';
-                        this.creativeWorkspace.error = e.message || '读取拆解版本失败';
-                        return;
-                    }
-                    if (!this.creativeWorkspace.create.name) this.creativeWorkspace.create.name = `${topic.title}创作项目`;
-                },
-
-                async openCreativeProjectFromTopic(topic) {
-                    if (topic.status !== 'adopted' || !topic.locked_breakdown_id) {
-                        this.topicLibrary.error = '请先采纳选题并锁定拆解版本';
-                        return;
-                    }
-                    this.currentTab = 'creative-projects';
-                    await this.fetchCreativeProjects();
-                    this.creativeWorkspace.create = {
-                        topic_id: topic.id, breakdown_id: '',
-                        name: `${topic.title}创作项目`, target_platform: '', target_episode_count: 20
-                    };
-                    await this.syncCreativeProjectSource();
-                },
-
-                async createCreativeProject() {
-                    await this.syncCreativeProjectSource();
-                    const source = this.creativeWorkspace.create;
-                    if (!source.topic_id || !source.breakdown_id) return;
-                    try {
-                        const res = await fetch('/api/creative/projects', {
-                            method: 'POST', headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify(source)
-                        });
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || '创建项目失败');
-                        this.creativeWorkspace.create = {topic_id: '', breakdown_id: '', name: '', target_platform: '', target_episode_count: 20};
-                        await this.fetchCreativeProjects(data.id);
-                    } catch (e) {
-                        this.creativeWorkspace.error = e.message || '创建项目失败';
-                    }
-                },
-
-                async selectCreativeProject(projectId) {
-                    try {
-                        const res = await fetch(`/api/creative/projects/${projectId}`);
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || '读取项目失败');
-                        this.creativeWorkspace.project = data;
-                        this.reconcileCreativeGenerationState(data);
-                        const candidates = this.latestProjectBlueprints();
-                        if (!candidates.some(item => item.id === this.creativeWorkspace.selectedBlueprintId)) {
-                            this.creativeWorkspace.selectedBlueprintId = data.locked_brief_id || candidates[0]?.id || null;
-                        }
-                        this.creativeWorkspace.selectedEpisode = Math.min(
-                            Math.max(1, Number(this.creativeWorkspace.selectedEpisode || 1)),
-                            Number(data.target_episode_count || 1)
-                        );
-                    } catch (e) {
-                        this.creativeWorkspace.error = e.message || '读取项目失败';
-                    }
-                },
-
-                creativeProjectStatusLabel(status) {
-                    return {
-                        created: '待生成方向', direction_review: '方向评审', brief_locked: 'Brief 已锁定',
-                        characters_draft: '人物草稿', characters_locked: '人物已锁定', outline_draft: '大纲草稿',
-                        outline_locked: '大纲已锁定', beats_in_progress: 'Beats 制作中', archived: '已归档'
-                    }[status] || status || '-';
-                },
-
-                riskLabel(level) { return {low: '低', medium: '中', high: '高'}[level] || level || '-'; },
-                riskBadgeClass(level) { return level === 'high' ? 'bg-danger' : level === 'medium' ? 'bg-warning text-dark' : 'bg-success'; },
-                listText(value) { return Array.isArray(value) ? value.join('\n') : String(value || ''); },
-                setListField(target, key, value) { target[key] = String(value || '').split(/[,，\n]/).map(item => item.trim()).filter(Boolean); },
-                textareaRows(value, charactersPerLine = 28) {
-                    const lines = String(value || '').split('\n');
-                    return Math.max(2, lines.reduce((total, line) => total + Math.max(1, Math.ceil(line.length / charactersPerLine)), 0));
-                },
-                creativeItemSaveLabel(item, saveKey) {
-                    const state = this.creativeWorkspace.saveState[saveKey];
-                    if (state === 'saving') return '保存中';
-                    return item?.updated_at ? '已保存' : '未保存';
-                },
-                showCreativeNotice(message, type = 'success') {
-                    clearTimeout(this.creativeWorkspace.noticeTimer);
-                    this.creativeWorkspace.notice = message;
-                    this.creativeWorkspace.noticeType = type;
-                    this.creativeWorkspace.noticeTimer = setTimeout(() => {
-                        this.creativeWorkspace.notice = '';
-                    }, 3000);
-                },
-
-                latestProjectBlueprints() {
-                    const items = this.creativeWorkspace.project?.blueprints || [];
-                    const latest = new Map();
-                    items.forEach(item => {
-                        const current = latest.get(item.candidate_group_id);
-                        if (!current || Number(item.version) > Number(current.version)) latest.set(item.candidate_group_id, item);
-                    });
-                    return [...latest.values()];
-                },
-
-                selectedProjectBlueprint() {
-                    return (this.creativeWorkspace.project?.blueprints || []).find(item => item.id === this.creativeWorkspace.selectedBlueprintId) || null;
-                },
-
-                latestCharacterBatchId() {
-                    const project = this.creativeWorkspace.project;
-                    if (!project) return '';
-                    if (project.character_batch_id) return project.character_batch_id;
-                    const items = project.characters || [];
-                    return items.length ? items[items.length - 1].batch_id : '';
-                },
-
-                currentProjectCharacters() {
-                    const batch = this.latestCharacterBatchId();
-                    return (this.creativeWorkspace.project?.characters || []).filter(item => item.batch_id === batch);
-                },
-
-                currentProjectRelationships() {
-                    const batch = this.latestCharacterBatchId();
-                    return (this.creativeWorkspace.project?.relationships || []).filter(item => item.batch_id === batch);
-                },
-
-                latestOutlineBatchId() {
-                    const project = this.creativeWorkspace.project;
-                    if (!project) return '';
-                    if (project.outline_batch_id) return project.outline_batch_id;
-                    const items = (project.episodes || []).filter(item => item.stage === 'outline');
-                    return items.length ? items[items.length - 1].batch_id : '';
-                },
-
-                currentProjectOutlines() {
-                    const batch = this.latestOutlineBatchId();
-                    return (this.creativeWorkspace.project?.episodes || [])
-                        .filter(item => item.stage === 'outline' && item.batch_id === batch)
-                        .sort((a, b) => Number(a.episode_number) - Number(b.episode_number));
-                },
-
-                selectedProjectBeats() {
-                    const items = (this.creativeWorkspace.project?.episodes || [])
-                        .filter(item => item.stage === 'beats'
-                            && Number(item.episode_number) === Number(this.creativeWorkspace.selectedEpisode)
-                            && item.upstream_refs?.outline_batch_id === this.creativeWorkspace.project?.outline_batch_id)
-                        .sort((a, b) => Number(b.version) - Number(a.version));
-                    return items[0] || null;
-                },
-
-                async startCreativeGeneration(url, body = {}, action = '') {
-                    this.creativeWorkspace.error = '';
-                    this.creativeWorkspace.startingAction = action;
-                    this.creativeWorkspace.jobMessage = '正在创建生成任务';
-                    this.creativeWorkspace.jobId = '';
-                    this.creativeWorkspace.jobStatus = '';
-                    this.creativeWorkspace.jobProgress = {indeterminate: true};
-                    this.creativeWorkspace.jobStartedAt = Date.now();
-                    this.creativeWorkspace.jobElapsedSeconds = 0;
-                    this.creativeWorkspace.paused = false;
-                    this.creativeWorkspace.notifiedStatus = '';
-                    this.creativeWorkspace.activeAction = action;
-                    try {
-                        const res = await fetch(url, {
-                            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
-                        });
-                        const data = await res.json();
-                        if (!res.ok || data.status === 'error') throw new Error(data.detail || data.message || '创建生成任务失败');
-                        this.creativeWorkspace.jobId = data.job.job_id;
-                        this.creativeWorkspace.jobStatus = data.job.status;
-                        this.creativeWorkspace.jobMessage = data.job.message || '任务已在后台启动';
-                        this.creativeWorkspace.jobProgress = data.job.progress || {};
-                        if (Number(data.job.started_at || 0) > 0) {
-                            this.creativeWorkspace.jobStartedAt = Number(data.job.started_at) * 1000;
-                        }
-                        await this.fetchJobs();
-                    } catch (e) {
-                        this.creativeWorkspace.error = e.message || '创建生成任务失败';
-                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
-                    } finally {
-                        this.creativeWorkspace.startingAction = '';
-                    }
-                },
-
-                generateProjectBlueprints() {
-                    return this.startCreativeGeneration(
-                        `/api/creative/projects/${this.creativeWorkspace.project.id}/blueprints/generate`,
-                        {count: Number(this.creativeWorkspace.directionCount || 3)},
-                        'blueprints'
-                    );
-                },
-
-                generateProjectCharacters() {
-                    return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/characters/generate`, {}, 'characters');
-                },
-
-                generateProjectOutlines() {
-                    return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/outlines/generate`, {}, 'outlines');
-                },
-
-                generateProjectBeats() {
-                    const episode = Number(this.creativeWorkspace.selectedEpisode || 0);
-                    return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/${episode}/beats/generate`, {}, 'beats');
-                },
-
-                creativeActionRunning(action) {
-                    return this.creativeWorkspace.startingAction === action
-                        || (this.creativeWorkspace.activeAction === action && this.creativeJobRunning());
-                },
-
-                creativeActionDisabled(action) {
-                    return Boolean(this.creativeWorkspace.startingAction)
-                        || (this.creativeWorkspace.activeAction === action && this.creativeJobRunning());
-                },
-
-                creativeProjectHasJobResult(project, action, jobId) {
-                    if (!project || !action || !jobId) return false;
-                    const generatedByJob = item => item.generation_meta?.job_id === jobId;
-                    if (action === 'blueprints') return (project.blueprints || []).some(generatedByJob);
-                    if (action === 'characters') {
-                        return (project.characters || []).some(generatedByJob)
-                            || (project.relationships || []).some(generatedByJob);
-                    }
-                    if (action === 'outlines') {
-                        return (project.episodes || []).some(item => item.stage === 'outline' && item.generation_meta?.job_id === jobId);
-                    }
-                    if (action === 'beats') {
-                        return (project.episodes || []).some(item => item.stage === 'beats' && item.generation_meta?.job_id === jobId);
-                    }
-                    return false;
-                },
-
-                reconcileCreativeGenerationState(project) {
-                    const action = this.creativeWorkspace.activeAction;
-                    const jobId = this.creativeWorkspace.jobId;
-                    if (!this.creativeJobStatusRunning() || !this.creativeProjectHasJobResult(project, action, jobId)) return false;
-                    this.creativeWorkspace.jobStatus = 'succeeded';
-                    this.creativeWorkspace.jobMessage = '生成完成，结果已回填到当前项目';
-                    this.creativeWorkspace.jobProgress = {completed: 1, total: 1};
-                    this.creativeWorkspace.startingAction = '';
-                    this.creativeWorkspace.paused = false;
-                    this.creativeWorkspace.notifiedStatus = 'succeeded';
-                    this.showCreativeNotice('生成完成，结果已回填到当前项目。');
-                    return true;
-                },
-
-                syncCreativeJobProgress() {
-                    if (!this.creativeWorkspace.jobId) return;
-                    const job = this.jobs.find(item => item.job_id === this.creativeWorkspace.jobId);
-                    if (!job) return;
-                    this.creativeWorkspace.jobStatus = job.status;
-                    this.creativeWorkspace.jobMessage = job.message || this.creativeWorkspace.jobMessage;
-                    this.creativeWorkspace.jobProgress = job.progress || {};
-                    this.creativeWorkspace.paused = Boolean(job.paused);
-                    if (Number(job.started_at || 0) > 0) {
-                        this.creativeWorkspace.jobStartedAt = Number(job.started_at) * 1000;
-                    }
-                    this.creativeWorkspace.jobElapsedSeconds = Math.max(
-                        Number(this.creativeWorkspace.jobElapsedSeconds || 0),
-                        Number(job.progress?.elapsed_seconds || 0)
-                    );
-                    if (this.reconcileCreativeGenerationState(this.creativeWorkspace.project)) return;
-                    if (['succeeded', 'failed', 'cancelled'].includes(job.status) && this.creativeWorkspace.notifiedStatus !== job.status) {
-                        this.creativeWorkspace.notifiedStatus = job.status;
-                        if (this.creativeWorkspace.project?.id) this.selectCreativeProject(this.creativeWorkspace.project.id);
-                        alert(job.status === 'succeeded' ? '创作内容生成完成，结果已回填到当前项目。' : `创作任务${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || ''}`);
-                    }
-                },
-
-                creativeJobStatusRunning() {
-                    return ['pending', 'running', 'stopping'].includes(this.creativeWorkspace.jobStatus);
-                },
-                creativeJobRunning() {
-                    if (!this.creativeJobStatusRunning()) return false;
-                    return !this.creativeProjectHasJobResult(
-                        this.creativeWorkspace.project,
-                        this.creativeWorkspace.activeAction,
-                        this.creativeWorkspace.jobId
-                    );
-                },
-                creativeJobRetryable() { return ['failed', 'cancelled'].includes(this.creativeWorkspace.jobStatus); },
-                creativeProgressIndeterminate() {
-                    const progress = this.creativeWorkspace.jobProgress || {};
-                    return Boolean(this.creativeWorkspace.startingAction || this.creativeJobRunning())
-                        && (progress.indeterminate || (Number(progress.completed || 0) === 0 && Number(progress.total || 0) <= 1));
-                },
-                creativeProgressPercent() {
-                    const progress = this.creativeWorkspace.jobProgress || {};
-                    const total = Number(progress.total || 0);
-                    return total ? Math.min(100, Math.round(Number(progress.completed || 0) * 100 / total)) : 0;
-                },
-                creativeProgressText() {
-                    const progress = this.creativeWorkspace.jobProgress || {};
-                    if (this.creativeWorkspace.startingAction && !this.creativeWorkspace.jobId) return '正在启动';
-                    if (this.creativeProgressIndeterminate()) return this.creativeWorkspace.paused ? '已暂停' : '处理中';
-                    return progress.total ? `${progress.completed || 0}/${progress.total}` : this.creativeWorkspace.jobStatus;
-                },
-                creativeProgressElapsedText() {
-                    const seconds = Number(this.creativeWorkspace.jobElapsedSeconds || 0);
-                    const prefix = this.creativeWorkspace.startingAction || this.creativeJobRunning() ? '已等待' : '用时';
-                    return `${prefix} ${this.formatBreakdownGenerationElapsed(seconds)}`;
-                },
-                tickCreativeGenerationElapsed() {
-                    if (!this.creativeWorkspace.startingAction && !this.creativeJobRunning()) return;
-                    const startedAt = Number(this.creativeWorkspace.jobStartedAt || 0);
-                    if (!startedAt) return;
-                    this.creativeWorkspace.jobElapsedSeconds = Math.max(
-                        Number(this.creativeWorkspace.jobElapsedSeconds || 0),
-                        Math.floor((Date.now() - startedAt) / 1000)
-                    );
-                },
-
-                async pauseCreativeJob() {
-                    const res = await fetch(`/api/jobs/${this.creativeWorkspace.jobId}/pause`, {method: 'POST'});
-                    const data = await res.json();
-                    if (!res.ok || data.status === 'error') { this.creativeWorkspace.error = data.message || data.detail || '暂停失败'; return; }
-                    this.creativeWorkspace.paused = true;
-                },
-
-                async resumeCreativeJob() {
-                    const res = await fetch(`/api/jobs/${this.creativeWorkspace.jobId}/resume`, {method: 'POST'});
-                    const data = await res.json();
-                    if (!res.ok || data.status === 'error') { this.creativeWorkspace.error = data.message || data.detail || '继续失败'; return; }
-                    this.creativeWorkspace.paused = false;
-                },
-
-                async cancelCreativeJob() {
-                    if (!confirm('确定取消当前创作任务吗？尚未完成的版本不会写入项目。')) return;
-                    await fetch(`/api/jobs/${this.creativeWorkspace.jobId}/stop`, {method: 'POST'});
-                    await this.fetchJobs();
-                },
-
-                async retryCreativeJob() {
-                    const res = await fetch(`/api/jobs/${this.creativeWorkspace.jobId}/retry`, {method: 'POST'});
-                    const data = await res.json();
-                    if (!res.ok || data.status === 'error') { this.creativeWorkspace.error = data.detail || data.message || '重试失败'; return; }
-                    this.creativeWorkspace.jobId = data.job.job_id;
-                    this.creativeWorkspace.jobStatus = data.job.status;
-                    this.creativeWorkspace.notifiedStatus = '';
-                    await this.fetchJobs();
-                },
-
-                async saveCreativeProjectSettings() {
-                    const project = this.creativeWorkspace.project;
-                    if (!project) return;
-                    const payload = {
-                        name: project.name, genre: project.genre || '', target_platform: project.target_platform || '',
-                        target_audience: project.target_audience || '', target_episode_count: Number(project.target_episode_count),
-                        episode_duration_seconds: Number(project.episode_duration_seconds)
-                    };
-                    const res = await fetch(`/api/creative/projects/${project.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
-                    const data = await res.json();
-                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '保存 Brief 参数失败'; return; }
-                    this.creativeWorkspace.project = data;
-                    await this.fetchCreativeProjects(data.id);
-                },
-
-                async saveProjectBlueprint() {
-                    const item = this.selectedProjectBlueprint();
-                    if (!item) return;
-                    const res = await fetch(`/api/creative/blueprints/${item.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content: item.content, difference_note: item.difference_note || ''})});
-                    const data = await res.json();
-                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '保存方向失败'; return; }
-                    Object.assign(item, data);
-                },
-
-                async createProjectBlueprintVersion() {
-                    const item = this.selectedProjectBlueprint();
-                    if (!item) return;
-                    const res = await fetch(`/api/creative/blueprints/${item.id}/versions`, {method: 'POST'});
-                    const data = await res.json();
-                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '创建新版本失败'; return; }
-                    await this.selectCreativeProject(this.creativeWorkspace.project.id);
-                    this.creativeWorkspace.selectedBlueprintId = data.id;
-                },
-
-                async lockProjectBrief() {
-                    const item = this.selectedProjectBlueprint();
-                    if (!item) return;
-                    const res = await fetch(`/api/creative/blueprints/${item.id}/lock-brief`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({difference_note: item.difference_note || ''})});
-                    const data = await res.json();
-                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '锁定 Brief 失败'; return; }
-                    await this.selectCreativeProject(this.creativeWorkspace.project.id);
-                    this.creativeWorkspace.step = 'characters';
-                },
-
-                async createBlankCharacterBatch() {
-                    const projectId = this.creativeWorkspace.project.id;
-                    const blank = {name: '新角色', age_range: '', identity: '', external_tags: [], desire: '', secret: '', growth_arc: '', dialogue_style: '', appearance_prompt: '', voice_prompt: ''};
-                    const res = await fetch(`/api/creative/projects/${projectId}/characters`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({characters: [blank], relationships: []})});
-                    const data = await res.json();
-                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '创建空白人物版本失败'; return; }
-                    await this.selectCreativeProject(projectId);
-                },
-
-                async saveProjectCharacter(item) { await this.saveCreativeItem(`/api/creative/characters/${item.id}`, item, '人物卡已保存', `character-${item.id}`); },
-                async saveProjectRelationship(item) { await this.saveCreativeItem(`/api/creative/relationships/${item.id}`, item, '人物关系已保存', `relationship-${item.id}`); },
-                async saveProjectEpisode(item) { await this.saveCreativeItem(`/api/creative/episodes/${item.id}`, item, '分集内容已保存', `episode-${item.id}`); },
-                async saveCreativeItem(url, item, successMessage, saveKey) {
-                    this.creativeWorkspace.error = '';
-                    this.creativeWorkspace.saveState[saveKey] = 'saving';
-                    try {
-                        const res = await fetch(url, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content: item.content})});
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || '保存失败');
-                        Object.assign(item, data);
-                        this.creativeWorkspace.saveState[saveKey] = 'saved';
-                        this.showCreativeNotice(successMessage || '保存成功');
-                        setTimeout(() => {
-                            if (this.creativeWorkspace.saveState[saveKey] === 'saved') this.creativeWorkspace.saveState[saveKey] = '';
-                        }, 1800);
-                    } catch (e) {
-                        this.creativeWorkspace.saveState[saveKey] = '';
-                        this.creativeWorkspace.error = `${successMessage || '保存'}失败：${e.message || '请稍后重试'}`;
-                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
-                    }
-                },
-
-                async lockProjectCharacters() {
-                    const projectId = this.creativeWorkspace.project.id;
-                    const batchId = this.latestCharacterBatchId();
-                    if (!batchId) return;
-                    const res = await fetch(`/api/creative/projects/${projectId}/characters/${batchId}/lock`, {method: 'POST'});
-                    const data = await res.json();
-                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '锁定人物关系失败'; return; }
-                    await this.selectCreativeProject(projectId);
-                    this.creativeWorkspace.step = 'outlines';
-                },
-
-                async lockProjectOutlines() {
-                    const projectId = this.creativeWorkspace.project.id;
-                    const batchId = this.latestOutlineBatchId();
-                    if (!batchId) return;
-                    const res = await fetch(`/api/creative/projects/${projectId}/outlines/${batchId}/lock`, {method: 'POST'});
-                    const data = await res.json();
-                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '锁定分集大纲失败'; return; }
-                    await this.selectCreativeProject(projectId);
-                    this.creativeWorkspace.step = 'beats';
-                },
-
-                async lockProjectBeats() {
-                    const item = this.selectedProjectBeats();
-                    if (!item) return;
-                    const res = await fetch(`/api/creative/episodes/${item.id}/lock`, {method: 'POST'});
-                    const data = await res.json();
-                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '锁定 Beats 失败'; return; }
-                    await this.selectCreativeProject(this.creativeWorkspace.project.id);
-                },
-
-                creativeExportUrl(stage, format = 'md') {
-                    const id = this.creativeWorkspace.project?.id || '';
-                    return `/api/creative/projects/${id}/export?stage=${encodeURIComponent(stage)}&format=${encodeURIComponent(format)}`;
-                },
-
-                async updateTopicLibrary(topic, status) {
-                    const res = await fetch(`/api/creative/topics/${topic.id}`, {
-                        method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status})
-                    });
-                    const data = await res.json();
-                    if (!res.ok) { this.topicLibrary.error = data.detail || '更新选题状态失败'; return; }
-                    Object.assign(topic, data);
-                },
-
-                createTopicLibraryBreakdown(topic) {
-                    return this.startTranscriptBreakdown(topic, 'topic-library');
-                },
-
-                async startTranscriptBreakdown(topic, source, forceRegenerate = false) {
-                    if (!topic?.id) return;
-                    this.breakdownGeneration = {
-                        topic,
-                        source,
-                        jobId: '',
-                        status: 'pending',
-                        message: '正在检查已下载的剧目脚本',
-                        progress: {completed: 0, total: 1},
-                        coverage: [],
-                        notifiedStatus: '',
-                        error: '',
-                        startedAt: Date.now(),
-                        elapsedSeconds: 0
-                    };
-                    try {
-                        const res = await fetch('/api/creative/breakdowns', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({topic_id: topic.id, force_regenerate: forceRegenerate})
-                        });
-                        const data = await res.json();
-                        const detail = data.detail;
-                        if (res.status === 409 && detail?.code === 'transcript_required') {
-                            this.breakdownGeneration.status = 'blocked';
-                            this.breakdownGeneration.message = detail.message;
-                            this.transcriptRequiredModal.show();
-                            return;
-                        }
-                        if (!res.ok || data.status === 'error') {
-                            throw new Error(typeof detail === 'string' ? detail : detail?.message || data.message || '生成拆解失败');
-                        }
-                        if (data.status === 'existing' && data.breakdown) {
-                            this.breakdownGeneration.status = 'existing';
-                            this.breakdownGeneration.message = '已加载最新拆解版本';
-                            this.breakdownGeneration.startedAt = 0;
-                            if (source === 'topic-library') this.topicLibrary.breakdown = data.breakdown;
-                            else this.creative.breakdown = data.breakdown;
-                            this.openBreakdownEditor(source, topic.title, data.breakdown);
-                            return;
-                        }
-                        this.breakdownGeneration.jobId = data.job.job_id;
-                        this.breakdownGeneration.status = data.job.status || 'pending';
-                        this.breakdownGeneration.message = data.job.message || '已加载剧目脚本，正在生成个性化拆解';
-                        this.breakdownGeneration.progress = data.job.progress || {completed: 0, total: 1};
-                        this.breakdownGeneration.coverage = data.coverage || [];
-                        this.breakdownGeneration.startedAt = Number(data.job.started_at || 0) > 0
-                            ? Number(data.job.started_at) * 1000
-                            : Date.now();
-                        this.breakdownGenerationProgressModal.show();
-                        await this.fetchJobs();
-                    } catch (e) {
-                        const message = e.message || '生成拆解失败';
-                        this.breakdownGeneration.status = 'failed';
-                        this.breakdownGeneration.message = message;
-                        this.breakdownGeneration.error = message;
-                        if (source === 'topic-library') this.topicLibrary.error = message;
-                        else this.creative.error = message;
-                        this.breakdownGenerationProgressModal.show();
-                    }
-                },
-
-                async syncBreakdownGenerationProgress() {
-                    if (!this.breakdownGeneration.jobId) return;
-                    const job = this.jobs.find(item => item.job_id === this.breakdownGeneration.jobId);
-                    if (!job) return;
-                    this.breakdownGeneration.status = job.status;
-                    this.breakdownGeneration.message = job.message || this.breakdownGeneration.message;
-                    this.breakdownGeneration.progress = job.progress || this.breakdownGeneration.progress || {};
-                    if (!this.breakdownGeneration.startedAt && Number(job.started_at || 0) > 0) {
-                        this.breakdownGeneration.startedAt = Number(job.started_at) * 1000;
-                    }
-                    this.breakdownGeneration.elapsedSeconds = Math.max(
-                        Number(this.breakdownGeneration.elapsedSeconds || 0),
-                        Number(job.progress?.elapsed_seconds || 0)
-                    );
-                    if (job.progress?.coverage?.length) this.breakdownGeneration.coverage = job.progress.coverage;
-                    if (!['succeeded', 'failed', 'cancelled'].includes(job.status) || this.breakdownGeneration.notifiedStatus === job.status) return;
-                    this.breakdownGeneration.notifiedStatus = job.status;
-                    if (job.status !== 'succeeded') {
-                        this.breakdownGeneration.error = `拆解生成${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || '请查看任务日志'}`;
-                        return;
-                    }
-                    let completedJob = job;
-                    if (!(completedJob.events || []).length) {
-                        const detailResponse = await fetch(`/api/jobs/${job.job_id}`);
-                        if (detailResponse.ok) completedJob = await detailResponse.json();
-                    }
-                    const finished = [...(completedJob.events || [])].reverse().find(event => event.event === 'job_finished' && event.payload?.breakdown_id);
-                    const breakdownId = finished?.payload?.breakdown_id;
-                    if (!breakdownId) {
-                        this.breakdownGeneration.error = '生成任务已结束，但未返回拆解版本，请查看任务日志。';
-                        return;
-                    }
-                    try {
-                        const res = await fetch(`/api/creative/breakdowns/${breakdownId}`);
-                        const record = await res.json();
-                        if (!res.ok) throw new Error(record.detail || '读取生成结果失败');
-                        if (this.breakdownGeneration.source === 'topic-library') {
-                            this.topicLibrary.breakdown = record;
-                            await this.fetchTopicLibrary();
-                        } else {
-                            this.creative.breakdown = record;
-                            await this.fetchCreativeTopics();
-                        }
-                        const source = this.breakdownGeneration.source;
-                        const title = this.breakdownGeneration.topic?.title || record.content?.title;
-                        this.breakdownGenerationProgressModal.hide();
-                        setTimeout(() => this.openBreakdownEditor(source, title, record), 200);
-                    } catch (e) {
-                        this.breakdownGeneration.error = e.message || '读取生成结果失败';
-                    }
-                },
-
-                breakdownGenerationRunning() {
-                    return ['pending', 'running', 'stopping'].includes(this.breakdownGeneration.status);
-                },
-
-                breakdownGenerationRetryable() {
-                    return ['failed', 'cancelled'].includes(this.breakdownGeneration.status);
-                },
-
-                breakdownGenerationIndeterminate() {
-                    if (!this.breakdownGenerationRunning()) return false;
-                    const progress = this.breakdownGeneration.progress || {};
-                    if (progress.indeterminate) return true;
-                    return Number(progress.completed || 0) === 0 && Number(progress.total || 0) <= 1;
-                },
-
-                breakdownGenerationProgressLabel() {
-                    if (!this.breakdownGenerationIndeterminate()) return `${this.breakdownGenerationProgressPercent()}%`;
-                    const seconds = Math.max(
-                        Number(this.breakdownGeneration.elapsedSeconds || 0),
-                        Number(this.breakdownGeneration.progress?.elapsed_seconds || 0)
-                    );
-                    return `已等待 ${this.formatBreakdownGenerationElapsed(seconds)}`;
-                },
-
-                formatBreakdownGenerationElapsed(seconds) {
-                    const safeSeconds = Math.max(0, Math.floor(Number(seconds || 0)));
-                    const minutes = Math.floor(safeSeconds / 60);
-                    const remainder = safeSeconds % 60;
-                    return minutes ? `${minutes}分${remainder}秒` : `${remainder}秒`;
-                },
-
-                tickBreakdownGenerationElapsed() {
-                    if (!this.breakdownGenerationRunning()) return;
-                    if (!this.breakdownGeneration.startedAt) this.breakdownGeneration.startedAt = Date.now();
-                    this.breakdownGeneration.elapsedSeconds = Math.max(
-                        Number(this.breakdownGeneration.elapsedSeconds || 0),
-                        Math.floor((Date.now() - this.breakdownGeneration.startedAt) / 1000)
-                    );
-                },
-
-                breakdownGenerationProgressPercent() {
-                    const progress = this.breakdownGeneration.progress || {};
-                    const total = Number(progress.total || 0);
-                    if (total) return Math.min(100, Math.round(Number(progress.completed || 0) * 100 / total));
-                    return this.breakdownGeneration.status === 'succeeded' ? 100 : 0;
-                },
-
-                async openBreakdownTaskLog() {
-                    const jobId = this.breakdownGeneration.jobId;
-                    this.breakdownGenerationProgressModal.hide();
-                    this.currentTab = 'dashboard';
-                    if (jobId) await this.selectJob(jobId);
-                },
-
-                regenerateCurrentBreakdown() {
-                    const record = this.breakdownEditor.record;
-                    if (!record?.topic_id || this.breakdownGenerationRunning()) return;
-                    if (!confirm('确定重新分析已下载脚本并生成一个新拆解版本吗？')) return;
-                    const topic = this.breakdownGeneration.topic?.id === record.topic_id
-                        ? this.breakdownGeneration.topic
-                        : {id: record.topic_id, title: this.breakdownEditor.title};
-                    const source = this.breakdownEditor.source || 'topic-library';
-                    this.breakdownEditorModal.hide();
-                    setTimeout(() => this.startTranscriptBreakdown(topic, source, true), 350);
-                },
-
-                async openTopicDownload(topic) {
-                    this.dramaDownload = {title: topic.title || '', topic, source: 'hongguo', fallbackReason: '', candidates: [], selectedCandidate: null, selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], selectedEpisodes: [], allEpisodes: false, searching: true, episodesLoading: false, starting: false, error: '', jobId: '', progress: {}, progressMessage: '', paused: false, finished: false, background: false};
-                    this.dramaDownloadModal.show();
-                    try {
-                        const res = await fetch('/api/hongguo/drama/download/search', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: this.dramaDownload.title})});
-                        const data = await res.json();
-                        this.dramaDownload.source = data.source || 'hongguo';
-                        this.dramaDownload.fallbackReason = data.fallback_reason || '';
-                        if (!res.ok || data.status === 'error') throw new Error(data.message || '剧目检索失败');
-                        this.dramaDownload.candidates = data.candidates || [];
-                        if (this.dramaDownload.source === 'douyin_fallback' && this.dramaDownload.candidates.length) {
-                            this.dramaDownload.selectedCandidate = this.dramaDownload.candidates[0];
-                            const firstDownloadable = this.dramaDownload.candidates.find(candidate => !candidate.is_paid);
-                            this.dramaDownload.selectedAwemeIds = firstDownloadable ? [firstDownloadable.aweme_id] : [];
-                            this.syncDouyinDownloadSelection();
-                        }
-                        if (!this.dramaDownload.candidates.length) this.dramaDownload.error = this.dramaDownload.source === 'douyin_fallback' ? '红果和抖音都没有找到可用视频，请确认剧名后重试' : '没有找到剧名完全匹配的红果剧目，请确认剧名后重试';
-                        this.dramaDownload.searching = false;
-                        // Hongguo has a book/episode flow, but Douyin fallback always
-                        // requires an explicit user choice to avoid downloading an
-                        // unrelated promotion or clip that happens to match the title.
-                        if (this.dramaDownload.candidates.length === 1 && this.dramaDownload.source !== 'douyin_fallback') await this.selectDramaDownloadCandidate(this.dramaDownload.candidates[0]);
-                    } catch (e) {
-                        this.dramaDownload.error = e.message || '剧目检索失败';
-                    } finally {
-                        this.dramaDownload.searching = false;
-                    }
-                },
-
-                async selectDramaDownloadCandidate(candidate) {
-                    this.dramaDownload.selectedCandidate = candidate;
-                    this.dramaDownload.episodes = [];
-                    this.dramaDownload.selectedEpisodes = [];
-                    this.dramaDownload.allEpisodes = false;
-                    if (this.dramaDownload.source === 'douyin_fallback') {
-                        this.dramaDownload.episodesLoading = false;
-                        this.dramaDownload.error = '';
-                        return;
-                    }
-                    this.dramaDownload.episodesLoading = true;
-                    this.dramaDownload.error = '';
-                    try {
-                        const res = await fetch('/api/hongguo/drama/episodes', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({book_id: candidate.book_id})});
-                        const data = await res.json();
-                        if (!res.ok || data.status === 'error') throw new Error(data.message || '读取剧集失败');
-                        this.dramaDownload.episodes = data.episodes || [];
-                    } catch (e) {
-                        this.dramaDownload.error = e.message || '读取剧集失败';
-                    } finally {
-                        this.dramaDownload.episodesLoading = false;
-                    }
-                },
-
-                toggleAllDramaEpisodes() {
-                    if (this.dramaDownload.allEpisodes) this.dramaDownload.selectedEpisodes = this.downloadableDramaEpisodes().map(item => item.episode);
-                    else this.dramaDownload.selectedEpisodes = [];
-                },
-
-                isDramaEpisodeDownloaded(episode) {
-                    return episode.download_status?.status === 'downloaded';
-                },
-
-                downloadableDramaEpisodes() {
-                    return this.dramaDownload.episodes.filter(episode => !this.isDramaEpisodeDownloaded(episode));
-                },
-
-                dramaSelectedEpisodesForDownload() {
-                    if (this.dramaDownload.allEpisodes) return this.downloadableDramaEpisodes().map(item => item.episode);
-                    return this.dramaDownload.selectedEpisodes.filter(episode => !this.dramaDownload.episodes.find(item => Number(item.episode) === Number(episode) && this.isDramaEpisodeDownloaded(item)));
-                },
-
-                toggleAllDouyinDownloadCandidates() {
-                    const downloadableIds = this.dramaDownload.candidates.filter(candidate => !candidate.is_paid).map(candidate => candidate.aweme_id);
-                    this.dramaDownload.selectedAwemeIds = this.dramaDownload.allDouyinCandidates ? downloadableIds : [];
-                },
-
-                syncDouyinDownloadSelection() {
-                    const downloadableIds = this.dramaDownload.candidates.filter(candidate => !candidate.is_paid).map(candidate => candidate.aweme_id);
-                    this.dramaDownload.allDouyinCandidates = downloadableIds.length > 0 && downloadableIds.every(id => this.dramaDownload.selectedAwemeIds.includes(id));
-                },
-
-                selectedDouyinEpisodeSummary() {
-                    const labels = this.dramaDownload.candidates
-                        .filter(candidate => this.dramaDownload.selectedAwemeIds.includes(candidate.aweme_id) && candidate.episode_label)
-                        .map(candidate => candidate.episode_label);
-                    const unique = [...new Set(labels)];
-                    return unique.length ? `集数：${unique.join('、')}` : '';
-                },
-
-                async startDramaDownload() {
-                    const isDouyinFallback = this.dramaDownload.source === 'douyin_fallback';
-                    const episodeNumbers = this.dramaSelectedEpisodesForDownload();
-                    if (isDouyinFallback ? !this.dramaDownload.selectedAwemeIds.length : !this.dramaDownload.selectedCandidate || !episodeNumbers.length) return;
-                    this.dramaDownload.starting = true;
-                    this.dramaDownload.error = '';
-                    this.dramaDownload.progressMessage = '正在创建下载任务…';
-                    this.dramaDownload.progress = {completed: 0, total: isDouyinFallback ? this.dramaDownload.selectedAwemeIds.length : episodeNumbers.length, percent: 0, downloaded: 0, skipped_paid: 0, failed: 0};
-                    try {
-                        const endpoint = isDouyinFallback ? '/api/hongguo/drama/douyin-fallback/jobs' : '/api/hongguo/drama/download/jobs';
-                        const payload = isDouyinFallback
-                            ? {title: this.dramaDownload.title, aweme_ids: this.dramaDownload.selectedAwemeIds, fallback_reason: this.dramaDownload.fallbackReason || ''}
-                            : {title: this.dramaDownload.title, book_id: this.dramaDownload.selectedCandidate.book_id, episodes: episodeNumbers, all_episodes: this.dramaDownload.allEpisodes && episodeNumbers.length === this.dramaDownload.episodes.length};
-                        const res = await fetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
-                        const data = await res.json();
-                        if (!res.ok || data.status === 'error') throw new Error(data.message || '创建下载任务失败');
-                        this.dramaDownload.jobId = data.job.job_id;
-                        this.dramaDownload.progress = data.job.progress || {};
-                        this.dramaDownload.progressMessage = isDouyinFallback ? '抖音降级下载任务已开始' : '下载任务已开始';
-                        await this.fetchJobs();
-                    } catch (e) {
-                        this.dramaDownload.error = e.message || '创建下载任务失败';
-                    } finally {
-                        this.dramaDownload.starting = false;
-                    }
-                },
-
-                syncDramaDownloadProgress() {
-                    if (!this.dramaDownload.jobId) return;
-                    const job = this.jobs.find(item => item.job_id === this.dramaDownload.jobId);
-                    if (!job) return;
-                    this.dramaDownload.paused = Boolean(job.paused);
-                    this.dramaDownload.progress = job.progress || this.dramaDownload.progress || {};
-                    this.dramaDownload.progressMessage = job.message || this.dramaDownload.progressMessage;
-                    if (job.status === 'succeeded') {
-                        if (!this.dramaDownload.finished) alert(`${job.message || (this.dramaDownload.source === 'douyin_fallback' ? '抖音降级视频解析完成' : '剧集下载完成')}，文件已保存到数据管理。`);
-                        this.dramaDownload.finished = true;
-                    } else if (['failed', 'cancelled'].includes(job.status)) {
-                        if (!this.dramaDownload.finished) alert(`剧集下载${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || ''}`);
-                        this.dramaDownload.finished = true;
-                    }
-                },
-
-                async pauseDramaDownload() {
-                    if (!this.dramaDownload.jobId) return;
-                    const res = await fetch(`/api/jobs/${this.dramaDownload.jobId}/pause`, {method: 'POST'});
-                    const data = await res.json();
-                    if (!res.ok || data.status === 'error') { this.dramaDownload.error = data.message || '暂停失败'; return; }
-                    this.dramaDownload.paused = true;
-                },
-
-                async resumeDramaDownload() {
-                    if (!this.dramaDownload.jobId) return;
-                    const res = await fetch(`/api/jobs/${this.dramaDownload.jobId}/resume`, {method: 'POST'});
-                    const data = await res.json();
-                    if (!res.ok || data.status === 'error') { this.dramaDownload.error = data.message || '继续失败'; return; }
-                    this.dramaDownload.paused = false;
-                },
-
-                async cancelDramaDownload() {
-                    if (!this.dramaDownload.jobId) return;
-                    if (!confirm('确定取消当前下载任务吗？已完成的集数会保留。')) return;
-                    await fetch(`/api/jobs/${this.dramaDownload.jobId}/stop`, {method: 'POST'});
-                },
-
-                async openDramaDownloadFiles() {
-                    this.dramaDownloadModal.hide();
-                    this.currentTab = 'data';
-                    await this.fetchFiles();
-                },
-
-                openBreakdownEditor(source, title, record) {
-                    const fieldGuide = record?.field_guide || [];
-                    this.breakdownEditor = {
-                        source,
-                        title: title || record?.content?.title || '未命名选题',
-                        record,
-                        content: this.normalizeBreakdownEditorContent(record?.content, fieldGuide),
-                        fieldGuide,
-                        saving: false,
-                        error: ''
-                    };
-                    this.breakdownEditorModal.show();
-                },
-
-                syncBreakdownEditorRecord(record) {
-                    const fieldGuide = record?.field_guide || this.breakdownEditor.fieldGuide || [];
-                    this.breakdownEditor.record = record;
-                    this.breakdownEditor.content = this.normalizeBreakdownEditorContent(record?.content, fieldGuide);
-                    this.breakdownEditor.fieldGuide = fieldGuide;
-                    if (this.breakdownEditor.source === 'topic-library') {
-                        this.topicLibrary.breakdown = record;
-                    } else {
-                        this.creative.breakdown = record;
-                    }
-                },
-
-                normalizeBreakdownEditorContent(content, fieldGuide) {
-                    const source = content && typeof content === 'object' ? content : {};
-                    return (fieldGuide || []).reduce((result, item) => {
-                        const value = source[item.key];
-                        result[item.key] = item.type === '数组'
-                            ? (Array.isArray(value) ? value.map(entry => String(entry ?? '')).filter(Boolean) : [])
-                            : String(value ?? '');
-                        return result;
-                    }, {});
-                },
-
-                addBreakdownArrayItem(key) {
-                    if (!Array.isArray(this.breakdownEditor.content[key])) this.breakdownEditor.content[key] = [];
-                    this.breakdownEditor.content[key].push('');
-                },
-
-                removeBreakdownArrayItem(key, index) {
-                    this.breakdownEditor.content[key]?.splice(index, 1);
-                },
-
-                async saveBreakdownEditor() {
-                    const record = this.breakdownEditor.record;
-                    if (!record || record.status === 'locked') return false;
-                    const content = this.normalizeBreakdownEditorContent(this.breakdownEditor.content, this.breakdownEditor.fieldGuide);
-                    this.breakdownEditor.saving = true;
-                    this.breakdownEditor.error = '';
-                    try {
-                        const res = await fetch(`/api/creative/breakdowns/${record.id}`, {
-                            method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content})
-                        });
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || '保存拆解失败');
-                        this.syncBreakdownEditorRecord(data);
-                        return true;
-                    } catch (e) {
-                        this.breakdownEditor.error = e.message || '保存拆解失败';
-                        return false;
-                    } finally {
-                        this.breakdownEditor.saving = false;
-                    }
-                },
-
-                async lockBreakdownEditor() {
-                    const record = this.breakdownEditor.record;
-                    if (!record || record.status === 'locked') return;
-                    if (!await this.saveBreakdownEditor()) return;
-                    this.breakdownEditor.saving = true;
-                    try {
-                        const res = await fetch(`/api/creative/breakdowns/${record.id}/lock`, {method: 'POST'});
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || '锁定拆解失败');
-                        this.syncBreakdownEditorRecord(data);
-                        if (this.breakdownEditor.source === 'topic-library') await this.fetchTopicLibrary();
-                        alert('拆解版本已锁定，后续修改需要创建新版本。');
-                    } catch (e) {
-                        this.breakdownEditor.error = e.message || '锁定拆解失败';
-                    } finally {
-                        this.breakdownEditor.saving = false;
-                    }
-                },
-
-                async saveTopicLibraryBreakdown() {
-                    return this.saveBreakdownEditor();
-                },
-
-                async lockTopicLibraryBreakdown() {
-                    return this.lockBreakdownEditor();
-                },
-
-                async generateCreativeTopics() {
-                    this.creative.loading = true;
-                    this.creative.error = '';
-                    try {
-                        const res = await fetch('/api/creative/topics/generate', {
-                            method: 'POST', headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({platform: this.creative.platform, limit: 10})
-                        });
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || '生成选题失败');
-                        this.creative.items = data.items || [];
-                    } catch (e) {
-                        this.creative.error = e.message || '生成选题失败';
-                    } finally {
-                        this.creative.loading = false;
-                    }
-                },
-
-                async updateCreativeTopic(topic, status) {
-                    const res = await fetch(`/api/creative/topics/${topic.id}`, {
-                        method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status})
-                    });
-                    const data = await res.json();
-                    if (!res.ok) { alert(data.detail || '更新失败'); return; }
-                    Object.assign(topic, data);
-                },
-
-                createCreativeBreakdown(topic) {
-                    return this.startTranscriptBreakdown(topic, 'creative');
-                },
-
-                async lockCreativeBreakdown() {
-                    return this.lockBreakdownEditor();
-                },
-
-                async saveDramaSettings() {
-                    this.dramaAnalysis.loading = true;
-                    this.dramaAnalysis.error = '';
-                    try {
-                        const payload = {config_url: this.dramaAnalysis.settings.config_url};
-                        if (this.dramaAnalysis.activationCode) payload.activation_code = this.dramaAnalysis.activationCode;
-                        const res = await fetch('/api/hongguo/drama/settings', {
-                            method: 'PUT',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify(payload)
-                        });
-                        const data = await res.json();
-                        if (!res.ok || data.status === 'error') throw new Error(data.message || '保存设置失败');
-                        this.dramaAnalysis.settings = data;
-                        this.dramaAnalysis.activationCode = '';
-                        alert('设置已保存');
-                    } catch (e) {
-                        this.dramaAnalysis.error = e.message || '保存设置失败';
-                    } finally {
-                        this.dramaAnalysis.loading = false;
-                    }
-                },
-
-                async testDramaInterface() {
-                    this.dramaAnalysis.loading = true;
-                    this.dramaAnalysis.error = '';
-                    this.dramaAnalysis.interfaceTest = null;
-                    try {
-                        const payload = {config_url: this.dramaAnalysis.settings.config_url};
-                        if (this.dramaAnalysis.activationCode) payload.activation_code = this.dramaAnalysis.activationCode;
-                        const res = await fetch('/api/hongguo/drama/test', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify(payload)
-                        });
-                        const data = await res.json();
-                        this.dramaAnalysis.interfaceTest = {
-                            status: data.status || (res.ok ? 'success' : 'error'),
-                            message: data.message || (res.ok ? '接口可用' : '接口不可用')
-                        };
-                    } catch (e) {
-                        this.dramaAnalysis.interfaceTest = {status: 'error', message: e.message || '接口测试失败'};
-                    } finally {
-                        this.dramaAnalysis.loading = false;
-                    }
-                },
-
-                async searchDrama() {
-                    const title = this.dramaAnalysis.title.trim();
-                    if (!title) return;
-                    clearTimeout(this.dramaAnalysis.suggestionTimer);
-                    this.dramaAnalysis.suggestionRequestId += 1;
-                    this.dramaAnalysis.suggestions = [];
-                    this.dramaAnalysis.loading = true;
-                    this.dramaAnalysis.error = '';
-                    this.dramaAnalysis.candidates = [];
-                    this.dramaAnalysis.selected = null;
-                    this.dramaAnalysis.episodes = [];
-                    this.dramaAnalysis.episode = 0;
-                    this.dramaAnalysis.searchSource = '';
-                    this.dramaAnalysis.fallbackReason = '';
-                    try {
-                        const res = await fetch('/api/hongguo/drama/search', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({title})
-                        });
-                        const data = await res.json();
-                        if (!res.ok || data.status === 'error') throw new Error(data.message || '搜索失败');
-                        this.dramaAnalysis.candidates = data.candidates || [];
-                        this.dramaAnalysis.searchSource = data.source || 'hongguo';
-                        this.dramaAnalysis.fallbackReason = data.fallback_reason || '';
-                        if (!this.dramaAnalysis.candidates.length) this.dramaAnalysis.error = '未找到匹配剧目';
-                    } catch (e) {
-                        this.dramaAnalysis.error = e.message || '搜索剧目失败';
-                    } finally {
-                        this.dramaAnalysis.loading = false;
-                    }
-                },
-
-                onDramaTitleInput() {
-                    clearTimeout(this.dramaAnalysis.suggestionTimer);
-                    const requestId = ++this.dramaAnalysis.suggestionRequestId;
-                    this.dramaAnalysis.suggestions = [];
-                    const title = this.dramaAnalysis.title.trim();
-                    if (title.length < 2) return;
-                    this.dramaAnalysis.suggestionTimer = setTimeout(async () => {
-                        this.dramaAnalysis.suggestionLoading = true;
-                        try {
-                            const res = await fetch('/api/hongguo/drama/suggest', {
-                                method: 'POST',
-                                headers: {'Content-Type': 'application/json'},
-                                body: JSON.stringify({title})
-                            });
-                            const data = await res.json();
-                            if (requestId !== this.dramaAnalysis.suggestionRequestId) return;
-                            this.dramaAnalysis.suggestions = res.ok && data.status !== 'error' ? (data.candidates || []) : [];
-                        } catch (e) {
-                            if (requestId === this.dramaAnalysis.suggestionRequestId) this.dramaAnalysis.suggestions = [];
-                        } finally {
-                            if (requestId === this.dramaAnalysis.suggestionRequestId) this.dramaAnalysis.suggestionLoading = false;
-                        }
-                    }, 350);
-                },
-
-                chooseDramaSuggestion(suggestion) {
-                    this.dramaAnalysis.title = suggestion.title || this.dramaAnalysis.title;
-                    this.dramaAnalysis.suggestions = [];
-                    this.searchDrama();
-                },
-
-                async selectDramaCandidate(candidate) {
-                    this.dramaAnalysis.error = '';
-                    this.dramaAnalysis.selected = candidate;
-                    this.dramaAnalysis.episodes = [];
-                    this.dramaAnalysis.episode = 0;
-                    if (this.dramaAnalysis.searchSource === 'douyin_fallback') return;
-                    this.dramaAnalysis.loading = true;
-                    try {
-                        const res = await fetch('/api/hongguo/drama/episodes', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({book_id: candidate.book_id})
-                        });
-                        const data = await res.json();
-                        if (!res.ok || data.status === 'error') throw new Error(data.message || '读取剧集失败');
-                        this.dramaAnalysis.episodes = data.episodes || [];
-                        this.dramaAnalysis.episode = this.dramaAnalysis.episodes[0]?.episode || 0;
-                    } catch (e) {
-                        this.dramaAnalysis.error = e.message || '读取剧集失败';
-                        this.dramaAnalysis.selected = null;
-                    } finally {
-                        this.dramaAnalysis.loading = false;
-                    }
-                },
-
-                async startDramaAnalysis() {
-                    const selected = this.dramaAnalysis.selected;
-                    const isDouyinFallback = this.dramaAnalysis.searchSource === 'douyin_fallback';
-                    if (!selected || (isDouyinFallback ? !selected.aweme_id : !this.dramaAnalysis.episode)) return;
-                    this.dramaAnalysis.loading = true;
-                    this.dramaAnalysis.error = '';
-                    try {
-                        const endpoint = isDouyinFallback
-                            ? '/api/hongguo/drama/douyin-fallback/jobs'
-                            : '/api/hongguo/drama/jobs';
-                        const jobPayload = isDouyinFallback
-                            ? {
-                                title: this.dramaAnalysis.title || selected.title,
-                                aweme_id: selected.aweme_id,
-                                fallback_reason: this.dramaAnalysis.fallbackReason
-                            }
-                            : {
-                                title: selected.title || this.dramaAnalysis.title,
-                                book_id: selected.book_id,
-                                episode: this.dramaAnalysis.episode
-                            };
-                        const res = await fetch(endpoint, {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify(jobPayload)
-                        });
-                        const data = await res.json();
-                        if (!res.ok || data.status === 'error') throw new Error(data.message || '创建下载任务失败');
-                        this.dramaAnalysis.jobId = data.job.job_id;
-                        this.dramaAnalysis.notifiedStatus = '';
-                        this.selectedJobId = data.job.job_id;
-                        await this.fetchJobs();
-                        await this.fetchLogs();
-                        alert(isDouyinFallback
-                            ? '抖音降级下载任务已创建，正在下载已确认的视频并生成转写。完成后会弹窗提醒。'
-                            : '剧集下载任务已创建，正在下载并生成转写。完成后会弹窗提醒。');
-                    } catch (e) {
-                        this.dramaAnalysis.error = e.message || '创建下载任务失败';
-                    } finally {
-                        this.dramaAnalysis.loading = false;
-                    }
-                },
-
-                dramaJobArtifacts() {
-                    return this.jobs.find(job => job.job_id === this.dramaAnalysis.jobId)?.artifacts || [];
-                },
-
-                formatDramaDuration(value) {
-                    const seconds = Math.max(0, Math.round(Number(value) / 1000));
-                    if (!seconds) return '未知';
-                    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
-                },
-
-                formatDramaBytes(value) {
-                    const bytes = Math.max(0, Number(value || 0));
-                    if (bytes < 1024) return `${bytes} B`;
-                    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
-                    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
-                },
-
-                notifyDramaJobCompletion() {
-                    const job = this.jobs.find(item => item.job_id === this.dramaAnalysis.jobId);
-                    if (!job || !['succeeded', 'failed', 'cancelled'].includes(job.status)) return;
-                    const marker = `${job.job_id}:${job.status}`;
-                    if (this.dramaAnalysis.notifiedStatus === marker) return;
-                    this.dramaAnalysis.notifiedStatus = marker;
-                    if (job.status === 'succeeded') {
-                        const artifactCount = (job.artifacts || []).length;
-                        const source = job.job?.crawler_type === 'douyin_fallback_analysis' ? '抖音降级视频' : '剧目';
-                        alert(`${source}下载与解析完成，已生成 ${artifactCount} 个本地文件。`);
-                        return;
-                    }
-                    alert(`剧目下载未完成：${job.message || job.status}`);
-                },
-
-                syncRagPlatform() {
-                    const job = this.jobs.find(item => item.job_id === this.ragIndex.job_id);
-                    if (job && ['dy', 'hg'].includes(job.platform)) this.ragIndex.platform = job.platform;
-                },
-
-                async fetchRagStatus() {
-                    try {
-                        const res = await fetch('/api/rag/status');
-                        this.ragStatus = await res.json();
-                    } catch (e) {
-                        this.ragStatus = {};
-                    }
-                },
-
-                async indexRagJob() {
-                    this.ragLoading = true;
-                    this.ragError = '';
-                    this.ragIndexResult = null;
-                    try {
-                        const res = await fetch('/api/rag/index', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify(this.ragIndex)
-                        });
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || data.message || '索引失败');
-                        this.ragIndexResult = data;
-                        await this.fetchRagStatus();
-                    } catch (e) {
-                        this.ragError = e.message || '索引失败';
-                    } finally {
-                        this.ragLoading = false;
-                    }
-                },
-
-                async queryRag() {
-                    this.ragLoading = true;
-                    this.ragError = '';
-                    this.ragAnswer = {};
-                    try {
-                        const filters = {};
-                        if (this.ragIndex.platform) filters.platform = [this.ragIndex.platform];
-                        if (this.ragIndex.job_id) filters.job_id = [this.ragIndex.job_id];
-                        const res = await fetch('/api/rag/query', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({question: this.ragQuestion, filters, top_k: this.ragTopK})
-                        });
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || data.message || '查询失败');
-                        this.ragAnswer = data;
-                    } catch (e) {
-                        this.ragError = e.message || '查询失败';
-                    } finally {
-                        this.ragLoading = false;
-                    }
-                },
-
-                selectedJobPlatform() {
-                    const job = this.jobs.find(item => item.job_id === this.selectedJobId);
-                    if (job && ['dy', 'hg'].includes(job.platform)) return job.platform;
-                    return 'dy';
-                },
-
-                async syncAnalyticsJob() {
-                    if (!this.selectedJobId) {
-                        this.analyticsError = '请选择一个 Job';
-                        return;
-                    }
-                    this.analyticsLoading = true;
-                    this.analyticsError = '';
-                    this.analyticsSyncResult = null;
-                    try {
-                        const res = await fetch('/api/analytics/sync', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({
-                                job_id: this.selectedJobId,
-                                platform: this.selectedJobPlatform(),
-                                rebuild: true
-                            })
-                        });
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || data.message || '同步失败');
-                        this.analyticsSyncResult = data;
-                        await this.reloadAnalyticsDashboard();
-                    } catch (e) {
-                        this.analyticsError = e.message || '同步失败';
-                    } finally {
-                        this.analyticsLoading = false;
-                    }
-                },
-
-                async fetchAnalyticsAssets() {
-                    try {
-                        const res = await fetch('/api/analytics/assets');
-                        this.analyticsAssets = await res.json();
-                    } catch (e) {
-                        this.analyticsAssets = {overview: {}, jobs: [], platform_distribution: []};
-                    }
-                },
-
-                async refreshAnalyticsDashboard(syncCurrent = true) {
-                    this.analyticsError = '';
-                    if (syncCurrent) {
-                        this.analyticsLoading = true;
-                        try {
-                            const platforms = this.analyticsPlatform ? [this.analyticsPlatform] : ['dy', 'hg'];
-                            const res = await fetch('/api/analytics/sync-all', {
-                                method: 'POST',
-                                headers: {'Content-Type': 'application/json'},
-                                body: JSON.stringify({platforms})
-                            });
-                            const data = await res.json();
-                            if (!res.ok) throw new Error(data.detail || data.message || '同步失败');
-                            this.analyticsSyncResult = {
-                                status: data.status,
-                                job_id: this.analyticsPlatform ? `${this.analyticsPlatform} 全部任务 ${data.synced_jobs || 0} 个` : `全部任务 ${data.synced_jobs || 0} 个`,
-                                drama_count: data.drama_count || 0,
-                                artifact_count: data.artifact_count || 0
-                            };
-                        } catch (e) {
-                            this.analyticsError = e.message || '同步失败';
-                        } finally {
-                            this.analyticsLoading = false;
-                        }
-                    }
-                    await this.reloadAnalyticsDashboard();
-                },
-
-                async syncAllAnalytics() {
-                    this.analyticsLoading = true;
-                    this.analyticsError = '';
-                    try {
-                        const res = await fetch('/api/analytics/sync-all', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({platforms: ['dy', 'hg']})
-                        });
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || data.message || '同步全部失败');
-                        this.analyticsSyncResult = {
-                            status: data.status,
-                            job_id: `全部任务 ${data.synced_jobs || 0} 个`,
-                            drama_count: data.drama_count || 0,
-                            artifact_count: data.artifact_count || 0
-                        };
-                        this.analyticsPlatform = '';
-                        await this.reloadAnalyticsDashboard();
-                    } catch (e) {
-                        this.analyticsError = e.message || '同步全部失败';
-                    } finally {
-                        this.analyticsLoading = false;
-                    }
-                },
-
-                async reloadAnalyticsDashboard() {
-                    const baseParams = [];
-                    if (this.analyticsPlatform) baseParams.push(`platform=${encodeURIComponent(this.analyticsPlatform)}`);
-                    const nonce = `_=${Date.now()}`;
-                    const commonSuffix = `?${[...baseParams, nonce].join('&')}`;
-                    const dramaParams = [...baseParams, nonce];
-                    if (this.analyticsDate) dramaParams.push(`date=${encodeURIComponent(this.analyticsDate)}`);
-                    const dramaSuffix = `?${dramaParams.join('&')}`;
-                    try {
-                        const [overviewRes, tagsRes, trendsRes, sentimentRes, dramasRes] = await Promise.all([
-                            fetch(`/api/analytics/overview${commonSuffix}`),
-                            fetch(`/api/analytics/tags${commonSuffix}`),
-                            fetch(`/api/analytics/trends${commonSuffix}&metric=heat`),
-                            fetch(`/api/analytics/sentiment${commonSuffix}`),
-                            fetch(`/api/analytics/dramas${dramaSuffix}&sort=heat&limit=30`)
-                        ]);
-                        this.analyticsOverview = await overviewRes.json();
-                        this.analyticsTags = await tagsRes.json();
-                        this.analyticsTrends = await trendsRes.json();
-                        const sentiment = await sentimentRes.json();
-                        const dramas = await dramasRes.json();
-                        this.analyticsSentiment = sentiment.items || [];
-                        this.analyticsDramas = dramas.items || [];
-                        this.$nextTick(() => {
-                            this.renderAnalyticsCharts();
-                            setTimeout(() => this.resizeAnalyticsCharts(), 80);
-                        });
-                    } catch (e) {
-                        this.analyticsOverview = {};
-                        this.analyticsTags = {categories: [], selling_points: [], hooks: [], plot_tags: []};
-                        this.analyticsTrends = {points: []};
-                        this.analyticsSentiment = [];
-                        this.analyticsDramas = [];
-                    }
-                },
-
-                renderAnalyticsCharts() {
-                    if (!window.echarts) return;
-                    this.renderPieChart('categoryChart', this.analyticsTags.categories || [], '题材');
-                    this.renderSentimentChart();
-                    this.renderTrendChart();
-                },
-
-                resizeAnalyticsCharts() {
-                    ['categoryChart', 'trendChart', 'sentimentChart'].forEach(id => {
-                        const el = document.getElementById(id);
-                        if (!el || !echarts.getInstanceByDom(el)) return;
-                        echarts.getInstanceByDom(el).resize();
-                    });
-                },
-
-                renderPieChart(elementId, rows, name) {
-                    const el = document.getElementById(elementId);
-                    if (!el) return;
-                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
-                    chart.clear();
-                    chart.setOption({
-                        backgroundColor: 'transparent',
-                        tooltip: {trigger: 'item'},
-                        series: [{
-                            name,
-                            type: 'pie',
-                            radius: ['40%', '70%'],
-                            data: rows.slice(0, 12).map(item => ({name: item.tag || '-', value: item.count || 0})),
-                            label: {color: '#f1f4f8'}
-                        }]
-                    });
-                },
-
-                renderBarChart(elementId, rows, name) {
-                    const el = document.getElementById(elementId);
-                    if (!el) return;
-                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
-                    chart.clear();
-                    const data = rows.slice(0, 10).reverse();
-                    chart.setOption({
-                        backgroundColor: 'transparent',
-                        tooltip: {trigger: 'axis'},
-                        grid: {left: 80, right: 20, top: 20, bottom: 30},
-                        xAxis: {type: 'value', axisLabel: {color: '#9aa4b2'}},
-                        yAxis: {type: 'category', data: data.map(item => this.shortLabel(item.tag || '-')), axisLabel: {color: '#f1f4f8', width: 72, overflow: 'truncate'}},
-                        series: [{name, type: 'bar', data: data.map(item => item.count || 0), itemStyle: {color: '#3b95f6'}}]
-                    });
-                },
-
-                renderSentimentChart() {
-                    const el = document.getElementById('sentimentChart');
-                    if (!el) return;
-                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
-                    chart.clear();
-                    const labels = {positive: '正面', neutral: '中性', negative: '负面'};
-                    chart.setOption({
-                        backgroundColor: 'transparent',
-                        tooltip: {trigger: 'item'},
-                        series: [{
-                            type: 'pie',
-                            radius: '65%',
-                            data: (this.analyticsSentiment || []).map(item => ({name: labels[item.sentiment] || item.sentiment, value: item.count || 0})),
-                            label: {color: '#f1f4f8'}
-                        }]
-                    });
-                },
-
-                renderTrendChart() {
-                    const el = document.getElementById('trendChart');
-                    if (!el) return;
-                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
-                    chart.clear();
-                    const points = this.analyticsTrends.points || [];
-                    chart.setOption({
-                        backgroundColor: 'transparent',
-                        tooltip: {trigger: 'axis'},
-                        grid: {left: 55, right: 20, top: 20, bottom: 35},
-                        xAxis: {type: 'category', data: points.map(item => item.date), axisLabel: {color: '#9aa4b2'}},
-                        yAxis: {type: 'value', axisLabel: {color: '#9aa4b2'}},
-                        series: [{name: this.analyticsTrends.metric || 'value', type: 'line', smooth: true, data: points.map(item => item.value || 0), itemStyle: {color: '#20c997'}, areaStyle: {opacity: .12}}]
-                    });
-                },
-
-                async fetchTodayRank() {
-                    this.todayRankLoading = true;
-                    this.todayRankError = '';
-                    const params = new URLSearchParams({
-                        platform: this.todayRankPlatform || 'dy',
-                        _: String(Date.now())
-                    });
-                    if (this.todayRankDate) params.set('date', this.todayRankDate);
-                    try {
-                        const res = await fetch(`/api/rank/today?${params.toString()}`);
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.detail || '加载今日榜单失败');
-                        if (this.todayRankDate && !(data.available_dates || []).includes(this.todayRankDate)) {
-                            this.todayRankDate = '';
-                            this.todayRankData = data;
-                            await this.fetchTodayRank();
-                            return;
-                        }
-                        this.todayRankData = data;
-                        if (this.todayRankPlatform === 'hg') {
-                            this.ensureHongguoRoute(true);
-                        }
-                    } catch (e) {
-                        this.todayRankData = null;
-                        this.todayRankError = e.message || '加载今日榜单失败';
-                    } finally {
-                        this.todayRankLoading = false;
-                    }
-                },
-
-                parseTodayRankHash(shouldFetch = false) {
-                    const raw = window.location.hash || '';
-                    const path = raw.replace(/^#\/?/, '');
-                    if (!path) return false;
-                    const parts = path.split('/').map(part => decodeURIComponent(part || ''));
-                    if (parts[0] !== 'today-rank') return false;
-                    const nextPlatform = parts[1] === 'hg' ? 'hg' : 'dy';
-                    const platformChanged = this.todayRankPlatform !== nextPlatform;
-                    this.currentTab = 'today-rank';
-                    this.todayRankPlatform = nextPlatform;
-                    if (nextPlatform === 'hg') {
-                        this.todayRankSectionKey = parts[2] || this.todayRankSectionKey;
-                        this.todayRankRankingKey = parts[3] || this.todayRankRankingKey;
-                        this.ensureHongguoRoute(false);
-                    }
-                    if (shouldFetch && platformChanged) this.fetchTodayRank();
-                    return true;
-                },
-
-                setTodayRankRoute(platform = 'dy', sectionKey = '', rankingKey = '') {
-                    const nextPlatform = platform === 'hg' ? 'hg' : 'dy';
-                    const platformChanged = this.todayRankPlatform !== nextPlatform;
-                    this.currentTab = 'today-rank';
-                    this.todayRankPlatform = nextPlatform;
-                    if (nextPlatform === 'hg') {
-                        const section = this.resolveHongguoSection(sectionKey || this.todayRankSectionKey);
-                        this.todayRankSectionKey = section?.key || sectionKey || '';
-                        const ranking = this.resolveHongguoRanking(this.todayRankSectionKey, rankingKey);
-                        this.todayRankRankingKey = ranking?.key || rankingKey || '';
-                    }
-                    const target = this.todayRankRouteHash();
-                    if (window.location.hash !== target) {
-                        window.location.hash = target;
-                        if (platformChanged) this.fetchTodayRank();
-                    } else {
-                        this.fetchTodayRank();
-                    }
-                },
-
-                todayRankRouteHash() {
-                    if (this.todayRankPlatform !== 'hg') return '#/today-rank/dy';
-                    const parts = ['#/today-rank', 'hg'];
-                    const section = this.selectedHongguoSection();
-                    const ranking = this.selectedHongguoRanking();
-                    const sectionSlug = section?.label || this.todayRankSectionKey;
-                    const rankingSlug = ranking?.label || this.todayRankRankingKey;
-                    if (sectionSlug) parts.push(encodeURIComponent(sectionSlug));
-                    if (rankingSlug) parts.push(encodeURIComponent(rankingSlug));
-                    return parts.join('/');
-                },
-
-                todayRankTables() {
-                    const data = this.todayRankData || {};
-                    return [
-                        {key: 'new', title: '新上榜', mode: 'base', items: data.new_entries || []},
-                        {key: 'dropped', title: '下榜', mode: 'base', items: data.dropped_entries || []},
-                        {key: 'rank-up', title: '排名上升', mode: 'rank', items: data.rank_up || []},
-                        {key: 'rank-down', title: '排名下降', mode: 'rank', items: data.rank_down || []},
-                        {key: 'heat-growth', title: '热度增长 Top10', mode: 'heat', items: data.heat_growth_top || []},
-                        {key: 'like-growth', title: '点赞增长 Top10', mode: 'like', items: data.like_growth_top || []}
-                    ];
-                },
-
-                todayRankSections() {
-                    const data = this.todayRankData || {};
-                    if (Array.isArray(data.sections) && data.sections.length) return data.sections;
-                    return [{
-                        key: 'all',
-                        label: '全部',
-                        summary: data.summary || {},
-                        rankings: [{
-                            key: 'all-default',
-                            label: '综合',
-                            summary: data.summary || {},
-                            new_entries: data.new_entries || [],
-                            dropped_entries: data.dropped_entries || [],
-                            rank_up: data.rank_up || [],
-                            rank_down: data.rank_down || [],
-                            current_entries: data.current_entries || [],
-                            columns: data.columns || {}
-                        }]
-                    }];
-                },
-
-                hongguoSections() {
-                    const data = this.todayRankData || {};
-                    return Array.isArray(data.sections) ? data.sections : [];
-                },
-
-                hongguoRankings() {
-                    return this.selectedHongguoSection()?.rankings || [];
-                },
-
-                selectedHongguoSection() {
-                    return this.resolveHongguoSection(this.todayRankSectionKey);
-                },
-
-                selectedHongguoRanking() {
-                    return this.resolveHongguoRanking(this.todayRankSectionKey, this.todayRankRankingKey);
-                },
-
-                resolveHongguoSection(sectionKey = '') {
-                    const sections = this.hongguoSections();
-                    return sections.find(section => section.key === sectionKey || section.label === sectionKey) || sections[0] || null;
-                },
-
-                resolveHongguoRanking(sectionKey = '', rankingKey = '') {
-                    const section = this.resolveHongguoSection(sectionKey);
-                    const rankings = section?.rankings || [];
-                    return rankings.find(ranking => ranking.key === rankingKey || ranking.label === rankingKey) || rankings[0] || null;
-                },
-
-                ensureHongguoRoute(updateHash = false) {
-                    if (this.todayRankPlatform !== 'hg') return;
-                    const section = this.resolveHongguoSection(this.todayRankSectionKey);
-                    this.todayRankSectionKey = section?.key || '';
-                    const ranking = this.resolveHongguoRanking(this.todayRankSectionKey, this.todayRankRankingKey);
-                    this.todayRankRankingKey = ranking?.key || '';
-                    if (updateHash) {
-                        const target = this.todayRankRouteHash();
-                        if (window.location.hash !== target) window.location.hash = target;
-                    }
-                },
-
-                rankingTables(ranking) {
-                    const columns = ranking.columns || {};
-                    return [
-                        {key: 'current', title: '当前榜单', mode: 'current', items: ranking.current_entries || [], columns: this.rankColumnsForTable(columns.current, ranking.current_entries || [], 'current')},
-                        {key: 'new', title: '新上榜', mode: 'base', items: ranking.new_entries || [], columns: this.rankColumnsForTable(columns.new, ranking.new_entries || [], 'base')},
-                        {key: 'dropped', title: '下榜', mode: 'base', items: ranking.dropped_entries || [], columns: this.rankColumnsForTable(columns.dropped, ranking.dropped_entries || [], 'base')},
-                        {key: 'rank-up', title: '排名上升', mode: 'rank', items: ranking.rank_up || [], columns: this.rankColumnsForTable(columns.rank_up, ranking.rank_up || [], 'rank')},
-                        {key: 'rank-down', title: '排名下降', mode: 'rank', items: ranking.rank_down || [], columns: this.rankColumnsForTable(columns.rank_down, ranking.rank_down || [], 'rank')}
-                    ];
-                },
-
-                getRankingActiveTable(rankingKey) {
-                    return this.rankingActiveTables[rankingKey] || 'current';
-                },
-
-                setRankingActiveTable(rankingKey, tableKey) {
-                    this.rankingActiveTables = { ...this.rankingActiveTables, [rankingKey]: tableKey };
-                },
-
-                summaryText(summary = {}) {
-                    return `上榜 ${summary.new_count || 0} / 下榜 ${summary.dropped_count || 0} / 上升 ${summary.rank_up_count || 0} / 下降 ${summary.rank_down_count || 0}`;
-                },
-
-                entryStatus(item = {}, tableKey = '') {
-                    if (tableKey === 'dropped') return '下榜';
-                    if (item.rank_delta > 0) return `上升 ${item.rank_delta}`;
-                    if (item.rank_delta < 0) return `下降 ${Math.abs(item.rank_delta)}`;
-                    if (item.previous_rank) return '在榜';
-                    return '新上榜';
-                },
-
-                entryStatusClass(item = {}, tableKey = '') {
-                    if (tableKey === 'dropped') return 'status-dropped';
-                    if (item.rank_delta > 0) return 'status-up';
-                    if (item.rank_delta < 0) return 'status-down';
-                    if (item.previous_rank) return 'status-stable';
-                    return 'status-new';
-                },
-
-                rankNumberClass(rank) {
-                    const number = Number(rank || 0);
-                    if (number >= 1 && number <= 3) return `rank-top-${number}`;
-                    return '';
-                },
-
-                rankColumnsForTable(columns = [], items = [], mode = '') {
-                    if (Array.isArray(columns) && columns.length) return columns;
-                    const common = [
-                        {key: 'rank', label: '排名', kind: 'rank', width: 55},
-                        {key: 'title', label: '剧名', kind: 'title', width: 220},
-                        {key: 'category', label: '分类', kind: 'category', width: 100},
-                        {key: 'status', label: '状态', kind: 'status', width: 100}
-                    ];
-                    const candidates = [
-                        {key: 'subject', label: '标签', kind: 'text', width: 150},
-                        {key: 'description', label: '简介', kind: 'text', width: 260},
-                        {key: 'score', label: '评分', kind: 'metric', width: 90},
-                        {key: 'share_count', label: '分享', kind: 'metric', width: 90},
-                        {key: 'like_count', label: '点赞', kind: 'metric', width: 95, icon: 'bi-hand-thumbs-up'},
-                        {key: 'favorite_count', label: '收藏', kind: 'metric', width: 95, icon: 'bi-star'},
-                        {key: 'heat', label: '热度', kind: 'metric', width: 95, icon: 'bi-fire'},
-                        {key: 'comment_count', label: '评论', kind: 'metric', width: 95, icon: 'bi-chat-dots'},
-                        {key: 'recommend_index', label: '推荐指数', kind: 'metric', width: 105},
-                        {key: 'rating_count', label: '评分人数', kind: 'metric', width: 105},
-                        {key: 'expectation_index', label: '期待指数', kind: 'metric', width: 105},
-                        {key: 'search_index', label: '热搜指数', kind: 'metric', width: 105},
-                        {key: 'max_heat', label: '最高热度', kind: 'metric', width: 105},
-                        {key: 'hot_comment', label: '热评', kind: 'text', width: 240},
-                        {key: 'rank_duration', label: '在榜时长', kind: 'rank_duration', width: 210}
-                    ];
-                    const dynamic = candidates.filter(column => items.some(item => {
-                        const value = column.key === 'subject' ? item.subject || item.topic_tags : item[column.key];
-                        return this.hasRankColumnValue(value);
-                    }));
-                    const rankChange = mode === 'rank'
-                        ? [{key: 'previous_rank', label: '上期排名', kind: 'metric', width: 90}, {key: 'rank_delta', label: '排名变化', kind: 'rank_delta', width: 105}]
-                        : [];
-                    return [...common, ...dynamic, ...rankChange];
-                },
-
-                hasRankColumnValue(value) {
-                    if (value === null || value === undefined) return false;
-                    if (typeof value === 'string') return !['', '-', '无', '暂无', 'null', 'None'].includes(value.trim());
-                    if (Array.isArray(value)) return value.length > 0;
-                    return true;
-                },
-
-                rankColumnHeaderClass(column = {}) {
-                    return column.kind === 'metric' ? 'rank-metric' : '';
-                },
-
-                rankColumnCellClass(column = {}) {
-                    return column.kind === 'metric' ? 'rank-metric' : '';
-                },
-
-                rankColumnStyle(column = {}) {
-                    return column.width ? `min-width: ${column.width}px;` : '';
-                },
-
-                rankColumnValue(item = {}, column = {}) {
-                    const value = item[column.key];
-                    if (column.key === 'subject') return value || this.shortTags(item.topic_tags);
-                    if (column.kind === 'rank_duration') return this.formatRankDuration(item);
-                    if (column.kind === 'text') return this.shortText(value, 64);
-                    return this.formatMetric(value);
-                },
-
-                formatRankDuration(item = {}) {
-                    if (item.rank_duration) return item.rank_duration;
-                    const continuous = Number(item.continuous_rank_days || 0);
-                    const cumulative = Number(item.cumulative_rank_days || 0);
-                    if (!continuous || !cumulative) return '-';
-                    return `连续在榜${continuous}天 / 累计在榜${cumulative}天`;
-                },
-
-                rankColumnTooltip(item = {}, column = {}, tbl = {}) {
-                    if (column.kind === 'status') return this.entryStatus(item, tbl.key);
-                    if (column.kind === 'rank_delta') return this.formatRankDelta(item.rank_delta);
-                    if (column.kind === 'rank') return String(item.rank || '-');
-                    if (column.kind === 'category') return item.board_type || item.category_label || '-';
-                    if (column.key === 'subject') return item.subject || this.shortTags(item.topic_tags);
-                    const value = item[column.key];
-                    return value === null || value === undefined || value === '' ? '-' : String(value);
-                },
-
-                rankingTableColspan(tbl = {}) {
-                    return Math.max((tbl.columns || []).length, 4);
-                },
-
-                todayRankDates() {
-                    return this.todayRankData?.available_dates || [];
-                },
-
-                formatMetric(value) {
-                    if (value === null || value === undefined || value === '') return '缺失';
-                    const number = Number(value || 0);
-                    if (!Number.isFinite(number)) return '0';
-                    if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)}亿`;
-                    if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}万`;
-                    return Number.isInteger(number) ? String(number) : number.toFixed(2);
-                },
-
-                formatDelta(value) {
-                    const number = Number(value || 0);
-                    if (!Number.isFinite(number) || number === 0) return '0';
-                    return `${number > 0 ? '+' : ''}${this.formatMetric(number)}`;
-                },
-
-                formatRankDelta(value) {
-                    const number = Number(value || 0);
-                    if (!Number.isFinite(number) || number === 0) return '0';
-                    return number > 0 ? `上升 ${number}` : `下降 ${Math.abs(number)}`;
-                },
-
-                shortTags(tags) {
-                    const values = Array.isArray(tags) ? tags.filter(Boolean) : [];
-                    return values.length ? values.slice(0, 3).join(' / ') : '-';
-                },
-
-                shortText(value, maxLength = 18) {
-                    const text = String(value || '').trim();
-                    return text ? (text.length > maxLength ? `${text.slice(0, maxLength)}...` : text) : '-';
-                },
-
-                warningText(warnings) {
-                    const labels = {
-                        heat_missing: '热度缺失',
-                        favorite_missing: '收藏未采集',
-                        like_missing: '点赞缺失',
-                        comment_missing: '评论缺失',
-                        title_missing: '标题缺失',
-                        category_missing: '分类缺失'
-                    };
-                    return (warnings || []).map(item => labels[item] || item).join('；');
-                },
-
-                shortLabel(value) {
-                    const text = String(value || '');
-                    return text.length > 8 ? `${text.slice(0, 8)}...` : text;
-                },
-
-                applyFileSearch() {
-                    const query = String(this.fileSearch || '').trim().toLowerCase();
-                    if (!query) {
-                        this.filteredFiles = this.files;
-                        return;
-                    }
-                    this.filteredFiles = this.files.filter(file =>
-                        String(file.name || '').toLowerCase().includes(query) ||
-                        String(file.path || '').toLowerCase().includes(query)
-                    );
-                },
-
-                groupedFiles() {
-                    const groupOrder = ['视频', '音频', '剧本与文稿', '转写数据', '元数据', '其他文件'];
-                    const dramas = new Map();
-                    for (const file of this.filteredFiles) {
-                        const dramaName = file.drama_name || '其他数据';
-                        if (!dramas.has(dramaName)) dramas.set(dramaName, {drama_name: dramaName, count: 0, groups: new Map()});
-                        const drama = dramas.get(dramaName);
-                        const typeName = file.file_group || '其他文件';
-                        if (!drama.groups.has(typeName)) drama.groups.set(typeName, []);
-                        drama.groups.get(typeName).push(file);
-                        drama.count += 1;
-                    }
-                    return [...dramas.values()]
-                        .sort((left, right) => left.drama_name === '其他数据' ? 1 : (right.drama_name === '其他数据' ? -1 : 0))
-                        .map(group => ({
-                            drama_name: group.drama_name,
-                            count: group.count,
-                            file_groups: [...group.groups.entries()]
-                                .sort((left, right) => groupOrder.indexOf(left[0]) - groupOrder.indexOf(right[0]))
-                                .map(([name, files]) => ({name, files})),
-                        }));
-                },
-
-                isFileSelected(path) {
-                    return this.selectedFilePaths.includes(path);
-                },
-
-                allVisibleFilesSelected() {
-                    return this.filteredFiles.length > 0 && this.filteredFiles.every(file => this.isFileSelected(file.path));
-                },
-
-                startBatchDelete() {
-                    this.selectedFilePaths = [];
-                    this.batchDeleteMode = true;
-                },
-
-                cancelBatchDelete() {
-                    this.selectedFilePaths = [];
-                    this.batchDeleteMode = false;
-                },
-
-                toggleFileSelection(path, selected) {
-                    if (selected && !this.isFileSelected(path)) {
-                        this.selectedFilePaths.push(path);
-                    }
-                    if (!selected) {
-                        this.selectedFilePaths = this.selectedFilePaths.filter(item => item !== path);
-                    }
-                },
-
-                toggleAllVisibleFiles(selected) {
-                    const visiblePaths = this.filteredFiles.map(file => file.path);
-                    if (selected) {
-                        this.selectedFilePaths = [...new Set([...this.selectedFilePaths, ...visiblePaths])];
-                    } else {
-                        this.selectedFilePaths = this.selectedFilePaths.filter(path => !visiblePaths.includes(path));
-                    }
-                },
-
-                async deleteSelectedFiles() {
-                    await this.deleteFiles(this.selectedFilePaths);
-                },
-
-                async deleteFiles(paths) {
-                    const selectedPaths = [...new Set(paths || [])];
-                    if (!selectedPaths.length) return;
-                    const label = selectedPaths.length === 1 ? '该文件' : `已选的 ${selectedPaths.length} 个文件`;
-                    if (!confirm(`确认删除${label}吗？本地文件将永久移除。`)) return;
-                    try {
-                        const res = await fetch('/api/files', {
-                            method: 'DELETE',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({paths: selectedPaths})
-                        });
-                        const data = await res.json().catch(() => ({}));
-                        if (!res.ok || data.status === 'error') {
-                            alert(data.message || '删除失败，请刷新后重试');
-                            return;
-                        }
-                        const deletedPaths = data.deleted_paths || [];
-                        this.selectedFilePaths = this.selectedFilePaths.filter(path => !deletedPaths.includes(path));
-                        await this.fetchFiles();
-                        if (data.status === 'partial_success') {
-                            alert(`已删除 ${deletedPaths.length} 个文件，${(data.failed || []).length} 个文件删除失败`);
-                        } else {
-                            alert(`已删除 ${deletedPaths.length} 个文件`);
-                        }
-                    } catch (e) {
-                        alert('删除失败，请检查服务状态后重试');
-                    }
-                },
-
-                formatSize(bytes) {
-                    if (!bytes) return '0 B';
-                    const units = ['B', 'KB', 'MB', 'GB'];
-                    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
-                    return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
-                },
-
-                formatDate(timestamp) {
-                    if (!timestamp) return '';
-                    return new Date(timestamp * 1000).toLocaleString();
-                },
-
-                downloadFile(path) {
-                    window.open(`/api/file/download?path=${encodeURIComponent(path)}`, '_blank');
-                },
-
-                async openFilePreview(file) {
-                    const kind = file.preview_kind || 'none';
-                    this.previewFile = {status: 'loading', file, kind, url: '', content: '', message: ''};
-                    this.filePreviewModal.show();
-                    if (['video', 'audio'].includes(kind)) {
-                        this.previewFile.url = `/api/file/preview?path=${encodeURIComponent(file.path)}`;
-                        this.previewFile.status = 'success';
-                        return;
-                    }
-                    if (!['text', 'json'].includes(kind)) {
-                        this.previewFile.status = 'error';
-                        this.previewFile.message = '该文件格式暂不支持在线预览';
-                        return;
-                    }
-                    try {
-                        const res = await fetch(`/api/file/content?path=${encodeURIComponent(file.path)}`);
-                        const data = await res.json();
-                        if (!res.ok) throw new Error(data.message || '读取文件失败');
-                        this.previewFile.content = data.content || '';
-                        this.previewFile.status = 'success';
-                    } catch (e) {
-                        this.previewFile.status = 'error';
-                        this.previewFile.message = e.message || '读取文件失败';
-                    }
-                },
-
-                closeFilePreview() {
-                    this.previewFile = {status: 'idle', file: null, kind: '', url: '', content: '', message: ''};
-                },
-
-                async analyzeAudio(file) {
-                    this.audioAnalysis = {status: 'loading', file_name: file.name};
-                    this.audioModal.show();
-                    try {
-                        const res = await fetch('/api/audio/analyze', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({file_path: file.path})
-                        });
-                        const data = await res.json();
-                        if (!res.ok || data.status === 'error') {
-                            this.audioAnalysis = {status: 'error', message: data.message || '分析失败'};
-                            return;
-                        }
-                        this.audioAnalysis = data;
-                    } catch (e) {
-                        this.audioAnalysis = {status: 'error', message: '分析失败'};
-                    }
-                },
-
-                downloadAudioAnalysis() {
-                    if (this.audioAnalysis.status !== 'success') return;
-                    const lines = [
-                        `文件：${this.audioAnalysis.file_name}`,
-                        `时间：${this.audioAnalysis.analysis_time}`,
-                        '',
-                        '## 摘要',
-                        this.audioAnalysis.summary || '',
-                        '',
-                        '## 关键点',
-                        ...(this.audioAnalysis.key_points || []).map(item => `- ${item}`),
-                        '',
-                        '## 转写文本',
-                        this.audioAnalysis.transcript || ''
-                    ];
-                    const blob = new Blob([lines.join('\n')], {type: 'text/markdown;charset=utf-8'});
-                    const url = URL.createObjectURL(blob);
-                    const link = document.createElement('a');
-                    const stem = String(this.audioAnalysis.file_name || 'audio').replace(/\.[^.]+$/, '');
-                    link.href = url;
-                    link.download = `${stem}_analysis.md`;
-                    document.body.appendChild(link);
-                    link.click();
-                    document.body.removeChild(link);
-                    URL.revokeObjectURL(url);
-                },
-
-                async fetchConfigForm() {
-                    try {
-                        const res = await fetch('/api/config/form');
-                        const data = await res.json();
-                        if (data.status === 'error') {
-                            alert(data.message || '加载配置失败');
-                            return;
-                        }
-                        this.configForm = {
-                            root: data.root || {},
-                            items: (data.items || []).map(item => this.normalizeConfigItem(item))
-                        };
-                    } catch (e) {
-                        alert('加载配置失败');
-                    }
-                },
-
-                normalizeConfigItem(item = {}) {
-                    return {
-                        project: item.project || '',
-                        '评论不少于': Number(item['评论不少于'] || 0),
-                        '搜索关键词': item['搜索关键词'] || '',
-                        '屏蔽关键词': item['屏蔽关键词'] || '',
-                        '有关视频判定': item['有关视频判定'] || '',
-                        '黑名单视频判定': item['黑名单视频判定'] || '',
-                        '作品天数': Number(item['作品天数'] || 0),
-                        '白名单地区': item['白名单地区'] || '',
-                        '黑名单地区': item['黑名单地区'] || ''
-                    };
-                },
-
-                addConfigItem() {
-                    this.configForm.items.push(this.normalizeConfigItem({
-                        '评论不少于': 3,
-                        '作品天数': 90
-                    }));
-                },
-
-                removeConfigItem(index) {
-                    if (!confirm('删除该项目配置？保存后会写入 data.json。')) return;
-                    this.configForm.items.splice(index, 1);
-                },
-
-                async saveConfigForm() {
-                    const crawlerType = String(this.configForm.root['默认爬取类型'] || '').toLowerCase();
-                    const isRank = crawlerType.includes('rank') || crawlerType.includes('榜单');
-                    if (!isRank) {
-                        if (this.configForm.items.length === 0) {
-                            alert('搜索类型至少需要一个项目配置');
-                            return;
-                        }
-                        const missing = this.configForm.items.findIndex(item => !String(item['搜索关键词'] || '').trim());
-                        if (missing >= 0) {
-                            alert(`第 ${missing + 1} 个项目缺少搜索关键词`);
-                            return;
-                        }
-                    }
-                    const res = await fetch('/api/config/form', {
-                        method: 'POST',
-                        headers: {'Content-Type': 'application/json'},
-                        body: JSON.stringify(this.configForm)
-                    });
-                    const data = await res.json();
-                    if (!res.ok || data.status !== 'success') {
-                        alert(data.message || '保存失败');
-                        return;
-                    }
-                    alert('保存成功');
-                    await this.loadConfig();
-                    await this.fetchConfigForm();
-                    await this.fetchBackups();
-                },
-
-                async fetchBackups() {
-                    try {
-                        const res = await fetch('/api/config/backups');
-                        this.backups = await res.json();
-                    } catch (e) {
-                        this.backups = [];
-                    }
-                },
-
-                async createBackup() {
-                    const res = await fetch('/api/config/backup/create', {method: 'POST'});
-                    const data = await res.json();
-                    if (data.status !== 'success') alert(data.message || '创建失败');
-                    await this.fetchBackups();
-                },
-
-                async restoreBackup(filename) {
-                    if (!confirm(`恢复备份 ${filename}？当前配置会被覆盖。`)) return;
-                    const res = await fetch('/api/config/backup/restore', {
-                        method: 'POST',
-                        headers: {'Content-Type': 'application/json'},
-                        body: JSON.stringify({filename})
-                    });
-                    const data = await res.json();
-                    if (data.status !== 'success') alert(data.message || '恢复失败');
-                    await this.fetchConfigForm();
-                    await this.loadConfig();
-                },
-
-                async deleteBackup(filename) {
-                    if (!confirm(`删除备份 ${filename}？`)) return;
-                    const res = await fetch('/api/config/backup/delete', {
-                        method: 'POST',
-                        headers: {'Content-Type': 'application/json'},
-                        body: JSON.stringify({filename})
-                    });
-                    const data = await res.json();
-                    if (data.status !== 'success') alert(data.message || '删除失败');
-                    await this.fetchBackups();
-                },
-
-                async openProjectModal(filePath, mode) {
-                    this.modalMode = mode;
-                    this.modalFile = filePath;
-                    this.selectedModalProjects = [];
-                    this.modalProjects = [];
-                    try {
-                        const res = await fetch('/api/file/analyze', {
-                            method: 'POST',
-                            headers: {'Content-Type': 'application/json'},
-                            body: JSON.stringify({file_path: filePath})
-                        });
-                        const data = await res.json();
-                        this.modalProjects = Object.entries(data)
-                            .map(([name, count]) => ({name, count}))
-                            .sort((a, b) => a.name.localeCompare(b.name));
-                        this.selectedModalProjects = this.modalProjects.map(item => item.name);
-                        this.projectModal.show();
-                    } catch (e) {
-                        alert('分析文件失败');
-                    }
-                },
-
-                moveProjectUp(index) {
-                    if (index <= 0) return;
-                    const item = this.modalProjects[index];
-                    this.modalProjects[index] = this.modalProjects[index - 1];
-                    this.modalProjects[index - 1] = item;
-                },
-
-                moveProjectDown(index) {
-                    if (index >= this.modalProjects.length - 1) return;
-                    const item = this.modalProjects[index];
-                    this.modalProjects[index] = this.modalProjects[index + 1];
-                    this.modalProjects[index + 1] = item;
-                },
-
-                async confirmBatchAction() {
-                    if (this.selectedModalProjects.length === 0) {
-                        alert('请至少选择一个项目');
-                        return;
-                    }
-                    const projects = this.modalProjects
-                        .filter(item => this.selectedModalProjects.includes(item.name))
-                        .map(item => item.name);
-                    const endpoint = this.modalMode === 'crawl' ? '/api/batch/run' : '/api/export/excel';
-                    const res = await fetch(endpoint, {
-                        method: 'POST',
-                        headers: {'Content-Type': 'application/json'},
-                        body: JSON.stringify({
-                            file_path: this.modalFile,
-                            projects,
-                            auto_upload: this.autoUpload
-                        })
-                    });
-                    const data = await res.json();
-                    alert(data.message || '已提交');
-                    this.projectModal.hide();
-                    this.currentTab = 'dashboard';
-                },
-
-                async fetchQrcodeStatus() {
-                    try {
-                        const res = await fetch('/api/qrcode/status');
-                        this.qrcodeStatus = await res.json();
-                    } catch (e) {}
-                },
-
-                async onLoginTypeChange() {
-                    if (this.config.login_type === 'qrcode') await this.fetchQrcodeStatus();
-                },
-
-                onPlatformChange() {
-                    if (this.config.crawler_type === 'rank' && !['dy', 'hg'].includes(this.config.platform)) {
-                        this.config.crawler_type = 'search';
-                        alert('榜单爬取仅支持抖音和红果，已切换为搜索类型');
-                    }
-                },
-
-                async startQrcodeLogin() {
-                    if (this.isRunning) {
-                        alert('爬虫正在运行，请先停止');
-                        return;
-                    }
-                    this.qrcodeLoggingIn = true;
-                    this.currentTab = 'dashboard';
-                    try {
-                        const res = await fetch('/api/qrcode/login', {method: 'POST'});
-                        const data = await res.json();
-                        if (data.status === 'success') {
-                            this.pollQrcodeStatus();
-                        } else {
-                            alert(data.message || '启动扫码失败');
-                            this.qrcodeLoggingIn = false;
-                        }
-                    } catch (e) {
-                        alert('启动扫码失败');
-                        this.qrcodeLoggingIn = false;
-                    }
-                },
-
-                pollQrcodeStatus() {
-                    const timer = setInterval(async () => {
-                        await this.fetchQrcodeStatus();
-                        if (this.qrcodeStatus.has_state) {
-                            clearInterval(timer);
-                            this.qrcodeLoggingIn = false;
-                            alert('扫码登录成功');
-                        }
-                    }, 3000);
-                    setTimeout(() => {
-                        clearInterval(timer);
-                        this.qrcodeLoggingIn = false;
-                    }, 300000);
-                },
-
-                async clearQrcodeLogin() {
-                    if (!confirm('清除保存的登录状态？')) return;
-                    const res = await fetch('/api/qrcode/clear', {method: 'POST'});
-                    const data = await res.json();
-                    if (data.status !== 'success') alert(data.message || '清除失败');
-                    await this.fetchQrcodeStatus();
-                },
-
-            }
-        }
-    </script>
-    <script src="/static/js/bootstrap.bundle.min.js"></script>
+<body>
+    <noscript><a href="/">进入 AiWorker</a></noscript>
+    <script>window.location.replace('/');</script>
 </body>
 </html>
diff --git a/templates/layouts/base.html b/templates/layouts/base.html
index 50b2eec..13c559e 100644
--- a/templates/layouts/base.html
+++ b/templates/layouts/base.html
@@ -1,67 +1,52 @@
 <!doctype html>
 <html lang="zh-CN">
 <head>
     <meta charset="utf-8">
     <meta name="viewport" content="width=device-width, initial-scale=1">
+    {% if csrf_token %}<meta name="csrf-token" content="{{ csrf_token }}">{% endif %}
     <title>{{ page_title }} - AiWorker</title>
-    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
-    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
+    <link href="/static/css/bootstrap.min.css" rel="stylesheet">
+    <link href="/static/css/bootstrap-icons.css" rel="stylesheet">
+    {% if workspace_page %}<link href="/static/css/workspaces.css" rel="stylesheet">{% endif %}
+    {% if workspace_page %}<script defer src="/static/js/alpine.min.js"></script>{% endif %}
     {% block styles %}{% endblock %}
 </head>
-<body class="bg-body-tertiary">
-    <header class="navbar navbar-dark bg-dark px-3">
-        <a class="navbar-brand" href="/">AiWorker</a>
-        {% if principal %}
-        <div class="d-flex align-items-center gap-3 text-white">
-            <span>{{ principal.display_name }}</span>
-            <button class="btn btn-outline-light btn-sm" id="logout-button" type="button">
-                <i class="bi bi-box-arrow-right" aria-hidden="true"></i>
-                退出登录
-            </button>
+<body{% if workspace_page %} x-data="appData()" x-init="init()" data-workspace-page="{{ workspace_page }}"{% else %} class="bg-body-tertiary"{% endif %}>
+    {% if principal %}
+    <aside class="sidebar">
+        <div class="p-4 border-bottom border-secondary-subtle">
+            <a class="h4 d-block mb-1 fw-bold text-decoration-none text-reset" href="/"><i class="bi bi-robot me-2"></i>AiWorker</a>
+            <div class="small text-muted">壹凡短剧选品平台</div>
+            <div class="small mt-2">{{ principal.display_name }}</div>
         </div>
-        {% endif %}
-    </header>
-    <div class="container-fluid">
-        <div class="row min-vh-100">
-            {% if principal %}
-            <nav class="col-12 col-md-3 col-xl-2 bg-white border-end py-3" aria-label="主导航">
-                <a class="nav-link mb-2{% if current_route == '/tasks' %} active{% endif %}" href="/tasks">
+        <nav class="nav flex-column py-3" aria-label="主导航">
+                <a class="nav-link{% if current_route == '/tasks' %} active{% endif %}" href="/tasks">
                     <i class="bi bi-list-task me-2" aria-hidden="true"></i>我的任务
                 </a>
                 {% for group in navigation %}
-                <section class="mb-3">
-                    <div class="px-3 py-2 fw-semibold text-body-secondary">
+                <section class="mb-2">
+                    <div class="px-3 py-2 small fw-semibold text-muted">
                         <i class="bi {{ group.icon }} me-2" aria-hidden="true"></i>{{ group.label }}
                     </div>
-                    <div class="nav nav-pills flex-column">
                         {% for item in group['items'] %}
                         <a class="nav-link{% if current_route == item.route %} active{% endif %}" href="{{ item.route }}">
                             {{ item.label }}
                         </a>
                         {% endfor %}
-                    </div>
                 </section>
                 {% endfor %}
-            </nav>
-            {% endif %}
-            <main class="col px-4 py-4">
-                {% block content %}{% endblock %}
-            </main>
-        </div>
-    </div>
-    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
-    {% if principal %}
-    <script>
-        document.getElementById("logout-button")?.addEventListener("click", async () => {
-            const session = await fetch("/api/auth/me").then((response) => response.json());
-            await fetch("/api/auth/logout", {
-                method: "POST",
-                headers: {"X-CSRF-Token": session.csrf_token}
-            });
-            window.location.assign("/login");
-        });
-    </script>
+                <button class="btn btn-outline-light btn-sm mx-3 mt-2" id="logout-button" type="button">
+                    <i class="bi bi-box-arrow-right" aria-hidden="true"></i>退出登录
+                </button>
+        </nav>
+    </aside>
     {% endif %}
+    <main{% if workspace_page %} class="main-content"{% else %} class="container py-4"{% endif %}>
+        {% block content %}{% endblock %}
+    </main>
+    <script src="/static/js/bootstrap.bundle.min.js"></script>
+    {% if principal %}<script src="/static/js/common/api.js"></script><script src="/static/js/common/session.js"></script>{% endif %}
+    {% if workspace_page %}<script src="/static/js/common/tasks.js"></script>{% endif %}
     {% block scripts %}{% endblock %}
 </body>
 </html>
diff --git a/templates/planning/brief.html b/templates/planning/brief.html
new file mode 100644
index 0000000..5bcd6dc
--- /dev/null
+++ b/templates/planning/brief.html
@@ -0,0 +1,9 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex justify-content-between align-items-start mb-3"><div><h3 class="mb-1">创作任务书</h3><div class="small text-muted">项目 {{ project_id }} · 编辑方向与 Brief，保留版本历史</div></div><div class="d-flex gap-2"><a class="btn btn-outline-secondary btn-sm" href="/planning/projects/{{ project_id }}/directions"><i class="bi bi-arrow-left me-1"></i>仿写方向</a><a class="btn btn-primary btn-sm" href="/planning/projects/{{ project_id }}/characters">人物关系<i class="bi bi-arrow-right ms-1"></i></a></div></div>
+{% include "common/workspace/project_progress.html" %}
+<template x-if="creativeWorkspace.project"><div>{% include "common/workspace/project_blueprints.html" %}</div></template>
+{% include "common/workspace/creative_progress_modal.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/common/creative.js"></script><script src="/static/js/planning/brief.js"></script>{% endblock %}
diff --git a/templates/planning/characters.html b/templates/planning/characters.html
new file mode 100644
index 0000000..48d46db
--- /dev/null
+++ b/templates/planning/characters.html
@@ -0,0 +1,9 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex justify-content-between align-items-start mb-3"><div><h3 class="mb-1">人物与关系</h3><div class="small text-muted">项目 {{ project_id }} · 人物卡、角色表现设置与人物关系</div></div><a class="btn btn-outline-secondary btn-sm" href="/planning/projects/{{ project_id }}/brief"><i class="bi bi-arrow-left me-1"></i>返回 Brief</a></div>
+{% include "common/workspace/project_progress.html" %}
+<template x-if="creativeWorkspace.project"><div>{% include "common/workspace/project_characters.html" %}</div></template>
+{% include "common/workspace/creative_progress_modal.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/common/creative.js"></script><script src="/static/js/planning/characters.js"></script>{% endblock %}
diff --git a/templates/planning/directions.html b/templates/planning/directions.html
new file mode 100644
index 0000000..fda9322
--- /dev/null
+++ b/templates/planning/directions.html
@@ -0,0 +1,9 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex justify-content-between align-items-start mb-3"><div><h3 class="mb-1">仿写方向</h3><div class="small text-muted">项目 {{ project_id }} · 比较受众、冲突、爽点、成本与风险</div></div><div class="d-flex gap-2"><a class="btn btn-outline-secondary btn-sm" href="/planning/projects">项目列表</a><a class="btn btn-primary btn-sm" href="/planning/projects/{{ project_id }}/brief">编辑 Brief<i class="bi bi-arrow-right ms-1"></i></a></div></div>
+{% include "common/workspace/project_progress.html" %}
+<template x-if="creativeWorkspace.project"><div>{% include "common/workspace/project_blueprints.html" %}</div></template>
+{% include "common/workspace/creative_progress_modal.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/common/creative.js"></script>{% endblock %}
diff --git a/templates/planning/projects.html b/templates/planning/projects.html
new file mode 100644
index 0000000..c78e1c6
--- /dev/null
+++ b/templates/planning/projects.html
@@ -0,0 +1,7 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between mb-3"><div><h3 class="mb-1">策划项目</h3><div class="small text-muted">从已批准拆解创建创作项目</div></div><button class="btn btn-outline-secondary btn-sm" @click="fetchCreativeProjects()"><i class="bi bi-arrow-clockwise"></i></button></div>
+{% include "common/workspace/project_list.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/common/creative.js"></script><script src="/static/js/planning/projects.js"></script>{% endblock %}
diff --git a/templates/review/approvals.html b/templates/review/approvals.html
new file mode 100644
index 0000000..f80ac6d
--- /dev/null
+++ b/templates/review/approvals.html
@@ -0,0 +1,7 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between border-bottom pb-3 mb-4"><div><h3 class="mb-1">版本审批</h3><div class="small text-muted">比较提交版本并记录批准、驳回或修改意见</div></div></div>
+<div id="approval-list" class="alert alert-light border" role="status">暂无待审批版本。</div>
+{% endblock %}
+{% block scripts %}<script src="/static/js/review/approvals.js"></script>{% endblock %}
diff --git a/templates/review/projects.html b/templates/review/projects.html
new file mode 100644
index 0000000..5a88d0b
--- /dev/null
+++ b/templates/review/projects.html
@@ -0,0 +1,6 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between border-bottom pb-3 mb-4"><div><h3 class="mb-1">负责项目</h3><div class="small text-muted">{% if project_id %}项目 {{ project_id }} 的成员与职责{% else %}当前账号负责的项目范围{% endif %}</div></div></div>
+<div class="alert alert-light border" role="status">项目成员与分工将在任务流启用后显示。</div>
+{% endblock %}
diff --git a/templates/review/tasks.html b/templates/review/tasks.html
new file mode 100644
index 0000000..2d7adc7
--- /dev/null
+++ b/templates/review/tasks.html
@@ -0,0 +1,7 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between border-bottom pb-3 mb-4"><div><h3 class="mb-1">审批任务</h3><div class="small text-muted">领取、指派并处理项目审批事项</div></div><button class="btn btn-outline-secondary btn-sm" id="refresh-review-tasks" title="刷新"><i class="bi bi-arrow-clockwise"></i></button></div>
+<div id="review-task-list" class="alert alert-light border" role="status">暂无待处理审批任务。</div>
+{% endblock %}
+{% block scripts %}<script src="/static/js/review/tasks.js"></script>{% endblock %}
diff --git a/templates/review/versions.html b/templates/review/versions.html
new file mode 100644
index 0000000..5b3ed51
--- /dev/null
+++ b/templates/review/versions.html
@@ -0,0 +1,6 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between border-bottom pb-3 mb-4"><div><h3 class="mb-1">项目版本</h3><div class="small text-muted">项目 {{ project_id }} · 已提交与已批准版本历史</div></div><a class="btn btn-outline-secondary btn-sm" href="/review/approvals"><i class="bi bi-arrow-left me-1"></i>返回审批</a></div>
+<div class="alert alert-light border" role="status">版本记录将在审批任务产生后显示。</div>
+{% endblock %}
diff --git a/templates/selection/breakdowns.html b/templates/selection/breakdowns.html
new file mode 100644
index 0000000..6a27800
--- /dev/null
+++ b/templates/selection/breakdowns.html
@@ -0,0 +1,9 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between mb-3"><div><h3 class="mb-1">剧目拆解</h3><div class="small text-muted">依据已下载转写生成、校正并锁定拆解版本</div></div><button class="btn btn-outline-secondary btn-sm" @click="fetchTopicLibrary()"><i class="bi bi-arrow-clockwise"></i></button></div>
+<div class="creative-topic-table"><table class="table table-sm table-hover align-middle"><thead><tr><th>剧目</th><th>素材覆盖</th><th>生命周期</th><th>拆解版本</th><th>操作</th></tr></thead><tbody><template x-for="topic in topicLibrary.items" :key="topic.id"><tr><td x-text="topic.title"></td><td x-text="`已下载 ${Number(topic.downloaded_episode_count || 0)} 集`"></td><td><span class="badge lifecycle-badge" :class="topicLifecycleClass(topic.lifecycle_status)" x-text="topic.lifecycle_label || topicLifecycleLabel(topic)"></span></td><td><span class="badge breakdown-version-badge" :class="breakdownStatusClass(topic)" x-text="breakdownStatusLabel(topic)"></span></td><td><button class="btn btn-sm btn-outline-light" @click="createTopicLibraryBreakdown(topic)"><i class="bi bi-diagram-3 me-1"></i><span x-text="topic.latest_breakdown_id ? '继续拆解' : '生成拆解'"></span></button></td></tr></template><tr x-show="!topicLibrary.items.length"><td colspan="5" class="text-center text-muted py-5">暂无可拆解剧目</td></tr></tbody></table></div>
+{% include "common/workspace/selection_modals.html" %}
+{% include "common/workspace/breakdown_modals.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/selection/topics.js"></script><script src="/static/js/selection/breakdowns.js"></script>{% endblock %}
diff --git a/templates/selection/dashboard.html b/templates/selection/dashboard.html
new file mode 100644
index 0000000..1398a8a
--- /dev/null
+++ b/templates/selection/dashboard.html
@@ -0,0 +1,14 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between mb-3">
+    <div><h3 class="mb-1">选品工作概览</h3><div class="small text-muted">榜单、候选选题与素材准备进度</div></div>
+    <div class="d-flex gap-2"><a class="btn btn-outline-light btn-sm" href="/selection/ranks"><i class="bi bi-trophy me-1"></i>今日榜单</a><a class="btn btn-primary btn-sm" href="/selection/topics"><i class="bi bi-stars me-1"></i>今日爆款</a></div>
+</div>
+<div class="row g-3">
+    <div class="col-md-4"><div class="card"><div class="card-body"><div class="small text-muted">候选选题</div><div class="display-6" x-text="creative.items.length"></div></div></div></div>
+    <div class="col-md-4"><div class="card"><div class="card-body"><div class="small text-muted">选题库</div><div class="display-6" x-text="topicLibrary.items.length"></div></div></div></div>
+    <div class="col-md-4"><div class="card"><div class="card-body"><div class="small text-muted">进行中机器任务</div><div class="display-6" x-text="jobs.filter(job => isJobRunning(job)).length"></div></div></div></div>
+</div>
+{% endblock %}
+{% block scripts %}<script src="/static/js/selection/topics.js"></script>{% endblock %}
diff --git a/templates/selection/dramas.html b/templates/selection/dramas.html
new file mode 100644
index 0000000..29222b1
--- /dev/null
+++ b/templates/selection/dramas.html
@@ -0,0 +1,7 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+{% include "common/workspace/selection_dramas.html" %}
+{% include "common/workspace/selection_modals.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/selection/dramas.js"></script>{% endblock %}
diff --git a/templates/selection/ranks.html b/templates/selection/ranks.html
new file mode 100644
index 0000000..0cc495d
--- /dev/null
+++ b/templates/selection/ranks.html
@@ -0,0 +1,4 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}{% include "common/workspace/selection_ranks.html" %}{% endblock %}
+{% block scripts %}<script src="/static/js/selection/ranks.js"></script>{% endblock %}
diff --git a/templates/selection/topics.html b/templates/selection/topics.html
new file mode 100644
index 0000000..a3e2b6d
--- /dev/null
+++ b/templates/selection/topics.html
@@ -0,0 +1,9 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<h1 class="visually-hidden">今日爆款与选题库</h1>
+{% include "common/workspace/selection_topics.html" %}
+{% include "common/workspace/selection_modals.html" %}
+{% include "common/workspace/breakdown_modals.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/selection/topics.js"></script><script src="/static/js/selection/breakdowns.js"></script>{% endblock %}
diff --git a/templates/writing/beats.html b/templates/writing/beats.html
new file mode 100644
index 0000000..1a3b2b7
--- /dev/null
+++ b/templates/writing/beats.html
@@ -0,0 +1,9 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<h3 class="mb-1">单集节拍表</h3><div class="small text-muted mb-3">项目 {{ project_id }} · 第 {{ episode_number }} 集 · 场景目标、阻力、情绪变化与转折</div>
+{% include "common/workspace/project_progress.html" %}
+<template x-if="creativeWorkspace.project"><div>{% include "common/workspace/project_beats.html" %}</div></template>
+{% include "common/workspace/creative_progress_modal.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/common/creative.js"></script><script src="/static/js/writing/beats.js"></script>{% endblock %}
diff --git a/templates/writing/outlines.html b/templates/writing/outlines.html
new file mode 100644
index 0000000..bd9dd12
--- /dev/null
+++ b/templates/writing/outlines.html
@@ -0,0 +1,9 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<h3 class="mb-1">全剧大纲</h3><div class="small text-muted mb-3">项目 {{ project_id }} · 全剧阶段弧线与分集大纲</div>
+{% include "common/workspace/project_progress.html" %}
+<template x-if="creativeWorkspace.project"><div>{% include "common/workspace/project_outlines.html" %}</div></template>
+{% include "common/workspace/creative_progress_modal.html" %}
+{% endblock %}
+{% block scripts %}<script src="/static/js/common/creative.js"></script><script src="/static/js/writing/outlines.js"></script>{% endblock %}
diff --git a/templates/writing/projects.html b/templates/writing/projects.html
new file mode 100644
index 0000000..e9fe40c
--- /dev/null
+++ b/templates/writing/projects.html
@@ -0,0 +1,7 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between mb-3"><div><h3 class="mb-1">编剧项目</h3><div class="small text-muted">查看已分配且已批准前置版本的项目</div></div><button class="btn btn-outline-secondary btn-sm" @click="fetchCreativeProjects()"><i class="bi bi-arrow-clockwise"></i></button></div>
+<table class="table table-sm align-middle"><thead><tr><th>项目</th><th>来源选题</th><th>目标集数</th><th>当前阶段</th><th>操作</th></tr></thead><tbody><template x-for="project in creativeWorkspace.projects" :key="project.id"><tr><td x-text="project.name"></td><td x-text="project.topic_title"></td><td x-text="project.target_episode_count"></td><td x-text="creativeProjectStatusLabel(project.status)"></td><td><a class="btn btn-sm btn-outline-light" :href="`/writing/projects/${project.id}/outlines`">打开大纲</a></td></tr></template><tr x-show="!creativeWorkspace.projects.length"><td colspan="5" class="text-center text-muted py-4">暂无已分配项目</td></tr></tbody></table>
+{% endblock %}
+{% block scripts %}<script src="/static/js/common/creative.js"></script>{% endblock %}
diff --git a/tests/test_role_page_content.py b/tests/test_role_page_content.py
new file mode 100644
index 0000000..ac84551
--- /dev/null
+++ b/tests/test_role_page_content.py
@@ -0,0 +1,192 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+
+from pathlib import Path
+
+
+ROOT = Path(__file__).resolve().parents[1]
+
+
+ROLE_TEMPLATES = (
+    "selection/dashboard.html",
+    "selection/ranks.html",
+    "selection/topics.html",
+    "selection/dramas.html",
+    "selection/breakdowns.html",
+    "planning/projects.html",
+    "planning/directions.html",
+    "planning/brief.html",
+    "planning/characters.html",
+    "writing/projects.html",
+    "writing/outlines.html",
+    "writing/beats.html",
+    "review/projects.html",
+    "review/tasks.html",
+    "review/approvals.html",
+    "review/versions.html",
+    "admin/users.html",
+    "admin/roles.html",
+    "admin/config.html",
+    "admin/data.html",
+    "admin/analytics.html",
+    "admin/jobs.html",
+    "admin/deliveries.html",
+    "admin/audit.html",
+)
+
+
+def _read(relative_path: str) -> str:
+    return (ROOT / relative_path).read_text(encoding="utf-8")
+
+
+def test_role_templates_are_standalone_pages_with_external_scripts():
+    for relative_path in ROLE_TEMPLATES:
+        html = _read(f"templates/{relative_path}")
+        assert '{% extends "layouts/base.html" %}' in html
+        assert "currentTab" not in html
+        assert "<script>" not in html
+
+
+def test_role_templates_do_not_share_unrelated_workspaces():
+    selection = _read("templates/selection/topics.html")
+    writing = _read("templates/writing/beats.html")
+    admin = _read("templates/admin/users.html")
+
+    assert "今日爆款" in selection
+    assert "单集节拍表" not in selection
+    assert "用户管理" not in selection
+    assert "单集节拍表" in writing
+    assert "用户管理" not in writing
+    assert "用户管理" in admin
+    assert "今日爆款" not in admin
+
+
+def test_each_operational_domain_loads_only_external_javascript():
+    expected_scripts = {
+        "templates/selection/ranks.html": "/static/js/selection/ranks.js",
+        "templates/selection/topics.html": "/static/js/selection/topics.js",
+        "templates/selection/dramas.html": "/static/js/selection/dramas.js",
+        "templates/selection/breakdowns.html": "/static/js/selection/breakdowns.js",
+        "templates/planning/projects.html": "/static/js/planning/projects.js",
+        "templates/planning/brief.html": "/static/js/planning/brief.js",
+        "templates/planning/characters.html": "/static/js/planning/characters.js",
+        "templates/writing/outlines.html": "/static/js/writing/outlines.js",
+        "templates/writing/beats.html": "/static/js/writing/beats.js",
+        "templates/review/tasks.html": "/static/js/review/tasks.js",
+        "templates/review/approvals.html": "/static/js/review/approvals.js",
+        "templates/admin/users.html": "/static/js/admin/users.js",
+        "templates/admin/roles.html": "/static/js/admin/roles.js",
+        "templates/admin/jobs.html": "/static/js/admin/jobs.js",
+        "templates/admin/deliveries.html": "/static/js/admin/deliveries.js",
+        "templates/admin/audit.html": "/static/js/admin/audit.js",
+    }
+    for template, script in expected_scripts.items():
+        assert script in _read(template)
+
+
+def test_common_api_owns_authenticated_request_behavior():
+    source = _read("static/js/common/api.js")
+
+    assert "X-CSRF-Token" in source
+    assert "csrf-token" in source
+    assert "response.status === 401" in source
+    assert "window.location.assign('/login')" in source
+    assert "response.status === 403" in source
+    assert "无权执行此操作" in source
+
+
+def test_role_scripts_do_not_depend_on_legacy_current_tab():
+    role_scripts = (ROOT / "static/js").glob("*/*.js")
+    for script in role_scripts:
+        if script.parent.name == "common":
+            continue
+        assert "currentTab" not in script.read_text(encoding="utf-8")
+
+
+def test_legacy_index_is_only_a_compatibility_redirect():
+    html = _read("templates/index.html")
+
+    assert "currentTab ===" not in html
+    assert "创作项目" not in html
+    assert "window.location.replace('/')" in html
+
+
+def test_selection_dashboard_loads_every_module_used_by_its_initializer():
+    dashboard = _read("templates/selection/dashboard.html")
+    common = _read("static/js/common/tasks.js")
+
+    assert "/static/js/selection/topics.js" in dashboard
+    assert "/static/js/selection/ranks.js" not in dashboard
+    assert "if (this.page === 'selection-ranks')" in common
+    assert "this.page === 'selection-ranks' || this.page === 'selection-dashboard'" not in common
+
+
+def test_planning_projects_use_real_stage_routes():
+    project_list = _read("templates/common/workspace/project_list.html")
+    creative = _read("static/js/common/creative.js")
+    directions = _read("templates/planning/directions.html")
+    brief = _read("templates/planning/brief.html")
+
+    assert "`/planning/projects/${project.id}/directions`" in project_list
+    assert "`/planning/projects/${data.id}/directions`" in creative
+    assert 'href="/planning/projects/{{ project_id }}/brief"' in directions
+    assert 'href="/planning/projects/{{ project_id }}/characters"' in brief
+
+
+def test_writing_outlines_link_each_episode_to_beats_route():
+    outlines = _read("templates/common/workspace/project_outlines.html")
+    creative = _read("static/js/common/creative.js")
+
+    assert (
+        "`/writing/projects/${creativeWorkspace.project.id}/episodes/"
+        "${item.episode_number}/beats`"
+    ) in outlines
+    assert "creativeWorkspace.step = 'beats'" not in creative
+
+
+def test_selection_drama_page_cannot_edit_admin_credentials():
+    selection = _read("templates/common/workspace/selection_dramas.html")
+    selection_script = _read("static/js/selection/dramas.js")
+    admin = _read("templates/admin/config.html")
+    admin_script = _read("static/js/admin/workspace.js")
+
+    for forbidden in (
+        "配置中心地址",
+        "激活码",
+        "config_url",
+        "activationCode",
+        "saveDramaSettings",
+        "testDramaInterface",
+    ):
+        assert forbidden not in selection
+        assert forbidden not in selection_script
+    assert "配置中心地址" in admin
+    assert "activationCode" in admin
+    assert "saveDramaSettings" in admin_script
+    assert "testDramaInterface" in admin_script
+
+
+def test_admin_identity_pages_expose_complete_management_flows():
+    users_html = _read("templates/admin/users.html")
+    users_js = _read("static/js/admin/users.js")
+    roles_html = _read("templates/admin/roles.html")
+    roles_js = _read("static/js/admin/roles.js")
+
+    assert 'id="user-editor-modal"' in users_html
+    assert 'id="user-role-select"' in users_html
+    assert "/api/admin/users/${user.id}/reset-password" in users_js
+    assert "method: 'PATCH'" in users_js
+    assert "role_ids" in users_js
+    assert "toggleUserStatus" in users_js
+
+    assert 'id="role-editor-modal"' in roles_html
+    assert 'id="permission-editor"' in roles_html
+    assert "/api/admin/permissions" in roles_js
+    assert "/api/admin/roles/${role.id}/clone" in roles_js
+    assert "permissions" in roles_js
+    assert "toggleRoleStatus" in roles_js
diff --git a/tests/test_webgui_ui_changes.py b/tests/test_webgui_ui_changes.py
index d4f88b4..c2c74c6 100644
--- a/tests/test_webgui_ui_changes.py
+++ b/tests/test_webgui_ui_changes.py
@@ -22,20 +22,37 @@ from pathlib import Path
 import pytest
 from fastapi import HTTPException
 from fastapi.testclient import TestClient
 
 import WebGUI
 from core.events import WorkerEvent
 from core.job import CrawlJob
 from runtime.job_state import JobState
 
 
+def _source(*relative_paths: str) -> str:
+    return "\n".join(
+        Path(path).read_text(encoding="utf-8") for path in relative_paths
+    )
+
+
+def _workspace_source() -> str:
+    templates = sorted(Path("templates").glob("**/*.html"))
+    scripts = sorted(Path("static/js").glob("**/*.js"))
+    styles = sorted(Path("static/css").glob("*.css"))
+    artifacts = [path for path in templates if path.name != "index.html"]
+    return "\n".join(
+        path.read_text(encoding="utf-8")
+        for path in (*artifacts, *scripts, *styles)
+    )
+
+
 def test_webgui_registers_rag_routes():
     paths = {route.path for route in WebGUI.app.routes}
 
     assert "/api/rag/status" in paths
     assert "/api/rag/index" in paths
     assert "/api/rag/query" in paths
 
 
 def test_webgui_registers_analytics_routes():
     paths = {route.path for route in WebGUI.app.routes}
@@ -49,249 +66,323 @@ def test_webgui_registers_analytics_routes():
 
 def test_webgui_registers_today_rank_route():
     paths = {route.path for route in WebGUI.app.routes}
 
     assert "/api/rank/today" in paths
     assert "/favicon.ico" in paths
 
 
 def test_webgui_registers_creative_phase3_routes_and_page():
     paths = {route.path for route in WebGUI.app.routes}
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "/api/creative/topics/generate" in paths
     assert "/api/creative/breakdowns" in paths
     assert "今日爆款" in html
     assert "generateCreativeTopics" in html
     assert '@change="generateCreativeTopics"' in html
 
 
 def test_webgui_registers_phase4_routes_and_flat_project_workspace():
     paths = {route.path for route in WebGUI.app.routes}
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "/api/creative/projects" in paths
     assert "/api/creative/projects/{project_id}" in paths
     assert "/api/creative/projects/{project_id}/blueprints/generate" in paths
     assert "/api/creative/blueprints/{blueprint_id}/lock-brief" in paths
     assert "/api/creative/projects/{project_id}/characters/generate" in paths
     assert "/api/creative/projects/{project_id}/outlines/generate" in paths
     assert "/api/creative/projects/{project_id}/episodes/{episode_number}/beats/generate" in paths
     assert "/api/jobs/{job_id}/retry" in paths
     assert "创作项目" in html
-    assert "currentTab === 'creative-projects'" in html
+    assert "currentTab" not in html
+    assert "/planning/projects" in _source("web/navigation_service.py")
     assert "fetchCreativeProjects" in html
     assert "generateProjectBlueprints" in html
     assert "lockProjectBrief" in html
     assert "generateProjectCharacters" in html
     assert "generateProjectOutlines" in html
     assert "generateProjectBeats" in html
     assert "pauseCreativeJob" in html
     assert "retryCreativeJob" in html
     assert "creative-workspace-scroll" not in html
     assert 'class="table table-sm table-hover align-middle mb-4"' not in html
     assert "creativeWorkspace.projects[0]?.id" in html
-    assert '@click="selectCreativeProject(project.id)"' in html
+    assert ':href="`/planning/projects/${project.id}/directions`"' in html
     assert "--bs-table-active-color: var(--text);" in html
 
 
 def test_project_table_uses_name_navigation_without_redundant_action_column():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
-    section = html.split('<section class="creative-project-workspace" x-show="currentTab === \'creative-projects\'">', 1)[1].split("</section>", 1)[0]
+    section = _source("templates/common/workspace/project_list.html")
+    html = section + _source("static/css/workspaces.css")
     project_table = section.split('<table class="table table-sm align-middle mb-4">', 1)[1].split("</table>", 1)[0]
 
     assert ">操作</th>" not in project_table
     assert 'title="打开项目"' not in project_table
     assert "bi-arrow-right" not in project_table
     assert 'class="project-name-button"' in project_table
-    assert '@click.stop="selectCreativeProject(project.id)"' in project_table
-    assert ':aria-current="creativeWorkspace.project?.id === project.id ? \'page\' : null"' in project_table
+    assert ':href="`/planning/projects/${project.id}/directions`"' in project_table
+    assert '@click.stop="selectCreativeProject(project.id)"' not in project_table
+    assert ':aria-current="creativeWorkspace.project?.id === project.id ? \'page\' : null"' not in project_table
     assert ".creative-project-row.table-active" in html
     assert ".project-name-button:focus-visible" in html
     assert 'colspan="5"' in project_table
 
 
 def test_creative_project_creation_supports_selecting_a_locked_breakdown_version():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "creativeWorkspace.lockedBreakdowns" in html
     assert "loadCreativeProjectBreakdowns" in html
     assert "选择拆解版本" in html
     assert ".form-select:disabled" in html
     assert "background-color: #151722;" in html
 
 
 def test_draft_breakdowns_are_visible_but_not_project_eligible():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "breakdownStatusLabel(topic)" in html
     assert "breakdownStatusClass(topic)" in html
     assert "草稿${version} · 待锁定" in html
     assert "草稿${version} · 已有锁定版" in html
     assert "继续拆解" in html
-    assert "fetch('/api/creative/topics?status=adopted&limit=500')" in html
+    assert "aiworkerApi.request('/api/creative/topics?status=adopted&limit=500')" in html
     assert ':disabled="!topic.locked_breakdown_id"' in html
     assert 'x-text="creativeTopicOptionLabel(topic)"' in html
 
 
 def test_today_hit_actions_follow_topic_status_without_conflicting_commands():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
-    section = html.split('<section x-show="currentTab === \'creative\'">', 1)[1].split("</section>", 1)[0]
+    html = _source("templates/common/workspace/selection_topics.html")
+    section = html.split('<h3 class="mb-0">今日爆款</h3>', 1)[1].split('<h3 class="mb-1">选题库</h3>', 1)[0]
 
     assert 'x-show="!topic.latest_breakdown_id && topic.status === \'pending_review\'" @click="updateCreativeTopic(topic, \'adopted\')"' in section
     assert 'x-show="!topic.latest_breakdown_id && topic.status === \'pending_review\'" @click="updateCreativeTopic(topic, \'rejected\')"' in section
     assert "!topic.latest_breakdown_id && ['pending_review', 'adopted'].includes(topic.status)" in section
     assert 'x-show="!topic.latest_breakdown_id && topic.status === \'rejected\'" @click="updateCreativeTopic(topic, \'adopted\')"' in section
     assert "重新采纳" in section
 
 
 def test_today_hit_actions_distinguish_existing_breakdowns():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
-    section = html.split('<section x-show="currentTab === \'creative\'">', 1)[1].split("</section>", 1)[0]
+    html = _source("templates/common/workspace/selection_topics.html")
+    section = html.split('<h3 class="mb-0">今日爆款</h3>', 1)[1].split('<h3 class="mb-1">选题库</h3>', 1)[0]
 
     assert 'x-show="!topic.latest_breakdown_id && topic.status === \'pending_review\'"' in section
     assert "!topic.latest_breakdown_id && ['pending_review', 'adopted'].includes(topic.status)" in section
     assert 'x-show="topic.latest_breakdown_id && topic.status !== \'archived\'"' in section
     assert '@click="createCreativeBreakdown(topic)">再次拆解</button>' in section
     assert '@click="updateCreativeTopic(topic, \'archived\')">归档</button>' in section
 
 
+def test_topic_library_shows_full_lifecycle_and_breakdown_version_column():
+    html = _workspace_source()
+    section = _source("templates/common/workspace/selection_topics.html").split(
+        '<h3 class="mb-1">选题库</h3>', 1
+    )[1]
+
+    assert "lifecycle_status" in html
+    assert "已下载 X 集" in section
+    assert "人物关系待生成" in section
+    assert "分集大纲已生成" in section
+    assert "Beats脚本已导出" in section
+    assert "<th>状态</th><th>拆解版本</th><th>操作</th>" in section
+    assert "breakdown-version-badge" in section
+    assert "lifecycle-adopted" in html
+    assert "lifecycle-beats-generated" in html
+    assert "lifecycle-beats-exported" in html
+
+
+def test_download_modal_restores_running_job_and_explains_progress():
+    html = _workspace_source()
+
+    assert "findDramaDownloadJob" in html
+    assert "restoreDramaDownloadJob" in html
+    assert "scrollDramaDownloadToTop" in html
+    assert "dramaDownload.jobStatus" in html
+    assert "当前处理" in html
+    assert "关闭窗口不会中断下载任务" in html
+
+
 def test_blueprint_core_conflict_uses_the_expandable_description_cell_pattern():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "blueprint-conflict-cell" in html
     assert ".blueprint-conflict-cell:hover" in html
     assert 'x-text="shortText(item.content.core_conflict, 42)"' not in html
 
 
 def test_character_editor_expands_long_values_and_confirms_row_save():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "creative-inline-textarea" in html
+
+
+def test_blueprint_editor_exposes_generated_direction_audience():
+    html = _workspace_source()
+
+    assert 'x-model="selectedProjectBlueprint().content.audience"' in html
+    assert "方向目标受众" in html
     assert "textareaRows(" in html
     assert "creativeWorkspace.saveState" in html
     assert "人物卡已保存" in html
     assert "creative-save-toast" in html
     assert "creative-save-status" in html
     assert "creativeItemSaveLabel" in html
 
 
 def test_beats_generation_shows_local_start_and_progress_feedback():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "creativeActionRunning('beats')" in html
     assert "正在创建生成任务" in html
     assert "beats-generation-status" in html
 
 
 def test_blueprint_generation_shows_immediate_and_live_progress_feedback():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert 'x-show="creativeWorkspace.startingAction || creativeWorkspace.jobId"' in html
     assert 'role="status" aria-live="polite"' in html
     assert "creativeActionRunning('blueprints')" in html
     assert "creativeProgressIndeterminate()" in html
     assert "creativeProgressElapsedText()" in html
     assert "tickCreativeGenerationElapsed" in html
     assert "creativeWorkspace.jobId = ''" in html
     assert "creativeWorkspace.jobProgress = {indeterminate: true}" in html
     assert "正在创建生成任务" in html
 
 
 def test_character_and_outline_generation_show_the_same_button_progress_feedback():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "creativeActionRunning('characters')" in html
     assert "creativeWorkspace.startingAction === 'characters' ? '正在启动' : '生成中'" in html
     assert "creativeActionRunning('outlines')" in html
     assert "creativeWorkspace.startingAction === 'outlines' ? '正在启动' : '生成中'" in html
     assert html.count(':disabled="creativeActionDisabled(') >= 3
 
 
+def test_creative_generation_opens_progress_modal_with_retry_controls():
+    html = _workspace_source()
+
+    assert 'id="creativeGenerationProgressModal"' in html
+    assert "this.modal('creativeGenerationProgressModal')" in html
+    assert "this.creativeGenerationProgressModal.show()" in html
+    assert "人物关系生成任务已发起" in html
+    assert "关闭弹窗不会中断生成任务" in html
+    assert '@click="retryCreativeJob"' in html
+
+
+def test_downloaded_topic_action_opens_episode_transcript_preview():
+    html = _workspace_source()
+
+    assert 'x-show="Number(topic.downloaded_episode_count || 0) > 0"' in html
+    assert '@click="openTopicTranscriptPreview(topic)"' in html
+    assert 'title="预览剧目转写"' in html
+    assert 'id="topicTranscriptPreviewModal"' in html
+    assert "this.modal('topicTranscriptPreviewModal')" in html
+    assert "/api/creative/topics/${topic.id}/transcripts" in html
+    assert "selectedTopicTranscript()" in html
+    assert "上一集" in html
+    assert "下一集" in html
+
+
 def test_next_stage_generation_is_not_blocked_by_a_stale_previous_action():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "creativeActionDisabled(action)" in html
     assert "this.creativeWorkspace.activeAction === action && this.creativeJobRunning()" in html
     assert ':disabled="creativeActionDisabled(\'outlines\')"' in html
     assert ':disabled="creativeActionDisabled(\'characters\')"' in html
     assert ':disabled="creativeActionDisabled(\'blueprints\')"' in html
 
 
 def test_saved_creative_results_reconcile_a_stale_running_indicator():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "creativeProjectHasJobResult(project, action, jobId)" in html
     assert "item.generation_meta?.job_id === jobId" in html
     assert "reconcileCreativeGenerationState(project)" in html
     assert "this.creativeWorkspace.jobStatus = 'succeeded'" in html
     assert "this.creativeWorkspace.jobProgress = {completed: 1, total: 1}" in html
     assert "生成完成，结果已回填到当前项目" in html
 
 
 def test_creative_running_state_stops_as_soon_as_its_saved_result_is_visible():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "creativeJobStatusRunning()" in html
     assert "if (!this.creativeJobStatusRunning()) return false" in html
     assert "!this.creativeProjectHasJobResult(" in html
-    assert "if (!this.creativeJobStatusRunning() || !this.creativeProjectHasJobResult" in html
+    assert "if (!this.creativeProjectHasJobResult(project, action, jobId)) return false" in html
+
+
+def test_terminal_creative_job_always_clears_the_action_even_if_result_matching_lags():
+    html = _workspace_source()
+
+    assert "finishCreativeGeneration()" in html
+    assert "this.finishCreativeGeneration();" in html
+    assert "this.creativeWorkspace.activeAction = ''" in html
+    assert "this.creativeWorkspace.startingAction = ''" in html
 
 
 def test_breakdown_editor_opens_in_modal_instead_of_page_bottom():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert 'id="breakdownEditorModal"' in html
     assert 'x-model="breakdownEditor.content[item.key]"' in html
     assert "breakdownEditor.content[item.key][index]" in html
     assert "addBreakdownArrayItem" in html
     assert "removeBreakdownArrayItem" in html
     assert "breakdownEditor.fieldGuide" in html
     assert "item.writing_prompt" in html
     assert "item.evidence_rule" in html
     assert "openBreakdownEditor" in html
     assert "saveBreakdownEditor" in html
     assert "lockBreakdownEditor" in html
-    assert "new bootstrap.Modal(document.getElementById('breakdownEditorModal'))" in html
+    assert "this.modal('breakdownEditorModal')" in html
     assert "normalizeBreakdownEditorContent" in html
     assert "breakdownEditor.draft" not in html
     assert 'class="card mt-3" x-show="topicLibrary.breakdown"' not in html
     assert 'class="card mt-3" x-show="creative.breakdown"' not in html
 
 
 def test_breakdown_generation_requires_transcript_and_shows_progress():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert 'id="transcriptRequiredModal"' in html
     assert "请先下载剧目脚本再拆解" in html
     assert 'id="breakdownGenerationProgressModal"' in html
     assert "startTranscriptBreakdown" in html
     assert "syncBreakdownGenerationProgress" in html
     assert "openTopicDownload(breakdownGeneration.topic)" in html
     assert "breakdownGeneration.coverage" in html
     assert "payload?.breakdown_id" in html
-    assert "fetch(`/api/jobs/${job.job_id}`)" in html
+    assert "aiworkerApi.request(`/api/jobs/${job.job_id}`)" in html
     assert "createTopicLibraryBreakdown(topic) {" in html
     assert "return this.startTranscriptBreakdown(topic, 'topic-library')" in html
     assert "createCreativeBreakdown(topic) {" in html
     assert "return this.startTranscriptBreakdown(topic, 'creative')" in html
     assert "breakdownGenerationProgressLabel" in html
     assert "breakdownGenerationIndeterminate" in html
     assert "elapsed_seconds" in html
     assert "this.breakdownGenerationRunning() ? 8" not in html
     assert "tickBreakdownGenerationElapsed" in html
     assert "formatBreakdownGenerationElapsed" in html
     assert "`已等待 ${this.formatBreakdownGenerationElapsed(seconds)}`" in html
+    assert "已发现下载视频，正在生成剧本转写" in html
+    assert "已发现第 ${breakdownGeneration.coverage.join('、')} 集下载视频" in html
 
 
 def test_existing_breakdown_opens_without_generation_and_regeneration_is_explicit():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "data.status === 'existing'" in html
     assert "data.breakdown" in html
     assert "this.breakdownGeneration.status = 'existing'" in html
     assert "openBreakdownEditor(source, topic.title, data.breakdown)" in html
     assert "regenerateCurrentBreakdown" in html
     assert "force_regenerate: forceRegenerate" in html
     assert "startTranscriptBreakdown(topic, source, true)" in html
     assert ">重新生成</button>" in html
 
@@ -330,34 +421,40 @@ def test_webgui_python_source_contains_no_mojibake_text():
         try:
             decoded = line.encode("gb18030").decode("utf-8")
         except (UnicodeEncodeError, UnicodeDecodeError):
             continue
         if decoded != line and any(ord(char) > 127 for char in line):
             reversible_lines.append((number, line, decoded))
 
     assert reversible_lines == []
 
 
-def test_index_template_has_today_rank_page():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+def test_selection_rank_template_has_today_rank_page():
+    html = _source(
+        "templates/selection/ranks.html",
+        "templates/common/workspace/selection_ranks.html",
+        "static/js/selection/ranks.js",
+        "static/js/common/tasks.js",
+        "static/css/workspaces.css",
+    )
 
-    assert "currentTab === 'today-rank'" in html
-    assert "currentTab === 'today-rank'" in html
+    assert "currentTab" not in html
+    assert "/static/js/selection/ranks.js" in html
     assert "fetchTodayRank" in html
     assert "async fetchJobs" in html
     assert "async stopCrawler" in html
     assert "todayRankDates" in html
     assert "todayRankPlatform" in html
     assert "数据质量提示" not in html
     assert "validationText" not in html
     assert "排名变动剧目" in html
-    assert "题材" in html
+    assert "{key: 'subject', label: '标签'" in html
     assert "item.subject" in html
     assert "topic_tags" in html
     assert "shortTags" in html
     assert "shortText" in html
     assert "warningText" in html
     assert "ranking-heading" in html
     assert "ranking-title" in html
     assert "rank-filter-btn" in html
     assert "rank-number" in html
     assert "rankNumberClass" in html
@@ -388,34 +485,34 @@ def test_index_template_has_today_rank_page():
     assert "min-width: 88px" not in html
     assert "min-width: 96px" not in html
     assert "max-height: 460px" not in html
     assert "ranking-summary" not in html
     assert "summaryText(ranking.summary)" not in html
     assert "todayRankDate = ''; fetchTodayRank()" not in html
     assert 'type="date" x-model="todayRankDate"' not in html
 
 
 def test_today_rank_duration_column_is_after_hot_comment_and_formats_both_day_counts():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     hot_comment = html.index("{key: 'hot_comment', label: '热评'")
     rank_duration = html.index("{key: 'rank_duration', label: '在榜时长'")
     assert hot_comment < rank_duration
     assert "column.kind === 'rank_duration'" in html
     assert "formatRankDuration" in html
     assert "连续在榜" in html
     assert "累计在榜" in html
 
 
 def test_removed_assets_and_rag_pages_are_not_in_sidebar():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
-    sidebar = html.split("<nav class=\"nav flex-column py-3\">", 1)[1].split("</nav>", 1)[0]
+    html = _source("templates/layouts/base.html")
+    sidebar = html.split('<nav class="nav flex-column py-3"', 1)[1].split("</nav>", 1)[0]
 
     assert "数据资产" not in sidebar
     assert "RAG 问答" not in sidebar
     assert "currentTab === 'assets'" not in sidebar
     assert "currentTab === 'rag'" not in sidebar
 
 
 def test_backend_log_configuration_writes_rotating_log(tmp_path):
     log_path = tmp_path / "logs" / "webgui.log"
 
@@ -441,21 +538,21 @@ def test_hongguo_rank_job_uses_csv_without_interface_extra():
         get_sub_comment=False,
     )
 
     job = WebGUI.create_job_from_config(config)
 
     assert not any(key.startswith("HONGGUO_") for key in job.extra)
 
 
 def test_hongguo_drama_routes_replace_legacy_proxy_routes():
     paths = {route.path for route in WebGUI.app.routes}
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "/api/hongguo/detail-monitor/status" not in paths
     assert "/hongguo/health" not in paths
     assert "/hongguo/api/api.php" not in paths
     assert "runRankCrawler('hg')" in html
     assert "detail-monitor" not in html
     assert "/api/hongguo/drama/settings" in paths
     assert "/api/hongguo/drama/search" in paths
     assert "/api/hongguo/drama/suggest" in paths
     assert "/api/hongguo/drama/episodes" in paths
@@ -657,34 +754,34 @@ async def test_delete_files_removes_multiple_selected_data_files(tmp_path, monke
     )
 
     assert result["status"] == "success"
     assert result["deleted_paths"] == ["data/jobs/demo/first.json", "data/jobs/demo/second.md"]
     assert not first.exists()
     assert not second.exists()
     assert retained.exists()
 
 
 def test_data_management_template_supports_multi_file_deletion():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "selectedFilePaths: []" in html
     assert "batchDeleteMode: false" in html
     assert "startBatchDelete" in html
     assert 'x-show="batchDeleteMode"' in html
     assert "toggleAllVisibleFiles" in html
     assert "deleteSelectedFiles" in html
     assert "method: 'DELETE'" in html
     assert "bi bi-trash" in html
 
 
 def test_data_management_template_supports_grouped_online_preview():
-    html = Path("templates/index.html").read_text(encoding="utf-8")
+    html = _workspace_source()
 
     assert "groupedFiles()" in html
     assert "group.file_groups" in html
     assert "file.drama_name" in html
     assert "file.file_group" in html
     assert "openFilePreview(file)" in html
     assert 'id="filePreviewModal"' in html
     assert '<video' in html
     assert '<audio' in html
     assert "previewFile.content" in html
diff --git a/web/page_router.py b/web/page_router.py
index b3939a5..615a085 100644
--- a/web/page_router.py
+++ b/web/page_router.py
@@ -14,20 +14,49 @@ from fastapi import APIRouter, Depends, HTTPException, Request
 from fastapi.responses import HTMLResponse, RedirectResponse, Response
 from fastapi.routing import APIRoute
 from fastapi.templating import Jinja2Templates
 
 from identity.dependencies import current_principal, require_page
 from identity.schema import Principal
 
 from .navigation_service import PAGE_ROUTES, NavigationService
 
 
+PAGE_TEMPLATES = {
+    "/selection/dashboard": "selection/dashboard.html",
+    "/selection/ranks": "selection/ranks.html",
+    "/selection/topics": "selection/topics.html",
+    "/selection/dramas": "selection/dramas.html",
+    "/selection/breakdowns": "selection/breakdowns.html",
+    "/planning/projects": "planning/projects.html",
+    "/planning/projects/{project_id}/directions": "planning/directions.html",
+    "/planning/projects/{project_id}/brief": "planning/brief.html",
+    "/planning/projects/{project_id}/characters": "planning/characters.html",
+    "/writing/projects": "writing/projects.html",
+    "/writing/projects/{project_id}/outlines": "writing/outlines.html",
+    "/writing/projects/{project_id}/episodes/{episode_number}/beats": "writing/beats.html",
+    "/review/projects": "review/projects.html",
+    "/review/tasks": "review/tasks.html",
+    "/review/approvals": "review/approvals.html",
+    "/review/projects/{project_id}/members": "review/projects.html",
+    "/review/projects/{project_id}/versions": "review/versions.html",
+    "/admin/users": "admin/users.html",
+    "/admin/roles": "admin/roles.html",
+    "/admin/config": "admin/config.html",
+    "/admin/data": "admin/data.html",
+    "/admin/analytics": "admin/analytics.html",
+    "/admin/jobs": "admin/jobs.html",
+    "/admin/deliveries": "admin/deliveries.html",
+    "/admin/audit": "admin/audit.html",
+}
+
+
 def build_page_router(
     templates: Jinja2Templates, navigation_service: NavigationService
 ) -> APIRouter:
     class PageRoute(APIRoute):
         def get_route_handler(self) -> Callable:
             handler = super().get_route_handler()
 
             async def handle(request: Request) -> Response:
                 try:
                     return await handler(request)
@@ -72,82 +101,99 @@ def build_page_router(
         request: Request, principal: Principal = Depends(current_principal)
     ) -> Response:
         return _page_response(
             templates,
             navigation_service,
             request,
             principal,
             page_title="我的任务",
         )
 
-    def page_endpoint(page_title: str, permission: str):
+    def page_endpoint(page_title: str, permission: str, template_name: str):
         def endpoint(
             request: Request,
             principal: Principal = Depends(require_page(permission)),
         ) -> Response:
             return _page_response(
                 templates,
                 navigation_service,
                 request,
                 principal,
                 page_title=page_title,
+                template_name=template_name,
             )
 
         return endpoint
 
     for route, permission, title in PAGE_ROUTES:
         router.add_api_route(
             route,
-            page_endpoint(title, permission),
+            page_endpoint(title, permission, PAGE_TEMPLATES[route]),
             methods=["GET"],
             response_class=HTMLResponse,
             name=f"page_{permission}_{title}",
             openapi_extra={"required_page_permission": permission},
         )
 
     return router
 
 
 def _page_response(
     templates: Jinja2Templates,
     navigation_service: NavigationService,
     request: Request,
     principal: Principal,
     *,
     page_title: str,
+    template_name: str = "common/tasks.html",
 ) -> Response:
     response = templates.TemplateResponse(
         request,
-        "common/tasks.html",
+        template_name,
         _template_context(
             request,
             principal,
             navigation_service,
             page_title=page_title,
+            workspace_page=(
+                ""
+                if template_name == "common/tasks.html"
+                else template_name.removesuffix(".html").replace("/", "-")
+            ),
         ),
     )
     navigation_service.remember_route(principal, request.url.path)
     return response
 
 
 def _template_context(
     request: Request,
     principal: Principal | None,
     navigation_service: NavigationService,
     *,
     page_title: str,
+    workspace_page: str = "",
 ) -> dict:
+    csrf_token = ""
+    service = getattr(request.app.state, "identity_permission_service", None)
+    if principal is not None and service is not None:
+        raw_token = request.cookies.get(service.session_service.cookie_name)
+        session = service.session_service.resolve_session(raw_token)
+        csrf_token = session.csrf_token if session is not None else ""
     return {
         "request": request,
         "principal": principal,
         "navigation": navigation_service.items_for(principal) if principal else [],
         "current_route": request.url.path,
         "page_title": page_title,
+        "workspace_page": workspace_page,
+        "csrf_token": csrf_token,
+        **request.path_params,
     }
 
 
 def _principal_from_request(request: Request) -> Principal | None:
     service = getattr(request.app.state, "identity_permission_service", None)
     if service is None:
         return None
     raw_token = request.cookies.get(service.session_service.cookie_name)
     return service.principal_for_session(raw_token)
