### Task 5: Role-Specific Page Router and Navigation

**Files:**
- Create: `web/__init__.py`
- Create: `web/navigation_service.py`
- Create: `web/page_router.py`
- Create: `templates/layouts/base.html`
- Create: `templates/common/403.html`
- Create: `templates/common/tasks.html`
- Modify: `WebGUI.py`
- Test: `tests/test_page_authorization.py`

**Interfaces:**
- Consumes: `require_page`, `Principal`, and Jinja2 templates.
- Produces: `build_page_router(templates, navigation_service) -> APIRouter`.
- Produces: `NavigationService.items_for(principal) -> list[dict]`.

- [ ] **Step 1: Write failing page visibility and direct-access tests**

Parameterize the five default roles and assert exact route visibility. Include these checks:

```python
def test_screenwriter_cannot_open_selection_page(writer_client):
    response = writer_client.get("/selection/ranks", follow_redirects=False)
    assert response.status_code == 403
    assert "今日榜单" not in writer_client.get("/writing/projects").text

def test_system_admin_sees_all_navigation(admin_client):
    html = admin_client.get("/admin/users").text
    for label in ("选品工作台", "创意策划", "编剧工作台", "审批中心", "系统管理"):
        assert label in html
```

- [ ] **Step 2: Run tests and verify all content still comes from index.html**

Run: `uv run pytest tests/test_page_authorization.py -q`

Expected: FAIL because role page routes and navigation do not exist.

- [ ] **Step 3: Implement the navigation catalog**

Map each navigation group to one required page permission. System administrators receive every item; other principals receive only matching items. Keep route labels and icons in Python so templates do not infer permissions independently.

- [ ] **Step 4: Implement base layout and page guards**

`base.html` must render current user, allowed navigation, logout, a content block, Bootstrap assets, and per-page script blocks. Add explicit `403` handling. `/` redirects to the last still-authorized route stored in session, otherwise to role priority: selection, planning, writing, review, admin.

- [ ] **Step 5: Register page routing in WebGUI.py**

Mount `/static`, configure `Jinja2Templates`, include identity and page routers, and change the legacy `/` handler to the permission-derived redirect. Do not remove business APIs in this task.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_page_authorization.py tests/test_identity_auth.py -q`

Expected: PASS.

Commit:

```bash
git add web templates/layouts templates/common WebGUI.py tests/test_page_authorization.py
git commit -m "feat: add role-specific page routing"
```

---

