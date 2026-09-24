# Beats 三场次限制与聚焦式编辑器实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 Beats 的每集场次上限统一为 3，并把单集、全集和优化草稿改造成以场景、人物、转折、结尾钩子为首屏核心的信息层级。

**架构：** 复用现有 `validate_beats` 作为所有服务端写入、锁定和采纳入口的共同防线；生成提示词及内置模板只同步规则文本和参数。Jinja2/Alpine 页面保留原 payload、权限和资源状态，仅改变 Beats 展示层级与前端数量状态；优化草稿使用独立 JS 生成相同的核心字段编辑结构。

**技术栈：** Python 3.11、FastAPI、Jinja2、Bootstrap 5、Alpine.js、原生 CSS/JavaScript、pytest。

---

## 文件职责与边界

- 修改 `creative/script_service.py`：统一 1..3 Beats 数量校验，并保留 legacy 读取兼容和序号/字段校验。
- 修改 `creative/prompt_templates.py`、`creative/prompt_registry.py`：生成规则和内置模板参数不超过 3。
- 修改 `creative/free_mode_service.py`、`screenplay_optimization/api.py`：确保自由模式、优化草稿的写入/锁定/采纳路径调用共同校验。
- 修改 `templates/common/workspace/project_beats.html`：单集三栏编辑结构、全集卡片摘要、默认折叠详细字段和连续性区。
- 修改 `static/js/common/creative.js`、`static/js/writing/beats.js`：数量计算、超限状态、按钮禁用和场次导航辅助，不改变 API payload。
- 修改 `static/js/writing/optimization_draft.js`：优化 Beats 编辑器核心字段、详细字段折叠、三场次上限和保存/采纳状态。
- 修改 `static/css/workspaces.css`：核心字段语义、长文本、折叠、超限和 1440/1024/390 响应式布局。
- 修改相关 `tests/`：后端边界、静态模板/脚本契约和已有浏览器测试的必要断言。

## 全局约束

- 不修改主工作区；所有代码位于 `.workspace/beats-three-scene-focus-20260924`。
- 不引入依赖，不改变 Beats 数据结构、版本语义、锁定状态机或 API payload。
- 历史超过 3 场的数据可读、不可静默截断；显示 `N/3` 警告并禁用保存、锁定、采纳，删除/合并恢复后可操作。
- 空白自由草稿允许 `beats: []` 暂存，但完成、锁定、采纳前必须满足现有字段校验及 1..3 约束。
- 首屏按“场景、人物、转折、结尾钩子”排序；行动目标、阻力、情绪变化、局面变化、代价、对抗策略默认折叠，展开后仍可编辑。
- 不删除连续性编辑能力；连续性检查默认折叠。模板元数据缺失统一显示“经典兼容模板”。
- 仅运行与改动直接相关的测试，并以真实命令输出作为完成证据。

### 任务 1：统一 Beats 业务约束

**文件：** `creative/script_service.py`、`creative/prompt_templates.py`、`creative/prompt_registry.py`、`creative/free_mode_service.py`、`screenplay_optimization/api.py` 及对应后端测试。

- [ ] 在现有校验测试中覆盖 1、2、3 通过，4 拒绝，并覆盖 legacy、自由模式和优化草稿保存/采纳入口。
- [ ] 将共享 validator 的上限改为 3；允许只读 legacy 读取但所有写入/锁定/采纳拒绝超限且不截断数据。
- [ ] 将单集/批量 prompt 和内置模板参数同步为 1-3，保留现有字段要求和重试流程。
- [ ] 运行相关 pytest，确认基线测试不回归并提交本任务。

### 任务 2：单集与全集 Beats 聚焦式 UI

**文件：** `templates/common/workspace/project_beats.html`、`static/js/common/creative.js`、`static/js/writing/beats.js`、`static/css/workspaces.css` 及静态/浏览器测试。

- [ ] 为单集页增加可访问的场次导航和当前场次编辑区，首屏展示场景、人物、转折、结尾钩子。
- [ ] 把六个分析字段放入默认关闭的 details/summary 折叠区，连续性区默认关闭且保留原编辑控件。
- [ ] 把全集表格改为每集卡片摘要，只展示状态、数量 `N/3`、场景、人物、转折、结尾钩子和单集入口。
- [ ] 增加实时数量状态、超限 aria-live 提示、保存/锁定禁用逻辑和第 3 个场次后新增禁用；历史超限不删除。
- [ ] 加入核心字段色彩/字号/长文本与响应式布局样式，运行模板和浏览器测试并提交本任务。

### 任务 3：优化草稿一致性 UI

**文件：** `static/js/writing/optimization_draft.js`、必要的 `static/css/workspaces.css` 和优化草稿测试。

- [ ] 将 Beats 优化编辑器重排为核心字段直显、详细分析默认折叠、连续性默认折叠。
- [ ] 限制新增至 3 个场次；数量超过 3 时显示 `N/3` 错误并禁用保存及锁定采纳，恢复合规后恢复操作。
- [ ] 保持比较预览、字段名称、保存和采纳 API payload 不变；长文本使用可读的自动高度控件。
- [ ] 运行优化草稿相关测试并提交本任务。

### 任务 4：整体验证与验收

**文件：** 相关测试、验证记录文件（不修改无关源码）。

- [ ] 增加/修正静态契约测试，确认四个核心字段默认可见、详细字段默认折叠、连续性默认折叠和历史模板文案正确。
- [ ] 运行定向后端、静态和已有浏览器测试；必要时修复真实失败。
- [ ] 在新端口启动隔离工作区服务，验证 3 场可编辑与 4 场历史阻断、三种视口无页面级横向滚动，并确认 8899 服务仍可用。
- [ ] 记录测试命令、结果和未解决事项，完成最终审查前不得宣称完成。

