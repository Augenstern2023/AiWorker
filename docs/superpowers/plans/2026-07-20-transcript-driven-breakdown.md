# Transcript-Driven Breakdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate evidence-backed, drama-specific breakdown drafts from at least one downloaded transcript and block template generation when no transcript exists.

**Architecture:** Add a focused transcript asset resolver, a structured LLM breakdown service, and a dedicated background worker. The API performs the fast transcript precondition check and enqueues the worker; the existing Alpine UI shows task progress, opens the generated draft, and routes missing-transcript users into the existing download modal.

**Tech Stack:** Python 3.11, FastAPI, SQLite, existing OpenAI-compatible LLM provider, existing runtime job manager, Alpine.js, Bootstrap 5, pytest, Playwright.

## Global Constraints

- At least one non-empty transcript is required; full-series coverage is not required.
- Facts must cite episode and local transcript/script paths.
- Missing transcripts must not create a draft breakdown.
- Existing locked breakdowns remain immutable; successful regeneration creates a new draft with the latest locked version as parent.
- Static field guidance remains generic, but each displayed example comes from the current generated breakdown.
- Use `uv`, not `pip`.
- Preserve unrelated dirty-worktree changes.

---

### Task 1: Resolve Downloaded Transcript Assets

**Files:**
- Create: `creative/transcript_assets.py`
- Test: `tests/test_creative_transcript_assets.py`

**Interfaces:**
- Consumes: workspace root `str | Path`, topic title `str`, optional platform `str`.
- Produces: `find_transcript_bundle(root, title, platform="") -> dict[str, Any]` with `title`, `episodes`, and `searched_paths`.

- [ ] **Step 1: Write failing resolver tests**

```python
def test_finds_script_by_source_metadata(tmp_path):
    episode = tmp_path / "data/hg/jobs/job-1/dramas/renamed/episode_01"
    episode.mkdir(parents=True)
    (episode / "source.json").write_text(
        json.dumps({"title": "目标剧", "episode": {"episode": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (episode / "script.md").write_text("## 转写内容\n\n第一集真实剧情", encoding="utf-8")
    bundle = find_transcript_bundle(tmp_path, "目标剧", "hg")
    assert bundle["episodes"][0]["episode_number"] == 1
    assert "第一集真实剧情" in bundle["episodes"][0]["text"]


def test_video_without_transcript_is_not_valid(tmp_path):
    episode = tmp_path / "data/hg/jobs/job-1/dramas/目标剧/episode_01"
    episode.mkdir(parents=True)
    (episode / "source.json").write_text(
        json.dumps({"title": "目标剧", "episode": {"episode": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (episode / "video.mp4").write_bytes(b"video")
    assert find_transcript_bundle(tmp_path, "目标剧", "hg")["episodes"] == []
```

- [ ] **Step 2: Run tests and verify the resolver is missing**

Run: `uv run pytest tests/test_creative_transcript_assets.py -q`

Expected: FAIL because `creative.transcript_assets` does not exist.

- [ ] **Step 3: Implement title normalization, metadata matching, text loading, deduplication, and episode sorting**

```python
def find_transcript_bundle(root: str | Path, title: str, platform: str = "") -> dict[str, Any]:
    normalized_title = normalize_title(title)
    candidates = discover source.json and manifest.json under data/{platform or *}/jobs
    for candidate in candidates:
        metadata_title = read title from source/manifest
        if normalize_title(metadata_title) != normalized_title:
            continue
        text = prefer script.md, then transcript.json.text, then transcript txt
        if text.strip():
            add episode with paths and transcript_source
    return newest non-empty asset per episode, sorted by episode_number
```

- [ ] **Step 4: Run resolver tests**

Run: `uv run pytest tests/test_creative_transcript_assets.py -q`

Expected: PASS.

- [ ] **Step 5: Commit resolver**

```powershell
git add creative/transcript_assets.py tests/test_creative_transcript_assets.py
git commit -m "feat: resolve downloaded drama transcripts"
```

### Task 2: Generate and Validate Personalized Breakdowns

**Files:**
- Create: `creative/breakdown_service.py`
- Modify: `creative/prompt_templates.py`
- Modify: `creative/hit_breakdown.py`
- Test: `tests/test_creative_breakdown_generation.py`

**Interfaces:**
- Consumes: `provider`, topic dict, transcript bundle.
- Produces: `generate_breakdown(provider, topic, bundle) -> tuple[dict, dict, list, dict]` containing content, field sources, risks, and generation metadata.
- Produces: `personalize_breakdown_field_guide(content) -> list[dict[str, str]]`.

- [ ] **Step 1: Write failing service tests**

```python
@pytest.mark.asyncio
async def test_generation_uses_transcripts_and_returns_field_evidence():
    provider = SequentialProvider([personalized_breakdown_payload()])
    content, sources, risks, meta = await generate_breakdown(provider, topic, bundle)
    assert "苏软" in content["protagonist_identity"]
    assert sources["protagonist_identity"][0]["episode_number"] == 1
    assert "第一集真实剧情" in provider.calls[0][1]["content"]


def test_field_examples_come_from_current_breakdown():
    guide = personalize_breakdown_field_guide({"protagonist_identity": "苏软，穿书女配", "hook_points": ["第1集：认错反派"]})
    assert next(item for item in guide if item["key"] == "protagonist_identity")["example"] == "苏软，穿书女配"
    assert "被婆家轻视" not in str(guide)
```

- [ ] **Step 2: Run tests and verify missing service failures**

Run: `uv run pytest tests/test_creative_breakdown_generation.py -q`

Expected: FAIL because generation service and personalized guide do not exist.

- [ ] **Step 3: Add the transcript-aware prompt and strict validator**

The prompt must request all 15 editable fields plus `field_sources`, where each source includes `episode_number`, `source_path`, and `excerpt`. Validate text fields as strings, array fields as lists, title equality, and evidence references against the supplied bundle. Use the existing one-repair pattern.

- [ ] **Step 4: Implement personalized field examples**

```python
def personalize_breakdown_field_guide(content):
    guide = breakdown_field_guide()
    for item in guide:
        value = content.get(item["key"])
        item["example"] = str(value[0]) if isinstance(value, list) and value else str(value or "当前转写证据不足，暂未生成示例")
    return guide
```

- [ ] **Step 5: Run service tests**

Run: `uv run pytest tests/test_creative_breakdown_generation.py -q`

Expected: PASS.

- [ ] **Step 6: Commit generation service**

```powershell
git add creative/breakdown_service.py creative/prompt_templates.py creative/hit_breakdown.py tests/test_creative_breakdown_generation.py
git commit -m "feat: generate personalized breakdowns from transcripts"
```

### Task 3: Add Background Breakdown Jobs and API Guard

**Files:**
- Create: `creative/breakdown_worker.py`
- Modify: `creative/api.py`
- Modify: `runtime/worker_launcher.py`
- Test: `tests/test_creative_breakdown_worker.py`
- Test: `tests/test_creative_phase4.py`
- Test: `tests/test_worker_launcher.py`

**Interfaces:**
- API: `POST /api/creative/breakdowns` returns a job response or structured `409 transcript_required`.
- Worker job extra: `topic_id`, `db_path`, `runtime_dir`, `workspace_root`, and serialized transcript summary.
- Worker completion event payload: `{"action": "breakdown", "breakdown_id": int, "topic_id": int, "coverage": [...]}`.

- [ ] **Step 1: Write failing API and worker tests**

```python
def test_breakdown_api_requires_at_least_one_transcript(tmp_path):
    response = client.post("/api/creative/breakdowns", json={"topic_id": topic["id"]})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "transcript_required"


def test_breakdown_api_enqueues_job_when_transcript_exists(tmp_path):
    write_episode_script(tmp_path, "目标剧", 1, "真实剧情")
    response = client.post("/api/creative/breakdowns", json={"topic_id": topic["id"]})
    assert response.status_code == 200
    assert manager.created[0]["crawler_type"] == "breakdown_generate"
```

- [ ] **Step 2: Run tests and verify current template behavior fails**

Run: `uv run pytest tests/test_creative_breakdown_worker.py tests/test_creative_phase4.py tests/test_worker_launcher.py -q`

Expected: FAIL because the API currently creates a static draft and no worker mapping exists.

- [ ] **Step 3: Add launcher mapping and worker**

```python
if platform == "creative" and crawler_type == "breakdown_generate":
    return ["uv", "run", "python", "-m", "creative.breakdown_worker", "--job-file", str(job_file)]
```

The worker re-resolves transcripts, generates the breakdown, creates a draft with the latest locked breakdown as `parent_version_id`, writes a generation artifact, and emits progress and completion events.

- [ ] **Step 4: Replace static POST behavior with precondition check and job creation**

Return `HTTPException(409, detail={...})` when `episodes` is empty. Otherwise enqueue the standard job manager payload and return its state. Add transcript coverage to the response.

- [ ] **Step 5: Return personalized guides from all breakdown GET/PATCH/lock responses**

Use `personalize_breakdown_field_guide(breakdown["content"])` so old and new drafts display examples based on their own content.

- [ ] **Step 6: Run API and worker tests**

Run: `uv run pytest tests/test_creative_breakdown_worker.py tests/test_creative_phase4.py tests/test_worker_launcher.py -q`

Expected: PASS.

- [ ] **Step 7: Commit backend workflow**

```powershell
git add creative/breakdown_worker.py creative/api.py runtime/worker_launcher.py tests/test_creative_breakdown_worker.py tests/test_creative_phase4.py tests/test_worker_launcher.py
git commit -m "feat: run breakdown generation as transcript-backed job"
```

### Task 4: Add Missing-Transcript Modal and Generation Progress

**Files:**
- Modify: `templates/index.html`
- Test: `tests/test_webgui_ui_changes.py`

**Interfaces:**
- Consumes structured `detail.code == "transcript_required"` and job completion event `payload.breakdown_id`.
- Produces `startTranscriptBreakdown(topic, source)`, `openTranscriptRequiredModal(topic)`, and `syncBreakdownGenerationProgress()`.

- [ ] **Step 1: Write failing template tests**

```python
def test_breakdown_generation_requires_transcript_and_shows_progress():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert 'id="transcriptRequiredModal"' in html
    assert "请先下载剧目脚本再拆解" in html
    assert "startTranscriptBreakdown" in html
    assert "syncBreakdownGenerationProgress" in html
    assert "openTopicDownload(breakdownGeneration.topic)" in html
```

- [ ] **Step 2: Run test and verify missing UI fails**

Run: `uv run pytest tests/test_webgui_ui_changes.py -q`

Expected: FAIL because the modal and background state do not exist.

- [ ] **Step 3: Add state, two Bootstrap modals, and shared start function**

The missing-transcript modal has only Cancel and Download actions. The progress modal displays discovered episodes, current job message, progress, retryable failure, and a task-log action.

- [ ] **Step 4: Poll completion and open the generated draft**

On a succeeded job, read `breakdown_id` from the `job_finished` event, fetch `/api/creative/breakdowns/{id}`, refresh the relevant topic list, close progress, and call `openBreakdownEditor(source, title, breakdown)`.

- [ ] **Step 5: Run UI tests and parse the Alpine script**

Run: `uv run pytest tests/test_webgui_ui_changes.py -q`

Run the existing Node `new Function(...)` syntax check against the inline app script.

Expected: PASS and successful JavaScript parse.

- [ ] **Step 6: Commit frontend workflow**

```powershell
git add templates/index.html tests/test_webgui_ui_changes.py
git commit -m "feat: guide breakdown generation through transcript workflow"
```

### Task 5: End-to-End Verification

**Files:**
- Modify only if verification exposes a defect in files from Tasks 1-4.

**Interfaces:**
- Validates the complete browser-to-worker-to-database flow.

- [ ] **Step 1: Run focused regression suite**

```powershell
uv run pytest tests/test_creative_transcript_assets.py tests/test_creative_breakdown_generation.py tests/test_creative_breakdown_worker.py tests/test_creative_phase3.py tests/test_creative_phase4.py tests/test_worker_launcher.py tests/test_webgui_ui_changes.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Restart Web GUI on port 8899**

Stop only the process listening on `8899`, then launch `uv run python WebGUI.py` with a hidden window.

- [ ] **Step 3: Verify missing-transcript behavior with 零五逆袭**

Use Playwright to click its breakdown action. Confirm the modal text, confirm no new breakdown row is created, and close the modal.

- [ ] **Step 4: Verify transcript-backed generation with 穿成炮灰后：不是反派吗？怎么要抱抱**

Start generation, wait for completion, confirm a new draft opens, confirm `protagonist_identity` contains drama-specific names or facts, confirm the field examples match the current content, and confirm evidence points to the downloaded script.

- [ ] **Step 5: Run final diff checks**

```powershell
git diff --check -- creative templates/index.html tests
```

Expected: no whitespace errors in touched files.
