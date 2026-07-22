# AiWorker 多 Agent 架构重构可行性分析

## 1. 结论

AiWorker 具备重构为多 Agent 架构的基础条件，但推荐采用渐进式方案：先从“多任务调度器 + 多进程平台 Worker Agent + 统一事件日志 + 存储/导出 Agent”开始，而不是直接改造成复杂微服务或完全同进程并发架构。

当前项目已经按平台划分了较清晰的模块边界，例如 `media_platform/douyin`、`media_platform/xhs`、`media_platform/bilibili` 等，每个平台都有相对独立的 `core.py`、`client.py`、`login.py` 和对应 `store` 实现。这为按平台拆分 Agent 提供了天然切入点。

主要阻碍在于：运行配置大量依赖 `config` 模块全局变量，任务上下文依赖 `ContextVar`，WebGUI 当前只维护单个爬虫进程，文件存储也不适合多进程并发写入。因此，第一阶段应优先采用进程隔离，避免多个 Agent 在同一个 Python 进程里共享全局状态。

## 2. 当前架构简述

### 2.1 入口与平台分发

项目入口为 `main.py`。核心分发点是 `CrawlerFactory`，当前支持：

```text
xhs | dy | ks | bili | wb | tieba | zhihu
```

平台实现分布在：

```text
media_platform/xhs/core.py        XiaoHongShuCrawler
media_platform/douyin/core.py     DouYinCrawler
media_platform/kuaishou/core.py   KuaishouCrawler
media_platform/bilibili/core.py   BilibiliCrawler
media_platform/weibo/core.py      WeiboCrawler
media_platform/tieba/core.py      TieBaCrawler
media_platform/zhihu/core.py      ZhihuCrawler
```

### 2.2 抽象层

基础抽象位于 `base/base_crawler.py`，已有：

- `AbstractCrawler`
- `AbstractLogin`
- `AbstractStore`
- `AbstractStoreImage`
- `AbstractStoreVideo`
- `AbstractApiClient`

这些抽象能作为 Agent 接口演进的基础，但目前仍偏向类继承接口，不包含任务 ID、状态事件、取消、重试、心跳等 Agent 运行协议。

### 2.3 配置与运行状态

全局配置集中在 `config/base_config.py`，典型配置包括：

```text
PLATFORM
KEYWORDS
LOGIN_TYPE
CRAWLER_TYPE
SAVE_DATA_OPTION
ENABLE_GET_COMMENTS
ENABLE_GET_SUB_COMMENTS
MAX_CONCURRENCY_NUM
ENABLE_CDP_MODE
CDP_DEBUG_PORT
USER_DATA_DIR
```

运行上下文位于 `var.py`，包含：

```text
crawler_type_var
source_keyword_var
project_var
comment_tasks_var
db_conn_pool_var
```

这些全局变量和 `ContextVar` 是多 Agent 改造中的主要耦合点。

### 2.4 WebGUI 单任务模型

`WebGUI.py` 中 `AppState` 当前只维护一个运行进程：

```text
state.process
state.log_queue
state.is_running
state.current_project
state.stop_requested
```

启动爬虫通过 `subprocess.Popen` 执行 `uv run main.py`。这说明项目已经有“外部 Worker 进程”的雏形，但还没有多任务管理能力。

### 2.5 存储体系

当前存储支持：

```text
csv | db | json | sqlite | mongodb | excel
```

各平台有独立 StoreFactory，例如：

```text
store/douyin/__init__.py
store/xhs/__init__.py
store/bilibili/__init__.py
```

文件写入主要通过 `tools/async_file_writer.py`，输出路径形如：

```text
data/{platform}/{file_type}/{crawler_type}_{item_type}_{date}.{file_type}
```

该模式在单任务下可用，但多 Agent 并发写同一 JSON、CSV 或 Excel 文件时存在竞争风险。

## 3. 多 Agent 改造可行性

### 3.1 高可行部分

#### 3.1.1 按平台拆分 Worker Agent

当前目录结构已经天然按平台隔离，适合形成以下 Worker：

```text
DouyinCrawlerAgent
XhsCrawlerAgent
BilibiliCrawlerAgent
KuaishouCrawlerAgent
WeiboCrawlerAgent
TiebaCrawlerAgent
ZhihuCrawlerAgent
```

每个 Worker 负责独立平台的登录、搜索、详情、评论、媒体抓取。

#### 3.1.2 使用多进程隔离

现有 WebGUI 已使用 `subprocess.Popen` 启动 `main.py`，因此将单进程扩展为多进程管理相对自然。

推荐第一阶段继续采用：

```text
uv run main.py --job-file .runtime/jobs/{job_id}.json
```

这样可以避免同进程共享 `config` 和 `ContextVar` 造成任务串扰。

#### 3.1.3 平台数据统一入库

已有 SQLAlchemy、SQLite、MySQL、MongoDB 存储实现，可以作为多 Agent 汇聚数据的基础。多 Agent 模式下建议优先使用数据库作为主存储，文件作为导出结果。

### 3.2 中等可行部分

#### 3.2.1 WebGUI 多任务管理

需要将当前单一 `state.process` 改为多任务字典：

```python
state.jobs: dict[str, JobState]
state.workers: dict[str, WorkerState]
```

并为前端增加任务列表、任务详情、日志流、停止任务等接口。

#### 3.2.2 存储 Agent 化

当前 StoreFactory 分散在各平台模块中，适合逐步抽出统一 Storage Agent。短期可以先保持平台 StoreFactory 不动，只按 `job_id` 隔离输出文件；中期再迁移为事件入库。

#### 3.2.3 后处理 Agent 化

项目已有词云、媒体下载、榜单报告、视频分析等功能迹象，适合拆成后处理 Agent：

```text
MediaDownloadAgent
WordCloudAgent
RankReportAgent
VideoAnalysisAgent
TitleFilterAgent
ExportAgent
```

这些 Agent 不应阻塞主爬虫流程。

### 3.3 低可行或暂不建议部分

#### 3.3.1 同进程多 Agent 并发

不建议第一阶段做。原因：

- `config` 是模块级全局状态；
- `ContextVar` 被平台、存储、文件写入共同依赖；
- CDP 浏览器实例、端口、用户目录需要隔离；
- 文件写入锁只在单事件循环内有效，不跨进程。

#### 3.3.2 直接微服务化

当前项目体量和依赖形态暂不需要直接拆成多个常驻服务。微服务会引入部署、服务发现、队列、监控、鉴权等额外复杂度。

#### 3.3.3 用 AI Agent 替代核心爬虫逻辑

不建议。平台采集流程需要确定性、限速、可恢复和可审计。AI Agent 更适合用于标题筛选、内容摘要、报告生成、异常诊断等后处理环节。

## 4. 推荐目标架构

### 4.1 第一阶段目标架构

```text
WebGUI / CLI
    |
    v
Coordinator Agent / Job Manager
    |
    +-- Douyin Worker Agent    -> uv run main.py --job-file ...
    +-- XHS Worker Agent       -> uv run main.py --job-file ...
    +-- Bilibili Worker Agent  -> uv run main.py --job-file ...
    +-- Kuaishou Worker Agent  -> uv run main.py --job-file ...
    +-- Weibo Worker Agent     -> uv run main.py --job-file ...
    +-- Tieba Worker Agent     -> uv run main.py --job-file ...
    +-- Zhihu Worker Agent     -> uv run main.py --job-file ...
    |
    v
Unified Event Log / Job State
    |
    v
Storage / Export Agent
    |
    v
SQLite / MySQL / MongoDB / JSON / Excel / Markdown
```

### 4.2 核心组件职责

#### Coordinator Agent / Job Manager

负责接收 WebGUI / CLI 任务、生成 `job_id`、写入 job 文件、启动平台 Worker 进程、读取 stdout / stderr、解析 JSONL 事件、聚合日志和任务状态、停止指定任务、处理超时和失败。

#### Platform Worker Agent

负责按 `job_file` 初始化运行配置，启动浏览器或 CDP，执行平台爬虫逻辑，输出结构化事件，并将采集结果交给存储层。

#### Storage Agent

负责幂等写入内容、评论、创作者、媒体元数据，避免多 Worker 直接竞争写同一个文件，并支持按 `job_id` 查询结果。

#### Export Agent

负责从数据库或 job 结果中导出 JSON、CSV、Excel、Markdown，后续可接入飞书、邮箱、Webhook 等分发渠道。

#### Media Download Agent

负责视频、图片、音频下载，包含下载重试、文件完整性校验、下载状态记录。

## 5. 推荐数据协议

### 5.1 CrawlJob

建议新增 `core/job.py`，定义任务上下文：

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class CrawlJob:
    job_id: str
    platform: str
    crawler_type: str
    keywords: list[str] = field(default_factory=list)
    login_type: str = "qrcode"
    save_data_option: str = "json"
    start_page: int = 1
    max_notes_count: int = 200
    max_concurrency: int = 1
    enable_get_comments: bool = True
    enable_get_sub_comments: bool = False
    enable_get_medias: bool = False
    enable_cdp_mode: bool = True
    cdp_debug_port: int | None = None
    user_data_dir: str | None = None
    project_keyword_map: dict[str, list[str]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
```

短期可以提供 `CrawlJob.from_config()` 和 `CrawlJob.apply_to_config()`，用于兼容旧代码。

### 5.2 Worker 事件协议

建议 Worker stdout 输出 JSONL，每行一个事件：

```json
{
  "event": "job_started",
  "job_id": "20260626-dy-001",
  "worker_id": "dy-worker-1",
  "platform": "dy",
  "level": "info",
  "message": "job started",
  "ts": 1780000000,
  "payload": {}
}
```

常见事件类型：

```text
job_started
browser_started
login_required
login_success
page_started
item_fetched
content_stored
comment_stored
media_queued
media_downloaded
warning
error
job_finished
job_failed
job_cancelled
```

### 5.3 输出目录隔离

多 Agent 模式下建议按 `job_id` 隔离运行产物：

```text
.runtime/jobs/{job_id}.json
.runtime/logs/{job_id}.jsonl
.runtime/state/{job_id}.json

data/{platform}/jobs/{job_id}/contents.json
data/{platform}/jobs/{job_id}/comments.json
exports/{job_id}/report.md
exports/{job_id}/result.xlsx
```

## 6. 分阶段迁移计划

### 阶段 0：保持现状，补充文档和测试

目标是明确现有行为，降低后续重构风险。建议记录 `main.py`、`WebGUI.py`、各平台 `core.py` 的调用链；为 `CrawlerFactory`、`StoreFactory`、文件写入路径生成逻辑、命令行参数解析增加测试；明确哪些平台必须使用 CDP，哪些可以无浏览器 API 请求。

验证命令：

```powershell
uv run pytest tests/ -q
```

### 阶段 1：引入 CrawlJob，但不改变主流程

目标是用显式任务上下文替代隐式全局配置的入口。建议新增 `core/job.py`，修改 `main.py` 和 `cmd_arg/arg.py`，支持 `--job-file`，job 文件读取后应用到 `config`，旧命令行继续可用。

### 阶段 2：WebGUI 支持多 Job 管理

目标是从单进程状态升级为多任务状态。建议修改 `WebGUI.py`、`templates/index.html` 和相关静态脚本。

新增接口：

```text
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/logs
POST /api/jobs/{job_id}/stop
```

### 阶段 3：Worker stdout 事件化

目标是 Coordinator 不再解析自然语言日志。建议新增 `core/events.py`，实现 `emit_event(event, level, message, payload)`，stdout 输出 JSONL，普通日志继续保留，但 UI 状态以事件为准。

### 阶段 4：按 job_id 隔离输出

目标是避免多 Agent 竞争写同一个文件。建议修改 `tools/async_file_writer.py` 和 `store/*/_store_impl.py`，文件名加入 `job_id` 或输出到 `data/{platform}/jobs/{job_id}/`，任务完成后由 Export Agent 合并导出。

### 阶段 5：Storage / Export Agent 化

目标是让 Worker 只负责采集，不负责最终报告形态。建议新增 `agents/storage_agent.py` 和 `agents/export_agent.py`。迁移时先让 Worker 继续调用现有 StoreFactory，再逐步将 StoreFactory 内部转为写统一数据库。

### 阶段 6：平台内部细分 Agent

在进程级多 Agent 稳定后，再考虑拆分 `SearchAgent`、`DetailAgent`、`CommentAgent`、`MediaAgent`、`CreatorAgent`。这一步不应太早做，因为当前各平台 `core.py` 中搜索、详情、评论抓取有较强流程耦合。

## 7. 关键风险与应对

### 7.1 CDP 端口和浏览器用户目录冲突

风险：多个 Worker 同时使用默认 `CDP_DEBUG_PORT = 9222` 和相同用户目录，可能导致浏览器控制混乱。

应对：每个 job 分配独立 CDP 端口和 user data dir；停止任务时只清理本 job 对应浏览器；避免全局 `taskkill /IM msedge.exe` 影响其他任务。

### 7.2 全局 config 串扰

风险：同进程并发运行时，不同任务会覆盖 `config.PLATFORM`、`config.CRAWLER_TYPE` 等变量。

应对：第一阶段使用多进程隔离；中长期将平台 Crawler 改为接收 `CrawlJob`；逐步减少对 `config` 的直接读取。

### 7.3 文件并发写入损坏

风险：`asyncio.Lock()` 不跨进程，多 Worker 写同一 JSON 文件会产生覆盖或 JSON 损坏。

应对：按 `job_id` 隔离文件；数据库存储优先；文件导出放到任务结束后执行。

### 7.4 登录态和 Cookie 共享问题

风险：多 Agent 使用同一浏览器目录时 Cookie、会话和检测状态互相影响。

应对：每个平台、每个 job 明确用户目录策略；对需要人工扫码的平台，任务状态要支持 `login_required`；GUI 展示登录等待状态。

### 7.5 反爬与请求限速

风险：并行 Agent 会显著提高请求频率，触发平台限制。

应对：Job Manager 增加全局并发限制；每个平台独立限速；支持任务优先级；默认不要多个 Worker 同时抓同一平台。

### 7.6 测试覆盖不足

风险：重构后容易破坏平台采集和存储行为。

应对：先补单元测试；对 Worker 命令生成、job 文件解析、事件解析、停止任务做测试；对每个平台保留最小 smoke test。

## 8. 推荐第一版 MVP 范围

第一版 MVP 建议只实现以下能力：

1. 新增 `CrawlJob` 数据结构；
2. 支持 `main.py --job-file <path>`；
3. WebGUI 可创建多个 job；
4. 每个 job 独立启动一个 Worker 进程；
5. 每个 Worker 输出 JSONL 事件；
6. Coordinator 维护多任务状态；
7. 支持停止指定 job；
8. 输出文件按 `job_id` 隔离；
9. 保留旧 CLI 和旧单任务模式。

不纳入 MVP：完整微服务化、消息队列、Kubernetes / Docker 编排、平台内部 Search/Detail/Comment 细粒度拆分、AI Agent 自动决策采集策略。

## 9. 建议文件变更清单

### 新增文件

```text
core/job.py
core/events.py
runtime/job_manager.py
runtime/job_state.py
runtime/worker_launcher.py
agents/storage_agent.py
agents/export_agent.py
tests/test_job_model.py
tests/test_worker_launcher.py
tests/test_events.py
```

### 可能修改文件

```text
main.py
cmd_arg/arg.py
WebGUI.py
tools/async_file_writer.py
store/*/_store_impl.py
store/*/__init__.py
templates/index.html
```

### 暂不建议大改文件

```text
media_platform/*/core.py
media_platform/*/client.py
media_platform/*/login.py
```

这些平台核心文件应先通过适配层接入 Job Manager，等多进程模型稳定后再逐步内部解耦。

## 10. 验证标准

### 10.1 功能验证

- 旧命令仍可运行：`uv run main.py --help`
- 单平台旧模式仍可运行；
- `--job-file` 模式可运行；
- WebGUI 能同时显示多个任务；
- 停止一个任务不影响其他任务；
- 每个任务有独立日志和输出目录。

### 10.2 自动化测试

建议执行：

```powershell
uv run pytest tests/ -q
```

新增测试应覆盖：

- `CrawlJob` 序列化 / 反序列化；
- job 文件应用到 config；
- Worker 启动命令生成；
- JSONL 事件解析；
- 多 job 状态更新；
- 输出路径隔离。

### 10.3 回归验证

至少验证 `dy`、`xhs`、`bili` 中的一个平台。每个平台至少验证 search 模式启动、日志进入 job 状态、输出文件或数据库记录存在、任务结束状态正确。

## 11. 最终建议

本项目多 Agent 化的最佳路线不是“大拆大改”，而是利用现有 `subprocess.Popen` 运行模型，先把 WebGUI 从单任务控制器升级为 Job Manager，再把平台爬虫包装为独立 Worker Agent。

短期应坚持三个原则：

1. **进程隔离优先**：避免全局配置串扰；
2. **事件协议优先**：避免 GUI 解析自然语言日志；
3. **存储隔离优先**：避免多 Agent 并发写坏文件。

当这些基础能力稳定后，再考虑将存储、媒体下载、分析、报告生成拆成独立 Agent。这样既能保留现有平台爬虫代码的可用性，又能逐步获得多任务、多平台、可观测、可停止、可扩展的 Agent 架构。
