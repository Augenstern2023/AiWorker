# Reuse Existing Breakdowns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the newest existing breakdown by default and only invoke transcript-backed LLM generation from an explicit “重新生成” command.

**Architecture:** Add a repository query for the newest breakdown regardless of status. The existing breakdown POST endpoint returns that record unless `force_regenerate=true`; the Alpine client opens an existing response immediately and reserves the progress workflow for first-time or forced generation.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Pydantic, Alpine.js, Bootstrap 5, pytest, Playwright.

## Global Constraints

- A normal breakdown click must never enqueue an LLM job when any version already exists.
- The newest version is selected by `version DESC, id DESC`, regardless of draft or locked status.
- Explicit regeneration always creates a new draft and never mutates locked versions.
- Existing missing-transcript handling remains unchanged for first-time and forced generation.
- Use `uv`, not `pip`, and preserve unrelated dirty-worktree changes.

---

### Task 1: Reuse the Latest Breakdown in the API

**Files:**
- Modify: `creative/repository.py`
- Modify: `creative/schema.py`
- Modify: `creative/api.py`
- Test: `tests/test_creative_phase4.py`

**Interfaces:**
- Produces: `CreativeRepository.get_latest_breakdown(topic_id: int) -> dict[str, Any] | None`.
- Consumes: `BreakdownCreateRequest.force_regenerate: bool = False`.
- API existing response: `{"status": "existing", "breakdown": {...}}`.
- API generation response: existing job response with `status=success`, `job`, and `coverage`.

- [ ] **Step 1: Write failing repository and API tests**

```python
def test_latest_breakdown_includes_newest_draft(repository):
    locked = create_and_lock_version(repository)
    draft = repository.create_breakdown(locked["topic_id"], {"title": "new"}, {}, [])
    assert repository.get_latest_breakdown(locked["topic_id"])["id"] == draft["id"]


def test_breakdown_api_reuses_latest_without_job(tmp_path):
    client, repository, manager = build_phase4_client(tmp_path)
    topic, locked = seed_adopted_topic(repository)
    response = client.post("/api/creative/breakdowns", json={"topic_id": topic["id"]})
    assert response.json()["status"] == "existing"
    assert response.json()["breakdown"]["id"] == locked["id"]
    assert manager.created == []
```

- [ ] **Step 2: Run tests and verify current unconditional generation fails**

Run: `uv run pytest tests/test_creative_phase4.py -q`

Expected: FAIL because no latest-version query or existing response exists.

- [ ] **Step 3: Implement latest lookup and force flag**

```python
def get_latest_breakdown(self, topic_id: int) -> dict[str, Any] | None:
    with self._connect() as conn:
        row = conn.execute(
            "SELECT * FROM creative_breakdowns WHERE topic_id = ? ORDER BY version DESC, id DESC LIMIT 1",
            (topic_id,),
        ).fetchone()
    return self._breakdown(dict(row)) if row else None


class BreakdownCreateRequest(BaseModel):
    topic_id: int
    force_regenerate: bool = False
```

In `POST /api/creative/breakdowns`, return the personalized latest breakdown before transcript discovery unless `force_regenerate` is true.

- [ ] **Step 4: Verify API tests pass**

Run: `uv run pytest tests/test_creative_phase4.py tests/test_creative_breakdown_worker.py -q`

Expected: PASS.

### Task 2: Open Existing Results and Add Explicit Regeneration

**Files:**
- Modify: `templates/index.html`
- Test: `tests/test_webgui_ui_changes.py`

**Interfaces:**
- Consumes: API `status=existing` and `breakdown`.
- Produces: `regenerateCurrentBreakdown()` sending `{topic_id, force_regenerate: true}`.

- [ ] **Step 1: Write failing UI tests**

```python
def test_existing_breakdown_opens_without_progress_and_regeneration_is_explicit():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "data.status === 'existing'" in html
    assert "data.breakdown" in html
    assert "regenerateCurrentBreakdown" in html
    assert "force_regenerate: true" in html
```

- [ ] **Step 2: Run UI tests and verify behavior is missing**

Run: `uv run pytest tests/test_webgui_ui_changes.py -q`

Expected: FAIL because every response is currently treated as a job.

- [ ] **Step 3: Handle existing response and add the editor command**

Update `startTranscriptBreakdown(topic, source, forceRegenerate = false)` to send the force flag. When the response has `status=existing`, assign the source record and call `openBreakdownEditor` without showing the progress modal. Add a “重新生成” button to the editor footer that calls `regenerateCurrentBreakdown()` and is independent of draft save/lock state.

- [ ] **Step 4: Run focused verification**

Run:

```powershell
uv run pytest tests/test_creative_phase4.py tests/test_creative_breakdown_worker.py tests/test_webgui_ui_changes.py -q
```

Parse the inline Alpine script with the existing Node `new Function(...)` check.

Expected: tests pass and JavaScript parses.

- [ ] **Step 5: Browser-check both branches**

Use Playwright to confirm an existing breakdown opens immediately with no new job, and the explicit editor command creates a new generation job and shows progress.
