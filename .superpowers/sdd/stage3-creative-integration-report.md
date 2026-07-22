# Stage 3 Task A Creative Workflow Integration Report

## Status

Implemented the five creative resource integrations: breakdown, Brief, character/relationship batch, outline batch, and Beats. Creative submissions now use server-built snapshots, freeze submitted versions, mirror workflow states into the creative database, apply approval pointers, clone returned drafts, and retry failed approval mirrors idempotently.

## TDD Evidence

RED was recorded before implementation:

```text
uv run pytest tests/test_creative_workflow_integration.py tests/test_role_page_content.py -q
ModuleNotFoundError: No module named 'creative.workflow_adapter'
```

Subsequent red-green cycles covered resource snapshots and task validation, all five approval/return paths, the new workflow registry resource ID after return, failed cross-database mirror retry, upstream supersession with preserved downstream history, schema migration, and UI action checks.

## Implementation

- Added `CreativeWorkflowAdapter` with server snapshots, task/node/assignee/resource checks, submission, edit guards, approval mirrors, returned-version cloning, mirror retry, and audited administrator compatibility actions.
- Added idempotent `workflow_task_id` migrations and propagation through creative generation and creation paths.
- Kept workflow approval authoritative across the system and creative SQLite databases; mirror failures are recorded as `creative.mirror_error` task events and retried on resource reads or repeated approval reads.
- Updated workflow return handling so the new draft registry entry points at the cloned creative resource.
- Replaced creative lock actions with submit-for-approval controls and added a waiting-review approval/return page with a required visible return comment.
- Retained legacy lock routes for authenticated administrator force use only, with workflow-task audit events.

## Verification

```text
uv run pytest tests/test_creative_workflow_integration.py tests/test_creative_phase4.py tests/test_creative_phase4_generation.py tests/test_role_page_content.py -q
62 passed

uv run pytest tests/test_workflow_api.py tests/test_workflow_approvals.py tests/test_workflow_production.py tests/test_workflow_tasks.py tests/test_workflow_transitions.py -q
57 passed, 1 pre-existing SQLAlchemy deprecation warning

uv run pytest tests/test_creative_breakdown_worker.py -q
4 passed

node --check static/js/common/creative.js
node --check static/js/selection/breakdowns.js
node --check static/js/selection/topics.js
node --check static/js/review/approvals.js
All passed

uv run pre-commit run --files <changed files>
All passed
```

## Concerns

- Supporting edits were required in `workflow/approval_service.py`, `workflow/repository.py`, the two creative workers, `static/js/selection/topics.js`, and `project_list.html`. These are directly required for transactional return registry updates, approved-resource worker reads, task linkage propagation, and approved-breakdown eligibility in the UI; no Job ownership behavior changed.
- The runtime still exposes legacy `locked` and `brief_locked` values for compatibility, but new creative UI actions do not write them.
- The existing SQLAlchemy 2.0 deprecation warning in `database/models.py` remains unrelated to this task.
