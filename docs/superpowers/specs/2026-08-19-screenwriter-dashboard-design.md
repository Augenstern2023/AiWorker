# 编剧工作概览设计

## 目标

为编剧提供只反映本人创作工作的独立概览，避免用候选选题、评论采集、素材转写和分析任务等选品指标衡量编剧工作。

## 页面与数据

- 新页面：`/writing/dashboard`，权限为 `page.writing`。
- 数据源仅为 `GET /api/creative/projects?participating_only=true` 和 `GET /api/workflow/tasks`；前者明确限制为当前账号的有效项目成员关系，避免把仅可预览的已设立项目计入参与项目。
- 项目数使用当前账号可见的参与项目数量。
- 任务只统计 `outline`、`beats`、`script`，并且 `assignee_user_id` 必须是当前账号。
- 待处理任务包含 `assigned` 和 `returned`；进行中为 `in_progress`；已完成为 `completed`。
- 三个阶段分别展示 `completed / 非 cancelled 任务总数` 和百分比。
- 最近任务按更新时间倒序展示，附项目、阶段、状态和进入项目入口。

## 交互与降级

项目与任务请求独立完成；任何一个请求失败时仍保留另一个请求的可用数据，并显示具体的局部错误。空数据使用明确的空状态。布局在桌面为四列指标卡，平板两列，手机单列。

## 导航与兼容

- 编剧工作台新增“工作概览”，并保留“编剧项目”。
- 选品工作台原“工作概览”改名为“选品概览”。
- 默认编剧角色和既有编剧角色均获得 `/writing/dashboard` 页面授权。
- 新页面不请求分析 API，因此不依赖 `analytics.read`。
