### Task 7: Human Task Repository and Role Pools

**Files:**
- Create: `workflow/__init__.py`
- Create: `workflow/schema.py`
- Create: `workflow/repository.py`
- Create: `workflow/state_machine.py`
- Create: `workflow/api.py`
- Test: `tests/test_workflow_tasks.py`
- Test: `tests/test_workflow_api.py`

**Interfaces:**
- Consumes: system database, principals, roles, project membership, permission guards.
- Produces: `WorkflowRepository.create_task(...) -> dict`.
- Produces: `WorkflowStateMachine.transition(task, action, actor) -> str`.
- Produces: `/api/workflow/tasks` claim/assign/reassign/start/cancel endpoints.
- Produces: `/api/workflow/projects/{project_id}/members` list/add/remove endpoints.

- [ ] **Step 1: Write failing task-pool and transition tests**

Cover pool creation, role eligibility, claim races, assignment, reassignment, project-member add/remove, managed-project scope, cancellation, event history, and no workload auto-assignment:

```python
def test_role_pool_task_is_not_auto_assigned(repository):
    task = repository.create_task(
        workflow_id="wf-1", task_type="breakdown.create", role_code="selection_operator",
        project_id=None, topic_id=12,
    )
    assert task["status"] == "pool"
    assert task["assignee_user_id"] is None

def test_only_matching_role_can_claim(repository, selection_user, writer_user):
    task = repository.create_task("wf-1", "breakdown.create", "selection_operator", topic_id=12)
    with pytest.raises(PermissionError):
        repository.claim_task(task["id"], writer_user)
    assert repository.claim_task(task["id"], selection_user)["status"] == "assigned"
```

- [ ] **Step 2: Run tests and verify workflow modules are missing**

Run: `uv run pytest tests/test_workflow_tasks.py tests/test_workflow_api.py -q`

Expected: FAIL with missing workflow modules.

- [ ] **Step 3: Implement task schemas and state machine**

Use exact states `pool`, `assigned`, `in_progress`, `waiting_review`, `returned`, `completed`, and `cancelled`. Define allowed actions and actor requirements in one mapping; repository methods must call the state machine rather than update status strings directly.

- [ ] **Step 4: Implement atomic role-pool claim and assignment**

Use `BEGIN IMMEDIATE` and `UPDATE ... WHERE status='pool' AND assignee_user_id IS NULL` to avoid two users claiming one task. Check current effective role codes before the update. Project owners and system administrators may assign or reassign; ordinary users may only claim matching pools.

- [ ] **Step 5: Implement task list and mutation APIs**

`GET /api/workflow/tasks` returns only claimable pools, assigned tasks, or managed-project tasks according to principal scope. Add `GET`, `POST`, and `DELETE` member operations under `/api/workflow/projects/{project_id}/members`; require `project.member.manage`, prevent removal of the current project owner until another owner is assigned, and store membership role/status in `project_members`. Every mutation records `workflow_task_events` with actor, before/after state, and timestamp.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_workflow_tasks.py tests/test_workflow_api.py -q`

Expected: PASS.

Commit:

```bash
git add workflow tests/test_workflow_tasks.py tests/test_workflow_api.py
git commit -m "feat: add human task pools and assignment"
```

---

