# Today Hit Breakdown-Aware Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Distinguish first-time and repeat breakdown actions in the today-hit list and provide archiving after a breakdown exists.

**Architecture:** Enrich topic list rows with metadata for the newest breakdown regardless of draft or locked status. Use that metadata in the Alpine template to select one mutually exclusive action group while reusing the existing breakdown loader and topic status endpoint.

**Tech Stack:** Python 3.11, SQLite, FastAPI, Alpine.js, Bootstrap 5, pytest.

## Global Constraints

- Any draft or locked breakdown counts as already broken down.
- “再次拆解” opens the latest existing breakdown and does not force model generation.
- Archived rows expose no today-hit actions.
- Keep `data.json` and credentials out of commits.

---

### Task 1: Expose Latest Breakdown Metadata

**Files:**
- Modify: `creative/repository.py`
- Test: `tests/test_creative_phase4.py`

**Interfaces:**
- Produces: topic list keys `latest_breakdown_id: int | None`, `latest_breakdown_version: int | None`, and `latest_breakdown_status: str | None`.

- [ ] **Step 1: Write the failing repository test**

Create a locked version followed by a draft and assert `list_topics()` exposes the draft as the latest version while retaining `locked_breakdown_id`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_creative_phase4.py::test_topic_list_exposes_latest_breakdown_metadata -q`

Expected: FAIL because `latest_breakdown_id` is absent.

- [ ] **Step 3: Add correlated latest-version fields**

Extend the topic query with correlated subqueries ordered by `version DESC, id DESC`, then carry the three fields together when same-title records are merged.

- [ ] **Step 4: Run the repository test**

Run: `uv run pytest tests/test_creative_phase4.py::test_topic_list_exposes_latest_breakdown_metadata -q`

Expected: PASS.

---

### Task 2: Render Breakdown-Aware Today-Hit Actions

**Files:**
- Modify: `templates/index.html`
- Test: `tests/test_webgui_ui_changes.py`

**Interfaces:**
- Consumes: `topic.latest_breakdown_id` and `topic.status`.
- Reuses: `createCreativeBreakdown(topic)` and `updateCreativeTopic(topic, status)`.

- [ ] **Step 1: Write the failing template test**

Assert that first-time buttons require `!topic.latest_breakdown_id`, repeat actions require `topic.latest_breakdown_id && topic.status !== 'archived'`, “再次拆解” calls `createCreativeBreakdown(topic)`, and “归档” updates the status to `archived`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_webgui_ui_changes.py::test_today_hit_actions_distinguish_existing_breakdowns -q`

Expected: FAIL because the today-hit template only checks topic status.

- [ ] **Step 3: Add mutually exclusive action groups**

Keep the first-time status matrix under `!topic.latest_breakdown_id`; add “再次拆解 / 归档” for existing non-archived breakdowns; render no buttons for archived rows.

- [ ] **Step 4: Run focused and regression tests**

Run: `uv run pytest tests/test_creative_phase3.py tests/test_creative_phase4.py tests/test_webgui_ui_changes.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Parse and browser-check the template**

Parse the inline Alpine script with Node, restart the Web GUI after confirming no active jobs, and verify first-time, repeat, and archived rows at `http://192.168.1.34:8899`.
