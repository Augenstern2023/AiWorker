# 榜单集中式 PostgreSQL 存储设计

## 背景

今日榜单目前由 `TodayRankService` 扫描运行机器上的本地文件：

- 抖音：`data/dy/jobs/*/rank/douyin_series_rank_*.json`
- 红果：`data/hg/jobs/*/rank/hongguo*.json`
- 红果外部导出：`E:\hg\exports\*/top10_rankings.csv`

`192.168.1.34` 保存了现有榜单文件，而 `192.168.1.100` 没有这些文件，因此后者虽然能访问集中式系统数据库，却无法展示榜单。现有 PostgreSQL `aiworker` 数据库也没有榜单表。

本期将标准化后的抖音、红果榜单快照及条目集中存入 PostgreSQL，使两台机器读取同一份榜单数据。原始 JSON/CSV 继续保留在 `192.168.1.34`，本期不引入 MinIO、S3 或共享文件系统。

## 目标

1. 回填现有抖音 JSON 和红果 CSV 历史榜单。
2. 后续每次生成新榜单后自动写入 PostgreSQL。
3. WebGUI 和依赖榜单的周报通过 PostgreSQL 读取，不再依赖运行机器的本地榜单目录。
4. 保持现有日期选择、榜单分组、排名变化、在榜时长和数据校验行为。
5. 导入过程幂等、事务化、可重试，并提供可核验的导入报告。
6. 数据库不可访问时明确报错，不用可能过期的本地文件冒充集中数据。

## 非目标

- 不迁移榜单原始 JSON/CSV 文件本身。
- 不引入对象存储或网络共享目录。
- 不改造榜单页面布局、交互或视觉样式。
- 不在本期集中化下载视频、分析报告等其他业务文件。
- 不迁移 Web 会话或改动账号、权限模型。

## 方案选择

采用“PostgreSQL 关系表 + JSONB 扩展字段”方案：标准化的快照和常用条目字段使用关系列，平台差异字段和源记录使用 JSONB 保真。

没有采用以下方案：

- 整份榜单存为单条 JSONB：初期简单，但不利于分组查询、历史比较、约束和后续统计。
- 远程读取共享文件或对象存储：需要新增网络文件权限、缓存和可用性治理，也超出本期范围。

## 数据模型

### `rank_snapshots`

一条记录代表某个平台的一次榜单快照。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint identity | 主键 |
| `platform` | varchar(16) | `dy` 或 `hg` |
| `captured_at` | timestamptz | 榜单采集时间 |
| `snapshot_date` | date | 用于日期筛选的业务日期 |
| `job_id` | text nullable | 生成任务标识 |
| `source_kind` | varchar(32) | `douyin_json`、`hongguo_csv` 或兼容的导入类型 |
| `source_ref` | text | 可移植的来源标识，不暴露必须可访问的绝对路径 |
| `source_checksum` | char(64) | 源内容 SHA-256 |
| `parser_version` | integer | 解析规则版本 |
| `record_count` | integer | 条目数量 |
| `validation_json` | jsonb | 原有校验汇总 |
| `imported_at` | timestamptz | 最近成功导入时间 |

约束和索引：

- `platform` 仅允许 `dy`、`hg`。
- `record_count >= 0`。
- `(platform, source_kind, source_ref)` 唯一，用于识别同一来源。
- `(platform, source_checksum)` 唯一，用于阻止同一内容从不同路径重复导入。
- 为 `(platform, snapshot_date, captured_at)` 建立查询索引。

同一来源、相同校验值再次导入时直接跳过；同一来源内容发生变化时，在一个事务中更新快照并替换其全部条目。相同内容从其他路径再次出现时复用已有结果并报告跳过。

`source_ref` 对项目内文件保存项目根目录相对路径，对外部红果导出保存导出根目录相对路径，统一使用 `/` 分隔符。API 继续通过现有 `selected_snapshot.source_path` 字段返回这个可移植标识，避免破坏前端响应契约。

### `rank_entries`

一条记录代表某个快照内的一个榜单位置。

| 字段组 | 主要字段 |
|---|---|
| 归属 | `id`、`snapshot_id` |
| 分组 | `section_name`、`ranking_name`、`rank` |
| 标识 | `series_key`、`title` |
| 描述 | `category`、`status`、`subject`、`description`、`hot_comment` |
| 指标 | `score`、`share_count`、`like_count`、`favorite_count`、`heat`、`comment_count`、`recommend_index`、`rating_count`、`expectation_index`、`search_index`、`max_heat` |
| 保真与校验 | `source_record_json`、`validation_warnings_json` |

`snapshot_id` 外键指向 `rank_snapshots`，删除快照时级联删除条目。`rank` 必须大于零，且 `(snapshot_id, section_name, ranking_name, rank)` 唯一。为 `snapshot_id`、榜单分组和 `series_key` 建立索引。

计数类指标使用 `bigint`，热度、评分和指数类指标使用 `numeric`；这样既能保存整数热度，也能保存红果现有的 `88.5` 等小数热度。无法稳定映射或属于平台专有的数据仍完整保存在 `source_record_json`。`previous_rank`、`rank_delta` 和 `rank_duration` 属于查询期派生值，不持久化。

## 组件边界

### 榜单文件解析器

从现有 `TodayRankService` 中提取纯文件解析职责，继续支持现有抖音 JSON、红果导入 JSON 和红果 CSV。解析器只负责把来源转换为现有 `RankSnapshot` 领域对象，不连接数据库，也不负责页面响应。

### `RankRepository`

只负责 PostgreSQL 数据访问：

- 事务化写入或替换一个快照及其条目；
- 按平台和日期读取快照；
- 返回可用于缓存失效和周报指纹的数据库版本标识；
- 将数据库记录还原为 `RankSnapshot`，隐藏 SQL 和表结构细节。

它通过现有 `SystemDatabase` 和 `AIWORKER_SYSTEM_DATABASE_URL` 获得连接，不新增第二套数据库配置。

### `RankImporter`

协调解析器与 `RankRepository`：计算来源校验值、解析并校验文件、执行幂等导入，返回 `imported`、`updated`、`skipped` 或 `failed` 状态及条目数。单文件失败不会阻止批量回填中的其他文件。

### `TodayRankService`

继续负责日期选择、红果来源优先级、前后快照选择、分组、排名变化、在榜时长和 API 响应组装。快照来源改为 `RankRepository`，不再扫描本地文件。现有响应缓存使用数据库版本标识失效。

### 周报服务

周报继续复用 `TodayRankService` 的榜单数据，同时把当前直接扫描榜单文件生成指纹的逻辑改为使用 `RankRepository` 的版本标识，确保在 `192.168.1.100` 上完全脱离本地榜单目录。

## 数据流

### 历史回填

新增可重复执行的命令：

```powershell
uv run python scripts/migrate_rank_files_to_postgres.py
```

默认扫描项目内抖音榜单目录和配置的红果导出目录，也允许通过参数指定根目录和日期范围。处理顺序为：发现来源、计算校验值、解析校验、事务写库、汇总结果。最终输出每个平台的发现数、导入数、更新数、跳过数、失败数和条目数；只要有失败，进程以非零状态退出。

### 后续自动入库

- `douyin_rank_worker` 在最终榜单 JSON 原子写入完成后调用统一导入服务。
- `hongguo_csv_worker` 在 CSV 校验和榜单转换完成后，以原 CSV 为权威来源调用同一导入服务。
- 原文件始终先落盘，再尝试写数据库。
- 数据库写入失败时保留原文件，任务结果明确标记 `central_sync_failed` 并记录不含凭据的错误；恢复后可通过同一导入命令重试。

### WebGUI 查询

```text
WebGUI API -> TodayRankService -> RankRepository -> PostgreSQL aiworker
```

数据库不可访问时，榜单 API 返回可识别的服务不可用错误和 HTTP 503。页面显示明确的“榜单数据库暂不可用”，不回退读取本机旧文件。

## 一致性与缓存

- 每个快照和全部条目在单个事务中提交或回滚。
- 批量回填以文件为事务边界，便于定位和重试失败来源。
- Repository 对按主键排序的快照 ID、校验值和最近导入时间生成确定性摘要作为版本值；新增、替换或删除快照都会改变版本值。
- `TodayRankService` 的内存和磁盘响应缓存使用该版本值，数据库内容变化后自动失效。
- 多台 WebGUI 进程只缓存派生响应，不拥有榜单事实来源，因此最终读取结果一致。

## 数据库迁移

新增一条接在当前 `0015_llm_settings` 后的 Alembic migration，创建两张表、约束、索引和外键。migration 的降级仅删除本次新增对象，不改动账号和其他业务表。

当前部署状态存在版本差异：开发仓库 head 为 `0015_llm_settings`，`192.168.1.100` 的数据库曾升级到 `0012_writer_stage_edit`。上线前必须让 `.34`、`.100` 使用同一代码版本，并把 `aiworker` 从实际 revision 连续升级到包含榜单表的新 head，禁止仅手工创建表或直接 stamp。

## 错误处理与可观测性

- 文件不存在、格式错误、校验失败、唯一约束冲突和数据库连接失败使用不同错误类型和日志消息。
- 日志包含平台、来源标识、校验值前缀、动作、条目数和耗时，不记录数据库密码或完整连接串。
- 历史回填报告逐文件列出结果，便于只重试失败项。
- 自动入库失败不能被记录为完全成功；源文件仍作为可恢复材料保留。
- API 将数据库不可用映射为 503，将无榜单数据保留为正常的空结果，二者不得混淆。

## 测试策略

1. Alembic 测试：从当前 schema 升级，验证表、索引、外键和约束。
2. 解析回归：使用现有抖音 JSON、红果 CSV 测试样本，验证标准化结果与当前服务一致。
3. Repository 集成测试：写入、读取、替换、级联删除、日期查询和版本变化。
4. 幂等测试：同一来源重复导入不增加快照和条目；来源内容变化时完整替换。
5. 事务测试：条目写入失败时不留下快照或部分条目。
6. Service 回归：日期、分区、榜单、排名变化、在榜时长及响应字段保持现有行为。
7. API 故障测试：数据库不可访问返回 503，无数据返回正常空响应。
8. 自动写入测试：两个榜单 worker 在文件生成后调用导入服务，并正确传播同步状态。
9. 周报测试：集中存储模式下不依赖本地榜单目录，数据库变化会使周报指纹变化。

## 上线与验收

上线按以下顺序执行：

1. 记录并备份 `aiworker` 数据库，确认当前 Alembic revision。
2. 将 `.34` 和 `.100` 部署到同一个、包含榜单 migration 的代码提交。
3. 执行 `uv run alembic upgrade head`，核验实际 head 和新表。
4. 在 `.34` 执行历史回填命令，保留完整报告。
5. 核对发现的来源文件数、成功快照数、条目数、日期范围和失败列表。
6. 分别从 `.34` 和 `.100` 请求相同日期、相同平台的榜单 API，比较业务响应。
7. 生成一份新榜单，确认自动入库、缓存失效及两台机器可见。
8. 验证周报能够使用集中榜单生成，不访问本地榜单文件。

验收不能只依赖命令退出码，必须同时满足：数据库数量与导入报告一致；两台机器读取结果一致；新榜单能自动入库；页面核心行为与迁移前一致；数据库故障时给出明确错误且源文件可用于补偿导入。

## 回滚

若应用层出现问题，先停止新版本的自动导入和榜单请求，将数据库保留用于排查，不立即执行 downgrade。原始 JSON/CSV 未移动或删除，可继续作为恢复来源。只有确认需要撤销 schema 且已备份新增榜单数据时，才执行 Alembic downgrade 删除本期两张表；账号及其他系统数据不受影响。
