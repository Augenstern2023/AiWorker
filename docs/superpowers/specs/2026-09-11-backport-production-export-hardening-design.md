# Production Export 回移加固设计

## 背景

`codex/backport-production-export` 已包含生产包导出、自由创作、工作流隔离与相关 UI，
但当前不能安全合并：Alembic baseline 被当前审计字段污染；工作流项目标识从本地数字 ID
切换为 `creative:<store_id>:project:<id>` 后，历史数据没有只读兼容路径；Writer 回滚和
机器任务权限仍有旧 ID 调用；主工作区另有一组未提交的项目列表诊断修复需要手工整合。

本设计只修改隔离工作区分支 `codex/fix-backport-production-export`。不合并、不重置、
不提交主工作区 `main`，也不批量重写 PostgreSQL 或 SQLite 中的历史业务记录。

## 目标

- 恢复 Alembic baseline 的历史不可变性，使空 schema 可以从 0001 升级到 head。
- 在应用层只读识别历史数字 Workflow ID，同时保持新数据使用 scoped ID。
- 保证同一 PostgreSQL 下多个 Creative store 的新 scoped 数据不会误落到其他 store 的旧 ID。
- Writer 回滚的快照、指纹、删除 fence、删除和恢复全过程使用同一个已解析项目键。
- 项目负责人可以按解析后的项目键停止有权限管理的机器任务。
- 将主工作区项目列表修复的日志和错误语义移植到分支的新只读列表实现。

## 非目标

- 不执行现有数据库的数据迁移、`alembic stamp`、upgrade、downgrade 或批量 UPDATE。
- 不删除或改写历史 Workflow、成员、提交、审计、回滚记录。
- 不处理本次审查发现但用户未纳入范围的 LLM `trust_env=False` 行为。
- 不合并 `codex/fix-backport-production-export` 到 `main`，也不推送远端。

## 方案比较

### 方案 A：原地重写历史 PostgreSQL ID

把旧数字项目键及关联 Workflow 主键统一更新为 scoped ID。最终数据最整齐，但多个旧
Creative store 可能复用相同自增 ID，历史记录缺少 store 归属，无法保证无歧义；同时会
产生不可逆的数据变更。该方案不符合本次边界，不采用。

### 方案 B：无条件双读 scoped 与数字 ID

所有权限和任务查询都同时接受两个键。实现简单，但新 store 的本地项目 ID 可能命中其他
store 留下的旧数字记录，削弱本分支引入 store scope 的隔离目标。该方案不采用。

### 方案 C：scoped 优先、证据约束的历史只读兼容（采用）

增加集中式 `WorkflowIdentityResolver`。解析项目或选题时先检查 scoped 数据；只有 scoped
数据不存在，且 Creative 本地记录保存的 task ID 能证明该 Workflow 属于当前项目/选题，
或项目创建者成员关系能证明旧项目归属时，才返回历史数字键。没有归属证据时返回 scoped
键并按新数据处理。解析过程只查询，不更新业务表。

该方案保留历史可用性，又避免新 store 仅因本地 ID 相同而收养其他 store 的旧 Workflow。
对于已经在旧架构下发生碰撞、且没有任何本地 task 证据的数据，系统不会猜测归属；列表会
报告 mismatch/missing，由管理员人工处理。

## 架构与数据流

### Alembic baseline

`system_database/baseline_schema.py` 必须只描述 0001 时点。删除分支提前加入的
`service_instance_id` 与 `creative_store_id`；最终运行时 `system_database/schema.py` 继续包含
两列，0019 仍是唯一引入列的 revision。测试同时断言 baseline 不含字段、运行时 schema
包含字段，并在独立临时 schema 上执行完整 upgrade。

### WorkflowIdentityResolver

新组件位于 `creative/workflow_identity.py`，依赖 `CreativeRepository` 与
`WorkflowRepository`，提供：

- `project_id(local_project_id) -> str`
- `topic_scope(local_topic_id) -> str`
- `project_candidates(local_project_id) -> tuple[str, str]`

CreativeRepository 提供本地只读证据：项目创建/当前资源 task IDs、创建者 ID，以及选题和
拆解记录中的 task IDs。WorkflowRepository 提供一次事务内的只读归属检查：

1. scoped key 已存在任何 Workflow、任务、成员或删除 fence时，固定返回 scoped key；
2. 已知本地 task ID 的 `project_id/workflow_id` 指向 legacy key 时，返回 legacy key；
3. 项目创建者在 legacy key 下有有效 `project_creator` 成员关系时，返回 legacy key；
4. 其他情况返回 scoped key，不创建、不修改记录。

所有新增 Workflow 仍显式使用 CreativeRepository 生成的 scoped key。resolver 只用于读取、
授权、继续历史操作和清理已经存在的数据，不改变新建路径。

### Writer 回滚

Writer 在 prepare/finalize 阶段解析一次 Workflow 项目键，并把它写入 undo 的 JSON 快照；
后续指纹、外部占用检查、删除 fence、`delete_project_data`、完成/中止 fence 和恢复都读取同一
快照字段。旧 undo 没有该字段时，在 Creative 项目仍存在的最早阶段解析并沿本次回滚携带，
不修改旧数据库行结构。

这样即使 Creative 项目在回滚中途已被删除，Workflow 清理也不会重新根据本地数字 ID 猜测。

### 机器任务权限

`WebGUI.py` 已有 `_workflow_project_key()`，将其改为调用 resolver。读任务、创建者存活校验和
`_assert_can_stop_job()` 共用该函数，避免同一 job 在不同权限路径中使用不同项目键。

### 项目列表手工整合

保留目标分支的只读列表设计，不恢复 GET 请求中的 Workflow 写入或 reconciliation。移植主
工作区的诊断语义：

- creation task 缺失时保留项目及 task 引用，返回 `workflow_link_status=missing` 并写 warning；
- Workflow 存储异常时记录带 `project_id/task_id` 的 exception，并返回稳定的 503；
- 正常 linked/mismatched/unbound 状态继续使用 resolver 后的项目键判断；
- 主工作区新增的三条测试作为行为契约移入隔离分支并按新响应字段调整。

## 错误处理与安全边界

- resolver 的数据库不可用错误不得伪装成“没有历史数据”，应向调用方传播并由 API 转成 503。
- legacy fallback 必须有 task 或 creator membership 证据；禁止只因 `project_id` 数字相同就采用。
- 同时存在 scoped 和 legacy 数据时 scoped 优先，legacy 保持只读，不自动合并。
- 临时 PostgreSQL schema 使用随机名称，只在测试连接指定的非生产数据库中创建，测试结束后
  删除；禁止对配置的默认 schema 执行 Alembic。
- 所有 Python 新文件保留项目版权头。

## 测试设计

测试按 TDD 红—绿循环分四组：

1. baseline：结构测试先因两列污染失败；修复后在随机临时 schema 上从 0001 升到 0019。
2. resolver：覆盖 legacy task 证据、creator 证据、无证据拒绝 fallback、scoped 优先、两个
   store 相同本地 ID 不串库、topic task 证据和存储异常传播。
3. Writer/权限：覆盖历史项目回滚、新 scoped 项目回滚、回滚中断恢复、负责人停止他人 job、
   无权限用户仍为 403。
4. 项目列表：覆盖缺失 creation task 保留证据和 warning、后续 task 不被清除、Workflow
   存储异常记录 exception 并返回 503，以及 linked/mismatched 状态。

相关测试通过后运行变更涉及的测试文件、完整 `tests/`（临时 schema 配置）、
`git diff --check` 与 `pre-commit run --all-files`。

## 提交与集成

在隔离分支按 baseline、resolver、Writer/权限、项目列表四个可审查提交推进。完成后只报告
分支和提交，不合并 `main`。主工作区现有 `creative/api.py` 与未跟踪测试保持不变。
