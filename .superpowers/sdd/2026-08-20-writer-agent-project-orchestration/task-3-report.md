# Task 3 实施报告

## 状态

DONE_WITH_CONCERNS：Task 3 实现、验证和只读自审已完成。按控制器指示不再执行任何 ACL、Git 元数据、暂存或提交操作；提交由 controller 完成。

## RED

- 命令：`$env:UV_CACHE_DIR='.uv-cache-task3'; uv run pytest tests/test_creative_application_service.py -q`
- 真实预期失败：测试收集时报 `ModuleNotFoundError: No module named 'creative.application_service'`。
- 说明：首次命令先被不可访问的全局 uv cache 拦截；改用 Task 3 本地 uv cache 后取得上述真实 RED。

## 实现摘要

- 新增共享 `CreativeApplicationService`，统一项目创建委托、阶段任务续建/领取、写权限与任务校验、草稿持久化、ResourceRef、锁定、revision CAS 和生成任务创建。
- 新增直接原创 Brief API `POST /api/creative/projects/{project_id}/blueprints`，并将现有原创项目创建、直接人物/大纲/Beats/剧本保存及相应锁定入口接入共享服务。
- `build_creative_router()` 支持注入共享 repository/application service；WebGUI 按指定顺序构造 repository、adapter、transition、original project service、application service，并向 creative/writer-agent 路由注入同一实例。
- 生成 payload 携带 instruction、snapshot ID、不可变 snapshot、fingerprint 和 operation ID；2 MiB 以上 snapshot 分块保存为 runtime 根目录内相对只读引用。
- Worker 校验 snapshot ID、SHA-256、大小和 runtime 路径边界，把冻结材料/指令加入模型输入，并对原子成果写入复用 operation ID；series/outline 恢复按 operation ID 隔离。
- repository 的幂等请求哈希统一委托排序 UTF-8 JSON 的 SHA-256 helper。

## GREEN / 回归原始统计

- `tests/test_creative_application_service.py tests/test_creative_phase4_generation.py tests/test_creative_operation_idempotency.py`：`53 passed in 3.83s`。
- `tests/test_original_project_api.py tests/test_project_member_stage_editing.py`：`11 passed, 11 warnings in 7.17s`。
- `tests/test_writer_agent_api.py tests/test_writer_agent_service.py`：`11 passed in 3.21s`。
- 聚焦服务/worker/WebGUI shared dependency/original concurrency：`41 passed, 17 warnings in 5.26s`。
- 指定三文件最终回归：`226 passed, 7 failed, 16 warnings in 5.60s`。7 个失败均为 Task 3 以外、当前 HEAD 已不满足的静态 UI 断言：navigation route 字符串、rank-source JS 方法、topic lifecycle 文案、两个旧 fetch 方法名、writing page script include、红果设置文案；Task 3 新增断言均通过。
- Python header hook：Passed。
- `python -m py_compile`（全部触及 Python 文件）：Passed。
- `git diff --check`：Passed。
- 完整 pre-commit：首次被全局 virtualenv cache ACL 拦截；重定向本地 cache 后环境安装超时。必需的 file-header hook 已单独通过。

## 改动文件

- `creative/application_service.py`
- `creative/api.py`
- `creative/schema.py`
- `creative/phase4_worker.py`
- `creative/repository.py`
- `WebGUI.py`
- `writer_agent/api.py`
- `tests/test_creative_application_service.py`
- `tests/test_creative_phase4_generation.py`
- `tests/test_webgui_ui_changes.py`

## 自审

- 未 reset、checkout、stash、clean 或改写历史。
- 未删除、修改或暂存既有 controller 产物；Task 3 本地 pytest/pre-commit scratch 亦未暂存。
- 原创项目仍通过 `OriginalProjectService`，没有绕过 project_creator/bootstrap。
- 阶段写入复用现有 active membership、permission service、workflow adapter 与 Task 2 repository 幂等接口。
- snapshot 文件只允许 runtime 根内相对路径，Worker 对路径越界、大小、ID、引用 SHA 与内容 SHA 全部 fail closed。

## 疑虑

- 指定三文件中有 7 个与 Task 3 无关的既有 WebGUI 静态断言失败，未越界修改相应 UI 文件。
- 当前 sandbox 无法写 shared `.git` worktree 元数据；controller 已明确接管提交。实现文件保持原样，未暂存。

## Commit SHA

`648c1b3` (`refactor: 共享创作阶段应用服务`；因子代理 sandbox 无法写 shared `.git` worktree metadata，由 controller 仅执行暂存与提交，未改动实现文件。)

## Fix round 1

### 状态

DONE_WITH_CONCERNS。C1、I1–I6、M1 已修复，并以行为测试扩展覆盖 M2。按控制器指示，未执行任何 Git 元数据、暂存、提交、ACL 或 `icacls` 操作；修复提交由 controller 完成。

### RED 证据

- 首轮聚焦命令：`$env:UV_CACHE_DIR='.uv-cache-task3'; uv run pytest tests/test_creative_application_service.py tests/test_creative_phase4_generation.py tests/test_creative_operation_idempotency.py tests/test_writer_agent_api.py -q -p no:cacheprovider --basetemp=.pytest-task3-fix1-red -k 'first_brief_rejects or lock_replay_and_changed or generation_always_adds or hydrates_legacy or worker_rejects_oversized or worker_requires_read_only or worker_requires_and_validates_server or explicit_episode_revision or generation_operation_reservation or writer_router_retains'`。
- 结果：`10 failed, 60 deselected in 2.48s`。失败分别证明第二根 Brief 未拒绝、锁重放在副作用后检查、生成缺少服务端 authority、缺少公开水合、inline 超限未拒绝、authority worker 校验缺失、精确 episode parent/CAS 缺失、生成 reservation 缺失、writer 注入未保留。最初的 `read_only` 用例另暴露测试自身缺少 `json` import；只修正该测试缺陷后继续取得真实 RED。
- `read_only` 真实 RED：`uv run pytest tests/test_creative_phase4_generation.py::test_worker_requires_read_only_chunk_reference -q -p no:cacheprovider --basetemp=.pytest-task3-fix1-red-readonly` → `1 failed in 1.37s`，原因为缺少 marker 时未抛出异常。
- WebGUI 公共 API RED 尝试：测试模块收集被 `PostgreSQL system database is unavailable or not at Alembic head` 阻断；配置测试数据库后已在 GREEN 阶段成功执行。

### 实现

- 保留应用服务内部 `ResourceRef`，新增公开水合方法；Brief、人物、分集大纲、Beats、剧本直接保存路由均恢复完整 legacy resource body，真实五阶段消费者可立即编辑和锁定。
- 所有五类 revision repository 写入在同一个 `BEGIN IMMEDIATE` 事务内校验 `expected_project_revision` 与精确 `replaces_resource_id`，请求哈希包含二者；人物/关系按精确旧 batch key、outline 按精确旧 batch episode、Beats/script 按精确 episode row 建立 parent。生产 Brief version 与 redo-capable 直存路由统一进入应用服务 revision 边界，过期 authority 映射 HTTP 409。
- 锁操作在 domain/workflow 副作用前持久化 reservation；同请求完整重放、同 key 不同请求先拒绝、并发 pending 拒绝，异常会释放 reservation，使 domain 已锁而 workflow 未完成的现有恢复路径可重试。Brief lock 也进入该边界，并将 `difference_note` 纳入哈希且保持完整旧响应。
- 新增生成 operation reservation 表与原子 API，绑定 operation/hash/owner/batch/result。outline 与 series Beats/script 在模型调用前 reserve；changed payload 和并发 owner 被拒绝，Task retry 仅可凭 `retry_of_job_id` 接管已有 checkpoint，checkpoint 在其写事务内复验 hash、batch 和 owner，完成结果可跳过模型与持久化直接重放。
- creative API 改用公开 `start_generation_compat()`，不再调用私有 `_snapshot_extra()`，仍由 API 保留 workflow job linking、旧响应、audit 和 URL 行为。writer-agent router 显式保留同一 repository/application-service 注入实例，没有提前实现后续任务的 project actions。
- 每个生成 payload 独立加入服务端 authority envelope 及 SHA-256；envelope 含 project/revision、锁定 Brief/人物/outline ID 与指纹，以及当前锁定 Beats 指纹。worker 在 provider 创建和任何持久化前强制验证 envelope hash 与 repository 当前 authority。
- 首个 Brief repository 事务原子限制为 original project 且不存在 root Brief；幂等重放先于 invariant 检查，同 key 不同 payload 仍先拒绝。
- worker 对 inline snapshot 重新 canonicalize 并强制不超过 2 MiB；chunk reference 要求精确字段和 `read_only is True`，并继续执行相对路径、resolved symlink escape、size、SHA、ID 与 JSON 校验。

### GREEN / 回归统计

- 新增聚焦契约：`10 passed, 60 deselected in 2.00s`。
- 最终 Task 3 核心、worker、operation、writer-agent、original API、成员编辑及真实五阶段 route compatibility 联合回归：`88 passed, 13 warnings in 12.41s`。
- 生成 worker + operation reservation/五类精确 lineage：`56 passed in 3.66s`；operation 文件单独为 `17 passed in 1.36s`。
- 配置测试 PostgreSQL 后 WebGUI 新公共 API/shared dependency 聚焦：`2 passed, 16 warnings in 3.50s`。
- 完整 `tests/test_webgui_ui_changes.py`：`188 passed, 7 failed, 16 warnings in 7.20s`。七项与原审查记录完全一致，均是未触及的 navigation/template/static JS 基线断言。
- 触及 Python 文件 `py_compile`：Passed。
- 触及 11 个 Python 文件 header check：`11 already compliant, 0 errors`。
- 遵从 controller 的禁止 Git 元数据操作指令，未运行 `git diff --check`、pre-commit 的 Git 驱动流程、staging 或 commit。

### 自审

- C1：真实 `test_project_governance_http_matrix_and_five_stage_collaboration` 已走通 Brief → characters → outline → Beats → script 的 create/edit/lock 全链路，证明旧响应 body 和即时消费者兼容。
- I1：新增一个 repository 行为测试同时验证 Brief、character batch、outline batch、Beats（既有聚焦）与 script 的精确 parent；stale revision 在 write lock 内拒绝。
- I2：reservation owner 是每次调用唯一值；pending 并发不会进入 transition；异常释放后，现有 transition partial-domain recovery 能继续 workflow。
- I3：owner 校验阻止旧 worker 在 retry 接管后继续 checkpoint/complete；新 owner 必须显式引用失败 job，且只能沿同 hash/batch 恢复。
- I4/I5：production source 中无 `._snapshot_extra(` 调用；worker 的 mandatory authority 校验发生在 provider 构造前。
- I6/M1：first-root 与 snapshot 约束均位于 repository/worker 的可信边界，而非仅依赖 API producer。
- 未修改无关 UI、workflow node catalog、identity constructor compatibility 或 scratch artifacts。

### 改动文件

- `creative/application_service.py`
- `creative/api.py`
- `creative/schema.py`
- `creative/phase4_worker.py`
- `creative/repository.py`
- `writer_agent/api.py`
- `tests/test_creative_application_service.py`
- `tests/test_creative_phase4_generation.py`
- `tests/test_creative_operation_idempotency.py`
- `tests/test_writer_agent_api.py`
- `tests/test_webgui_ui_changes.py`
- `.superpowers/sdd/2026-08-20-writer-agent-project-orchestration/task-3-report.md`

### 疑虑

- 完整 WebGUI 文件仍有七个既有、未触及的静态 UI 断言失败：planning navigation route、rank-source method、topic lifecycle 文案、两个旧 topic refresh 方法名、writing page script include、红果设置文案。
- 更广的旧 workflow/e2e 集合仍有与 Task 3 无关的 `IdentityRepository(Path)` constructor drift setup errors，以及既有 workflow node/static 断言差异；未越界修复。
- controller 明确禁止本轮 Git 操作，因此 whitespace/diff 的 Git 检查和提交 SHA 无法由本轮执行者提供。

### Commit SHA

`PENDING_CONTROLLER_COMMIT`
