### Task 6: Split Selection, Planning, Writing, Review, and Admin Pages

**Files:**
- Create: `templates/selection/dashboard.html`
- Create: `templates/selection/ranks.html`
- Create: `templates/selection/topics.html`
- Create: `templates/selection/dramas.html`
- Create: `templates/selection/breakdowns.html`
- Create: `templates/planning/projects.html`
- Create: `templates/planning/directions.html`
- Create: `templates/planning/brief.html`
- Create: `templates/planning/characters.html`
- Create: `templates/writing/projects.html`
- Create: `templates/writing/outlines.html`
- Create: `templates/writing/beats.html`
- Create: `templates/review/projects.html`
- Create: `templates/review/tasks.html`
- Create: `templates/review/approvals.html`
- Create: `templates/review/versions.html`
- Create: `templates/admin/users.html`
- Create: `templates/admin/roles.html`
- Create: `templates/admin/config.html`
- Create: `templates/admin/data.html`
- Create: `templates/admin/analytics.html`
- Create: `templates/admin/jobs.html`
- Create: `templates/admin/deliveries.html`
- Create: `templates/admin/audit.html`
- Create: `static/js/common/api.js`
- Create: `static/js/common/session.js`
- Create: `static/js/common/tasks.js`
- Create: `static/js/selection/ranks.js`
- Create: `static/js/selection/topics.js`
- Create: `static/js/selection/dramas.js`
- Create: `static/js/selection/breakdowns.js`
- Create: `static/js/planning/projects.js`
- Create: `static/js/planning/brief.js`
- Create: `static/js/planning/characters.js`
- Create: `static/js/writing/outlines.js`
- Create: `static/js/writing/beats.js`
- Create: `static/js/review/tasks.js`
- Create: `static/js/review/approvals.js`
- Create: `static/js/admin/users.js`
- Create: `static/js/admin/roles.js`
- Create: `static/js/admin/jobs.js`
- Create: `static/js/admin/deliveries.js`
- Create: `static/js/admin/audit.js`
- Modify: `templates/index.html`
- Modify: `tests/test_webgui_ui_changes.py`
- Test: `tests/test_role_page_content.py`

**Interfaces:**
- Consumes: role routes, base layout, existing APIs, and existing Alpine behavior.
- Produces: standalone role pages with no dependency on a global `currentTab`.

- [ ] **Step 1: Write failing template separation tests**

Assert each route template contains only its domain, scripts are external, and the legacy monolith no longer contains business sections:

```python
def test_role_templates_do_not_share_unrelated_workspaces():
    selection = Path("templates/selection/topics.html").read_text("utf-8")
    writing = Path("templates/writing/beats.html").read_text("utf-8")
    assert "今日爆款" in selection
    assert "单集节拍表" not in selection
    assert "单集节拍表" in writing
    assert "用户管理" not in writing

def test_legacy_index_is_only_a_compatibility_redirect():
    html = Path("templates/index.html").read_text("utf-8")
    assert "currentTab ===" not in html
    assert "创作项目" not in html
```

- [ ] **Step 2: Run tests and verify the monolith fails them**

Run: `uv run pytest tests/test_role_page_content.py -q`

Expected: FAIL because role templates and scripts do not exist.

- [ ] **Step 3: Extract shared authenticated fetch behavior**

Create `static/js/common/api.js` with one request helper that reads the CSRF token injected by `base.html`, sets `X-CSRF-Token` for writes, converts `401` to `/login`, and surfaces `403` as a permission message. Do not duplicate fetch wrappers in role files.

- [ ] **Step 4: Move selection pages without behavior regressions**

Move current rank, today-hit, topic-library, drama-search/download, transcript-preview, breakdown-generation, and breakdown-editor UI into selection templates and scripts. Replace tab changes with real links. Preserve current progress restoration, paid-item skipping, multi-episode download, dynamic breakdown field guide, and lifecycle labels.

- [ ] **Step 5: Move planning and writing pages**

Split project listing, directions/Brief, characters/relationships, outlines, and Beats into their routes. Each detail route reads `project_id` and `episode_number` from the URL or template context. Preserve autosized fields, save indicators, job progress, retry, pause, and version history display.

- [ ] **Step 6: Move review and admin operational pages**

Create review placeholders backed by real route context for tasks/approvals implemented in later tasks. Move crawler control, config editor, data management, analytics, machine jobs, and current stage exports under admin templates. Do not expose these fragments in non-admin HTML.

- [ ] **Step 7: Reduce index.html and update template tests**

Replace `templates/index.html` with a minimal compatibility page that redirects through `/`. Update old tests to inspect the new domain template or external JS file rather than expecting every string in one file.

- [ ] **Step 8: Run focused UI tests and commit**

Run: `uv run pytest tests/test_role_page_content.py tests/test_webgui_ui_changes.py -q`

Expected: PASS.

Commit:

```bash
git add templates static/js tests/test_role_page_content.py tests/test_webgui_ui_changes.py
git commit -m "refactor: split web gui into role workspaces"
```

---

