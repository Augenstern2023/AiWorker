## 多Agent架构重构可行性分析方案

本项目**适合重构为“多 Agent / 多 Worker 架构”**，但不建议一上来做成完整微服务或复杂 AI Agent 系统。更可行的路径是：

> **先把现有单进程爬虫改造成“任务调度器 + 平台 Worker Agent + 存储/后处理 Agent”的进程级多 Agent 架构。**

原因是项目已经有较清晰的平台边界：`xhs / dy / ks / bili / wb / tieba / zhihu` 各自有 `core.py / client.py / login.py / store`，但当前配置、状态、浏览器、文件写入仍大量依赖全局变量和单进程假设，需要先解耦。

---

## 依据

### 1. 当前代码已经具备“按平台拆 Agent”的天然边界

入口在 `main.py`：

- `main.py:39-48` 定义 `CrawlerFactory.CRAWLERS`
- 平台映射包括：
  - `xhs`
  - `dy`
  - `ks`
  - `bili`
  - `wb`
  - `tieba`
  - `zhihu`

每个平台都有独立目录，例如：

```text
media_platform/douyin/core.py
media_platform/douyin/client.py
media_platform/douyin/login.py

media_platform/xhs/core.py
media_platform/xhs/client.py
media_platform/xhs/login.py
```

这说明第一层 Agent 拆分可以直接按平台做：

```text
DouyinCrawlerAgent
XhsCrawlerAgent
BilibiliCrawlerAgent
KuaishouCrawlerAgent
WeiboCrawlerAgent
TiebaCrawlerAgent
ZhihuCrawlerAgent
```

这类拆分成本最低，收益也最明确。

---

### 2. 抽象层已经存在，但目前更像“类接口”，不是 Agent 协议

`base/base_crawler.py` 已经有基础抽象：

- `AbstractCrawler`
  - `start()`
  - `search()`
  - `launch_browser()`
- `AbstractLogin`
- `AbstractStore`
- `AbstractApiClient`

这对多 Agent 很有帮助，因为可以把每个 Agent 的行为收敛成统一协议：

```text
Agent.start(job)
Agent.stop(job_id)
Agent.status(job_id)
Agent.emit(event)
```

不过现有抽象偏同步调用链，没有任务上下文、事件、状态机、重试策略、取消语义，所以还不能直接支撑多 Agent 编排。

---

### 3. 当前最大阻碍是全局配置和 ContextVar 状态

当前配置集中在 `config/base_config.py`，并且运行时由命令行解析直接修改 `config` 模块变量。

典型全局配置：

- `config.PLATFORM`
- `config.KEYWORDS`
- `config.CRAWLER_TYPE`
- `config.SAVE_DATA_OPTION`
- `config.ENABLE_GET_COMMENTS`
- `config.MAX_CONCURRENCY_NUM`
- `config.ENABLE_CDP_MODE`

`var.py` 里还有跨模块 ContextVar：

```text
crawler_type_var
source_keyword_var
project_var
comment_tasks_var
```

存储层、文件写入、平台爬虫都依赖这些变量。例如：

- `tools/async_file_writer.py:27` 使用 `project_var`
- `tools/async_file_writer.py:48-55` 写文件时根据当前 `project_var` 给数据加项目名
- `store/*/__init__.py` 通过 `source_keyword_var.get()` 注入搜索关键词
- 各平台 `core.py` 中设置 `crawler_type_var / source_keyword_var / project_var`

这意味着如果多个 Agent 在同一进程内并发跑不同平台或不同项目，状态很容易串扰。  
所以第一阶段更建议采用**进程隔离**，而不是同一 Python 进程里直接 `asyncio.gather()` 多个平台。

---

### 4. WebGUI 当前也是单任务模型

`WebGUI.py` 里 `AppState` 是单例全局状态：

```text
state.process
state.log_queue
state.is_running
state.current_project
state.batch_queue
state.stop_requested
```

启动爬虫主要通过 `subprocess.Popen(["uv", "run", "main.py", ...])`。  
这说明现有 GUI 已经有“外部进程 Worker”的雏形，但只支持一个当前进程，不支持多任务、多 Agent 状态聚合。

要做多 Agent，需要把：

```text
state.process: Optional[subprocess.Popen]
```

升级为：

```text
state.jobs: dict[job_id, JobState]
state.workers: dict[worker_id, WorkerState]
```

否则 GUI 无法同时管理多个平台任务。

---

### 5. 存储层支持多后端，但并发写入需要重新设计

当前支持：

- csv
- json
- db
- sqlite
- mongodb
- excel

每个平台有自己的 `StoreFactory`，例如：

- `store/douyin/__init__.py:33-48`
- `store/xhs/__init__.py:33-48`

但文件写入路径按日期、平台、爬虫类型生成：

```text
data/{platform}/{file_type}/{crawler_type}_{item_type}_{date}.{file_type}
```

如果多个 Agent 同时写同一个 JSON/CSV/Excel 文件，会有竞争风险。  
尤其是 JSON 当前是读整个文件、append、再整体写回，这种方式不适合多进程并发。

所以多 Agent 后建议优先采用：

1. SQLite / MySQL / MongoDB 作为主存储；
2. 文件导出变成独立 Export Agent；
3. JSON/Excel 只作为最终导出物，而不是实时并发写入目标。

---

## 推荐架构

### MVP 架构：进程级多 Agent

```text
WebGUI / CLI
    |
    v
Coordinator Agent / Job Manager
    |
    +-- Douyin Worker Agent
    +-- XHS Worker Agent
    +-- Bilibili Worker Agent
    +-- Kuaishou Worker Agent
    +-- Weibo Worker Agent
    +-- Tieba Worker Agent
    +-- Zhihu Worker Agent
    |
    v
Storage Agent / Export Agent
    |
    v
SQLite / MySQL / MongoDB / JSON / Excel
```

### Agent 职责划分

#### 1. Coordinator Agent

负责：

- 接收 GUI / CLI 任务；
- 生成 `job_id`；
- 拆分任务；
- 启动 Worker；
- 聚合日志；
- 控制停止、重试、超时；
- 汇总最终结果。

#### 2. Platform Crawler Agent

每个平台一个 Worker 类型：

```text
DouyinCrawlerAgent
XhsCrawlerAgent
BilibiliCrawlerAgent
...
```

负责：

- 登录；
- 搜索；
- 详情抓取；
- 评论抓取；
- 媒体抓取；
- 产出结构化事件。

不要让它直接决定最终存储路径，而是发出统一事件：

```json
{
  "job_id": "...",
  "platform": "dy",
  "type": "content",
  "source_keyword": "...",
  "project_names": ["..."],
  "payload": {}
}
```

#### 3. Storage Agent

负责：

- 接收内容、评论、创作者、媒体元数据；
- 统一写入数据库；
- 做幂等 upsert；
- 控制文件导出。

#### 4. Media Download Agent

适合单独拆，因为媒体下载耗时、失败率高、容易拖慢主爬虫。

负责：

- 下载视频；
- 下载图片；
- 下载音频；
- 重试；
- 校验文件大小；
- 记录下载状态。

#### 5. Analysis / Filter Agent

你项目里已有标题筛选、B站视频分析、榜单分析脚本的迹象。  
这些不应混在平台爬虫里，适合后处理 Agent：

```text
TitleFilterAgent
VideoAnalysisAgent
RankReportAgent
WordCloudAgent
```

#### 6. Report Agent

负责：

- 生成 Markdown；
- 生成 Excel；
- 生成词云；
- 推送到飞书或其他渠道。

---

## 推荐分阶段改造路径

### 阶段 1：先做“任务上下文”解耦

目标：让爬虫不再依赖全局 `config` 和 `ContextVar` 作为唯一运行上下文。

新增一个数据模型，例如：

```text
core/job.py
```

定义：

```python
@dataclass
class CrawlJob:
    job_id: str
    platform: str
    crawler_type: str
    keywords: list[str]
    login_type: str
    save_data_option: str
    enable_get_comments: bool
    enable_get_sub_comments: bool
    max_concurrency: int
    project_keyword_map: dict[str, list[str]]
```

短期可以保留 `config`，但新增一层适配：

```text
CrawlJob.from_config()
```

这样不破坏旧 CLI。

---

### 阶段 2：引入 Job Manager，但继续用 subprocess 跑 Worker

这是最稳的阶段。

当前 WebGUI 已经用 `subprocess.Popen` 启动 `main.py`，可以扩展为：

```text
uv run main.py --job-file .runtime/jobs/{job_id}.json
```

每个 Worker 独立进程读取自己的 job 文件，避免全局变量串扰。

新增：

```text
runtime/job_manager.py
runtime/worker_process.py
```

保留现有平台代码，先不大改内部逻辑。

---

### 阶段 3：日志和状态事件化

当前日志主要写 stdout / queue。多 Agent 后需要统一事件格式：

```json
{
  "job_id": "20260626-dy-001",
  "worker_id": "dy-worker-1",
  "level": "info",
  "event": "item_stored",
  "message": "stored aweme",
  "payload": {
    "aweme_id": "..."
  },
  "ts": 1780000000
}
```

Coordinator 从 stdout 读取 JSONL 事件即可。  
这比直接解析自然语言日志可靠。

---

### 阶段 4：存储 Agent 化

先不要让多个 Worker 并发写 JSON/Excel。建议：

- Worker 写数据库；
- Export Agent 从数据库导出 JSON / Excel / Markdown；
- 文件输出只发生在任务末尾。

如果短期仍要写 JSON，至少要按 `job_id` 分文件：

```text
data/douyin/json/{job_id}_contents.json
data/douyin/json/{job_id}_comments.json
```

避免多个 Agent 写同一文件。

---

### 阶段 5：平台内部再拆细粒度 Agent

等进程级多平台 Worker 稳定后，再考虑平台内部拆：

```text
SearchAgent
DetailAgent
CommentAgent
MediaAgent
CreatorAgent
```

不建议一开始就拆这么细，因为现有 `core.py` 里搜索、详情、评论流程高度耦合，直接拆会引入大量接口重写。

---

## 可行性评估

| 维度 | 评估 |
|---|---|
| 平台边界 | 高可行，目录结构已经按平台拆分 |
| 进程级 Worker | 高可行，WebGUI 已经使用 subprocess 启动爬虫 |
| 同进程多 Agent | 中低可行，受全局 config 和 ContextVar 限制 |
| 存储解耦 | 中等可行，需要处理 JSON/Excel 并发写 |
| GUI 多任务管理 | 中等可行，需要重构 AppState |
| 浏览器/CDP 多实例 | 有风险，真实浏览器用户目录、端口、Cookie 需要隔离 |
| 微服务化 | 暂不建议，当前体量没必要先上服务拆分 |
| AI Agent 化 | 适合做后处理，不适合替代核心爬虫流程 |

---

## 主要风险

### 1. CDP 模式不适合无隔离并发

`config/base_config.py` 默认：

```text
ENABLE_CDP_MODE = True
CDP_DEBUG_PORT = 9222
USER_DATA_DIR = "%s_user_data_dir"
```

如果多个 Agent 同时启动浏览器，可能出现：

- CDP 端口冲突；
- 用户目录锁定；
- Cookie 状态互相影响；
- 浏览器被一个 Agent 清理，另一个 Agent 失联。

建议每个 Worker 分配独立：

```text
cdp_port
user_data_dir
browser_context
```

---

### 2. 全局 config 会导致任务串扰

如果同一进程跑多个任务，比如一个任务是：

```text
platform=dy
crawler_type=search
```

另一个任务是：

```text
platform=xhs
crawler_type=detail
```

它们会共享 `config.PLATFORM / config.CRAWLER_TYPE / config.SAVE_DATA_OPTION`。  
所以短期必须用进程隔离，长期要改成显式 `CrawlJob` 参数。

---

### 3. 文件存储不是多进程安全

`tools/async_file_writer.py` 里的锁是 `asyncio.Lock()`，只对同一事件循环内有效。  
多进程写同一文件时，这个锁无效。

---

### 4. 测试覆盖不足

当前测试文件较少：

```text
tests/test_excel_store.py
tests/test_store_factory.py
test/test_db_sync.py
test/test_proxy_ip_pool.py
...
```

重构前需要先补核心测试：

- Job 配置解析测试；
- StoreFactory 测试；
- Worker 命令生成测试；
- JSONL 事件解析测试；
- 文件导出测试；
- 停止任务测试。

---

## 建议的最小可行版本

第一版不要追求“全自动多 Agent 智能系统”，而是做：

```text
多任务 Job Manager + 多进程平台 Worker + 统一事件日志 + 结果导出
```

具体能力：

1. WebGUI 可以同时看到多个任务；
2. 每个任务有独立 `job_id`；
3. 每个任务独立启动一个 `uv run main.py --job-file ...`；
4. stdout 输出 JSONL 状态事件；
5. 任务结果按 `job_id` 存储；
6. 支持停止指定任务；
7. 支持任务完成后统一导出 Excel / Markdown。

这是最符合现有代码形态的方案。

---

## 下一步

建议先做一份正式重构方案文档，内容包括：

1. `CrawlJob` 数据结构；
2. Job Manager 状态机；
3. Worker 启动协议；
4. JSONL 事件协议；
5. 存储迁移策略；
6. WebGUI 多任务界面改造；
7. 第一阶段只改哪些文件；
8. 回滚方案。

如果你愿意，我可以下一步直接在项目里生成一份：

```text
docs/multi_agent_refactor_feasibility.md
```

或进一步写成可执行的阶段性开发计划。