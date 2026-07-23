# Project Owner Generated Content Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a managing project owner preview and locally download the latest or historic generated Brief, character relationships, outline, and Beats content from the topic library.

**Architecture:** Add a read-only project API that derives immutable preview snapshots from the existing creative resources, validates managed-project access, and returns stage-specific version metadata. A second API serializes a selected validated snapshot directly into an HTTP attachment. The topic-library lifecycle badge opens a Bootstrap modal that uses the existing read-only approval renderer.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Alpine.js, Bootstrap 5, pytest, uv.

## Global Constraints

- Allow only the managing project owner or system administrator through the preview API.
- Use browser attachments for downloads; do not write a server delivery or export file.
- Default to the newest generated resource and retain historic resources read-only.
- Preserve unrelated dirty-worktree changes.

---

### Task 1: Read-only generated-content API

**Files:**
- Modify: `creative/api.py`
- Test: `tests/test_creative_phase4.py`

**Interfaces:**
- Produces `GET /api/creative/projects/{project_id}/generated-content?stage={brief|characters|outline|beats}`.
- Produces `GET /api/creative/projects/{project_id}/generated-content/download?stage=...&resource_id=...&format={md|json}`.

- [ ] Write failing API tests for a managing owner receiving the newest version first, selecting a historic resource, rejecting a non-manager, and receiving an attachment header.
- [ ] Run `uv run pytest tests/test_creative_phase4.py -k generated_content -q` and confirm the routes are absent.
- [ ] Build stage snapshots from existing blueprints, character batches, outline batches, and individual Beats resources using `CreativeWorkflowAdapter.snapshot`; validate every selected resource belongs to the requested project.
- [ ] Permit the two GET routes through `creative_api_authorization`, then enforce `assert_project_read_access` in the route.
- [ ] Serialize the selected snapshot with the existing delivery serializer and return `Content-Disposition: attachment` without creating a delivery record.
- [ ] Re-run the focused API tests.

### Task 2: Topic-library preview modal

**Files:**
- Modify: `static/js/selection/topics.js`
- Modify: `static/js/common/tasks.js`
- Modify: `templates/common/workspace/selection_topics.html`
- Modify: `templates/common/workspace/selection_modals.html`
- Test: `tests/test_webgui_ui_changes.py`

**Interfaces:**
- Consumes the Task 1 `generated-content` response: `{project, stage, versions, selected_resource_id, snapshot}`.
- Produces `openGeneratedContentPreview(topic)`, `selectGeneratedContentVersion(resourceId)`, and `downloadGeneratedContent(format)`.

- [ ] Write a failing UI source test that expects clickable generated badges, a version selector, the approval renderer, and the local download endpoint.
- [ ] Run `uv run pytest tests/test_webgui_ui_changes.py -k generated_content -q` and confirm it fails.
- [ ] Add modal state to the common Alpine store, load the newest version when a generated badge is clicked, and reload the requested historic resource on selection.
- [ ] Render only stage-specific content through `window.aiworkerApprovalRenderer.render`, with loading, empty, and error states, and download through the authenticated attachment endpoint.
- [ ] Re-run the focused UI tests and `uv run pytest tests/test_creative_phase4.py tests/test_webgui_ui_changes.py -q`.
