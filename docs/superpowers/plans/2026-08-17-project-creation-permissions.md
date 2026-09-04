# 项目创建与打开权限实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 以 `project.create` 统一项目创建链路，并让“打开项目”严格按具体项目访问权显示。

**架构：** 身份目录和一次性迁移负责授予最小创建权限；工作流仓库提供受限的创建者初始化；创作 API 返回行级项目访问标记；模板只负责消费权限结果。生产身份数据继续由 PostgreSQL 管理，启动初始化不覆盖后台配置。

**技术栈：** Python 3.11、FastAPI、SQLAlchemy、PostgreSQL、Alembic、Jinja2、Alpine.js、pytest

---

## 文件结构

- 修改 `identity/permission_catalog.py`：新安装角色默认创建权限。
- 创建 `system_database/alembic/versions/0009_project_creator_permission.py`：已有角色一次性增补权限。
- 修改 `workflow/repository.py`：候选读取权限参数和创建者负责人初始化。
- 修改 `creative/api.py`：统一创建授权并返回项目访问标记。
- 修改 `web/page_routes.py`：允许编剧从已授权项目进入 Brief 动态页。
- 修改 `templates/common/workspace/selection_topics.html`：拆分创建和打开门控。
- 修改 `tests/test_planning_assignment_api.py`：真实 PostgreSQL API 和角色渲染回归。
- 修改 `tests/test_page_access_service.py`、`tests/test_webgui_ui_changes.py`：页面继承和模板静态约束。

### 任务 1：建立失败回归测试

- [ ] 修改 `tests/test_planning_assignment_api.py`，使用 `SystemDatabase`、随机测试账号和真实角色 Principal。
- [ ] 断言 screenwriter 拥有 `project.create`，候选接口返回 200，创建接口返回五个任务和创建者负责人成员。
- [ ] 断言无 `project.create` 账号的候选和创建请求返回 403。
- [ ] 渲染选题模板，断言创建按钮由 `project.create` 控制且打开按钮不在同一 Jinja 门控内。
- [ ] 运行目标测试，确认因当前 `project.manage`、`workflow.assign` 和负责人初始化限制而失败。

### 任务 2：实现最小创建权限链路

- [ ] 在默认角色中为 screenwriter 与 project_owner 增加 `project.create`。
- [ ] 创建 0009 迁移，仅用 `ON CONFLICT DO NOTHING` 增补现有 screenwriter/project_owner 的 `project.create`，降级不删除后台权限。
- [ ] 为候选查询增加 `required_permission` 参数，默认仍为 `workflow.assign`。
- [ ] 为 `add_project_member` 增加显式 `allow_creator_bootstrap`，仅允许有 `project.create` 的创建者初始化自己。
- [ ] 将两个项目创建入口和成员候选入口统一为 `project.create`。
- [ ] 运行任务 1 测试，确认通过。

### 任务 3：按项目访问权显示打开按钮

- [ ] 在选题 API 中计算 `can_open_project`。
- [ ] 将创建按钮改为 `project.create` 门控，将打开按钮移出创建门控并使用 `can_open_project`。
- [ ] 为 Brief 动态页增加 `/writing/projects` 附加页面授权。
- [ ] 增加已加入/未加入项目的访问标记测试和页面继承测试。
- [ ] 运行相关测试，确认通过。

### 任务 4：回归与在线验证

- [ ] 运行 `tests/test_planning_assignment_api.py`、页面访问、UI 和身份仓库相关测试。
- [ ] 运行完整 `tests/` 套件并记录任何与本变更无关的历史失败。
- [ ] 运行文件头检查、`node --check` 和 `git diff --check`。
- [ ] 重启 WebGUI，确认在线 OpenAPI、健康状态和静态资源来自同一版本。

