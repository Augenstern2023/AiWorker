# Task 7 Report: Human Task Repository and Role Pools

## Status

Complete. Commit: `1fb3c18891e9e9a5b836ec012d9a1958924e44c8`
(`feat: add human task pools and assignment`).

## Delivered

- Added the `workflow` package with request schemas, a single declarative task
  transition map, repository operations, and FastAPI routes.
- Human tasks are created in `pool` with no assignee. Claims use live effective
  roles plus `BEGIN IMMEDIATE` and a guarded `UPDATE`.
- Added manager-scoped assign/reassign/cancel, assignee start, active project
  membership checks, managed-project task visibility, and atomic event writes.
- Added project member list/add/remove operations with role/status persistence
  and protection for the last active project owner.
- Added focused repository, concurrency, state-machine, API permission, scope,
  membership, cancellation, and event-history tests.

## TDD Evidence

- RED 1: focused test collection failed with
  `ModuleNotFoundError: No module named 'workflow'` for both new test modules.
- GREEN 1: initial implementation reached `12 passed`.
- RED 2: project assignment test showed an outside-project user was accepted:
  `DID NOT RAISE <class 'ValueError'>`.
- GREEN 2: active project membership was enforced; focused suite passed.
- RED 3: project-pool claim test showed an outside-project user could claim:
  `DID NOT RAISE <class 'PermissionError'>`.
- GREEN 3: claim and task-list project membership scope was enforced.

## Verification

- `uv run pytest tests/test_workflow_tasks.py tests/test_workflow_api.py -q`
  -> `13 passed in 1.89s`
- `uv run pytest tests/test_identity_repository.py tests/test_identity_permissions.py -q`
  -> `13 passed in 1.81s`
- `uv run pre-commit run --files workflow/__init__.py workflow/schema.py workflow/repository.py workflow/state_machine.py workflow/api.py tests/test_workflow_tasks.py tests/test_workflow_api.py`
  -> all configured hooks passed
- `git diff --cached --check` passed before commit.

## Self-Review

- Confirmed all task status writes go through `WorkflowStateMachine` before SQL.
- Confirmed claims and initial assignments guard both `status = 'pool'` and a
  null assignee inside an immediate SQLite transaction.
- Confirmed current database roles, user status, project membership, and
  manager scope are queried at mutation time rather than trusting stale
  principal fields.
- Confirmed task mutations record actor, source/target status, details, and
  timestamp in the same transaction.
- Confirmed only the seven Task 7 source/test files were included in the commit;
  unrelated dirty-tree changes were left untouched.

## Concerns

- `build_workflow_router()` is implemented and tested but intentionally not
  mounted in `WebGUI.py`, which was outside the Task 7 file list. A later app
  composition task must include the router before these routes are live in the
  main application.
- Project membership changes cannot be stored in `workflow_task_events`
  because that table requires a task foreign key. Task mutations are fully
  recorded; membership audit integration belongs with the global audit layer.

## Review Fixes

Follow-up commit: `9691f774adfcf92167cc036420083de2ea81d247`
(`fix: tighten workflow actor and membership events`).

- Added `workflow_project_member_events` as the dedicated workflow event
  representation for project membership add, update, and remove mutations.
  Events persist the actor, before/after snapshots, event type, membership and
  project identifiers, and timestamp in the same transaction as the mutation.
- Verified the member APIs propagate the authenticated actor for add, update,
  and successful remove operations. The earlier membership-event concern is
  resolved by the dedicated event table.
- Made `create_task(..., actor=...)` require a non-null actor. Exported the
  explicit `SYSTEM_ACTOR` constant for automatic workflow transitions and
  stored its non-null actor reference on workflow creation and task events.
- Changed the last-owner rule so only another active membership whose
  `project_role` is exactly `project_owner` permits removal. `owner`, `manager`,
  and `project_manager` do not satisfy the replacement requirement.
- Removed the system-administrator claim bypass. Claims now always require the
  live effective role matching the task pool; administrators retain manual
  assign/reassign authority, including assignment to themselves.
- Changed atomic claim SQL and event recording to use the status returned by
  `WorkflowStateMachine.transition()` rather than hardcoding `assigned`.

### Follow-Up TDD Evidence

- RED: importing the required `SYSTEM_ACTOR` failed before it existed.
- RED: explicit `actor=None` raised an accidental `AttributeError` instead of
  the required actor validation error.
- RED: package-level import of `SYSTEM_ACTOR` failed before the public export
  was added.
- GREEN: focused workflow tests finished with `17 passed`.

### Follow-Up Verification

- `uv run pytest tests/test_workflow_tasks.py tests/test_workflow_api.py tests/test_identity_repository.py tests/test_identity_permissions.py -q`
  -> `30 passed in 3.42s`
- Task-file pre-commit hooks all passed.
- `git diff --cached --check` passed before the follow-up commit.
