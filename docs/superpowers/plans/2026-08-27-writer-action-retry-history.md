# 编剧大师动作重试历史折叠实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不修改动作审计数据的前提下，让每条 `root_action_id` 重试链只显示最新动作，并把旧动作放进只读的可折叠执行历史。

**架构：** 保持 Writer Agent 会话接口返回完整动作列表，在浏览器控制器渲染前对动作做稳定分组和排序。当前动作继续复用现有卡片、按钮和轮询逻辑；旧动作只渲染为同一卡片内的原生 `<details>` 历史条目，不绑定任何操作。

**技术栈：** 原生 JavaScript、HTML `<details>`、Bootstrap 工具类、Playwright、pytest。

---

## 工作区保护规则

- 当前分支存在大量用户未提交修改；不得使用 `git reset`、`git restore`、`git checkout --` 或 stash。
- 修改前先运行 `git diff -- static/js/review/writer_agent.js tests/test_writer_agent_browser.py`，保留两个文件中已有修改。
- 源码只使用 `apply_patch` 编辑。
- 测试临时目录使用 pytest 自身隔离目录，不删除现有测试产物。
- 提交只包含 `static/js/review/writer_agent.js` 和 `tests/test_writer_agent_browser.py`。
- 8899 存在活动 Job 时不得重启；发布前必须再次读取活动 Job 和精确 listener PID。

## 文件结构

- 修改 `static/js/review/writer_agent.js`：重试链分组、当前动作选择、只读历史渲染和只轮询当前动作。
- 修改 `tests/test_writer_agent_browser.py`：覆盖成功重试折叠、多次重试历史、最新失败重试目标和既有单动作行为。

## 任务 1：折叠动作重试链并展示只读历史

**文件：**
- 修改：`static/js/review/writer_agent.js:130-195,686-698`
- 测试：`tests/test_writer_agent_browser.py:320-430`

- [ ] **步骤 1：编写成功重试链的失败浏览器测试**

在 `tests/test_writer_agent_browser.py` 增加：

```python
def test_writer_agent_collapses_successful_retry_chain_into_read_only_history(
    page: Page,
) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {
                    "id": "original",
                    "root_action_id": "original",
                    "status": "failed",
                    "retryable": True,
                    "title": "创建原创项目",
                    "completed_steps": 0,
                    "total_steps": 1,
                    "created_at": 10,
                },
                {
                    "id": "retry-1",
                    "root_action_id": "original",
                    "retry_of_action_id": "original",
                    "status": "succeeded",
                    "title": "创建原创项目",
                    "completed_steps": 1,
                    "total_steps": 1,
                    "created_at": 20,
                },
                {
                    "id": "retry-2",
                    "root_action_id": "original",
                    "retry_of_action_id": "original",
                    "status": "succeeded",
                    "title": "创建原创项目",
                    "completed_steps": 1,
                    "total_steps": 1,
                    "created_at": 30,
                },
            ],
        },
    )

    assert page.locator(".writer-action-card").count() == 1
    assert page.get_by_role("button", name="重试").count() == 0
    history = page.get_by_text("查看执行历史（2）")
    assert history.is_visible()
    history.click()
    assert page.locator(".writer-action-history-item").count() == 2
    assert page.locator(".writer-action-history-item button").count() == 0
```

这个测试要抓住的破坏是：渲染器继续把同一根动作的失败和成功重试分别显示，并把旧失败项暴露为可操作的重试卡。

- [ ] **步骤 2：编写最新失败动作唯一可重试的失败测试**

增加：

```python
def test_writer_agent_retries_only_the_latest_failed_action_in_a_chain(
    page: Page,
) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {
                    "id": "original",
                    "root_action_id": "original",
                    "status": "failed",
                    "retryable": True,
                    "title": "生成剧本",
                    "created_at": 10,
                },
                {
                    "id": "retry-latest",
                    "root_action_id": "original",
                    "retry_of_action_id": "original",
                    "status": "failed",
                    "retryable": True,
                    "title": "生成剧本",
                    "created_at": 20,
                },
            ],
        },
    )

    assert page.locator(".writer-action-card").count() == 1
    retry = page.get_by_role("button", name="重试")
    assert retry.count() == 1
    retry.click()
    page.wait_for_function("window.__writerActionRequests.length === 1")
    assert page.evaluate("window.__writerActionRequests[0].url").endswith(
        "/actions/retry-latest/retry"
    )
```

这个测试要抓住的破坏是：旧失败动作仍能发起重试，或最新失败动作因折叠而失去唯一的重试入口。

- [ ] **步骤 3：运行两个测试确认 RED**

运行：

```powershell
uv run pytest tests/test_writer_agent_browser.py -q -k "collapses_successful_retry_chain or retries_only_the_latest_failed_action"
```

预期：第一个测试看到 3 张动作卡或仍有“重试”；第二个测试看到 2 张动作卡或不止一个“重试”。失败必须来自尚未实现的重试链折叠，而不是 fixture、浏览器或语法错误。

- [ ] **步骤 4：实现稳定分组和排序**

在 `renderWriterActionCard` 之前增加：

```javascript
    function writerActionChainKey(action) {
        return String(action?.root_action_id || action?.id || '');
    }

    function writerActionOrder(entry) {
        const action = entry.action || {};
        return [
            Number(action.created_at || 0),
            Number(action.updated_at || 0),
            Number(entry.index || 0),
        ];
    }

    function writerActionChains(actions) {
        const grouped = new Map();
        (actions || []).forEach((action, index) => {
            const key = writerActionChainKey(action);
            if (!grouped.has(key)) grouped.set(key, []);
            grouped.get(key).push({action, index});
        });
        return [...grouped.values()].map((entries) => {
            entries.sort((left, right) => {
                const a = writerActionOrder(left);
                const b = writerActionOrder(right);
                return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
            });
            return {
                current: entries.at(-1).action,
                history: entries.slice(0, -1).reverse().map((entry) => entry.action),
            };
        });
    }
```

分组键缺失时回退到动作 ID；排序先使用创建时间，再使用更新时间和输入位置，兼容旧记录。

- [ ] **步骤 5：实现只读历史组件**

增加状态文案复用函数，并让当前卡与历史项使用同一状态映射：

```javascript
    function writerActionStatusLabel(action) {
        return ({
            pending: '待确认', queued: '已排队', running: '生成中',
            succeeded: '已完成', failed: '执行失败', cancelled: '已取消',
        })[action?.status] || action?.status || '';
    }

    function renderWriterActionHistory(actions) {
        const details = document.createElement('details');
        details.className = 'writer-action-history border-top mt-3 pt-2';
        const summary = document.createElement('summary');
        summary.className = 'small text-muted';
        summary.textContent = `查看执行历史（${actions.length}）`;
        details.append(summary);
        actions.forEach((action) => {
            const item = document.createElement('section');
            item.className = 'writer-action-history-item small border-top mt-2 pt-2';
            writerCardText(item, 'fw-semibold', action.title || action.action_type || '创作动作');
            const progress = Number(action.total_steps || 0) > 0
                ? ` · ${Number(action.completed_steps || 0)} / ${Number(action.total_steps)}`
                : '';
            const occurredAt = sessionTime(action.updated_at || action.created_at);
            writerCardText(
                item, 'text-muted mt-1',
                `${writerActionStatusLabel(action)}${progress}${occurredAt ? ` · ${occurredAt}` : ''}`
            );
            details.append(item);
        });
        return details;
    }
```

把 `renderWriterActionCard(action)` 改为 `renderWriterActionCard(action, history = [])`；状态行改用 `writerActionStatusLabel(action)`，并在卡片末尾仅当 `history.length > 0` 时追加 `renderWriterActionHistory(history)`。历史函数不得调用 `renderWriterActionCard`，因此不会生成操作按钮或轮询器。

- [ ] **步骤 6：只渲染和轮询每条链的当前动作**

把 mount 中的动作循环替换为：

```javascript
            writerActionChains(mountedWriterSession.actions || []).forEach(
                ({current, history}) => host.append(
                    renderWriterActionCard(current, history)
                )
            );
```

保留 `replaceMountedAction` 将新重试动作加入完整会话数组的行为。每次状态更新会重新 mount，分组器自动把新动作设为当前动作；只有 `renderWriterActionCard` 收到的当前动作可能启动轮询。

- [ ] **步骤 7：运行聚焦测试确认 GREEN**

运行：

```powershell
uv run pytest tests/test_writer_agent_browser.py -q -k "collapses_successful_retry_chain or retries_only_the_latest_failed_action"
```

预期：2 个测试全部 PASS，输出无页面异常。

- [ ] **步骤 8：运行 Writer Agent UI 回归**

运行：

```powershell
uv run pytest tests/test_writer_agent_browser.py tests/test_webgui_ui_changes.py -q
```

预期：全部 PASS。重点确认不同 `root_action_id` 的并行动作仍分别展示，单动作确认、取消、成果链接、移动端无横向溢出和完整项目审核抽屉均不回归。

- [ ] **步骤 9：运行预提交检查并精确提交**

运行：

```powershell
uv run pre-commit run --files static/js/review/writer_agent.js tests/test_writer_agent_browser.py
git commit --only static/js/review/writer_agent.js tests/test_writer_agent_browser.py -m "fix: 折叠动作重试历史"
```

提交前确认 `git diff --cached --name-only` 仅包含上述两个文件；不得带入工作区其他修改。

- [ ] **步骤 10：安全发布和项目 11 冒烟**

读取 `.runtime/agent_state.db`、`.runtime/state` 和 `.runtime/jobs`，确认没有 `pending`、`running` 或 `stopping` Job。核实 8899 listener 命令行属于当前工作区的 `uvicorn WebGUI:app` 后，仅重启该 listener，不终止 Worker。

在项目 11 的会话 8 页面确认：

- 只显示一张“创建原创项目并生成第1-5集剧本”当前卡；
- 当前卡状态为已完成且没有“重试”；
- 出现“查看执行历史（2）”；
- 展开后可看到旧失败和早期成功记录，但历史区没有按钮；
- 大纲完整项目审核抽屉和右侧项目进度仍可使用。
