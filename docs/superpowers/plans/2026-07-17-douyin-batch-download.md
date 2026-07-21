# Douyin Batch Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Default to the first Douyin result, support ordered multi-video downloads, and skip explicitly paid content across Douyin and Hongguo downloads.

**Architecture:** Normalize provider responses in the clients, keep ordered identifiers in the job contract, and let workers own serial execution and manifests. The Alpine modal manages selection only and relies on existing job progress APIs.

**Tech Stack:** Python 3.11, FastAPI/Pydantic, Playwright/CDP, Alpine.js, pytest.

## Global Constraints

- Preserve existing single-item API compatibility.
- Never attempt to bypass or download explicitly paid content.
- Keep downloads serial to reduce Douyin account risk.
- Do not remove existing pause, cancel, background, or artifact behavior.

---

### Task 1: Provider normalization and paid policy

**Files:**
- Create: `agents/drama_download_policy.py`
- Modify: `agents/douyin_drama_fallback.py`
- Modify: `agents/hongguo_drama.py`
- Test: `tests/test_hongguo_drama.py`

- [ ] Add failing tests for explicit paid fields and mix-item expansion.
- [ ] Run the focused tests and confirm they fail for missing behavior.
- [ ] Implement conservative paid detection and ordered candidate normalization.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Batch API and worker

**Files:**
- Modify: `WebGUI.py`
- Modify: `agents/douyin_drama_fallback_worker.py`
- Test: `tests/test_hongguo_drama.py`

- [ ] Add failing tests for ordered `aweme_ids`, legacy compatibility, batch output, and paid skips.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement request validation, serial processing, pause checkpoints, item directories, and `manifest.json`.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Topic-library modal

**Files:**
- Modify: `templates/index.html`
- Test: `tests/test_webgui_ui_changes.py`

- [ ] Add failing UI assertions for primary selection, multi-select, paid labels, and batch payloads.
- [ ] Run the UI test and confirm it fails.
- [ ] Implement the Douyin-only multi-select branch without changing Hongguo episode selection.
- [ ] Run the UI test and confirm it passes.

### Task 4: Hongguo paid skips and verification

**Files:**
- Modify: `agents/hongguo_drama_download_worker.py`
- Modify: `agents/hongguo_drama_worker.py`
- Test: `tests/test_hongguo_drama.py`

- [ ] Add failing tests for known-paid episodes and provider payment errors.
- [ ] Implement skip records and non-failing all-paid completion.
- [ ] Run focused and full unit suites.
- [ ] Restart WebGUI on port `8899` and verify the live page contains the batch controls.

