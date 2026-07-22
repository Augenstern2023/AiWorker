# Final Fixes V8 Report

## Scope

Separated immutable project-creation ownership from the mutable current creative-stage task. Legacy project reconciliation can now move Brief-task ownership to the system-bound project without replacing a valid Characters, Outline, or Beats task already stored on that project.

## RED Evidence

The complete legacy sequence was added before the implementation:

```text
uv run pytest tests/test_creative_phase4.py::test_legacy_brief_orphan_reconciliation_preserves_new_current_stage_task -q

FAILED
expected target workflow_task_id = Characters task C
observed target workflow_task_id = legacy Brief task T
```

The regression creates older orphan A with Brief task T, binds newer project B to T in the system workflow, advances B to Characters task C, and then reads the project list. Before the fix, reconciling A overwrote B's current stage from C back to T.

## Implementation

- Added nullable `creation_workflow_task_id` to `creative_projects` as the immutable idempotency and ownership key.
- Kept `workflow_task_id` as the mutable current-stage task used by creative resources and project visibility.
- Backfilled the creation key during migration and deduplicated it by keeping the newest nondeleted row.
- Reconciliation now moves creation ownership and removes stale orphan links, but only fills the target's current task when it is empty or already the same Brief task.
- Project-list reconciliation processes only Brief creation tasks. False creation keys backfilled from later-stage tasks are cleared conditionally.
- API create/retry uses the creation key while system `task.project_id` remains authoritative for cross-database repair.

## GREEN Evidence

Targeted creative workflow, project migration, and API reconciliation coverage:

```text
uv run pytest tests/test_creative_phase4.py tests/test_project_visibility.py \
  tests/test_creative_workflow_integration.py tests/test_workflow_bootstrap.py \
  tests/test_role_workflow_migration.py -q
64 passed
```

Focused legacy sequence and idempotent reconciliation cases:

```text
uv run pytest \
  tests/test_creative_phase4.py::test_legacy_brief_orphan_reconciliation_preserves_new_current_stage_task \
  tests/test_creative_phase4.py::test_project_retry_prefers_system_bound_project_over_creative_mismatch \
  tests/test_creative_phase4.py::test_project_duplicate_migration_keeps_newest_nondeleted_row \
  tests/test_creative_phase4.py::test_project_creation_retries_binding_without_duplicate -q
4 passed
```

Unit suite:

```text
uv run pytest tests/ -q
498 passed, 1 failed
```

The sole failure is the accepted pre-existing XHS Excel factory mismatch: `TestXhsStoreFactory.test_create_excel_store` returns `ExcelStoreBase` rather than `XhsExcelStoreImplement`.

Full suite excluding the unavailable Redis integration tests:

```text
uv run pytest -q --ignore=test/test_proxy_ip_pool.py --ignore=test/test_redis_cache.py
502 passed, 8 skipped, 1 failed
```

The same accepted XHS Excel mismatch is the only failure. The run also retained the pre-existing MongoDB integration coroutine warning.

## Self-Review

- The new creation key is written once at project creation and is not changed by `link_project_workflow_task`, so later-stage task transitions cannot destroy project idempotency.
- Reconciliation detaches both stale creation ownership and the matching stale current-task pointer from orphan rows before assigning the target creation key.
- The target current task is preserved when it differs from the Brief creation task, which directly closes the reviewed A/T, B/T, B/C sequence.
- List reconciliation no longer suppresses failures; invalid system bindings return `503` for explicit recovery instead of silently mutating or hiding data.
- Creative DB migration cannot query the system DB directly, so its deterministic fallback remains newest-nondeleted. The API layer subsequently corrects that heuristic using authoritative system `task.project_id`.
