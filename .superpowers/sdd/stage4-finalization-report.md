# Stage 4 Finalization Report

## Baseline and Scope

- Baseline: `867e423` (`fix: tighten machine job controls`).
- Implemented only Stage 4 migration, approved delivery, five-role acceptance, and documentation changes.
- Preserved the external auto-update commit and unrelated dirty `.superpowers/sdd` files.
- Did not implement ZIP packages, complete scripts, or storyboards.

## Implemented

### Legacy migration

- Added `scripts/migrate_role_workflow.py` with `migrate(root, admin_user_id, dry_run=False)`.
- Maps legacy `locked` / `brief_locked` resources to approved lightweight submissions and preserves drafts.
- Stores deterministic legacy IDs and legacy state in snapshot migration metadata.
- Reattributes `creative_projects.created_by = 'local'` to the bootstrap administrator.
- Creates exactly one next role-pool task per active project and blocks on missing or conflicting current pointers instead of guessing.
- Uses deterministic IDs and migration ledgers for idempotency.
- Creates system/creative database backups before writes and restores both databases after a failed cross-database migration.
- Dry runs plan the migration without changing either database.

### Approved administrator delivery

- Added `creative/delivery.py` and registered it in `WebGUI.py`.
- `GET/POST /api/admin/deliveries`, `GET /api/admin/deliveries/options`, and protected download routes require a live system administrator.
- Only approved Brief, characters, outline, and Beats submissions can be delivered.
- Reuses current Markdown/JSON rendering and records project, stage, resource ID/version, submission ID/version, SHA-256 fingerprint, actor, format, path, and creation time.
- Records `delivery.create` audit events and persists delivery history/files across restart.
- Deprecated the legacy direct export path in authenticated applications; it requires an approved submission and directs callers to audited delivery creation with `410 Gone`.
- Replaced direct export links on `/admin/deliveries` with an approved-version selector, format control, delivery history, refresh, and protected download actions.
- Restricted the delivery page and navigation entry to `system_admin`, not a standalone page permission.

### End-to-end acceptance and approval audit

- Added a five-user scenario covering selection operator, creative planner, screenwriter, project owner, and system administrator.
- Covers breakdown, Brief, characters, outline, Beats, automatic next-role tasks, immutable submitted edits, page/API isolation, approved delivery, audit records, and restart persistence.
- Approval transitions now persist `approval_records` for the five reviewed creative stages.

### Documentation

- Updated `improve/壹凡短剧选品平台PRD实施文档.md` with the implemented five roles, local authentication, role pools, page routes, approval registry, approved delivery, and current scope boundary.
- Updated `docs/index.md` with design/plan links, local workflow entry points, migration commands, and the no-full-script/no-storyboard/no-ZIP boundary.

## TDD Evidence

- Migration tests initially failed because `scripts.migrate_role_workflow` did not exist.
- Delivery tests initially failed because the protected delivery API did not exist, then exposed the unprotected legacy export route before it was secured.
- The irreconcilable pointer test failed before migration pointer validation was added.
- Existing direct-export regressions failed after export protection and were updated to the approved-delivery contract while retaining direct Markdown formatter coverage.

## Verification

- Final Stage 4 plus affected workflow/creative/page/security tests: `115 passed`.
- Identity/workflow/audit selection: `112 passed`; creative/UI/job selection: `167 passed`.
- Full `tests/`: `467 passed`, `1 failed`, with only the accepted pre-existing `TestXhsStoreFactory.test_create_excel_store` assertion failure.
- Integration `test/`: `5 passed`, `8 skipped`, `6 failed` because Redis was unavailable at `127.0.0.1:6379` for proxy-pool and Redis-cache integration cases.
- Node syntax: all `24` files under `static/js/**/*.js` parsed with `node --check`.
- Focused pre-commit: all hooks passed after the mixed-line-ending hook normalized `WebGUI.py`, `creative/api.py`, and `docs/index.md`.
- `git diff --check`: clean.

The SQLAlchemy `declarative_base()` deprecation warning remains pre-existing and is unrelated to Stage 4.
