# 创作生成任务并行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow independent Beats and scene-script generation jobs to run concurrently without permitting duplicate generation of the same resource.

**Architecture:** Extend `JobManager._can_run_concurrently` with a `phase4_generate` rule. It derives a stable identity from `project_id`, `action`, and optional `episode_number`; distinct identities may run together, while identical identities remain mutually exclusive. The creative API already supplies those fields in `extra`.

**Tech Stack:** Python 3.11, FastAPI, pytest.

## Global Constraints

- Keep platform-wide exclusion for types outside the explicit concurrency allow-lists.
- Preserve existing drama-download and breakdown concurrency behavior.
- Never allow duplicate non-terminal creative resource identities.
- Use `apply_patch` for edits and `\.venv\Scripts\python.exe -m pytest` for tests.

---

### Task 1: Define creative generation concurrency behavior

**Files:**
- Modify: `tests/test_job_manager.py`

**Interfaces:**
- Consumes: `JobManager.start_job(job: CrawlJob) -> JobState`.
- Produces: Regression coverage for allowed and rejected `phase4_generate` combinations.

- [ ] **Step 1: Write failing tests**

Add a test that seeds a running `phase4_generate` Beats job for project `1`, episode `1`, then asserts that a Beats job for project `1`, episode `2` starts successfully. In the same test, assert that a second project `1`, episode `1` Beats job raises `JobConflictError`.

Add companion cases asserting that a project `1`, episode `1` scene-script job starts alongside the Beats job, that duplicate scene-script jobs are rejected, that duplicate `outlines` jobs for project `1` are rejected, and that an `outlines` job for project `2` starts.

- [ ] **Step 2: Verify red**

Run `\.venv\Scripts\python.exe -m pytest tests\test_job_manager.py -k creative_generation -q`.

Expected: failure from the existing `platform 'creative' already has a running job` guard when starting the different-episode Beats job.

### Task 2: Implement resource-identity concurrency

**Files:**
- Modify: `runtime/job_manager.py:24-31, 121-139`
- Test: `tests/test_job_manager.py`

**Interfaces:**
- Consumes: a `CrawlJob` with `crawler_type="phase4_generate"` and `extra` containing `project_id`, `action`, and optionally `episode_number`.
- Produces: `_can_run_concurrently(existing: CrawlJob, incoming: CrawlJob) -> bool` is `True` only for distinct valid creative generation identities.

- [ ] **Step 1: Add the explicit type set**

Add `_CONCURRENT_CREATIVE_GENERATION_TYPES = frozenset({"phase4_generate"})` beside the existing concurrency type sets.

- [ ] **Step 2: Add a focused policy branch**

In `_can_run_concurrently`, after the breakdown branch, add a branch selected when both crawler types are in `_CONCURRENT_CREATIVE_GENERATION_TYPES`. Build a tuple from stripped `project_id`, `action`, and `episode_number` (empty when absent). Return `False` when `project_id` or `action` is missing; otherwise allow only differing tuples.

- [ ] **Step 3: Verify green**

Run `\.venv\Scripts\python.exe -m pytest tests\test_job_manager.py -k creative_generation -q`.

Expected: pass; different episodes and actions start, duplicate resource identities remain conflicts.

- [ ] **Step 4: Verify local policy compatibility**

Run `\.venv\Scripts\python.exe -m pytest tests\test_job_manager.py -q`.

Expected: all JobManager tests pass, including existing download and breakdown concurrency coverage.

### Task 3: Verify API metadata contract and finish

**Files:**
- Modify: `tests/test_creative_phase4.py`
- Test: `tests/test_creative_phase4.py`

**Interfaces:**
- Consumes: `POST /api/creative/projects/{project_id}/episodes/{episode_number}/beats/generate`.
- Produces: job payloads retaining `project_id`, `action`, and `episode_number` metadata used by `JobManager`.

- [ ] **Step 1: Write an API contract test**

Using `build_phase4_client`, prepare a project with a locked outline batch for two episodes. Submit the Beats generation endpoint for episode `1` and episode `2` with their respective claimed workflow tasks. Assert both responses are `200`, then assert the recording manager received two payloads with `extra.action == "beats"`, the project ID, and `extra.episode_number` values `[1, 2]`.

- [ ] **Step 2: Verify contract**

Run `\.venv\Scripts\python.exe -m pytest tests\test_creative_phase4.py -k episode_scoped_identity -q`.

Expected: pass without production changes, demonstrating that the API already supplies the identity data required by Task 2.

- [ ] **Step 3: Run focused verification and pre-commit**

Run `\.venv\Scripts\python.exe -m pytest tests\test_creative_phase4.py tests\test_job_manager.py -k "episode_scoped_identity or creative_generation or breakdowns_to_run_together" -q`.

Run `\.venv\Scripts\pre-commit.exe run --files runtime\job_manager.py tests\test_job_manager.py tests\test_creative_phase4.py`.

- [ ] **Step 4: Commit**

Run `git add -- runtime/job_manager.py tests/test_job_manager.py tests/test_creative_phase4.py` followed by `git commit -m "feat: allow concurrent creative generation jobs"`.
