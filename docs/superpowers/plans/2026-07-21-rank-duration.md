# Rank Duration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add continuous and cumulative days-on-rank metrics to today-rank records and display them immediately to the right of the hot-comment column.

**Architecture:** `TodayRankService` derives per-record duration metrics from all snapshots up to the selected date, scoped by platform ranking group and record identity. It annotates copies of the selected and previous snapshots before the existing comparison pipeline, while the shared dynamic column definitions expose a dedicated duration renderer in Alpine.

**Tech Stack:** Python 3.11, dataclasses, FastAPI service responses, Alpine.js, Bootstrap 5, pytest, Playwright.

## Global Constraints

- Continuous days reset only when a snapshot day contains the same ranking group and explicitly omits the drama.
- Days with no data for the same ranking group do not break continuity.
- Cumulative days count distinct natural dates and never count multiple snapshots on one date twice.
- Historical queries use no snapshots later than the selected snapshot.
- The column must be immediately after `hot_comment` for both Douyin and Hongguo.
- Use `uv`, not `pip`, and preserve unrelated dirty-worktree changes.

---

### Task 1: Derive Rank Duration Metrics

**Files:**
- Modify: `analysis/today_rank_service.py`
- Test: `tests/test_today_rank_phase.py`

**Interfaces:**
- Produces record fields `continuous_rank_days: int`, `cumulative_rank_days: int`, and `rank_duration: str`.
- Produces helper `_annotate_rank_durations(snapshot, snapshots) -> RankSnapshot`.

- [ ] **Step 1: Write failing history tests**

Add CSV-backed tests covering three cases: uninterrupted appearances, an explicit same-board absence that resets continuous days while preserving cumulative days, and a date with no same-board data that does not reset continuity.

```python
assert entry["continuous_rank_days"] == 1
assert entry["cumulative_rank_days"] == 2
assert entry["rank_duration"] == "连续在榜1天 / 累计在榜2天"
```

- [ ] **Step 2: Run tests and verify fields are missing**

Run: `uv run pytest tests/test_today_rank_phase.py -q`

Expected: FAIL with missing duration fields.

- [ ] **Step 3: Implement daily group history annotation**

Build daily unions keyed by ranking group. For each target record, count distinct appearance dates for cumulative days, walk backward over dates that contain the same group until an explicit absence for the continuous start, then use inclusive natural-date difference for continuous days. Annotate cloned records and return a cloned `RankSnapshot` so cached/raw snapshots are not mutated.

- [ ] **Step 4: Add the backend column after hot comment**

Append this entry after `hot_comment` in `RANK_DATA_COLUMNS`:

```python
{"key": "rank_duration", "label": "在榜时长", "kind": "rank_duration", "width": 210}
```

- [ ] **Step 5: Run backend tests**

Run: `uv run pytest tests/test_today_rank_phase.py -q`

Expected: PASS.

### Task 2: Render Rank Duration in Today Rank Tables

**Files:**
- Modify: `templates/index.html`
- Test: `tests/test_webgui_ui_changes.py`

**Interfaces:**
- Consumes: `rank_duration`, `continuous_rank_days`, and `cumulative_rank_days`.
- Produces: `formatRankDuration(item)`.

- [ ] **Step 1: Write failing UI order and renderer tests**

```python
assert "formatRankDuration" in html
assert html.index("key: 'hot_comment'") < html.index("key: 'rank_duration'")
assert "连续在榜" in html
assert "累计在榜" in html
```

- [ ] **Step 2: Run UI tests and verify missing column**

Run: `uv run pytest tests/test_webgui_ui_changes.py -q`

Expected: FAIL because the fallback columns and renderer lack rank duration.

- [ ] **Step 3: Add the fallback column and formatter**

Add the duration candidate immediately after hot comment, allow `rank_duration` cells through the shared value template, and format raw numeric fields when the backend display string is absent.

- [ ] **Step 4: Run focused tests and parse JavaScript**

Run:

```powershell
uv run pytest tests/test_today_rank_phase.py tests/test_webgui_ui_changes.py -q
```

Then parse the inline Alpine script with the existing Node `new Function(...)` check.

Expected: tests pass and JavaScript parses.

- [ ] **Step 5: Browser-check column order and values**

Restart the Web GUI only after confirming no active jobs. Use Playwright to select a Hongguo ranking and assert the “在榜时长” header is immediately right of “热评” and visible rows contain both continuous and cumulative day labels.
