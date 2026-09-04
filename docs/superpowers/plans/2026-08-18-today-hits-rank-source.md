# 今日爆款榜单来源实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 今日爆款支持按平台榜单来源生成和查看 Top10，并把同一剧目的多个榜单来源用 `|` 合并展示。

**架构：** `TopicService` 从今日榜单的全部 section 收集记录，按规范化剧名合并具体榜单来源，再按一级来源筛选。`CreativeRepository` 持久化合并后的 `rank_source`，API 将来源条件贯穿生成与查询；Alpine 页面维护平台对应的来源选择并展示来源列。

**技术栈：** Python 3.11、FastAPI、SQLite、Alpine.js、Bootstrap 5、pytest。

---

### 任务 1：按来源聚合榜单候选

**文件：**
- 修改：`creative/topic_service.py`
- 测试：`tests/test_creative_lifecycle.py`

- [ ] **步骤 1：编写失败的服务测试**

构造抖音“榜单/热榜”和红果“全部/真人剧/漫剧/AI剧” sections，其中同名剧目出现在两个具体榜单；断言：

```python
topics = service.generate("hg", rank_source="真人剧", limit=10)
assert topics[0]["rank_source"] == "真人剧热播榜|真人剧推荐榜"
assert {item["title"] for item in topics} == {"真人多榜剧"}
```

该测试要抓住“服务仍读取顶层 `current_entries`，或重复剧目只保留第一个榜单”的破坏。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_creative_lifecycle.py -k "rank_source" -q`

预期：FAIL，`TopicService.generate()` 不接受 `rank_source` 或返回项没有合并后的来源。

- [ ] **步骤 3：编写最少实现**

新增平台默认来源和允许值校验；遍历 `response["sections"] -> rankings -> current_entries`，生成具体来源名，按规范化标题合并来源和证据。来源用保序去重后的 `"|".join(...)` 保存，最后按一级来源匹配并评分排序。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_creative_lifecycle.py -k "rank_source" -q`

预期：PASS。

### 任务 2：持久化并通过 API 筛选来源

**文件：**
- 修改：`creative/repository.py`
- 修改：`creative/api.py`
- 测试：`tests/test_creative_lifecycle.py`

- [ ] **步骤 1：编写失败的 API/仓储测试**

插入具有 `rank_source="真人剧热播榜|真人剧推荐榜"` 的候选，断言真人剧筛选命中、漫剧筛选不命中；调用生成接口时传 `rank_source` 并断言响应只包含匹配来源。

```python
assert [item["title"] for item in repository.list_topics(rank_source="真人剧")] == ["真人多榜剧"]
response = client.post("/api/creative/topics/generate", json={"platform": "dy", "rank_source": "热榜", "limit": 10})
assert response.status_code == 200
```

该测试要抓住“来源未落库、查询忽略来源或 API 未转发来源”的破坏。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_creative_lifecycle.py -k "rank_source" -q`

预期：FAIL，数据库无 `rank_source` 或接口模型忽略筛选。

- [ ] **步骤 3：编写最少实现**

按仓储现有启动迁移方式添加 `rank_source TEXT NOT NULL DEFAULT ''`；补齐 upsert、直接收录和查询。查询使用分隔符边界匹配而非裸 `LIKE '%榜单%'`：

```sql
('|' || rank_source || '|') LIKE '%|' || ? || '|%'
```

红果一级来源先在 Python 中匹配每个 `|` token 的前缀，避免“漫剧”误命中“AI剧”。API 请求模型、GET 参数和生成接口均传递 `rank_source`。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_creative_lifecycle.py -k "rank_source or generating_today_hits" -q`

预期：PASS。

### 任务 3：实现今日爆款来源切换与展示

**文件：**
- 修改：`static/js/common/tasks.js`
- 修改：`static/js/selection/topics.js`
- 修改：`static/js/selection/breakdowns.js`
- 修改：`templates/common/workspace/selection_hits.html`
- 测试：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写失败的界面契约测试**

断言页面存在抖音和红果来源按钮、表头“榜单来源”，状态含 `rankSource`，生成请求和读取请求都携带 `rank_source`，缓存键包含来源。

该测试要抓住“切换来源只改外观但请求仍混用平台级数据”的破坏。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_webgui_ui_changes.py -k "today_hits" -q`

预期：FAIL，当前页面只有平台选择器且表头仍为“平台”。

- [ ] **步骤 3：编写最少实现**

在 `creative` 状态中加入平台对应默认来源；来源按钮使用真实 `button`、可见 focus 和 `aria-pressed`。平台切换时恢复该平台记忆的来源，来源切换时持久化并重新生成。所有 request/cache key 带来源；表格显示 `topic.rank_source || topic.rank_type || '-'`，空态文案指出当前来源。

- [ ] **步骤 4：运行界面测试验证通过**

运行：`uv run pytest tests/test_webgui_ui_changes.py -k "today_hits" -q`

预期：PASS。

### 任务 4：回归与视觉验证

**文件：**
- 验证：上述所有修改文件

- [ ] **步骤 1：运行相关回归测试**

运行：`uv run pytest tests/test_creative_lifecycle.py tests/test_webgui_ui_changes.py tests/test_today_rank_phase.py -q`

预期：PASS，且输出无错误。

- [ ] **步骤 2：运行文件头检查**

运行：`uv run pre-commit run --all-files`

预期：PASS；若已有无关文件失败，记录准确文件与原因，不修改无关变更。

- [ ] **步骤 3：启动 WebGUI 并检查桌面页面**

启动本地服务后，用浏览器确认：抖音显示“榜单/热榜”，红果显示“全部/真人剧/漫剧/AI剧”；切换后表格不混入其他来源，多来源以 `|` 显示，控件和表格无重叠。
