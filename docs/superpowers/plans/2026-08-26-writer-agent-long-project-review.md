# 编剧大师完整项目长审核实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为编剧大师增加可预检、可恢复、可追溯的完整项目后台审核，同时保留快速点评的 180/210 秒同步路径。

**架构：** 通过确定性意图路由把完整大纲、完整剧本和完整项目审核从 Writer Agent 同步 LLM 分流出来。服务端先冻结当前活动资源快照，再使用现有 `script_reviews` 持久化和 JobManager 启动独立 `project_review` Worker；Worker 动态分批但批内逐集处理，每集保存检查点。Writer Agent 会话只保存审核 ID，页面通过专用接口恢复清单、进度和报告。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、asyncio、现有 OpenAI-compatible LLM Provider、JobManager、原生 JavaScript、Bootstrap、Playwright、pytest。

---

## 工作区保护规则

当前工作区包含大量用户已有且与 Writer Agent、创作项目和大纲功能重叠的未提交修改。执行每个任务时必须遵守：

- 不使用 `git reset`、`git restore`、`git checkout --` 或 stash。
- 修改前先对本任务列出的每个目标文件运行 `git diff -- 文件路径`，理解并保留现有改动；例如任务 5 先运行 `git diff -- writer_agent/api.py writer_agent/store.py WebGUI.py`。
- 只用 `apply_patch` 编辑源码。
- 所有新建 `.py` 文件复制仓库现有版权头，满足 `tools/file_header_manager.py` 检查。
- 测试产生的临时目录使用新的唯一名称，不删除用户已有测试目录。
- 每次提交显式列出本任务文件；工作区已有暂存内容不得进入提交。
- 对新文件使用该任务步骤中列出的精确 `git add --` 命令，提交使用该任务步骤中列出的精确 `git commit --only` 命令。
- 8899 当前服务在所有运行中任务进入终态前不得停止或重启。

## 文件结构

### 新建文件

- `project_review/__init__.py`：完整项目审核包入口。
- `project_review/source.py`：当前活动版本解析、来源快照、加载清单、指纹和动态批次策略。
- `project_review/service.py`：逐集提示词、结果规范化、检查点、批次摘要、最终汇总和 Markdown 导出。
- `project_review/worker.py`：后台 Worker、阶段恢复、请求事件和审核记录状态转换。
- `writer_agent/project_review.py`：长审核意图解析及预检、创建、查询、恢复的应用协调器。
- `templates/review/_writer_project_review.html`：双阶段抽屉和完整审核进度组件。
- `static/js/review/writer_project_review.js`：清单渲染、确认、轮询、慢响应和恢复交互。
- `tests/test_project_review_source.py`：来源选择、清单、指纹和批次边界测试。
- `tests/test_project_review_service.py`：提示词、逐集检查点、批次摘要和最终报告测试。
- `tests/test_project_review_worker.py`：Worker 事件、失败恢复和产物测试。
- `tests/test_project_review_repository.py`：幂等审核记录和恢复 CAS 测试。
- `tests/test_writer_project_review_browser.py`：双阶段抽屉和轮询交互测试。

### 修改文件

- `analysis/llm.py`：允许 Provider 工厂接收可选重试回调，保持现有调用兼容。
- `config/bilibili_config.py`：增加 `PROJECT_REVIEW_LLM_*` 独立配置。
- `creative/repository.py`：增加幂等创建、项目审核完成和可恢复状态转换。
- `screenplay_review/api.py`：正式剧本审核列表和确认接口排除 `audit_scope` 项目长审核。
- `runtime/worker_launcher.py`：注册 `project_review.worker`。
- `runtime/job_manager.py`：允许不同 review ID 的 `screenplay_review` 和 `project_review` 沿用审核并发隔离规则。
- `writer_agent/api.py`：注入 JobManager、确定性分流、预检/确认/查询/恢复/导出路由。
- `writer_agent/store.py`：幂等追加带审核 ID 的会话状态消息。
- `WebGUI.py`：把现有 JobManager 注入 Writer Agent 路由。
- `templates/review/writer_agent.html`：引入完整审核局部模板和脚本。
- `static/js/review/writer_agent.js`：把完整审核消息交给独立 UI 模块渲染。
- `static/css/workspaces.css`：双阶段抽屉、资源状态、进度和移动端样式。
- `tests/analysis/test_llm.py`：独立审核 Provider 配置与重试回调测试。
- `tests/test_script_review_api.py`：正式审核与项目长审核隔离回归测试。
- `tests/test_worker_launcher.py`：新 Worker 命令测试。
- `tests/test_job_manager.py`：两种审核 Job 的并发隔离测试。
- `tests/test_writer_agent_api.py`：意图分流、预检、确认、权限、幂等和恢复接口测试。
- `tests/test_writer_agent_browser.py`：现有 Writer Agent 渲染兼容测试。
- `tests/test_webgui_ui_changes.py`：模板和静态资源接线测试。

### 明确不修改

- `screenplay_review/service.py` 的锁定来源构造和正式审核语义。
- 现有 Writer Agent 快速点评的 `WRITER_AGENT_LLM_*` 与 180/210 秒限制。
- 数据库表结构和 Alembic 版本。

## 任务 1：当前活动来源快照与动态批次

**文件：**
- 创建：`project_review/__init__.py`
- 创建：`project_review/source.py`
- 创建：`tests/test_project_review_source.py`

- [ ] **步骤 1：编写动态批次边界失败测试**

```python
from project_review.source import batch_size_for, build_batches


def test_dynamic_batch_policy_uses_actual_stage_unit_count() -> None:
    assert [batch_size_for(total) for total in (1, 9, 10, 200, 201, 205)] == [1, 1, 10, 10, 20, 20]
    assert build_batches([*range(1, 202)])[-1] == [201]
    assert build_batches([*range(1, 206)])[-1] == [201, 202, 203, 204, 205]
```

- [ ] **步骤 2：编写当前活动版本和范围失败测试**

构造一个项目：Brief 同时有锁定 v2 和退回 v3；人物同时有历史批次和活动草稿批次；第 1 集大纲有历史 v1、锁定 v2、草稿 v3；第 2 集只有锁定大纲；第 1 集剧本引用旧 Beats。断言：

```python
source = build_project_review_source(project, audit_scope="project")

assert source["audit_scope"] == "project"
assert source["brief"]["version"] == 3
assert source["stages"]["outline"]["episode_numbers"] == [1, 2]
assert source["stages"]["outline"]["units"][0]["resource"]["version"] == 3
assert source["stages"]["script"]["episode_numbers"] == [1]
assert source["manifest"]["outline_count"] == 2
assert source["manifest"]["script_count"] == 1
assert source["manifest"]["missing_episode_numbers"] == []
assert source["upstream_mismatches"][0]["episode_number"] == 1
```

再分别断言 `audit_scope="outline"` 不包含 script 阶段、`audit_scope="script"` 只按实际剧本数分批，且 `target_episode_count=200` 不会把只有 2 集内容的项目显示成 200 个工作单元。

- [ ] **步骤 3：运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_project_review_source.py -q
```

预期：FAIL，报错 `ModuleNotFoundError: No module named 'project_review'`。

- [ ] **步骤 4：实现来源模型、批次策略和确定性指纹**

在 `project_review/source.py` 提供以下公共接口：

```python
AUDIT_SCOPES = frozenset({"outline", "script", "project"})


def batch_size_for(total: int) -> int:
    if total < 1:
        return 0
    if total < 10:
        return 1
    if total <= 200:
        return 10
    return 20


def build_batches(episode_numbers: list[int]) -> list[list[int]]:
    numbers = sorted({int(number) for number in episode_numbers if int(number) > 0})
    size = batch_size_for(len(numbers))
    return [numbers[index:index + size] for index in range(0, len(numbers), size)] if size else []


def build_project_review_source(project: dict[str, object], audit_scope: str) -> dict[str, object]:
    """Freeze current active resources, manifest, mismatches, batches and fingerprint."""


def project_review_fingerprint(source: dict[str, object]) -> str:
    """Hash canonical JSON while excluding the existing fingerprint field."""
```

实现要求：

- 资源状态排除 `superseded`、`historical`。
- `draft`、`returned`、`submitted` 优先于锁定指针指向的资源。
- 同一候选按 `(version, id)` 取最大项。
- 大纲按活动 `batch_id` 和集号去重；Beats、剧本按集号取当前项。
- `project` 范围分别为 outline、script 阶段计算 `batches`。
- Manifest 只含元数据、确定性截断摘要和源页面 URL，不复制完整正文。
- Source 快照保留完整内容供 Worker 使用。
- 上游不一致写入 `upstream_mismatches`，不删除对应工作单元。
- 字符数使用规范化后的 `json.dumps(content, ensure_ascii=False, sort_keys=True)` 长度。

- [ ] **步骤 5：补充空内容、缺失集号和摘要截断测试**

断言空大纲被列入 `unreadable_resources`；集号 `[1, 3]` 产生缺失集号 `[2]`；Manifest 摘要每项不超过 200 字符；两次构造相同项目产生相同 fingerprint，修改任一字段后 fingerprint 改变。

- [ ] **步骤 6：运行任务测试并提交**

运行：

```powershell
uv run pytest tests/test_project_review_source.py -q
```

预期：全部 PASS。

提交：

```powershell
git add -- project_review/__init__.py project_review/source.py tests/test_project_review_source.py
git commit --only project_review/__init__.py project_review/source.py tests/test_project_review_source.py -m "feat: 构建完整项目审核快照"
```

## 任务 2：逐集审核、检查点与报告合同

**文件：**
- 创建：`project_review/service.py`
- 创建：`tests/test_project_review_service.py`

- [ ] **步骤 1：编写单次请求输入边界失败测试**

```python
from project_review.service import build_unit_messages


def test_outline_unit_prompt_loads_only_current_unit_and_bounded_context(source) -> None:
    messages = build_unit_messages(
        source,
        stage_kind="outline",
        episode_number=31,
        checkpoint={"continuity_state": {"unresolved_conflicts": ["失踪真相"]}},
    )
    payload = json.loads(messages[-1]["content"])
    assert payload["current_unit"]["episode_number"] == 31
    assert payload["loaded_resource_refs"]
    assert payload["checkpoint_context"]["unresolved_conflicts"] == ["失踪真相"]
    assert "完整项目历史正文" not in payload
```

剧本范围额外断言当前集输入包含对应 outline、beats 和 script；大纲范围不包含 script 正文。系统提示必须要求每条 finding 输出 `resource_id`、`version`、`field`、`evidence` 和 `suggestion`，并禁止改写来源内容。

- [ ] **步骤 2：编写动态批次、逐集检查点和恢复失败测试**

使用可记录调用的 Fake Provider 审核 12 集大纲。第一次运行在第 7 集抛错，检查 `on_checkpoint` 已保存 1–6 集。第二次把该 partial report 作为 `initial_report`，断言 Provider 只收到第 7–12 集和缺失批次摘要，不重新请求 1–6 集。

```python
report = await review_project_source(
    provider,
    source,
    on_checkpoint=checkpoints.append,
    on_request=requests.append,
    initial_report=partial,
)
assert [item["episode_number"] for item in report["stage_results"]["outline"]] == list(range(1, 13))
assert len({(item["stage_kind"], item["episode_number"]) for item in report["all_unit_results"]}) == 12
```

- [ ] **步骤 3：运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_project_review_service.py -q
```

预期：FAIL，报错无法导入 `project_review.service`。

- [ ] **步骤 4：实现审核合同和恢复状态机**

在 `project_review/service.py` 实现审核消息、结果规范化、逐集循环、摘要和导出。结果规范化的核心代码固定为：

```python
def unit_key(stage_kind: str, episode_number: int) -> str:
    return f"{stage_kind}:{int(episode_number)}"


def normalize_unit_result(payload: dict, *, stage_kind: str, episode_number: int) -> dict:
    allowed = {"P0", "P1", "P2"}
    findings = []
    for item in payload.get("findings") or []:
        severity = str(item.get("severity") or "P2").upper()
        findings.append({
            "severity": severity if severity in allowed else "P2",
            "category": str(item.get("category") or "continuity"),
            "resource_id": item.get("resource_id"),
            "version": item.get("version"),
            "field": str(item.get("field") or "content"),
            "evidence": str(item.get("evidence") or "证据不足"),
            "suggestion": str(item.get("suggestion") or ""),
        })
    return {
        "stage_kind": stage_kind,
        "episode_number": int(episode_number),
        "summary": str(payload.get("summary") or ""),
        "findings": findings,
        "continuity_state": payload.get("continuity_state") or {},
    }
```

同时实现 `build_unit_messages`、`build_batch_summary_messages`、`build_final_summary_messages`、`review_project_source` 和 `build_project_review_markdown`。三个消息构造器都把输入通过 `json.dumps(payload, ensure_ascii=False)` 放入 user message；`review_project_source` 使用普通 `for` 循环依次 await 每个工作单元，禁止 `gather` 或并行 task。

检查点 JSON 固定包含：

```python
{
    "partial": True,
    "audit_scope": source["audit_scope"],
    "stage_results": {"outline": [], "script": []},
    "batch_summaries": [],
    "continuity_state": {},
    "cursor": {"stage_kind": "outline", "episode_number": 7, "batch_index": 1},
}
```

逐集结果保存后立即调用 `on_checkpoint`；批次摘要和最终汇总使用独立检查点键。恢复时按 `(stage_kind, episode_number)` 去重，并只补齐缺失逐集结果、缺失批次摘要或最终汇总。

- [ ] **步骤 5：实现报告规范化和 Markdown 测试**

断言最终报告包含 `ai_verdict`、`summary`、`core_findings`、两个阶段结果、批次摘要、来源 manifest 和上游不一致问题；Markdown 列出项目、范围、快照指纹、资源版本、每集证据和跨集问题。非法 verdict 统一规范为“建议修改”，severity 只允许 `P0/P1/P2`。

- [ ] **步骤 6：运行任务测试并提交**

运行：

```powershell
uv run pytest tests/test_project_review_source.py tests/test_project_review_service.py -q
```

预期：全部 PASS。

提交：

```powershell
git add -- project_review/service.py tests/test_project_review_service.py
git commit --only project_review/service.py tests/test_project_review_service.py -m "feat: 实现可恢复的逐集项目审核"
```

## 任务 3：审核记录幂等生命周期与正式审核隔离

**文件：**
- 修改：`creative/repository.py:4575-4775`
- 修改：`screenplay_review/api.py:73-340`
- 创建：`tests/test_project_review_repository.py`
- 修改：`tests/test_script_review_api.py`

- [ ] **步骤 1：编写幂等创建和完成状态失败测试**

```python
first, first_created = repository.create_project_review_once(
    project_id=11,
    episode_numbers=[1, 2],
    source_fingerprint="snapshot-a",
    source_payload={"audit_scope": "outline", "request_key": "confirm-1"},
    created_by="writer-1",
    request_key="confirm-1",
)
second, second_created = repository.create_project_review_once(
    project_id=11,
    episode_numbers=[1, 2],
    source_fingerprint="snapshot-a",
    source_payload={"audit_scope": "outline", "request_key": "confirm-1"},
    created_by="writer-1",
    request_key="confirm-1",
)
assert first["id"] == second["id"]
assert (first_created, second_created) == (True, False)
completed = repository.complete_project_review(first["id"], {"ai_verdict": "通过"})
assert completed["status"] == "completed"
```

再使用两个线程同时调用，断言数据库只产生一条匹配 `request_key` 的记录。

- [ ] **步骤 2：编写恢复 CAS 和正式审核隔离失败测试**

断言 `failed` 且 `source_payload.audit_scope` 存在的记录可以进入 `queued` 恢复；同一 operation 已经 queued/running 时重复恢复返回当前记录；`completed` 或普通正式剧本审核调用恢复时抛出明确错误。正式 `/api/script-reviews` 列表不返回项目长审核，confirm 接口拒绝 `audit_scope` 记录。

- [ ] **步骤 3：运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_project_review_repository.py tests/test_script_review_api.py -q
```

预期：新增测试 FAIL，提示缺少 `create_project_review_once`。

- [ ] **步骤 4：实现原子幂等创建和状态转换**

在 `CreativeRepository` 增加 `create_project_review_once`、`complete_project_review` 和 `prepare_project_review_resume`。识别幂等键的辅助函数使用：

```python
def _review_request_key(row: sqlite3.Row) -> str:
    try:
        payload = json.loads(row["source_payload_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    return str(payload.get("request_key") or "")
```

`create_project_review_once` 使用 `BEGIN IMMEDIATE`，在同一写事务内按 `created_by + project_id` 读取候选记录并解析 `source_payload_json.request_key`。相同 request key 和相同 fingerprint 返回既有记录；相同 request key 但 fingerprint 不同抛出 `ValueError("确认请求已用于不同项目快照")`。新记录继续写入现有 `script_reviews`，使用 `source_type="project"`、`mode="collection"`，不改表结构。

`complete_project_review` 写入 `status="completed"`，不能进入正式审核的 `pending_confirmation`。`prepare_project_review_resume` 保留 report 和 source payload，只清空 error 并把 failed 原子改为 queued；queued/running 输入按幂等重放返回当前记录，completed 和非项目长审核输入拒绝。

- [ ] **步骤 5：隔离正式剧本审核 API**

在 `screenplay_review/api.py` 的 list、get/confirm/delete相关路径中保持既有访问控制，并确保：正式列表过滤 `source_payload.audit_scope`；confirm 对项目长审核返回 409“完整项目审核不进入人工确认流程”；现有正式记录行为不变。

- [ ] **步骤 6：运行任务测试并提交**

运行：

```powershell
uv run pytest tests/test_project_review_repository.py tests/test_script_review_api.py tests/test_script_reviews.py -q
```

预期：全部 PASS。

提交：

```powershell
git add -- tests/test_project_review_repository.py
git commit --only creative/repository.py screenplay_review/api.py tests/test_project_review_repository.py tests/test_script_review_api.py -m "feat: 持久化完整项目审核生命周期"
```

## 任务 4：独立 LLM 配置、Worker 与任务事件

**文件：**
- 修改：`analysis/llm.py:417-445`
- 修改：`config/bilibili_config.py:94-115`
- 创建：`project_review/worker.py`
- 修改：`runtime/worker_launcher.py:15-35`
- 修改：`runtime/job_manager.py:31-38,470-515`
- 修改：`tests/analysis/test_llm.py`
- 创建：`tests/test_project_review_worker.py`
- 修改：`tests/test_worker_launcher.py`
- 修改：`tests/test_job_manager.py`

- [ ] **步骤 1：编写独立 Provider 配置失败测试**

```python
def test_project_review_profile_uses_high_600_seconds_and_two_retries():
    settings = resolve_llm_settings(config, prefix="PROJECT_REVIEW_LLM")
    assert settings["reasoning_effort"] == "high"
    assert settings["timeout_seconds"] == 600
    assert settings["max_retries"] == 2
```

再用 `create_llm_provider_from_config(DummyConfig, prefix="PROJECT_REVIEW_LLM", retry_callback=callback)` 断言 callback 被传入 Provider，原有无 callback 调用保持可用。
把 `PROJECT_REVIEW_LLM_TIMEOUT_SECONDS=invalid`、`PROJECT_REVIEW_LLM_MAX_RETRIES=invalid` 和 `PROJECT_REVIEW_LLM_REASONING_EFFORT=ultra-invalid` 分别注入环境，断言配置辅助函数回退到 600、2、`high`，并由 `caplog` 捕获 warning；配置模块导入不能失败。

- [ ] **步骤 2：编写 Worker 命令和审核并发失败测试**

```python
assert build_worker_command(
    ".runtime/jobs/project-review.json",
    platform="creative",
    crawler_type="project_review",
) == [sys.executable, "-m", "project_review.worker", "--job-file", ".runtime/jobs/project-review.json"]
```

在 JobManager 测试中断言不同 `review_id` 的 `screenplay_review`/`project_review` 可各自保留；同一个 review ID 的重复 operation 返回同一 Job，不重复启动。

- [ ] **步骤 3：编写 Worker 检查点、事件和恢复失败测试**

构造 12 集来源和 Fake Provider。断言 Worker 事件的具体字段：

```python
event = next(item for item in emitted if item["payload"].get("current_episode") == 7)
payload = event["payload"]
assert payload["audit_scope"] == "outline"
assert payload["stage_kind"] == "outline"
assert payload["batch_size"] == 10
assert (payload["batch_start"], payload["batch_end"]) == (1, 10)
assert payload["reasoning_effort"] == "high"
assert payload["prompt_chars"] > 0
assert (payload["request_attempt"], payload["request_max_attempts"]) == (1, 3)
assert payload["loaded_resource_refs"]
```

模拟第 7 集失败后断言审核记录为 failed 且保留 1–6 集；重新运行相同 review ID 后只处理第 7 集及以后。批次摘要失败和最终汇总失败分别只重做缺失阶段。

- [ ] **步骤 4：运行测试确认失败**

运行：

```powershell
uv run pytest tests/analysis/test_llm.py tests/test_worker_launcher.py tests/test_job_manager.py tests/test_project_review_worker.py -q
```

预期：新增测试 FAIL，提示缺少配置或 Worker 路由。

- [ ] **步骤 5：实现 Provider 回调和独立配置**

把工厂签名改为：

```python
def create_llm_provider_from_config(
    config_module: Any,
    prefix: str = "LLM",
    *,
    retry_callback: Callable[[int, int, str], None] | None = None,
) -> OpenAICompatibleLLMProvider:
```

向 Provider 传入 `retry_callback`。在 `config/bilibili_config.py` 增加 `_env_int(name, default)` 和 `_env_choice(name, default, allowed)`；两者捕获非法环境值并使用模块 logger 记录 warning。项目审核配置使用这两个安全辅助函数：

```python
PROJECT_REVIEW_LLM_REASONING_EFFORT = _env_choice(
    "PROJECT_REVIEW_LLM_REASONING_EFFORT", "high", {"low", "medium", "high", "xhigh", "max"}
)
PROJECT_REVIEW_LLM_TIMEOUT_SECONDS = _env_int("PROJECT_REVIEW_LLM_TIMEOUT_SECONDS", 600)
PROJECT_REVIEW_LLM_MAX_RETRIES = _env_int("PROJECT_REVIEW_LLM_MAX_RETRIES", 2)
```

- [ ] **步骤 6：实现 Worker 与可选进度 payload**

`project_review.worker` 从 `job.extra.review_id` 和 `db_path` 读取冻结快照，调用 `review_project_source`，并：

- 开始时把记录改为 running；
- 不在整个 `review_project_source` 外层增加 `asyncio.wait_for`，任务总时长不设硬上限；
- 每集结果和摘要通过 repository checkpoint 持久化；
- `on_request` 发送批次、集号、时间、字符数、档位和资源引用；
- Provider retry callback 发送尝试次数，不记录正文；
- 完成时调用 `complete_project_review`；
- 失败时调用 `fail_script_review` 后让进程非零退出；
- 报告写入 `data/project_reviews/{job.job_id}/report.json` 并发送 artifact。

把 `project_review` 加入 Worker launcher，并把 JobManager 审核并发集合调整为 `{"screenplay_review", "project_review"}`，identity 仍使用 `review_id`。

- [ ] **步骤 7：运行任务测试并提交**

运行：

```powershell
uv run pytest tests/analysis/test_llm.py tests/test_worker_launcher.py tests/test_job_manager.py tests/test_project_review_worker.py -q
```

预期：全部 PASS。

提交：

```powershell
git add -- project_review/worker.py tests/test_project_review_worker.py
git commit --only analysis/llm.py config/bilibili_config.py project_review/worker.py runtime/worker_launcher.py runtime/job_manager.py tests/analysis/test_llm.py tests/test_project_review_worker.py tests/test_worker_launcher.py tests/test_job_manager.py -m "feat: 运行完整项目后台审核"
```

## 任务 5：Writer Agent 意图分流和审核接口

**文件：**
- 创建：`writer_agent/project_review.py`
- 修改：`writer_agent/store.py:200-365,515-530`
- 修改：`writer_agent/api.py:120-930`
- 修改：`WebGUI.py:542-705`
- 修改：`tests/test_writer_agent_api.py`

- [ ] **步骤 1：编写确定性意图分流失败测试**

```python
@pytest.mark.parametrize((message, expected), [
    ("审核更新后的项目大纲", "outline"),
    ("审核全部大纲", "outline"),
    ("审核完整剧本", "script"),
    ("审核完整项目", "project"),
    ("审核一下这一集", None),
    ("点评这段台词", None),
])
def test_long_project_review_scope(message, expected):
    assert long_project_review_scope(message) == expected
```

API 测试把 `create_provider` 替换为一旦调用就报错的 Fake。关联项目后发送“审核全部大纲”，断言响应包含 `kind="project_review_offer"`、`audit_scope="outline"`，Provider 调用数为 0；普通“审核一下这一集”仍调用同步 Provider。

- [ ] **步骤 2：编写预检、指纹冲突和权限失败测试**

覆盖：

- 未关联项目返回 409 并提示先关联；
- 无项目读取权限返回 403；
- 项目创建者、当前项目成员和可管理该项目的角色在具备 `script.review.generate/read` 权限时允许预检、创建和读取；
- 预检返回 manifest、fingerprint 和实际批次；
- 预检后修改项目，再确认旧 fingerprint 返回 409 和刷新后的 manifest；
- 确认使用 `client_request_id`，重复请求只生成一个 review 和一个 Job；
- 确认后会话只追加一条 `kind="project_review"`、带 review ID 的助手消息。

- [ ] **步骤 3：编写状态、导出和恢复失败测试**

断言：只有会话所有者或系统管理员且仍有项目读取权限可以查询/导出；失败记录调用 resume 使用相同 `operation_id=f"project-review:{review_id}"` 和 `retry_failed_operation=True`；重复 resume 不产生第二个进程；completed 记录不可恢复。

- [ ] **步骤 4：运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_writer_agent_api.py -q
```

预期：新增测试 FAIL，提示缺少长审核路由或协调器。

- [ ] **步骤 5：实现协调器和会话消息幂等追加**

在 `writer_agent/project_review.py` 实现确定性解析器：

```python
def long_project_review_scope(message: str) -> str | None:
    normalized = "".join(str(message or "").lower().split())
    if any(term in normalized for term in ("完整项目", "全项目")):
        return "project"
    if any(term in normalized for term in ("全部剧本", "所有剧本", "完整剧本")):
        return "script"
    if any(term in normalized for term in ("全部大纲", "所有大纲", "完整大纲", "更新后的项目大纲")):
        return "outline"
    return None
```

并实现 `ProjectReviewCoordinator.preview/create/get/resume` 四个同步方法。每个方法先校验会话关联项目和当前账号读取权限；create 重新构造 source 并比较 fingerprint，get 校验 review 的 `writer_session_id`，resume 只接受 failed 记录并复用原 source。

Coordinator 使用 `CreativeRepository`、`WriterAgentStore`、PermissionService 和 JobManager。创建 Job 时调用 `get_or_create_job`，operation ID 固定为 `f"project-review:{review['id']}"`，extra 包含 review ID、项目 ID、创建账号和数据库路径。

在 Store 增加 `append_project_review_message_once`：使用 `BEGIN IMMEDIATE`，按 review ID 检查既有消息后再追加，避免确认重放导致重复消息。

- [ ] **步骤 6：接入 Writer Agent 路由**

给 `build_writer_agent_router` 和 `register_writer_agent_routes` 增加 `job_manager` 依赖，WebGUI 传入现有单例。

在 chat 中完成关联项目权限检查后、启动消息租约和同步 Provider 前执行长审核分流。保存用户消息和 offer 助手消息后直接返回，不进入 `writer_messages`。

增加内部路由：

```text
POST /api/writer-agent/sessions/{session_id}/project-reviews/preview
POST /api/writer-agent/sessions/{session_id}/project-reviews
GET  /api/writer-agent/sessions/{session_id}/project-reviews/{review_id}
POST /api/writer-agent/sessions/{session_id}/project-reviews/{review_id}/resume
GET  /api/writer-agent/sessions/{session_id}/project-reviews/{review_id}/export
```

请求模型分别包含 `audit_scope`、`expected_fingerprint` 和 `client_request_id`。导出调用 `build_project_review_markdown`。

- [ ] **步骤 7：运行任务测试并提交**

运行：

```powershell
uv run pytest tests/test_writer_agent_api.py tests/test_writer_agent_service.py tests/test_writer_agent_integration.py -q
```

预期：除仓库已知且与本功能无关的 authority/CAS 用例外，新增和相关测试全部 PASS；如果基线已修复，则整个命令全部 PASS。任何新失败必须修复后再提交。

提交：

```powershell
git add -- writer_agent/project_review.py
git commit --only writer_agent/project_review.py writer_agent/store.py writer_agent/api.py WebGUI.py tests/test_writer_agent_api.py -m "feat: 从编剧对话发起完整项目审核"
```

## 任务 6：双阶段抽屉、实时进度与恢复交互

**文件：**
- 创建：`templates/review/_writer_project_review.html`
- 创建：`static/js/review/writer_project_review.js`
- 修改：`templates/review/writer_agent.html`
- 修改：`static/js/review/writer_agent.js`
- 修改：`static/css/workspaces.css`
- 创建：`tests/test_writer_project_review_browser.py`
- 修改：`tests/test_writer_agent_browser.py`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写预检抽屉失败浏览器测试**

挂载 Writer Agent 会话和 `project_review_offer` 消息，mock preview 接口返回项目 11、Brief v3、人物批次、90 集大纲、字符数、缺失项和 fingerprint。点击“查看本次加载内容”后断言：

```python
drawer.get_by_text("本次加载内容").wait_for()
assert drawer.get_by_text("项目 #11").is_visible()
assert drawer.get_by_text("Brief v3").is_visible()
assert drawer.get_by_text("实际加载第 1–90 集").is_visible()
assert drawer.get_by_text("历史废弃版本").is_visible()
assert drawer.get_by_role("button", name="确认并开始完整审核").is_enabled()
```

展开资源项后显示字段摘要和“打开源内容”本地链接；链接必须通过现有 `safeWriterLocalUrl` 约束。

- [ ] **步骤 2：编写运行、慢响应和恢复失败浏览器测试**

mock 状态依次返回 running 第 31 集、request_started_at 超过 8 分钟、request_attempt=2、failed checkpoint=30、恢复后 running、completed。断言页面分别显示：

- “正在审核第 31 集（当前批次第 31–40 集）”；
- “模型响应较慢，任务仍在运行”；
- “第 31 集正在进行第 2 次尝试”；
- “从第 31 集继续”；
- “已完成 90/90 集”和报告下载按钮。

关闭抽屉后 polling 继续；重新 mount 相同会话时根据 review ID恢复 polling；`prefers-reduced-motion` 下无强制动画。

- [ ] **步骤 3：运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_writer_project_review_browser.py tests/test_writer_agent_browser.py tests/test_webgui_ui_changes.py -q
```

预期：新增测试 FAIL，找不到完整审核组件或脚本。

- [ ] **步骤 4：实现局部模板和独立 UI 控制器**

局部模板包含：

- `writer-project-review-drawer`；
- 冻结内容清单区；
- 当前请求内容区；
- 批次/逐集进度区；
- 慢响应、错误和阶段性结果提示；
- 确认、关闭、继续、查看结果和下载按钮。

`writer_project_review.js` 暴露：

```javascript
window.writerProjectReviewUI = {
  renderMessage(article, message, context) {},
  mountSession(session, context) {},
  disposeSession(sessionId) {}
};
```

控制器维护 review ID 到轮询 timer 的 Map。等待时长由 `request_started_at` 在浏览器计算；服务端无需每秒发送事件。终态停止 timer，页面关闭只隐藏抽屉，不调用取消接口。

- [ ] **步骤 5：接入现有 Writer Agent 渲染**

`writer_agent.js` 在 `renderConversation` 中识别 `project_review_offer` 和 `project_review`，委托新模块渲染；普通 material、文本报告和创作 action 保持原路径。`renderActiveSession` 调用 `mountSession`，切换会话时调用 `disposeSession`。

模板先加载 `writer_project_review.js`，再加载 `writer_agent.js`，并 include 局部模板。CSS 放入 `workspaces.css`，覆盖 1280px 三栏、1100px 双栏和 760px 移动端抽屉。

- [ ] **步骤 6：运行任务测试并提交**

运行：

```powershell
uv run pytest tests/test_writer_project_review_browser.py tests/test_writer_agent_browser.py tests/test_webgui_ui_changes.py -q
```

预期：全部 PASS。

提交：

```powershell
git add -- templates/review/_writer_project_review.html static/js/review/writer_project_review.js tests/test_writer_project_review_browser.py
git commit --only templates/review/_writer_project_review.html static/js/review/writer_project_review.js templates/review/writer_agent.html static/js/review/writer_agent.js static/css/workspaces.css tests/test_writer_project_review_browser.py tests/test_writer_agent_browser.py tests/test_webgui_ui_changes.py -m "feat: 展示完整项目审核清单与进度"
```

## 任务 7：端到端回归、项目 11 基准与安全发布

**文件：**
- 修改：仅限前述任务中被测试暴露出缺陷的文件

- [ ] **步骤 1：运行后端定向测试**

```powershell
uv run pytest tests/analysis/test_llm.py tests/test_project_review_source.py tests/test_project_review_service.py tests/test_project_review_worker.py tests/test_project_review_repository.py tests/test_script_reviews.py tests/test_script_review_api.py tests/test_worker_launcher.py tests/test_job_manager.py tests/test_writer_agent_api.py tests/test_writer_agent_service.py tests/test_writer_agent_integration.py -q
```

预期：本功能相关测试全部 PASS。仓库已有 authority/CAS 基线失败必须与修改前基线逐项比对；不得把新增失败归类为既有问题。

- [ ] **步骤 2：运行 UI 定向测试**

```powershell
uv run pytest tests/test_writer_project_review_browser.py tests/test_writer_agent_browser.py tests/test_webgui_ui_changes.py tests/test_original_project_browser.py -q
```

预期：全部 PASS。

- [ ] **步骤 3：运行完整测试和预提交检查**

```powershell
uv run pytest tests/ -q
uv run pre-commit run --all-files
```

预期：全部 PASS；如存在修改前即可复现的失败，记录完整测试名、错误和基线证据，不能修改无关代码掩盖失败。

- [ ] **步骤 4：使用项目 11 做只读快照基准**

在不启动审核 Worker 的情况下调用 source builder，核对：

- Manifest 的项目名称、Brief、人物、大纲和剧本版本与项目页面一致；
- 实际大纲/剧本数量而非目标集数决定批次；
- 每个工作单元列出资源 ID、版本、字段摘要和字符数；
- 上游不一致在清单中可见；
- Source fingerprint 连续构造一致；
- 日志和 Job payload 不含完整正文。

再用 Fake Provider 执行完整后台流程，确认请求顺序为逐集、批次摘要、最终汇总，并验证断点恢复。

- [ ] **步骤 5：检查 8899 上的活动任务并执行发布门禁**

通过现有已登录页面或 `/api/jobs` 查看 `pending`、`running`、`stopping` Job。只要存在活动任务：

- 不停止或重启 8899；
- 保留已完成实现和测试结果；
- 等任务进入终态后再继续发布。

没有活动任务后，解析 8899 listener 的 PID，并核对进程命令行属于当前工作区的 `uvicorn WebGUI:app`。只停止这个已验证 PID，然后使用隐藏窗口从 `D:\Crawler\AiWorker` 启动：

```powershell
Start-Process -WindowStyle Hidden -FilePath ".\.venv\Scripts\python.exe" -ArgumentList @("-m", "uvicorn", "WebGUI:app", "--host", "0.0.0.0", "--port", "8899") -WorkingDirectory "D:\Crawler\AiWorker"
```

不得按端口模糊终止其他进程，不得终止任何 Worker PID。

- [ ] **步骤 6：执行 8899 冒烟验证**

验证：

- `/review/writer-agent` 能加载新脚本且浏览器控制台无错误；
- 普通“审核一下这一集”仍走同步快速点评；
- “审核更新后的项目大纲”直接出现加载清单，不出现同步转圈；
- 预检展示真实资源版本和数量；
- 确认后后台任务显示当前批次、当前集、等待时间和检查点；
- 关闭并重新打开页面后进度恢复；
- Markdown 报告可以下载。

- [ ] **步骤 7：提交回归修复并记录最终状态**

若本任务产生回归修复，回到负责该文件的任务，使用该任务已经列出的精确 `git commit --only` 文件清单补交修复；任务 7 不创建包含不确定文件范围的笼统提交。

最终交付记录包括：定向测试结果、完整测试结果、项目 11 基准摘要、8899 PID、服务启动时间和冒烟结果。不得把工作区其他未提交修改纳入本功能提交。
