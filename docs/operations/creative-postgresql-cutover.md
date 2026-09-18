# 创作域 PostgreSQL 正式切换 Runbook

本文是创作数据从 `.runtime/creative/creative.db`（SQLite）迁移到 PostgreSQL 的正式切换
操作手册。所有命令均在仓库根目录执行，且**必须逐条按顺序执行**。

适用版本：Alembic head `0025_creative_schema_baseline`。

---

## 0. 前置条件（全部必须为真）

| 检查项 | 命令 | 期望 |
|---|---|---|
| 工作树干净 | `git status --porcelain` | 无已跟踪文件改动 |
| 已同步远端 | `git rev-parse HEAD; git rev-parse manjuxuanpin/main` | 两个 SHA 相同 |
| Alembic 生产前置 | `uv run python tools/check_alembic_integrity.py production` | 退出码 0 |
| 迁移库可达 | `uv run python -c "from system_database import *; d=SystemDatabase(SystemDatabaseSettings.from_environment()); print(d.health())"` | `reachable=True` |
| 目标创作表为空 | 见第 4 步 `--check` 输出 | `empty: true` |

> 切换期间应用**不得**在线写入创作库。SQLite 与 PostgreSQL 不双写：一旦 `--apply`
> 成功，SQLite 即冻结为历史源，任何后续写入都会造成数据分叉。

### 已知环境注意事项

1. **SQLite 源文件的字节哈希会漂移。** 实测同一份内容（36 张表 / 1924 行，逐表计数与
   规范化哈希完全一致）在不同时刻的 SHA-256 分别为
   `02e430c2…`、`e9b2dd5b…`、`47af0618…`。SQLite 会就地重写页，因此**不要用文件哈希
   判断源是否变化**，必须用 `--check` 输出的逐表哈希。这也意味着切换前必须停掉应用，
   否则导入完成后的写入会丢失。
2. `creative_breakdowns.source_snapshot_id` 与 `script_optimizations.prompt_snapshot_json`
   在冻结的 legacy DDL 中不存在，回退导出会补列并给出 warning，属预期。

---

## 1. 维护公告

通知使用方：创作域将在 `<开始时间>` 至 `<结束时间>` 停止写入，预计 `<N>` 分钟。切换
窗口内 WebGUI 的创作相关页面不可用。

---

## 2. 停止进程

```powershell
# 停止 WebGUI 与所有 worker（按实际进程名调整）
Get-Process | Where-Object { $_.ProcessName -match 'python|pythonw' } |
  Select-Object Id, ProcessName, StartTime
# 逐个确认后停止；不要使用 -Force 盲目批量杀进程
Stop-Process -Id <pid>
```

停止后确认没有任何进程持有 SQLite 文件（Windows 上被占用会导致备份行为异常）。

## 3. 检查未决任务

在停止应用前记录未决工作量，避免把「运行中的任务」误判成数据丢失：

```sql
-- 仍在运行/排队的创作操作
SELECT status, count(*) FROM creative_generation_operations GROUP BY status;
-- 未完成的优化与审核
SELECT status, count(*) FROM script_optimizations GROUP BY status;
SELECT status, count(*) FROM script_reviews GROUP BY status;
```

把输出存档到变更记录中。

---

## 4. 备份

### 4.1 SQLite 备份（使用 Backup API，不要复制正在写入的文件）

```powershell
uv run python -c @"
import sqlite3, pathlib, datetime, hashlib
src = pathlib.Path('.runtime/creative/creative.db')
stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
dst = src.with_name(f'creative.db.pre-postgres-migration.{stamp}.bak')
source = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
target = sqlite3.connect(str(dst))
with target:
    source.backup(target)
target.close(); source.close()
print('backup:', dst)
print('sha256:', hashlib.sha256(dst.read_bytes()).hexdigest())
"@
```

### 4.2 记录源指纹

```powershell
uv run python scripts/migrate_creative_sqlite_to_postgres.py `
  --source .runtime/creative/creative.db --check > .runtime/creative/pre-migration-check.json
```

`pre-migration-check.json` 里的 `source_sha256`、`row_counts`、`digests` 是后续比对的基准，
必须存档。

### 4.3 PostgreSQL 逻辑备份

```powershell
$env:PGPASSWORD = '<只在本会话设置，不要写入文件>'
& 'E:\postgresql\bin\pg_dump.exe' `
  --format=custom --no-owner --no-privileges `
  --file=".runtime/creative/aiworker_system-pre-cutover.dump" `
  "postgresql://aiworker_app@127.0.0.1:5432/aiworker_system"
Remove-Item Env:\PGPASSWORD
```

---

## 5. 校验源（只读，不触碰 PostgreSQL）

```powershell
uv run python scripts/migrate_creative_sqlite_to_postgres.py `
  --source .runtime/creative/creative.db --check
```

**必须看到**：`ok: true`、`integrity_ok: true`、`problems: []`、`total_rows: 1924`。
与 4.2 的指纹逐表比对一致。

## 6. 升级数据库结构

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = "postgresql+psycopg://aiworker_app:<pass>@127.0.0.1:5432/aiworker_system"
uv run alembic upgrade head          # 期望：0025_creative_schema_baseline
uv run alembic current
```

## 7. 导入（单事务）

```powershell
uv run python scripts/migrate_creative_sqlite_to_postgres.py `
  --source .runtime/creative/creative.db --apply
```

**必须看到**：退出码 0、`status: "completed"`、`verified: true`、`problems: []`，
且 `row_counts` 与 4.2 完全一致。

若退出码非 0：**立即停止**，按下文「强制停止条件」处理。业务事务已整体回滚，控制表会留下
一条 `status=failed` 记录；目标创作表仍为空，可修复后重跑。

## 8. 独立复核

```powershell
uv run python scripts/migrate_creative_sqlite_to_postgres.py `
  --source .runtime/creative/creative.db --verify
```

`--verify` 会重新读取源、重新规范化，并要求**逐表哈希字节相同**，再检查版本链、资源
Head 与 store 身份。退出码 0 且 `status: "verified"` 才算通过。

## 9. 存储开关与启动顺序

```powershell
$env:AIWORKER_CREATIVE_STORAGE = "postgresql"
```

选择 `postgresql` 时运行时**不会**回退到 SQLite：目标不可达或未到 Alembic head 会直接
报错。启动顺序：

1. PostgreSQL 已升级到 head；
2. 设置 `AIWORKER_SYSTEM_DATABASE_URL` 与 `AIWORKER_CREATIVE_STORAGE=postgresql`；
3. 启动 WebGUI；
4. 再启动各 worker（worker 自行从环境构造仓储，不再接收数据库路径）。

## 10. 冒烟测试

1. 项目列表能打开，数量与 4.2 的 `creative_projects` 一致；
2. 打开一个已有项目，能读取蓝图 / 人物 / 大纲 / 节拍 / 剧本五级内容；
3. 创建一个新项目，`id` 大于迁移前的最大 `id`（序列已重置，不会复用旧 ID）；
4. 查看任务日志，确认没有 `db_path` / 数据库 URL 泄漏；
5. `creative_store_metadata` 的 `store_id` 与迁移前一致（工作流身份不变）。

## 11. 业务抽查

抽样核对（不得以抽样替代第 8 步的全量哈希）：

```sql
SELECT count(*) FROM creative_projects;      -- 期望 18
SELECT count(*) FROM creative_topics;        -- 期望 652
SELECT count(*) FROM creative_episodes;      -- 期望 774
SELECT value FROM creative_store_metadata WHERE key = 'store_id';
SELECT run_id, mode, status, source_sha256 FROM creative_database_import_runs
  ORDER BY started_at DESC LIMIT 3;
```

跨域引用核对见 `creative-cross-domain-reference-audit.md`。

## 12. 解除维护

确认第 10、11 步全部通过后再恢复对外服务，并在变更记录中写入：切换开始/结束时间、
导入 run_id、源 SHA-256、逐表哈希、回退导出路径与 SHA-256。

---

## 13. 七天观察期

### 13.1 运行检查 SQL（不含凭证）

```sql
-- 连接与等待事件
SELECT pid, state, wait_event_type, wait_event, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE datname = current_database() AND state <> 'idle'
ORDER BY duration DESC;

-- 未授予的锁（长期非空说明存在阻塞或死锁风险）
SELECT pid, locktype, mode, granted, relation::regclass
FROM pg_locks WHERE NOT granted;

-- 长事务
SELECT pid, now() - xact_start AS age, state FROM pg_stat_activity
WHERE xact_start IS NOT NULL ORDER BY age DESC LIMIT 10;
```

应用日志按关键字统计：`deadlock`、`unique`/`duplicate key`、`lease`、`rollback`。
若启用 `pg_stat_statements`，额外关注项目列表、版本列表与 Head 查询的调用次数与平均耗时：

```sql
SELECT calls, mean_exec_time, left(query, 90) FROM pg_stat_statements
WHERE query ILIKE '%creative_%' ORDER BY calls DESC LIMIT 20;
```

### 13.2 观察期内可回退

回退导出（只允许写入不存在的路径，禁止覆盖线上库与封存备份）：

```powershell
uv run python scripts/rollback_creative_postgres_to_sqlite.py `
  --destination .runtime/rollback/creative.db `
  --rollback-dir .runtime/rollback
```

导出器先写临时文件、校验通过后再原子重命名；校验包含 `integrity_check`、
`foreign_key_check`、逐表哈希、版本链与 Head，全部通过才发布，并输出 SHA-256。

回退步骤：停应用 → 用上述文件替换 SQLite 源（保留原文件）→
`AIWORKER_CREATIVE_STORAGE=sqlite` 并显式配置路径 → 启动 → 冒烟。
**七天观察期内不得删除 SQLite 源与封存备份。**

### 13.3 观察期结束

观察期满且无未决问题后，才可移除运行时 SQLite 依赖（任务 16 第 2 部分）。

---

## 14. 数据库权限（最小权限）

| 账号 | 权限 |
|---|---|
| Alembic 账号 | 拥有创作表与系统表的 DDL（`CREATE` / `ALTER` / `DROP`） |
| 应用账号 `aiworker_app` | 仅创作表与系统表的 `SELECT` / `INSERT` / `UPDATE` / `DELETE`，以及 identity sequence 的 `USAGE`，**不含** DDL |

```sql
-- 应用账号不应是超级用户，也不应能建表
SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = 'aiworker_app';
```

---

## 15. 强制停止条件

出现以下**任一**情况，立即停止切换并回退，不得带病上线：

1. SQLite `PRAGMA integrity_check` 不是 `ok`；
2. 源中存在非法 JSON 或非法时间值（`--check` 报 `problems`）；
3. 目标创作表非空；
4. 存在未在审计文档中声明的非法孤儿引用；
5. 逐表行数或哈希与源不一致；
6. identity sequence 无效（下一个 ID ≤ 已导入最大值）；
7. Alembic 不是 head；
8. 冒烟测试任一项失败。

**导入与复核脚本不提供跳过这些校验的参数**；如需绕过，只能改代码，那本身就是停止信号。

---

## 16. 演练记录

切换前须完成两次脱敏副本演练，第二次从**全新的隔离 PostgreSQL schema** 开始，分别存档：
源 SHA-256、各阶段耗时、逐表计数与哈希、孤儿报告、导入报告、回退报告、模拟停机时长。

已完成的实测证据（2026-09-17/18）：

| 项目 | 结果 |
|---|---|
| 源规模 | 36 张表 / 1924 行 / `integrity_check = ok` |
| `--check` | 退出码 0，`ok: true`，`problems: []` |
| `--apply`（隔离库） | 退出码 0，`verified: true`，37/37 表计数与哈希一致 |
| `--verify`（隔离库） | 退出码 0，`status: verified`，`problems: []` |
| identity sequence | 13 条序列的下一个 ID 全部大于已导入最大值 |
| 跨域引用 | 99 个引用值 / 91 已解析 / 8 个已声明的合法历史缺口 / 0 未声明孤儿 |
| 往返无损 | 线上 SQLite A → PostgreSQL → SQLite B：37/37 表行数、逐表规范化哈希、ID 区间、store 身份全部相同 |

## 正式切换记录（2026-09-18）

按本手册第 1–2 步在 `aiworker_system` 生产库执行。

| 项目 | 结果 |
|---|---|
| 前置：Alembic | `0025_creative_schema_baseline`（head） |
| 前置：写入方 | 无 `WebGUI.py` / worker 进程在跑；SQLite 已静默约 15 小时 |
| 前置：目标创作表 | 仅 1 行 `creative_store_metadata`（应用自行写入的随机 `store_id`），无业务数据 |
| SQLite 备份 | `.runtime/creative/creative.db.pre-cutover.20260918-104553.bak`，`integrity_check = ok`，SHA-256 `e9b2dd5b326e52d0d7f287247f217624d3a7b394c2c50bc74615ccfa7517e18a` |
| PostgreSQL 备份 | `.runtime/creative/pg-cutover-20260918-104617.dump`（custom 格式，113 张表），18 239 597 字节，SHA-256 `4b99d5e1cadae071aa5a6b9896a2e7b502798cbedd47e31b21f7a3dc266d6d3d` |
| `--check` | 退出码 0，`ok: true`，`problems: []`，`total_rows: 1924`，源 SHA-256 `47af06185892527e7af0f53707d6515515d13c2abde4deda93ed859a19878960` |
| `--apply` | 退出码 0，`status: completed`，`ok: true`，`problems: []`，37 张表 |
| `--verify` | 退出码 0，`status: verified`，`ok: true`，37/37 表逐表哈希一致，无 `problems` |
| store 身份 | 生产 `store_id = 67707ae7-e8ec-4198-8ffd-706ced485d40`，与源一致（导入覆盖了应用生成的 `32394270-…`） |
| 读路径冒烟 | `list_projects()` 18 个项目；`workflow_project_id()` = `creative:67707ae7-…:project:8`；主题可读；`schema_current = true` |
| 唯一告警 | `production_status: absent from the legacy source; nothing to import`（该表在旧库中不存在，非错误） |

### 切换后独立复核

上述结果来自导入器自身；下表是**独立于 `--verify`** 的复核，直接从生产库与封存源重新推导目标关心的四件事（ID、版本链、workflow 身份、跨域引用）：

| 检查项 | 结果 |
|---|---|
| store 身份 | 生产 `67707ae7-…` = 源，一致 |
| 逐表行数 | 36 张表比对，**0 处不一致** |
| 项目 | 18 个；其中 17 个带 `workflow_task_id`，15 个可解析（差额即下方 2 个已声明缺口） |
| 身份样例 | `creative:67707ae7-…:project:1` / `:project:2` |
| ID 保持 | `creative_topics` 4186/4186、`creative_projects` 19/19、`creative_episodes` 774/774、`creative_characters` 100/100、`creative_relationships` 127/127，全部一致 |
| 跨域引用 | 10 条边 / 99 个值 / 91 已解析 / 8 个已声明合法缺口 / **0 个未声明孤儿** |
| 结论 | **PASS** |

**未完成、需在观察期内处理的部分：**

- 第 3 步的完整业务冒烟（原创 / 仿写 / 完整剧本三种项目各跑一遍读取、生成、修订、锁定、审核、优化、交付、导出）尚未执行。
- 第 4 步的 7 天观察期从本次导入起算，最早 **2026-09-25** 才能判定。
- 第 6 步删除 `creative/legacy_sqlite_repository.py` 与 SQLite 运行分支必须等观察期结束；在此之前回退一律使用 PostgreSQL → SQLite 导出器生成新文件，不得直接启用封存库。

