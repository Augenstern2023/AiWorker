# Alembic Baseline 不可变性修复设计

## 背景

新建 PostgreSQL 数据库执行 `alembic upgrade head` 时，`0001_system_schema_baseline`
通过 `system_database.baseline_schema.BASELINE_METADATA` 创建 `users` 表。当前 baseline
错误地提前包含了本应由 `0005_user_contact_profile` 引入的 `email`、`phone`、`gender`
字段、性别约束和联系人唯一索引。迁移执行到 `0005` 再次添加 `email` 时触发
`DuplicateColumn`，整个 PostgreSQL migration 事务回滚。

## 目标

- 恢复 `0001` baseline 在其历史时点的结构，使全新数据库可完整升级到 Alembic head。
- 保留 `0005_user_contact_profile` 作为联系人字段、约束和索引的唯一引入点。
- 增加无需 PostgreSQL 即可运行的结构回归测试，防止后续 schema 功能再次污染 baseline。
- 保留已有数据库的升级语义，不修改 revision 编号、依赖链或 Alembic 版本记录。

## 非目标

- 不把 migration 改造成按数据库现状分支的条件式脚本。
- 不执行 `alembic stamp`，不跳过任何 revision。
- 不修改当前运行时 `SYSTEM_METADATA` 中的最终用户字段。
- 不迁移或清理业务数据；当前目标 `aiworker` 数据库为空。

## 方案

采用恢复不可变 baseline 的方案：

1. 从 `BASELINE_METADATA.users` 删除 `email`、`phone`、`gender`。
2. 从 baseline 删除 `ck_users_gender`。
3. 从 baseline 删除 `uq_users_active_email` 和 `uq_users_active_phone`。
4. 保持 `0005_user_contact_profile` 不变，由它按顺序添加上述字段、约束和索引。

不采用在 `0005` 中检查字段是否存在的方案，因为条件式 migration 会掩盖历史定义错误，
并使相同 revision 在不同数据库上产生不同结构。不采用 `stamp` 或手工删列，因为这会绕过
迁移语义，且无法形成长期回归保护。

## 测试设计

先在 `tests/test_system_database_alembic.py` 增加结构测试，断言：

- baseline 的 `users` 表不包含 `email`、`phone`、`gender`；
- baseline 的 `users` 表不包含 `ck_users_gender`；
- baseline metadata 不包含 `uq_users_active_email` 和 `uq_users_active_phone`。

测试必须先在当前代码上因 baseline 污染而失败。完成最小实现后再次运行并通过。随后使用
专用空数据库执行完整 `alembic upgrade head`，验证最终 `users` 表包含联系人字段且数据库
revision 到达 head。最后运行相关 Alembic 测试和文件头检查。

## 安全与回滚

代码修改仅涉及历史 baseline 定义和回归测试，不触碰现有业务表或数据。目标数据库当前没有
public 表和 `alembic_version`，可以直接从空库重试。若代码验证失败，回滚本次两个目标文件的
变更即可；不得通过修改数据库版本记录规避失败。
