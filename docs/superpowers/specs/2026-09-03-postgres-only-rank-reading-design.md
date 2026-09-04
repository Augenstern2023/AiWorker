# 榜单仅从 PostgreSQL 读取与缺失提醒设计

## 背景

集中式榜单表已经由 `0016_rank_storage` 建立，历史榜单也已完成回填。但当前生产代码仍存在直接构造 `TodayRankService(root=...)` 的入口，这会默认使用 `RankFileSource`，从 `data/**/rank/*.json` 和 `E:\hg\exports` 读取本地榜单。WebGUI 因此可能展示本地当天数据，即使 PostgreSQL 尚未入库。

本次变更只收紧生产读取路径并增加前端提醒，不调整数据库结构，也不迁移本地文件。

## 目标

- WebGUI、周报、选题和榜单 Excel 等生产消费者统一以 PostgreSQL 为榜单唯一事实来源。
- PostgreSQL 不可用时明确失败，禁止回退到本地 JSON、CSV 或本地响应缓存。
- 用户查看某个平台的最新榜单时，如果 PostgreSQL 没有上海时区当天快照，弹出“请先抓取”提醒。
- 历史日期查询缺失时不弹当天抓取提醒。

## 非目标

- 不修改 `rank_snapshots`、`rank_entries` 或 Alembic revision。
- 不在弹窗中启动任务，也不自动抓取或写入数据库。
- 不删除本地榜单文件；它们仍可作为迁移、审计和故障调查材料。
- 不改变榜单抓取与入库任务本身的业务流程。

## 方案比较

### 方案 A：只修改 WebGUI 初始化

把 WebGUI 中的 `TodayRankService(root=...)` 改为 `TodayRankService.from_environment(...)`。改动最小，但周报、选题和 Excel 等其他生产入口仍可能读取本地数据，无法满足“严格从 PostgreSQL 读取”。

### 方案 B：生产入口统一使用 PostgreSQL，并显式隔离文件源（采用）

生产入口全部通过 PostgreSQL 工厂创建榜单服务。本地文件源只能通过明确的测试或迁移入口注入，数据库模式禁用本地响应文件缓存。这样既保留迁移解析能力，也能从结构上阻止生产代码误用本地榜单。

### 方案 C：彻底删除本地榜单解析代码

约束最强，但会破坏历史回填、源文件审计和大量解析测试，不符合本次需求，也没有必要。

## 服务端设计

### 数据源边界

`TodayRankService` 继续依赖 `RankSnapshotSource` 接口，但不再隐式选择本地文件源：

- `TodayRankService.from_environment()` 创建 `SystemDatabase` 和 `RankRepository`，用于生产。
- 文件解析只通过显式文件源工厂或显式 `snapshot_source=RankFileSource(...)` 使用，供迁移与测试调用。
- 生产调用点不得直接创建文件源服务。

需要核对并修改的生产调用点包括：

- WebGUI 今日榜单 API 和每日状态检查；
- 周报默认榜单服务；
- 选题服务默认榜单服务；
- 榜单 Excel 导出默认榜单服务。

### 缓存约束

PostgreSQL 模式不读取 `.runtime/rank-cache` 中的本地响应文件。允许保留进程内短期响应缓存，但每次命中前必须用 PostgreSQL revision 校验数据版本。数据库查询失败时返回现有的 `503 榜单数据库暂不可用`，不得使用本地文件或旧缓存兜底。

文件源模式只用于测试、迁移或显式离线工具，可继续使用文件响应缓存，以维持现有解析测试和工具能力。

### 当天状态

API 保持现有响应字段：

- `business_date`：按 `Asia/Shanghai` 计算的当天日期；
- `has_today_snapshot`：PostgreSQL 快照日期中是否存在 `business_date`。

即使接口为了方便仍返回数据库中最近一期榜单，只要最近一期不是当天，`has_today_snapshot` 就必须为 `false`。

## 前端交互设计

前端成功读取最新榜单后，满足以下全部条件时显示 warning Toast：

- 请求未指定历史日期；
- `has_today_snapshot === false`；
- 响应包含 `business_date`。

文案：

- 标题：`今日榜单尚未入库`
- 内容：`红果今日榜单暂无 PostgreSQL 数据，请先抓取。` 或 `抖音今日榜单暂无 PostgreSQL 数据，请先抓取。`

弹窗复用现有 `window.aiworkerUi.showToast`，具备关闭按钮和辅助技术播报，不增加确认按钮或“立即抓取”动作。同一页面生命周期内，同一平台和业务日期只提示一次，避免切换标签或缓存命中时反复打扰；日期变化后可以再次提示。

选择明确的历史日期但没有数据时，继续展示现有空状态，不弹“今日榜单”提醒。数据库不可用时继续进入现有错误状态，不把连接故障误报为“请先抓取”。

模板中的“默认展示本地最新榜单快照”改为“默认展示 PostgreSQL 最新榜单快照”，避免误导用户。

## 数据流

1. 浏览器请求 `/api/rank/today`。
2. WebGUI 注入 PostgreSQL 模式的 `TodayRankService`。
3. 服务先向 PostgreSQL 查询榜单 revision，再从 `RankRepository` 读取快照与条目。
4. 服务返回最新数据库快照以及 `business_date`、`has_today_snapshot`。
5. 前端渲染榜单；若最新请求缺少当天快照，则显示只读 warning Toast。
6. PostgreSQL 连接失败时返回 503，流程终止，不访问本地榜单文件。

## 测试策略

### 服务端

- WebGUI 生产服务由 PostgreSQL 工厂创建，不再默认使用 `RankFileSource`。
- 在本地目录存在更新榜单、PostgreSQL 只有旧榜单时，API 仍返回数据库旧榜单并标记 `has_today_snapshot=false`。
- PostgreSQL 不可用时返回 503，且不读取本地文件缓存。
- 周报、选题和 Excel 的默认生产服务都从 PostgreSQL 读取。
- 显式文件源模式仍可供解析测试使用。

### 前端

- 最新请求缺少当天快照时调用 warning Toast，文案包含平台名和“请先抓取”。
- 当天快照存在时不弹窗。
- 指定历史日期缺失时不弹窗。
- 并发请求中，过期响应不得触发弹窗。
- 同一平台和业务日期只提示一次。

### 回归

- 今日榜单服务与 API 测试；
- 前端并发与页面契约测试；
- 周报、选题、Excel 导出测试；
- PostgreSQL 集中榜单相关测试。

## 发布与验收

代码部署后必须重启 WebGUI，使进程不再保留旧的本地文件服务实例。重启后：

- 当天 PostgreSQL 有数据的平台显示当天榜单且不提醒；
- 当天 PostgreSQL 无数据的平台最多弹一次“请先抓取”；
- 新增或修改本地 JSON、CSV 不会改变接口结果；
- PostgreSQL 不可用时接口返回 503。

2026-09-03 的红果快照补录是独立的数据操作，不属于本代码变更。补录前页面会按本设计提醒；补录并刷新后提醒消失。
