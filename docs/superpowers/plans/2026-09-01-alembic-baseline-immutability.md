# Alembic Baseline 不可变性修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 恢复 `0001_system_schema_baseline` 的历史结构，并用无 PostgreSQL 依赖的测试保护 baseline 不被后续用户字段污染。

**架构：** `BASELINE_METADATA` 只保留 0001 时点的 users 结构；`0005_user_contact_profile` 保持唯一负责 email、phone、gender、性别约束及两个联系人索引。测试直接检查 SQLAlchemy metadata，不连接数据库。

**技术栈：** Python 3.11、SQLAlchemy、pytest、Alembic。

---

### 任务 1：恢复 baseline 并添加不可变结构测试

**文件：**
- 修改：`system_database/baseline_schema.py:30-67`
- 修改：`tests/test_system_database_alembic.py:94` 附近

- [ ] **步骤 1：编写失败的结构回归测试**

在 `tests/test_system_database_alembic.py` 增加一个测试，读取 `BASELINE_METADATA.tables["users"]`，断言列集合不包含 `email`、`phone`、`gender`，约束名称不包含 `ck_users_gender`，并断言 baseline metadata 的索引名称不包含 `uq_users_active_email`、`uq_users_active_phone`。

- [ ] **步骤 2：运行测试确认因 baseline 污染而失败**

运行：`uv run pytest tests/test_system_database_alembic.py -k baseline -v`
预期：新增测试失败，失败原因是 baseline 当前包含联系人列、性别约束或联系人索引。

- [ ] **步骤 3：编写最小实现**

从 `users` 表定义删除 `email`、`phone`、`gender` 三个 `Column`，删除 `ck_users_gender`，删除 `uq_users_active_email` 与 `uq_users_active_phone` 两个 `Index`；不修改 `0005_user_contact_profile.py`、revision 编号、依赖链或 `SYSTEM_METADATA`。

- [ ] **步骤 4：运行针对性测试确认通过**

运行：`uv run pytest tests/test_system_database_alembic.py -k baseline -v`
预期：baseline 相关测试全部通过。

- [ ] **步骤 5：运行完整相关测试**

运行：`uv run pytest tests/test_system_database_alembic.py -v`
预期：无 PostgreSQL URL 时，结构测试通过、数据库测试按 fixture 跳过；退出码为 0。

- [ ] **步骤 6：运行文件头检查**

运行：`uv run pre-commit run --files system_database/baseline_schema.py tests/test_system_database_alembic.py`
预期：所有 hook 通过。

- [ ] **步骤 7：检查差异并提交**

确认 diff 只包含本任务目标文件及本计划文件，然后提交：

```bash
git add system_database/baseline_schema.py tests/test_system_database_alembic.py docs/superpowers/plans/2026-09-01-alembic-baseline-immutability.md
git commit -m "fix: restore immutable alembic baseline"
```
