# 剧目选中后再次检索修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 再次检索时立即清除旧剧目选择，并防止缓存脚本与过期异步请求恢复旧状态。

**架构：** 复用页面现有的静态资源版本机制；在 Alpine 剧目状态中加入单调递增的搜索/剧集请求序号，只允许当前请求提交状态。剧集读取使用独立的 `seriesLoading`，新搜索同步使旧剧集请求失效。

**技术栈：** Alpine.js、Jinja2、Playwright、pytest

---

### 任务 1：建立浏览器回归

**文件：**
- 修改：`tests/test_hongguo_provider_browser.py`

- [x] 编写 Playwright 测试：首次搜索并选中剧目，挂起第二次搜索，断言旧选中区域立即隐藏；释放请求后只出现新结果。
- [x] 编写渲染测试：`selection/dramas.html` 生成的 `dramas.js` URL 包含传入的 `asset_version`。
- [x] 运行 `uv run pytest tests/test_hongguo_provider_browser.py -q -k "research or asset_version"`，确认测试因缓存参数和请求隔离缺失而失败。

### 任务 2：实现缓存失效和请求隔离

**文件：**
- 修改：`templates/selection/dramas.html`
- 修改：`static/js/common/tasks.js`
- 修改：`static/js/selection/dramas.js`
- 测试：`tests/test_hongguo_provider_browser.py`

- [x] 为剧目页脚本 URL 增加 `?v={{ asset_version }}`。
- [x] 在剧目状态中增加 `searchRequestId` 和 `seriesRequestId`。
- [x] 搜索开始时递增两个序号并同步清空选择；搜索响应仅在序号仍为当前值时写入。
- [x] 剧集请求使用 `seriesLoading`，且仅当前请求能写入剧集、错误和结束状态。
- [x] 运行浏览器回归，确认新增测试通过。

### 任务 3：回归验证

**文件：**
- 验证：`static/js/selection/dramas.js`
- 验证：`templates/common/workspace/selection_dramas.html`

- [x] 运行 `uv run pytest tests/test_hongguo_provider_browser.py tests/test_role_page_content.py -q`（相关剧目测试通过；该文件另有 3 个既有无关失败）。
- [x] 运行相关静态契约测试和 `uv run pre-commit run --all-files`。
- [x] 用 Playwright 在桌面宽度验证选择、再次检索、加载和新结果四个状态无重叠。
