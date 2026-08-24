# 任务 11 验收报告

状态：DONE_WITH_CONCERNS（Task 11 定向验收已通过；全量 `tests/` 与全量 `pre-commit` 仍受既有基线/网络环境阻塞）。

## 已实现

- 已追踪 `ActionService.decide()` → `WriterActionRunner.run()` →
  `WriterProjectOrchestrator` → `WriterAgentStore` 的真实路径。根因是 runner
  过去只消费已有步骤，因此项目动作和未物化的计划会空步骤成功。
- 已使 runner 执行 `project.create_original` / `project.link`，并在创建项目且
  指定目标阶段时在同一确认内绑定项目、物化计划、继续执行。
- 已让 lock 步骤从其成功依赖的唯一、同阶段同集的 `resource_refs` 自动取得
  `resource_id`；无资源或多个资源均返回 `RESULT_VALIDATION_FAILED`。
- 终态结果现合并已有项目结果，并记录 `partial`、成功/失败步骤和已执行集数；
  重试动作只汇报自身重试的失败集。
- 恢复旧的内部直接 Phase 4 worker 作业兼容性：缺少 authority envelope 的旧作业
  从当前项目推导同一权威上下文。
- `execute_project_action()` 保持既有外部结果键；计划权威上下文改为内部读取。
- 新增 `WriterAgentE2EHarness`，以真实 HTTP、仓储、工作流适配器和 Phase 4 worker
  覆盖一句话灵感、完整故事、上传材料与关联既有项目四种起点。
- 未关联会话持久化动作时也记录 `session_revision`；完整故事或显式跳过方向
  比较可直接进入 Brief；上传实际材料后会排除仅用于启动会话的“继续创作”占位文本。
- Writer plan 的 lock 步骤在其任务处于 `assigned` 时先进入 `in_progress`，与
  工作流的 `complete_work` 状态机契约一致。

## RED / GREEN 证据

- RED：`uv run --no-cache pytest tests/test_writer_agent_runner.py -k
  'lock_step_resolves_only_one_succeeded_dependency_resource' -q
  --basetemp=.pytest-root-task11-red-lock` → `1 failed, 2 passed`，缺少
  `resource_id`。
- GREEN：同一测试 → `3 passed`。
- RED：`uv run --no-cache pytest tests/test_writer_agent_runner.py -k
  'keeps_successful_episodes_and_retries_only_failures' -q
  --basetemp=.pytest-root-task11-red-terminal-result` → `KeyError:
  succeeded_steps`。
- GREEN：同一测试 → `1 passed`。
- RED：`uv run --no-cache pytest tests/test_writer_agent_orchestrator.py -k
  'create_action_binds_idempotent_original_project' -q
  --basetemp=.pytest-root-task11-red-project-result` → 结果多出
  `project_context`。
- GREEN：项目动作精确结果与创建后续大纲测试均为 `1 passed`。
- 回归 RED：任务简报规定的首组套件首次 `1 failed, 95 passed`，旧直接 worker
  缺少 `source_authority`。
- 回归 GREEN：
  `uv run --no-cache pytest tests/test_original_workflow_integration.py
  tests/test_writer_agent_integration.py tests/test_writer_agent_api.py
  tests/test_writer_agent_runner.py -q
  --basetemp=.pytest-root-task11-focused-green` → `96 passed`。
- RED：未绑定会话的 `creative.run_plan` 动作缺少 `session_revision`，
  `tests/test_writer_agent_api.py -k persists_valid_cards_actions_and_source_snapshot`
  → `KeyError: 'session_revision'`。
- GREEN：同一测试 → `1 passed`。
- E2E GREEN：四类起点场景 → `4 passed, 27 deselected`。
- 聚焦回归 GREEN：
  `uv run --no-cache pytest tests/test_original_workflow_integration.py
  tests/test_writer_agent_integration.py tests/test_writer_agent_api.py
  tests/test_writer_agent_runner.py -q
  --basetemp=.pytest-root-task11-focused-final` → `100 passed`。
- 权限与浏览器验收 GREEN：任务简报步骤 3 的七个文件 → `50 passed`。
- 数据库/API 兼容核心 GREEN：Alembic、creative repository 与 writer store →
  `36 passed`。
- 变更 Python 文件已通过 `py_compile`，并通过
  `tools/file_header_manager.py --check`（11/11 均已有版权头）。

## 修改文件

- `creative/phase4_worker.py`
- `writer_agent/action_service.py`
- `writer_agent/api.py`
- `writer_agent/orchestrator.py`
- `writer_agent/runner.py`
- `writer_agent/store.py`
- `tests/test_writer_agent_orchestrator.py`
- `tests/test_writer_agent_runner.py`
- `tests/test_writer_agent_integration.py`
- `tests/test_writer_agent_api.py`
- `tests/writer_agent_harness.py`

`tests/test_creative_workflow_integration.py` 是工作区既有的用户改动；本任务未
修改该文件，也不会暂存。

## 疑虑

- 任务简报步骤 5 的完整命令结果为 `224 passed, 7 failed`。失败全部来自
  `tests/test_webgui_ui_changes.py`，断言的是导航路径、选题页文案、旧的
  `fetchTopicLibrary()` 调用、脚本页资源标签和红果设置文案；这些文件不在
  Task 11 变更范围内，且失败与 Writer Agent/数据库兼容性无关。未修改这些
  无关 UI 资产，也未提交。
- 环境对 `.pytest_cache` 无写权限；所有验证使用 `uv run --no-cache` 与独立
  `--basetemp`，因此伴随 PytestCacheWarning。
- `pre-commit` 无法完成：其全局缓存
  `C:\Users\Administrator\.cache\pre-commit` 为只读，启动时即因
  `OperationalError: attempt to write a readonly database` 退出；已用项目自身的
  文件头检查覆盖本任务涉及的 Python 文件。

## 第 1 轮审查修复与最终验证

### 审查修复

- 创建项目后发生部分步骤失败时，重试会复用已创建项目，不会重复执行创建。
- 显式 `resource_id` 的 lock 步骤必须与唯一成功直接依赖所产出的资源一致。
- Phase 4 缺失 authority envelope 时不再无条件降级为旧作业；仅兼容可验证的
  旧内部作业上下文。
- E2E Harness 的 provider 按 system prompt 区分 beats 与 script，脚本标题按集数
  返回；异步状态模拟为 `pending → running → terminal`，并以条件 event 替代
  `time.sleep`。
- 选题库榜源切换已贯通前端选项、localStorage、请求 query 与缓存键；生命周期
  筛选补上 `beats_exported`。5 项旧的静态 UI 断言已迁移至实际消费者/模块化契约。
- 最终审查补充：平台切换会恢复目标平台已保存的榜源偏好；新增 Node 驱动的真实
  浏览器模块回归测试，覆盖“红果漫剧 → 抖音 → 红果”场景。

### RED / GREEN 补充

- E2E 首次 RED：beats 请求被历史消息污染而返回 characters；修复后
  `tests/test_writer_agent_integration.py` 为 `32 passed`。
- Harness event 化首次 RED：4 个用例因状态变更不可观察失败；为 store 的关键
  状态转换设置 `action_changed` 后，同一文件为 `32 passed`。

### 最终命令与结果

- 聚焦步骤 2：
  `uv run --no-cache pytest tests/test_original_workflow_integration.py
  tests/test_writer_agent_integration.py tests/test_writer_agent_api.py
  tests/test_writer_agent_runner.py -q
  --basetemp=.pytest-root-task11-focused-final3` → `103 passed`。
- 步骤 3 权限/页面/浏览器验收 → `50 passed`。
- 步骤 5 兼容性：
  `tests/test_system_database_alembic.py tests/test_original_creative_repository.py
  tests/test_writer_agent_store.py tests/test_webgui_ui_changes.py` → `232 passed`。
- `python -m py_compile`（Phase 4、orchestrator、runner、store、Harness）和
  `git diff --check` 均通过；8 个本任务 Python 文件的文件头检查结果为
  `Updated/Need update: 0`。

### 剩余环境门禁

- 全量 `tests/` 在 `tests/test_approval_free_system.py::test_workflow_router_exposes_no_approval_operations`
  首次失败（此前 `86 passed`）：测试以 `WindowsPath` 构造 `IdentityRepository`，而
  当前实现要求 `SystemDatabase`，触发 `AttributeError: 'WindowsPath' object has no
  attribute 'transaction'`。这与 Task 11 无关，未擅自修复。
- 在工作区专用 `PRE_COMMIT_HOME` 与 `VIRTUALENV_APP_DATA` 下重试后，`pre-commit`
  仍因访问 `https://github.com/pre-commit/pre-commit-hooks/` 的 443 端口失败；日志为
  `.pre-commit-task11-final5/pre-commit.log`。因此无法取得全量 pre-commit 结果。
