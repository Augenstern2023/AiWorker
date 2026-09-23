# AiWorker 全站 UI 重构设计规格

> 状态：已批准方向（Operate + Monitor、Command / Inspect、数据密集工作台）
> 范围：在既有 FastAPI + Jinja2 + Bootstrap 5 + Alpine.js 中完成全站工作流页面重构，不改变 API、字段、权限和路由。

## 1. 目标与边界

本轮解决“只有 `/tasks` 和 Beats 页面发生明显变化，其他页面仍像旧页面”的问题。重构覆盖四类页面：

- **工作台壳**：统一侧栏、页面标题、面包屑/项目上下文、状态反馈、跳转与移动端导航。
- **创作项目页**：Brief、Characters、Outlines、Beats、Scripts 使用同一套项目上下文和 Command / Inspect 编辑结构。
- **选题数据页**：Topics、Hits、Dramas、Ranks 使用筛选工具栏、结果列表/详情双层信息架构和可识别的加载/空/错误状态。
- **Prompt 页面**：模板中心与模板效果从“功能卡片”改为步骤上下文 + 左侧资源导航 + 右侧内容/分析面板。

不引入新框架、构建链、图标库或业务接口；不修改保存 payload、权限判断、状态机和数据库 schema。

## 2. 统一视觉系统

### 2.1 视觉姿态

深色、低装饰、内容优先的生产工作台。背景用画布/面板/抬升三级表面建立层级；蓝色只用于主要交互和焦点；绿/黄/红只表示成功、警告、失败。避免渐变、玻璃拟态、装饰性统计卡和大面积圆角。

### 2.2 令牌与组件

继续使用 `static/css/workspaces.css`，新增或整理以下语义族：

- 表面：`--surface-canvas`、`--surface-panel`、`--surface-raised`、`--surface-input`
- 内容：`--color-text`、`--color-muted`、`--color-border`
- 语义：`--color-accent`、`--color-success`、`--color-warning`、`--color-danger`
- 几何：`--radius-control`、`--radius-panel`、`--space-1` 至 `--space-6`
- 组件类：`.workspace-page-header`、`.workspace-context-bar`、`.workspace-toolbar`、`.workspace-split`、`.workspace-list-pane`、`.workspace-inspector`、`.workspace-empty`、`.workspace-state`、`.workspace-section-label`

组件必须使用真实内容建立层级，不能把每个区块包装为同等权重的卡片。

## 3. 页面结构

### 3.1 全局工作台壳

`base.html` 提供：

1. 可键盘触达的跳转链接和 `main` landmark。
2. 桌面侧栏、移动端 topbar/backdrop；当前路由高亮。
3. 页面上下文：当前项目、阶段、角色或数据域；不依赖前端二次请求才能理解页面用途。
4. 统一 live region、toast、权限错误容器。
5. 资源按路由加载；大脚本不进入基础模板。

页面模板通过 `workspace-page-header`、`workspace-context-bar` 和 `workspace-toolbar` 组合标题、说明、返回、刷新、主要操作。

### 3.2 创作项目页：Command / Inspect

适用于 Brief、Characters、Outlines、Beats、Scripts：

- 顶部：项目名称/编号、阶段导航、返回项目列表、保存/生成/锁定等动作。
- 左侧（桌面宽度 >= 1000px）：项目阶段导航和当前文档/集数导航；移动端改为可折叠的 horizontal scroller。
- 中间：当前阶段的核心编辑内容，首屏只保留当前任务；复杂表格转为条目/段落/场景卡。
- 右侧：状态、版本、连续性、生成任务和下一步动作；在窄屏移动到内容下方。
- 编辑/生成失败、未加载、无项目、权限不足必须显示可操作的状态块。
- 保留所有现有 `x-model`、`@click`、`x-for`、`id` 与 data 属性，视觉包裹层不得改变行为选择器。

### 3.3 选题数据页：Filter / Inspect

适用于 Topics、Hits、Dramas、Ranks：

- 页面头部直接显示数据域、更新时间与主动作。
- 工具栏包含搜索/平台/时间/状态等已有筛选字段，并在移动端自动换行。
- 主区使用“结果列表 + 详情检查区”布局；详情可由当前选中项或 Bootstrap offcanvas/modal 承载。
- 表格仍作为批量/高密度查看的语义基础，但不再让所有字段同时占据首屏；次要字段折叠到详情。
- 空数据说明下一步可执行动作；加载状态使用 skeleton 或明确的 loading 文案；错误状态提供重试入口。
- 不新增虚假指标或虚构数据。

### 3.4 Prompt 页面：Step / Library / Effects

- 顶部统一显示项目和当前创作步骤。
- 步骤选择与集数选择位于紧凑上下文工具栏，不再占据大型说明卡首屏。
- 主区采用资源导航 + 内容面板，个人/项目/市场/效果作为可切换面板，不重复使用三张同权重宣传卡。
- 模板列表展示名称、版本、状态、更新时间和使用动作；编辑/导入/提交审核等动作保持现有 API。
- 效果页保留真实统计字段和空状态，不渲染假的 KPI。

## 4. 交互、状态与响应式

- 所有按钮有明确 `type`、可见 focus ring、`aria-label` 或可见文本；可折叠区使用原生 `<details>` 或 Bootstrap 状态。
- 页面加载时只初始化当前路由需要的模块；轮询必须可暂停、不可重叠、离开页面销毁。
- 断网/接口错误：就地显示错误摘要和“重试”；不清空用户正在编辑的字段。
- 空数据：说明原因并给出创建、筛选或返回上一步的操作。
- `1440×900`：桌面双/三栏可稳定阅读，不出现无意义横向滚动。
- `1024×768`：保留主内容和操作，右侧检查区可下移或折叠。
- `390×844`：单列、工具栏换行、列表项不截断主要标题，编辑动作可触达，不出现页面级横向滚动。
- `prefers-reduced-motion: reduce` 下关闭非必要过渡。

## 5. 性能约束

- 继续使用本地 Bootstrap/Bootstrap Icons/Alpine；不引入新依赖。
- 通过页面模块边界避免 `/tasks` 和选题页加载无关创作脚本。
- 页面脚本保持 `defer` 或底部加载，静态版本使用进程级缓存。
- 目标不是人为压缩源代码，而是减少路由不必要的下载、解析与轮询。
- 资源契约测试覆盖模板脚本归属、核心令牌、脚本体积上限和重型库只在目标路由加载。

## 6. 验收标准

1. 选择、策划、写作、Prompt 页面均具备新的页面头部、上下文工具栏和内容层级，不再是旧的“标题 + 一排同权重卡片/大表格”。
2. Brief、Characters、Outlines、Beats、Scripts 共用 Command / Inspect 布局；现有保存、生成、下载、锁定、版本和审核返回操作可用。
3. Topics、Hits、Dramas、Ranks 的筛选、列表、详情、分页/批量和状态操作可用。
4. Prompt 中心和效果页的步骤切换、面板切换、模板管理、效果查看可用。
5. 三种视口检查通过；键盘 focus、跳过链接、live region、错误/空/加载状态可识别。
6. 相关定向测试通过，且原有浏览器测试没有因 DOM 结构改动回归。
7. 未改变路由/API/字段/权限；没有新增框架、依赖或构建链。

## 7. 风险与回退

重构只添加包裹层、类名和布局 CSS，保留 Alpine 绑定及现有 id/data 属性。若某页的浏览器测试依赖特定结构，优先恢复该页包裹层而不是修改业务脚本。所有页面族使用独立提交，便于按族回退。
