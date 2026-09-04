# 榜单仅从 PostgreSQL 读取与缺失提醒实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让所有生产榜单消费者仅从 PostgreSQL 读取，并在最新视图缺少当天数据库快照时弹出“请先抓取”提醒。

**架构：** `TodayRankService` 的数据源改为必须显式选择：生产工厂注入 `RankRepository`，文件工厂注入 `RankFileSource`。PostgreSQL 模式禁用磁盘响应缓存，只保留每次经数据库 revision 校验的进程内缓存；所有生产入口使用 PostgreSQL 工厂。前端依据 API 的 `business_date` 和 `has_today_snapshot` 显示去重 warning Toast。

**技术栈：** Python 3.11、FastAPI、SQLAlchemy、PostgreSQL、Alpine.js、Bootstrap Toast、pytest、Node.js

---

## 状态字段结论与实施约束

- `rank_entries.status` 保持来源内容属性语义，例如来源将来提供的“连载中/已完结”；不得写入“新上榜/下榜/上升/下降”。
- “新上榜/下榜”由相邻快照的 `series_key` 集合差计算，“上升/下降”由相同分组下的 `rank` 差计算；`previous_rank`、`rank_delta` 和榜单变动状态继续只在查询期生成。
- 不新增 Alembic migration，也不批量更新现有 `status IS NULL` 记录。抖音和红果源记录没有内容状态时，数据库保持 `NULL` 是合法结果。
- PostgreSQL-only 改造必须增加回归测试：即使两期 `rank_entries.status` 都是 `NULL`，API 仍能根据数据库快照产生新上榜和下榜集合。
- 同一天存在多次抓取时，维持当前“按 `captured_at` 选择最近一期”的比较规则；本任务不改成固定的日首快照或跨自然日比较。

## 文件结构

- 修改 `analysis/today_rank_service.py`：显式区分 PostgreSQL 与文件数据源，控制磁盘响应缓存。
- 修改 `WebGUI.py`：WebGUI 的共享榜单服务使用 PostgreSQL 工厂。
- 修改 `analysis/weekly_report_service.py`：周报默认服务使用 PostgreSQL 工厂。
- 修改 `creative/topic_service.py`：选题默认使用 PostgreSQL，并保留显式依赖注入。
- 修改 `analysis/rank_excel_export.py`：Excel 默认从 PostgreSQL 读取，并允许测试显式注入文件服务。
- 修改 `static/js/selection/ranks.js`：判断当天快照缺失并触发 Toast。
- 修改 `static/js/common/tasks.js`：保存弹窗去重状态。
- 修改 `templates/common/workspace/selection_ranks.html`：把本地数据提示改为 PostgreSQL 提示。
- 创建 `tests/test_rank_source_policy.py`：验证生产数据源和磁盘缓存边界。
- 修改 `tests/test_today_rank_phase.py`、`tests/test_today_rank_daily_status.py`：显式使用文件工厂测试解析逻辑。
- 修改 `tests/test_weekly_report_service.py`、`tests/test_weekly_report_api.py`：显式注入文件榜单服务。
- 修改 `tests/test_creative_phase3.py`、`tests/test_creative_lifecycle.py`：显式注入榜单服务。
- 修改 `tests/test_rank_excel_export.py`：显式注入文件榜单服务，并验证默认生产工厂的输出。
- 修改 `tests/test_today_rank_job_creation.py`：验证 WebGUI 共享榜单服务使用 PostgreSQL repository。
- 修改 `tests/test_today_rank_request_race_js.py`：执行真实 JavaScript，验证提醒、去重和并发边界。
- 修改 `tests/test_webgui_ui_changes.py`：验证页面初始状态与 PostgreSQL 提示文案。
- 修改 `docs/runbooks/centralized-rank-storage.md`：补充重启和当天缺失验收。

### 任务 1：显式隔离 PostgreSQL 与本地文件数据源

**文件：**
- 创建：`tests/test_rank_source_policy.py`
- 修改：`analysis/today_rank_service.py:156-455,889-933`
- 修改：`tests/test_today_rank_phase.py`
- 修改：`tests/test_today_rank_daily_status.py`

- [ ] **步骤 1：编写失败的生产数据源测试**

点名要抓住的破坏：`TodayRankService` 再次在未指定数据源时自动选择 `RankFileSource`，或者 PostgreSQL 模式读取磁盘响应缓存。

```python
def test_default_service_uses_postgres_repository_and_disables_disk_cache(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "AIWORKER_SYSTEM_DATABASE_URL",
        "postgresql+psycopg://rank_user:not-a-secret@127.0.0.1:55432/rank_test",
    )

    service = TodayRankService(root=tmp_path)

    assert isinstance(service.snapshot_source, RankRepository)
    assert service.disk_response_cache_enabled is False
    service._owned_database.dispose()


def test_postgres_mode_ignores_matching_local_response_cache(tmp_path):
    source = StaticSnapshotSource([_snapshot("数据库榜单")], revision="same")
    cache = tmp_path / ".runtime/rank-cache/douyin-latest-limit-10.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({
        "source_revision": "same",
        "response": {"platform": "dy", "date": "2026-09-03", "current_entries": [{"title": "本地缓存榜单"}]},
    }), encoding="utf-8")

    service = TodayRankService(root=tmp_path, snapshot_source=source)
    response = service.get_today_rank("dy")

    assert response["current_entries"][0]["title"] == "数据库榜单"
```

同一测试文件先定义完整的内存数据源和快照 fixture，避免测试依赖本地文件：

```python
class StaticSnapshotSource:
    def __init__(self, snapshots: list[RankSnapshot], revision: str = "db-v1"):
        self.snapshots = snapshots
        self.current_revision = revision

    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        return [item for item in self.snapshots if item.platform == platform]

    def revision(self, platform: str) -> str:
        return self.current_revision


def _snapshot(title: str) -> RankSnapshot:
    return RankSnapshot(
        platform="dy",
        job_id="db-job",
        source_path=Path("database/dy.json"),
        captured_at=datetime(2026, 9, 3, 10, 0),
        records=[{
            "id": "series-1", "record_id": "series-1", "title": title,
            "rank": 1, "tab": "榜单", "group": "短剧 / 榜单",
            "validation_warnings": [],
        }],
        validation={"status": "passed", "has_warnings": False},
    )
```

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：

```powershell
uv run pytest tests/test_rank_source_policy.py -v
```

预期：第一个测试发现默认服务仍是 `RankFileSource`，第二个测试读到“本地缓存榜单”。

- [ ] **步骤 3：实现显式数据源和缓存策略**

在 `TodayRankService` 中使用以下接口：

```python
class TodayRankService(_LegacyRankEngine):
    def __init__(
        self,
        root: str | Path = ".",
        hongguo_exports_root: str | Path | None = None,
        cache_dir: str | Path | None = None,
        snapshot_source: RankSnapshotSource | None = None,
        *,
        disk_response_cache_enabled: bool = False,
    ) -> None:
        super().__init__(root, hongguo_exports_root, cache_dir)
        self.disk_response_cache_enabled = disk_response_cache_enabled
        if snapshot_source is None:
            from analysis.rank_repository import RankRepository
            from system_database import SystemDatabase, SystemDatabaseSettings

            database = SystemDatabase(SystemDatabaseSettings.from_environment())
            snapshot_source = RankRepository(database)
            self._owned_database = database
        self.snapshot_source = snapshot_source

    @classmethod
    def from_files(cls, root=".", hongguo_exports_root=None, cache_dir=None):
        from analysis.rank_file_source import RankFileSource

        source = RankFileSource(root, hongguo_exports_root, cache_dir)
        return cls(
            root=root,
            hongguo_exports_root=hongguo_exports_root,
            cache_dir=cache_dir,
            snapshot_source=source,
            disk_response_cache_enabled=True,
        )
```

`from_environment()` 继续显式注入 `RankRepository` 并传入 `disk_response_cache_enabled=False`。`_read_response_cache()` 在该标志为假时直接返回 `None`，`_write_response_cache()` 在该标志为假时直接返回。

- [ ] **步骤 4：把文件解析测试改为显式文件工厂**

在 `tests/test_today_rank_phase.py` 和 `tests/test_today_rank_daily_status.py` 中，把文件 fixture 对应的：

```python
TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=cache_dir)
```

改为：

```python
TodayRankService.from_files(
    root=tmp_path,
    hongguo_exports_root=exports,
    cache_dir=cache_dir,
)
```

保留显式 `snapshot_source=` 的数据库/不可用源测试，不改成文件工厂。

- [ ] **步骤 5：运行任务 1 测试并确认通过**

```powershell
uv run pytest tests/test_rank_source_policy.py tests/test_today_rank_phase.py tests/test_today_rank_daily_status.py tests/test_today_rank_postgres_api.py -v
```

预期：全部通过；需要专用 PostgreSQL 的用例在没有 `AIWORKER_TEST_SYSTEM_DATABASE_URL` 时保持现有 skip 行为。

- [ ] **步骤 6：提交任务 1**

```powershell
git add analysis/today_rank_service.py tests/test_rank_source_policy.py tests/test_today_rank_phase.py tests/test_today_rank_daily_status.py
git commit -m "refactor: enforce explicit rank data sources"
```

### 任务 2：把全部生产榜单消费者切换到 PostgreSQL

**文件：**
- 修改：`WebGUI.py:478`
- 修改：`analysis/weekly_report_service.py:177-190`
- 修改：`creative/topic_service.py:27-34`
- 修改：`analysis/rank_excel_export.py:48-61`
- 修改：`tests/test_weekly_report_service.py`
- 修改：`tests/test_weekly_report_api.py`
- 修改：`tests/test_creative_phase3.py`
- 修改：`tests/test_creative_lifecycle.py`
- 修改：`tests/test_rank_excel_export.py`
- 修改：`tests/test_today_rank_postgres_api.py`
- 修改：`tests/test_today_rank_job_creation.py`

- [ ] **步骤 1：编写失败的生产消费者测试**

点名要抓住的破坏：WebGUI、周报、选题或 Excel 再次直接构造文件榜单服务。

在 `tests/test_rank_source_policy.py` 中增加：

```python
def test_default_weekly_and_topic_services_use_postgres(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AIWORKER_SYSTEM_DATABASE_URL",
        "postgresql+psycopg://rank_user:not-a-secret@127.0.0.1:55432/rank_test",
    )
    weekly = WeeklyReportService(root=tmp_path)
    topics = TopicService(root=str(tmp_path))

    assert isinstance(weekly.rank_service.snapshot_source, RankRepository)
    assert isinstance(topics.rank_service.snapshot_source, RankRepository)
    weekly.rank_service._owned_database.dispose()
    topics.rank_service._owned_database.dispose()
```

在 `tests/test_today_rank_job_creation.py` 增加 WebGUI 实例契约：

```python
def test_webgui_shared_rank_service_uses_postgres_repository():
    assert isinstance(WebGUI.today_rank_service.snapshot_source, RankRepository)
    assert WebGUI.today_rank_service.disk_response_cache_enabled is False
```

在 `tests/test_rank_excel_export.py` 增加一个完整输出测试：把 `TodayRankService.from_environment` 的外部数据库边界替换为一个显式文件服务，执行真实 `build_rank_workbook()`，并断言工作表中的标题来自该服务。该测试不对替身调用次数做断言。

在 `tests/test_today_rank_postgres_api.py` 增加跨快照状态语义测试。向专用测试数据库写入两期都不含 `status` 的抖音快照：上一期包含 `series-a/series-b`，当前期包含 `series-a/series-c`。

```python
def test_postgres_snapshots_derive_new_and_dropped_without_persisted_status(
    tmp_path: Path,
    database: SystemDatabase,
):
    repository = RankRepository(database)
    for captured_at, checksum, records in (
        (
            datetime(2026, 9, 2, 10, 0),
            "a" * 64,
            [
                {"id": "series-a", "title": "持续在榜剧", "rank": 1, "tab": "榜单"},
                {"id": "series-b", "title": "下榜剧", "rank": 2, "tab": "榜单"},
            ],
        ),
        (
            datetime(2026, 9, 3, 10, 0),
            "b" * 64,
            [
                {"id": "series-a", "title": "持续在榜剧", "rank": 1, "tab": "榜单"},
                {"id": "series-c", "title": "新上榜剧", "rank": 2, "tab": "榜单"},
            ],
        ),
    ):
        repository.store_snapshot(
            RankSnapshot(
                platform="dy",
                job_id=f"job-{captured_at:%Y%m%d}",
                source_path=Path(f"database/{captured_at:%Y%m%d}.json"),
                captured_at=captured_at,
                records=records,
                validation={"status": "passed", "has_warnings": False},
            ),
            source_kind="douyin_json",
            source_ref=f"database/{captured_at:%Y%m%d}.json",
            source_checksum=checksum,
            parser_version=1,
        )

    app = FastAPI()
    app.include_router(build_today_rank_router(
        root=tmp_path,
        service=TodayRankService(root=tmp_path, snapshot_source=repository),
    ))
    payload = TestClient(app).get("/api/rank/today?platform=dy").json()

    assert payload["summary"]["new_count"] == 1
    assert payload["summary"]["dropped_count"] == 1
    assert payload["new_entries"][0]["title"] == "新上榜剧"
    assert payload["dropped_entries"][0]["title"] == "下榜剧"

    with database.connection() as connection:
        persisted = connection.execute(
            select(rank_entries.c.status)
        ).scalars().all()
    assert persisted == [None, None, None, None]
```

再直接查询这两期的 `rank_entries.status`，断言它们保持 `NULL`。该测试抓住的破坏是：实现错误地依赖持久化 `status` 才能计算榜单变化，或把榜单变动状态污染到来源内容字段。

- [ ] **步骤 2：运行新增测试并确认失败**

```powershell
uv run pytest tests/test_rank_source_policy.py tests/test_rank_excel_export.py tests/test_today_rank_job_creation.py -v
```

预期：周报或选题仍使用旧的直接构造路径，Excel 还没有可替换的生产工厂边界。

- [ ] **步骤 3：修改生产入口**

使用明确的生产工厂：

```python
today_rank_service = TodayRankService.from_environment(root=WORKSPACE_ROOT)
```

```python
self.rank_service = rank_service or TodayRankService.from_environment(
    root=self.root
)
```

`TopicService` 接受可选注入：

```python
def __init__(
    self,
    root: str,
    rank_service: TodayRankService | None = None,
) -> None:
    self.rank_service = rank_service or TodayRankService.from_environment(root=root)
```

`build_rank_workbook` 接受 `rank_service`，生产默认值来自 PostgreSQL：

```python
service = rank_service or TodayRankService.from_environment(root=root)
```

不得捕获数据库异常后回退到 `TodayRankService.from_files()`。

- [ ] **步骤 4：更新文件 fixture 测试的依赖注入**

- 周报测试统一传入 `rank_service=TodayRankService.from_files(root=tmp_path)`。
- 选题测试在构造函数传入现有 fake，而不是构造后改写属性。
- Excel 测试传入 `rank_service=TodayRankService.from_files(...)`。
- WebGUI 的 PostgreSQL API 测试在本地目录放置更新日期文件，并确认响应仍只返回数据库快照。
- PostgreSQL 状态语义测试保持 `status IS NULL`，同时确认新上榜和下榜集合由两期数据库记录动态生成。

- [ ] **步骤 5：运行生产消费者测试**

```powershell
uv run pytest tests/test_rank_source_policy.py tests/test_rank_excel_export.py tests/test_weekly_report_service.py tests/test_weekly_report_api.py tests/test_creative_phase3.py tests/test_creative_lifecycle.py tests/test_today_rank_postgres_api.py tests/test_today_rank_job_creation.py -v
```

预期：全部通过或仅保留未配置专用 PostgreSQL 时的既有 skip。

- [ ] **步骤 6：提交任务 2**

```powershell
git add WebGUI.py analysis/weekly_report_service.py creative/topic_service.py analysis/rank_excel_export.py tests/test_rank_source_policy.py tests/test_rank_excel_export.py tests/test_weekly_report_service.py tests/test_weekly_report_api.py tests/test_creative_phase3.py tests/test_creative_lifecycle.py tests/test_today_rank_postgres_api.py tests/test_today_rank_job_creation.py
git commit -m "fix: read production ranks only from postgres"
```

### 任务 3：缺少当天数据库快照时弹窗提醒

**文件：**
- 修改：`static/js/selection/ranks.js:3-105`
- 修改：`static/js/common/tasks.js:207-225`
- 修改：`templates/common/workspace/selection_ranks.html:4-11`
- 修改：`tests/test_today_rank_request_race_js.py`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写失败的真实 JavaScript 行为测试**

点名要抓住的破坏：最新请求缺少当天快照却不提醒、历史请求误提醒、同日重复提醒或过期并发响应提醒。

在 Node 测试中加载真实 `ranks.js`，提供完整响应和真实状态对象：

```javascript
const toasts = [];
global.window.aiworkerUi = {showToast(payload) { toasts.push(payload); }};
const state = {
  ...methods,
  todayRankPlatform: 'hg', todayRankDate: '',
  todayRankMissingNoticeKeys: {},
};
const missing = {
  platform: 'hg', date: '2026-09-02',
  business_date: '2026-09-03', has_today_snapshot: false,
};

methods.notifyMissingTodayRank.call(state, missing, 'hg', '');
methods.notifyMissingTodayRank.call(state, missing, 'hg', '');

if (toasts.length !== 1) throw new Error('missing-today toast was not deduplicated');
if (toasts[0].kind !== 'warning' || !toasts[0].message.includes('请先抓取')) {
  throw new Error('missing-today toast content is invalid');
}
```

同一测试再传入 `requestedDate='2026-09-01'` 和 `has_today_snapshot=true`，断言 Toast 数量不增加。并发测试让过期响应完成，断言它没有触发提醒。

- [ ] **步骤 2：运行前端测试并确认失败**

```powershell
uv run pytest tests/test_today_rank_request_race_js.py tests/test_webgui_ui_changes.py -v
```

预期：`notifyMissingTodayRank` 尚不存在，模板仍显示“本地最新榜单快照”。

- [ ] **步骤 3：实现 warning Toast 和去重状态**

在 `ranks.js` 增加：

```javascript
notifyMissingTodayRank(data, requestedPlatform, requestedDate) {
    if (requestedDate || data?.has_today_snapshot !== false || !data?.business_date) return;
    const key = `${requestedPlatform}:${data.business_date}`;
    if (this.todayRankMissingNoticeKeys?.[key]) return;
    this.todayRankMissingNoticeKeys = {
        ...(this.todayRankMissingNoticeKeys || {}),
        [key]: true,
    };
    const platformName = requestedPlatform === 'hg' ? '红果' : '抖音';
    window.aiworkerUi?.showToast({
        kind: 'warning',
        title: '今日榜单尚未入库',
        message: `${platformName}今日榜单暂无 PostgreSQL 数据，请先抓取。`,
    });
}
```

只在缓存响应或网络响应已经确认仍对应当前选择后调用。`tasks.js` 初始状态增加 `todayRankMissingNoticeKeys: {}`。模板文案改为“默认展示 PostgreSQL 最新榜单快照”。

- [ ] **步骤 4：运行前端测试并确认通过**

```powershell
uv run pytest tests/test_today_rank_request_race_js.py tests/test_webgui_ui_changes.py -v
```

预期：全部通过，Node 进程退出码为 0。

- [ ] **步骤 5：提交任务 3**

```powershell
git add static/js/selection/ranks.js static/js/common/tasks.js templates/common/workspace/selection_ranks.html tests/test_today_rank_request_race_js.py tests/test_webgui_ui_changes.py
git commit -m "feat: warn when today's postgres rank is missing"
```

### 任务 4：运行手册与完整验收

**文件：**
- 修改：`docs/runbooks/centralized-rank-storage.md`

- [ ] **步骤 1：补充发布与验收说明**

记录以下可执行检查：部署后必须重启 WebGUI；本地新增 JSON/CSV 不得改变 API 结果；无当天数据库快照时页面显示提醒；PostgreSQL 连接异常时 API 返回 503；2026-09-03 红果补录属于独立数据操作。

同时记录 `rank_entries.status` 的来源内容属性语义，以及“新上榜/下榜/排名变化”由 PostgreSQL 快照动态计算、不要求历史记录回填该字段。

- [ ] **步骤 2：运行集中榜单回归测试**

```powershell
uv run pytest tests/test_rank_source_policy.py tests/test_rank_file_source.py tests/test_rank_importer.py tests/test_rank_repository.py tests/test_today_rank_phase.py tests/test_today_rank_daily_status.py tests/test_today_rank_postgres_api.py tests/test_today_rank_request_race_js.py tests/test_today_rank_job_creation.py tests/test_rank_excel_export.py tests/test_weekly_report_service.py tests/test_weekly_report_api.py tests/test_weekly_report_worker.py tests/test_creative_phase3.py tests/test_creative_lifecycle.py tests/test_webgui_ui_changes.py -v
```

预期：退出码为 0；未设置测试数据库导致的 skip 单独报告，不冒充通过的 PostgreSQL 集成验证。

- [ ] **步骤 3：运行格式与文件头检查**

```powershell
uv run pre-commit run --all-files
git diff --check
```

预期：退出码均为 0。

- [ ] **步骤 4：使用当前生产 PostgreSQL 做只读验收**

通过 `TodayRankService.from_environment()` 分别读取 `dy`、`hg`，只输出平台、返回日期、`business_date`、`has_today_snapshot` 和记录数，不输出连接串或密码。预期结果完全由 `rank_snapshots`、`rank_entries` 决定；本地文件较新时也不能覆盖数据库日期。

- [ ] **步骤 5：提交任务 4**

```powershell
git add docs/runbooks/centralized-rank-storage.md
git commit -m "docs: document postgres-only rank verification"
```

- [ ] **步骤 6：最终检查**

```powershell
git status --short --branch
git log -5 --oneline
```

预期：工作树干净，分支包含设计、数据源边界、生产入口、缺失提醒和运行手册提交。
