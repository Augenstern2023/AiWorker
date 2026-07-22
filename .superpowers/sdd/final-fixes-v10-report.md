# Final Fixes V10 Report

## Scope

Made idempotent project creation preserve explicit project membership removals. The project-list path was corrected in V9, but retrying the original `POST /api/creative/projects` request still called `bind_workflow_project()` after finding the already-bound project. Because binding bootstraps creator membership, that retry reversed a project owner's removal decision.

## RED Evidence

The V9 visibility regression was extended before implementation to retry the same creation request after the project owner removes the creator:

```text
uv run pytest tests/test_project_visibility.py::test_project_author_owns_drafts_and_managed_reader_sees_approved_only -q

FAILED
the idempotent POST returned the existing project, but creator membership was recreated
```

The regression requires the retry to return the same project while leaving membership absent and direct project access forbidden.

## Implementation

The creation endpoint now retains the task's original `bound_id` and applies the same three-way policy as project-list reconciliation:

- `bound_id is None`: create or recover the creative project, then perform the first workflow binding and membership bootstrap.
- `bound_id` points to another creative project record: reconcile creation ownership to the authoritative system-bound project without rebinding membership.
- `bound_id` already matches the idempotent project: return it without calling the binding method.

## Binding Audit

`creative/api.py` contains two `bind_workflow_project()` calls:

- Project creation: guarded by `bound_id is None`.
- Project-list repair: guarded by `bound_id is None`.

Both calls are now limited to genuine first binding. Already-consistent read/retry paths cannot recreate membership, while the initial binding behavior is unchanged.

## GREEN Evidence

Focused regression:

```text
uv run pytest tests/test_project_visibility.py::test_project_author_owns_drafts_and_managed_reader_sees_approved_only -q
1 passed
```

Targeted project, workflow, migration, and creative integration coverage:

```text
uv run pytest tests/test_project_visibility.py tests/test_workflow_tasks.py \
  tests/test_workflow_api.py tests/test_creative_phase4.py \
  tests/test_creative_workflow_integration.py \
  tests/test_role_workflow_migration.py -q
83 passed
```

## Self-Review

- A brand-new project with an unbound Brief task still performs exactly one workflow binding.
- Recovery of a creative record created before a failed first binding still retries that first binding.
- An authoritative binding to another project still reconciles creative creation ownership.
- An already-equal binding is side-effect free in both GET and POST idempotent paths.
- The repeated POST returns the existing project but does not restore membership or project access.
- Unrelated dirty and untracked files remain untouched.
