# 负责项目成员编辑实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在负责项目操作列提供受权限保护的项目成员编辑入口和完整成员增删页面。

**架构：** 现有工作流成员仓库继续承担增删与项目管理校验；工作流 API 新增项目候选人查询并丰富成员显示字段。负责项目模板和脚本使用现有动态路由实现列表态与成员编辑态，不引入新的前端依赖。

**技术栈：** FastAPI、SQLAlchemy、PostgreSQL、Jinja2、原生 JavaScript、Bootstrap、pytest、Playwright

---

### 任务 1：成员候选与显示信息 API

**文件：**
- 修改：`workflow/api.py`
- 修改：`web/page_access_catalog.py`
- 创建：`tests/test_project_member_management_api.py`

- [ ] 编写 PostgreSQL 失败测试：项目负责人能读取三类候选人，成员列表包含 `username/display_name`，非管理者返回 403。
- [ ] 运行 `pytest tests/test_project_member_management_api.py -q`，确认候选接口为 404 或响应缺少显示字段。
- [ ] 新增 `GET /api/workflow/projects/{project_id}/member-candidates?project_role=...`，仅支持 `project_owner/creative_planner/screenwriter`，要求 `project.member.manage` 并校验项目管理关系。
- [ ] 丰富成员列表响应但不修改仓库存储模型；在成员管理页 API 白名单加入候选接口。
- [ ] 重跑目标测试确认通过。

### 任务 2：负责项目入口与成员编辑页面

**文件：**
- 修改：`templates/review/projects.html`
- 修改：`static/js/review/projects.js`
- 修改：`tests/test_role_page_content.py`
- 创建：`tests/test_project_member_management_browser.py`

- [ ] 编写失败测试：有权限时操作列生成成员入口；成员页能切换角色候选、添加成员、按成员关系移除并刷新列表。
- [ ] 运行目标静态与浏览器测试，确认入口和编辑控件缺失。
- [ ] 在模板中增加权限数据、成员编辑表单、加载/空/错误状态和成员表格；窄屏下操作按钮允许换行且表格保持横向滚动。
- [ ] 在脚本中实现列表态入口和成员编辑态的数据加载、候选过滤、添加、确认移除、保存中禁用与错误提示。
- [ ] 运行浏览器测试、相关页面授权测试、JavaScript 语法检查、Python 编译、文件头检查和 `git diff --check`。
- [ ] 重启 8899 WebGUI，验证在线页面和接口已加载。
