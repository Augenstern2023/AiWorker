# 榜单集中式 PostgreSQL 存储实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将抖音和红果的标准化榜单快照集中写入 PostgreSQL，让 `192.168.1.34` 与 `192.168.1.100` 读取同一份历史及新增榜单数据。

**架构：** 文件解析器把现有 JSON/CSV 转为 `RankSnapshot`，`RankImporter` 计算来源标识与校验值并调用 `RankRepository` 事务化写入 `rank_snapshots`、`rank_entries`。生产环境的榜单 API 和周报显式注入 PostgreSQL Repository；文件来源仅用于 `.34` 的历史回填、自动导入和现有解析回归测试，不作为生产查询回退。

**技术栈：** Python 3.11、SQLAlchemy 2、PostgreSQL、Alembic、FastAPI、Typer、pytest、uv。

---

## 实施前提

- 在以当前分支最新计划提交为基线的独立 Git worktree 中执行，避免触碰当前工作区的 `static/js/admin/users.js` 和 `tests/test_role_page_content.py` 用户改动。
- 所有 Python 命令使用 `uv run`，使用 uv 默认全局缓存，不创建仓库内 `.uv-cache*`。
- 新增 `.py` 文件复制项目现有版权头，确保 `pre-commit` 文件头检查通过。
- PostgreSQL 集成测试只使用 `AIWORKER_TEST_SYSTEM_DATABASE_URL` 指向的测试库；禁止对 `aiworker` 或 `aiworker_system` 生产库运行 pytest。
- 每次提交只包含当前任务列出的文件。

## 文件结构

### 新建

- `analysis/rank_models.py`：榜单快照、来源协议和写入结果等领域类型。
- `analysis/rank_file_source.py`：发现、解析、标准化本地抖音 JSON 与红果 JSON/CSV，并计算文件源版本。
- `analysis/rank_repository.py`：PostgreSQL 快照读写、幂等替换、反序列化和版本摘要。
- `analysis/rank_importer.py`：单文件导入、批量发现、校验值、来源标识和导入报告。
- `system_database/alembic/versions/0016_rank_storage.py`：榜单集中存储 schema migration。
- `scripts/migrate_rank_files_to_postgres.py`：历史榜单批量回填命令。
- `tests/test_rank_file_source.py`：文件解析器及领域对象回归测试。
- `tests/test_rank_repository.py`：PostgreSQL Repository 集成测试。
- `tests/test_rank_importer.py`：幂等导入、批量报告和命令行测试。
- `tests/test_today_rank_postgres_api.py`：集中查询和 503 API 契约测试。
- `docs/runbooks/centralized-rank-storage.md`：备份、升级、回填、校验和回滚手册。

### 修改

- `system_database/schema.py`：声明 `rank_snapshots`、`rank_entries` SQLAlchemy 表元数据。
- `tests/test_system_database_alembic.py`：验证 migration 创建的表、字段、索引、外键和约束。
- `analysis/today_rank_service.py`：从文件职责中解耦，通过 `RankSnapshotSource` 查询快照与版本。
- `analysis/today_rank_api.py`：生产环境装配 PostgreSQL 服务，并把数据库不可用映射为 HTTP 503。
- `analysis/weekly_report_service.py`：复用注入的集中榜单源及其版本摘要。
- `analysis/analytics_api.py`：为 WebGUI 周报显式装配 PostgreSQL 榜单服务。
- `agents/weekly_report_worker.py`：为后台周报任务显式装配 PostgreSQL 榜单服务。
- `agents/douyin_rank_worker.py`：抖音文件与分析完成后自动写入集中库。
- `agents/hongguo_csv_worker.py`：红果 CSV 转换完成后自动写入集中库。
- `tests/test_today_rank_phase.py`：保持现有文件解析和页面响应回归覆盖。
- `tests/test_weekly_report_service.py`：验证周报使用注入源的数据库版本。
- `tests/test_weekly_report_api.py`：验证 WebGUI 周报装配集中榜单源。
- `tests/test_douyin_rank_worker.py`：验证抖音自动同步成功、失败状态和执行顺序。
- `tests/test_hongguo_csv_worker.py`：验证红果自动同步成功、失败状态和执行顺序。

### 不修改

- `static/js/selection/ranks.js`：现有逻辑已展示 API 的 `detail`，503 返回“榜单数据库暂不可用”即可呈现。
- 原始榜单 JSON/CSV：只读导入，不移动、不改写、不删除。

---

### 任务 1：提取榜单领域模型和文件来源

**文件：**
- 创建：`analysis/rank_models.py`
- 创建：`analysis/rank_file_source.py`
- 修改：`analysis/today_rank_service.py:98-622,862-1387,1706-1960`
- 创建：`tests/test_rank_file_source.py`
- 测试：`tests/test_today_rank_phase.py`

- [ ] **步骤 1：编写失败的文件来源测试**

在临时目录创建一份抖音 JSON 和一份红果 CSV，直接测试新组件，不通过 `TodayRankService`：

```python
from analysis.rank_file_source import RankFileSource
from analysis.rank_models import RankSnapshot


def test_rank_file_source_parses_douyin_and_exposes_revision(tmp_path):
    rank_path = tmp_path / "data/dy/jobs/job-1/rank/douyin_series_rank_20260901_100000.json"
    write_json(rank_path, {
        "captured_at": "2026-09-01 10:00:00",
        "records": [{"rank": 1, "title": "集中榜单剧", "series_id": "dy-1", "tab": "榜单"}],
    })
    source = RankFileSource(root=tmp_path)

    snapshots = source.list_snapshots("dy")

    assert len(snapshots) == 1
    assert isinstance(snapshots[0], RankSnapshot)
    assert snapshots[0].source_path == rank_path
    assert snapshots[0].records[0]["title"] == "集中榜单剧"
    assert source.revision("dy") != source.empty_revision
```

再增加红果 CSV 用例，断言 `section`、`ranking_type`、数值指标、校验警告和原有 raw metadata 补全结果与 `tests/test_today_rank_phase.py` 的预期一致。

- [ ] **步骤 2：运行测试确认新模块尚不存在**

运行：

```powershell
uv run pytest tests/test_rank_file_source.py -q
```

预期：FAIL，报错 `ModuleNotFoundError: No module named 'analysis.rank_file_source'`。

- [ ] **步骤 3：定义稳定的领域接口**

在 `analysis/rank_models.py` 创建以下类型；`RankSnapshot.to_dict()` 对相对 `source_path` 使用 `/` 输出，对绝对路径使用传入根目录转换为相对路径：

```python
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class RankSnapshot:
    platform: str
    job_id: str
    source_path: Path
    captured_at: datetime
    records: list[dict[str, Any]]
    validation: dict[str, Any]

    def to_dict(self, root: Path) -> dict[str, Any]:
        source = self.source_path
        if source.is_absolute():
            try:
                source = source.resolve().relative_to(root.resolve())
            except ValueError:
                source = Path(source.name)
        return {
            "platform": self.platform,
            "job_id": self.job_id,
            "source_path": source.as_posix(),
            "captured_at": self.captured_at.strftime("%Y-%m-%d %H:%M:%S"),
            "date": self.captured_at.date().isoformat(),
            "record_count": len(self.records),
        }


class RankSnapshotSource(Protocol):
    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        raise NotImplementedError

    def revision(self, platform: str) -> str:
        raise NotImplementedError
```

- [ ] **步骤 4：提取纯文件来源，保持解析结果不变**

把 `RankSnapshot`、文件路径常量、文件版本计算、红果 raw index、文件发现、`_load_snapshot()`、`_load_hongguo_csv_snapshot()`、分析结果补全及其文件解析辅助函数移到 `analysis/rank_file_source.py`。公开接口固定为：

```python
class RankFileSource:
    empty_revision = hashlib.sha256(b"[]").hexdigest()

    def __init__(
        self,
        root: str | Path = ".",
        hongguo_exports_root: str | Path | None = None,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.hongguo_exports_root = resolve_hongguo_exports_root(
            self.root, hongguo_exports_root
        )
        self.hongguo_raw_root = resolve_hongguo_raw_root(self.hongguo_exports_root)
        self.cache_dir = resolve_rank_cache_dir(self.root, cache_dir)

    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        if platform not in {"dy", "hg"}:
            raise ValueError("platform must be dy or hg")
        return self._list_douyin() if platform == "dy" else self._list_hongguo()

    def parse_path(self, platform: str, path: str | Path) -> RankSnapshot:
        snapshot = self._parse_known_path(platform, Path(path))
        if snapshot is None:
            raise ValueError(f"unable to parse {platform} rank source: {path}")
        return snapshot

    def revision(self, platform: str) -> str:
        return self._paths_revision(self._relevant_paths(platform))
```

`TodayRankService` 暂时默认创建 `RankFileSource`，并只调用它的 `list_snapshots()` 与 `revision()`；比较、分组、日期选择、在榜时长和响应缓存仍保留在 service。避免 `rank_file_source` 反向导入 `TodayRankService`，确保无循环依赖。

- [ ] **步骤 5：运行解析器和现有榜单回归测试**

运行：

```powershell
uv run pytest tests/test_rank_file_source.py tests/test_today_rank_phase.py -q
```

预期：PASS，现有抖音/红果字段、排名变化、缓存和在榜时长断言全部保持不变。

- [ ] **步骤 6：提交领域模型与文件来源**

```powershell
git add analysis/rank_models.py analysis/rank_file_source.py analysis/today_rank_service.py tests/test_rank_file_source.py tests/test_today_rank_phase.py
git commit -m "refactor: extract rank file source"
```

---

### 任务 2：新增 PostgreSQL 榜单 schema

**文件：**
- 创建：`system_database/alembic/versions/0016_rank_storage.py`
- 修改：`system_database/schema.py`
- 修改：`tests/test_system_database_alembic.py`

- [ ] **步骤 1：编写失败的 metadata 和 migration 测试**

在 `tests/test_system_database_alembic.py` 增加：

```python
def test_system_schema_declares_central_rank_tables():
    snapshots = SYSTEM_METADATA.tables["rank_snapshots"]
    entries = SYSTEM_METADATA.tables["rank_entries"]
    assert snapshots.c.source_checksum.type.length == 64
    assert isinstance(snapshots.c.validation_json.type, JSONB)
    assert isinstance(entries.c.source_record_json.type, JSONB)
    foreign_key = next(iter(entries.c.snapshot_id.foreign_keys))
    assert foreign_key.ondelete == "CASCADE"


def test_upgrade_head_creates_central_rank_constraints(postgres_url, monkeypatch):
    run_alembic_upgrade(postgres_url, monkeypatch)
    engine = create_engine(postgres_url)
    try:
        inspector = inspect(engine)
        assert inspector.has_table("rank_snapshots")
        assert inspector.has_table("rank_entries")
        assert {item["name"] for item in inspector.get_unique_constraints("rank_snapshots")} >= {
            "uq_rank_snapshots_source_ref",
            "uq_rank_snapshots_checksum",
        }
        assert {item["name"] for item in inspector.get_foreign_keys("rank_entries")} == {
            "fk_rank_entries_snapshot_id"
        }
    finally:
        engine.dispose()
```

- [ ] **步骤 2：运行 schema 测试确认失败**

运行：

```powershell
uv run pytest tests/test_system_database_alembic.py -k "central_rank" -q
```

预期：FAIL，`SYSTEM_METADATA.tables` 中不存在 `rank_snapshots`。

- [ ] **步骤 3：声明 SQLAlchemy metadata**

在 `system_database/schema.py` 使用 `BigInteger`、`Numeric`、`String` 和 PostgreSQL `JSONB` 声明两张表。字段必须与 migration 完全一致，核心定义如下：

```python
rank_snapshots = Table(
    "rank_snapshots",
    SYSTEM_METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("platform", String(16), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("snapshot_date", Date, nullable=False),
    Column("job_id", Text),
    Column("source_kind", String(32), nullable=False),
    Column("source_ref", Text, nullable=False),
    Column("source_checksum", String(64), nullable=False),
    Column("parser_version", Integer, nullable=False, server_default=text("1")),
    Column("record_count", Integer, nullable=False),
    Column("validation_json", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("imported_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("platform IN ('dy', 'hg')", name="ck_rank_snapshots_platform"),
    CheckConstraint("record_count >= 0", name="ck_rank_snapshots_record_count"),
    UniqueConstraint("platform", "source_kind", "source_ref", name="uq_rank_snapshots_source_ref"),
    UniqueConstraint("platform", "source_checksum", name="uq_rank_snapshots_checksum"),
)

rank_entries = Table(
    "rank_entries",
    SYSTEM_METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("snapshot_id", BigInteger, ForeignKey("rank_snapshots.id", ondelete="CASCADE"), nullable=False),
    Column("section_name", Text, nullable=False, server_default=text("''")),
    Column("ranking_name", Text, nullable=False, server_default=text("''")),
    Column("rank", Integer, nullable=False),
    Column("series_key", Text, nullable=False, server_default=text("''")),
    Column("title", Text, nullable=False),
    Column("category", Text),
    Column("status", Text),
    Column("subject", Text),
    Column("description", Text),
    Column("hot_comment", Text),
    Column("score", Numeric(20, 4)),
    Column("share_count", BigInteger),
    Column("like_count", BigInteger),
    Column("favorite_count", BigInteger),
    Column("heat", Numeric(20, 4)),
    Column("comment_count", BigInteger),
    Column("recommend_index", Numeric(20, 4)),
    Column("rating_count", BigInteger),
    Column("expectation_index", Numeric(20, 4)),
    Column("search_index", Numeric(20, 4)),
    Column("max_heat", Numeric(20, 4)),
    Column("source_record_json", JSONB, nullable=False),
    Column("validation_warnings_json", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    CheckConstraint("rank > 0", name="ck_rank_entries_rank_positive"),
    UniqueConstraint("snapshot_id", "section_name", "ranking_name", "rank", name="uq_rank_entries_board_rank"),
)
```

增加索引 `ix_rank_snapshots_platform_date_captured`、`ix_rank_entries_snapshot_id`、`ix_rank_entries_board`、`ix_rank_entries_series_key`。`heat`、`max_heat` 使用 `Numeric`，因为现有红果数据包含 `88.5` 这类小数热度；计数列在关系投影时只接受整数值，原始数值始终在 JSONB 中保留。

- [ ] **步骤 4：实现 Alembic migration**

创建 revision `0016_rank_storage`，`down_revision = "0015_llm_settings"`。`upgrade()` 使用 `op.create_table()` 和 `op.create_index()` 创建相同对象；外键显式命名 `fk_rank_entries_snapshot_id`。`downgrade()` 先删除条目索引和 `rank_entries`，再删除快照索引和 `rank_snapshots`。不得修改 `0001` 至 `0015` 的历史 migration。

- [ ] **步骤 5：运行 migration 测试**

运行：

```powershell
uv run pytest tests/test_system_database_alembic.py -k "central_rank or upgrade_head" -q
```

预期：配置了测试 PostgreSQL 时 PASS；未配置时 metadata 测试 PASS、数据库用例 SKIP。

- [ ] **步骤 6：提交 schema**

```powershell
git add system_database/schema.py system_database/alembic/versions/0016_rank_storage.py tests/test_system_database_alembic.py
git commit -m "feat: add centralized rank schema"
```

---

### 任务 3：实现事务化 PostgreSQL RankRepository

**文件：**
- 修改：`analysis/rank_models.py`
- 创建：`analysis/rank_repository.py`
- 创建：`tests/test_rank_repository.py`

- [ ] **步骤 1：编写失败的 Repository 集成测试**

测试使用 `AIWORKER_TEST_SYSTEM_DATABASE_URL`，拒绝数据库名 `aiworker` 和 `aiworker_system`。迁移到 head 后写入一个快照并覆盖四类行为：

```python
result = repository.store_snapshot(
    snapshot,
    source_kind="douyin_json",
    source_ref="data/dy/jobs/job-1/rank/douyin_series_rank_20260901_100000.json",
    source_checksum="a" * 64,
    parser_version=1,
)
assert result.action == "imported"
assert repository.list_snapshots("dy")[0].records == snapshot.records

same = repository.store_snapshot(
    snapshot,
    source_kind="douyin_json",
    source_ref="data/dy/jobs/job-1/rank/douyin_series_rank_20260901_100000.json",
    source_checksum="a" * 64,
    parser_version=1,
)
assert same.action == "skipped"
assert snapshot_entry_counts(database) == (1, len(snapshot.records))
```

再增加测试：同一 `source_ref` 新 checksum 返回 `updated` 并完整替换条目；故意插入重复榜单排名触发约束后快照与条目同时回滚；新增、更新、删除后 `revision("dy")` 均变化；`revision("hg")` 不受抖音写入影响。

- [ ] **步骤 2：运行 Repository 测试确认失败**

运行：

```powershell
uv run pytest tests/test_rank_repository.py -q
```

预期：FAIL，无法导入 `analysis.rank_repository`。

- [ ] **步骤 3：增加写入结果和数据库异常类型**

在 `analysis/rank_models.py` 增加：

```python
from typing import Literal


RankWriteAction = Literal["imported", "updated", "skipped"]


@dataclass(frozen=True)
class RankWriteResult:
    action: RankWriteAction
    snapshot_id: int
    record_count: int


class RankDatabaseUnavailable(RuntimeError):
    pass
```

- [ ] **步骤 4：实现 Repository 事务和反序列化**

`RankRepository` 接口固定为：

```python
class RankRepository:
    def __init__(self, database: SystemDatabase) -> None:
        self.database = database

    def store_snapshot(
        self,
        snapshot: RankSnapshot,
        *,
        source_kind: str,
        source_ref: str,
        source_checksum: str,
        parser_version: int,
    ) -> RankWriteResult:
        with self.database.transaction() as connection:
            return self._store_in_transaction(
                connection,
                snapshot,
                source_kind,
                source_ref,
                source_checksum,
                parser_version,
            )

    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        return self._load_snapshots(platform)

    def revision(self, platform: str) -> str:
        return self._revision_digest(platform)
```

写入顺序为：按 `(platform, source_checksum)` 查询内容重复项；按 `(platform, source_kind, source_ref)` 查询来源项；相同来源和 checksum 返回 `skipped`；来源相同但内容变化时先删除旧条目、更新快照、批量插入新条目；新来源执行插入。每条 `source_record_json` 保存完整标准化 record，关系字段从同一 record 投影：`section_name = board_section or tab or category_label`，`ranking_name = rank_list or category_label or group`，`series_key = series_id or record_id or id`。Repository 还原快照时使用 `Path(source_ref)` 作为 `source_path`，以保持现有 API 字段。

`list_snapshots()` 一次查询快照、一次查询全部关联条目，在 Python 按 `snapshot_id` 组装，避免 N+1；结果按 `(captured_at, source_ref)` 排序。JSONB 中的 `Decimal` 值在返回领域对象时转为 `int` 或 `float`，保持现有 API JSON 可序列化。

`revision()` 查询指定平台的 `(id, source_checksum, imported_at)`，按 `id` 排序后使用紧凑 JSON 计算 SHA-256。捕获 `OperationalError`、`DBAPIError` 等连接层 `SQLAlchemyError` 并抛出 `RankDatabaseUnavailable("榜单数据库暂不可用")`；数据约束错误保持原异常，使导入报告能够指出具体文件。

- [ ] **步骤 5：运行 Repository 测试**

运行：

```powershell
uv run pytest tests/test_rank_repository.py -q
```

预期：测试 PostgreSQL 已配置时全部 PASS；未配置时整文件 SKIP，并在真实回填前补跑。

- [ ] **步骤 6：提交 Repository**

```powershell
git add analysis/rank_models.py analysis/rank_repository.py tests/test_rank_repository.py
git commit -m "feat: add postgres rank repository"
```

---

### 任务 4：实现幂等导入器和历史回填命令

**文件：**
- 创建：`analysis/rank_importer.py`
- 创建：`scripts/migrate_rank_files_to_postgres.py`
- 创建：`tests/test_rank_importer.py`

- [ ] **步骤 1：编写失败的导入器测试**

使用真实临时文件和记录调用的 fake repository，覆盖来源分类、相对标识、checksum、日期过滤和批量失败隔离：

```python
result = importer.import_path(
    "dy",
    rank_path,
    source_kind="douyin_json",
    source_root=tmp_path,
)
assert result.status == "imported"
assert repository.calls[0]["source_ref"] == "data/dy/jobs/job-1/rank/douyin_series_rank_20260901_100000.json"
assert repository.calls[0]["source_checksum"] == hashlib.sha256(rank_path.read_bytes()).hexdigest()

report = importer.import_all(platform="all", start_date=date(2026, 9, 1), end_date=date(2026, 9, 2))
assert report.discovered == 2
assert report.failed == 1
assert report.imported == 1
assert len(report.results) == 2
```

增加命令行测试：fake importer 返回失败项时退出码为 1，JSON 输出包含 `discovered/imported/updated/skipped/failed/record_count/results`，且不含数据库 URL 或密码。

- [ ] **步骤 2：运行导入器测试确认失败**

运行：

```powershell
uv run pytest tests/test_rank_importer.py -q
```

预期：FAIL，无法导入 `analysis.rank_importer`。

- [ ] **步骤 3：实现导入结果与批量发现**

在 `analysis/rank_importer.py` 定义：

```python
@dataclass(frozen=True)
class RankImportResult:
    platform: str
    source_ref: str
    status: str
    record_count: int
    error: str = ""


@dataclass(frozen=True)
class RankImportReport:
    discovered: int
    imported: int
    updated: int
    skipped: int
    failed: int
    record_count: int
    results: list[RankImportResult]


class RankImporter:
    def __init__(
        self,
        parser: RankFileSource,
        repository: RankRepository,
        project_root: str | Path,
        hongguo_exports_root: str | Path,
    ) -> None:
        self.parser = parser
        self.repository = repository
        self.project_root = Path(project_root).resolve()
        self.hongguo_exports_root = Path(hongguo_exports_root).resolve()

    def import_path(
        self,
        platform: str,
        path: str | Path,
        *,
        source_kind: str,
        source_root: str | Path,
    ) -> RankImportResult:
        source = Path(path).resolve()
        source_ref = source.relative_to(Path(source_root).resolve()).as_posix()
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        snapshot = self.parser.parse_path(platform, source)
        write = self.repository.store_snapshot(
            snapshot,
            source_kind=source_kind,
            source_ref=source_ref,
            source_checksum=checksum,
            parser_version=1,
        )
        return RankImportResult(platform, source_ref, write.action, write.record_count)
```

`import_all()` 发现项目内 `data/dy/jobs/*/rank/douyin_series_rank_*.json` 和红果导出根目录 `*/top10_rankings.csv`。每个文件独立 `try/except`，失败结果写入异常类型和脱敏消息后继续；按实际结果汇总计数。日期从成功解析的 `snapshot.captured_at.date()` 判断，无法解析日期的文件记为失败，不静默丢弃。

- [ ] **步骤 4：实现环境装配和 Typer 命令**

提供以下工厂，确保 worker 和 CLI 使用同一条路径：

```python
def rank_importer_from_environment(
    project_root: str | Path,
    hongguo_exports_root: str | Path,
) -> tuple[RankImporter, SystemDatabase]:
    database = SystemDatabase(SystemDatabaseSettings.from_environment())
    parser = RankFileSource(
        root=project_root,
        hongguo_exports_root=hongguo_exports_root,
    )
    return (
        RankImporter(
            parser,
            RankRepository(database),
            project_root,
            hongguo_exports_root,
        ),
        database,
    )
```

`scripts/migrate_rank_files_to_postgres.py` 使用 Typer 暴露 `--project-root`、`--hongguo-exports-root`、`--platform [all|dy|hg]`、`--start-date`、`--end-date`。启动时先调用 `database.health()`，要求 reachable 且 `schema_current`；最后无论成功失败都 `database.dispose()`。打印 `json.dumps(asdict(report), ensure_ascii=False, default=str)`，`report.failed > 0` 时 `raise typer.Exit(code=1)`。

- [ ] **步骤 5：运行导入器与 CLI 测试**

运行：

```powershell
uv run pytest tests/test_rank_importer.py -q
uv run python scripts/migrate_rank_files_to_postgres.py --help
```

预期：测试 PASS；帮助信息列出全部参数且不连接生产数据库。

- [ ] **步骤 6：提交导入器和命令**

```powershell
git add analysis/rank_importer.py scripts/migrate_rank_files_to_postgres.py tests/test_rank_importer.py
git commit -m "feat: add idempotent rank importer"
```

---

### 任务 5：让榜单 API 从 PostgreSQL 读取

**文件：**
- 修改：`analysis/today_rank_service.py:165-475`
- 修改：`analysis/today_rank_api.py`
- 创建：`tests/test_today_rank_postgres_api.py`
- 测试：`tests/test_today_rank_phase.py`

- [ ] **步骤 1：编写失败的 service/source 和 API 503 测试**

使用内存 fake source 证明 service 不扫描 root，并用抛出 `RankDatabaseUnavailable` 的 service 验证 API：

```python
source = FakeRankSource([old_snapshot, current_snapshot], revision="db-v1")
service = TodayRankService(root=tmp_path, snapshot_source=source)
response = service.get_today_rank("dy")
assert response["date"] == "2026-09-02"
assert source.list_calls == ["dy"]
assert source.revision_calls == ["dy"]

app = FastAPI()
app.include_router(build_today_rank_router(service=UnavailableRankService()))
api_response = TestClient(app).get("/api/rank/today?platform=dy")
assert api_response.status_code == 503
assert api_response.json() == {"detail": "榜单数据库暂不可用"}
```

再增加缓存测试：source revision 从 `db-v1` 改为 `db-v2` 后，相同 service 返回新增快照且 `cache_status != "hit"`。

- [ ] **步骤 2：运行集中查询测试确认失败**

运行：

```powershell
uv run pytest tests/test_today_rank_postgres_api.py -q
```

预期：FAIL，`TodayRankService` 不接受 `snapshot_source` 或 router 不接受 service 注入。

- [ ] **步骤 3：给 TodayRankService 注入来源**

构造函数保留文件来源默认值供解析回归测试使用，但生产工厂必须显式使用数据库：

```python
class TodayRankService:
    def __init__(
        self,
        root: str | Path = ".",
        hongguo_exports_root: str | Path | None = None,
        cache_dir: str | Path | None = None,
        snapshot_source: RankSnapshotSource | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.cache_dir = resolve_rank_cache_dir(self.root, cache_dir)
        self.snapshot_source = snapshot_source or RankFileSource(
            root=self.root,
            hongguo_exports_root=hongguo_exports_root,
            cache_dir=self.cache_dir,
        )

    @classmethod
    def from_environment(cls, root: str | Path = ".") -> "TodayRankService":
        database = SystemDatabase(SystemDatabaseSettings.from_environment())
        return cls(root=root, snapshot_source=RankRepository(database))

    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        return self.snapshot_source.list_snapshots(platform)

    def source_revision(self, platform: str) -> str:
        return self.snapshot_source.revision(platform)
```

`get_today_rank()` 的缓存 revision 改调 `source_revision()`。删除 service 内已经迁移到 `RankFileSource` 的扫描方法和红果文件状态；响应组装、`selected_snapshot.source_path` 字段和所有比较逻辑保持不变。

- [ ] **步骤 4：生产 router 显式装配数据库并返回 503**

```python
def build_today_rank_router(
    root: str | Path = ".",
    service: TodayRankService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/rank", tags=["rank"])
    rank_service = service or TodayRankService.from_environment(root=root)

    @router.get("/today")
    async def today_rank(platform: str, date: str = "", limit: int = 10, refresh: bool = False):
        try:
            return rank_service.get_today_rank(platform, date, limit, refresh)
        except RankDatabaseUnavailable as exc:
            raise HTTPException(status_code=503, detail="榜单数据库暂不可用") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
```

保留现有 `Query` 的 `limit` 约束和命名参数调用；这里只展示异常映射和装配边界。其他未知异常仍返回 500，但响应不得包含数据库连接串。

- [ ] **步骤 5：运行 API 与完整榜单回归**

运行：

```powershell
uv run pytest tests/test_today_rank_postgres_api.py tests/test_today_rank_phase.py tests/test_today_rank_request_race_js.py -q
```

预期：PASS；文件源单元测试和数据库源 API 契约同时成立，生产 router 没有本地文件回退。

- [ ] **步骤 6：提交集中查询改造**

```powershell
git add analysis/today_rank_service.py analysis/today_rank_api.py tests/test_today_rank_postgres_api.py tests/test_today_rank_phase.py
git commit -m "feat: read rank api from postgres"
```

---

### 任务 6：让周报复用集中榜单源

**文件：**
- 修改：`analysis/weekly_report_service.py:175-320`
- 修改：`analysis/analytics_api.py:33-48`
- 修改：`agents/weekly_report_worker.py:19-25`
- 修改：`tests/test_weekly_report_service.py`
- 修改：`tests/test_weekly_report_api.py`

- [ ] **步骤 1：编写失败的周报版本测试**

向 `WeeklyReportService` 注入使用 fake source 的 `TodayRankService`，根目录不创建任何榜单文件：

```python
rank_source = FakeRankSource(complete_week_snapshots, revision="postgres-revision-1")
rank_service = TodayRankService(root=tmp_path, snapshot_source=rank_source)
service = WeeklyReportService(
    root=tmp_path,
    runtime_dir=tmp_path / ".runtime",
    rank_service=rank_service,
)

assert service._source_revision("dy") == "postgres-revision-1"
assert service.build_latest("dy", as_of=datetime(2026, 9, 7))["status"] == "ready"
```

增加缓存失效测试：fake source revision 改为 `postgres-revision-2` 后，`generate_all_available()` 重新生成而不是返回旧的 `cache_status="hit"`。

- [ ] **步骤 2：运行周报集中源测试确认失败**

运行：

```powershell
uv run pytest tests/test_weekly_report_service.py -k "postgres_revision or injected_rank_source" -q
```

预期：FAIL，`WeeklyReportService.__init__()` 不接受 `rank_service`。

- [ ] **步骤 3：注入榜单 service 并删除文件指纹扫描**

```python
class WeeklyReportService:
    def __init__(
        self,
        root: str | Path = ".",
        runtime_dir: str | Path = ".runtime",
        analysis_script_path: str | Path | None = None,
        rank_service: TodayRankService | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.rank_service = rank_service or TodayRankService(root=self.root)

    def _source_revision(self, platform: str) -> str:
        return self.rank_service.source_revision(platform)
```

删除 `_source_revision()` 中对 `data/*/jobs`、红果 exports 和 raw 目录的扫描。保留 `_fingerprint()` 对已经查询出的 `RankSnapshot` 内容与分析脚本的摘要，因为它用于单份周报内容去重。

- [ ] **步骤 4：修改两个生产装配点**

`analysis/analytics_api.py` 的 lazy factory 和 `agents/weekly_report_worker.py` 都创建 `TodayRankService.from_environment(root)`，并通过 `rank_service=` 传入 `WeeklyReportService`。测试中的直接 `WeeklyReportService(root=tmp_path)` 仍使用文件源，避免单元测试连接生产数据库。

- [ ] **步骤 5：运行周报测试**

运行：

```powershell
uv run pytest tests/test_weekly_report_service.py tests/test_weekly_report_api.py -q
```

预期：PASS，集中源用例不创建本地榜单文件，现有周报内容、缓存及导出测试保持通过。

- [ ] **步骤 6：提交周报改造**

```powershell
git add analysis/weekly_report_service.py analysis/analytics_api.py agents/weekly_report_worker.py tests/test_weekly_report_service.py tests/test_weekly_report_api.py
git commit -m "feat: source weekly reports from postgres ranks"
```

---

### 任务 7：榜单生成后自动同步 PostgreSQL

**文件：**
- 修改：`analysis/rank_importer.py`
- 修改：`agents/douyin_rank_worker.py:135-246`
- 修改：`agents/hongguo_csv_worker.py:83-161`
- 修改：`tests/test_douyin_rank_worker.py`
- 修改：`tests/test_hongguo_csv_worker.py`

- [ ] **步骤 1：编写失败的 worker 同步测试**

抖音测试记录调用顺序，确保分析产物先生成、数据库随后读取已补全记录、禅道推送最后执行：

```python
calls = []
monkeypatch.setattr(douyin_rank_worker, "analyze_rank_file", lambda *args, **kwargs: calls.append("analysis") or analysis_manifest(tmp_path))
monkeypatch.setattr(douyin_rank_worker, "sync_rank_source", lambda platform, path, **kwargs: calls.append(("sync", platform, Path(path).name)) or imported_result())
monkeypatch.setattr(douyin_rank_worker, "deliver_rank_update", lambda *args, **kwargs: calls.append("delivery") or {"status": "sent"})

assert douyin_rank_worker.run_job(job) == 0
assert calls == ["analysis", ("sync", "dy", "douyin_series_rank_20260902_100000.json"), "delivery"]
```

红果测试断言最近两份 CSV 都同步，但只有最新一份禅道推送。两个 worker 都增加失败用例：`sync_rank_source` 抛出 `RankDatabaseUnavailable` 后返回 1、发出 `job_failed` 且原始文件仍存在。

- [ ] **步骤 2：运行 worker 同步测试确认失败**

运行：

```powershell
uv run pytest tests/test_douyin_rank_worker.py tests/test_hongguo_csv_worker.py -k "sync or central" -q
```

预期：FAIL，worker 模块没有 `sync_rank_source`。

- [ ] **步骤 3：增加单文件环境同步 helper**

在 `analysis/rank_importer.py` 增加：

```python
def sync_rank_source(
    platform: str,
    path: str | Path,
    *,
    project_root: str | Path,
    hongguo_exports_root: str | Path,
) -> RankImportResult:
    importer, database = rank_importer_from_environment(
        project_root, hongguo_exports_root
    )
    try:
        if platform == "dy":
            return importer.import_path(
                "dy", path, source_kind="douyin_json", source_root=project_root
            )
        return importer.import_path(
            "hg", path, source_kind="hongguo_csv", source_root=hongguo_exports_root
        )
    finally:
        database.dispose()
```

- [ ] **步骤 4：接入抖音 worker**

在 `_run_auto_analysis()` 完成后、`deliver_rank_update()` 前调用 `sync_rank_source("dy", json_path, project_root=Path.cwd(), hongguo_exports_root=DEFAULT_HONGGUO_EXPORTS_ROOT)`。成功事件 payload 包含 `status/source_ref/record_count`。失败时发出：

```python
emit_event(
    "job_failed",
    job_id=job.job_id,
    worker_id=worker_id,
    platform="dy",
    level="error",
    message="抖音榜单已生成，但集中数据库同步失败",
    payload={"sync_status": "central_sync_failed", "error_type": type(exc).__name__},
)
return 1
```

不得删除 JSON/Markdown 或已生成分析文件，也不得在失败日志中输出连接串。

- [ ] **步骤 5：接入红果 worker**

每个 `selected_paths` 中的 CSV 在 `write_snapshot()` 成功后调用 `sync_rank_source("hg", csv_path, project_root=Path.cwd(), hongguo_exports_root=exports_root)`。任一同步失败时记录 `central_sync_failed` 并返回 1；已写出的红果转换 JSON 和原 CSV 保留。全部同步成功后才发送 `job_finished`，禅道仍只推送 `selected_paths[-1]`。

- [ ] **步骤 6：运行完整 worker 测试**

运行：

```powershell
uv run pytest tests/test_douyin_rank_worker.py tests/test_hongguo_csv_worker.py -q
```

预期：PASS；旧用例通过 monkeypatch 的成功同步 helper 不连接数据库，新用例覆盖失败状态与文件保留。

- [ ] **步骤 7：提交自动同步**

```powershell
git add analysis/rank_importer.py agents/douyin_rank_worker.py agents/hongguo_csv_worker.py tests/test_douyin_rank_worker.py tests/test_hongguo_csv_worker.py
git commit -m "feat: sync generated ranks to postgres"
```

---

### 任务 8：完整回归、运行手册和生产回填

**文件：**
- 创建：`docs/runbooks/centralized-rank-storage.md`
- 验证：本计划列出的全部代码和测试文件

- [ ] **步骤 1：编写操作手册**

手册必须包含以下无密码命令，并明确每步的预期证据：

```powershell
uv run alembic current
uv run alembic heads
uv run alembic upgrade head
uv run python scripts/migrate_rank_files_to_postgres.py --project-root D:\Crawler\AiWorker --hongguo-exports-root E:\hg\exports --platform all
```

备份命令使用 `pg_dump -h 192.168.1.34 -U aiworker_app -d aiworker -Fc -f E:\postgresql\backups\aiworker-before-rank-migration-20260902.dump`，密码通过交互输入或受控环境提供，不写入手册。手册还要给出：按平台统计快照/条目和日期范围的只读 SQL、检查 Alembic revision 的 SQL、`.34` 与 `.100` 调用相同 `/api/rank/today` 的 PowerShell 命令、自动生成新榜单后的核验 SQL、停止自动任务与恢复原代码版本的回滚步骤。

- [ ] **步骤 2：运行针对性测试**

```powershell
uv run pytest tests/test_rank_file_source.py tests/test_rank_repository.py tests/test_rank_importer.py tests/test_today_rank_postgres_api.py tests/test_today_rank_phase.py tests/test_weekly_report_service.py tests/test_weekly_report_api.py tests/test_douyin_rank_worker.py tests/test_hongguo_csv_worker.py tests/test_system_database_alembic.py -q
```

预期：全部可运行用例 PASS；PostgreSQL 用例只有在未配置测试库时允许 SKIP。真实迁移前必须配置测试库重新运行，确保 Repository 与 migration 用例实际 PASS。

- [ ] **步骤 3：运行项目回归和文件头检查**

```powershell
uv run pytest tests/ -q
uv run pre-commit run --all-files
```

预期：PASS。若仓库原有无关失败，记录失败命令、文件和现有基线证据，不通过修改无关业务代码掩盖。

- [ ] **步骤 4：提交运行手册**

```powershell
git add docs/runbooks/centralized-rank-storage.md
git commit -m "docs: add centralized rank migration runbook"
```

- [ ] **步骤 5：确认部署版本和生产数据库备份**

在 `.34` 与 `.100` 分别记录 `git rev-parse HEAD`，两端必须是同一个已审核实现提交。执行自定义格式 `pg_dump` 到 `E:\postgresql\backups`，随后用 `Get-Item` 确认备份文件存在且大小大于零。记录生产 `uv run alembic current` 输出；若仍是 `0012_writer_stage_edit`，升级必须连续经过 `0013`、`0014`、`0015`、`0016`，不得 stamp。

- [ ] **步骤 6：升级生产 schema 并回填历史榜单**

在 `.34` 使用已配置的 `AIWORKER_SYSTEM_DATABASE_URL` 执行：

```powershell
uv run alembic upgrade head
uv run alembic current
uv run python scripts/migrate_rank_files_to_postgres.py --project-root D:\Crawler\AiWorker --hongguo-exports-root E:\hg\exports --platform all
```

预期：current 为 `0016_rank_storage (head)`；回填报告 `failed=0`；`discovered` 等于执行时实际发现的抖音 JSON 与红果 CSV 数量。立即重复执行同一回填命令，预期所有已导入来源均为 `skipped`，数据库快照数和条目数不增加。

- [ ] **步骤 7：执行数据库和双机验收**

通过只读 SQL核对每个平台的快照数、条目数、最早/最晚日期、`record_count` 与实际条目数差异均为零。分别从 `.34`、`.100` 请求 `dy`、`hg` 的最新日期和一个历史日期；对响应去除 `cache_status` 后计算 JSON 摘要，预期两台机器完全一致。

启动一份新的抖音或红果榜单任务，预期：worker 成功、数据库新增或更新一个快照、`.100` 强制刷新后看到新日期、原始文件仍留在 `.34`。最后执行一次周报任务，预期不读取 `.100` 的本地榜单目录且能生成集中数据对应的周报。

- [ ] **步骤 8：记录最终证据**

在实施交付说明中记录：实现提交、两端部署提交、备份路径与大小、升级前后 revision、回填报告摘要、第二次幂等回填摘要、两平台数据库统计、双机 API 摘要和新增榜单自动同步结果。不得记录数据库密码或包含密码的 URL。
