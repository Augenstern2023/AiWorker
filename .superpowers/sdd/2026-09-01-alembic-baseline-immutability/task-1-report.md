# Task 1 报告：恢复 Alembic baseline 不可变性

## 状态
实现完成；目标测试与相关测试通过。pre-commit 因当前环境缓存目录权限问题未能执行。

## 改动
- `system_database/baseline_schema.py`
  - 从 baseline `users` 删除 `email`、`phone`、`gender`。
  - 删除约束 `ck_users_gender`。
  - 删除索引 `uq_users_active_email`、`uq_users_active_phone`。
- `tests/test_system_database_alembic.py`
  - 新增 `test_baseline_users_structure_excludes_contact_profile`，检查三列、性别约束和两个联系人索引不在 `BASELINE_METADATA` 中。
- 未修改 `0005_user_contact_profile.py`、revision、依赖链或 `SYSTEM_METADATA`。测试文件中已有的其他未提交改动未覆盖。

## TDD RED 证据
命令：
```text
$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_system_database_alembic.py -k baseline -v
```
结果：退出码 1；`1 failed, 2 passed, 7 deselected`。新增测试因当前污染失败，明确报告 `ck_users_gender` 仍在 users 约束集合中。

首次按计划命令直接运行时，uv 因全局缓存 `C:\Users\Administrator\AppData\Local\uv\cache` 拒绝访问而退出；改用工作区 `.uv-cache` 后获得上述 RED 结果。

## GREEN 及验证
1. 针对性测试：
```text
$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_system_database_alembic.py -k baseline -v
```
结果：退出码 0；`3 passed, 7 deselected`。

2. 完整相关测试：
```text
$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_system_database_alembic.py -v
```
结果：退出码 0；`10 passed`（14 warnings，包含 Alembic 配置弃用警告及 pytest cache 权限警告）。

3. pre-commit：
```text
$env:UV_CACHE_DIR='.uv-cache'; $env:PRE_COMMIT_HOME='.pre-commit-cache'; uv run pre-commit run --files system_database/baseline_schema.py tests/test_system_database_alembic.py
```
结果：退出码 1，未进入 hook 检查；pre-commit 初始化环境时在 `.pre-commit-cache` 创建仓库目录收到 `WinError 5 拒绝访问`，日志也因权限无法写入。按原命令运行同样因全局 pre-commit SQLite/cache 只读失败。未使用越权重试（本会话禁用审批）。

## 文件与提交
- 目标修改：`system_database/baseline_schema.py`、`tests/test_system_database_alembic.py`
- 报告：`.superpowers/sdd/2026-09-01-alembic-baseline-immutability/task-1-report.md`
- 计划文件未修改。
- 提交信息：`fix: restore immutable alembic baseline`

## 自审
- 结构回归测试直接读取 SQLAlchemy metadata，不连接 PostgreSQL。
- baseline 中其余 users 字段、约束和索引保持不变；联系人字段由后续 migration 负责。
- `git diff --check` 应无空白错误；提交前仅暂存目标代码和测试，避免覆盖工作区其他人的改动。

## 疑虑
- pre-commit 受 Windows 沙箱/缓存权限限制未完成，需在具备可写 pre-commit 缓存的环境补跑。
- 完整 Alembic 测试在无 `AIWORKER_TEST_SYSTEM_DATABASE_URL` 时按 fixture 跳过数据库连接测试；本次输出显示其余测试均通过。

## 审查修复
- 将 `test_baseline_users_structure_excludes_contact_profile` 中对 `set(users.c)` 的比较改为 `{column.name for column in users.c}`，确保断言比较列名而非 SQLAlchemy `Column` 对象。
- 未扩大测试范围；其余 baseline 结构、约束和索引断言保持不变。

### 覆盖测试
命令：
```text
$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_system_database_alembic.py -k baseline -v
```

输出：
```text
============================= test session starts =============================
platform win32 -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0 -- D:\Crawler\AiWorker\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\Crawler\AiWorker
configfile: pyproject.toml
plugins: anyio-4.14.2, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 10 items / 7 deselected / 3 selected

tests/test_system_database_alembic.py::test_baseline_users_structure_excludes_contact_profile PASSED [ 33%]
tests/test_system_database_alembic.py::test_baseline_table_names_are_immutable PASSED [ 66%]
tests/test_system_database_alembic.py::test_baseline_keeps_text_primary_keys PASSED [100%]

============================== warnings summary ===============================
2 warnings (Alembic 配置弃用警告、pytest cache 权限警告)
================= 3 passed, 7 deselected, 2 warnings in 0.56s =================
```

结果：退出码 0；3 个 baseline 测试通过，7 个测试按 `-k baseline` 排除。
