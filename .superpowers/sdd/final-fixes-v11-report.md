# Final Fixes V11 Report

## Scope

Closed a project-scope bypass in idempotent project creation. V10 stopped an equal-binding retry from recreating membership, but the endpoint still returned `CreativeRepository.get_project()` directly. A removed creator could therefore repeat the original `POST /api/creative/projects` request and read every blueprint, character, relationship, and episode in the existing project despite direct GET and list access being denied.

## RED Evidence

The project visibility regression was extended before implementation:

```text
uv run pytest tests/test_project_visibility.py::test_project_author_owns_drafts_and_managed_reader_sees_approved_only -q

FAILED
expected 403, received 200 with v11-sensitive-blueprint in the response body
```

The regression first confirms that a current member can retry creation idempotently. It then removes that member, adds a sensitive draft blueprint, confirms direct GET is forbidden, and repeats the same project-creation request.

## Implementation

- Added a project read-access assertion using the same assigned-or-managed project rules as the project routes.
- Marked the request as an existing-project path whenever the creative project already exists or the workflow task is already bound.
- Before any existing-project response, the endpoint now requires current project visibility.
- Authorized retries return `project_view()`, so stage-level resource filtering matches direct project reads instead of exposing the repository's unfiltered aggregate.
- Brand-new first creation retains its existing response behavior after successful initial binding.

## Existing-Path Audit

`create_project()` has no early return before its final response. Every equal-binding, cross-database reconciliation, and failed-first-binding recovery path converges on `resolved_project`. All paths classified as existing projects pass through `assert_project_read_access()` before returning data. Unauthorized retries therefore return `403` without serializing project resources.

The initial `bound_id is None` plus no-existing-project path still creates and binds normally. Existing administrator retry coverage remains valid because system administrators pass the visibility check, and the regression confirms an active member retry remains `200` and idempotent.

## GREEN Evidence

Focused visibility and leakage regression:

```text
uv run pytest tests/test_project_visibility.py::test_project_author_owns_drafts_and_managed_reader_sees_approved_only -q
1 passed
```

Project visibility, creative Phase 4/workflow integration, and identity permission coverage:

```text
uv run pytest tests/test_project_visibility.py tests/test_creative_phase4.py \
  tests/test_creative_workflow_integration.py tests/test_identity_permissions.py \
  tests/test_identity_repository.py -q
70 passed
```

## Self-Review

- Removed creators receive `403` from both direct GET and idempotent POST.
- The forbidden POST response contains no sensitive blueprint content.
- Active members retain a `200` idempotent retry and receive the normal permission-filtered project view.
- System administrator retries remain `200` through existing Phase 4 coverage.
- Cross-project ownership reconciliation cannot return the authoritative project before its visibility check.
- Unrelated dirty and untracked files remain untouched.
