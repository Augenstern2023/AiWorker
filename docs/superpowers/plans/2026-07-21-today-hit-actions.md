# Today Hit Status Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make today-hit row actions reflect the current topic status without contradictory or redundant commands.

**Architecture:** Keep the existing update endpoints and Alpine methods. Restrict button visibility directly in the today-hit row template so local status updates immediately switch the available actions.

**Tech Stack:** Alpine.js, Bootstrap 5, pytest, Playwright.

## Global Constraints

- Change only the today-hit action column.
- Pending rows show adopt, breakdown, and reject.
- Adopted rows show breakdown only.
- Rejected rows show re-adopt only.
- Use existing `updateCreativeTopic` and `createCreativeBreakdown` methods.

---

### Task 1: Render Status-Aware Today-Hit Actions

**Files:**
- Modify: `templates/index.html`
- Test: `tests/test_webgui_ui_changes.py`

- [ ] **Step 1: Write a failing template test**

Assert that adopt and reject use `x-show="topic.status === 'pending_review'"`, breakdown is available for pending/adopted states, and rejected rows contain a “重新采纳” action that updates to `adopted`.

- [ ] **Step 2: Run the test and verify unconditional buttons fail**

Run: `uv run pytest tests/test_webgui_ui_changes.py -q`

Expected: FAIL because today-hit buttons are currently unconditional.

- [ ] **Step 3: Add status visibility conditions**

Use Alpine `x-show` conditions on the existing buttons and add the re-adopt button for `rejected` rows. Do not change the topic-library action template.

- [ ] **Step 4: Verify tests and JavaScript parsing**

Run: `uv run pytest tests/test_webgui_ui_changes.py -q`, then parse the inline Alpine script with Node `new Function(...)`.

- [ ] **Step 5: Browser-check pending, adopted, and rejected rows**

Verify each visible row exposes only the actions allowed by the design.
