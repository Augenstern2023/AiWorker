# Final Fixes V9 Report

## Scope

Made project-list reconciliation side-effect free when a project's Brief creation task is already bound to that project. Previously, every list request by the creator called `bind_workflow_project()` for an already-consistent binding. That method bootstraps creator and reviewer memberships, so it silently reversed a project owner's explicit removal of the creator.

## RED Evidence

The regression was added before the implementation:

```text
uv run pytest tests/test_project_visibility.py::test_project_author_owns_drafts_and_managed_reader_sees_approved_only -q

FAILED
expected the removed creator's project list to be empty
observed the project in the response after list reconciliation recreated membership
```

The test also confirms that the owner successfully removes the creator membership before the creator requests `/api/creative/projects`.

## Implementation

Project-list reconciliation now handles the three binding states independently:

- An unbound Brief task still calls `bind_workflow_project()` to perform the initial binding and membership bootstrap.
- A Brief task bound to a different project still calls `reconcile_project_workflow_task()` to migrate creative creation ownership to the authoritative project.
- A Brief task already bound to the current project performs no operation.

No behavior inside `bind_workflow_project()` was changed.

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

Unit suite:

```text
uv run pytest tests/ -q
498 passed, 1 failed
```

The sole failure is the accepted pre-existing XHS Excel factory mismatch: `TestXhsStoreFactory.test_create_excel_store` returns `ExcelStoreBase` rather than `XhsExcelStoreImplement`.

Excluding that accepted mismatch, the unit suite is green:

```text
uv run pytest tests/ -q --deselect tests/test_store_factory.py::TestXhsStoreFactory::test_create_excel_store
498 passed, 1 deselected
```

## Self-Review

- `bound_id is None` still performs the first binding and membership bootstrap.
- A binding to a different project still migrates creation ownership.
- An equal project binding is now side-effect free.
- A project owner's explicit membership removal remains durable across project-list reads, and the removed creator cannot see the project.
- Unrelated dirty and untracked `.superpowers/sdd` files remain untouched.
