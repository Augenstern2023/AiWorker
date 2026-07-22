# Final Role Workflow Fixes Report

## Scope

Implemented the six final-review corrections on the existing dirty `main` worktree without modifying or staging the pre-existing SDD packages or `progress.md` change.

- Protected the complete production `/api/*` surface with authentication before CSRF, coarse legacy permissions, creative route permissions, and live task/project resource scope. Protected unsafe requests are audited with request metadata and result.
- Restricted backup restore/delete to generated backup basenames whose resolved parent is the backup directory; all backup APIs require system administration.
- Added idempotent topic workflow bootstrap/takeover, the initial `topic_decision` pool task, an authenticated owner/admin API, adoption, audit, and the selection UI action.
- Kept a Beats task active across every current outline episode, withheld delivery until all episodes are approved, and made one terminal delivery record every approved episode export marker.
- Added persistent custom-role claimable task types and role API/UI support; custom roles can claim configured task types and system administrators can claim any pool task.
- Added success/failure workflow audit events for claim, assign/reassign, start/cancel, submit, approve, return, and fork. Project audit readers must provide a managed project; global audit remains limited to `audit.read` or administrators.

## RED Evidence

Focused tests were written or changed before production changes and observed failing for the intended missing behavior:

```text
tests/test_web_security.py::test_protected_api_rejects_anonymous_write_before_csrf
  expected 401, observed 200
tests/test_workflow_tasks.py::test_system_admin_can_claim_any_target_role
  PermissionError: actor does not have the task pool role
tests/test_workflow_tasks.py::test_custom_role_claims_configured_task_type
  AttributeError: replace_role_claimable_task_types
tests/test_web_security.py::test_backup_path_rejects_traversal_and_non_backup_names
  ImportError: resolve_backup_path
tests/test_workflow_bootstrap.py
  ModuleNotFoundError: creative.workflow_bootstrap
tests/test_role_workflow_e2e.py
  delivery task was created after episode 1 of 2
tests/test_audit_service.py::test_project_audit_reader_is_limited_to_managed_project
  managed project request returned 403
```

## GREEN Evidence

Final focused coverage after formatting:

```text
uv run pytest tests/test_web_security.py tests/test_workflow_tasks.py \
  tests/test_workflow_api.py tests/test_audit_service.py \
  tests/test_identity_admin_api.py tests/test_role_workflow_e2e.py \
  tests/test_workflow_bootstrap.py -q
60 passed
```

Broader final-review suite:

```text
uv run pytest [18 focused identity/workflow/creative/UI modules] -q
269 passed
```

Unit suite:

```text
uv run pytest tests/ -q
484 passed, 1 failed
```

The sole unit failure is the accepted pre-existing XHS Excel factory mismatch: `TestXhsStoreFactory.test_create_excel_store` returns `ExcelStoreBase` instead of `XhsExcelStoreImplement`.

Full suite excluding unavailable local Redis integration tests:

```text
uv run pytest -q --ignore=test/test_proxy_ip_pool.py --ignore=test/test_redis_cache.py
488 passed, 8 skipped, 1 failed
```

The same accepted XHS Excel test is the only failure. The unfiltered full run additionally had six Redis connection failures because no Redis service was listening on `127.0.0.1:6379`.

Static verification:

```text
node --check static/js/admin/roles.js
node --check static/js/selection/topics.js
uv run pre-commit run --files <all scoped changed files>
git diff --check
```

All static checks and scoped pre-commit hooks passed.

## Important Re-review Fixes

The seven Important findings from the follow-up review were addressed in a second TDD wave:

- Planner/writer approved-context reads no longer require `topic.read`; managed project readers can load managed projects.
- Project creation records the authenticated author. Project mutation is author-only outside system administration, and project reads expose non-approved stage records only to the project author or current task assignee. The existing migration continues to reattribute legacy `created_by = 'local'` projects to the bootstrap administrator.
- Legacy file deletion and analytics synchronization are administrator-only while file/analytics reads retain their read permissions. Protected destructive requests continue through the unified API audit middleware.
- Topic workflow bootstrap now authorizes before adoption, uses `project.manage` plus managed-project scope rather than a literal role, reconciles the task before adoption, is hidden from selection operators, and is available from the project-owner review page.
- Direct assignment now uses the live configurable claimable task types as well as built-in role compatibility.
- Beats delivery records only the selected episode artifact marker. A two-episode lifecycle remains partial after the first artifact and reaches `beats_exported` only after both artifacts are delivered.
- Project membership add/remove and submission creation now record success/failure audits with request metadata and project scope.

Follow-up RED evidence included unauthorized bootstrap changing topic state, custom-role assignment rejection, premature two-episode export completion, missing project authorship/draft filtering, method-insensitive destructive permissions, and absent membership/submission audit records.

Follow-up focused GREEN evidence:

```text
uv run pytest tests/test_project_visibility.py tests/test_workflow_api.py \
  tests/test_workflow_tasks.py tests/test_workflow_bootstrap.py \
  tests/test_role_workflow_e2e.py tests/test_analytics_phase2.py \
  tests/test_creative_lifecycle.py tests/test_creative_phase4.py \
  tests/test_creative_workflow_integration.py tests/test_delivery_permissions.py \
  tests/test_web_security.py tests/test_webgui_ui_changes.py \
  tests/test_role_page_content.py tests/test_role_workflow_migration.py -q
178 passed
```

Follow-up broader role/workflow/creative suite: `286 passed`. Updated unit suite: `488 passed, 1 failed`; the only failure remains the accepted pre-existing XHS Excel factory mismatch.
