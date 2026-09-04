# 编剧领取已设立项目创作阶段实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让编剧在所有已设立项目中领取无人处理的大纲、Beats、场次剧本任务，并在领取后使用现有编辑工作流。

**架构：** 新增窄权限作为跨项目领取能力；工作流仓库统一负责池任务可见性、冲突判断、原子领取和成员写入。创作 API 只为写作阶段初始化入口放行该能力，后续修改继续依赖现有项目成员和任务执行人校验。

**技术栈：** FastAPI、SQLAlchemy、PostgreSQL、Alembic、pytest、Alpine.js

---

### 任务 1：权限目录与增量迁移

**文件：**
- 修改：`identity/permission_catalog.py`
- 创建：`system_database/alembic/versions/0012_writer_stage_edit.py`
- 测试：`tests/test_system_database_alembic.py`

- [ ] 编写失败测试，断言编剧 Principal 包含 `project.write.established.writing`，且迁移不会删除其他后台权限。
- [ ] 运行目标测试并确认因权限不存在而失败。
- [ ] 在权限目录和 screenwriter 默认角色中加入窄权限；迁移使用 `ON CONFLICT DO NOTHING`，`downgrade()` 保留配置。
- [ ] 运行权限和迁移测试确认通过。

### 任务 2：跨项目写作阶段原子领取

**文件：**
- 修改：`workflow/repository.py`
- 测试：`tests/test_workflow_tasks.py`

- [ ] 编写失败测试，覆盖未加入项目的编剧可见并领取 `outline` 池任务、领取后成为 `screenwriter` 成员。
- [ ] 增加失败断言：`characters` 不可领取；同阶段已有其他人的活动任务时不可抢占。
- [ ] 运行目标测试确认权限和成员约束导致失败。
- [ ] 抽取写作阶段领取判定，统一用于 `list_tasks()`、`can_claim_task()` 和 `claim_task()`；领取任务和成员 upsert 在同一事务完成。
- [ ] 运行工作流测试确认通过，包括并发领取测试。

### 任务 3：创作阶段初始化入口

**文件：**
- 修改：`creative/api.py`
- 测试：`tests/test_planning_assignment_api.py`

- [ ] 编写失败测试：外部编剧对大纲 `workflow-tasks/outline/ensure` 得到已分配给自己的任务，对 `characters` 仍为 403。
- [ ] 运行测试确认大纲请求当前返回 403。
- [ ] 仅对 `outline`、`beats`、`script` 初始化入口接受 `project.write.established.writing`，其余非 GET 项目操作保持成员/管理者校验。
- [ ] 运行创作 API 测试确认项目设置仍不可修改。

### 任务 4：回归、部署和生产验证

**文件：**
- 验证：`identity/permission_catalog.py`
- 验证：`workflow/repository.py`
- 验证：`creative/api.py`

- [ ] 运行相关权限、工作流、页面和创作 API 测试。
- [ ] 运行 Python 编译、文件头检查和 `git diff --check`。
- [ ] 执行 `alembic upgrade head`，重启 8899 WebGUI。
- [ ] 用生产 `bianju` 验证：项目 7 大纲任务可领取，人物关系不可编辑，其他编剧活动中的场次剧本不可抢占。

