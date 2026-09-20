# 创作域跨域引用审计

## 背景

创作库的部分列保存的是**其他领域**的主键：用户 ID、工作流任务 ID、提交 ID。这些列在
PostgreSQL 中**故意不声明外键**，原因有两条：

1. 创作表与系统表共用一个 `SystemDatabase`，但两个领域的生命周期并不同步：用户可以被
   停用或删除，工作流任务可以被清理，而创作行属于历史记录，必须保留。
2. 声明外键会把跨域写入绑死在同一事务里，让创作域的迁移无法独立推进。

因此这些引用由应用层而非数据库负责校验。本次迁移用
`creative.sqlite_import.inspect_cross_domain_references` 对全部引用做一次分类：

- **complete**：引用能在父表中找到对应行；
- **legal historical gap**：找不到，但已在
  `creative.sqlite_import.LEGAL_HISTORICAL_GAP_REFERENCES` 中逐值声明并在此文说明；
- **illegal orphan**：找不到且未声明。**这是致命错误**，导入会被判定失败。

声明是**值级**的（`表, 列, 值` 三元组），不是列级。只声明列会让该列上将来出现的、
真正断裂的引用也被一并放过。

## 引用的父表与父列

生产系统库中的真实父列是 `users.id` 与 `workflow_tasks.id`。早期实现曾错误地写成
`users.user_id` / `workflow_tasks.task_id`，这两个列并不存在；由于解析器对缺失的父表或
父列会安全地返回「全部未解析」，该错误不会报错，只会把**所有**引用都判成孤儿，从而以
一个极具误导性的错误挡住正式迁移。此问题已修复。

`submission_id -> workflow_submissions.id` 为将来预留：当前系统库中没有该表，也没有任何
创作行填写该列。

## 审计结果

审计对象是完整的 1924 行迁移副本；父表存在性在**生产库 `aiworker_system` 上只读校验**
（审计时该库有 22 个用户、145 个工作流任务）。

| 创作列 | 父列 | 引用值 | 已解析 | 合法历史缺口 |
|---|---|---:|---:|---:|
| `creative_projects.created_by` | `users.id` | 9 | 8 | 1 |
| `creative_projects.workflow_task_id` | `workflow_tasks.id` | 17 | 15 | 2 |
| `creative_blueprints.workflow_task_id` | `workflow_tasks.id` | 17 | 15 | 2 |
| `creative_characters.workflow_task_id` | `workflow_tasks.id` | 11 | 11 | 0 |
| `creative_episodes.workflow_task_id` | `workflow_tasks.id` | 22 | 22 | 0 |
| `creative_relationships.workflow_task_id` | `workflow_tasks.id` | 10 | 10 | 0 |
| `script_reviews.created_by` | `users.id` | 2 | 2 | 0 |
| `script_optimizations.created_by` | `users.id` | 2 | 2 | 0 |
| `creative_prompt_template_revisions.created_by` | `users.id` | 1 | 0 | 1 |
| `selection_stage_snapshots.actor_user_id` | `users.id` | 8 | 6 | 2 |
| **合计** | | **99** | **91** | **8** |

## 已声明的合法历史缺口

### 非用户哨兵值

| 表 | 列 | 值 | 说明 |
|---|---|---|---|
| `creative_projects` | `created_by` | `local` | 在强制用户身份之前写入的本地占位值 |
| `creative_prompt_template_revisions` | `created_by` | `system` | 系统内置提示词模板的修订，没有人类作者 |

两者都不是 UUID，永远不可能在 `users` 中解析。它们是历史写法，不是数据损坏。

### 已被删除的用户

| 表 | 列 | 值 |
|---|---|---|
| `selection_stage_snapshots` | `actor_user_id` | `34773a8a-0e17-4295-a30c-c54ddf21771d` |
| `selection_stage_snapshots` | `actor_user_id` | `4581b90a-4743-4bc4-b7d8-75ce6359c8e5` |

这两个用户在创作行写入之后从系统中移除。快照记录的是「当时是谁做的」，删除用户不应
抹掉这段历史。

### 已不存在的工作流任务

| 表 | 列 | 值 |
|---|---|---|
| `creative_projects` | `workflow_task_id` | `036fc64d-bf63-4a3b-97a4-e1d05ef1aca0` |
| `creative_projects` | `workflow_task_id` | `f2942f4b-0638-4d95-88bf-23dde8bb5941` |
| `creative_blueprints` | `workflow_task_id` | `036fc64d-bf63-4a3b-97a4-e1d05ef1aca0` |
| `creative_blueprints` | `workflow_task_id` | `f2942f4b-0638-4d95-88bf-23dde8bb5941` |

同两个任务 ID 同时出现在项目与其蓝图版本上，说明它们是同一次引导流程留下的痕迹。项目
另有 `creation_request_id` 作为幂等身份，因此工作流任务缺失不影响项目本身的寻址。

## 复现方式

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = "postgresql+psycopg://<user>:<pass>@<host>:5432/aiworker_system"
uv run python -c @"
from pathlib import Path
from creative.sqlite_import import inspect_cross_domain_references
from system_database import SystemDatabase, SystemDatabaseSettings
db = SystemDatabase(SystemDatabaseSettings.from_environment())
for r in inspect_cross_domain_references(db, source=Path('.runtime/creative/creative.db')):
    print(r.table, r.column, '->', r.parent_table, r.parent_column,
          'distinct', r.distinct_values, 'resolved', r.resolved,
          'gaps', len(r.legal_gaps), 'illegal', len(r.illegal_orphans))
db.dispose()
"@
```

父表存在性校验也可单独进行：把创作行所在库设为 `AIWORKER_SYSTEM_DATABASE_URL`，再以只读
连接查询生产库的 `users` / `workflow_tasks`。

## 变更时机

导入完成后，`creative_database_import_runs.issue_summary_json` 会记录该次运行的问题列表。
如果导入时报出本文件未列出的孤儿，说明出现了新的断裂引用：**先查清原因，不要直接把它加进
`LEGAL_HISTORICAL_GAP_REFERENCES`**。该常量只应容纳本文已论证的历史值。
