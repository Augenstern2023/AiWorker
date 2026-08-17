# 项目创建与打开权限设计

## 目标

让选题库中的“创建项目及任务”和“打开项目”遵循两套独立规则：创建使用最小权限 `project.create`，打开使用当前账号对具体项目的访问权。WebGUI 重启后，前后端接口、模板与测试必须保持一致。

## 权限模型

- 系统管理员始终可以创建和打开项目。
- `screenwriter`、`creative_planner`、`project_owner` 默认获得 `project.create`；已有生产角色通过一次性 Alembic 迁移只增补该权限，不替换或删除后台配置。
- 创建接口、成员候选接口和模板创建按钮统一检查 `project.create`。
- `project.manage` 与 `workflow.assign` 继续只用于既有项目的管理和任务分配，不再作为项目创建前置条件。
- 创建者只有在创建请求中，且目标项目尚无成员时，才能凭 `project.create` 将自己初始化为项目级 `project_owner`。初始化完成后，后续成员管理仍走项目负责人校验。

## 打开项目

- “打开项目”不受 `project.create` 或 `project.manage` 控制。
- 选题接口根据 `PermissionService.can_access_assigned_project` 和 `can_access_managed_project` 为每条关联项目返回 `can_open_project`。
- 模板只在 `lifecycle_project_id` 存在且 `can_open_project` 为真时显示按钮。
- 按钮进入项目 Brief。编剧的 `/writing/projects` 页面授权可以继承到 Brief 动态详情页，但项目 API 仍校验成员关系，因此无法借页面授权读取未加入的项目。

## 候选人与项目初始化

- 项目成员候选查询复用工作流角色候选逻辑，但显式接受 `project.create` 作为所需权限；原任务分配候选接口仍要求 `workflow.assign`。
- 创建项目后依次写入创建者负责人身份、选择的策划/编剧成员和五个 `pool` 阶段任务。
- `creation_request_id` 与 `ensure_project_task` 继续保证重复请求不产生重复项目或任务。

## 测试

- PostgreSQL API 测试使用 `AIWORKER_TEST_SYSTEM_DATABASE_URL` 和真实 `SystemDatabase`，不再用废弃的 SQLite 路径构造身份仓库。
- 测试真实 `screenwriter` Principal：拥有 `project.create`、能读取成员候选、能创建项目并成为项目负责人。
- 测试没有 `project.create` 的角色无法读取候选或创建项目。
- 用 Jinja2 渲染真实角色，验证创建按钮由 `project.create` 控制，打开按钮独立存在并由行级 `can_open_project` 控制。
- 测试编剧只能为自己已加入的项目得到 `can_open_project=true`。

## 运维验证

- 重启 8899 WebGUI 后，在线 OpenAPI 必须包含成员候选接口和新请求字段。
- 运行目标测试、相关回归、Python 文件头检查和 JavaScript 语法检查。

