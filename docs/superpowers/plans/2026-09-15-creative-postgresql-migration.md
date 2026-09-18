# 创作数据库 PostgreSQL 迁移实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `.runtime/creative/creative.db` 的全部创作数据和运行时访问迁入 `AIWORKER_SYSTEM_DATABASE_URL` 指向的现有 PostgreSQL，同时保留 ID、版本链、工作流身份、业务行为和 7 天无损回退能力。

**架构：** 在 PostgreSQL `public` schema 中建立 24 张创作业务表和 1 张迁移控制表，由 Alembic 统一管理。新增按领域拆分的 SQLAlchemy Core 仓储，过渡期保留 SQLite 实现用于契约对比和回退，生产切换后由 `CreativeRepository` 门面只暴露 PostgreSQL 实现；导入器在维护窗口执行单事务全量导入、逐表哈希验证和 sequence 重置。

**技术栈：** Python 3.11、SQLAlchemy Core 2.x、PostgreSQL、Alembic、psycopg 3、Typer、pytest、uv。

**设计依据：** [`improve/壹凡短剧选品平台改进方案.md`](../../../improve/壹凡短剧选品平台改进方案.md#12-创作数据库迁移至-postgresql-的全面方案)

---

## 全局约束

- 所有依赖、测试和 CLI 命令使用 `uv`，不得创建仓库内 uv 缓存目录。
- 生产创作表与系统表使用同一个 `SystemDatabase` 和同一 PostgreSQL 数据库。
- 保留现有整数 ID、文本 ID 和 `creative_store_metadata.store_id`。
- 所有 schema 变更进入 Alembic；运行时不得创建或修改表。
- 业务返回结构、异常文案、排序、指纹和工作流身份保持兼容。
- 生产不双写；切换在维护窗口完成。
- 外部模型调用和文件生成不得发生在数据库事务内。
- 每个任务先写失败测试，再写最少实现，并独立提交。
- 不改动与本迁移无关的用户工作区文件。

## 文件结构

### 新建文件

| 文件 | 职责 |
| --- | --- |
| `creative/database_schema.py` | 24 张创作业务表和迁移控制表的 SQLAlchemy Core 定义 |
| `creative/postgres_repository.py` | 组合各领域仓储的 PostgreSQL 公共实现 |
| `creative/repositories/base.py` | 连接/事务作用域、行映射、时间与 JSON 出站兼容 |
| `creative/repositories/topics.py` | 选题、拆解、选题阶段快照 |
| `creative/repositories/projects.py` | 项目、种子、生命周期、工作流身份 |
| `creative/repositories/versions.py` | Brief、人物、关系、大纲、Beats、剧本与 Head |
| `creative/repositories/operations.py` | 幂等结果、生成租约、删除和 Writer 回滚状态 |
| `creative/repositories/reviews.py` | 剧本/项目审核、优化和优化草稿 |
| `creative/repositories/deliveries.py` | 阶段导出、正式交付、生产包和生产状态记录 |
| `creative/sqlite_import.py` | SQLite 只读检查、类型转换、导入和验证 |
| `creative/sqlite_rollback.py` | 7 天窗口内从 PostgreSQL 生成 legacy SQLite |
| `creative/repository_factory.py` | 过渡期显式选择 SQLite 或 PostgreSQL 后端 |
| `scripts/migrate_creative_sqlite_to_postgres.py` | `--check`、`--apply`、`--verify` CLI |
| `scripts/rollback_creative_postgres_to_sqlite.py` | 无损回退导出 CLI |
| `system_database/alembic/versions/0023_creative_schema_baseline.py` | 创作域 PostgreSQL 基线 |
| `tests/test_creative_postgres_schema.py` | 元数据与 Alembic 结构测试 |
| `tests/test_creative_repository_contract.py` | SQLite/PostgreSQL 行为契约测试 |
| `tests/test_postgres_creative_topics.py` | 选题和拆解仓储测试 |
| `tests/test_postgres_creative_projects.py` | 项目、种子和生命周期测试 |
| `tests/test_postgres_creative_versions.py` | 五阶段版本、Head 和并发测试 |
| `tests/test_postgres_creative_operations.py` | 幂等、租约、删除和恢复测试 |
| `tests/test_postgres_creative_reviews.py` | 审核和优化测试 |
| `tests/test_postgres_creative_deliveries.py` | 交付、导出和生产状态测试 |
| `tests/test_creative_sqlite_import.py` | 检查、导入、验证和失败回滚测试 |
| `tests/test_creative_sqlite_rollback.py` | PostgreSQL 到 SQLite 回退测试 |
| `tests/test_creative_runtime_postgres_only.py` | 最终运行时 SQLite 依赖扫描 |
| `docs/operations/creative-postgresql-cutover.md` | 正式切换、验收和回退 Runbook |
| `docs/operations/creative-cross-domain-reference-audit.md` | 跨用户、任务、提交引用的约束决策记录 |

### 主要修改文件

| 文件 | 修改职责 |
| --- | --- |
| `system_database/alembic/env.py` | 把创作元数据加入 Alembic target metadata |
| `tests/conftest.py` | 复用隔离 PostgreSQL schema fixture |
| `creative/repository.py` | 过渡期导出 legacy 门面，正式切换时改为 PostgreSQL 门面 |
| `creative/legacy_sqlite_repository.py` | 由现有仓储移入的冻结 SQLite 实现 |
| `creative/free_mode_service.py` | 使用 SQLAlchemy Connection 和仓储事务作用域 |
| `creative/workflow_adapter.py` | 与 WorkflowRepository 共享事务连接 |
| `creative/delivery.py` | 删除动态 DDL，委托交付仓储 |
| `creative/production_exports.py` | 删除 SQLite 连接和动态 DDL |
| `creative/production_export.py` | 快照读取改为仓储/Connection 输入 |
| `creative/production_status.py` | 状态持久化改为 PostgreSQL |
| `creative/api.py` | 注入 PostgreSQL 仓储，移除默认文件路径和任务 `db_path` |
| `creative/application_service.py` | 任务参数不再携带创作数据库路径 |
| `creative/selection_stage_service.py` | worker 从环境创建 PostgreSQL 仓储 |
| `creative/breakdown_worker.py` | 移除 `creative.db` 路径初始化 |
| `creative/phase4_worker.py` | 移除 `creative.db` 路径初始化和心跳仓储重复创建 |
| `screenplay_review/api.py` | 审核任务参数移除 `db_path` |
| `screenplay_review/worker.py` | 从系统数据库环境构造仓储 |
| `screenplay_optimization/api.py` | 优化任务参数移除 `db_path` |
| `screenplay_optimization/worker.py` | 从系统数据库环境构造仓储 |
| `project_review/worker.py` | 从系统数据库环境构造仓储 |
| `workflow/transition_service.py` | 删除从系统 SQLite 路径推导创作库的兼容逻辑 |
| `WebGUI.py` | 共享同一 `SystemDatabase` 实例 |
| `.gitea/workflows/alembic-integrity.yml` | 增加创作 schema 与导入验证测试 |
| `README.md` | 更新唯一 PostgreSQL 数据源、启动和迁移说明 |
| `docs/index.md` | 增加迁移与切换文档入口 |

---

### 任务 1：建立创作域 PostgreSQL Schema 和 Alembic 基线

**文件：**
- 创建：`creative/database_schema.py`
- 创建：`system_database/alembic/versions/0023_creative_schema_baseline.py`
- 创建：`tests/test_creative_postgres_schema.py`
- 修改：`system_database/alembic/env.py:12-17`
- 修改：`tests/test_system_database_alembic.py:31-105`

- [ ] **步骤 1：编写失败的元数据测试**

在 `tests/test_creative_postgres_schema.py` 固定 24 张业务表和迁移控制表：

```python
EXPECTED_CREATIVE_TABLES = {
    "creative_store_metadata", "creative_topics", "creative_breakdowns",
    "creative_projects", "creative_seeds", "creative_blueprints",
    "creative_characters", "creative_relationships", "creative_episodes",
    "creative_resource_heads", "creative_generation_contexts",
    "creative_mode_migrations", "creative_operation_results",
    "creative_generation_operations", "creative_project_deletions",
    "creative_stage_exports", "selection_stage_snapshots",
    "role_workflow_migrations", "script_reviews", "script_optimizations",
    "script_optimization_drafts", "creative_deliveries",
    "creative_production_exports", "production_status",
    "creative_database_import_runs",
}

def test_creative_metadata_declares_complete_schema():
    assert set(CREATIVE_METADATA.tables) == EXPECTED_CREATIVE_TABLES
```

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_creative_postgres_schema.py -q`

预期：FAIL，报错 `ModuleNotFoundError: creative.database_schema`。

- [ ] **步骤 3：定义独立创作元数据和关键类型**

在 `creative/database_schema.py` 使用 `CREATIVE_METADATA = MetaData()`。整数主键使用：

```python
Column(
    "id",
    BigInteger,
    Identity(always=False),
    primary_key=True,
)
```

JSON 列使用 `JSONB`，时间列使用 `DateTime(timezone=True)`，布尔列使用 `Boolean`。为 `creative_projects.creation_mode`、`workflow_mode`、`bootstrap_status` 等增加命名 `CheckConstraint`；保留现有 partial unique index 条件。

`creative_database_import_runs` 使用文本运行 ID 主键，并至少定义 `source_sha256`、`source_schema_fingerprint`、`application_commit`、`alembic_revision`、`status`、`source_counts_json`、`target_counts_json`、`table_hashes_json`、`issue_summary_json`、`started_at` 和 `finished_at`。统计与摘要列使用 JSONB，时间列使用 `TIMESTAMPTZ`，`status` 只允许 `running`、`completed`、`failed`。

- [ ] **步骤 4：编写 Alembic 基线**

新增 revision `0023_creative_schema_baseline`，`down_revision = "0022_remove_team_management"`。按依赖顺序创建表，降级按反向依赖顺序删除。只创建创作域内部外键；跨 `users`、`workflow_tasks`、`workflow_submissions` 的外键不在本 revision 中创建。

- [ ] **步骤 5：把创作元数据加入 Alembic 环境**

修改 `system_database/alembic/env.py`：

```python
from creative.database_schema import CREATIVE_METADATA
from system_database.schema import SYSTEM_METADATA

target_metadata = (SYSTEM_METADATA, CREATIVE_METADATA)
```

- [ ] **步骤 6：补充空库升级和结构断言**

在 `tests/test_creative_postgres_schema.py` 使用 `isolated_system_database` 断言：

```python
def test_alembic_head_creates_creative_tables(isolated_system_database):
    inspector = inspect(isolated_system_database.engine)
    assert EXPECTED_CREATIVE_TABLES <= set(inspector.get_table_names())
    assert isinstance(CREATIVE_METADATA.tables["creative_topics"].c.evidence_json.type, JSONB)
    assert CREATIVE_METADATA.tables["creative_projects"].c.id.identity is not None
```

- [ ] **步骤 7：运行 schema 验证**

运行：

```powershell
uv run pytest tests/test_creative_postgres_schema.py tests/test_system_database_alembic.py -q
uv run python tools/check_alembic_integrity.py production
uv run pre-commit run --files creative/database_schema.py system_database/alembic/env.py system_database/alembic/versions/0023_creative_schema_baseline.py tests/test_creative_postgres_schema.py tests/test_system_database_alembic.py
```

预期：测试通过；Alembic 只有一个 head，空库可升级到 `0023_creative_schema_baseline`。

- [ ] **步骤 8：Commit**

```powershell
git add creative/database_schema.py system_database/alembic/env.py system_database/alembic/versions/0023_creative_schema_baseline.py tests/test_creative_postgres_schema.py tests/test_system_database_alembic.py
git commit -m "feat(database): add creative PostgreSQL schema baseline"
```

---

### 任务 2：建立 PostgreSQL 仓储基础和契约测试框架

**文件：**
- 创建：`creative/repositories/__init__.py`
- 创建：`creative/repositories/base.py`
- 创建：`creative/postgres_repository.py`
- 创建：`creative/repository_factory.py`
- 移动：`creative/repository.py` → `creative/legacy_sqlite_repository.py`
- 创建：`creative/repository.py`
- 创建：`tests/test_creative_repository_contract.py`
- 修改：`tests/conftest.py:67-99`

- [ ] **步骤 1：编写失败的连接边界测试**

```python
def test_postgres_creative_repository_requires_postgresql(isolated_system_database):
    repository = PostgresCreativeRepository(isolated_system_database)
    assert repository.database is isolated_system_database
    assert repository.store_id
    assert repository.workflow_project_id(12).endswith(":project:12")
```

再增加 `connection_scope(existing_connection)` 不提交外部事务、`transaction_scope()` 自主管理提交的测试。

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_creative_repository_contract.py -q`

预期：FAIL，缺少 `PostgresCreativeRepository`。

- [ ] **步骤 3：实现基础连接和出站转换**

在 `creative/repositories/base.py` 实现：

```python
class CreativeRepositoryBase:
    def __init__(self, database: SystemDatabase) -> None:
        if database.url.get_backend_name() != "postgresql":
            raise ValueError("creative runtime database must be PostgreSQL")
        self.database = database
        self.store_id = self._load_store_id()

    @contextmanager
    def connection_scope(self, connection: Connection | None = None):
        if connection is not None:
            yield connection
            return
        with self.database.connection() as owned:
            yield owned

    @contextmanager
    def transaction_scope(self, connection: Connection | None = None):
        if connection is not None:
            yield connection
            return
        with self.database.transaction() as owned:
            yield owned
```

同时提供 `row_dict()`、UTC `datetime` 到 Unix 秒/ISO 字符串的兼容转换和 JSONB 深拷贝，禁止把 `Row`、`RowMapping` 或 `datetime` 直接泄漏给旧调用方。基础类还提供 `health()`：验证 PostgreSQL 后端、Alembic head 和 `creative_store_metadata` 可读，返回值不得包含未脱敏 URL。

- [ ] **步骤 4：冻结 SQLite 实现并建立组合仓储骨架**

执行 `git mv creative/repository.py creative/legacy_sqlite_repository.py`，在 legacy 模块末尾增加：

```python
LegacySQLiteCreativeRepository = CreativeRepository
```

过渡期 `creative/repository.py` 继续导出 legacy 类，保证现有调用方不变：

```python
from creative.legacy_sqlite_repository import LegacySQLiteCreativeRepository

CreativeRepository = LegacySQLiteCreativeRepository
```

`creative/postgres_repository.py` 定义 `PostgresCreativeRepository`，暂时只继承基础类。`repository_factory.py` 提供显式的过渡期开关：

```python
def build_creative_repository(database: SystemDatabase, *, backend: str):
    if backend == "postgresql":
        return PostgresCreativeRepository(database)
    if backend == "sqlite":
        from creative.legacy_sqlite_repository import LegacySQLiteCreativeRepository
        return LegacySQLiteCreativeRepository(Path(".runtime/creative/creative.db"))
    raise ValueError("AIWORKER_CREATIVE_STORAGE must be sqlite or postgresql")
```

生产切换前默认值保持 `sqlite`；最终清理任务删除 SQLite 分支。

- [ ] **步骤 5：建立契约测试辅助器**

在 `tests/test_creative_repository_contract.py` 定义只比较公开值的辅助函数：

```python
def canonical_payload(value):
    if isinstance(value, dict):
        return {key: canonical_payload(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical_payload(item) for item in value]
    return value

@pytest.fixture
def postgres_creative(isolated_system_database):
    return PostgresCreativeRepository(isolated_system_database)

@pytest.fixture
def postgres_factory(isolated_system_database):
    return lambda: PostgresCreativeRepository(isolated_system_database)
```

先固定 store ID、工作流 ID 编解码、时间和 JSON 返回类型；后续任务向同一文件补充领域场景。

- [ ] **步骤 6：运行基础测试**

运行：`uv run pytest tests/test_creative_repository_contract.py tests/test_creative_workflow_identity.py -q`

预期：PASS；旧 SQLite 测试保持不变。

- [ ] **步骤 7：Commit**

```powershell
git add creative/repository.py creative/legacy_sqlite_repository.py creative/repositories creative/postgres_repository.py creative/repository_factory.py tests/conftest.py tests/test_creative_repository_contract.py
git commit -m "refactor(creative): establish PostgreSQL repository foundation"
```

---

### 任务 3：迁移选题、拆解和阶段快照仓储

**文件：**
- 创建：`creative/repositories/topics.py`
- 创建：`tests/test_postgres_creative_topics.py`
- 修改：`creative/postgres_repository.py`
- 修改：`tests/test_creative_repository_contract.py`

- [ ] **步骤 1：编写选题行为和冲突测试**

覆盖 `upsert_topics`、`adopt_rank_topic`、`ensure_download_topic`、列表筛选、人工决定保护、软删除、拆解版本及阶段快照。并发用例固定：相同来源选题并发写入后只能有一行。

```python
def topic_payload(*, score=80):
    return {
        "platform": "hg", "job_id": "job-1", "source_path": "rank.json",
        "captured_at": "2026-09-15T00:00:00Z", "title": "测试选题",
        "description": "", "category": "都市", "rank_type": "热播榜",
        "score": score, "confidence": "high", "score_details": {},
        "recommendation": "adopt", "evidence": [], "source_platforms": ["hg"],
    }

def test_topic_upsert_preserves_manual_decision(postgres_creative):
    topic = postgres_creative.upsert_topics([topic_payload()])[0]
    postgres_creative.decide_topic(topic["id"], "adopted", "人工采纳", "user-1")
    updated = postgres_creative.upsert_topics([topic_payload(score=99)])[0]
    assert updated["status"] == "adopted"
    assert updated["decision_note"] == "人工采纳"
```

- [ ] **步骤 2：运行测试确认缺失方法**

运行：`uv run pytest tests/test_postgres_creative_topics.py -q`

预期：FAIL，`PostgresCreativeRepository` 缺少选题方法。

- [ ] **步骤 3：使用 Core 实现选题 upsert 和查询**

使用：

```python
statement = pg_insert(creative_topics).values(values)
statement = statement.on_conflict_do_update(
    constraint="uq_creative_topics_source",
    set_={"score": statement.excluded.score, "updated_at": now},
    where=creative_topics.c.status == "pending_review",
).returning(*creative_topics.c)
```

所有列表查询显式声明 `ORDER BY` 和 `NULLS LAST`，不得依赖 PostgreSQL 默认顺序。

- [ ] **步骤 4：实现人工决定、拆解与快照**

人工决定先 `SELECT ... FOR UPDATE`，再判断现有决定和请求是否冲突。拆解创建通过 `MAX(version)` 的锁定父行或项目级 advisory lock 分配版本；阶段快照以 `(stage, topic_id, version)` 唯一约束保证并发安全。

- [ ] **步骤 5：补充 SQLite/PostgreSQL 契约对比**

用相同输入分别运行旧 SQLite 和新 PostgreSQL 场景，比较公开字典、合并后的 rank sources、分类过滤和异常消息。

- [ ] **步骤 6：运行领域回归**

运行：

```powershell
uv run pytest tests/test_postgres_creative_topics.py tests/test_creative_phase3.py tests/test_creative_breakdown_generation.py tests/test_creative_repository_contract.py -q
```

预期：PASS。

- [ ] **步骤 7：Commit**

```powershell
git add creative/repositories/topics.py creative/postgres_repository.py tests/test_postgres_creative_topics.py tests/test_creative_repository_contract.py
git commit -m "refactor(creative): move topics and breakdowns to PostgreSQL Core"
```

---

### 任务 4：迁移项目、种子、生命周期和工作流身份

**文件：**
- 创建：`creative/repositories/projects.py`
- 创建：`tests/test_postgres_creative_projects.py`
- 修改：`creative/postgres_repository.py`
- 修改：`tests/test_creative_repository_contract.py`

- [ ] **步骤 1：编写项目和种子失败测试**

覆盖仿写/原创创建、`creation_request_id` 幂等、bootstrap 状态、种子冻结/修订、项目更新 revision、软删除、store ID 及工作流 ID 解码。

```python
def test_original_project_creation_is_idempotent(postgres_creative):
    first = postgres_creative.create_original_project(
        name="测试项目", seed_text="一句话", target_episode_count=20,
        created_by="user-1",
        creation_request_id="request-1",
    )
    replay = postgres_creative.create_original_project(
        name="测试项目", seed_text="一句话", target_episode_count=20,
        created_by="user-1",
        creation_request_id="request-1",
    )
    assert replay["id"] == first["id"]
```

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_postgres_creative_projects.py -q`

预期：FAIL，项目方法尚未实现。

- [ ] **步骤 3：实现项目创建和 ID 保持语义**

使用 `INSERT ... RETURNING` 获取新 ID；幂等冲突捕获 `IntegrityError` 后按 `(created_by, creation_request_id)` 回读并比较不可变请求字段。不得捕获所有数据库异常并误判为幂等重放。

- [ ] **步骤 4：实现项目更新和种子版本**

项目更新使用：

```python
result = connection.execute(
    update(creative_projects)
    .where(
        creative_projects.c.id == project_id,
        creative_projects.c.revision == expected_revision,
    )
    .values(**changes, revision=creative_projects.c.revision + 1)
    .returning(*creative_projects.c)
).mappings().one_or_none()
```

返回空时区分项目不存在和 revision 冲突，保持原异常文案。种子修订锁定项目行后分配版本并保留 `parent_version_id`。

本任务及后续任务中的公开写方法统一增加仅限关键字的 `connection: Connection | None = None` 参数；传入连接时加入调用方事务，未传入时由仓储自行开启事务。

- [ ] **步骤 5：实现项目读取、生命周期和删除标记**

迁移 `list_projects`、`list_projects_for_bootstrap`、`get_project`、`get_project_inputs`、workflow task 关联、创建者审计和删除状态。所有项目列表显式过滤 `deleted` 并保持原排序。

- [ ] **步骤 6：运行项目回归**

运行：

```powershell
uv run pytest tests/test_postgres_creative_projects.py tests/test_original_creative_repository.py tests/test_creative_lifecycle.py tests/test_creative_workflow_identity.py tests/test_project_creator_migration.py -q
```

预期：PostgreSQL 新测试通过；旧测试无新增失败。

- [ ] **步骤 7：Commit**

```powershell
git add creative/repositories/projects.py creative/postgres_repository.py tests/test_postgres_creative_projects.py tests/test_creative_repository_contract.py
git commit -m "refactor(creative): move projects and seeds to PostgreSQL Core"
```

---

### 任务 5：迁移 Brief、人物、关系、大纲、Beats、剧本和资源 Head

**文件：**
- 创建：`creative/repositories/versions.py`
- 创建：`tests/test_postgres_creative_versions.py`
- 修改：`creative/postgres_repository.py`
- 修改：`creative/free_mode_service.py`
- 修改：`tests/test_creative_repository_contract.py`

- [ ] **步骤 1：编写五阶段版本失败测试**

为每个阶段固定创建、编辑、修订、锁定、选择当前版本、父版本和批次语义。增加 Head revision 冲突测试：

```python
def test_stale_head_revision_cannot_replace_current_head(postgres_creative):
    project = postgres_creative.create_original_project(
        name="自由项目", seed_text="种子", target_episode_count=20,
        created_by="user-1", creation_request_id="head-project", workflow_mode="free",
    )
    first, second = postgres_creative.create_blueprints(
        project["id"],
        [{"content": {"title": "方向一"}}, {"content": {"title": "方向二"}}],
        direction_type="original",
    )
    postgres_creative.lock_brief(first["id"])
    postgres_creative.lock_brief(second["id"])
    head = postgres_creative.set_resource_head(
        project_id=project["id"], resource_scope="free", stage="brief",
        episode_number=0, resource_ref=str(first["id"]), resource_version=1,
        expected_revision=0, updated_by="user-1",
    )
    with pytest.raises(ValueError, match="resource head changed"):
        postgres_creative.set_resource_head(
            project_id=project["id"], resource_scope="free", stage="brief",
            episode_number=0, resource_ref=str(second["id"]), resource_version=1,
            expected_revision=head["revision"] - 1, updated_by="user-1",
        )
```

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_postgres_creative_versions.py -q`

预期：FAIL，版本仓储方法尚未实现。

- [ ] **步骤 3：实现 Brief、人物和关系仓储**

批次创建在同一事务中写入全部人物/关系。锁定批次时锁住项目和目标批次行，验证没有混合状态，再一次性更新。`source_refs_json` 和 `generation_meta_json` 以原生字典写入 JSONB。

- [ ] **步骤 4：实现大纲、Beats 和剧本仓储**

迁移大纲 checkpoint/续跑、字段重写、全剧 overview、单集版本和 authority fingerprint。版本分配不能依赖 `SELECT MAX` 后无锁插入；先获取项目级 advisory lock，再读取最大版本并插入。

- [ ] **步骤 5：实现 Head 和生成上下文**

`creative_resource_heads` 使用复合主键加 revision 条件更新；新增并固定 `set_resource_head(..., expected_revision, updated_by, connection=None)` 公共方法。`creative_generation_contexts` 保存 JSONB manifest/snapshot，`context_hash` 继续使用现有规范化算法。

- [ ] **步骤 6：改造自由模式服务连接类型**

把 `creative/free_mode_service.py` 中直接调用 `_connect()` 的事务改为 `transaction_scope()`，类型改为 SQLAlchemy `Connection`。服务不得拼接 SQL 表名；需要的读写方法下沉到版本仓储。

- [ ] **步骤 7：运行版本和自由模式回归**

运行：

```powershell
uv run pytest tests/test_postgres_creative_versions.py tests/test_creative_phase4.py tests/test_creative_free_mode.py tests/test_creative_free_generation.py tests/test_original_direction_generation.py -q
```

预期：PASS；并发 Head 测试只能有一个写入成功。

- [ ] **步骤 8：Commit**

```powershell
git add creative/repositories/versions.py creative/postgres_repository.py creative/free_mode_service.py tests/test_postgres_creative_versions.py tests/test_creative_repository_contract.py
git commit -m "refactor(creative): move creative versions and heads to PostgreSQL"
```

---

### 任务 6：迁移幂等操作、生成租约、删除和 Writer 回滚状态

**文件：**
- 创建：`creative/repositories/operations.py`
- 创建：`tests/test_postgres_creative_operations.py`
- 修改：`creative/postgres_repository.py`
- 修改：`tests/test_creative_repository_contract.py`

- [ ] **步骤 1：编写竞争和租约失败测试**

覆盖 `record_operation_result`、side-effect reservation、generation reservation、续租、失败、完成、过期接管、删除快照和 Writer rollback。并发测试必须使用两个独立 PostgreSQL connection。

```python
def test_generation_reservation_has_one_owner(postgres_factory, postgres_creative):
    project = postgres_creative.create_original_project(
        name="租约项目", seed_text="种子", target_episode_count=20,
        created_by="user-1", creation_request_id="lease-project",
    )
    first = postgres_factory().reserve_generation_operation(
        operation_id="op-1", project_id=project["id"], stage="outline",
        request_hash="hash", owner_id="a",
    )
    second = postgres_factory().reserve_generation_operation(
        operation_id="op-1", project_id=project["id"], stage="outline",
        request_hash="hash", owner_id="b",
    )
    assert first["owner_id"] == "a"
    assert second["owner_id"] == "a"
```

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_postgres_creative_operations.py -q`

预期：FAIL，操作仓储方法尚未实现。

- [ ] **步骤 3：实现幂等结果和租约条件更新**

使用唯一操作 ID 和请求哈希判断重放。续租与完成使用：

```python
update(creative_generation_operations).where(
    creative_generation_operations.c.operation_id == operation_id,
    creative_generation_operations.c.owner_id == owner_id,
    creative_generation_operations.c.status == "active",
).values(updated_at=now).returning(creative_generation_operations.c.operation_id)
```

影响行数为 0 时按当前状态返回原有冲突异常，不进行无条件重试。

- [ ] **步骤 4：实现删除和 Writer 状态导入导出**

用显式表对象白名单替代动态表名和 `PRAGMA table_info`。恢复顺序必须先项目资源、后技术操作；主键冲突要比较规范化内容，内容不一致时拒绝覆盖。

- [ ] **步骤 5：运行操作回归**

运行：

```powershell
uv run pytest tests/test_postgres_creative_operations.py tests/test_creative_operation_idempotency.py tests/test_writer_agent_rollback.py tests/test_creative_lifecycle.py -q
```

预期：PASS；租约竞争、删除恢复和重放测试稳定通过。

- [ ] **步骤 6：Commit**

```powershell
git add creative/repositories/operations.py creative/postgres_repository.py tests/test_postgres_creative_operations.py tests/test_creative_repository_contract.py
git commit -m "refactor(creative): move idempotency and generation leases to PostgreSQL"
```

---

### 任务 7：迁移审核、项目长审和剧本优化

**文件：**
- 创建：`creative/repositories/reviews.py`
- 创建：`tests/test_postgres_creative_reviews.py`
- 修改：`creative/postgres_repository.py`
- 修改：`tests/test_creative_repository_contract.py`

- [ ] **步骤 1：编写审核与优化状态机测试**

覆盖创建、幂等长审、开始、checkpoint、完成、失败、取消、人工确认、stale 检测、优化任务、草稿接受/拒绝和同集 peer 草稿关联。

```python
def test_confirmed_review_cannot_be_completed_again(postgres_creative):
    review = postgres_creative.create_script_review(
        source_type="text", mode="single", project_id=None, episode_numbers=[1],
        source_fingerprint="fp", source_payload={"text": "内容"}, created_by="user-1",
    )
    postgres_creative.start_script_review(review["id"])
    postgres_creative.complete_script_review(review["id"], {"ai_verdict": "通过"})
    postgres_creative.confirm_script_review(
        review["id"], "通过", "ok", "owner", current_fingerprint="fp",
    )
    with pytest.raises(ValueError, match="review status changed"):
        postgres_creative.complete_script_review(review["id"], {"score": 90})
```

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_postgres_creative_reviews.py -q`

预期：FAIL，审核仓储方法尚未实现。

- [ ] **步骤 3：实现审核仓储**

所有状态转换通过 `WHERE id=:id AND status IN (...)` 条件更新加 `RETURNING`。项目长审幂等键从 `source_payload_json` 的 `request_key` 读取时兼容 JSONB 字典，不再重复 `json.loads()`。

- [ ] **步骤 4：实现优化与草稿仓储**

优化完成和草稿写入在同一事务中；接受草稿时锁住优化任务、草稿和目标分集版本，保证一个草稿只接受一次且 accepted episode 与项目一致。

- [ ] **步骤 5：运行审核回归**

运行：

```powershell
uv run pytest tests/test_postgres_creative_reviews.py tests/test_script_reviews.py tests/test_script_review_api.py tests/test_project_review_repository.py tests/test_scene_scripts.py -q
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add creative/repositories/reviews.py creative/postgres_repository.py tests/test_postgres_creative_reviews.py tests/test_creative_repository_contract.py
git commit -m "refactor(review): move creative reviews and optimizations to PostgreSQL"
```

---

### 任务 8：迁移交付、生产包、阶段导出和生产状态

**文件：**
- 创建：`creative/repositories/deliveries.py`
- 创建：`tests/test_postgres_creative_deliveries.py`
- 修改：`creative/delivery.py:60-130,204-430,670-695`
- 修改：`creative/production_exports.py:68-132,140-420`
- 修改：`creative/production_export.py:40-60,1077-1265`
- 修改：`creative/production_status.py:43-103`
- 修改：`creative/postgres_repository.py`

- [ ] **步骤 1：编写失败的交付和导出测试**

覆盖正式/非正式交付唯一键、阶段指纹、审计补写、生产包指纹、文件 SHA-256、部分集导出、版本 manifest 和生产状态 upsert。

```python
def test_delivery_request_is_idempotent(postgres_creative, existing_project_id):
    values = {
        "id": "delivery-1", "project_id": existing_project_id, "stage": "brief",
        "resource_type": "brief", "resource_id": "10", "resource_version": 1,
        "submission_id": "submission-1", "submission_version": 1,
        "format": "json", "content_fingerprint": "fp", "actor_id": "user-1",
        "path": "deliveries/brief.json", "is_formal": True,
    }
    first = postgres_creative.record_delivery(values)
    replay = postgres_creative.record_delivery(values)
    assert replay["id"] == first["id"]
```

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_postgres_creative_deliveries.py -q`

预期：FAIL，交付仓储或 PostgreSQL 快照入口不存在。

- [ ] **步骤 3：把交付表操作下沉到仓储**

测试文件定义 `existing_project_id` fixture，通过 `create_original_project()` 建立真实项目并返回 ID。删除 `CreativeDeliveryService._init_schema()` 和 `ProductionExportService._init_schema()`。交付仓储提供 `record_delivery(values, connection=None)`，按已命名唯一约束回读完全相同的请求。捕获 PostgreSQL `IntegrityError` 时只处理该幂等约束，其他完整性错误继续抛出。

- [ ] **步骤 4：改造生产包快照读取**

把：

```python
_inspect_snapshot(db_path, project_id, episode_numbers, version_manifest)
```

改为：

```python
_inspect_snapshot(repository, project_id, episode_numbers, version_manifest, connection=None)
```

所有快照查询在一个只读事务连接中完成，保证项目、Head 和版本 manifest 来自同一数据库快照。

- [ ] **步骤 5：迁移生产状态**

`sync_production_status()` 接收 `CreativeRepository`，文件系统检查结束后调用仓储 upsert；`get_production_status()` 从 `production_status` JSONB 列读取。文件 I/O 在事务外执行。

- [ ] **步骤 6：运行交付和生产包回归**

运行：

```powershell
uv run pytest tests/test_postgres_creative_deliveries.py tests/test_production_export.py tests/test_production_exports_web.py tests/test_production_migration_guard.py tests/test_weekly_report_service.py -q
```

预期：PASS；代码中不再存在这三处运行时 DDL。

- [ ] **步骤 7：Commit**

```powershell
git add creative/repositories/deliveries.py creative/delivery.py creative/production_exports.py creative/production_export.py creative/production_status.py creative/postgres_repository.py tests/test_postgres_creative_deliveries.py
git commit -m "refactor(delivery): persist creative exports in PostgreSQL"
```

---

### 任务 9：实现 SQLite 源检查和确定性数据转换

**文件：**
- 创建：`creative/sqlite_import.py`
- 创建：`tests/test_creative_sqlite_import.py`
- 修改：`creative/database_schema.py`

- [ ] **步骤 1：编写源检查失败测试**

构造最小 legacy SQLite fixture，覆盖 23 张现存表；另建缺列、非法 JSON、非法时间、非法布尔、重复唯一键、断裂父版本和 Head 孤儿 fixture。测试文件定义 `legacy_creative_db(tmp_path)` fixture，使用冻结的 legacy DDL 创建源库并返回路径；`corrupt_value(path, table, column, value)` 只能从测试内的表/列白名单选择目标。

```python
def test_inspection_rejects_invalid_json(legacy_creative_db):
    corrupt_value(legacy_creative_db, "creative_topics", "evidence_json", "{")
    with pytest.raises(ValueError, match=r"creative_topics\.evidence_json.*row"):
        inspect_sqlite_creative(legacy_creative_db)
```

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_creative_sqlite_import.py -q`

预期：FAIL，缺少 `inspect_sqlite_creative`。

- [ ] **步骤 3：定义显式表规范**

在 `creative/sqlite_import.py` 定义不可变 `CreativeTableSpec`，逐表列出主键、JSON 列、时间列、布尔列、依赖表和稳定排序列。禁止根据列名后缀猜测所有类型。

```python
@dataclass(frozen=True)
class CreativeTableSpec:
    name: str
    primary_key: tuple[str, ...]
    json_columns: frozenset[str]
    timestamp_columns: frozenset[str]
    boolean_columns: frozenset[str]
    dependencies: tuple[str, ...]
```

- [ ] **步骤 4：实现只读检查**

使用 `source.as_uri() + "?mode=ro"` 打开 SQLite，先执行 `PRAGMA integrity_check`，再检查 schema、行数、ID 范围、JSON、时间、布尔、唯一键和内部引用。原库缺少尚未使用的 `production_status` 时按空表处理，其他必需表缺失则根据已知历史 schema 迁移规则补充报告，不修改源文件。

- [ ] **步骤 5：实现规范化行与哈希**

```python
def canonical_row(row: Mapping[str, Any], spec: CreativeTableSpec) -> bytes:
    normalized = normalize_for_target(row, spec)
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=canonical_json_default,
    ).encode("utf-8")
```

浮点时间统一转换为 UTC ISO 8601 后参与迁移哈希；业务内容指纹字段保持原字符串。

- [ ] **步骤 6：增加目标跨域只读检查**

当传入 `SystemDatabase` 时，检查目标创作表为空，并把 `created_by`、`actor_id`、`workflow_task_id`、`submission_id` 分类为完整、合法历史缺口或非法孤儿。非法孤儿使检查失败；合法历史缺口进入报告但不自动造数据。

- [ ] **步骤 7：运行源检查测试**

运行：`uv run pytest tests/test_creative_sqlite_import.py -q`

预期：所有有效 fixture 通过，所有损坏 fixture 返回精确表名、列名和主键。

- [ ] **步骤 8：Commit**

```powershell
git add creative/sqlite_import.py creative/database_schema.py tests/test_creative_sqlite_import.py
git commit -m "feat(migration): validate legacy creative SQLite data"
```

---

### 任务 10：实现单事务导入、验证、sequence 重置和 CLI

**文件：**
- 创建：`scripts/migrate_creative_sqlite_to_postgres.py`
- 修改：`creative/sqlite_import.py`
- 修改：`tests/test_creative_sqlite_import.py`

- [ ] **步骤 1：编写原 ID 保留和整体回滚测试**

```python
def test_import_preserves_ids_and_resets_sequences(legacy_creative_db, isolated_system_database):
    report = import_sqlite_creative(legacy_creative_db, isolated_system_database)
    assert report.verified is True
    repository = PostgresCreativeRepository(isolated_system_database)
    created = repository.create_original_project(
        name="迁移后项目", seed_text="新种子", target_episode_count=20,
        created_by="user-1",
        creation_request_id="after-import",
    )
    assert created["id"] > report.id_ranges["creative_projects"][1]
```

再注入中途失败，断言 24 张业务表均没有部分导入结果，`creative_database_import_runs` 只保留一条 `failed` 控制记录及脱敏错误摘要。

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_creative_sqlite_import.py -q`

预期：FAIL，缺少 `import_sqlite_creative`。

- [ ] **步骤 3：实现依赖顺序导入**

先在独立短事务中写入 `creative_database_import_runs(status="running")`。业务导入顺序固定为：metadata → topics → breakdowns → projects → seeds/versions → heads/contexts → operations → reviews/optimizations → deliveries/exports → legacy migration records。使用 Core bulk insert，显式传入原 ID；不使用 conflict-ignore。业务事务成功后把控制记录更新为 `completed`；失败时业务事务整体回滚，再用独立短事务把控制记录更新为 `failed`。

- [ ] **步骤 4：重置 identity sequence**

对每个 identity 列执行等价逻辑：

```python
sequence = connection.execute(
    select(func.pg_get_serial_sequence(table.name, "id"))
).scalar_one()
maximum = connection.execute(select(func.max(table.c.id))).scalar_one()
connection.execute(
    text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"),
    {"sequence": sequence, "value": maximum or 1, "called": maximum is not None},
)
```

随后在事务内验证下一 ID 不会与源最大值冲突；测试完成后回滚探测写入。

- [ ] **步骤 5：实现验证报告**

逐表对比行数、主键范围和规范化哈希；验证 store ID、版本链、Head、工作流身份、文件引用、约束和 sequence。完成时把源 SHA-256、应用 commit、Alembic revision、统计、哈希和结果写回 `creative_database_import_runs`。目标非空判断忽略该控制表，只检查 24 张业务表。

- [ ] **步骤 6：实现 Typer CLI**

CLI 必须恰好选择一个模式：

```python
def main(
    source: Path = typer.Option(..., exists=True, readable=True),
    check: bool = typer.Option(False),
    apply: bool = typer.Option(False),
    verify: bool = typer.Option(False),
) -> None:
    ...
```

`--check` 可仅检查源；`--apply` 和 `--verify` 强制使用环境中的 PostgreSQL，并拒绝测试安全名称保护失败、Alembic 非 head 或目标非空。正式生产执行由 Runbook 的维护模式、备份和人工确认保护，CLI 不因目标是生产库而错误拒绝已经明确发起的正式迁移。`--verify` 的强制门槛是逐表完整哈希一致，不能只比较行数或抽样主键。

- [ ] **步骤 7：运行导入和 CLI 测试**

运行：

```powershell
uv run pytest tests/test_creative_sqlite_import.py tests/test_creative_postgres_schema.py -q
uv run python scripts/migrate_creative_sqlite_to_postgres.py --source .runtime/creative/creative.db --check
```

预期：测试通过；对真实源的 `--check` 只读完成并输出 JSON，不修改 SQLite。

- [ ] **步骤 8：Commit**

```powershell
git add creative/sqlite_import.py scripts/migrate_creative_sqlite_to_postgres.py tests/test_creative_sqlite_import.py
git commit -m "feat(migration): import creative SQLite into PostgreSQL"
```

---

### 任务 11：实现 PostgreSQL 到 SQLite 的 7 天无损回退导出

**文件：**
- 创建：`creative/sqlite_rollback.py`
- 创建：`scripts/rollback_creative_postgres_to_sqlite.py`
- 创建：`tests/test_creative_sqlite_rollback.py`
- 修改：`creative/sqlite_import.py`

- [ ] **步骤 1：编写往返失败测试**

测试路径固定为 SQLite A → PostgreSQL → SQLite B，比较 A 与 B 的逐表行数、ID、store ID 和规范化哈希；再在 PostgreSQL 新增项目和版本，确认 B 包含切换后的新增数据。

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_creative_sqlite_rollback.py -q`

预期：FAIL，缺少回退导出器。

- [ ] **步骤 3：实现全新文件导出**

导出器只允许写入不存在的新路径：

```python
if destination.exists():
    raise FileExistsError(f"rollback destination already exists: {destination}")
```

用冻结的 legacy SQLite DDL 创建 24 张表，把 PostgreSQL `TIMESTAMPTZ` 转回 Unix 秒、JSONB 转为稳定 JSON 文本、Boolean 转为 0/1，并显式插入原 ID。

- [ ] **步骤 4：验证回退文件**

导出完成后执行 SQLite `integrity_check`、foreign key check、逐表哈希、版本链和 Head 校验。先写临时文件，验证成功后用原子重命名发布最终文件；失败时保留错误报告，不发布目标路径。

- [ ] **步骤 5：实现回退 CLI 安全边界**

要求明确的 `--destination`，解析后的路径必须位于用户指定的回退目录内；禁止覆盖 `.runtime/creative/creative.db` 和原封存文件。输出 SHA-256 及验证报告。

- [ ] **步骤 6：运行回退测试**

运行：`uv run pytest tests/test_creative_sqlite_rollback.py tests/test_creative_sqlite_import.py -q`

预期：往返哈希一致，新增 PostgreSQL 数据不会丢失，覆盖和越界路径被拒绝。

- [ ] **步骤 7：Commit**

```powershell
git add creative/sqlite_rollback.py scripts/rollback_creative_postgres_to_sqlite.py tests/test_creative_sqlite_rollback.py
git commit -m "feat(migration): add lossless creative SQLite rollback export"
```

---

### 任务 12：切换 WebGUI、API 和 worker 到共享 PostgreSQL 仓储

**文件：**
- 修改：`WebGUI.py:516-550,2940-3140`
- 修改：`creative/api.py:989-1010,1870-1890,2470-2490,3420-3540,4945-4965`
- 修改：`creative/application_service.py:1200-1230`
- 修改：`creative/selection_stage_service.py:165-190`
- 修改：`creative/breakdown_worker.py:70-90`
- 修改：`creative/phase4_worker.py:345-370,935-985`
- 修改：`screenplay_review/api.py:180-205,360-385`
- 修改：`screenplay_review/worker.py:45-65`
- 修改：`screenplay_optimization/api.py:145-170,325-350`
- 修改：`screenplay_optimization/worker.py:40-60`
- 修改：`project_review/worker.py:135-155`
- 修改：`workflow/transition_service.py:350-375`
- 修改：`tests/test_worker_launcher.py`
- 修改：`tests/test_creative_breakdown_worker.py`
- 修改：`tests/test_creative_phase4_generation.py`
- 修改：`tests/test_script_review_api.py`
- 修改：`tests/test_script_optimizations.py`
- 修改：`tests/test_project_review_worker.py`
- 修改：`tests/test_writer_agent_integration.py`
- 修改：`tests/test_page_api_authorization.py`

- [ ] **步骤 1：编写失败的运行时边界测试**

新增源码扫描断言：生产模块不得出现 `creative.db`、任务 `db_path` 或从路径构造 `CreativeRepository`。worker 测试只传业务 ID，并 monkeypatch `SystemDatabaseSettings.from_environment()`。

```python
def test_creative_worker_payload_does_not_expose_database_location():
    payload = build_generation_payload(project_id=12, operation_id="op-1")
    assert "db_path" not in payload
    assert "database_url" not in payload
```

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/test_worker_launcher.py tests/test_creative_breakdown_worker.py tests/test_script_reviews.py -q`

预期：FAIL，当前 payload 仍包含 `db_path`。

- [ ] **步骤 3：WebGUI 注入共享实例**

把：

```python
creative_repository = CreativeRepository(WORKSPACE_ROOT / ".runtime/creative/creative.db")
```

改为：

```python
creative_repository = PostgresCreativeRepository(system_database)
```

所有 creative、workflow、audit、review、optimization 和 writer-agent router 使用该共享实例，不允许 router 在缺少注入时回退到 SQLite。

- [ ] **步骤 4：移除任务数据库位置参数**

从所有任务 payload 删除 `db_path` 和 `creative_db_path`。worker 启动函数统一调用：

```python
database = SystemDatabase(SystemDatabaseSettings.from_environment())
repository = PostgresCreativeRepository(database)
```

worker 退出时调用 `database.dispose()`；同一 worker 内的心跳和业务写入复用同一仓储。

- [ ] **步骤 5：删除隐式 SQLite 回退**

`build_creative_router`、审核/优化 router 和 `TransitionService` 必须要求显式仓储或 `SystemDatabase`。删除根据 `runtime_dir`、旧 `identity_repository.db_path` 推导创作库路径的逻辑。

- [ ] **步骤 6：运行 API 和 worker 回归**

运行：

```powershell
uv run pytest tests/test_worker_launcher.py tests/test_creative_breakdown_worker.py tests/test_creative_phase4_generation.py tests/test_script_reviews.py tests/test_writer_agent_integration.py tests/test_page_api_authorization.py -q
```

预期：PASS；任务参数和日志中没有数据库 URL 或路径。

- [ ] **步骤 7：Commit**

```powershell
git add WebGUI.py creative/api.py creative/application_service.py creative/selection_stage_service.py creative/breakdown_worker.py creative/phase4_worker.py screenplay_review/api.py screenplay_review/worker.py screenplay_optimization/api.py screenplay_optimization/worker.py project_review/worker.py workflow/transition_service.py tests/test_worker_launcher.py tests/test_creative_breakdown_worker.py tests/test_creative_phase4_generation.py tests/test_script_review_api.py tests/test_script_optimizations.py tests/test_project_review_worker.py tests/test_writer_agent_integration.py tests/test_page_api_authorization.py
git commit -m "refactor(runtime): route creative workloads through PostgreSQL"
```

提交前用 `git diff --cached --name-only` 确认没有把无关文件加入提交。

---

### 任务 13：实现创作域与工作流域共享事务

**文件：**
- 修改：`identity/repository.py` 的 connection scope 公共接口
- 修改：`workflow/repository.py` 的项目/任务写方法签名
- 修改：`creative/workflow_adapter.py`
- 修改：`creative/original_project_service.py`
- 修改：`creative/application_service.py`
- 修改：`creative/delivery.py`
- 修改：`writer_agent/orchestrator.py`
- 修改：`writer_agent/rollback_service.py`
- 修改：`tests/test_creative_workflow_integration.py`
- 修改：`tests/test_original_workflow_integration.py`
- 修改：`tests/test_delivery_permissions.py`
- 修改：`tests/test_writer_agent_rollback.py`

- [ ] **步骤 1：编写跨域原子性失败测试**

在项目创建、阶段锁定、正式交付和 Writer 删除中分别注入第二域失败，断言创作表与工作流表都没有部分提交。测试文件提供由 `IdentityRepository(isolated_system_database)` 构造的 `workflow_repository` fixture；服务级测试在 WorkflowRepository 写入前注入明确异常。

```python
def test_shared_transaction_rolls_back_creative_and_workflow_rows(
    isolated_system_database, postgres_creative, workflow_repository,
):
    with pytest.raises(RuntimeError, match="injected task failure"):
        with isolated_system_database.transaction() as connection:
            postgres_creative.create_original_project(
                name="原子项目", seed_text="种子", target_episode_count=20,
                created_by="user-1", creation_request_id="atomic-1",
                connection=connection,
            )
            raise RuntimeError("injected task failure")
    with isolated_system_database.connection() as connection:
        creative_count = connection.execute(
            select(func.count()).select_from(creative_projects)
        ).scalar_one()
        workflow_count = connection.execute(
            select(func.count()).select_from(workflow_tasks)
        ).scalar_one()
    assert creative_count == 0
    assert workflow_count == 0
```

- [ ] **步骤 2：运行测试确认当前存在半提交风险**

运行：

```powershell
uv run pytest tests/test_creative_workflow_integration.py tests/test_original_workflow_integration.py tests/test_delivery_permissions.py tests/test_writer_agent_rollback.py -q
```

预期：新增原子性测试 FAIL。

- [ ] **步骤 3：统一可选 Connection 签名**

对跨域方法使用一致签名：

```python
def ensure_project_task(..., connection: Connection | None = None) -> dict[str, Any]:
    with self.identity_repository.transaction_scope(connection) as active:
        return self._ensure_project_task(active, ...)
```

传入外部连接时不得提交、回滚或关闭；未传入时保持方法可独立使用。

- [ ] **步骤 4：服务层拥有事务边界**

项目创建、锁定推进、正式交付和删除由 application service 开启一次 `system_database.transaction()`，把同一 connection 传给 creative、workflow 和 audit 仓储。外部 LLM/文件操作先在事务外完成，再用短事务登记结果。

- [ ] **步骤 5：统一锁顺序**

所有跨域写操作固定按“creative project advisory lock → creative resource row → workflow project/task row → audit insert”顺序加锁。不得在 WorkflowRepository 内反向获取创作锁。

- [ ] **步骤 6：运行原子性和死锁回归**

运行：

```powershell
uv run pytest tests/test_creative_workflow_integration.py tests/test_original_workflow_integration.py tests/test_delivery_permissions.py tests/test_writer_agent_rollback.py tests/test_workflow_identity_migration.py -q
```

预期：注入失败时两域同时回滚；并发测试无死锁。

- [ ] **步骤 7：Commit**

```powershell
git add identity/repository.py workflow/repository.py creative/workflow_adapter.py creative/original_project_service.py creative/application_service.py creative/delivery.py writer_agent/orchestrator.py writer_agent/rollback_service.py tests/test_creative_workflow_integration.py tests/test_original_workflow_integration.py tests/test_delivery_permissions.py tests/test_writer_agent_rollback.py
git commit -m "refactor(workflow): make creative transitions transactionally atomic"
```

---

### 任务 14：完成 PostgreSQL 契约、并发、迁移和性能验证

**文件：**
- 修改：`tests/test_creative_repository_contract.py`
- 修改：`tests/test_postgres_creative_topics.py`
- 修改：`tests/test_postgres_creative_projects.py`
- 修改：`tests/test_postgres_creative_versions.py`
- 修改：`tests/test_postgres_creative_operations.py`
- 修改：`tests/test_postgres_creative_reviews.py`
- 修改：`tests/test_postgres_creative_deliveries.py`
- 修改：`.gitea/workflows/alembic-integrity.yml`
- 创建：`tools/check_creative_postgres_queries.py`

- [ ] **步骤 1：建立完整公开方法清单测试**

从 legacy `CreativeRepository` 导出所有非私有方法名，断言 `PostgresCreativeRepository` 全部实现；为每个方法至少存在一个直接测试或上层集成测试映射。

```python
import inspect as pyinspect

def public_methods(repository_type):
    return {
        name for name, value in pyinspect.getmembers(repository_type)
        if callable(value) and not name.startswith("_")
    }

def test_postgres_repository_covers_legacy_public_surface():
    legacy = public_methods(LegacySQLiteCreativeRepository)
    postgres = public_methods(PostgresCreativeRepository)
    assert legacy - postgres == set()
```

- [ ] **步骤 2：迁移高价值 SQLite fixture 到 PostgreSQL**

优先参数化以下套件：

- `tests/test_creative_phase4.py`；
- `tests/test_creative_phase4_generation.py`；
- `tests/test_creative_free_mode.py`；
- `tests/test_creative_lifecycle.py`；
- `tests/test_creative_operation_idempotency.py`；
- `tests/test_original_creative_repository.py`；
- `tests/test_script_reviews.py`；
- `tests/test_production_export.py`。

迁移器自身的源 fixture 保持 SQLite，不把所有 `sqlite3` 测试机械删除。

完成优先套件后运行 `rg -n "CreativeRepository\(|creative\.db" tests`，把其余运行时行为测试全部改为 `isolated_system_database`/`postgres_creative` fixture；只有迁移器、回退器和明确标记的 legacy 契约测试可以继续创建 SQLite 文件。

- [ ] **步骤 3：增加真实多连接竞争测试**

使用 `ThreadPoolExecutor` 和独立仓储实例验证项目创建、版本号、Head、生成租约、审核确认及交付幂等。每个用例设置超时，失败时输出 PostgreSQL 锁信息。

- [ ] **步骤 4：增加查询计划检查工具**

`tools/check_creative_postgres_queries.py` 对项目列表、版本列表、Head、待恢复生成操作、审核和交付查询执行 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`；若生产规模 fixture 下出现预期之外的全表扫描或单查询超过约定阈值则返回非零。

- [ ] **步骤 5：接入 CI**

在 `.gitea/workflows/alembic-integrity.yml` 的 PostgreSQL service 中运行：

```yaml
- run: uv run pytest tests/test_creative_postgres_schema.py tests/test_creative_sqlite_import.py tests/test_creative_sqlite_rollback.py -q
- run: uv run python tools/check_alembic_integrity.py production
```

不得在 CI 日志打印数据库密码。

- [ ] **步骤 6：运行完整测试矩阵**

运行：

```powershell
uv run pytest tests/ -q
uv run pytest test/ -q
uv run python tools/check_creative_postgres_queries.py
uv run pre-commit run --all-files
```

预期：所有不依赖外部平台凭证的测试通过；环境型跳过必须列出原因，不得把失败改为无条件 skip。

- [ ] **步骤 7：Commit**

```powershell
git add tests/test_creative_repository_contract.py tests/test_postgres_creative_topics.py tests/test_postgres_creative_projects.py tests/test_postgres_creative_versions.py tests/test_postgres_creative_operations.py tests/test_postgres_creative_reviews.py tests/test_postgres_creative_deliveries.py tests/test_creative_phase4.py tests/test_creative_phase4_generation.py tests/test_creative_free_mode.py tests/test_creative_lifecycle.py tests/test_creative_operation_idempotency.py tests/test_original_creative_repository.py tests/test_script_reviews.py tests/test_production_export.py .gitea/workflows/alembic-integrity.yml tools/check_creative_postgres_queries.py
git commit -m "test(creative): verify PostgreSQL migration and concurrency"
```

提交前检查 staged 文件，排除与本任务无关的用户改动。

---

### 任务 15：准备过渡版本、生产演练和正式切换 Runbook

**文件：**
- 创建：`docs/operations/creative-postgresql-cutover.md`
- 创建：`docs/operations/creative-cross-domain-reference-audit.md`
- 修改：`README.md:76-117,167-179,264-273`
- 修改：`docs/index.md`
- 修改：`creative/repository_factory.py`
- 修改：`tests/test_creative_sqlite_import.py`
- 修改：`tests/test_creative_sqlite_rollback.py`

- [ ] **步骤 1：编写存储开关安全测试**

过渡版本只接受 `sqlite` 或 `postgresql`；选择 PostgreSQL 时必须为 Alembic head，选择 SQLite 时必须显式配置，不能静默回退。

```python
class UnavailableDatabase:
    url = make_url("postgresql+psycopg://app:test@127.0.0.1:5432/test")

    def health(self):
        return SimpleNamespace(reachable=False, schema_current=False)

def test_postgres_selection_never_falls_back_to_sqlite(monkeypatch):
    monkeypatch.setenv("AIWORKER_CREATIVE_STORAGE", "postgresql")
    with pytest.raises(RuntimeError, match="creative PostgreSQL is unavailable"):
        build_runtime_creative_repository(UnavailableDatabase())
```

在 `repository_factory.py` 新增 `build_runtime_creative_repository(database)`：从 `AIWORKER_CREATIVE_STORAGE` 读取后端，选择 PostgreSQL 时先检查 `database.health().reachable` 和 `schema_current`，失败直接抛错，绝不回退 SQLite。

- [ ] **步骤 2：编写生产 Runbook**

`docs/operations/creative-postgresql-cutover.md` 必须逐项包含：维护公告、停止进程、未决任务检查、SQLite Backup API 备份、SHA-256、PostgreSQL 逻辑备份、`--check`、Alembic、`--apply`、`--verify`、存储开关、启动顺序、冒烟、业务抽查、解除维护和 7 天观察。

Runbook 同时给出不含凭证的运行检查 SQL：通过 `pg_stat_activity` 查看连接与等待事件，通过 `pg_locks` 查看未授予锁，通过应用日志统计死锁、唯一冲突、租约恢复和事务回滚；若环境启用 `pg_stat_statements`，再检查项目列表、版本列表和 Head 查询的调用次数及平均耗时。数据库权限章节明确遵循最小权限：Alembic 账号负责 DDL，应用账号只拥有创作/系统表 DML 和 identity sequence 使用权。

- [ ] **步骤 3：固定强制停止条件**

Runbook 明确以下任一情况立即停止：SQLite integrity 非 `ok`、非法 JSON/时间、目标非空、非法孤儿、逐表数量或哈希不同、sequence 无效、Alembic 非 head、冒烟失败。不得提供跳过强制验证的参数。

- [ ] **步骤 4：执行两次脱敏副本演练**

每次演练保存：源 SHA-256、各阶段耗时、逐表统计和哈希、孤儿报告、导入报告、回退报告、实际停机模拟时长。第二次演练必须从全新隔离 PostgreSQL schema 开始。

把 `created_by`、`actor_id`、`workflow_task_id` 和 `submission_id` 的审计结果写入 `creative-cross-domain-reference-audit.md`。只有同时满足“零孤儿”和“被引用记录生命周期不会被硬删除”时才允许建立外键；否则保留命名索引与应用层写入校验，并在文档中记录不建外键的具体生命周期原因。该判断逐列执行，不以抽样代替全量查询。

- [ ] **步骤 5：更新用户和运维文档**

README 改为创作数据与系统数据均存储在 PostgreSQL；`.runtime/creative/creative.db` 仅作为历史迁移源。`docs/index.md` 增加迁移 CLI 和 Runbook 链接。

- [ ] **步骤 6：运行文档及过渡版本验证**

运行：

```powershell
uv run pytest tests/test_creative_sqlite_import.py tests/test_creative_sqlite_rollback.py tests/test_worker_launcher.py -q
uv run pre-commit run --files creative/repository_factory.py tests/test_creative_sqlite_import.py tests/test_creative_sqlite_rollback.py README.md docs/index.md docs/operations/creative-postgresql-cutover.md
```

预期：PASS；Runbook 命令可在隔离环境完整复制执行。

- [ ] **步骤 7：Commit**

```powershell
git add docs/operations/creative-postgresql-cutover.md docs/operations/creative-cross-domain-reference-audit.md README.md docs/index.md creative/repository_factory.py tests/test_creative_sqlite_import.py tests/test_creative_sqlite_rollback.py
git commit -m "docs(operations): add creative PostgreSQL cutover runbook"
```

---

### 任务 16：正式切换、7 天观察和运行时 SQLite 清理

**文件：**
- 修改：`creative/repository.py`
- 修改：`creative/repository_factory.py`
- 修改：`creative/production_export_cli.py`
- 修改：`scripts/migrate_role_workflow.py`
- 修改：`README.md`
- 创建：`tests/test_creative_runtime_postgres_only.py`
- 修改：迁移到 PostgreSQL fixture 的创作测试导入
- 最终删除：`creative/legacy_sqlite_repository.py`

- [ ] **步骤 1：在维护窗口执行正式导入**

严格执行 `docs/operations/creative-postgresql-cutover.md`。保存 SQLite 和 PostgreSQL 备份、迁移 JSON/Markdown 报告、应用 commit、Alembic revision 和负责人签字结果。

- [ ] **步骤 2：切换公共门面**

过渡发布中先保留 legacy 文件，把 `creative/repository.py` 改为：

```python
from creative.postgres_repository import PostgresCreativeRepository

CreativeRepository = PostgresCreativeRepository

__all__ = ["CreativeRepository", "PostgresCreativeRepository"]
```

生产默认且显式选择 PostgreSQL；SQLite 只能由回退 CLI 或明确的 legacy 测试导入。

- [ ] **步骤 3：执行正式冒烟和全链路验收**

至少验证一个原创项目、一个仿写项目和一个完整剧本项目：读取、编辑、生成、修订、锁定、审核、优化、正式交付和生产包导出。对照迁移报告重新执行 `--verify`。

- [ ] **步骤 4：观察 7 天**

每日检查连接池、慢查询、锁等待、死锁、唯一冲突、生成租约恢复、审核失败和交付失败。任何回退必须先使用 PostgreSQL → SQLite 导出器生成新文件，不允许直接启用原封存库。

- [ ] **步骤 5：编写清理前失败扫描测试**

```python
from pathlib import Path

RUNTIME_PATHS = ["creative", "screenplay_review", "screenplay_optimization", "project_review", "workflow", "WebGUI.py"]
EXCLUDED = {"creative/sqlite_import.py", "creative/sqlite_rollback.py"}

def scan(paths, patterns):
    matches = []
    for raw_path in paths:
        path = Path(raw_path)
        files = [path] if path.is_file() else path.rglob("*.py")
        for file in files:
            if file.as_posix() in EXCLUDED:
                continue
            text = file.read_text(encoding="utf-8")
            matches.extend((file.as_posix(), pattern) for pattern in patterns if pattern in text)
    return matches

def test_runtime_has_no_creative_sqlite_dependency():
    matches = scan(RUNTIME_PATHS, patterns=("sqlite3", "creative.db", "PRAGMA", "CREATE TABLE"))
    assert matches == []
```

排除 `creative/sqlite_import.py`、`creative/sqlite_rollback.py` 和对应 CLI/测试；它们不是运行时路径。

- [ ] **步骤 6：满 7 天后删除 SQLite 运行分支**

删除 `creative/legacy_sqlite_repository.py`、`AIWORKER_CREATIVE_STORAGE=sqlite` 分支、运行时路径参数和旧 CLI 默认 `--db`。`scripts/migrate_role_workflow.py` 改为 PostgreSQL 业务迁移或标记为只接受显式 legacy 源的离线工具。

- [ ] **步骤 7：运行最终验证**

运行：

```powershell
uv run pytest tests/ -q
uv run pytest test/ -q
uv run python tools/check_alembic_integrity.py production
uv run python tools/check_creative_postgres_queries.py
rg -n "creative\.db|import sqlite3|PRAGMA|CREATE TABLE|ALTER TABLE" creative screenplay_review screenplay_optimization project_review workflow WebGUI.py
uv run pre-commit run --all-files
```

预期：测试和检查通过；`rg` 结果仅允许离线导入/回退模块及明确的测试 fixture。

- [ ] **步骤 8：Commit**

```powershell
git add creative/repository.py creative/repository_factory.py creative/production_export_cli.py creative/legacy_sqlite_repository.py scripts/migrate_role_workflow.py README.md tests/test_creative_runtime_postgres_only.py
git commit -m "refactor(storage): remove runtime creative SQLite support"
```

提交前逐项检查 staged 文件，确保没有提交 `.runtime`、数据库备份、迁移报告中的敏感内容或用户无关改动。

---

## 最终验收清单

- [ ] 24 张创作业务表和 `creative_database_import_runs` 均由 Alembic 管理。
- [ ] PostgreSQL 保留全部源 ID、store ID、版本链、Head 和业务指纹。
- [ ] 源/目标逐表行数、ID 范围和标准化哈希完全一致。
- [ ] 所有 identity sequence 的下一值大于已导入最大 ID。
- [ ] WebGUI、API、worker、审核、优化、交付和导出只访问 PostgreSQL。
- [ ] 创作与工作流关键写操作使用同一事务。
- [ ] 并发项目创建、版本、Head、租约、审核和交付测试通过。
- [ ] SQLite → PostgreSQL → SQLite 往返验证无数据损失。
- [ ] 两次脱敏演练和一次正式切换均有报告。
- [ ] 7 天观察期完成，运行时 SQLite 支持已删除。
- [ ] README、文档索引、备份、恢复和监控说明已更新。
- [ ] `uv run pytest tests/ -q`、相关集成测试、Alembic 完整性检查和 pre-commit 全部通过。
