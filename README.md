# AiWorker - 壹凡短剧选品平台

AiWorker 是一套面向短剧内容运营、策划和制作团队的本地化工作平台。项目在 MediaCrawler 多平台采集能力之上，整合了榜单分析、剧目检索下载、脚本转写、爆款拆解、选题管理、AI 剧本评审与优化、编剧助手和创作项目工作流，并提供本地账号、角色权限、审批工作流与操作审计能力。

系统默认通过 Web GUI 使用，同时保留桌面 GUI 和命令行爬虫入口。耗时下载、转写和 AI 生成任务统一进入任务中心，可查看进度、日志和产物。

## 当前能力

| 模块 | 主要能力 |
| --- | --- |
| 今日榜单 | 读取抖音、红果榜单快照；展示排名变化、热评及连续/累计在榜天数 |
| 剧目检索下载 | 检索剧目和分集；批量下载视频、音频及脚本；显示进度并支持中断后继续 |
| 今日爆款 | 从榜单生成候选选题 Top10，展示推荐分、题材、来源与运营状态 |
| 选题库 | 采纳、不跟进、重新采纳、归档；筛选状态、平台、题材及拆解版本 |
| 剧目拆解 | 至少基于一集已转写脚本生成个性化拆解，记录逐字段证据、来源和风险 |
| 拆解版本 | 编辑、保存、锁定、选择历史版本；默认复用最新版本，明确操作后才重新生成 |
| 创作项目 | 从锁定拆解生成仿制方向、Brief、人物关系、分集大纲和单集 Beats |
| 版本与导出 | 各创作阶段支持保存、锁定和版本衔接，可导出包含 Brief、人物、大纲、Beats 与剧本的 ZIP 文档包 |
| AI 剧本评审 | 基于转写脚本的轻量 AI 剧本评审（`screenplay_review`） |
| 剧本优化 | 生成剧本优化草稿（`screenplay_optimization`） |
| 编剧助手 | 独立短剧/漫画剧写作教练服务（`writer_agent`） |
| 通用采集 | 支持小红书、抖音、快手、B 站、微博、贴吧和知乎的搜索、详情及创作者采集 |
| 数据管理 | 支持 JSON、CSV、Excel、SQLite、MySQL 和 MongoDB 等存储方式 |

## 核心工作流

```text
榜单/剧目数据
  -> 剧目检索与分集下载
  -> 至少一集脚本转写
  -> 个性化爆款拆解
  -> 选题采纳与拆解锁定
  -> 仿制方向与 Brief
  -> 人物关系
  -> 分集大纲
  -> 单集 Beats
  -> 制作资料导出
```

拆解不会在每次点击时重复调用模型。已有草稿或锁定版本时，列表显示“再次拆解 / 归档”；“再次拆解”打开最新版本，只有编辑器中的“重新生成”才会重新分析已下载脚本。

## 短剧选品使用顺序

1. 在“今日榜单”抓取或选择抖音/红果榜单快照。
2. 在“剧目检索下载”中找到目标剧目，选择至少一集并完成下载与转写。
3. 在“今日爆款”生成候选选题，结合推荐分、题材和在榜时长进行判断。
4. 点击“拆解”。没有有效转写时，系统会提示先下载剧目脚本。
5. 检查拆解字段、证据和风险，保存后锁定可用版本。
6. 采纳选题并进入“创作项目”，依次完成方向、Brief、人物、分集大纲和 Beats。
7. 每个阶段保存并锁定后再进入下游阶段，最后导出制作资料。

> 多人协作时，人员任务从 `/tasks` 领取；业务页面分别位于 `/selection/*`、`/planning/*`、`/writing/*`、`/review/*` 和 `/admin/*`。提交版本进入轻量审批注册表，批准后才创建下一角色池任务；系统管理员在 `/admin/deliveries` 交付批准成果的 Markdown/JSON 快照。

## 架构总览

AiWorker 由两层组成：底层是 MediaCrawler 派生的**通用采集层**，上层是基于 FastAPI 的**应用工作层**。

```text
┌─────────────────────────────── 应用工作层（FastAPI Web GUI） ──────────────────────────────┐
│  WebGUI.py                                                                                │
│  ├─ web/           页面路由、导航、页面访问控制                                             │
│  ├─ identity/      本地账号、角色、权限、会话                                                │
│  ├─ organization/  组织架构（部门）                                                         │
│  ├─ workflow/      任务池、项目成员、审批、状态机、流转                                       │
│  ├─ audit/         安全与业务审计                                                           │
│  ├─ creative/      选题、拆解、创作项目、版本与交付导出                                       │
│  ├─ agents/        抖音/红果分析、剧目下载、评论抓取等后台代理                                │
│  ├─ analysis/      ASR 转写、LLM 提供者、今日榜单、数据分析、周报                              │
│  ├─ screenplay_review / screenplay_optimization / writer_agent   剧本评审、优化、编剧助手      │
│  ├─ rag/           RAG 索引与检索                                                          │
│  └─ runtime/       任务管理器、工作进程调度                                                   │
├─────────────────────────────── 通用采集层（MediaCrawler） ────────────────────────────────┤
│  main.py -> CrawlerFactory -> media_platform/{xhs,dy,ks,bili,wb,tieba,zhihu}/              │
│  base/ base_crawler、store/、database/、proxy/、cache/、tools/（CDP/Playwright）             │
└─────────────────────────────── 数据层 ────────────────────────────────────────────────────┘
   PostgreSQL（系统数据 + 创作数据，system_database，Alembic 迁移）          data/ 文件
```

- **系统数据**：账号、角色、权限、部门、任务、审批、审计等保存在 PostgreSQL（`system_database`），由 Alembic 迁移维护。
- **创意数据**：选题、拆解、创作项目与版本同样保存在 PostgreSQL（`system_database` 的创作表），与系统数据共用一个数据库和一套 Alembic 迁移，不再使用 SQLite。历史文件 `.runtime/creative/creative.db` 只作为迁移源保留。
- **采集数据**：爬取内容、剧目媒体、转写脚本、分析报告、导出文件保存在 `data/`。

## 环境要求

- Windows 10/11（当前 Web GUI 和批处理启动方式主要在 Windows 验证）
- Python 3.11
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 16 或更高版本
- Chrome 或 Edge；CDP 模式默认连接本机浏览器
- **PostgreSQL**（Web GUI 必需）：通过 `AIWORKER_SYSTEM_DATABASE_URL` 连接，必须使用 `postgresql+psycopg://` 前缀

## 安装

```powershell
git clone http://192.168.1.11:3000/yifangkeji/manjuxuanpin.git
cd manjuxuanpin
uv sync
uv run playwright install
```

项目统一使用 `uv` 管理 Python 环境和依赖，请不要直接使用 `pip install` 修改项目环境。

### 环境配置

Web GUI 启动前需要配置系统数据库连接（PostgreSQL）。在项目根目录 `.env` 中设置：

```dotenv
# 系统数据库连接（必须为 postgresql+psycopg://）
AIWORKER_SYSTEM_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1:5432/aiworker

# 可选：允许的跨域来源（逗号分隔，配置后才会开启 CORS）
# AIWORKER_ALLOWED_ORIGINS=https://your-origin.example.com
```

首次启动时 `WebGUI.py` 会校验系统数据库 schema 是否处于 Alembic 最新版本。若需要初始化或升级数据库，请先确保 PostgreSQL 实例可用并执行迁移（参见 `system_database/` 与 `scripts/migrate_system_sqlite_to_postgres.py`，如需从旧 SQLite 系统数据迁移）。

> 不要把含真实凭证的 `.env` 提交到 Git。`docs/` 与 `scripts/` 中提供了历史 SQLite 系统数据迁移入口，重复运行不会重复创建版本或任务。

## 启动 Web GUI

```powershell
uv run WebGUI.py
```

启动后访问：

- 本机：`http://127.0.0.1:8899`
- 局域网：`http://<本机IP>:8899`

Windows 用户也可以运行 `启动Web程序.bat`。程序会创建所需的数据和运行时目录；第一次使用 Playwright 或 CDP 时，浏览器可能要求完成登录。

首次进入时使用本地账号注册登录。默认角色包括选品运营、创意策划、编剧、项目负责人和系统管理员（详见“角色与权限”）。

## 启动桌面 GUI

```powershell
uv run GUI.py
```

也可以运行 `启动程序.bat`。

## 命令行采集

```powershell
# 查看全部参数
uv run main.py --help

# 关键词搜索
uv run main.py --platform xhs --lt qrcode --type search

# 指定内容详情
uv run main.py --platform xhs --lt qrcode --type detail

# 创作者主页
uv run main.py --platform xhs --lt qrcode --type creator
```

常用配置位于 [config/base_config.py](config/base_config.py)，平台覆盖配置位于 `config/<platform>_config.py`。支持的平台标识包括 `xhs`、`dy`、`ks`、`bili`、`wb`、`tieba` 和 `zhihu`。

## 角色与权限

- 系统内置账号、角色、权限和会话（`identity/`）；相关维护入口统一位于系统管理 → 用户管理 / 角色管理 / 导航配置。
- 五个默认角色为选品运营、创意策划、编剧、项目负责人和系统管理员。
- 页面访问与 API 权限按角色收敛（`web/page_access_service.py`、`identity/permission_service.py`），系统管理员不受页面权限限制。
- 组织架构（部门）通过 `organization/` 管理；操作审计记录在 `audit/`。

## 数据与运行时目录

| 路径 | 内容 |
| --- | --- |
| `data/` | 爬取数据、剧目媒体、转写脚本、分析报告和导出文件 |
| `.runtime/` | 任务状态、日志和临时运行数据 |
| `.runtime/creative/creative.db` | 历史创作数据的 SQLite 迁移源（切换后不再写入） |
| `backups/` | 生成配置的备份文件 |
| `browser_data/` | CDP 浏览器用户数据 |
| `docs/` | 产品、实施、导出和架构文档 |
| `improve/` | 历史 PRD 与改进方案文档 |

不要手工删除正在运行任务对应的运行时文件。

## 测试与质量检查

```powershell
# 单元测试
uv run pytest tests/ -q

# 集成测试（部分用例需要 Redis、MongoDB、PostgreSQL 或有效平台环境）
uv run pytest test/ -q

# 浏览器驱动
uv run playwright install

# 提交前检查
uv run pre-commit run --all-files

# 首次检出后安装提交与推送保护
uv run python tools/install_git_hooks.py

# 手工检查 Alembic 迁移图
uv run python tools/check_alembic_integrity.py repository
```

Alembic 保护以 `manjuxuanpin` 为唯一主上游：数据库结构声明变化必须与新增迁移脚本
一同提交，历史迁移不可修改或删除，推送时会再次校验提交范围。数据库结构提交不得
使用 `--no-verify` 绕过本地检查；远端 CI 和分支保护仍是最终保障。

生产数据库只能通过受控入口升级；该命令会先确认当前分支、干净工作树、数据库
revision，以及当前 `HEAD` 已存在于 `manjuxuanpin/main`：

```powershell
uv run python tools/check_alembic_integrity.py production
uv run python scripts/upgrade_production_database.py
```

`uv run alembic upgrade head` 仅用于隔离测试数据库，不作为生产升级入口。

`manjuxuanpin` 使用 `.gitea/workflows/alembic-integrity.yml` 在干净 checkout 中复验
提交范围、迁移图和 PostgreSQL 空库升级。仓库管理员必须启用 Gitea Actions，禁止
直接推送 `main`，并把 `verify-migrations` 设置为合并请求的必需状态检查；工作流未
实际运行时，不能把配置文件存在视为远端保护已经生效。

## 配置与凭证安全

- 外部服务地址、激活码、Cookie、模型密钥等仅保存在本机配置或环境变量中。
- Web GUI 的 LLM 页面密钥保存在当前 Windows 用户的 Windows Credential Manager；保存后，Web
  进程和已经运行的 Worker 在下一次模型调用时都会读取新密钥，无需重启；已在途的请求继续使用
  发出时的凭据。无法使用凭据库或尚未
  保存页面密钥时，系统继续使用该提供商配置的 API Key 环境变量作为兜底。
- 远程 HTTP 页面不得提交 LLM 密钥。若通过 HTTPS 反向代理提供远程管理页面，代理必须受信任，
  并正确传递原始请求 scheme 和客户端地址；否则传输守卫会拒绝请求，以避免将密钥暴露在不可信
  的 HTTP 链路上。
- 不要将包含真实凭证的 `data.json`、`.env`、浏览器数据或运行日志提交到 Git。
- 榜单快照、评论和转写存在来源限制；拆解页面会保留证据与风险说明，不应将推断内容表述为平台事实。

### HTTPS 反向代理与 LLM 密钥

默认 `uv run WebGUI.py` 直接监听 8899，不适合让远程客户端提交 LLM 密钥。远程管理必须先在
受控边缘代理终止 HTTPS，再只允许固定反代地址向 Uvicorn 传递 forwarded headers。本机反代可用：

```powershell
uv run uvicorn WebGUI:app --host 127.0.0.1 --port 8899 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

将 `--forwarded-allow-ips` 替换为实际的固定反代 IP；严禁使用 `*`。边缘代理必须先剥离客户端
自带的 `Forwarded` 和 `X-Forwarded-*` headers，再重写由代理可信计算的 scheme 与 client 地址。
只有这条受信任链路可以让应用的 HTTPS/loopback 密钥传输守卫安全地判定请求来源。

## 项目结构

```text
WebGUI.py                FastAPI Web GUI 入口（默认使用方式）
GUI.py                   桌面 GUI 入口
main.py                  命令行采集入口（CrawlerFactory 分发）

agents/                  抖音/红果分析、剧目下载、评论抓取等后台代理
analysis/                ASR 转写、LLM 提供者、今日榜单、数据分析、周报
audit/                   安全与业务审计
base/                    采集层抽象基类（爬虫、存储、登录、客户端）
cache/                   缓存系统
config/                  全局与平台配置
core/                    事件总线、采集任务定义
creative/                选题、拆解、创作项目、版本与交付导出
database/                数据库 ORM 与会话层
identity/                本地账号、角色、权限、会话
libs/                    JS 反检测脚本等资源
media_platform/          各内容平台采集实现
model/                   Pydantic 数据模型
organization/            组织架构（部门）
proxy/                   代理 IP 池
rag/                     RAG 索引与检索
runtime/                 任务管理和工作进程
scripts/                 迁移与运维脚本
screenplay_review/       AI 剧本评审
screenplay_optimization/ 剧本优化草稿
store/                   数据存储实现（csv/db/json/sqlite/mongodb/excel）
system_database/         PostgreSQL 系统数据层（Alembic 迁移、健康检查）
templates/ + static/     Web GUI 页面与静态资源
tools/                   浏览器启动、CDP、异步文件写入等工具
web/                     页面路由、导航、页面访问控制
workflow/                任务池、项目成员、审批、状态机、流转
writer_agent/            独立短剧/漫画剧写作教练服务
```

## 相关文档

- [短剧选品平台 PRD 实施文档](improve/壹凡短剧选品平台PRD实施文档.md)
- [短剧选品平台改进方案](improve/壹凡短剧选品平台改进方案.md)
- [角色职能工作流设计](docs/superpowers/specs/2026-07-22-role-responsibility-workflow-design.md)
- [PostgreSQL 系统基座实施计划](docs/superpowers/plans/2026-07-27-postgresql-system-foundation.md)
- [基于剧目转写的个性化拆解设计](docs/superpowers/specs/2026-07-20-transcript-driven-breakdown-design.md)
- [Excel 导出指南](docs/excel_export_guide.md)
- [数据存储指南](docs/data_storage_guide.md)
- [项目文档索引](docs/index.md)

## 免责声明

本项目仅供学习、研究及获得权利人明确许可的场景使用。使用者必须遵守目标平台服务条款、数据授权范围以及所在地法律法规，不得将本项目用于未经授权的大规模采集、隐私侵害、账号攻击或其他违法用途。因不当使用产生的责任由使用者自行承担。

本项目基于开源 MediaCrawler 演进，原项目及相关组件的版权和许可仍归各自权利人所有。详见 [LICENSE](LICENSE)。
