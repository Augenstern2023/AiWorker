### Stage 2 Task B: Lightweight Submission and Approval State Machine

**Goal:** Add a lightweight unified approval registry in `system.db` that freezes submitted snapshots and supports approve, return, and fork without introducing `creative_stage_versions` or modifying creative domain tables in this stage.

**Files:**
- Modify: `identity/repository.py`
- Modify: `workflow/schema.py`
- Modify: `workflow/repository.py`
- Create: `workflow/approval_service.py`
- Test: `tests/test_workflow_approvals.py`

**Required interfaces:**

```python
class ApprovalService:
    def create_draft(
        self,
        *,
        task_id: str,
        resource_type: str,
        resource_id: str,
        content: dict[str, Any] | list[Any],
        actor: Principal,
        parent_submission_id: str | None = None,
    ) -> dict[str, Any]: ...

    def assert_editable(self, submission_id: str, actor: Principal) -> dict[str, Any]: ...
    def submit(self, submission_id: str, actor: Principal) -> dict[str, Any]: ...
    def approve(self, submission_id: str, actor: Principal) -> dict[str, Any]: ...
    def return_submission(
        self,
        submission_id: str,
        actor: Principal,
        review_comment: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...
    def fork(self, submission_id: str, actor: Principal) -> dict[str, Any]: ...
```

**Schema:** Add `workflow_submissions` to `system.db` with:

```text
id TEXT PRIMARY KEY
task_id TEXT NOT NULL
resource_type TEXT NOT NULL
resource_id TEXT NOT NULL
version INTEGER NOT NULL
parent_submission_id TEXT
status TEXT NOT NULL
content_snapshot_json TEXT NOT NULL
content_fingerprint TEXT NOT NULL
created_by TEXT NOT NULL
submitted_by TEXT
submitted_at REAL
reviewed_by TEXT
reviewed_at REAL
review_comment TEXT NOT NULL DEFAULT ''
created_at REAL NOT NULL
updated_at REAL NOT NULL
UNIQUE(resource_type, resource_id, version)
```

**State rules:**

- Exact states: `draft`, `submitted`, `approved`, `returned`, `superseded`.
- Only `draft` is editable.
- `submit` requires the task assignee, freezes the canonical JSON snapshot and moves the human task to `waiting_review`.
- Submitted snapshot content and fingerprint never change.
- The submitting user cannot approve their own submission unless `actor.is_system_admin`.
- `approve` requires `version.approve` on a managed task or system administrator; it marks the submission approved and its human task completed.
- Approving a newer version marks the previous approved version for the same resource as `superseded`.
- `return_submission` requires a non-empty review comment, marks the submitted version `returned`, creates a new `draft` with incremented version and `parent_submission_id`, and marks the human task `returned` while assigning it back to the original submitter.
- `fork` is allowed from `approved` or `returned`, creates the next version as `draft`, and never mutates the source.
- Every mutation writes `workflow_task_events` with non-null actor and before/after snapshots.
- Use canonical `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))` and SHA-256 for the fingerprint.
- Use `BEGIN IMMEDIATE` for submit/approve/return/fork so submission and task states cannot diverge.
- No production workflow API routes in this task; Stage 2 Task C adds orchestration and routes.

**TDD requirements:**

1. Write failing tests first and record RED evidence.
2. Cover draft editing, submit freeze, wrong assignee, self-approval rejection, administrator approval, normal reviewer approval, required return comment, returned source immutability, new draft parent/version, superseding previous approval, idempotent rejection of repeated terminal transitions, and transaction rollback when task transition fails.
3. Run `uv run pytest tests/test_workflow_approvals.py tests/test_workflow_tasks.py -q` and obtain PASS.
4. Run focused pre-commit hooks.
5. Commit only listed files with message `feat: add lightweight approval state machine`.
6. Write RED/GREEN evidence and self-review to `.superpowers/sdd/stage2-approval-report.md`.
