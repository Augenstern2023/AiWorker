# 编剧大师对话重新发送与系统回档实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为编剧大师的历史用户消息增加可审计、可幂等且能撤销项目创作副作用的回档与重新发送能力。

**架构：** WriterAgentStore 在每条用户消息处理前保存不可变轮次检查点，并把模型产生的动作归属到该检查点。RollbackService 以会话历史 revision 为并发边界，先处理活动任务，再校验并倒序补偿动作的创作与工作流快照，最后原子恢复会话历史；重新发送复用相同回档过程并以新消息 ID 调用现有消息管线。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、SQLAlchemy/PostgreSQL、原生 JavaScript、Bootstrap Icons、pytest、Playwright。

---

## 文件结构

- 创建 `writer_agent/rollback_service.py`：回档预览、冲突校验、动作倒序补偿和幂等阶段协调。
- 修改 `writer_agent/store.py`：迁移检查点/动作撤销/回档表，维护 history revision，提供检查点和会话恢复事务。
- 修改 `writer_agent/action_service.py`：把当前消息检查点传给候选动作持久化。
- 修改 `writer_agent/api.py`：建立检查点、抽取可复用消息处理函数，注册预览、回档和重新发送接口。
- 修改 `writer_agent/runner.py`：动作执行前后捕获撤销前镜像和后置条件。
- 修改 `creative/repository.py`：导出、校验和补偿一个项目受 Writer 动作影响的创作状态。
- 修改 `workflow/repository.py`：导出、校验和补偿一个项目受 Writer 动作影响的任务状态。
- 修改 `static/js/review/writer_agent.js`：消息操作按钮、预览确认、加载/错误状态及刷新。
- 修改 `templates/review/writer_agent.html`：消息操作区和确认对话框样式/结构。
- 修改 `tests/test_writer_agent_store.py`：检查点、history revision、恢复和幂等单元测试。
- 修改 `tests/test_writer_agent_api.py`：API、权限、冲突、动作补偿和重新发送集成测试。
- 修改 `tests/test_writer_agent_browser.py`：桌面、移动端、键盘和失败状态交互测试。

### 任务 1：轮次检查点与纯会话回档

**文件：**
- 修改：`writer_agent/store.py`
- 测试：`tests/test_writer_agent_store.py`

- [ ] **步骤 1：编写检查点生命周期的失败测试**

测试点名的破坏是“用户消息发送前没有留下可恢复边界”。使用真实临时 SQLite Store，先创建会话，再调用预期 API：

```python
checkpoint = store.begin_turn(
    session["id"], "writer-1", "message-1",
    original_message={"message": "重写第一幕", "display_user_message": True},
    content_hash="hash-1",
)
assert checkpoint["message_count_before"] == 1
assert checkpoint["report_count_before"] == 0
assert checkpoint["status"] == "open"
assert store.begin_turn(
    session["id"], "writer-1", "message-1",
    original_message={"message": "重写第一幕", "display_user_message": True},
    content_hash="hash-1",
)["id"] == checkpoint["id"]
```

- [ ] **步骤 2：运行测试并确认因 `begin_turn` 不存在而失败**

运行：`uv run pytest tests/test_writer_agent_store.py -k "turn_checkpoint" -v`

- [ ] **步骤 3：实现 Store 迁移和检查点生命周期**

为 session 增加 `history_revision INTEGER NOT NULL DEFAULT 0`；创建 `writer_agent_turn_checkpoints`、`writer_agent_action_undo` 和 `writer_agent_rollbacks`。实现下列接口，并让调用端只依赖返回的持久化检查点：

```python
checkpoint = store.begin_turn(
    session_id, actor_user_id, user_message_id,
    original_message=original_message,
    content_hash=content_hash,
)
store.complete_turn(checkpoint["id"])
same_turn = store.require_turn(session_id, user_message_id, actor_user_id)
affected_turns = store.list_turns_from(same_turn["id"])
```

同一消息 ID、同摘要返回原记录；不同摘要抛 `IDEMPOTENCY_KEY_REUSED`。`save_turn` 成功追加新轮次时递增 `history_revision`，重复合并不递增。

- [ ] **步骤 4：编写并运行纯会话恢复的失败测试**

测试建立两轮消息和两个报告，回档第一轮后断言只保留材料消息、`reports=[]`、`report={}`、后续检查点为 `rolled_back/superseded`、history revision 只增加一次；重复相同 `client_request_id` 返回同一结果。

运行：`uv run pytest tests/test_writer_agent_store.py -k "restore_conversation or rollback_idempotent" -v`

- [ ] **步骤 5：实现原子恢复与回档审计方法并使测试通过**

实现下列接口，使恢复入口只接收服务端检查点而不是前端传入的消息数组：

```python
rollback, created = store.begin_rollback(
    session_id, actor_user_id, checkpoint_id,
    mode="rollback", client_request_id="rollback-1",
    expected_history_revision=2,
)
store.advance_rollback(rollback["id"], "workflow_restored")
restored = store.restore_conversation(rollback["id"])
```

`restore_conversation` 在一个 `BEGIN IMMEDIATE` 中按检查点快照恢复 `project_id/context_revision`，按数量截断 messages/reports，重建 latest report，把目标及后续动作改为不可操作的 `rolled_back`，使后续检查点失效并递增 history revision。

- [ ] **步骤 6：运行 Store 回归并提交任务 1**

运行：`uv run pytest tests/test_writer_agent_store.py -v`

提交：`git commit -m "feat(writer): add turn checkpoints (task 1/5)"`

### 任务 2：项目与工作流可补偿快照

**文件：**
- 修改：`creative/repository.py`
- 修改：`workflow/repository.py`
- 创建：`writer_agent/rollback_service.py`
- 测试：`tests/test_writer_agent_api.py`

- [ ] **步骤 1：编写创作状态补偿的失败测试**

测试点名的破坏是“动作创建/编辑内容后只恢复了聊天”。在真实临时 CreativeRepository 中创建项目及旧 Brief，捕获前镜像，再创建并锁定新 Brief；调用补偿后断言项目活动 `locked_brief_id`、status、revision 和旧资源字段恢复，新资源不再是活动成果，快照指纹与前镜像一致。

同时覆盖 characters、relationships、outline、beats、script 的代表性新增行和项目活动指针。

- [ ] **步骤 2：运行测试并确认因项目快照 API 不存在而失败**

运行：`uv run pytest tests/test_writer_agent_api.py -k "creative_rollback_snapshot" -v`

- [ ] **步骤 3：实现 CreativeRepository 的受控快照和补偿**

实现下列调用契约：

```python
before = repository.export_writer_rollback_state(project_id)
after_fingerprint = repository.writer_rollback_fingerprint(project_id)
restored = repository.restore_writer_rollback_state(
    project_id, before, expected_fingerprint=after_fingerprint,
)
```

快照覆盖项目行、seeds、blueprints、characters、relationships、episodes、operation results 和 generation operations。恢复事务先比较规范 JSON SHA-256；不一致抛 `project rollback state changed`。恢复已有行的原字段；动作新增的创作版本转为 `superseded`，技术性 operation 行删除；项目活动指针和 revision 恢复。动作新建项目走 `delete_project`，且删除前必须匹配后置指纹。

- [ ] **步骤 4：编写工作流状态补偿的失败测试**

测试点名的破坏是“项目内容恢复但任务仍显示完成或占用”。用测试数据库创建项目任务、提交和 Job 绑定，捕获快照后推进状态；补偿后断言既有任务/提交字段恢复，动作新增任务进入 cancelled，追加 `writer.rollback` 事件，项目成员关系不被无关修改覆盖。

- [ ] **步骤 5：实现 WorkflowRepository 的受控快照和补偿**

实现下列调用契约：

```python
before = workflow_repository.export_writer_rollback_state(str(project_id))
after_fingerprint = workflow_repository.writer_rollback_fingerprint(str(project_id))
restored = workflow_repository.restore_writer_rollback_state(
    str(project_id), before,
    expected_fingerprint=after_fingerprint,
    actor=principal,
)
```

快照只覆盖该项目的 workflow instance、tasks、submissions、task jobs 和项目成员关系摘要。补偿在单个 PostgreSQL 事务中校验后置指纹、恢复既有任务/提交、取消新增任务并追加事件；不删除既有审计事件。

- [ ] **步骤 6：实现 RollbackService 的动作前后镜像和倒序补偿**

`prepare_action_undo(action, actor)` 在首次副作用前保存 preimage；`finalize_action_undo(action, actor)` 保存当前创作/工作流指纹；`preview(session_id, message_id, actor)` 汇总影响；`rollback(session_id, message_id, actor, request)` 倒序校验所有 postcondition 后再开始补偿。任何动作仍 active 返回 `ROLLBACK_BUSY`，任何指纹变化返回 `ROLLBACK_CONFLICT`。

- [ ] **步骤 7：运行补偿测试并提交任务 2**

运行：`uv run pytest tests/test_writer_agent_api.py -k "rollback_snapshot or rollback_conflict or rollback_workflow" -v`

提交：`git commit -m "feat(writer): compensate project state (task 2/5)"`

### 任务 3：消息、动作执行与回档 API 集成

**文件：**
- 修改：`writer_agent/action_service.py`
- 修改：`writer_agent/api.py`
- 修改：`writer_agent/runner.py`
- 修改：`writer_agent/store.py`
- 测试：`tests/test_writer_agent_api.py`

- [ ] **步骤 1：编写 API 契约失败测试**

测试点名的破坏是“页面无法预览或提交一个有并发保护的历史回档”。创建两轮会话后断言：

```python
preview = client.get(
    f"/api/writer-agent/sessions/{session_id}/messages/{message_id}/rollback-preview"
)
assert preview.status_code == 200
assert preview.json()["messages_to_remove"] == 4

rolled_back = client.post(
    f"/api/writer-agent/sessions/{session_id}/messages/{message_id}/rollback",
    json={"client_request_id": "rollback-1",
          "expected_history_revision": preview.json()["history_revision"]},
)
assert rolled_back.status_code == 200
```

另写 404 伪造 message ID、403 非所有者、409 history revision 过期、409 active job、409 外部项目 revision 冲突。

- [ ] **步骤 2：运行测试并确认因路由不存在而失败**

运行：`uv run pytest tests/test_writer_agent_api.py -k "rollback_preview or rollback_endpoint" -v`

- [ ] **步骤 3：把检查点贯穿普通消息与动作执行**

在 message claim 成功后调用 `begin_turn`；通过处理上下文把 checkpoint ID 传入 `persist_proposed_actions`，使 `create_action` 写入 `turn_checkpoint_id`；所有 `save_turn` 成功分支调用 `complete_turn`。Runner 在启动白名单写动作前调用 `prepare_action_undo`，在 succeeded/failed/cancelled 终态调用 `finalize_action_undo`。

- [ ] **步骤 4：注册预览和回档路由并使 API 测试通过**

新增 Pydantic 请求：

```python
class TurnRollbackRequest(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=200)
    expected_history_revision: int = Field(ge=0)
```

路由只接受当前会话可写账户；将稳定业务错误映射为 403/404/409/500，并返回刷新后的 session。

- [ ] **步骤 5：编写重新发送失败测试**

模型 provider 记录输入。请求 resend 后断言旧消息和后续状态先消失、provider 只新增调用一次、返回消息内容相同但 message ID 不同；让 provider 首次失败后用同一 `client_request_id` 重试，断言补偿只发生一次。

- [ ] **步骤 6：抽取消息处理函数并实现 resend**

把现有 `chat` 路由主体抽取为接收已解析 principal/session/payload 的内部 async 函数。`resend` 完成 `rollback(mode="resend")` 后，从检查点读取原始安全字段，清除旧 message/client_message ID，生成 `message-{uuid4().hex}` 并进入同一处理函数。回档审计保存新 message ID，幂等重试读取它。

- [ ] **步骤 7：运行 Writer API 回归并提交任务 3**

运行：`uv run pytest tests/test_writer_agent_api.py -v`

提交：`git commit -m "feat(writer): expose rollback and resend api (task 3/5)"`

### 任务 4：历史消息操作界面

**文件：**
- 修改：`static/js/review/writer_agent.js`
- 修改：`templates/review/writer_agent.html`
- 修改：`tests/test_writer_agent_browser.py`

- [ ] **步骤 1：编写消息操作和确认预览的失败测试**

测试点名的破坏是“历史用户消息没有安全操作入口或点击即破坏状态”。挂载带 `turn_controls` 的 session，断言只有用户消息出现两个按钮；点击回档后先产生 GET preview，弹窗显示“将撤销 3 条消息、1 个动作、2 项项目内容”，取消后没有 POST。

- [ ] **步骤 2：运行浏览器测试并确认按钮不存在而失败**

运行：`uv run pytest tests/test_writer_agent_browser.py -k "message_rollback_controls" -v`

- [ ] **步骤 3：实现按钮、确认对话框和可访问状态**

`renderConversation()` 为具有 `message.turn_controls.can_rollback/can_resend` 的用户消息追加 `.writer-turn-actions`。按钮使用 `bi-arrow-counterclockwise` 和 `bi-send-repeat`，同时保留可见文字、`title` 和 `aria-label`。模板增加原生 `<dialog>`，关闭/Escape 后恢复触发按钮焦点；窄屏操作区自然换行且始终可见。

- [ ] **步骤 4：编写加载、成功与错误失败测试**

覆盖重复点击只 POST 一次、执行时发送/动作/历史操作统一禁用、成功后 session 一次刷新、busy 和 conflict 显示服务端消息、网络失败保留原对话。

- [ ] **步骤 5：实现回档/重发控制器并使测试通过**

实现 `previewTurnChange(message, mode)`、`confirmTurnChange()` 和 `setTurnMutationBusy()`；请求体使用新的 `writerRequestId()` 和预览 history revision。成功调用 `updateSession(updated.session || updated)`，不做乐观截断；重发期间不额外调用前端 `ask()`。

- [ ] **步骤 6：运行桌面与移动端浏览器回归并提交任务 4**

运行：`uv run pytest tests/test_writer_agent_browser.py -v`

提交：`git commit -m "feat(writer): add rollback controls to chat (task 4/5)"`

### 任务 5：全量回归、迁移与完成检查

**文件：**
- 修改：`docs/superpowers/plans/2026-09-03-writer-conversation-rollback.md`（勾选已执行步骤）

- [ ] **步骤 1：运行 Writer Agent 单元/API/浏览器测试**

运行：

```powershell
uv run pytest tests/test_writer_agent_store.py tests/test_writer_agent_api.py tests/test_writer_agent_browser.py -v
```

- [ ] **步骤 2：运行相关 Creative 与 Workflow 回归**

运行：

```powershell
uv run pytest tests/test_creative_repository.py tests/test_workflow_repository.py -v
```

如果仓库实际文件名不同，使用 `rg --files tests | rg "creative|workflow"` 选择覆盖相同 repository 的现有测试文件，并在完成报告中列出实际命令。

- [ ] **步骤 3：运行格式、文件头和差异检查**

运行：

```powershell
uv run pre-commit run --all-files
git diff --check
git status --short
```

- [ ] **步骤 4：启动本地服务并做页面冒烟验证**

使用项目现有 WebGUI 启动方式监听 `0.0.0.0`，在独立测试会话验证发送两轮纯消息、预览、取消、回档和重新发送；再验证一个会创建草稿动作的测试项目。不得对既有生产会话或项目执行回档。

- [ ] **步骤 5：核对规格验收项并提交任务 5**

逐项核对检查点、系统补偿、冲突拒绝、幂等重发、权限与响应式交互。只提交本任务产生的计划勾选或修复：

提交：`git commit -m "test(writer): verify conversation rollback (task 5/5)"`
