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

## Final Important Re-review

The last four Important findings were closed in a third TDD wave:

- Aggregate project responses now apply stage-specific read permissions. Approved records require the matching normal or approved-only permission; non-approved records require the full stage read permission plus either matching workflow-submission authorship or current task assignment. Project authorship no longer grants blanket draft visibility.
- Project owners load a dedicated `/api/creative/topics/workflow-candidates` feed containing only `id`, `title`, `platform`, `category`, `status`, and `captured_at`. The review bootstrap UI uses this restricted feed.
- Transcript preview has an explicit `drama.transcript.read` dependency and is no longer reachable through general approved-topic compatibility.
- Bootstrap leaves topics in `pending_review`. Direct `PATCH ... adopted` is rejected and the selection UI no longer exposes direct adoption. A real `topic_decision` approval performs idempotent creative adoption; returns remain pending. Adoption mirror failures return `503`, record only a failed approval audit, persist reconciliation state, and converge on retry or submission reconciliation.

Third-wave RED evidence reproduced blanket project-author draft access, missing stage permissions, owner candidate-list authorization failure, transcript access through broad topic compatibility, direct selection adoption, missing topic-scope reviewer authorization, and partial cross-database adoption.

Third-wave GREEN evidence:

```text
uv run pytest [22 focused identity/workflow/creative/UI modules] -q
301 passed

uv run pytest tests/ -q
489 passed, 1 failed

uv run pytest -q --ignore=test/test_proxy_ip_pool.py --ignore=test/test_redis_cache.py
493 passed, 8 skipped, 1 failed
```

The sole unit failure remains the accepted pre-existing XHS Excel factory mismatch.

## Final Workflow Ordering And Draft Linkage

The remaining two Important findings were closed with a fourth TDD wave:

- Topic decisions now complete their system approval first, mirror adoption into the creative database second, and only then create the idempotent `source_package` successor. A failed adoption leaves no claimable successor. A failure after adoption is recoverable through retry or task/submission reads, which reconcile the missing successor without duplication.
- Manual blank character batches now require an authenticated active `characters` task. The API validates an explicit task ID or resolves exactly one active assigned task for the current actor and project, persists that ID on every row, and rejects unauthorized, missing, or ambiguous task context. The UI sends the active task ID, and the linked draft remains visible, editable, and submittable.
- Mirror-error event recording now ensures the disabled workflow system actor exists before writing the foreign-keyed event.

Fourth-wave RED evidence:

```text
tests/test_workflow_bootstrap.py::test_topic_adoption_mirror_failure_audits_failure_and_reconciles
  expected zero source_package tasks after adoption failure, observed one
tests/test_workflow_bootstrap.py::test_topic_successor_failure_reconciles_on_task_read
  expected adopted topic after injected successor failure, observed pending_review
tests/test_role_workflow_e2e.py::test_five_role_workflow_isolated_audited_and_persistent
  expected missing active characters task to return 409, observed 200
tests/test_webgui_ui_changes.py::test_blank_character_batch_links_the_active_workflow_task
  blank character creation did not resolve or send the active workflow task
```

Fourth-wave GREEN evidence:

```text
uv run pytest tests/test_workflow_bootstrap.py tests/test_role_workflow_e2e.py \
  tests/test_webgui_ui_changes.py tests/test_workflow_transitions.py -q
71 passed

uv run pytest [22 focused identity/workflow/creative/UI modules] -q
280 passed

uv run pytest tests/ -q
491 passed, 1 failed

uv run pytest -q --ignore=test/test_proxy_ip_pool.py --ignore=test/test_redis_cache.py
495 passed, 8 skipped, 1 failed
```

The only failure in both broad runs remains the accepted pre-existing XHS Excel factory mismatch.

## Fail-Closed Creative Task Governance

The final Important gap was closed with a fifth TDD wave:

- Every workflow-governed creative creation route now resolves and validates an active task instead of treating `workflow_task_id` as optional. Omitted IDs resolve only when exactly one assigned, in-progress, or returned task matches the current actor and stage/project/topic; zero or multiple matches return `409`.
- Breakdown generation requires `breakdown`; Brief/project/direction creation requires `brief`; character, outline, and Beats generation or manual version creation require their matching nodes. Explicit wrong-node, wrong-project, wrong-topic, unauthorized, and closed tasks remain rejected.
- Hongguo download, material analysis, and Douyin fallback jobs require `source_package`. They resolve one eligible task when omitted, reject `breakdown` and other nodes, and always persist job creator, workflow-task, project, and task-job linkage.
- Project creation now requires an approved breakdown plus an unbound active Brief task. Binding the new project updates the whole workflow and bootstraps memberships for the Brief creator and prior workflow reviewers, preserving the normal topic-to-project chain.
- Selection and planning UI actions resolve/start and send the active task for source download, breakdown generation, project creation, directions, characters, outlines, and Beats.

Fifth-wave RED evidence:

```text
tests/test_job_permissions.py::test_selection_download_job_records_owner_task_link_and_audit
  a breakdown task was accepted for source download (200 instead of 409)
tests/test_creative_phase4.py::test_phase4_generation_api_enqueues_standard_creative_job
  Brief generation without a human task was accepted (200 instead of 409)
tests/test_creative_phase4.py::test_phase4_api_creates_multiple_projects_and_lists_them
  project creation accepted a merely locked, unapproved breakdown
tests/test_webgui_ui_changes.py::test_workflow_governed_creation_actions_send_active_task_ids
  source download, breakdown generation, and project creation omitted active task IDs
```

Fifth-wave GREEN evidence:

```text
uv run pytest tests/test_creative_phase4.py tests/test_job_permissions.py \
  tests/test_role_workflow_e2e.py tests/test_project_visibility.py \
  tests/test_creative_workflow_integration.py tests/test_workflow_bootstrap.py \
  tests/test_webgui_ui_changes.py tests/test_workflow_tasks.py -q
139 passed

uv run pytest tests/ -q
492 passed, 1 failed

uv run pytest -q --ignore=test/test_proxy_ip_pool.py --ignore=test/test_redis_cache.py
496 passed, 8 skipped, 1 failed
```

The only failure in both broad runs remains the accepted pre-existing XHS Excel factory mismatch.

## Exact Source Scope And Project Reconciliation

The last two Important findings were closed with a sixth TDD wave:

- Every Hongguo download/material request and Douyin fallback request now carries a required `topic_id`. The server loads that exact topic, compares normalized titles, and requires the active `source_package` task to reference the same topic. Unknown topics, title mismatches, task-topic mismatches, and omitted scope are rejected without scanning or treating an empty match as authorization.
- Both selection download flows and the standalone drama-material UI resolve the one matching source task and send its exact `topic_id` and `workflow_task_id`. Created jobs retain `topic_id` in job extras together with ownership and workflow linkage.
- `creative_projects.workflow_task_id` now has a migrated unique partial index. Duplicate legacy links are detached while preferring the active project. Repository creation is idempotent by task ID and handles unique races by reading the winner.
- Project creation first finds an existing project for the Brief task, then reconciles workflow binding and memberships. A post-creative bind failure returns `503`; retry or project-list reconciliation binds the same project and exposes it without creating a duplicate.

Sixth-wave RED evidence:

```text
tests/test_job_permissions.py::test_source_job_rejects_unknown_or_mismatched_topic
  unknown topic_id was ignored and the source job was created
tests/test_creative_phase4.py::test_project_creation_retries_binding_without_duplicate
  injected system bind failure escaped instead of returning a reconcilable 503
tests/test_webgui_ui_changes.py::test_drama_material_jobs_send_exact_source_topic_and_task
  standalone material jobs omitted exact topic/task scope
```

Sixth-wave GREEN evidence:

```text
uv run pytest tests/test_job_permissions.py tests/test_creative_phase4.py \
  tests/test_webgui_ui_changes.py tests/test_workflow_bootstrap.py -q
103 passed

uv run pytest tests/ -q
495 passed, 1 failed

uv run pytest -q --ignore=test/test_proxy_ip_pool.py --ignore=test/test_redis_cache.py
499 passed, 8 skipped, 1 failed
```

The only failure in both broad runs remains the accepted pre-existing XHS Excel factory mismatch.
