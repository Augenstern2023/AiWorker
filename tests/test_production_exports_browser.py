# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
MODAL = ROOT / "templates/common/workspace/production_export_modal.html"
CONTROLLER = ROOT / "static/js/common/production_export.js"


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        current = browser.new_page(viewport={"width": 1280, "height": 900})
        current.set_default_timeout(3_000)
        yield current
        browser.close()


def _bootstrap_stub() -> str:
    """A minimal Bootstrap Modal + nav-tabs substitute for headless testing."""
    return """
    window.bootstrap = {
      Modal: {
        getOrCreateInstance(el) {
          el.__instance = el.__instance || new Modal(el);
          return el.__instance;
        }
      }
    };
    function Modal(el) {
      this.el = el;
      this._closed = true;
      const self = this;
      this.show = function () {
        el.classList.add('show');
        el.style.display = 'block';
        el.setAttribute('aria-hidden', 'false');
        self._closed = false;
        el.dispatchEvent(new Event('shown.bs.modal'));
      };
      this.hide = function () {
        el.classList.remove('show');
        el.style.display = 'none';
        el.setAttribute('aria-hidden', 'true');
        self._closed = true;
        el.dispatchEvent(new Event('hidden.bs.modal'));
      };
    }
    window.addEventListener('load', function () {
      document.querySelectorAll('[data-bs-toggle="tab"]').forEach(function (tab) {
        tab.addEventListener('click', function () {
          var id = tab.getAttribute('data-bs-target');
          var group = tab.closest('.tab-content');
          tab.parentElement.parentElement.querySelectorAll('.nav-link').forEach(function (n) {
            n.classList.remove('active'); n.setAttribute('aria-selected', 'false');
          });
          tab.classList.add('active'); tab.setAttribute('aria-selected', 'true');
          group.querySelectorAll('.tab-pane').forEach(function (p) {
            p.classList.toggle('show', p === document.querySelector(id));
            p.classList.toggle('active', p === document.querySelector(id));
          });
        });
      });
      document.querySelectorAll('[data-bs-dismiss="modal"]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var el = document.getElementById('productionExportModal');
          if (el && el.__instance) el.__instance.hide();
        });
      });
    });
    """


def mount(page: Page, *, preview: dict, history: dict, export: dict | None = None) -> None:
    fixtures = json.dumps(
        {"preview": preview, "history": history, "export": export},
        ensure_ascii=False,
    )
    modal_html = MODAL.read_text(encoding="utf-8")
    page.set_content(f"<div class='container'>{modal_html}</div>")
    page.add_script_tag(
        content=f"""
        {_bootstrap_stub()}
        window.__fixtures = {fixtures};
        window.__requestLog = [];
        window.__pending = new Map();
        window.__cancelled = [];
        const __origAbort = AbortController.prototype.abort;
        AbortController.prototype.abort = function () {{
            window.__cancelled.push(true);
            return __origAbort.call(this);
        }};
        window.aiworkerApi = {{
          request(url, options = {{}}) {{
            window.__requestLog.push({{url, method: options.method || 'GET', body: options.body || null}});
            if (options && options.signal && options.signal.aborted) {{
              const err = new Error('Aborted'); err.name = 'AbortError';
              return Promise.reject(err);
            }}
            if (url.includes('/production-export/preview')) {{
              const parsed = new URL(url, 'http://test.local');
              const episodes = parsed.searchParams.getAll('episode_numbers').map(Number);
              const preview = JSON.parse(JSON.stringify(window.__fixtures.preview));
              if (episodes.length) {{
                const total = Number(preview.project.target_episode_count || episodes.length);
                preview.selection = {{
                  export_scope: episodes.length === total && episodes.every((n, i) => n === i + 1) ? 'full' : 'partial',
                  episode_numbers: episodes,
                  project_total_episodes: total,
                  exported_episode_count: episodes.length,
                }};
                preview.stages = (preview.stages || []).filter(
                  (stage) => !stage.episode_number || episodes.includes(Number(stage.episode_number))
                );
              }}
              const payload = {{ok: true, json: () => Promise.resolve(preview)}};
              return delay(payload);
            }}
            if (url.includes('/production-exports?')) return delay({{ok: true, json: () => Promise.resolve(window.__fixtures.history)}});
            if (url.endsWith('/production-exports') && options.method === 'POST') {{
              if (window.__fixtures.export === null) {{
                return delay({{ok: false, status: 409, json: () => Promise.resolve({{detail: '版本已变化，请重新检查并确认'}})}});
              }}
              return delay({{ok: true, status: 201, json: () => Promise.resolve({{export: window.__fixtures.export, created: true}})}});
            }}
            if (url.includes('/download')) {{
              return delay({{ok: true, headers: {{get: () => "attachment; filename*=UTF-8''%E5%AF%BB%E6%89%BE%E7%9C%9F%E7%9B%B8_%E9%A1%B9%E7%9B%AE%E6%96%87%E6%A1%A3%E5%8C%85_abcabcabcabc.zip"}}, blob: () => Promise.resolve(new Blob(['PK'], {{type: 'application/zip'}}))}});
            }}
            return delay({{ok: false, status: 500, json: () => Promise.resolve({{}})}});
          }}
        }};
        function delay(payload) {{
          return new Promise((resolve) => setTimeout(() => resolve(payload), 25));
        }}
        window.aiworkerApi.isAbortError = (e) => e && e.name === 'AbortError';
        window.aiworkerApi.parseErrorDetail = async (r) => (r && r.detail) || '';
        window.aiworkerUi = {{
          requestKey(key, factory) {{
            const controller = new AbortController();
            window.__pending.set(key, controller);
            return factory(controller.signal).finally(() => {{
              if (window.__pending.get(key) === controller) window.__pending.delete(key);
            }});
          }},
          cancelRequest(key) {{
            const controller = window.__pending.get(key);
            if (controller) {{ controller.abort(); window.__pending.delete(key); }}
          }},
          setButtonLoading(button, opts) {{
            button.disabled = opts.loading;
            button.textContent = opts.loading ? (opts.label || '处理中…') : '生成项目文档包';
          }},
          showToast(opts) {{ window.__lastToast = opts; }},
          announce() {{}},
        }};
        window.aiworkerProductionExport = {{ open() {{}} }};
        """
    )
    page.add_script_tag(path=CONTROLLER)


def _stage(state="ready", number=None):
    return {"stage": "script", "episode_number": number, "state": state,
            "versions": [{"id": 7, "version": 2}], "message": "检查通过"}


def test_open_show_check_and_generate(page: Page) -> None:
    """Generate button is only enabled when all checks pass; download fires after."""
    preview = {"project": {"id": "3", "name": "寻找真相", "target_episode_count": 2},
               "ready": True, "fingerprint": "abc" * 8,
               "stages": [
                   {"stage": "breakdown", "episode_number": None,
                    "state": "ready", "versions": [{"id": 4, "version": 2}],
                    "message": "检查通过"},
                   _stage(),
               ], "issues": []}
    history = {"items": [], "total": 0, "page": 1, "page_size": 20}
    export = {"id": "e1", "project_id": "3", "fingerprint": preview["fingerprint"],
              "contract_version": "2.3", "artifact_format": "zip", "media_type": "application/zip",
              "created_by": "owner", "created_by_name": "负责人",
              "created_at": 1, "episode_count": 2, "size_bytes": 100,
              "file_sha256": "deadbeef", "audit_status": "recorded",
              "download_url": "/api/creative/production-exports/e1/download"}
    mount(page, preview=preview, history=history, export=export)

    page.evaluate("""projectId => window.aiworkerProductionExport.open(projectId, {projectName: '寻找真相'})""", "3")

    modal = page.locator("#productionExportModal")
    modal.wait_for(state="visible")
    assert modal.get_attribute("aria-hidden") == "false"
    assert page.locator("#production-export-project-name").inner_text() == "寻找真相"
    page.wait_for_function(
        "() => document.querySelector('#production-export-check-status').textContent.includes('全部检查通过')"
    )
    assert page.locator("#production-export-check-status").inner_text() == "全部检查通过，可生成锁定版本的项目文档包。"
    assert "剧目拆解" in page.locator("#production-export-stages").inner_text()

    generate = page.locator("#production-export-generate")
    assert not generate.is_disabled()
    generate.click()
    page.wait_for_function("() => !document.querySelector('#production-export-result').classList.contains('d-none')")
    result_text = page.locator("#production-export-result").inner_text()
    assert "项目文档包已生成并锁定版本" in result_text
    assert "仅导出创作资料，尚未开始生产" in result_text
    # The result panel shows the fingerprint's leading 12 characters.
    assert "abcabcabcabc" in result_text
    assert page.locator("#production-export-result a").get_attribute("href") == export["download_url"]


def test_custom_episode_selection_is_rechecked_and_sent_to_create(page: Page) -> None:
    stages = [
        _stage(number=number)
        for number in (1, 2, 3)
        for _ in ("outline", "beats", "script")
    ]
    for index, stage in enumerate(stages):
        stage["stage"] = ("outline", "beats", "script")[index % 3]
    preview = {
        "project": {"id": "3", "name": "寻找真相", "target_episode_count": 3},
        "ready": True,
        "fingerprint": "abc" * 8,
        "stages": stages,
        "issues": [],
    }
    history = {"items": [], "total": 0, "page": 1, "page_size": 20}
    exported = {
        "id": "e-partial", "project_id": "3", "fingerprint": preview["fingerprint"],
        "contract_version": "1.0", "created_by": "owner", "created_by_name": "负责人",
        "created_at": 1, "episode_count": 2, "episode_numbers": [1, 3],
        "export_scope": "partial", "project_total_episodes": 3,
        "workflow_mode": "free", "size_bytes": 100, "file_sha256": "deadbeef",
        "audit_status": "recorded",
        "download_url": "/api/creative/production-exports/e-partial/download",
    }
    mount(page, preview=preview, history=history, export=exported)
    page.evaluate(
        "() => window.aiworkerProductionExport.open('3', {projectName: '寻找真相'})"
    )
    page.wait_for_function(
        "() => document.querySelector('#production-export-check-status').textContent.includes('全部检查通过')"
    )

    page.locator("#production-export-scope").select_option("custom")
    page.locator("#production-export-custom-episodes").fill("1,3")
    assert page.locator("#production-export-generate").is_disabled()
    page.locator("#production-export-scope-apply").click()
    page.wait_for_function(
        "() => window.__requestLog.some(item => item.url.includes('episode_numbers=1') && item.url.includes('episode_numbers=3'))"
    )
    page.locator("#production-export-generate").click()
    page.wait_for_function(
        "() => window.__requestLog.some(item => item.method === 'POST' && item.url.endsWith('/production-exports'))"
    )
    body = page.evaluate(
        "() => JSON.parse(window.__requestLog.findLast(item => item.method === 'POST').body)"
    )
    assert body["episode_numbers"] == [1, 3]


def test_available_selection_uses_only_fully_ready_episodes(page: Page) -> None:
    stages = []
    for number in (1, 2, 3):
        for stage_name in ("outline", "beats", "script"):
            stage = _stage(number=number)
            stage["stage"] = stage_name
            if number == 2 and stage_name == "script":
                stage["state"] = "invalid"
            stages.append(stage)
    preview = {
        "project": {"id": "3", "name": "寻找真相", "target_episode_count": 3},
        "ready": False,
        "fingerprint": None,
        "stages": stages,
        "issues": [],
    }
    mount(
        page,
        preview=preview,
        history={"items": [], "total": 0, "page": 1, "page_size": 20},
        export=None,
    )
    page.evaluate(
        "() => window.aiworkerProductionExport.open('3', {projectName: '寻找真相'})"
    )
    page.wait_for_function(
        "() => document.querySelector('#production-export-available-summary').textContent.includes('第 1、3 集')"
    )
    page.locator("#production-export-scope").select_option("available")
    page.locator("#production-export-scope-apply").click()
    page.wait_for_function(
        "() => window.__requestLog.some(item => item.url.includes('episode_numbers=1') && item.url.includes('episode_numbers=3'))"
    )


def test_blocked_issues_disable_generate_and_link_to_edit(page: Page) -> None:
    """When checks block, the generate button stays disabled and issues link out."""
    preview = {"project": {"id": "3", "name": "寻找真相", "target_episode_count": 2},
               "ready": False, "fingerprint": None, "stages": [_stage()],
               "issues": [{"stage": "script", "episode_number": 1, "state": "invalid",
                           "message": "缺少完整的锁定版本，请完成并锁定",
                           "code": "missing_locked_input",
                           "edit_url": "/writing/projects/3/scripts"}]}
    history = {"items": [], "total": 0, "page": 1, "page_size": 20}
    mount(page, preview=preview, history=history, export=None)

    page.evaluate("() => window.aiworkerProductionExport.open('3', {projectName: '寻找真相'})")
    page.wait_for_function(
        "() => document.querySelector('#production-export-check-status').textContent.includes('阻塞项')"
    )
    assert page.locator("#production-export-check-status").inner_text() == "存在阻塞项，请先在对应页面完成并锁定内容。"
    assert page.locator("#production-export-generate").is_disabled()
    issue = page.locator("#production-export-issues .alert").first
    assert "缺少完整的锁定版本" in issue.inner_text()
    assert issue.locator("a").get_attribute("href") == "/writing/projects/3/scripts"


def test_history_tab_tags_versions_and_pending_blocks_download(page: Page) -> None:
    """History shows current/historical tags; pending-audit records cannot download."""
    current = "abc" * 8
    preview = {"project": {"id": "3", "name": "寻找真相", "target_episode_count": 2},
               "ready": True, "fingerprint": current, "stages": [_stage()], "issues": []}
    history = {"total": 2, "page": 1, "page_size": 20, "items": [
        {"id": "e2", "project_id": "3", "fingerprint": current, "contract_version": "1.0",
         "created_by": "owner", "created_by_name": "负责人", "created_at": 2,
         "episode_count": 2, "size_bytes": 90, "file_sha256": "aa", "audit_status": "recorded",
         "download_url": "/api/creative/production-exports/e2/download"},
        {"id": "e1", "project_id": "3", "fingerprint": "old" * 8, "contract_version": "1.0",
         "created_by": "admin", "created_by_name": "管理员", "created_at": 1,
         "episode_count": 2, "size_bytes": 80, "file_sha256": "bb", "audit_status": "pending",
         "download_url": "/api/creative/production-exports/e1/download"}]}
    mount(page, preview=preview, history=history, export=None)

    page.evaluate("() => window.aiworkerProductionExport.open('3', {projectName: '寻找真相'})")
    page.locator("#production-export-tab-history").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#production-export-history-list tr').length > 0"
    )

    rows = page.locator("#production-export-history-list tr")
    assert rows.count() == 2
    assert "当前版本" in rows.nth(0).inner_text()
    assert "历史版本" in rows.nth(1).inner_text()
    assert rows.nth(1).locator("button").is_disabled()
    assert "审计待补记" in rows.nth(1).inner_text()


def test_custom_version_controls_filter_and_send_the_confirmed_manifest(page: Page) -> None:
    manifest = {
        "brief": {"resource_ref": "11", "version": 2},
        "characters": {"resource_ref": "cast-2", "version": 2},
        "outline": {"resource_ref": "outline-2", "version": 2},
        "episodes": {
            "1": {
                "outline": {"resource_ref": "21", "version": 2},
                "beats": {"resource_ref": "31", "version": 3},
                "script": {"resource_ref": "41", "version": 4},
            }
        },
    }
    preview = {
        "project": {"id": "3", "name": "寻找真相", "target_episode_count": 1},
        "ready": True, "fingerprint": "abc" * 8,
        "stages": [_stage(number=1)], "issues": [],
        "version_selection": {
            "strategy": "current", "manifest": manifest,
            "warnings": ["自由创作的自定义版本请确认一致性"],
            "candidates": {
                "brief": [
                    {"resource_ref": "11", "version": 2, "label": "v2 · 11", "compatible": True, "selected": True},
                    {"resource_ref": "10", "version": 1, "label": "v1 · 10", "compatible": True, "selected": False},
                ],
                "characters": [
                    {"resource_ref": "cast-2", "version": 2, "label": "v2 · cast-2", "compatible": True, "selected": True},
                    {"resource_ref": "cast-bad", "version": 1, "label": "v1 · cast-bad", "compatible": False, "selected": False, "disabled_reason": "与上游不兼容"},
                ],
                "outline": [
                    {"resource_ref": "outline-2", "version": 2, "label": "v2 · outline-2", "compatible": True, "selected": True},
                ],
                "episodes": {"1": {
                    "beats": [{"resource_ref": "31", "version": 3, "label": "v3 · 31", "compatible": True, "selected": True}],
                    "script": [{"resource_ref": "41", "version": 4, "label": "v4 · 41", "compatible": True, "selected": True}],
                }},
            },
        },
    }
    exported = {
        "id": "e-custom", "project_id": "3", "fingerprint": preview["fingerprint"],
        "contract_version": "1.0", "created_by": "owner", "created_by_name": "负责人",
        "created_at": 1, "episode_count": 1, "episode_numbers": [1],
        "export_scope": "full", "project_total_episodes": 1,
        "workflow_mode": "free", "version_strategy": "custom",
        "version_manifest": manifest, "size_bytes": 100, "file_sha256": "deadbeef",
        "audit_status": "recorded",
        "download_url": "/api/creative/production-exports/e-custom/download",
    }
    mount(page, preview=preview, history={"items": [], "total": 0}, export=exported)
    page.evaluate("() => window.aiworkerProductionExport.open('3', {projectName: '寻找真相'})")
    page.wait_for_function(
        "() => document.querySelector('#production-export-check-status').textContent.includes('全部检查通过')"
    )

    page.locator("#production-export-version-strategy").select_option("custom")
    page.wait_for_function(
        "() => window.__requestLog.some(item => item.method === 'POST' && item.url.includes('/production-export/preview'))"
    )
    assert page.locator("#production-export-version-controls").is_visible()
    assert page.locator("#production-export-version-characters option").nth(1).is_disabled()
    assert page.locator("#production-export-version-brief").input_value() == "11"
    assert page.locator("#production-export-version-brief option:checked").inner_text() == (
        "第 2 版（当前选择）"
    )
    assert "cast-2" not in page.locator(
        "#production-export-version-characters"
    ).inner_text()
    assert "第 2 版" in page.locator("#production-export-stages").inner_text()
    assert "#7" not in page.locator("#production-export-stages").inner_text()
    assert "自由创作" in page.locator("#production-export-version-warning").inner_text()

    page.locator("#production-export-generate").click()
    page.wait_for_function(
        "() => window.__requestLog.some(item => item.method === 'POST' && item.url.endsWith('/production-exports'))"
    )
    body = page.evaluate(
        "() => JSON.parse(window.__requestLog.findLast(item => item.url.endsWith('/production-exports')).body)"
    )
    assert body["version_strategy"] == "custom"
    assert body["version_manifest"] == manifest


def test_history_version_combination_can_be_reused(page: Page) -> None:
    manifest = {
        "brief": {"resource_ref": "1", "version": 1},
        "characters": {"resource_ref": "cast", "version": 1},
        "outline": {"resource_ref": "outline", "version": 1},
        "episodes": {},
    }
    preview = {
        "project": {"id": "3", "name": "寻找真相", "target_episode_count": 2},
        "ready": True, "fingerprint": "abc" * 8, "stages": [_stage()], "issues": [],
        "version_selection": {"strategy": "current", "manifest": manifest, "candidates": {}, "warnings": []},
    }
    record = {
        "id": "e1", "project_id": "3", "fingerprint": "old" * 8,
        "contract_version": "1.0", "created_by": "owner", "created_by_name": "负责人",
        "created_at": 1, "episode_count": 1, "episode_numbers": [2],
        "export_scope": "partial", "project_total_episodes": 2,
        "workflow_mode": "sequential", "version_strategy": "custom",
        "version_manifest": manifest, "size_bytes": 80, "file_sha256": "bb",
        "audit_status": "recorded", "download_url": "/download",
    }
    mount(page, preview=preview, history={"items": [record], "total": 1}, export=None)
    page.evaluate("() => window.aiworkerProductionExport.open('3', {projectName: '寻找真相'})")
    page.locator("#production-export-tab-history").click()
    page.get_by_role("button", name="复用版本").click()
    page.wait_for_function(
        "() => window.__requestLog.some(item => item.method === 'POST' && item.url.includes('/production-export/preview'))"
    )
    assert page.locator("#production-export-version-strategy").input_value() == "custom"
    body = page.evaluate(
        "() => JSON.parse(window.__requestLog.findLast(item => item.url.includes('/production-export/preview')).body)"
    )
    assert body["episode_numbers"] == [2]
    assert body["version_manifest"] == manifest


def test_switching_project_cancels_stale_requests(page: Page) -> None:
    """Opening another project aborts the previous project's in-flight requests."""
    preview = {"project": {"id": "3", "name": "寻找真相", "target_episode_count": 2},
               "ready": True, "fingerprint": "abc" * 8, "stages": [_stage()], "issues": []}
    history = {"items": [], "total": 0, "page": 1, "page_size": 20}
    mount(page, preview=preview, history=history, export=None)

    page.evaluate("() => window.aiworkerProductionExport.open('3', {projectName: '寻找真相'})")
    # Now open a different project; the stale preview/history keys should be cancelled.
    page.evaluate("""() => {
        window.__cancelled = [];
        const origAbort = AbortController.prototype.abort;
        AbortController.prototype.abort = function () {
            window.__cancelled.push(true);
            return origAbort.call(this);
        };
        window.aiworkerProductionExport.open('9', {projectName: '另一个'});
    }""")
    # The controller for project 3 preview/history/create should have been aborted.
    assert page.evaluate("() => (window.__cancelled || []).length >= 1")


def test_download_uses_server_fingerprint_name(page: Page) -> None:
    """The download filename comes from content-disposition when present."""
    current = "abc" * 8
    preview = {"project": {"id": "3", "name": "寻找真相", "target_episode_count": 2},
               "ready": True, "fingerprint": current, "stages": [_stage()], "issues": []}
    history = {"total": 1, "page": 1, "page_size": 20, "items": [
        {"id": "e1", "project_id": "3", "fingerprint": current, "contract_version": "1.0",
         "created_by": "owner", "created_by_name": "负责人", "created_at": 2,
         "episode_count": 2, "size_bytes": 90, "file_sha256": "aa", "audit_status": "recorded",
         "download_url": "/api/creative/production-exports/e1/download"}]}
    mount(page, preview=preview, history=history, export=None)

    # Stub URL.createObjectURL / anchor.click to capture the download name.
    page.evaluate("""
        window.__downloads = [];
        const origCreate = URL.createObjectURL.bind(URL);
        URL.createObjectURL = function (blob) {
            window.__downloads.push({blobType: blob.type, name: null});
            return 'blob:fake';
        };
        HTMLAnchorElement.prototype.click = function () {
            const last = window.__downloads[window.__downloads.length - 1];
            if (last && this.download) last.name = this.download;
        };
    """)
    page.evaluate("() => window.aiworkerProductionExport.open('3', {projectName: '寻找真相'})")
    page.locator("#production-export-tab-history").click()
    page.locator("#production-export-history-list button").first.click()
    page.wait_for_function("() => (window.__downloads || []).length >= 1")
    name = page.evaluate("() => window.__downloads[0].name")
    assert name.endswith(".zip")
    assert "寻找真相" in name
