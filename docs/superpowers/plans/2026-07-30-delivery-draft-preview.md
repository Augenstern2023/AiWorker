# 成果交付草稿预览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let delivery users select every generated creative resource, create downloadable draft snapshots, and preserve formal-delivery lifecycle effects for locked resources only.

**Architecture:** Extend `creative_deliveries` with resource status and formal-delivery metadata. `DeliveryService.options` enumerates all generated versions, while `DeliveryService.create` classifies the selected resource as formal or draft. Reconciliation performs stage export and workflow completion only for formal records. The existing page renders status labels from the API.

**Tech Stack:** Python 3.11, SQLite, FastAPI, vanilla JavaScript, pytest.

## Global Constraints

- Draft snapshots must never complete a delivery workflow task or record a stage export.
- Locked, brief-locked, and approved resources retain current formal-delivery behavior.
- Resource/project access authorization remains unchanged.
- Use `apply_patch` for edits and `\.venv\Scripts\python.exe -m pytest` for verification.

---

### Task 1: Cover draft and formal delivery classification

**Files:**
- Modify: `tests/test_scene_scripts.py`
- Modify: `creative/delivery.py`

**Interfaces:**
- Consumes: `DeliveryService.options()` and `DeliveryService.create(...)`.
- Produces: options exposing resource `status` and `is_formal`, plus draft delivery records that do not progress lifecycle state.

- [ ] **Step 1: Write failing service tests**

Create a project with a generated draft resource and a delivery task. Assert `options()` contains the draft resource with `status == "draft"` and `is_formal is False`. Create its snapshot and assert the returned record is draft, the file exists, the delivery task remains non-terminal, and no stage export exists. Add a locked-resource case proving `is_formal is True` and workflow completion is retained.

- [ ] **Step 2: Run red**

Run `\.venv\Scripts\python.exe -m pytest tests\test_scene_scripts.py -k "draft_delivery or delivery_options" -q`.

Expected: failure because options only include locked resources and `create()` rejects drafts.

- [ ] **Step 3: Add delivery persistence fields and classification helpers**

Add `resource_status` and `is_formal` columns with backward-compatible schema migration. Implement a single helper that classifies locked, brief-locked, and approved resources as formal; all existing generated resource statuses are draft previews. Persist those fields in new delivery records and expose them through `_public_record`.

- [ ] **Step 4: Run the service tests green**

Run `\.venv\Scripts\python.exe -m pytest tests\test_scene_scripts.py -k "draft_delivery or delivery_options" -q`.

Expected: pass.

### Task 2: Enumerate every generated resource version

**Files:**
- Modify: `creative/delivery.py`
- Test: `tests/test_scene_scripts.py`

**Interfaces:**
- Consumes: `CreativeRepository.list_projects()` project resources.
- Produces: `DeliveryService.options()` records for all generated brief, character, outline, beats, and script versions.

- [ ] **Step 1: Replace locked-pointer option selection**

Build options from project `blueprints`, unique character batch IDs, unique outline batch IDs, and all `episodes` with stage `beats` or `script`. Skip empty character and outline batches. Include `resource_id`, `stage`, `resource_version`, `project_id`, `project_name`, `resource_status`, and `is_formal`.

- [ ] **Step 2: Do not require a delivery task for listing or draft snapshots**

For formal resources, resolve the current delivery workflow task exactly as today. For drafts, set `workflow_task_id` to `None` and create the snapshot without resolving a task.

- [ ] **Step 3: Guard reconciliation side effects**

Return draft records after file persistence and audit recording without calling `_record_stage_export` or `_complete_workflow_task`. Keep those calls for formal records only.

- [ ] **Step 4: Run focused lifecycle tests**

Run `\.venv\Scripts\python.exe -m pytest tests\test_scene_scripts.py tests\test_creative_lifecycle.py -k "delivery or scene_script_download" -q`.

Expected: draft preview tests and existing script delivery tests pass.

### Task 3: Make draft state visible on the delivery page

**Files:**
- Modify: `templates/admin/deliveries.html`
- Modify: `static/js/admin/deliveries.js`
- Modify: `tests/test_webgui_ui_changes.py`

**Interfaces:**
- Consumes: delivery option and record fields `resource_status` and `is_formal`.
- Produces: a “可预览成果” selector and visible “草稿预览 / 正式交付” labels.

- [ ] **Step 1: Write a failing page-source test**

Assert the page uses “可预览成果” rather than “已完成版本”, and the delivery JavaScript includes labels for `草稿预览`, `正式交付`, and resource status.

- [ ] **Step 2: Run red**

Run `\.venv\Scripts\python.exe -m pytest tests\test_webgui_ui_changes.py -k delivery_draft_preview -q`.

Expected: failure because the page still calls resources “已完成版本” and renders no status.

- [ ] **Step 3: Render API status**

Update option labels and history rows to display the resource status and whether the record is a preview snapshot or formal delivery. Preserve the existing download action.

- [ ] **Step 4: Run focused tests and pre-commit**

Run `\.venv\Scripts\python.exe -m pytest tests\test_scene_scripts.py tests\test_webgui_ui_changes.py -k "draft_delivery or delivery_options or delivery_draft_preview" -q`.

Run `\.venv\Scripts\pre-commit.exe run --files creative\delivery.py templates\admin\deliveries.html static\js\admin\deliveries.js tests\test_scene_scripts.py tests\test_webgui_ui_changes.py`.

- [ ] **Step 5: Commit**

Run `git add -- creative/delivery.py templates/admin/deliveries.html static/js/admin/deliveries.js tests/test_scene_scripts.py tests/test_webgui_ui_changes.py` followed by `git commit -m "feat: preview draft delivery resources"`.
