# 分集大纲折叠卡片实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将分集大纲横向表格改为可搜索、可批量展开收起、草稿可编辑且锁定只读的纵向剧集卡片列表。

**架构：** 保持现有 FastAPI、Jinja、Alpine 数据源与保存接口不变。页面专属 `outlines.js` 负责搜索和折叠视图状态，`project_outlines.html` 负责语义化卡片结构，`workspaces.css` 提供桌面双栏、移动单栏与深色交互样式；Playwright 使用真实模板、真实页面脚本和真实 Alpine 验证行为。

**技术栈：** Jinja2、Alpine.js、Bootstrap 5、原生 CSS、pytest、Playwright

---

## 文件结构

| 文件 | 变更 | 职责 |
|---|---|---|
| `tests/test_outline_episode_cards_browser.py` | 创建 | 使用真实模板、真实大纲页面脚本和 Alpine 验证搜索、折叠、编辑、锁定及响应式布局 |
| `static/js/writing/outlines.js` | 修改 | 提供大纲搜索、单卡与批量折叠、上下文重置方法 |
| `static/js/common/tasks.js` | 修改 | 为大纲页面视图状态提供稳定初始值 |
| `templates/common/workspace/project_outlines.html` | 修改 | 用纵向折叠卡片替换大表格并完整保留字段、状态和操作 |
| `static/css/workspaces.css` | 修改 | 提供深色卡片、双栏字段、截断、内部滚动、hover/focus 与移动端样式 |

### 任务 1：大纲搜索与折叠状态

**文件：**
- 创建：`tests/test_outline_episode_cards_browser.py`
- 修改：`static/js/writing/outlines.js`
- 修改：`static/js/common/tasks.js`

- [ ] **步骤 1：编写页面状态的失败测试**

在新测试文件中加入项目版权头，并通过 Playwright 载入真实 `outlines.js`。测试直接实例化脚本注册的模块方法，使用以下固定条目：

```python
OUTLINE_ITEMS = [
    {"episode_number": 1, "content": {"core_event": "密室账本曝光"}},
    {"episode_number": 2, "content": {"core_event": "董事会逼宫"}},
    {"episode_number": 12, "content": {"core_event": "终局反击"}},
]
```

断言：

```python
assert evaluate("state.filteredProjectOutlines().map(item => item.episode_number)") == [1, 2, 12]
fill_query("2")
assert filtered_episode_numbers() == [2, 12]
fill_query("密室")
assert filtered_episode_numbers() == [1]
toggle_episode(1)
assert expanded_episode_numbers() == [1]
```

另加用例证明批量展开/收起只影响当前筛选结果，并证明相同上下文刷新保留视图状态、项目或版本上下文变化会调用重置。

- [ ] **步骤 2：运行测试并确认因模块方法缺失而失败**

运行：

```powershell
uv run pytest tests/test_outline_episode_cards_browser.py -q
```

预期：FAIL，错误指出 `filteredProjectOutlines`、`toggleOutlineEpisode` 或大纲模块工厂不存在，而不是 Playwright 启动错误。

- [ ] **步骤 3：实现最小视图状态和方法**

在 `creativeWorkspace` 初始状态中增加：

```javascript
outlineQuery: '',
expandedOutlineEpisodes: {},
outlineCardContextKey: '',
```

将 `outlines.js` 改为页面专属模块，提供：

```javascript
filteredProjectOutlines() {
    const items = this.currentProjectOutlines();
    const query = String(this.creativeWorkspace.outlineQuery || '').trim().toLocaleLowerCase();
    if (!query) return items;
    return items.filter((item) => {
        const episode = String(Number(item.episode_number || 0));
        const event = String(item.content?.core_event || '').toLocaleLowerCase();
        return episode.includes(query) || event.includes(query);
    });
},
outlineEpisodeExpanded(episodeNumber) {
    return Boolean(this.creativeWorkspace.expandedOutlineEpisodes[String(Number(episodeNumber))]);
},
toggleOutlineEpisode(episodeNumber) {
    const key = String(Number(episodeNumber));
    this.creativeWorkspace.expandedOutlineEpisodes[key] = !this.outlineEpisodeExpanded(episodeNumber);
},
```

批量方法遍历 `filteredProjectOutlines()`；重置方法清空查询和展开对象；上下文同步方法以项目 ID、锁定 Brief、人物版本和大纲批次组成稳定键，仅在键变化时重置。

- [ ] **步骤 4：运行测试确认通过**

运行：

```powershell
uv run pytest tests/test_outline_episode_cards_browser.py -q
```

预期：任务 1 的状态测试全部 PASS。

- [ ] **步骤 5：提交任务 1**

```powershell
git add tests/test_outline_episode_cards_browser.py static/js/writing/outlines.js static/js/common/tasks.js
git commit -m "feat: add outline card view state (task 1/3)"
```

### 任务 2：剧集卡片模板与深色响应式样式

**文件：**
- 修改：`tests/test_outline_episode_cards_browser.py`
- 修改：`templates/common/workspace/project_outlines.html`
- 修改：`static/css/workspaces.css`

- [ ] **步骤 1：扩展浏览器测试并确认要捕获的破坏**

测试加载真实 `project_outlines.html`、`outlines.js`、Bootstrap CSS、工作台 CSS 和 Alpine。它必须在以下生产破坏发生时失败：恢复表格、删除任一字段、状态/操作移入折叠区、锁定版本仍可编辑、移动端重新出现横向滚动。

加入断言：

```python
assert page.locator(".outline-episode-card").count() == 3
assert page.locator("table").count() == 0
assert page.locator(".outline-episode-details:visible").count() == 0
page.get_by_role("button", name="展开第 1 集").click()
assert page.locator(".outline-episode-details:visible").count() == 1
assert page.get_by_text("核心事件", exact=True).count() == 1
assert page.get_by_text("结尾悬念", exact=True).count() == 1
```

继续验证：搜索 `董事会` 只显示第 2 集；搜索无结果显示独立空状态；“全部展开/全部收起”工作；草稿六个文本框可编辑且保存被调用；将条目状态改为 `locked` 后文本框禁用且保存隐藏；剧本完成、Beats 更新和向右箭头在收起与展开状态下都可见。

响应式用例分别设置 `1280x900` 和 `390x844`，断言桌面 `.outline-field-grid` 为两列、移动端为一列，且 `document.documentElement.scrollWidth <= window.innerWidth`。

- [ ] **步骤 2：运行测试并确认因卡片结构缺失而失败**

运行：

```powershell
uv run pytest tests/test_outline_episode_cards_browser.py -q
```

预期：FAIL，首个界面失败为 `.outline-episode-card` 数量为 0 或搜索控件不存在。

- [ ] **步骤 3：用卡片列表替换大表格**

在保留顶部标题、下载/生成/锁定按钮和人物关系版本模块原位的前提下：

- 增加 `#outline-episode-search`、`#outline-collapse-all`、`#outline-expand-all`。
- 使用 `filteredProjectOutlines()` 渲染 `.outline-episode-card`。
- 卡片头部始终渲染集数、截断核心事件、剧本完成、Beats 更新、保存和 Beats 箭头。
- 使用带 `aria-expanded` 与动态 `aria-label` 的真实按钮控制详情。
- 展开区用 `.outline-field-grid` 完整绑定 `core_event`、`conflict`、`character_progress`、`highlight`、`reversal`、`cliffhanger`。
- 所有文本框继续使用 `:disabled="!creativeResourceEditable(item)"`；保存继续使用 `x-show="creativeResourceEditable(item)"` 和 `saveProjectEpisode(item)`。
- 空查询结果与无大纲数据使用不同文案。

- [ ] **步骤 4：实现深色、双栏与移动端样式**

在 `workspaces.css` 中增加以 `.outline-` 开头的局部样式：

```css
.outline-episode-card { background: #1e222c; border: 1px solid #30394a; }
.outline-episode-card:hover { border-color: rgba(83, 155, 255, .62); }
.outline-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.outline-field-textarea { max-height: 180px; overflow-y: auto; resize: vertical; }
@media (max-width: 767.98px) {
    .outline-field-grid { grid-template-columns: minmax(0, 1fr); }
}
```

同时实现预览省略号、操作区换行、可见焦点、chevron 状态和 reduced-motion 兼容；不得使用全局选择器改变其他工作区。

- [ ] **步骤 5：运行卡片浏览器测试确认通过**

运行：

```powershell
uv run pytest tests/test_outline_episode_cards_browser.py -q
```

预期：全部 PASS，桌面与移动端均无横向溢出。

- [ ] **步骤 6：提交任务 2**

```powershell
git add tests/test_outline_episode_cards_browser.py templates/common/workspace/project_outlines.html static/css/workspaces.css
git commit -m "feat: replace outline table with episode cards (task 2/3)"
```

### 任务 3：回归、视觉检查与交付验证

**文件：**
- 修改：仅在验证发现问题时修改任务 1、2 中的文件

- [ ] **步骤 1：运行定向功能测试与现有前端基线**

运行：

```powershell
uv run pytest tests/test_outline_episode_cards_browser.py tests/test_data_management_search_browser.py -q
```

预期：新测试和既有 Playwright 基线全部 PASS。

- [ ] **步骤 2：运行相关静态 UI 测试并记录既有数据库阻塞**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_webgui_ui_changes.py -q
```

如果测试 PostgreSQL 仍不可用或未到 Alembic head，记录为基线环境阻塞；不得迁移或修改数据库。若能进入测试执行，则所有与 outlines 相关的断言必须通过。

- [ ] **步骤 3：运行格式与文件头检查**

运行：

```powershell
uv run pre-commit run --files tests/test_outline_episode_cards_browser.py static/js/writing/outlines.js static/js/common/tasks.js templates/common/workspace/project_outlines.html static/css/workspaces.css
```

预期：退出码 0；新 Python 测试文件通过版权头检查。

- [ ] **步骤 4：生成桌面和移动端截图并人工检查**

复用浏览器测试中的真实页面装配函数，分别生成 `.tmp/outline-cards-desktop.png` 与 `.tmp/outline-cards-mobile.png`。检查：顶部既有模块位置不变；默认收起；展开字段无裁切；状态与操作始终可见；长文本内部滚动；移动端单栏且无横向滚动。

- [ ] **步骤 5：检查最终差异并提交验证修正**

运行：

```powershell
git diff --check
git status --short
```

若验证产生代码修正，重新运行步骤 1 和步骤 3，再提交：

```powershell
git add tests/test_outline_episode_cards_browser.py static/js/writing/outlines.js static/js/common/tasks.js templates/common/workspace/project_outlines.html static/css/workspaces.css
git commit -m "test: verify outline episode card workflow (task 3/3)"
```

最终工作树只包含计划内文件的提交，不包含 `.tmp` 截图或数据库变更。
