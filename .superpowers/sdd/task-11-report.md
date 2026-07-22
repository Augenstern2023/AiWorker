# Task 11 Report: Secure and Link Machine Jobs

## Implemented

- Normalized `created_by_user_id`, `workflow_task_id`, and `project_id` into `CrawlJob.extra` and exposed them in JobManager public state.
- Added idempotent `workflow_task_jobs` repository linkage for job creation and retry.
- Attached authenticated ownership and workflow/project context to selection download, crawler, breakdown, and creative generation jobs.
- Protected job list, detail, logs, stop, pause, resume, retry, delete, latest logs, and legacy stop endpoints.
- Enforced creator permissions, active project membership for project reads, managed-project owner control, admin wildcard access, and admin-only legacy/delete behavior.
- Audited successful create, stop, pause, resume, retry, and delete mutations.
- Kept worker launch and command selection unchanged; ownership remains metadata in `CrawlJob.extra`.

## TDD Coverage

- JobManager ownership persistence and public state.
- Human-task to machine-job linkage and idempotency.
- Selection download ownership, linkage, and create audit.
- Creative generation ownership, project/task linkage, and create audit.
- Creator, project member, project owner, unauthorized user, admin, and legacy visibility/control cases.
- Pause/resume/stop/retry/delete mutation audits and authentication of legacy job endpoints.

## Verification

```text
uv run python -m py_compile WebGUI.py creative/api.py runtime/job_manager.py workflow/repository.py tests/test_job_permissions.py
PASS

uv run pytest tests/test_job_manager.py tests/test_job_permissions.py tests/test_webgui_ui_changes.py -q
62 passed, 1 warning in 3.90s

git diff --check
PASS (only existing CRLF conversion notices)

uv run pre-commit run
PASS for the staged Task 11 files
```

The warning is the existing SQLAlchemy `declarative_base()` deprecation warning from `database/models.py`.

`uv run pre-commit run --all-files` was also attempted. The task files passed
the header check, but the repository-wide run failed because unrelated existing
Python files lack required headers; its broad formatting changes were reverted.
