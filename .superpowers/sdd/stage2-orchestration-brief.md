### Stage 2 Task C: Approval APIs and Next-Role Task Orchestration

**Goal:** Mount the workflow subsystem in production, expose task/submission/review APIs, and create the next role-pool task exactly once after approval.

**Files:**
- Create: `workflow/transition_service.py`
- Modify: `identity/repository.py`
- Modify: `workflow/schema.py`
- Modify: `workflow/repository.py`
- Modify: `workflow/api.py`
- Modify: `WebGUI.py`
- Modify: `static/js/common/tasks.js`
- Test: `tests/test_workflow_transitions.py`
- Test: `tests/test_workflow_production.py`

**Node catalog:**

```python
WORKFLOW_NODES = {
    "topic_decision": ("selection_operator", "project_owner", "source_package"),
    "source_package": ("selection_operator", "project_owner", "breakdown"),
    "breakdown": ("selection_operator", "project_owner", "brief"),
    "brief": ("creative_planner", "project_owner", "characters"),
    "characters": ("creative_planner", "project_owner", "outline"),
    "outline": ("screenwriter", "project_owner", "beats"),
    "beats": ("screenwriter", "project_owner", "delivery"),
    "delivery": ("system_admin", None, None),
}
```

The tuple is `(executor_role, reviewer_role, next_node)`.

**Required interfaces:**

```python
class TransitionService:
    def create_submission(self, task_id: str, resource_type: str, resource_id: str, content: Any, actor: Principal) -> dict: ...
    def submit(self, submission_id: str, actor: Principal) -> dict: ...
    def approve(self, submission_id: str, actor: Principal) -> dict: ...
    def return_submission(self, submission_id: str, actor: Principal, review_comment: str) -> dict: ...
    def fork(self, submission_id: str, actor: Principal) -> dict: ...
```

`approve()` response contains `submission`, `task`, and `next_task` (`None` for terminal delivery). It must be idempotent: repeated approval does not create another task and returns the already-created next task.

**API routes:**

```text
GET  /api/workflow/tasks
GET  /api/workflow/tasks/{task_id}
POST /api/workflow/tasks/{task_id}/claim
POST /api/workflow/tasks/{task_id}/assign
POST /api/workflow/tasks/{task_id}/reassign
POST /api/workflow/tasks/{task_id}/start
POST /api/workflow/tasks/{task_id}/cancel
GET  /api/workflow/projects/{project_id}/members
POST /api/workflow/projects/{project_id}/members
DELETE /api/workflow/projects/{project_id}/members/{user_id}

POST /api/workflow/tasks/{task_id}/submissions
GET  /api/workflow/tasks/{task_id}/submissions
POST /api/workflow/submissions/{submission_id}/submit
POST /api/workflow/submissions/{submission_id}/approve
POST /api/workflow/submissions/{submission_id}/return
POST /api/workflow/submissions/{submission_id}/fork
```

**Rules:**

- Production uses the shared `IdentityRepository`, `PermissionService`, and session/CSRF middleware already on `WebGUI.app`.
- Register the workflow router during WebGUI startup; routes must appear in `WebGUI.app.routes`.
- API authentication uses the real current-principal dependency, not test-only overrides.
- Task list visibility and mutations retain Task 7 rules.
- Create submission requires the task assignee and task status `assigned`, `in_progress`, or `returned`.
- Submit/approve/return/fork delegate state changes to `ApprovalService`; do not duplicate status SQL in the API.
- Approval creates the next node task in the target executor role pool with no assignee.
- Copy `workflow_id`, `topic_id`, and `project_id` from the approved task to the next task.
- Use a deterministic idempotency key `(workflow_id, next_node, predecessor_task_id)` persisted on the next task or an equivalent unique database constraint.
- Do not create a separate review task in this simplified stage; project owners/system admins review the waiting task directly.
- `delivery` is a system-admin pool task and is terminal after completion.
- Every API write is protected by the global CSRF middleware.
- `static/js/common/tasks.js` must render real visible pool/assigned/waiting-review rows and provide claim/start actions; it must not expose approve/return buttons to users lacking review permission.
- No creative API integration in this stage.

**TDD requirements:**

1. Write tests first and record RED evidence.
2. Cover the full node mapping, exact target roles, no assignee, copied identifiers, repeated approval idempotency, return without next task, terminal delivery, unauthorized review, production route registration, unauthenticated `401`, bad CSRF `403`, and role-filtered task listing.
3. Run `uv run pytest tests/test_workflow_tasks.py tests/test_workflow_api.py tests/test_workflow_approvals.py tests/test_workflow_transitions.py tests/test_workflow_production.py -q`.
4. Run `uv run pytest tests/test_identity_auth.py tests/test_web_security.py tests/test_page_authorization.py -q`.
5. Run Node syntax check for `static/js/common/tasks.js` and focused pre-commit.
6. Commit only listed files with message `feat: expose approval workflow orchestration`.
7. Write report to `.superpowers/sdd/stage2-orchestration-report.md`.
