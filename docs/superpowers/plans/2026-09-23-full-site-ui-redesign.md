# AiWorker 全站 UI 重构实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改变既有路由、API、字段、权限和 Alpine 行为绑定的前提下，把选择、策划、写作与 Prompt 页面统一为可操作的工作台界面。

**架构：** 共享 `workspaces.css` 提供语义令牌、页面头部、上下文工具栏、双栏/三栏布局和状态块；Jinja 模板只增加包裹层和信息层级，保留既有 DOM id、`x-*`、`@click` 与 data 属性。页面族按选题、创作、Prompt 分开改动，使用独立子代理和独立测试。

**技术栈：** FastAPI、Jinja2、Bootstrap 5、Bootstrap Icons、Alpine.js、原生 JavaScript、pytest、Playwright。

---

## 文件职责与写入边界

- 修改 `static/css/workspaces.css`：全站工作台令牌、页面壳、工具栏、split layout、状态与响应式。
- 修改 `templates/layouts/base.html`：工作台页面头部插槽与统一语义容器，不添加页面专属资源。
- 修改 `templates/selection/*.html` 及 `templates/common/workspace/selection_*.html`：选题 Filter / Inspect 结构。
- 修改 `templates/planning/*.html`、`templates/writing/*.html` 及 `templates/common/workspace/project_*.html`：Command / Inspect 结构。
- 修改 `templates/common/workspace/prompt_template_center.html`、`prompt_template_effects.html` 及 prompt 相关 include：Step / Library / Effects 结构。
- 修改 `tests/test_frontend_performance_contract.py`、`tests/test_webgui_ui_changes.py`、`tests/test_role_page_content.py`：固化页面族结构、资源边界和可访问性契约。
- 不修改 `web/`、API handler、数据库模型、业务脚本中的保存 payload；除非测试证明某个现有选择器因包裹层回归，才在对应页面族内最小调整。

## 实施顺序

1. 先由主代理完成基线检查和共享 CSS 契约测试。
2. 并行委派三个互不重叠的页面族：选题、创作、Prompt。
3. 主代理整合三个页面族，补齐共享壳和测试，运行定向测试。
4. 由子代理复核 DOM/资源/视觉验收，主代理修复回归并完成三种视口检查。

### 任务 1：补齐全站 UI 契约与共享布局基础

**文件：**
- 修改：`static/css/workspaces.css`
- 修改：`templates/layouts/base.html`
- 修改：`tests/test_frontend_performance_contract.py`
- 修改：`tests/test_webgui_ui_changes.py`

- [x] **步骤 1：记录隔离工作区并运行现有 UI 基线**

运行：

```powershell
git status --short
uv run pytest tests/test_frontend_performance_contract.py tests/test_webgui_ui_changes.py tests/test_role_page_content.py -q
```

预期：记录当前失败项；不触碰 `8899` 服务和已有未提交计划文件。

- [x] **步骤 2：增加共享结构的失败断言**

为 CSS 令牌与下列类增加断言：`workspace-page-header`、`workspace-context-bar`、`workspace-toolbar`、`workspace-split`、`workspace-list-pane`、`workspace-inspector`、`workspace-state`、`workspace-empty`；为页面模板增加 skip link、main landmark、`aria-live` 和 reduced-motion 断言。

- [x] **步骤 3：实现共享令牌与响应式布局**

在 `workspaces.css` 中用现有颜色令牌补齐页面头部、上下文条、工具栏、split pane、列表/检查区、状态块和 skeleton 样式；加入 `@media (max-width: 999px)` 与 `@media (max-width: 575px)`，窄屏将 split pane 堆叠、工具栏换行、列表横向滚动只作用于局部容器。保留现有类和 Bootstrap 覆盖，避免全局 `overflow-x: hidden` 掩盖布局错误。

- [x] **步骤 4：将基础壳接入语义容器**

在 `base.html` 保留现有导航、toast、权限提示和脚本顺序，仅把 `main` 内页面内容包进兼容的 workspace 容器；补充页面级 focus ring 和 `aria-describedby` 需要的插槽，不把 Alpine 或创作脚本放进 base。

- [x] **步骤 5：运行共享契约测试并提交**

运行：

```powershell
uv run pytest tests/test_frontend_performance_contract.py tests/test_webgui_ui_changes.py -q
```

提交：`git add static/css/workspaces.css templates/layouts/base.html tests/test_frontend_performance_contract.py tests/test_webgui_ui_changes.py; git commit -m "style: 建立全站工作台布局基础"`

### 任务 2：选题页面改为 Filter / Inspect 工作台

**负责子代理写入边界：**
- 修改：`templates/selection/topics.html`
- 修改：`templates/selection/hits.html`
- 修改：`templates/selection/dramas.html`
- 修改：`templates/selection/ranks.html`
- 修改：`templates/common/workspace/selection_topics.html`
- 修改：`templates/common/workspace/selection_hits.html`
- 修改：`templates/common/workspace/selection_dramas.html`
- 修改：`templates/common/workspace/selection_ranks.html`
- 修改：仅在必要时修改对应 `static/js/selection/*.js` 的选择器；不改 API。

- [x] **步骤 1：检查现有 Alpine 绑定与浏览器断言**

运行 `rg -n "x-|@click|id=|data-|fetch|selection" templates/selection templates/common/workspace/selection_* static/js/selection tests/test_*browser.py`，将现有行为选择器记录在子代理返回说明中。

- [x] **步骤 2：建立失败的页面族结构断言**

断言四个页面有 `workspace-page-header`、`workspace-toolbar`，至少一个 `workspace-split`/`workspace-list-pane`/`workspace-inspector`，并保留原有筛选字段、批量动作与结果容器。

- [x] **步骤 3：实现页面头部与工具栏**

把页面标题、更新时间、刷新/采集/批量主动作组织到 `workspace-page-header`；把现有搜索、平台、时间、状态筛选放入 `workspace-toolbar`。不删字段、不改变绑定，只移动包裹层和 Bootstrap 栅格。

- [x] **步骤 4：实现结果列表与详情检查区**

列表保留现有 `x-for`/表格和操作按钮，详情/预览/评论/排名信息放入 `workspace-inspector` 或现有 modal/offcanvas；为空数据、加载和错误容器增加语义类和可操作的重试/筛选入口。

- [x] **步骤 5：验证选题页面并提交**

运行相关静态测试与 `uv run pytest tests/test_today_hit_actions.py tests/test_role_page_content.py -q`（若文件存在则执行），提交：`git commit -m "style: 重构选题数据工作台"`。

### 任务 3：创作项目页统一 Command / Inspect

**负责子代理写入边界：**
- 修改：`templates/planning/projects.html`
- 修改：`templates/planning/brief.html`
- 修改：`templates/planning/characters.html`
- 修改：`templates/writing/projects.html`
- 修改：`templates/writing/outlines.html`
- 修改：`templates/writing/beats.html`
- 修改：`templates/writing/scripts.html`
- 修改：`templates/common/workspace/project_module_navigation.html`
- 修改：`templates/common/workspace/project_progress.html`
- 修改：`templates/common/workspace/project_blueprints.html`
- 修改：`templates/common/workspace/project_characters.html`
- 修改：`templates/common/workspace/project_outlines.html`
- 修改：`templates/common/workspace/project_beats.html`
- 修改：`templates/common/workspace/project_scripts.html`

- [x] **步骤 1：检查并记录核心行为选择器**

运行 `rg -n "x-|@click|id=|data-|creativeWorkspace|save|generate|lock|download" templates/planning templates/writing templates/common/workspace/project_*`；不得改动这些选择器的含义。

- [x] **步骤 2：增加 Command / Inspect 结构断言**

为 Brief、Characters、Outlines、Beats、Scripts 断言项目上下文条、阶段导航、核心内容区、检查区和保留的保存/生成/锁定动作存在。

- [x] **步骤 3：统一页面头部和项目导航**

在现有标题、返回项目、模板、导出按钮外增加 `workspace-page-header` 与 `workspace-context-bar`；将项目阶段导航改为紧凑 horizontal nav，当前阶段仍由现有 `creative_module` 和 Alpine 状态控制。

- [x] **步骤 4：将核心内容与检查区分栏**

不重写业务字段：给现有 blueprint、characters、outlines、beats、scripts include 加 `workspace-split`，核心编辑区使用 `workspace-editor-pane`，进度/版本/连续性/下一步动作使用 `workspace-inspector`。移动端用 CSS 堆叠。

- [x] **步骤 5：验证创作流程并提交**

运行 `uv run pytest tests/test_role_page_content.py tests/test_episode_all_view_browser.py tests/test_original_project_browser.py -q`，提交：`git commit -m "style: 统一创作项目 Command Inspect 布局"`。

### 任务 4：Prompt 页面改为 Step / Library / Effects

**负责子代理写入边界：**
- 修改：`templates/common/workspace/prompt_template_center.html`
- 修改：`templates/common/workspace/prompt_template_effects.html`
- 修改：`templates/common/workspace/prompt_library_modal.html`
- 修改：`templates/common/workspace/prompt_template_effects.html` 相关 include（不改变 API 绑定）。

- [x] **步骤 1：增加失败断言**

断言模板中心和效果页存在 `workspace-context-bar`、`workspace-toolbar`、`workspace-split`、步骤选择、集数选择和既有面板动作；删除“同权重三卡片”只作为视觉结构断言，不删除业务入口。

- [x] **步骤 2：实现紧凑步骤上下文条**

保留 `creativeWorkspace.promptLibrary.stage`、`selectedEpisode` 和 change handlers，将步骤/集数选择移入工具栏，并让标题、当前项目、当前步骤在首屏可读。

- [x] **步骤 3：实现资源导航与内容面板**

把个人、项目、市场、效果入口改成左侧 `workspace-list-pane` 的可聚焦导航，右侧 `workspace-inspector`/内容面板由已有 `openPromptWorkspacePanel()` 切换；保留 modal 作为深层编辑/导入承载。

- [x] **步骤 4：补齐模板列表、空数据与错误状态样式**

只展示已有真实字段（名称、版本、状态、更新时间、动作）；使用共享 state 类，不添加虚构统计。

- [x] **步骤 5：验证 Prompt 页面并提交**

运行 `uv run pytest tests/test_prompt_workspace_browser.py tests/test_prompt_marketplace_browser.py tests/test_role_page_content.py -q`，提交：`git commit -m "style: 重构 Prompt 模板工作台"`。

### 任务 5：整合、性能契约与三视口验收

**文件：**
- 修改：`tests/test_frontend_performance_contract.py`
- 修改：`tests/test_page_authorization.py`（仅当页面资源断言需要补充）
- 修改：相关模板或 CSS（仅修复已证实的回归）

- [x] **步骤 1：由主代理审查所有子代理 diff**

运行 `git diff HEAD~3 -- templates static/css/workspaces.css` 和 `git status --short`，检查没有改变 API、权限、字段、业务脚本依赖或引入新依赖。

- [x] **步骤 2：补齐资源边界与状态契约**

验证 `/tasks` 不加载无关创作脚本，选题页不加载写作/Prompt 模块，Prompt 页不加载选题评论采集模块；验证页面都有 skip link、main、focus、loading/empty/error 类。

- [x] **步骤 3：运行必要定向测试**

```powershell
uv run pytest tests/test_frontend_performance_contract.py tests/test_page_authorization.py tests/test_role_page_content.py tests/test_webgui_ui_changes.py -q
uv run pytest tests/test_original_project_browser.py tests/test_episode_all_view_browser.py tests/test_prompt_workspace_browser.py tests/test_prompt_marketplace_browser.py tests/test_llm_settings_browser.py -q
```

出现失败时只修根因并重跑失败测试，不重复运行已通过套件。

- [ ] **步骤 4：执行三种视口人工验收**

在不触碰 `8899` 的新端口服务上检查 `/tasks`、一个选题页、Brief、Characters、Outlines、Beats、Scripts、Prompt 中心和效果页：`1440×900`、`1024×768`、`390×844`。验证键盘 focus、工具栏、筛选、主动作、空/加载/错误/成功状态和无页面级横向滚动。

- [ ] **步骤 5：记录验收证据并提交收口**

运行 `uv run ruff check .`、`uv lock --check` 和最终定向测试；检查 `git status --short` 只包含预期文档/代码。提交：`git commit -m "feat: 完成全站工作台 UI 重构"`。

## 完成标准

- 选择、策划、写作、Prompt 页面有明显且一致的 UI 结构变化，而非仅追加脚本。
- 所有现有业务动作保持可用，既有浏览器测试通过。
- 共享 CSS 令牌、状态、focus 和响应式规则通过静态契约。
- 三种视口通过人工验收；没有影响当前 `8899` 服务。
- 无 React、Vue、Tailwind、新依赖或新构建链；无用户原有改动被覆盖或提交。

## 执行记录

- 共享工作台壳已接入选题四页、选题概览、策划/写作项目页、写作概览、Prompt 中心/效果页及 `/tasks`；补齐 Characters 局部横向滚动和榜单/项目/任务状态语义。
- 契约测试改为渲染 Jinja include 后检查真实结构，并覆盖关键 ID、Alpine 属性与资源边界。
- 已验证：`uv run pytest tests/test_frontend_performance_contract.py tests/test_page_authorization.py tests/test_role_page_content.py tests/test_episode_all_view_browser.py tests/test_original_project_browser.py tests/test_prompt_workspace_browser.py tests/test_prompt_marketplace_browser.py tests/test_role_page_access_browser.py tests/test_selection_dashboard_browser.py tests/test_selection_project_creation_browser.py -q`（123 passed）；`uv run ruff check .`、`uv lock --check`、75 个模板 Jinja 解析通过。
- `tests/test_webgui_ui_changes.py` 仍受环境阻塞：系统数据库必须提供 `postgresql+psycopg://` 且处于 Alembic head；未修改业务代码绕过该前置条件。
- CUA 浏览器面不可用；已用 Playwright 对隔离端口登录入口做三种视口 smoke（1440×900、1024×768、390×844），页面级 `scrollWidth == innerWidth`。真实登录态工作台逐页视觉验收仍需在可用浏览器/数据库环境完成。
