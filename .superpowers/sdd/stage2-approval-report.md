# Stage 2 Task B Approval State Machine Report

## Status

Implemented and committed as `682d41a` (`feat: add lightweight approval state machine`).
Only the five files listed in the task brief were included in the commit.

## RED Evidence

Command:

```text
uv run pytest tests/test_workflow_approvals.py -q
```

Observed failure before production code was added:

```text
ModuleNotFoundError: No module named 'workflow.approval_service'
1 error in 0.56s
```

The first implementation run then exposed a test-fixture precondition: project tasks
cannot be assigned to authors who are not active project members. The fixture was
corrected to create the required membership; production assignment behavior was not
relaxed.

## GREEN Evidence

Final focused tests:

```text
uv run pytest tests/test_workflow_approvals.py tests/test_workflow_tasks.py -q
.......................                                                  [100%]
23 passed in 2.46s
```

Focused pre-commit:

```text
uv run pre-commit run --files identity/repository.py workflow/schema.py workflow/repository.py workflow/approval_service.py tests/test_workflow_approvals.py
```

All applicable hooks passed: file headers, trailing whitespace, EOF, large files,
merge conflicts, case conflicts, and mixed line endings. The YAML hook was skipped
because no YAML file was in the focused set.

## Coverage

- Draft editability and non-draft rejection
- Canonical JSON snapshot and SHA-256 fingerprint on submit
- Wrong-assignee submission rejection and task transition to `waiting_review`
- Self-approval rejection and system-administrator approval
- Managed-project reviewer approval and task completion
- Required return comment, immutable returned source, parent/version linkage, and reassignment
- Previous approved version supersession
- Forking from `approved` and `returned` without source mutation
- Repeated terminal transition rejection
- Atomic rollback when the task state machine rejects submission
- `workflow_submissions` creation without `creative_stage_versions`

## Self-Review

- Confirmed the schema matches the required columns, exact statuses, and resource/version uniqueness.
- Confirmed submit, approve, return, and fork run inside `BEGIN IMMEDIATE` transactions.
- Confirmed submission updates happen in the same transaction as task transitions, and the rollback test exercises failure after the submission update.
- Confirmed every service mutation records a task event with a live, non-null actor plus task and submission before/after snapshots.
- Confirmed normal approval requires live `version.approve` permission and managed-project authority; administrators bypass review authority and self-approval restrictions as specified.
- Confirmed canonical snapshot/fingerprint fields are never modified after leaving `draft`; only lifecycle and review metadata change.
- Confirmed no creative domain table or production API route was added or changed.

## Concerns

No known blockers. Fork deliberately creates only the next draft registry row and an
event; next-task orchestration remains outside this task, as reserved for Stage 2 Task C.

## Review Fixes

Follow-up commit: `e31f385` (`fix: authorize managed reviewers and approval ordering`).

### RED Evidence

The first review regression run produced three expected failures:

```text
uv run pytest tests/test_workflow_approvals.py -q
3 failed, 11 passed in 2.05s
```

- Custom-role approve failed because `_assert_project_manager` required the global
  `project_owner` role.
- Custom-role return failed for the same reason.
- Reverse approval order did not reject an older submission after a newer version
  had already been approved.

A focused permission-scope regression then failed as expected because task-scoped
`version.approve` was incorrectly accepted for managed-project review:

```text
1 failed, 14 passed in 1.96s
```

### GREEN Evidence

```text
uv run pytest tests/test_workflow_approvals.py tests/test_workflow_tasks.py -q
...........................                                              [100%]
27 passed in 2.84s
```

Focused pre-commit passed all applicable hooks for the four changed files.

### Review Self-Check

- Review authorization now requires the live permission with `managed_projects` or
  `all` scope and an active managed-role project membership.
- Custom roles are not required to carry the global `project_owner` role.
- The already-authorized review context reaches the task state machine without
  changing the recorded actor identity.
- System administrators retain their bypass.
- Approval rejects an older submitted version when a newer approved version exists;
  the rejection occurs before any submission or task mutation.
- `BEGIN IMMEDIATE` serialization plus lower-version supersession and the newer-version
  conflict guard maintain at most one current approved version through service operations.

No new concerns were found.
