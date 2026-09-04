# Task 9 Report — Writer Agent 动作 API

**Status: DONE**

## 实现

- 新增动作读取、确认/取消、失败重试三个 API；动作写操作始终按会话 owner 复检，管理员读取权限不扩大为动作写权限。
- 新增 `ActionService`，按钮与自然语言确认都构造同一 `ActionCommand` 并走同一决策边界。
- `claim_pending_action_once()` 返回是否首次 claim，重复 `client_request_id` 返回同一动作且不会重复 enqueue。
- retry 使用既有持久化幂等键，重复请求返回同一 retry action 且只 enqueue 一次。
- 自然语言确认在 LLM 前匹配 pending 动作；附带新约束时仍进入修订/讨论路径。
- WebGUI 注入真实 store/runner/action service/audit service；登记新增 API 页面授权路径。
- 审计只保存动作、项目、阶段、状态和引用标识，不保存材料正文或凭据。

## TDD 证据

RED：动作服务尚不存在，聚焦 API 测试在收集时以 `ImportError: cannot import name 'ActionService'` 失败。

GREEN：

```powershell
uv run --no-cache pytest tests/test_writer_agent_api.py -q -k "button_and_natural or action_decision_rechecks or repeated_decision or retry_client" --basetemp=.pytest-task9-green1
```

结果：`4 passed, 14 deselected, 1 warning`。

聚焦回归：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run --no-cache pytest tests/test_writer_agent_actions.py tests/test_writer_agent_api.py tests/test_writer_agent_runner.py tests/test_writer_agent_store.py tests/test_page_authorization.py tests/test_webgui_ui_changes.py::test_webgui_registers_independent_writer_agent_workbench -q --basetemp=.pytest-task9-full
```

结果：`94 passed, 17 warnings in 6.90s`。完整 `test_webgui_ui_changes.py` 另有 7 个与本任务无关、在未改目标区域的旧静态文本断言失败，已在聚焦测试中隔离。

`py_compile`、目标 Python 文件版权头及 `git diff --check` 均通过。

## 疑虑

- 无 Task 9 范围内已知功能疑虑。

---

## 第 1 轮审查修复

**Status: DONE**

### 实现

- 重试创建改为 `WriterAgentStore.create_retry_action_once()`：SQLite `BEGIN IMMEDIATE` 事务原子返回 retry 动作与 `created` 标记；仅首次创建才 enqueue 和审计，消除事务外预查询的并发双 enqueue。
- `ActionService` 在确认与重试的持久化状态变更前，按 `WorkflowRepository.is_active_project_member()` 与 `PermissionService` 的已分配/管理项目写契约复检实时项目写权；`project.create_original` 另复检 `project.create`。撤权拒绝时动作仍为 pending，重试动作不会创建。
- 自然语言请求支持 `message_id` / `client_message_id`。使用稳定消息 ID 构造确认幂等键；无 ID 的旧客户端每次生成新消息 ID。相同 ID 重放返回已保存会话，不同 ID 的相同正文保持独立。
- 管理员可按既有会话读权限读取 action GET；动作确认/重试仍严格按 owner 拒绝。
- 审计覆盖确认 claim、取消后的实际状态、retry 及 runner 的 succeeded/failed/cancelled 终态；仅写 action/project/stage/status/job ID 和稳定错误码，未写材料正文、凭据或错误正文。

### TDD 证据

RED：

```powershell
uv run pytest tests/test_writer_agent_api.py -q -k "natural_language_message_id or admin_can_read_action" --basetemp=.pytest-task9-fix1-red-api
```

结果：`2 failed, 18 deselected`；消息重放落入 LLM 路径并返回 503，管理员 action GET 返回 404。

```powershell
uv run pytest tests/test_writer_agent_api.py -q -k "natural_language_message_id or admin_can_read_action or rechecks_revoked or concurrent_retry or cancel_audit" --basetemp=.pytest-task9-fix1-red-core
```

结果：`5 failed, 18 deselected`；包含撤权前未复检、真实并发 retry enqueue 两次、取消审计仍为 queued。

```powershell
uv run pytest tests/test_writer_agent_runner.py -q -k "audits_success_failure" --basetemp=.pytest-task9-fix1-red-runner-audit
```

结果：`1 failed, 34 deselected`；runner 未记录任何终态审计。

`project.create_original` 权限测试以移除该生产守卫的单点变异运行：`1 failed, 23 deselected`（未抛出 `PROJECT_CREATE_DENIED`），随后恢复最小守卫。

GREEN：

```powershell
uv run pytest tests/test_writer_agent_api.py -q -k "natural_language_message_id or admin_can_read_action or rechecks_revoked or concurrent_retry or cancel_audit" --basetemp=.pytest-task9-fix1-green-core2
```

结果：`5 passed, 18 deselected`。

```powershell
uv run pytest tests/test_writer_agent_runner.py -q -k "audits_success_failure" --basetemp=.pytest-task9-fix1-green-runner-audit
```

结果：`1 passed, 34 deselected`。

```powershell
uv run --no-cache pytest tests/test_writer_agent_api.py -q -k "original_project_confirmation" --basetemp=.pytest-task9-fix1-green-original
```

结果：`1 passed, 23 deselected`。

聚焦回归：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run --no-cache pytest tests/test_writer_agent_actions.py tests/test_writer_agent_api.py tests/test_writer_agent_runner.py tests/test_writer_agent_store.py tests/test_page_authorization.py tests/test_webgui_ui_changes.py::test_webgui_registers_independent_writer_agent_workbench -q --basetemp=.pytest-task9-fix1-final
```

结果：`101 passed, 17 warnings in 7.32s`。

### 改动文件与自审

- `writer_agent/store.py`、`writer_agent/runner.py`、`writer_agent/action_service.py`、`writer_agent/api.py`、`WebGUI.py`
- `tests/test_writer_agent_api.py`、`tests/test_writer_agent_runner.py`
- `git diff --check` 通过。未修改或暂存 `tests/test_creative_workflow_integration.py`；未触碰 scratch。
