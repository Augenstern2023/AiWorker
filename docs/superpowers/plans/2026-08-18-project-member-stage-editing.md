# 项目成员全阶段编辑权限实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让任意有效项目成员动态获得所属项目全部创作阶段的查看、生成、编辑、版本和完成功能。

**架构：** 在工作流仓库集中定义项目成员编辑策略，工作流状态机和 Creative API 只消费该策略。保持角色权限表、项目成员表结构和单阶段任务模型不变；前端任务解析允许项目成员协作使用开放任务。

**技术栈：** FastAPI、SQLAlchemy、PostgreSQL、SQLite 创作仓库、原生 JavaScript、pytest

---

### 任务 1：项目成员工作流任务策略

**文件：**
- 修改：`workflow/repository.py`
- 修改：`workflow/state_machine.py`
- 测试：`tests/test_project_member_stage_editing.py`

- [ ] 编写 PostgreSQL 失败测试：非阶段角色的有效成员能看到并领取池任务，另一有效成员能启动和完成已领取任务，非成员与失效成员被拒绝。
- [ ] 运行目标测试，确认当前因角色不匹配、非负责人而失败。
- [ ] 增加统一的有效项目成员和项目任务编辑判定；任务列表、领取、开始及状态流转使用该判定。
- [ ] 重跑测试确认工作流策略通过，且非项目任务仍保持原有角色和负责人限制。

### 任务 2：Creative API 全阶段授权

**文件：**
- 修改：`creative/api.py`
- 修改：`creative/workflow_adapter.py`
- 修改：`workflow/approval_service.py`
- 测试：`tests/test_project_member_stage_editing.py`

- [ ] 编写失败测试：仅有无关全局角色的项目成员可以生成 Brief、编辑资源并完成；对其他项目返回 403。
- [ ] 运行目标测试确认动作权限、机器任务权限或任务负责人检查返回 403。
- [ ] 对项目范围内的 Creative API 动作使用动态成员授权；生成任务、资源任务校验、草稿与完成流转允许有效成员协作。
- [ ] 保持项目删除、成员管理和非项目 API 原有权限门控。
- [ ] 重跑目标测试确认所有阶段策略一致。

### 任务 3：前端任务协作与错误反馈

**文件：**
- 修改：`static/js/common/creative.js`
- 修改：`static/js/common/api.js`
- 测试：`tests/test_project_member_stage_editing_browser.py`

- [ ] 编写浏览器失败测试：项目成员复用他人已领取的开放任务时直接生成，不调用只能由负责人执行的开始接口；403 显示后端具体原因。
- [ ] 运行浏览器测试确认当前失败。
- [ ] 调整项目任务解析和 403 详情读取，保持现有进度弹窗、按钮禁用和错误提示布局。
- [ ] 运行浏览器测试与 JavaScript 语法检查。

### 任务 4：回归与上线

**文件：**
- 验证：`tests/test_project_member_stage_editing.py`
- 验证：`tests/test_project_member_stage_editing_browser.py`
- 验证：相关权限、Creative API 与项目成员测试

- [ ] 运行目标 PostgreSQL、浏览器、页面授权与现有创作权限回归测试。
- [ ] 运行 Python 编译、JavaScript 语法、文件头和 `git diff --check`。
- [ ] 重启 8899 WebGUI；验证 OpenAPI、最新脚本和项目 8 的成员授权路径已加载。
