# 壹凡短剧选品平台角色职能分配改进方案

**版本**：V1.0  
**日期**：2026-07-22  
**关联文档**：[壹凡短剧选品平台 PRD 实施文档](../../../improve/壹凡短剧选品平台PRD实施文档.md)  
**适用范围**：本地账号、角色权限、人员任务、统一版本审批、页面路由拆分与审计

## 1. 方案结论

平台从当前本地单用户模式升级为本地多用户协作模式，首期不接入企业微信、飞书、OIDC 或其他第三方身份平台。

最终保留五类默认角色：

1. 选品运营。
2. 创意策划。
3. 编剧。
4. 项目负责人。
5. 系统管理员。

剧目分析师的职能和权限全部并入选品运营。制作运营、数据运营暂不设立，其数据运维、任务运维、质量检查和成果导出职能全部归属系统管理员。系统管理员即最高权限管理员，不再单独设置超级管理员。

人员任务采用“系统创建角色任务池，用户领取或负责人指派”的方式。系统不根据工作量自动选择具体用户。系统所称“自动分配”仅指用户绑定角色后自动获得角色权限，以及高级权限自动补齐必要的依赖权限。

创作链路采用统一版本状态机。任何阶段内容提交后立即冻结，只有审批通过后才能创建下一阶段任务。审批退回不会解冻原提交版本，而是基于原版本创建新的草稿版本。

各角色使用独立页面路由。无权限页面不出现在导航中，直接访问返回 `403`；后端 API 同时执行权限校验，不能以隐藏前端按钮代替鉴权。

## 2. 当前源码基线

当前源码已经具备以下业务能力：

```text
今日榜单
-> 今日爆款与候选选题
-> 选题采纳、淘汰或归档
-> 剧目检索下载
-> 字幕或 ASR 转写
-> 爆款拆解生成、编辑和锁定
-> 创建创作项目
-> 仿写方向
-> 创作任务书（Brief）
-> 人物与关系
-> 分集大纲
-> 单集节拍表（Beats）
-> Markdown/JSON 阶段导出
```

当前实现与多用户协作目标存在以下差距：

| 当前实现 | 存在问题 | 改进方向 |
| --- | --- | --- |
| `WebGUI.py` 返回单个 `templates/index.html` | 所有角色共用同一页面，无法形成真实页面隔离 | 拆分服务端页面路由、模板和前端脚本 |
| 没有登录、会话和当前用户 | API 无法识别操作者 | 增加本地账号、会话 Cookie 和用户上下文 |
| `creative_projects.created_by` 默认为 `local` | 无法追溯创建人 | 迁移为真实 `user_id` |
| 拆解、Brief、人物、大纲、Beats 使用 `draft/locked` | 没有提交、审批、退回和审批人 | 迁移至统一版本状态机 |
| 当前锁定接口直接把版本设为锁定 | 作者可以绕过审批直接进入下一节点 | 替换为提交与审批接口 |
| `TopicLifecycleService` 根据产物推导生命周期 | 无法表达人员任务、待审批和退回 | 保留业务生命周期，新增独立人员工作流状态 |
| `JobManager` 管理下载、转写、LLM 等机器任务 | 不能表示领取人、负责人和审核人 | 新增人员任务系统，并通过 `job_id` 关联机器任务 |
| 阶段导出为直接 GET 请求 | 未验证操作者和批准版本 | 增加导出权限、批准版本校验和审计 |

当前系统尚未生成完整单集正文、分镜和 ZIP 制作包。本方案只对现有 Brief、人物关系、大纲、Beats 和阶段导出进行权限治理；完整正文和制作包在 Phase 5 接入同一工作流。

## 3. 角色模型

### 3.1 选品运营

**角色代码**：`selection_operator`

**目标**：将榜单和剧目素材整理为可审批、可追溯的选题与拆解成果。

**职责**：

- 查看抖音、红果榜单、排名变化、在榜时长、热度和分析看板。
- 生成候选选题，检查推荐分、题材、来源平台和数据完整性。
- 编辑选题说明，提交采纳或淘汰建议。
- 按剧名检索剧目，人工确认红果或抖音降级结果。
- 选择单集、多集或全剧下载，检查付费跳过、失败集和任务日志。
- 预览视频、音频、字幕、ASR 转写和 `script.md`。
- 确认素材覆盖范围，记录缺失集数、转写来源和质量问题。
- 根据实际转写生成拆解，逐字段校正内容、来源和风险。
- 提交选题决策单、素材包和拆解版本等待审批。

**默认页面**：

```text
/tasks
/selection/dashboard
/selection/ranks
/selection/topics
/selection/dramas
/selection/breakdowns
```

**默认权限**：

```text
page.selection
rank.read
analytics.read
topic.read
topic.generate
topic.edit
topic.submit
drama.search
drama.download
drama.transcript.read
data.media.preview
breakdown.read
breakdown.generate
breakdown.edit
breakdown.version.create
breakdown.submit
workflow.pool.read
workflow.claim
workflow.task.update
job.create.selection
job.read.own
job.cancel.own
```

**限制**：

- 不能批准自己提交的版本。
- 不能直接将拆解设为批准或锁定。
- 不能创建仿写方向、人物关系、大纲或 Beats。
- 不能修改系统配置、角色权限或全局数据文件。

### 3.2 创意策划

**角色代码**：`creative_planner`

**目标**：将批准的拆解转化为具备差异化、风险说明和人物基础的创作方案。

**职责**：

- 从批准的选题与拆解创建创作项目草稿。
- 生成 3-5 个仿写方向，比较目标受众、冲突、爽点、成本和风险。
- 对高风险方向填写差异化修改说明。
- 编辑创作任务书，包括题材、平台、受众、世界观、主线目标、集数和时长。
- 创建和编辑人物卡、角色表现设置及人物关系。
- 基于已批准版本创建修订版本，不覆盖历史内容。
- 提交 Brief 和人物关系版本等待审批。

**默认页面**：

```text
/tasks
/planning/projects
/planning/projects/{project_id}/directions
/planning/projects/{project_id}/brief
/planning/projects/{project_id}/characters
```

**默认权限**：

```text
page.planning
project.read.assigned
project.create
project.edit.own_draft
breakdown.read.approved
blueprint.read
blueprint.generate
blueprint.edit
blueprint.version.create
brief.read
brief.edit
brief.submit
character.read
character.generate
character.edit
character.submit
relationship.read
relationship.edit
workflow.pool.read
workflow.claim
workflow.task.update
job.create.creative
job.read.project
job.cancel.own
```

**限制**：

- 不能从未批准的选题或拆解创建项目。
- 不能批准自己提交的 Brief 或人物关系。
- 不能编辑分集大纲和 Beats。
- 不能解除已提交或已批准版本的冻结状态。

### 3.3 编剧

**角色代码**：`screenwriter`

**目标**：依据批准的 Brief、人物和关系完成全剧结构与单集节奏设计。

**职责**：

- 查看项目批准的拆解、Brief、人物和关系。
- 生成和编辑全剧阶段弧线。
- 按最多 10 集一批生成并编辑分集大纲。
- 按指定集数生成和编辑单集节拍表。
- 校验行动目标、阻力、情绪变化、转折和结尾钩子。
- 创建大纲和 Beats 的修订版本。
- 提交大纲批次或单集 Beats 等待审批。

**默认页面**：

```text
/tasks
/writing/projects
/writing/projects/{project_id}/outlines
/writing/projects/{project_id}/episodes/{episode_number}/beats
```

**默认权限**：

```text
page.writing
project.read.assigned
brief.read.approved
character.read.approved
relationship.read.approved
outline.read
outline.generate
outline.edit
outline.version.create
outline.submit
beats.read
beats.generate
beats.edit
beats.version.create
beats.submit
workflow.pool.read
workflow.claim
workflow.task.update
job.create.writing
job.read.project
job.cancel.own
```

**限制**：

- 不能修改批准的 Brief、人物或关系。
- 不能在人物关系未批准前创建大纲。
- 不能在大纲未批准前创建 Beats。
- 当前阶段不能生成完整正文或完整台词。
- 不能批准自己提交的版本。

### 3.4 项目负责人

**角色代码**：`project_owner`

**目标**：负责项目组织、任务分配、审批决策和阶段流转。

**职责**：

- 创建或接管选题工作流和创作项目。
- 添加、移除项目成员，并为成员指定项目角色。
- 将任务放入角色任务池，或直接指派给具体用户。
- 改派、收回、取消和重新开启人员任务。
- 查看提交版本与来源版本差异。
- 批准或退回选题决策、素材包、拆解、Brief、人物关系、大纲和 Beats。
- 审批退回时填写明确的修改意见。
- 确认项目阶段，归档或恢复项目。
- 查看项目范围内的人员任务、机器 Job、产物和审计记录。

**默认页面**：

```text
/tasks
/review/projects
/review/tasks
/review/approvals
/review/projects/{project_id}/members
/review/projects/{project_id}/versions
```

**默认权限**：

```text
page.review
project.read.managed
project.manage
project.member.manage
project.archive
workflow.read.managed
workflow.assign
workflow.reassign
workflow.reclaim
workflow.cancel
version.read.managed
version.compare
version.approve
version.return
job.read.project
audit.read.project
```

**限制**：

- 默认不编辑选品、拆解、Brief、大纲或 Beats 正文。
- 需要参与内容创作时，应额外绑定相应业务角色。
- 默认不能管理系统用户、角色、模型、凭证和全局数据。
- 除系统管理员外，提交人不能审批自己提交的版本。

### 3.5 系统管理员

**角色代码**：`system_admin`

**目标**：负责全部系统权限、平台运维、数据治理、成果交付和异常恢复。

**职责**：

- 创建、停用、恢复本地用户并重置密码。
- 创建自定义角色，调整角色权限、页面权限和数据范围。
- 维护爬虫、LLM、ASR、红果接口、RAG、导出和本地运行配置。
- 发起榜单同步、数据分析、索引、机器任务重试或取消。
- 查看并处理快照缺失、来源归并、重复剧名、题材异常和任务失败。
- 查看所有项目、人员任务、机器 Job、版本和文件产物。
- 导出批准的 Brief、人物关系、大纲和 Beats。
- 执行业务审批、强制解锁、越权修改、任务取消和异常状态修复。
- 查看全局审计日志。

**默认页面**：

```text
/tasks
/selection/*
/planning/*
/writing/*
/review/*
/admin/users
/admin/roles
/admin/config
/admin/data
/admin/analytics
/admin/jobs
/admin/deliveries
/admin/audit
```

**权限规则**：

```text
role_code = system_admin
permission = *
data_scope = all
```

系统管理员可以存在多个，但系统必须始终保留至少一个启用状态的系统管理员。最后一个启用的系统管理员不能被停用、删除、移除管理员角色或修改为无管理权限。

系统管理员的越权修改、强制解锁、任务取消、用户停用等操作必须记录审计日志，但原因字段为可选，不强制填写。

## 4. 自定义角色与权限自动分配

### 4.1 自定义角色

系统管理员可以复制默认角色创建自定义角色，并配置：

- 角色名称和说明。
- 页面访问权限。
- API 操作权限。
- 可领取的任务类型。
- 数据范围。
- 是否允许指派或审批。
- 角色启用状态。

首期不提供用户级零散权限。用户只能通过角色获得权限，避免同一用户存在无法解释的例外授权。

### 4.2 多角色权限

一个用户可以拥有多个启用角色，有效权限取并集：

```text
effective_permissions(user) = union(enabled_role.permissions)
```

数据范围按最大合法范围合并：

```text
task < project < managed_projects < all
```

系统管理员拥有 `*`，权限校验直接通过，但业务资源状态校验和审计记录仍然执行。

### 4.3 权限依赖

系统自动补齐高级权限所依赖的读取权限和页面权限：

| 选择权限 | 自动补齐 |
| --- | --- |
| `breakdown.edit` | `breakdown.read`、`topic.read`、`page.selection` |
| `brief.edit` | `brief.read`、`project.read.assigned`、`page.planning` |
| `character.edit` | `character.read`、`brief.read.approved`、`page.planning` |
| `outline.edit` | `outline.read`、`brief.read.approved`、`character.read.approved`、`page.writing` |
| `beats.edit` | `beats.read`、`outline.read.approved`、`page.writing` |
| `version.approve` | `version.read.managed`、`version.compare`、`page.review` |
| `workflow.assign` | `workflow.read.managed`、`project.read.managed`、`page.review` |
| `export.create` | `approved_content.read`、`page.admin.delivery` |

当高级权限仍存在时，不允许单独移除必要依赖。修改角色权限后，成员的有效权限立即重新计算；服务端会话不缓存永久权限快照。

## 5. 页面与路由隔离

### 5.1 页面路由

```text
/login
/logout
/tasks

/selection/dashboard
/selection/ranks
/selection/topics
/selection/dramas
/selection/breakdowns

/planning/projects
/planning/projects/{project_id}/directions
/planning/projects/{project_id}/brief
/planning/projects/{project_id}/characters

/writing/projects
/writing/projects/{project_id}/outlines
/writing/projects/{project_id}/episodes/{episode_number}/beats

/review/projects
/review/tasks
/review/approvals
/review/projects/{project_id}/members
/review/projects/{project_id}/versions

/admin/users
/admin/roles
/admin/config
/admin/data
/admin/analytics
/admin/jobs
/admin/deliveries
/admin/audit
```

### 5.2 页面访问规则

- 登录成功后，系统根据用户有效权限生成导航。
- 用户只看到有权限的页面入口。
- `/` 重定向到用户最近访问且仍有权限的页面；没有历史记录时按角色优先级跳转。
- 直接访问无权限页面返回 `403` 页面，不返回页面业务数据。
- 未登录访问业务页面重定向到 `/login`。
- 多角色用户看到各角色页面的并集。
- 系统管理员看到全部页面。
- 页面可见不代表资源可操作，项目成员范围和版本状态仍需再次校验。

### 5.3 前端拆分

```text
templates/
  layouts/base.html
  auth/login.html
  common/tasks.html
  selection/dashboard.html
  selection/ranks.html
  selection/topics.html
  selection/dramas.html
  selection/breakdowns.html
  planning/projects.html
  planning/directions.html
  planning/brief.html
  planning/characters.html
  writing/projects.html
  writing/outlines.html
  writing/beats.html
  review/projects.html
  review/tasks.html
  review/approvals.html
  review/versions.html
  admin/users.html
  admin/roles.html
  admin/config.html
  admin/data.html
  admin/analytics.html
  admin/jobs.html
  admin/deliveries.html
  admin/audit.html

static/js/
  common/
  selection/
  planning/
  writing/
  review/
  admin/
```

后端资源 API 继续按业务域组织，不为每个角色复制一套 API。每个页面路由和 API 通过统一权限依赖校验当前用户。

## 6. 全流程任务分配

### 6.1 流程节点

| 顺序 | 节点 | 执行角色池 | 审批角色 | 审批通过后的动作 |
| ---: | --- | --- | --- | --- |
| 1 | 选题决策单 | 选品运营 | 项目负责人 | 选题变为已采纳，创建素材准备任务 |
| 2 | 下载与转写素材包 | 选品运营 | 项目负责人 | 冻结素材覆盖范围，创建拆解任务 |
| 3 | 剧目拆解 | 选品运营 | 项目负责人 | 批准拆解版本，允许创建创作项目 |
| 4 | 仿写方向与 Brief | 创意策划 | 项目负责人 | 批准 Brief，创建人物关系任务 |
| 5 | 人物与关系 | 创意策划 | 项目负责人 | 批准人物关系批次，创建大纲任务 |
| 6 | 分集大纲 | 编剧 | 项目负责人 | 批准整批大纲，创建 Beats 任务 |
| 7 | 单集 Beats | 编剧 | 项目负责人 | 批准指定集版本，更新项目完成度 |
| 8 | 阶段成果导出 | 系统管理员 | 无二次审批 | 导出批准版本并记录交付快照 |

项目负责人尚未确定时，审批任务进入项目负责人角色池。系统管理员可以领取、指派或直接审批任何任务。

### 6.2 人员任务状态

```text
pool          待领取
assigned      已指派
in_progress   进行中
waiting_review 待审批
returned      已退回
completed     已完成
cancelled     已取消
```

状态规则：

- 上游审批通过后自动创建下一节点人员任务。
- 任务默认进入目标角色池，不自动选择具体用户。
- 角色成员可以领取任务。
- 项目负责人或系统管理员可以直接指派、改派、收回或取消任务。
- 提交版本时，执行任务进入 `waiting_review`，同时生成审批任务。
- 审批退回时，原任务进入 `returned`，系统创建新草稿并允许原执行人继续处理。
- 审批通过时，执行任务和审批任务进入 `completed`。
- 人员任务可以关联多个机器 Job，例如下载、ASR、LLM 生成和导出。

## 7. 统一版本状态机

### 7.1 状态定义

```text
draft      草稿，可编辑
submitted  已提交，冻结并等待审批
approved   已批准，永久只读，可作为下一节点输入
returned   已退回，永久保留并创建下一草稿版本
superseded 已被后续批准版本取代，仍可追溯
```

### 7.2 状态转换

```text
draft
  -> submitted
       -> approved
       -> returned -> fork new draft

approved -> fork new draft
new approved version -> previous approved becomes superseded
```

### 7.3 强制规则

1. 只有 `draft` 可以编辑或被 AI 生成结果覆盖。
2. 提交时记录内容哈希、提交人和提交时间，并立即冻结。
3. `submitted` 不能由提交人撤回后原地修改。
4. 普通审批人不能批准自己提交的版本。
5. 审批通过后，下一节点只能读取该 `approved` 版本。
6. 审批退回时，退回意见必填；系统基于退回版本创建新草稿。
7. 修改批准内容必须创建新版本，不能覆盖原版本。
8. 下游内容永久记录其上游批准版本 ID。
9. 上游产生新批准版本时，旧下游内容不删除，只标记为基于旧版本。
10. 系统管理员可以强制修复、审批或解锁，但必须写审计日志，原因可为空。

### 7.4 聚合版本

人物与关系、分集大纲等成组内容以批次作为审批单位：

- 人物卡和人物关系共享一个人物关系版本。
- 全剧阶段弧线和全部分集大纲共享一个大纲批次版本。
- Beats 按单集形成版本；后续可增加多集批量提交。
- 批次未整体批准前，不允许部分内容流转到下一节点。

## 8. 人员任务与机器任务

人员任务和现有 `JobManager` 任务必须分开：

| 类型 | 负责内容 | 示例 |
| --- | --- | --- |
| 人员任务 `workflow_task` | 领取、指派、编辑、提交、审批、退回 | 完成拆解、审核 Brief、编写第 3 集 Beats |
| 机器任务 `job` | 下载、转写、LLM、数据同步、导出 | 红果下载、ASR 转写、生成大纲、导出 Markdown |

关联规则：

- 人员任务可关联零个或多个 `job_id`。
- 机器任务完成不代表人员任务完成，执行人仍需检查结果并提交版本。
- 机器任务失败不会自动关闭人员任务。
- 只有操作者拥有对应人员任务和 Job 权限时，才能启动、取消或重试机器任务。
- 系统管理员可以处理全部机器任务。

## 9. 数据模型

### 9.1 系统数据库

新增 `.runtime/system/system.db`：

| 表 | 用途 |
| --- | --- |
| `users` | 本地用户、密码哈希、状态、最后登录时间 |
| `roles` | 默认角色和自定义角色 |
| `permissions` | 页面、API、任务和数据权限字典 |
| `user_roles` | 用户与角色绑定 |
| `role_permissions` | 角色权限和数据范围 |
| `permission_dependencies` | 高级权限的自动依赖关系 |
| `sessions` | 服务端登录会话 |
| `project_members` | 项目成员、项目角色和成员状态 |
| `workflow_instances` | 选题或项目对应的工作流实例 |
| `workflow_tasks` | 人员任务、角色池、领取人、指派人和状态 |
| `workflow_task_jobs` | 人员任务与 `JobManager` 任务关联 |
| `workflow_task_events` | 领取、改派、提交、退回等任务历史 |
| `approval_records` | 审批人、结论、意见和版本引用 |
| `audit_logs` | 全局安全与业务审计 |

### 9.2 创意版本表

在 `.runtime/creative/creative.db` 新增 `creative_stage_versions`：

```text
id
topic_id
project_id
stage
version
parent_version_id
status
resource_group_id
content_fingerprint
created_by
submitted_by
submitted_at
reviewed_by
reviewed_at
review_comment
source_refs_json
created_at
updated_at
```

现有拆解、方向、人物、关系和 episode 记录增加 `stage_version_id`。版本状态以 `creative_stage_versions.status` 为准，原表 `status` 在迁移期仅作为兼容字段。

### 9.3 安全约束

- 用户名唯一且不区分大小写。
- 密码只保存 Argon2id 哈希。
- 会话 Token 只保存哈希值。
- 会话 Cookie 使用 `HttpOnly`、`SameSite=Lax`；生产 HTTPS 环境启用 `Secure`。
- 所有写请求执行 CSRF 校验。
- 停用用户后立即注销其全部会话。
- 最后一个启用的系统管理员不可被停用或降级。
- 删除用户采用停用和软删除，历史版本、审批和审计记录保留用户引用。

## 10. API 设计

### 10.1 登录与当前用户

```text
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
POST /api/auth/change-password
```

### 10.2 用户与角色

```text
GET   /api/admin/users
POST  /api/admin/users
PATCH /api/admin/users/{user_id}
POST  /api/admin/users/{user_id}/reset-password
PUT   /api/admin/users/{user_id}/roles

GET   /api/admin/roles
POST  /api/admin/roles
PATCH /api/admin/roles/{role_id}
POST  /api/admin/roles/{role_id}/clone
PUT   /api/admin/roles/{role_id}/permissions
GET   /api/admin/permissions
```

### 10.3 人员任务

```text
GET  /api/workflow/tasks
GET  /api/workflow/tasks/{task_id}
POST /api/workflow/tasks/{task_id}/claim
POST /api/workflow/tasks/{task_id}/assign
POST /api/workflow/tasks/{task_id}/reassign
POST /api/workflow/tasks/{task_id}/start
POST /api/workflow/tasks/{task_id}/cancel
GET  /api/workflow/projects/{project_id}
```

### 10.4 版本审批

```text
POST /api/versions/{version_id}/submit
POST /api/versions/{version_id}/approve
POST /api/versions/{version_id}/return
POST /api/versions/{version_id}/fork
GET  /api/versions/{version_id}
GET  /api/versions/{version_id}/compare/{other_version_id}
```

现有 `/lock` 接口进入兼容废弃期。普通用户不能再通过 `/lock` 直接锁定；最终由 `approve` 完成批准和下一节点流转。

### 10.5 审计与交付

```text
GET  /api/admin/audit
GET  /api/admin/deliveries
POST /api/admin/deliveries
GET  /api/admin/deliveries/{delivery_id}/download
```

所有现有创意、下载、文件、配置和 Job API 增加统一 `require_permission()` 依赖及资源范围校验。

## 11. 后端模块规划

```text
identity/
  schema.py
  repository.py
  password_service.py
  session_service.py
  permission_service.py
  dependencies.py
  api.py

workflow/
  schema.py
  repository.py
  state_machine.py
  assignment_service.py
  transition_service.py
  api.py

audit/
  schema.py
  repository.py
  service.py
  api.py

web/
  page_dependencies.py
  page_router.py
  navigation_service.py
```

`WebGUI.py` 最终只负责应用初始化、公共中间件、路由注册和生命周期管理，不继续承载用户、权限、工作流和页面业务逻辑。

`creative/version_service.py` 统一实现提交、冻结、批准、退回、派生新版本和上游门禁。拆解、Brief、人物、大纲和 Beats API 不再各自实现不同的锁定规则。

## 12. 审计设计

以下操作必须写入 `audit_logs`：

- 登录成功、登录失败和退出。
- 创建、停用、恢复用户和重置密码。
- 绑定角色、解除角色和修改角色权限。
- 领取、指派、改派、收回和取消任务。
- 创建、编辑、提交、批准、退回、派生和强制解锁版本。
- 修改爬虫、模型、ASR、红果接口和导出配置。
- 启动、暂停、继续、取消、重试和删除机器任务。
- 删除数据文件。
- 创建和下载成果交付物。
- 系统管理员执行越权修改或异常状态修复。

审计字段包括：

```text
actor_user_id
actor_role_snapshot_json
action
target_type
target_id
before_summary_json
after_summary_json
request_id
ip_address
user_agent
reason
result
created_at
```

`reason` 全部允许为空。审批退回的 `review_comment` 属于业务修改意见，仍然必填。

## 13. 数据迁移

1. 备份 `.runtime/creative/creative.db` 和当前任务状态。
2. 创建系统数据库和默认权限字典。
3. 初始化第一个系统管理员本地账号。
4. 创建五个默认角色和权限依赖。
5. 为现有 `creative_projects.created_by='local'` 记录关联初始化管理员，保留原始值到迁移备注。
6. 将现有 `locked` 拆解、Brief、人物、大纲和 Beats 映射为 `approved` 版本。
7. 将现有 `draft` 映射为 `draft`，不自动创建审批记录。
8. 为现有项目建立工作流实例，但不自动生成已完成历史人员任务。
9. 依据当前项目状态创建一个下一待办任务，进入对应角色池。
10. 保留旧锁定字段一个版本周期用于回滚，完成验证后停止读取。

迁移必须幂等，并在任一步失败时回滚数据库事务；不得删除现有创作内容或 Job 产物。

## 14. 分阶段实施

### 阶段一：本地账号与权限底座

- 建立系统数据库、本地登录、会话、密码和当前用户上下文。
- 初始化系统管理员和五个默认角色。
- 实现自定义角色、权限依赖和最后管理员保护。
- 为现有 API 增加默认拒绝策略，逐项声明所需权限。

### 阶段二：页面路由拆分

- 将 `templates/index.html` 拆为公共布局和角色页面。
- 建立选品、策划、编剧、审批和系统管理页面路由。
- 根据当前用户动态生成导航。
- 对无权限页面和 API 返回 `403`。

### 阶段三：统一版本状态机

- 新增阶段版本表和版本服务。
- 将直接锁定改为提交、批准和退回。
- 提交时冻结并记录内容指纹。
- 实现退回派生新草稿和批准版本上游门禁。

### 阶段四：人员任务与审批中心

- 建立角色任务池、领取、指派、改派和取消。
- 将版本提交与审批任务关联。
- 审批通过后自动创建下一节点任务。
- 增加我的任务、任务分配和审批中心页面。

### 阶段五：现有业务接入

- 接入选题决策、剧目下载、转写、拆解、Brief、人物、大纲和 Beats。
- 将机器 Job 关联到人员任务。
- 限制导出只能使用批准版本。
- 将数据运维、机器任务和成果交付页面归入系统管理员。

### 阶段六：迁移、审计与验收

- 执行现有 `draft/locked` 数据迁移。
- 增加全局审计、管理员越权操作记录和交付记录。
- 完成角色页面隔离、API 越权、版本冻结和流程流转测试。
- 验证 SQLite 重启后用户、权限、任务、审批和审计完整保留。

## 15. 验收标准

### 15.1 角色与页面

- 选品运营只能看到任务中心和选品相关页面。
- 创意策划只能看到任务中心和已分配的策划项目页面。
- 编剧只能看到任务中心和已分配的写作项目页面。
- 项目负责人可以看到管理项目、任务分配和审批页面。
- 系统管理员可以看到全部页面。
- 任意角色直接访问无权限页面或 API 均返回 `403`。
- 多角色用户看到合法页面并集。

### 15.2 权限

- 绑定角色后用户立即获得角色权限。
- 修改角色权限后用户无需重新创建账号即可生效。
- 高级权限会自动补齐读取和页面依赖。
- 不能删除高级权限仍依赖的基础权限。
- 系统不能停用或降级最后一个启用的系统管理员。

### 15.3 版本与审批

- 草稿可编辑，提交后所有编辑 API 返回 `409`。
- 待审批版本不能作为下一节点输入。
- 审批通过后自动创建下一节点任务。
- 审批退回后原版本保持只读，并产生新草稿版本。
- 普通用户不能批准自己提交的版本。
- 系统管理员可以越权审批或解锁，且操作进入审计日志，原因允许为空。
- 已批准版本和所有下游引用在服务重启后仍可追溯。

### 15.4 任务与 Job

- 新任务进入角色池而不是自动分配具体用户。
- 角色成员可以领取，项目负责人可以指派或改派。
- 用户不能领取不属于其角色的任务。
- 下载、转写和 LLM Job 完成后，人员任务不会自动视为已提交。
- 机器任务失败时，人员任务保留并可继续重试。

### 15.5 审计与导出

- 用户、角色、任务、审批、配置、文件和 Job 关键操作均有审计记录。
- 审计日志包含操作者、目标、时间、请求信息和结果。
- 系统管理员关键操作不强制填写原因。
- 阶段导出只能选择批准版本。
- 导出记录包含项目、阶段、版本、内容指纹、操作者和时间。

## 16. 非目标

- 首期不接入企业微信、飞书、LDAP、OIDC 或其他单点登录。
- 首期不实现组织、部门、多租户和跨工作区隔离。
- 首期不根据工作量自动选择任务执行人。
- 首期不提供用户级零散权限和显式拒绝规则。
- 本方案不提前实现完整剧本正文、分镜和 ZIP 制作包。
- 本方案不改变现有 dy/hg 数据采集、转写和 AI 生成算法，只增加用户、权限、任务、审批和审计边界。

## 17. 关键决策

1. 系统管理员就是最高权限管理员，不再设置超级管理员。
2. 系统管理员承担数据运营和制作运营职责。
3. 剧目分析师全部并入选品运营。
4. 权限自动分配指角色绑定和权限依赖，不指按工作量自动分配用户。
5. 人员任务与机器 Job 分开管理，通过关联表连接。
6. 所有创作阶段使用同一版本状态机。
7. 提交版本冻结，批准后才能流转，退回时创建新草稿。
8. 页面路由、API 权限和资源范围必须同时校验。
9. 系统管理员关键操作必须审计，但原因字段可为空。
10. 当前锁定版本迁移为批准版本，不丢失历史内容。
