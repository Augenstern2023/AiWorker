# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Browser coverage for Hongguo provider selection and settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ALPINE_SCRIPT = ROOT / "static/js/alpine.min.js"
BOOTSTRAP_STYLE = ROOT / "static/css/bootstrap.min.css"
WORKSPACE_STYLE = ROOT / "static/css/workspaces.css"
DRAMA_SCRIPT = ROOT / "static/js/selection/dramas.js"
ADMIN_SCRIPT = ROOT / "static/js/admin/workspace.js"
DRAMA_TEMPLATE = ROOT / "templates/common/workspace/selection_dramas.html"
ADMIN_TEMPLATE = ROOT / "templates/admin/config.html"


def _mount_drama_page(page: Page) -> None:
    template = DRAMA_TEMPLATE.read_text(encoding="utf-8")
    page.set_content(f'<main x-data="appData()">{template}</main>')
    page.add_style_tag(path=BOOTSTRAP_STYLE)
    page.add_style_tag(path=WORKSPACE_STYLE)
    page.evaluate("window.aiworkerModules = []")
    page.add_script_tag(path=DRAMA_SCRIPT)
    page.evaluate(
        """
        () => {
          const candidate = {
            provider_id: 'hm', source_platform: 'hm',
            series_ref: 'hm-series-1', book_id: 'hm-series-1',
            title: '目标短剧', episode_count: 2, summary: '授权测试剧目'
          };
          window.requests = [];
          window.alert = () => {};
          window.aiworkerApi = {
            async request(url, options = {}) {
              const body = options.body ? JSON.parse(options.body) : null;
              window.requests.push({url, method: options.method || 'GET', body});
              let payload = {};
              if (url.endsWith('/download/search')) payload = {
                source: 'hongguo_providers', candidates: [candidate],
                providers: [{
                  provider_id: 'hm', display_name: '海马剧场', status: 'success',
                  candidates: [candidate]
                }], capture_candidates: []
              };
              else if (url.endsWith('/episodes')) payload = {
                episodes: [{
                  provider_id: 'hm', series_ref: 'hm-series-1',
                  episode_ref: 'hm-episode-1', episode: 1,
                  title: '第1集', is_paid: false
                }]
              };
              else if (url.endsWith('/download/jobs')) payload = {
                topic: {id: 9, title: '目标短剧', status: 'adopted'},
                job: {job_id: 'job-1'}
              };
              return {ok: true, async json() { return payload; }};
            }
          };
          const module = window.aiworkerModules.at(-1)();
          window.appData = () => Object.assign({
            dramaAnalysis: {
              title: '', platform: 'hg', candidates: [], providers: [],
              captureCandidates: [], thirdPartyError: '', captureError: '',
              selected: null, episodes: [], episode: 0, searchSource: '',
              fallbackReason: '', loading: false, error: '', jobId: '',
              notifiedStatus: ''
            },
            dramaReviews: {
              status: 'idle', title: '', platform: 'hg', limit: 100,
              allReviews: false, loading: false, polling: false, error: '',
              jobId: '', reviewCount: 0, sourceTotal: 0, items: []
            },
            jobs: [], selectedJobId: '',
            async fetchJobs() {}, async fetchLogs() {}, downloadFile() {}
          }, module);
        }
        """
    )
    page.add_script_tag(path=ALPINE_SCRIPT)


def _mount_admin_status_page(page: Page, response_payload: dict) -> None:
    template = ADMIN_TEMPLATE.read_text(encoding="utf-8")
    content = template.split("{% block content %}", 1)[1].split("{% include", 1)[0]
    page.set_content(f'<main class="container-fluid" x-data="appData()">{content}</main>')
    page.add_style_tag(path=BOOTSTRAP_STYLE)
    page.evaluate("window.aiworkerModules = []")
    page.add_script_tag(path=ADMIN_SCRIPT)
    page.evaluate(
        """
        (payload) => {
          window.resolveInterfaceCheck = null;
          window.aiworkerApi = {
            async request(url, options = {}) {
              if (url !== '/api/hongguo/drama/test') {
                return {ok: true, async json() { return {}; }};
              }
              await new Promise(resolve => { window.resolveInterfaceCheck = resolve; });
              return {ok: true, async json() { return payload; }};
            }
          };
          const module = window.aiworkerModules.at(-1)();
          window.appData = () => Object.assign({
            dramaAnalysis: {
              loading: false, interfaceTesting: false, error: '', interfaceTest: null,
              settings: {
                config_url: 'https://config.example.test', activation_code: '',
                has_activation_code: false, capture_root: 'E:\\\\hg\\\\raw\\\\video_urls',
                capture_service_url: 'http://127.0.0.1:8776', wait_timeout_seconds: 600
              }
            }
          }, module);
        }
        """,
        response_payload,
    )
    page.add_script_tag(path=ALPINE_SCRIPT)


@pytest.mark.parametrize(
    ("status", "alert_class"),
    [
        ("success", "alert-success"),
        ("partial", "alert-warning"),
        ("error", "alert-danger"),
    ],
)
def test_admin_source_check_renders_summary_styles_and_text_badges(
    status: str, alert_class: str
) -> None:
    payload = {
        "status": status,
        "message": "汇总结果",
        "checks": [
            {
                "id": "capture_directory",
                "label": "抓包目录",
                "status": "success",
                "message": "目录可用",
            },
            {
                "id": "capture_service",
                "label": "8776 视频抓包服务",
                "status": "error",
                "message": "服务不可用",
            },
            {
                "id": "third_party",
                "label": "第三方剧目来源",
                "status": "partial",
                "message": "6/7 个来源可用；异常来源：HM 源",
            },
        ],
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(3_000)
        _mount_admin_status_page(page, payload)

        button = page.locator("#hongguo-interface-check")
        button.click()
        page.wait_for_function("window.resolveInterfaceCheck !== null")
        assert button.is_disabled()
        assert page.get_by_text("检查中…").is_visible()
        page.evaluate("window.resolveInterfaceCheck()")
        page.get_by_text("汇总结果").wait_for()

        assert alert_class in page.get_by_text("汇总结果").get_attribute("class")
        assert page.get_by_text("正常", exact=True).count() == 1
        assert page.get_by_text("部分异常", exact=True).count() == 1
        assert page.get_by_text("异常", exact=True).count() == 1
        assert page.get_by_text("6/7 个来源可用；异常来源：HM 源").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        browser.close()


def test_drama_search_loading_state_is_hidden_before_alpine_initializes() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        template = DRAMA_TEMPLATE.read_text(encoding="utf-8")
        page.set_content(
            f"<style>[x-cloak] {{ display: none !important; }}</style>{template}"
        )

        assert not page.get_by_text("正在检索剧目来源…").is_visible()
        browser.close()


def test_inactive_drama_search_states_stay_hidden_after_alpine_initializes() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_drama_page(page)

        assert not page.get_by_text("正在检索剧目来源…").is_visible()
        assert not page.locator('[x-show="dramaReviews.jobId"]').is_visible()
        browser.close()


def test_hongguo_candidate_requires_click_and_keeps_provider_identity() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(3_000)
        _mount_drama_page(page)

        page.get_by_placeholder("输入完整剧名，优先检索第三方剧目来源").fill(
            "目标短剧"
        )
        page.get_by_role("button", name="搜索").click()
        page.wait_for_function("window.requests.length === 1")
        assert [item["url"] for item in page.evaluate("window.requests")] == [
            "/api/hongguo/drama/download/search"
        ]

        candidate_button = page.locator(
            'section[aria-label="海马剧场 搜索结果"] button'
        )
        assert candidate_button.count() == 1, page.locator("button").all_inner_texts()
        candidate_button.click()
        page.wait_for_function("window.requests.length === 2")
        episodes_request = page.evaluate("window.requests[1]")
        assert episodes_request["url"] == "/api/hongguo/drama/episodes"
        assert episodes_request["body"]["provider_id"] == "hm"
        assert episodes_request["body"]["series_ref"] == "hm-series-1"

        page.get_by_role("button", name="下载剧集").click()
        page.wait_for_function("window.requests.length === 3")
        job_request = page.evaluate("window.requests[2]")
        assert job_request["url"] == "/api/hongguo/drama/download/jobs"
        assert job_request["body"]["provider_id"] == "hm"
        assert job_request["body"]["series_ref"] == "hm-series-1"
        assert "topic_id" not in job_request["body"]
        assert "workflow_task_id" not in job_request["body"]
        browser.close()


def test_completed_drama_video_artifact_opens_inline_preview() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(3_000)
        _mount_drama_page(page)
        baseline_scroll_width = page.evaluate("document.documentElement.scrollWidth")
        page.evaluate(
            """
            () => {
              const state = document.querySelector('main')._x_dataStack[0];
              state.dramaAnalysis.jobId = 'job-video';
              state.jobs = [{
                job_id: 'job-video', status: 'succeeded', message: '下载完成',
                artifacts: [{
                  path: 'data/hg/jobs/job-video/dramas/目标短剧/episode_01/video.mp4',
                  artifact_type: 'video'
                }]
              }];
            }
            """
        )

        page.get_by_role("button", name="video.mp4").click()

        player = page.locator("#dramaAnalysisVideo")
        player.wait_for(state="visible")
        assert player.get_attribute("src") == (
            "/api/file/preview?path="
            "data%2Fhg%2Fjobs%2Fjob-video%2Fdramas%2F%E7%9B%AE%E6%A0%87%E7%9F%AD%E5%89%A7%2F"
            "episode_01%2Fvideo.mp4"
        )
        assert page.evaluate("document.documentElement.scrollWidth") <= baseline_scroll_width
        browser.close()


def test_drama_page_versions_search_scripts_to_invalidate_browser_cache() -> None:
    environment = Environment(loader=FileSystemLoader(ROOT / "templates"))
    html = environment.get_template("selection/dramas.html").render(
        principal=None,
        asset_version="drama-state-v2",
        navigation=[],
        page_title="剧目检索下载",
        workspace_page="selection-dramas",
        workspace_runtime=False,
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        script_sources = page.locator("script[src]").evaluate_all(
            "elements => elements.map(element => element.getAttribute('src'))"
        )
        assert "/static/js/common/creative.js?v=drama-state-v2" in script_sources
        assert "/static/js/selection/dramas.js?v=drama-state-v2" in script_sources
        browser.close()


def test_new_search_invalidates_pending_episode_request_and_hides_old_selection() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(3_000)
        _mount_drama_page(page)
        page.evaluate(
            """
            () => {
              const oldCandidate = {
                provider_id: 'hm', source_platform: 'hm',
                series_ref: 'old-series', book_id: 'old-series',
                title: '旧剧目', episode_count: 1
              };
              const newCandidate = {
                provider_id: 'hg', source_platform: 'hg',
                series_ref: 'new-series', book_id: 'new-series',
                title: '新剧目', episode_count: 2
              };
              window.requests = [];
              window.resolveOldEpisodes = null;
              window.resolveNewSearch = null;
              let searchCount = 0;
              window.aiworkerApi.request = async (url, options = {}) => {
                const body = options.body ? JSON.parse(options.body) : null;
                window.requests.push({url, method: options.method || 'GET', body});
                if (url.endsWith('/download/search')) {
                  searchCount += 1;
                  if (searchCount === 1) return {
                    ok: true,
                    async json() { return {
                      status: 'success', source: 'hongguo_providers',
                      candidates: [oldCandidate], capture_candidates: [],
                      providers: [{
                        provider_id: 'hm', display_name: 'HM 源',
                        status: 'available', candidates: [oldCandidate]
                      }]
                    }; }
                  };
                  return await new Promise(resolve => {
                    window.resolveNewSearch = () => resolve({
                      ok: true,
                      async json() { return {
                        status: 'success', source: 'hongguo_providers',
                        candidates: [newCandidate], capture_candidates: [],
                        providers: [{
                          provider_id: 'hg', display_name: '红果源',
                          status: 'available', candidates: [newCandidate]
                        }]
                      }; }
                    });
                  });
                }
                if (url.endsWith('/episodes')) return await new Promise(resolve => {
                  window.resolveOldEpisodes = () => resolve({
                    ok: true,
                    async json() { return {status: 'success', episodes: [{
                      provider_id: 'hm', series_ref: 'old-series',
                      episode_ref: 'old-episode', episode: 1,
                      title: '旧剧目第1集', is_paid: false
                    }]}; }
                  });
                });
                return {ok: true, async json() { return {}; }};
              };
            }
            """
        )

        search_input = page.get_by_placeholder(
            "输入完整剧名，优先检索第三方剧目来源"
        )
        search_input.fill("旧剧目")
        page.get_by_role("button", name="搜索").click()
        page.get_by_text("旧剧目", exact=True).first.wait_for()
        page.get_by_text("旧剧目", exact=True).first.click()
        page.wait_for_function("window.resolveOldEpisodes !== null")

        search_input.fill("新剧目")
        search_input.press("Enter")
        page.wait_for_function("window.resolveNewSearch !== null")
        assert not page.locator('[x-show="dramaAnalysis.selected"]').is_visible()
        assert page.get_by_text("正在检索剧目来源…").is_visible()

        page.evaluate("window.resolveOldEpisodes()")
        page.wait_for_timeout(50)
        assert page.get_by_text("正在检索剧目来源…").is_visible()
        assert page.locator("main").evaluate(
            "element => element._x_dataStack[0].dramaAnalysis.episodes.length"
        ) == 0

        page.evaluate("window.resolveNewSearch()")
        page.get_by_text("新剧目", exact=True).first.wait_for()
        assert not page.locator('[x-show="dramaAnalysis.selected"]').is_visible()
        browser.close()


def test_settings_keep_saved_activation_code_when_password_is_left_blank() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(3_000)
        page.set_content(
            """
            <main x-data="appData()">
              <button id="load" @click="fetchDramaSettings">加载</button>
              <input id="activation" x-model="dramaAnalysis.settings.activation_code">
              <button id="save" @click="saveDramaSettings">保存</button>
            </main>
            """
        )
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=ADMIN_SCRIPT)
        page.evaluate(
            """
            () => {
              window.alert = () => {};
              window.requests = [];
              window.aiworkerApi = {
                async request(url, options = {}) {
                  const body = options.body ? JSON.parse(options.body) : null;
                  window.requests.push({url, method: options.method || 'GET', body});
                  const payload = {
                    config_url: 'https://config.example.test',
                    has_activation_code: true,
                    capture_root: 'E:\\\\hg\\\\raw\\\\video_urls',
                    capture_service_url: 'http://127.0.0.1:8776',
                    wait_timeout_seconds: 600
                  };
                  return {ok: true, async json() { return payload; }};
                }
              };
              const module = window.aiworkerModules.at(-1)();
              window.appData = () => Object.assign({
                dramaAnalysis: {
                  loading: false, error: '', interfaceTest: null,
                  settings: {
                    config_url: '', activation_code: '', has_activation_code: false,
                    capture_root: '', capture_service_url: '', wait_timeout_seconds: 600
                  }
                }
              }, module);
            }
            """
        )
        page.add_script_tag(path=ALPINE_SCRIPT)

        page.locator("#load").click()
        page.wait_for_function("window.requests.length === 1")
        assert page.locator("#activation").input_value() == ""

        page.locator("#save").click()
        page.wait_for_function("window.requests.length === 2")
        save_request = page.evaluate("window.requests[1]")
        assert save_request["method"] == "PUT"
        assert "activation_code" not in save_request["body"]
        browser.close()
