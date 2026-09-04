# 全集剧情概览实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在全剧大纲页面新增可生成、编辑、保存并跟随大纲版本的六项全集剧情概览。

**架构：** 使用 `series_overview` 六字段对象作为大纲批次公共元数据，沿用现有 `series_arc` 的 JSON 快照模式写入批次内全部分集记录。新大纲在阶段弧线请求中同步生成概览；旧草稿通过独立后台任务汇总当前分集；批次级 PATCH 在一个事务中原子保存。前端在现有 Alpine 大纲页模块中维护独立编辑草稿，并在分集搜索区上方渲染响应式概览区。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、Alpine.js、Bootstrap、Playwright、pytest

---

## 文件结构

- 创建 `creative/outline_overview.py`：定义六字段契约、规范化和校验函数，供生成、仓储和下载共同使用。
- 修改 `creative/prompt_templates.py`：扩展阶段弧线提示词并增加旧大纲概览汇总提示词。
- 修改 `creative/script_service.py`：校验新大纲概览、生成旧批次概览，并支持断点续生成的概览传递。
- 修改 `creative/repository.py`：在创建、检查点、恢复、读取和批次级更新中维护 `series_overview`。
- 修改 `creative/phase4_worker.py`：持久化新大纲概览，并执行旧大纲的概览生成任务。
- 修改 `creative/schema.py`：增加批次级概览保存请求模型。
- 修改 `creative/api.py`：增加生成与保存端点，并在 Markdown 下载中输出一次概览。
- 修改 `static/js/common/tasks.js`：初始化概览页面状态。
- 修改 `static/js/common/creative.js`：识别概览后台任务的标签、权限类别和完成状态。
- 修改 `static/js/writing/outlines.js`：维护概览草稿、校验、生成、保存、收起和上下文同步。
- 修改 `templates/common/workspace/project_outlines.html`：在搜索栏上方渲染方案 A 的六项结构化概览。
- 修改 `static/css/workspaces.css`：增加概览两列/单列布局与全部交互状态样式。
- 修改 `tests/test_creative_phase4_generation.py`：覆盖生成契约、断点续生成、仓储原子更新、工作任务和下载。
- 修改 `tests/test_creative_phase4.py`：覆盖概览生成与保存 API、状态和权限约束。
- 修改 `tests/test_outline_episode_cards_browser.py`：覆盖概览区展示、编辑、校验、保存、旧数据生成入口、锁定态和响应式布局。

## 工作区说明

当前仓库是普通检出而非 linked worktree，并且 `creative/api.py`、`creative/repository.py`、`creative/prompt_templates.py` 已有用户未提交改动。隔离 worktree 不会包含这些正在使用的改动，回合末合并还会增加覆盖风险，因此本计划在当前工作区原地执行；所有补丁只改任务相关代码，提交时不得把既有用户改动混入功能提交。

### 任务 1：建立概览契约并随新大纲生成、恢复和持久化

**文件：**
- 创建：`creative/outline_overview.py`
- 修改：`creative/prompt_templates.py`
- 修改：`creative/script_service.py`
- 修改：`creative/repository.py`
- 修改：`creative/phase4_worker.py`
- 测试：`tests/test_creative_phase4_generation.py`

- [ ] **步骤 1：编写失败的生成契约测试**

增加 `valid_series_overview()` 测试夹具，并让阶段弧线响应包含：

```python
{
    "series_arc": "相识、对抗、合作",
    "stages": ["相识", "对抗", "合作"],
    "series_overview": valid_series_overview(),
}
```

断言 `generate_outline_bundle()` 返回规范化后的六项概览；缺字段、空字符串或非字符串时结构修复仍失败；断点续生成能带回已保存概览。

- [ ] **步骤 2：运行测试验证正确失败**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_creative_phase4_generation.py -k "outline and (overview or chunks or continues)" -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-generation-red
```

预期：FAIL，生成结果缺少 `series_overview` 或校验函数尚不存在。

- [ ] **步骤 3：实现共享契约和生成逻辑**

在 `creative/outline_overview.py` 定义：

```python
SERIES_OVERVIEW_FIELDS = (
    "core_event", "character_progress", "conflict",
    "reversal", "highlight", "cliffhanger",
)

def normalize_series_overview(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("全集剧情概览必须是对象")
    normalized = {key: str(value.get(key) or "").strip() for key in SERIES_OVERVIEW_FIELDS}
    missing = [key for key, text in normalized.items() if not text]
    if missing:
        raise ValueError(f"全集剧情概览缺少字段：{missing}")
    if any(not isinstance(value.get(key), str) for key in SERIES_OVERVIEW_FIELDS):
        raise ValueError("全集剧情概览字段必须是字符串")
    return normalized
```

扩展 `series_arc_messages()`，要求返回 `series_overview`。让 `_validate_series_arc()` 调用共享校验；让 `generate_outline_bundle()` 在新生成和恢复路径中返回概览。

- [ ] **步骤 4：编写失败的仓储与工作任务测试**

断言：

```python
assert all(
    item["content"]["series_overview"] == valid_series_overview()
    for item in repository.get_outline_batch(project_id, batch_id)["episodes"]
)
assert repository.find_resumable_outline_batch(project_id)["series_overview"] == valid_series_overview()
```

工作任务测试同时验证首次检查点和恢复后的全部分集持有同一概览。

- [ ] **步骤 5：运行仓储测试验证正确失败**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_creative_phase4_generation.py -k "worker and outline" -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-worker-red
```

预期：FAIL，分集 `content` 尚未写入 `series_overview`。

- [ ] **步骤 6：实现创建、检查点和恢复持久化**

为 `create_outline_batch()` 和 `save_outline_batch_checkpoint()` 增加可选关键字参数 `series_overview`；有值时先规范化，再与 `series_arc` 一起写入每条分集。`get_outline_batch()` 返回批次概览；`find_resumable_outline_batch()` 返回已保存概览。`phase4_worker` 在检查点和恢复参数中传递该对象。

- [ ] **步骤 7：运行任务 1 测试验证通过**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_creative_phase4_generation.py -k "outline" -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-generation-green
```

预期：所有选中测试 PASS。

- [ ] **步骤 8：提交任务 1**

只暂存本任务新增文件和任务相关补丁；若相关文件含用户既有未提交改动，则保留为未提交工作区改动并在交付报告说明，不执行整文件暂存。

### 任务 2：实现旧大纲概览生成、批次保存和下载

**文件：**
- 修改：`creative/prompt_templates.py`
- 修改：`creative/script_service.py`
- 修改：`creative/repository.py`
- 修改：`creative/phase4_worker.py`
- 修改：`creative/schema.py`
- 修改：`creative/api.py`
- 测试：`tests/test_creative_phase4_generation.py`
- 测试：`tests/test_creative_phase4.py`

- [ ] **步骤 1：编写失败的旧大纲汇总与原子保存测试**

为 `generate_series_overview()` 提供当前批次分集，断言提示词包含全部集数且返回六项概览。仓储测试调用：

```python
updated = repository.update_outline_overview(project_id, batch_id, valid_series_overview())
assert updated["series_overview"] == valid_series_overview()
assert {json.dumps(item["content"]["series_overview"], sort_keys=True) for item in updated["episodes"]} == {
    json.dumps(valid_series_overview(), sort_keys=True)
}
```

再断言空字段、非当前批次和锁定批次被拒绝，且失败前后的分集内容完全相同。

- [ ] **步骤 2：运行测试验证正确失败**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_creative_phase4_generation.py -k "series_overview or outline_overview" -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-repository-red
```

预期：FAIL，生成函数和仓储更新方法尚不存在。

- [ ] **步骤 3：实现旧数据生成与事务保存**

新增 `series_overview_messages()` 与 `generate_series_overview()`。`CreativeRepository.update_outline_overview()` 校验项目、当前兼容草稿批次和全部六项字段，在单个事务中更新该批次每条 `content_json`；任何错误整体回滚。

扩展 `phase4_worker` 接受 `outline_overview` 动作，从 `extra.batch_id` 读取当前批次，调用模型汇总并原子保存，不修改分集业务字段或生成元数据。

- [ ] **步骤 4：编写失败的 API 和下载测试**

覆盖：

```python
response = client.patch(
    f"/api/creative/projects/{project_id}/outlines/{batch_id}/overview",
    json={"series_overview": valid_series_overview()},
)
assert response.status_code == 200
```

并断言 `POST /api/creative/projects/{project_id}/outlines/{batch_id}/overview/generate` 创建 `outline_overview` 后台任务、使用 outline 工作流任务、锁定批次返回 409。下载测试断言“## 全集剧情概览”位于“## 第 1 集”之前且仅出现一次；旧数据不输出空章节。

- [ ] **步骤 5：运行 API 测试验证正确失败**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_creative_phase4.py tests/test_creative_phase4_generation.py -k "overview or outline_markdown" -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-api-red
```

预期：FAIL，路由、请求模型或下载章节尚不存在。

- [ ] **步骤 6：实现 API、任务权限类别和 Markdown 输出**

增加 `OutlineOverviewRequest`，实现两个规格端点。生成端点调用 `validate_task(workflow_task_id, "outline", principal, project_id=project_id)` 后创建 `outline_overview` 任务；`create_generation_job()` 将该动作归类为 writing job。PATCH 端点先调用 `workflow_adapter.assert_editable("outline", f"{project_id}:{batch_id}")`，再原子保存。

`build_outline_markdown()` 从排序后第一条记录读取概览，按六项固定顺序输出一次。

- [ ] **步骤 7：运行任务 2 测试验证通过**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_creative_phase4.py tests/test_creative_phase4_generation.py -k "overview or outline_markdown" -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-api-green
```

预期：所有选中测试 PASS。

- [ ] **步骤 8：提交任务 2**

只提交能够与用户既有改动清晰分离的任务补丁；不得整文件暂存 `creative/api.py`、`creative/repository.py` 或 `creative/prompt_templates.py` 的既有改动。

### 任务 3：实现方案 A 页面与完整交互状态

**文件：**
- 修改：`static/js/common/tasks.js`
- 修改：`static/js/common/creative.js`
- 修改：`static/js/writing/outlines.js`
- 修改：`templates/common/workspace/project_outlines.html`
- 修改：`static/css/workspaces.css`
- 测试：`tests/test_outline_episode_cards_browser.py`

- [ ] **步骤 1：编写失败的浏览器展示与响应式测试**

在 `CARD_OUTLINE_ITEMS` 中加入相同 `series_overview`，断言概览位于搜索栏之前、标题可见、六项标签顺序正确、默认展开、桌面为两列、390px 为单列且页面无横向溢出。

- [ ] **步骤 2：运行测试验证正确失败**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_outline_episode_cards_browser.py -k "overview" -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-browser-layout-red
```

预期：FAIL，`.outline-series-overview` 不存在。

- [ ] **步骤 3：实现概览状态、模板和样式**

在页面模块中实现：

```javascript
currentOutlineSeriesOverview()
outlineSeriesOverviewExists()
toggleOutlineSeriesOverview()
validateOutlineSeriesOverview()
saveOutlineSeriesOverview()
generateOutlineSeriesOverview()
```

上下文键包含项目、Brief、人物批次、大纲批次和当前概览快照，切换版本或后台任务回填时重载编辑草稿。模板按方案 A 渲染标题栏与六个带显式标签的文本域；CSS 使用两列 grid 和现有窄屏断点单列回退。

- [ ] **步骤 4：编写失败的生成、校验、保存与锁定态测试**

测试真实交互：旧草稿无概览时显示“生成概览”，点击后请求后台生成动作；清空字段时出现字段级错误且没有 PATCH；六项齐全时 PATCH 一次并更新当前批次全部本地记录；失败保留输入；锁定批次字段禁用且隐藏保存按钮。

- [ ] **步骤 5：运行交互测试验证正确失败**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_outline_episode_cards_browser.py -k "overview" -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-browser-interaction-red
```

预期：FAIL，对应按钮、校验提示或请求行为尚未实现。

- [ ] **步骤 6：实现生成、保存和任务完成识别**

`generateOutlineSeriesOverview()` 复用 outline 工作流任务与 `startCreativeGeneration()`；公共任务状态增加 `outline_overview` 标签和启动提示。保存失败不替换草稿；成功后把响应中的概览同步到当前批次每条本地分集。概览已有内容时不显示重新生成按钮。

- [ ] **步骤 7：运行任务 3 测试验证通过**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_outline_episode_cards_browser.py -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-browser-green
```

预期：整个大纲浏览器测试文件 PASS。

- [ ] **步骤 8：提交任务 3**

提交页面、样式、JS 和浏览器测试的任务相关改动；保持其他未提交页面改动原样。

### 任务 4：回归、静态检查与视觉验收

**文件：**
- 检查：上述全部修改文件
- 输出：`.runtime/screenshots/outline-series-overview/`

- [ ] **步骤 1：运行后端目标回归**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_creative_phase4_generation.py tests/test_creative_phase4.py tests/test_outline_episode_cards_browser.py -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-final
```

预期：全部 PASS，0 failed。

- [ ] **步骤 2：运行前端语法与文件头检查**

```powershell
node --check static/js/writing/outlines.js
node --check static/js/common/creative.js
.\.venv\Scripts\python.exe tools/file_header_manager.py --check
```

预期：命令退出码均为 0。

- [ ] **步骤 3：运行关联 API 与页面回归**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_page_api_authorization.py tests/test_webgui_ui_changes.py -q -p no:cacheprovider --basetemp=.runtime/tmp/series-overview-related
```

预期：全部 PASS；若存在任务前已知基线失败，必须用未修改代码或既有记录证明与本功能无关。

- [ ] **步骤 4：生成桌面与窄屏截图并检查**

设置 `AIWORKER_OUTLINE_SCREENSHOT_DIR=.runtime/screenshots/outline-series-overview` 运行浏览器测试，检查 1280px 与 390px 截图中：标题与六项内容完整、操作不重叠、桌面两列、窄屏单列、分集搜索和卡片仍可用。

- [ ] **步骤 5：审查最终差异与规格覆盖**

逐项核对设计规格中的生成、旧数据、编辑、锁定、版本、下载、错误和响应式要求。使用 `git diff --check`，并确认没有修改或暂存任务外文件。

- [ ] **步骤 6：提交可安全分离的最终补丁**

若所有任务相关改动都能与用户既有改动精确分离，则提交功能；否则保留为工作区改动并在交付报告中列出文件和原因，避免把用户改动混入提交。
