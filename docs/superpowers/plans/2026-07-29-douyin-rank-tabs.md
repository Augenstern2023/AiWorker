# Douyin Rank Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display Douyin “榜单” and “热榜” as separate switchable ranking tables, defaulting to “榜单”.

**Architecture:** Group Douyin records by their source `tab` before the existing comparison pipeline, returning `sections` compatible with the ranking-table UI. Extend the Alpine route and selection helpers so the selected Douyin section drives both summary cards and the table without changing Hongguo behavior.

**Tech Stack:** Python 3.11, FastAPI, Alpine.js, Bootstrap 5, pytest.

## Global Constraints

- Keep each captured Douyin tab's original ranks unchanged.
- A 7-record heat board remains exactly 7 records; do not pad, trim, or renumber it.
- Default Douyin tab is “榜单”.
- Keep unrelated dirty-worktree changes untouched.

---

### Task 1: Return Separately Compared Douyin Boards

**Files:**
- Modify: `analysis/today_rank_service.py`
- Test: `tests/test_today_rank_phase.py`

**Interfaces:**
- Produces `sections` for Douyin, with one section per `tab` and a `rankings[0]` payload returned by `_compare_records`.

- [ ] **Step 1: Write a failing service test**

Create a snapshot containing a 16-record `榜单` and a 7-record `热榜`; assert that their `current_entries` remain separate and retain ranks and counts.

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/test_today_rank_phase.py::test_today_rank_splits_douyin_tabs_without_changing_heat_count -q`

Expected: FAIL because Douyin currently returns a single merged comparison without sections.

- [ ] **Step 3: Implement the smallest grouping adapter**

Group records by normalized `tab`, order “榜单” before “热榜”, and reuse `_compare_records` for each group. Keep the existing top-level comparison response for API compatibility.

- [ ] **Step 4: Verify the service test passes**

Run: `uv run pytest tests/test_today_rank_phase.py::test_today_rank_splits_douyin_tabs_without_changing_heat_count -q`

Expected: PASS.

### Task 2: Select Douyin Boards in the UI

**Files:**
- Modify: `templates/common/workspace/selection_ranks.html`
- Modify: `static/js/selection/ranks.js`
- Test: `tests/test_webgui_ui_changes.py`

**Interfaces:**
- Consumes `todayRankData.sections` for Douyin.
- Produces `selectedDouyinSection()`, `setTodayRankRoute('dy', sectionKey)`, and `#/today-rank/dy/<section>`.

- [ ] **Step 1: Write failing template and script assertions**

Assert that the UI renders a Douyin board selector, the default fallback key is “榜单”, and the JavaScript parses the route's fourth component as the selected Douyin board.

- [ ] **Step 2: Verify the test fails**

Run: `uv run pytest tests/test_webgui_ui_changes.py -q`

Expected: FAIL because the current Douyin view has one combined table and no selected-board state.

- [ ] **Step 3: Implement selected-board rendering**

Render “榜单” and “热榜” as buttons from API sections. Bind summary cards and the table to the selected board; preserve Hongguo's existing section/ranking controls.

- [ ] **Step 4: Verify focused tests and JavaScript parsing**

Run: `uv run pytest tests/test_today_rank_phase.py tests/test_webgui_ui_changes.py -q`

Expected: PASS.
