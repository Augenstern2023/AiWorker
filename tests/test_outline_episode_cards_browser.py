# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Browser coverage for the episode-outline card workspace."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]

OUTLINE_ITEMS = [
    {"episode_number": 1, "content": {"core_event": "密室账本曝光"}},
    {"episode_number": 2, "content": {"core_event": "董事会逼宫"}},
    {"episode_number": 12, "content": {"core_event": "终局反击"}},
]

SERIES_OVERVIEW = {
    "core_event": "林澈接手濒临倒闭的旧厂，在七天内追查假账并守住全厂生计",
    "character_progress": "林澈从逃避家族责任成长为主动承担代价的团队领袖",
    "conflict": "资金断裂、董事会逼宫与幕后资本围猎三线并进",
    "reversal": "真正操盘者是父亲最信任的旧合伙人",
    "highlight": "旧样品现场反证、停产夜抢单与全厂直播自救",
    "cliffhanger": "海外订单指向父亲失踪背后的另一桩旧案",
}

CARD_OUTLINE_ITEMS = [
    {
        "id": 101,
        "project_id": 8,
        "batch_id": "outlines-v3",
        "version": 3,
        "episode_number": 1,
        "stage": "outline",
        "status": "draft",
        "content": {
            "series_overview": SERIES_OVERVIEW,
            "core_event": "密室账本曝光，林澈发现旧厂财务记录被人系统性篡改",
            "conflict": "董事会要求立即停产，林澈坚持继续追查",
            "character_progress": "林澈从被动接手转为主动承担",
            "highlight": "林澈用废弃仓库中的旧样品现场反证",
            "reversal": "匿名举报人正是财务主管",
            "cliffhanger": "仓库监控拍到一个本应离职的人",
        },
    },
    {
        "id": 102,
        "project_id": 8,
        "batch_id": "outlines-v3",
        "version": 3,
        "episode_number": 2,
        "stage": "outline",
        "status": "draft",
        "content": {
            "series_overview": SERIES_OVERVIEW,
            "core_event": "董事会逼宫，林澈用隐藏订单争取三天时间",
            "conflict": "资金链断裂与内部质疑同时升级",
            "character_progress": "合伙人首次公开支持林澈",
            "highlight": "隐藏订单在最后一分钟完成验真",
            "reversal": "订单客户与竞争对手存在关联",
            "cliffhanger": "财务主管收到匿名离职威胁",
        },
    },
    {
        "id": 112,
        "project_id": 8,
        "batch_id": "outlines-v3",
        "version": 3,
        "episode_number": 12,
        "stage": "outline",
        "status": "draft",
        "content": {
            "series_overview": SERIES_OVERVIEW,
            "core_event": "终局反击，所有伏笔在发布会现场闭环",
            "conflict": "林澈必须在直播中证明完整证据链",
            "character_progress": "团队完成最终和解",
            "highlight": "直播观众共同补全关键时间线",
            "reversal": "真正主谋主动进入直播间",
            "cliffhanger": "新订单来自一个未知海外地址",
        },
    },
]


@pytest.fixture
def outline_state_page() -> Iterator[Page]:
    """Load the real page module around a small, deterministic outline state."""
    outline_script = ROOT / "static/js/writing/outlines.js"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(2_000)
        page.set_content('<body data-workspace-page="writing-outlines"></body>')
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=outline_script)
        page.evaluate(
            """
            ([items]) => {
              window.state = Object.assign({
                creativeWorkspace: {},
                outlineItems: items,
                currentProjectOutlines() { return this.outlineItems; }
              }, window.aiworkerModules.at(-1)?.());
            }
            """,
            [OUTLINE_ITEMS],
        )
        yield page
        browser.close()


@pytest.fixture
def outline_cards_page() -> Iterator[Page]:
    """Render the real outline workspace with its page script and Alpine."""
    template = (ROOT / "templates/common/workspace/project_outlines.html").read_text(
        encoding="utf-8"
    )
    rewrite_modal_path = (
        ROOT / "templates/common/workspace/outline_field_rewrite_modal.html"
    )
    rewrite_modal = (
        rewrite_modal_path.read_text(encoding="utf-8")
        if rewrite_modal_path.exists()
        else ""
    )
    outline_script = ROOT / "static/js/writing/outlines.js"
    bootstrap_css = (ROOT / "static/css/bootstrap.min.css").read_text(encoding="utf-8")
    workspace_css = (ROOT / "static/css/workspaces.css").read_text(encoding="utf-8")
    alpine_script = ROOT / "static/js/alpine.min.js"
    bootstrap_script = ROOT / "static/js/bootstrap.bundle.min.js"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(2_000)
        page.set_content(
            f'<main class="main-content" x-data="appData()">{template}{rewrite_modal}</main>'
        )
        page.add_style_tag(content=f"{bootstrap_css}\n{workspace_css}")
        page.add_script_tag(path=bootstrap_script)
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=outline_script)
        page.evaluate(
            """
            ([items]) => {
              window.savedEpisodeNumbers = [];
              window.updateEpisodeNumbers = [];
              window.outlineOverviewRequests = [];
              window.outlineOverviewGeneration = null;
              window.outlineFieldRewriteRequests = [];
              window.selectedCreativeProjects = [];
              window.aiworkerApi = {
                async request(url, options = {}) {
                  const body = options.body ? JSON.parse(options.body) : null;
                  if (url.includes('/fields/') && url.endsWith('/rewrite')) {
                    window.outlineFieldRewriteRequests.push({url, method: options.method || 'GET', body});
                    return {
                      ok: true,
                      async json() {
                        return {
                          episode_id: 101, project_id: 8, batch_id: 'outlines-v3',
                          field: 'core_event', original_value: '密室账本曝光，林澈发现旧厂财务记录被人系统性篡改',
                          candidate_value: 'AI 重写后的核心事件', source_fingerprint: 'a'.repeat(64),
                          source_status: 'draft', meta: {prompt_template_version: 'outline-field-rewrite-v1'}
                        };
                      }
                    };
                  }
                  if (url.includes('/fields/') && url.endsWith('/apply-rewrite')) {
                    window.outlineFieldRewriteRequests.push({url, method: options.method || 'GET', body});
                    return {
                      ok: true,
                      async json() {
                        return {
                          created_new_version: false, batch_id: 'outlines-v3', version: 3,
                          episode: {
                            id: 101, project_id: 8, batch_id: 'outlines-v3', version: 3,
                            episode_number: 1, status: 'draft',
                            content: {core_event: body.candidate_value}
                          }
                        };
                      }
                    };
                  }
                  window.outlineOverviewRequests.push({url, method: options.method || 'GET', body});
                  return {
                    ok: true,
                    async json() { return {series_overview: body?.series_overview || null}; }
                  };
                }
              };
              window.appData = () => Object.assign({
                outlineItems: items,
                creativeWorkspace: {
                  project: {
                    id: 8, target_episode_count: 3, locked_brief_id: 4,
                    character_batch_id: 'characters-v2', outline_batch_id: 'outlines-v3'
                  },
                  outlineQuery: '', expandedOutlineEpisodes: {}, outlineCardContextKey: '',
                  outlineOverviewExpanded: true, outlineOverviewDraft: {},
                  outlineOverviewErrors: {}, outlineOverviewSaving: false,
                  outlineFieldRewrite: {
                    projectId: 0, batchId: '', episodeId: 0, episodeNumber: 0,
                    fieldKey: '', fieldLabel: '', sourceStatus: '', sourceText: '',
                    sourceFingerprint: '', instruction: '', candidateText: '',
                    loading: false, applying: false, error: '', requestSequence: 0
                  },
                  selectedOutlineBriefId: 4, selectedOutlineCharacterBatchId: 'characters-v2',
                  startingAction: '', saveState: {}
                },
                currentProjectOutlines() { return this.outlineItems; },
                creativeWorkflowStatusLabel(status) {
                  return status === 'draft' ? '草稿' : '已完成';
                },
                currentOutlineBatchStatus() { return this.outlineItems[0]?.status || 'draft'; },
                projectHasGeneratedScripts() { return true; },
                mergedScriptDownloadUrl() { return '/api/creative/projects/8/scripts/download'; },
                generateProjectOutlines() {},
                async resolveCreativeWorkflowTask() { return {id: 'outline-task-8'}; },
                startCreativeGeneration(url, body, action) {
                  window.outlineOverviewGeneration = {url, body, action};
                },
                promptGenerationPayload(_stage, body = {}) { return {...body}; },
                showCreativeNotice() {},
                modal(id) {
                  const element = document.getElementById(id);
                  return element ? bootstrap.Modal.getOrCreateInstance(element) : null;
                },
                async selectCreativeProject(projectId) {
                  window.selectedCreativeProjects.push(Number(projectId));
                },
                creativeActionDisabled() { return false; },
                creativeActionRunning() { return false; },
                outlineGenerationActionLabel() { return '重新生成'; },
                creativeResourceEditable(item) { return item?.status === 'draft'; },
                completeProjectOutlines() {},
                latestOutlineBatchId() { return 'outlines-v3'; },
                currentGeneratedResourceId() { return ''; },
                creativeGeneratedDownloadUrl() { return '#download-outline'; },
                availableLockedBriefs() { return []; },
                selectOutlineBrief() {},
                availableLockedCharacterBatches() { return []; },
                selectOutlineCharacterBatch() {},
                projectEpisodeScript(episodeNumber) {
                  return Number(episodeNumber) === 1 ? {status: 'locked'} : null;
                },
                projectEpisodeScriptLabel(episodeNumber) {
                  return Number(episodeNumber) === 1 ? '剧本已完成' : '';
                },
                hasProjectEpisodeUpdates(episodeNumber) {
                  return Number(episodeNumber) === 1;
                },
                projectEpisodeUpdateSummary(episodeNumber) {
                  return Number(episodeNumber) === 1 ? 'Beats 更新' : '';
                },
                openOutlineEpisodeUpdates(episodeNumber) {
                  window.updateEpisodeNumbers.push(Number(episodeNumber));
                },
                saveProjectEpisode(item) {
                  window.savedEpisodeNumbers.push(Number(item.episode_number));
                }
              }, ...window.aiworkerModules.map(factory => factory()));
            }
            """,
            [CARD_OUTLINE_ITEMS],
        )
        page.add_script_tag(path=alpine_script)
        page.wait_for_function(
            "window.Alpine && Alpine.$data(document.querySelector('main')).outlineItems.length === 3"
            " && Alpine.$data(document.querySelector('main')).creativeWorkspace.outlineCardContextKey"
            " && Alpine.$data(document.querySelector('main')).creativeWorkspace.outlineOverviewDraft.core_event"
        )
        yield page
        browser.close()


def filtered_episode_numbers(page: Page) -> list[int]:
    return page.evaluate(
        "state.filteredProjectOutlines().map(item => item.episode_number)"
    )


def expanded_episode_numbers(page: Page) -> list[int]:
    return page.evaluate(
        """
        state.outlineItems
          .filter(item => state.outlineEpisodeExpanded(item.episode_number))
          .map(item => item.episode_number)
        """
    )


def test_outline_filter_matches_episode_number_and_core_event(
    outline_state_page: Page,
) -> None:
    """Removing either supported search path must make this test fail."""
    page = outline_state_page

    assert filtered_episode_numbers(page) == [1, 2, 12]
    page.evaluate("state.creativeWorkspace.outlineQuery = '2'")
    assert filtered_episode_numbers(page) == [2, 12]
    page.evaluate("state.creativeWorkspace.outlineQuery = '  密室  '")
    assert filtered_episode_numbers(page) == [1]
    assert page.evaluate(
        "state.filteredProjectOutlines()[0] === state.outlineItems[0]"
    )


def test_outline_expand_and_collapse_actions_only_change_visible_results(
    outline_state_page: Page,
) -> None:
    """Batch actions must not disturb cards hidden by the current filter."""
    page = outline_state_page

    page.evaluate("state.toggleOutlineEpisode(1)")
    assert expanded_episode_numbers(page) == [1]
    page.evaluate("state.creativeWorkspace.outlineQuery = '董事会'")
    page.evaluate("state.expandAllOutlineEpisodes()")
    assert expanded_episode_numbers(page) == [1, 2]
    page.evaluate("state.collapseAllOutlineEpisodes()")
    assert expanded_episode_numbers(page) == [1]


def test_outline_card_context_only_resets_for_a_different_project_or_version(
    outline_state_page: Page,
) -> None:
    """Refreshing the same context must not unexpectedly close a writer's card."""
    page = outline_state_page
    original = {
        "id": 8,
        "locked_brief_id": 4,
        "character_batch_id": "characters-v2",
        "outline_batch_id": "outlines-v3",
    }

    page.evaluate("project => state.syncOutlineEpisodeCardContext(project)", original)
    page.evaluate("state.creativeWorkspace.outlineQuery = '密室'")
    page.evaluate("state.toggleOutlineEpisode(1)")
    page.evaluate("project => state.syncOutlineEpisodeCardContext(project)", original)
    assert page.evaluate("state.creativeWorkspace.outlineQuery") == "密室"
    assert expanded_episode_numbers(page) == [1]

    changed = {**original, "outline_batch_id": "outlines-v4"}
    page.evaluate("project => state.syncOutlineEpisodeCardContext(project)", changed)
    assert page.evaluate("state.creativeWorkspace.outlineQuery") == ""
    assert expanded_episode_numbers(page) == []


def test_latest_outline_batch_prefers_new_revision_draft_only_while_project_is_drafting() -> None:
    creative_script = ROOT / "static/js/common/creative.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<body></body>")
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=creative_script)
        result = page.evaluate(
            """
            () => {
              const methods = Object.assign({}, ...window.aiworkerModules.map(factory => factory()));
              const compatible = {
                project_id: 8, stage: 'outline', upstream_refs: {
                  brief_id: 4, character_batch_id: 'characters-v2'
                }
              };
              const state = Object.assign({creativeWorkspace: {project: {
                id: 8, status: 'outlines_locked', locked_brief_id: 4,
                character_batch_id: 'characters-v2', outline_batch_id: 'locked-v3',
                episodes: [
                  {...compatible, batch_id: 'old-draft-v2', version: 2, status: 'draft'},
                  {...compatible, batch_id: 'locked-v3', version: 3, status: 'locked'},
                  {...compatible, batch_id: 'new-draft-v4', version: 4, status: 'draft'},
                  {...compatible, batch_id: 'wrong-brief-v9', version: 9, status: 'draft',
                    upstream_refs: {brief_id: 99, character_batch_id: 'characters-v2'}}
                ]
              }}}, methods);
              const lockedChoice = state.latestOutlineBatchId();
              state.creativeWorkspace.project.status = 'outline_draft';
              const draftChoice = state.latestOutlineBatchId();
              return {lockedChoice, draftChoice};
            }
            """
        )
        browser.close()

    assert result == {"lockedChoice": "locked-v3", "draftChoice": "new-draft-v4"}


def test_outline_cards_default_to_compact_previews_and_keep_actions_visible(
    outline_cards_page: Page,
) -> None:
    """Restoring the wide table or hiding actions in details must fail here."""
    page = outline_cards_page

    assert page.locator(".outline-episode-card").count() == 3
    assert page.locator("table").count() == 0
    assert page.locator(".outline-episode-details:visible").count() == 0

    first = page.locator('.outline-episode-card[data-episode-number="1"]')
    assert first.get_by_text("剧本已完成", exact=True).is_visible()
    assert first.get_by_text("Beats 更新", exact=True).is_visible()
    assert first.locator(".outline-save-action").is_visible()
    assert first.locator(".outline-beats-action").is_visible()
    assert first.locator(".outline-event-preview").get_attribute("title") == (
        CARD_OUTLINE_ITEMS[0]["content"]["core_event"]
    )

    page.get_by_role("button", name="展开第 1 集").click()
    first.locator(".outline-episode-details").wait_for(state="visible")
    assert first.locator(".outline-episode-details").is_visible()
    assert first.locator(".outline-field-label").all_inner_texts() == [
        "核心事件",
        "冲突",
        "人物推进",
        "亮点",
        "反转",
        "结尾悬念",
    ]
    assert first.get_by_text("剧本已完成", exact=True).is_visible()
    assert first.get_by_text("Beats 更新", exact=True).is_visible()
    assert first.locator(".outline-beats-action").is_visible()


def test_outline_field_ai_rewrite_previews_and_applies_only_selected_field(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    page.get_by_role("button", name="展开第 1 集").click()
    before_conflict = page.locator("#outline-conflict-101").input_value()
    page.locator(
        '[data-episode-number="1"] [data-outline-ai-field="core_event"]'
    ).click()
    page.locator("#outlineFieldRewriteInstruction").fill("强化危险感")
    page.get_by_role("button", name="生成新稿").click()
    page.locator("#outlineFieldRewriteCandidate").wait_for(state="visible")
    assert page.locator("#outlineFieldRewriteCandidate").input_value() == "AI 重写后的核心事件"
    screenshot_dir = os.getenv("AIWORKER_OUTLINE_SCREENSHOT_DIR")
    if screenshot_dir:
        output_dir = Path(screenshot_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=output_dir / "outline-field-rewrite-desktop.png", full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator("#outlineFieldRewriteModal .modal-dialog").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if screenshot_dir:
        page.screenshot(
            path=Path(screenshot_dir) / "outline-field-rewrite-mobile.png",
            full_page=True,
        )
    page.get_by_role("button", name="采用新稿").click()

    assert page.locator("#outline-core-event-101").input_value() == "AI 重写后的核心事件"
    assert page.locator("#outline-conflict-101").input_value() == before_conflict
    requests = page.evaluate("window.outlineFieldRewriteRequests")
    assert requests[0]["body"] == {"instruction": "强化危险感"}
    assert requests[1]["body"]["candidate_value"] == "AI 重写后的核心事件"
    assert "content" not in requests[1]["body"]


def test_outline_field_ai_rewrite_ignores_response_after_batch_switch(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    result = page.evaluate(
        """
        async () => {
          const data = Alpine.$data(document.querySelector('main'));
          let resolveRequest;
          window.aiworkerApi.request = () => new Promise(resolve => { resolveRequest = resolve; });
          data.openOutlineFieldRewrite(data.outlineItems[0], 'core_event', '核心事件');
          const pending = data.requestOutlineFieldRewrite();
          await new Promise(resolve => setTimeout(resolve, 0));
          data.creativeWorkspace.project = {...data.creativeWorkspace.project, id: 9, outline_batch_id: 'outlines-v9'};
          resolveRequest({
            ok: true,
            async json() {
              return {candidate_value: '迟到的新稿', source_fingerprint: 'b'.repeat(64), source_status: 'draft'};
            }
          });
          await pending;
          return data.creativeWorkspace.outlineFieldRewrite.candidateText;
        }
        """
    )

    assert result == ""


@pytest.mark.parametrize("close_path", ["escape", "backdrop"])
def test_outline_field_ai_rewrite_ignores_late_response_after_bootstrap_modal_close(
    outline_cards_page: Page,
    close_path: str,
) -> None:
    page = outline_cards_page
    page.evaluate(
        """
        () => {
          const data = Alpine.$data(document.querySelector('main'));
          const modal = document.getElementById('outlineFieldRewriteModal');
          window.outlineModalShown = new Promise(resolve => {
            modal.addEventListener('shown.bs.modal', resolve, {once: true});
          });
          window.outlineModalHidden = new Promise(resolve => {
            modal.addEventListener('hidden.bs.modal', resolve, {once: true});
          });
          window.aiworkerApi.request = () => new Promise(resolve => {
            window.resolveOutlineFieldRewrite = resolve;
          });
          data.openOutlineFieldRewrite(data.outlineItems[0], 'core_event', '核心事件');
          window.outlineFieldRewritePending = data.requestOutlineFieldRewrite();
        }
        """
    )
    page.wait_for_function("typeof window.resolveOutlineFieldRewrite === 'function'")
    page.locator("#outlineFieldRewriteModal").wait_for(state="visible")
    page.evaluate("() => window.outlineModalShown")

    if close_path == "escape":
        page.evaluate(
            "document.getElementById('outlineFieldRewriteModal').dispatchEvent("
            "new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))"
        )
    else:
        page.evaluate(
            "const modal = document.getElementById('outlineFieldRewriteModal');"
            "modal.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));"
            "modal.dispatchEvent(new MouseEvent('click', {bubbles: true}));"
        )
    page.locator("#outlineFieldRewriteModal").wait_for(state="hidden")
    page.evaluate("() => window.outlineModalHidden")

    result = page.evaluate(
        """
        async () => {
          window.resolveOutlineFieldRewrite({
            ok: true,
            async json() {
              return {
                episode_id: 101, project_id: 8, batch_id: 'outlines-v3',
                field: 'core_event', candidate_value: '关闭后的迟到新稿',
                source_fingerprint: 'b'.repeat(64), source_status: 'draft'
              };
            }
          });
          await window.outlineFieldRewritePending;
          return Alpine.$data(document.querySelector('main')).creativeWorkspace.outlineFieldRewrite;
        }
        """
    )

    assert result["episodeId"] == 0
    assert result["candidateText"] == ""


def test_series_overview_precedes_episode_tools_and_uses_the_six_field_layout(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    overview = page.locator(".outline-series-overview")

    assert overview.is_visible()
    assert overview.get_by_text("全集剧情概览", exact=True).is_visible()
    assert overview.locator(".outline-overview-field-label").all_inner_texts() == [
        "核心事件",
        "人物推进",
        "主要冲突",
        "关键反转",
        "剧情亮点",
        "结尾悬念",
    ]
    assert overview.locator("textarea").count() == 6
    assert page.get_by_role("button", name="保存全集剧情概览").is_disabled()
    overview_box = overview.bounding_box()
    toolbar_box = page.locator(".outline-list-toolbar").bounding_box()
    assert overview_box is not None and toolbar_box is not None
    assert overview_box["y"] + overview_box["height"] <= toolbar_box["y"]

    page.get_by_role("button", name="收起全集剧情概览").click()
    overview.locator(".outline-overview-body").wait_for(state="hidden")
    assert page.get_by_role("button", name="展开全集剧情概览").is_visible()


def test_series_overview_validates_and_saves_the_batch_without_losing_input(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    overview = page.locator(".outline-series-overview")
    fields = overview.locator("textarea")

    fields.nth(0).fill("")
    page.get_by_role("button", name="保存全集剧情概览").click()
    overview.locator(".outline-overview-error").first.wait_for(state="visible")
    assert "请填写核心事件" in overview.inner_text()
    assert page.evaluate("window.outlineOverviewRequests.length") == 0
    page.get_by_role("button", name="收起全集剧情概览").click()
    error_summary = overview.locator(".outline-overview-error-summary")
    error_summary.wait_for(state="visible")
    assert "请填写核心事件" in error_summary.inner_text()
    page.get_by_role("button", name="展开全集剧情概览").click()

    fields.nth(0).fill("更新后的全剧核心事件")
    page.get_by_role("button", name="保存全集剧情概览").click()
    page.wait_for_function("window.outlineOverviewRequests.length === 1")
    request = page.evaluate("window.outlineOverviewRequests[0]")
    assert request["method"] == "PATCH"
    assert request["url"].endswith("/outlines/outlines-v3/overview")
    assert request["body"]["series_overview"]["core_event"] == "更新后的全剧核心事件"
    assert page.evaluate(
        "Alpine.$data(document.querySelector('main')).outlineItems.every("
        "item => item.content.series_overview.core_event === '更新后的全剧核心事件')"
    )

    page.evaluate(
        "Alpine.$data(document.querySelector('main')).outlineItems.forEach(item => item.status = 'locked')"
    )
    assert fields.nth(0).is_disabled()
    page.get_by_role("button", name="保存全集剧情概览").wait_for(state="hidden")


def test_series_overview_save_response_does_not_mutate_a_new_context(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    result = page.evaluate(
        """
        async ([otherOverview]) => {
          const data = Alpine.$data(document.querySelector('main'));
          let resolveRequest;
          window.aiworkerApi.request = () => new Promise(resolve => { resolveRequest = resolve; });
          data.creativeWorkspace.outlineOverviewDraft.core_event = 'A 项目保存值';
          const saving = data.saveOutlineSeriesOverview();
          await new Promise(resolve => setTimeout(resolve, 0));
          data.creativeWorkspace.project = {
            ...data.creativeWorkspace.project,
            id: 9,
            outline_batch_id: 'outlines-v9'
          };
          data.outlineItems = [{
            id: 901,
            episode_number: 1,
            stage: 'outline',
            status: 'draft',
            content: {series_overview: {...otherOverview}, core_event: 'B 项目事件'}
          }];
          resolveRequest({
            ok: true,
            async json() {
              return {series_overview: {...otherOverview, core_event: 'A 项目保存值'}};
            }
          });
          await saving;
          return data.outlineItems[0].content.series_overview.core_event;
        }
        """,
        [SERIES_OVERVIEW],
    )

    assert result == SERIES_OVERVIEW["core_event"]


def test_an_older_save_response_cannot_clear_a_newer_context_saving_state(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    result = page.evaluate(
        """
        async ([otherOverview]) => {
          const data = Alpine.$data(document.querySelector('main'));
          const pending = [];
          window.aiworkerApi.request = () => new Promise(resolve => pending.push(resolve));
          data.creativeWorkspace.outlineOverviewDraft.core_event = 'A 项目保存值';
          const first = data.saveOutlineSeriesOverview();
          await new Promise(resolve => setTimeout(resolve, 0));

          data.creativeWorkspace.project = {
            ...data.creativeWorkspace.project,
            id: 9,
            outline_batch_id: 'outlines-v9'
          };
          data.outlineItems = [{
            id: 901,
            episode_number: 1,
            stage: 'outline',
            status: 'draft',
            content: {series_overview: {...otherOverview}, core_event: 'B 项目事件'}
          }];
          await new Promise(resolve => setTimeout(resolve, 0));
          data.resetOutlineSeriesOverview();
          data.creativeWorkspace.outlineOverviewDraft.core_event = 'B 项目保存值';
          const second = data.saveOutlineSeriesOverview();
          await new Promise(resolve => setTimeout(resolve, 0));

          pending[0]({
            ok: true,
            async json() { return {series_overview: {...otherOverview, core_event: 'A 项目保存值'}}; }
          });
          await first;
          const savingAfterFirst = data.creativeWorkspace.outlineOverviewSaving;

          pending[1]({
            ok: true,
            async json() { return {series_overview: {...otherOverview, core_event: 'B 项目保存值'}}; }
          });
          await second;
          return {
            savingAfterFirst,
            savingAfterSecond: data.creativeWorkspace.outlineOverviewSaving,
            savedValue: data.outlineItems[0].content.series_overview.core_event
          };
        }
        """,
        [SERIES_OVERVIEW],
    )

    assert result == {
        "savingAfterFirst": True,
        "savingAfterSecond": False,
        "savedValue": "B 项目保存值",
    }


def test_legacy_draft_exposes_the_series_overview_generation_action(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    page.evaluate(
        """
        const data = Alpine.$data(document.querySelector('main'));
        data.outlineItems.forEach(item => delete item.content.series_overview);
        data.syncOutlineEpisodeCardContext({...data.creativeWorkspace.project, outline_batch_id: 'legacy-v1'});
        """
    )

    generate = page.get_by_role("button", name="生成全集剧情概览")
    generate.wait_for(state="visible")
    assert generate.is_visible()
    generate.click()
    page.wait_for_function("window.outlineOverviewGeneration !== null")
    started = page.evaluate("window.outlineOverviewGeneration")
    assert started["action"] == "outline_overview"
    assert started["url"].endswith("/outlines/outlines-v3/overview/generate")
    assert started["body"]["workflow_task_id"] == "outline-task-8"


def test_locked_current_outline_exposes_one_time_series_overview_generation(
    outline_cards_page: Page,
) -> None:
    """Treating every locked field as non-generatable must hide this action."""

    page = outline_cards_page
    page.evaluate(
        """
        const data = Alpine.$data(document.querySelector('main'));
        data.outlineItems.forEach(item => {
          item.status = 'locked';
          delete item.content.series_overview;
        });
        data.resetOutlineSeriesOverview();
        data.resolveCreativeWorkflowTask = () => {
          throw new Error('锁定版本不应重新开启大纲任务');
        };
        void 0;
        """
    )

    generate = page.get_by_role("button", name="生成全集剧情概览")
    generate.wait_for(state="visible")
    assert page.get_by_text(
        "当前大纲版本已锁定，可基于全部分集内容补充生成。", exact=True
    ).is_visible()
    assert page.get_by_role("button", name="保存全集剧情概览").is_hidden()

    generate.click()
    page.wait_for_function("window.outlineOverviewGeneration !== null")
    started = page.evaluate("window.outlineOverviewGeneration")
    assert started["action"] == "outline_overview"
    assert started["url"].endswith("/outlines/outlines-v3/overview/generate")
    assert started["body"] == {}


def test_series_overview_grid_collapses_to_one_column_without_page_overflow(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    grid = page.locator(".outline-overview-grid")

    desktop_columns = page.evaluate(
        "element => getComputedStyle(element).gridTemplateColumns", grid.element_handle()
    )
    assert len(desktop_columns.split()) == 2
    page.set_viewport_size({"width": 390, "height": 844})
    mobile_columns = page.evaluate(
        "element => getComputedStyle(element).gridTemplateColumns", grid.element_handle()
    )
    assert len(mobile_columns.split()) == 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_outline_search_and_batch_controls_operate_on_visible_cards(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page

    page.locator("#outline-episode-search").fill("董事会")
    assert page.locator(".outline-episode-card:visible").count() == 1
    assert page.locator('.outline-episode-card[data-episode-number="2"]').is_visible()

    page.locator("#outline-expand-all").click()
    page.locator(
        '.outline-episode-card[data-episode-number="2"] .outline-episode-details'
    ).wait_for(state="visible")
    assert page.locator(".outline-episode-details:visible").count() == 1
    page.locator("#outline-collapse-all").click()
    page.locator(
        '.outline-episode-card[data-episode-number="2"] .outline-episode-details'
    ).wait_for(state="hidden")
    assert page.locator(".outline-episode-details:visible").count() == 0

    page.locator("#outline-episode-search").fill("不存在的剧情")
    empty_result = page.get_by_text("未找到匹配的分集大纲", exact=True)
    empty_result.wait_for(state="visible")
    assert empty_result.is_visible()


def test_outline_drafts_are_editable_but_locked_versions_are_read_only(
    outline_cards_page: Page,
) -> None:
    """Removing the existing version guard must expose an enabled locked field."""
    page = outline_cards_page
    first = page.locator('.outline-episode-card[data-episode-number="1"]')

    page.get_by_role("button", name="展开第 1 集").click()
    fields = first.locator("textarea")
    assert fields.count() == 6
    first.locator("[data-outline-ai-field]").first.wait_for(state="visible")
    assert first.locator("[data-outline-ai-field]:visible").count() == 6
    assert fields.nth(0).is_enabled()
    fields.nth(0).fill("编辑后的核心事件")
    first.locator(".outline-save-action").click()
    assert page.evaluate("window.savedEpisodeNumbers") == [1]
    assert page.evaluate(
        "Alpine.$data(document.querySelector('main')).outlineItems[0].content.core_event"
    ) == "编辑后的核心事件"

    page.evaluate(
        "Alpine.$data(document.querySelector('main')).outlineItems[0].status = 'locked'"
    )
    assert fields.nth(0).is_disabled()
    first.locator(".outline-save-action").wait_for(state="hidden")
    first.locator("[data-outline-ai-field]").first.wait_for(state="visible")
    assert first.locator("[data-outline-ai-field]:visible").count() == 6


def test_outline_field_grid_is_responsive_without_horizontal_page_overflow(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    page.get_by_role("button", name="展开第 1 集").click()
    grid = page.locator(
        '.outline-episode-card[data-episode-number="1"] .outline-field-grid'
    )
    grid.wait_for(state="visible")

    desktop_columns = page.evaluate(
        "element => getComputedStyle(element).gridTemplateColumns", grid.element_handle()
    )
    assert len(desktop_columns.split()) == 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    screenshot_dir = os.getenv("AIWORKER_OUTLINE_SCREENSHOT_DIR")
    if screenshot_dir:
        output_dir = Path(screenshot_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=output_dir / "outline-cards-desktop.png", full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    mobile_columns = page.evaluate(
        "element => getComputedStyle(element).gridTemplateColumns", grid.element_handle()
    )
    assert len(mobile_columns.split()) == 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    primary_action_boxes = page.locator(
        ".outline-workspace > .d-flex:first-child > .d-flex > .btn:visible"
    ).evaluate_all(
        "elements => elements.map(element => ({width: element.offsetWidth, height: element.offsetHeight}))"
    )
    assert primary_action_boxes
    assert all(box["width"] >= 80 and box["height"] <= 44 for box in primary_action_boxes)
    heading_copy_box = page.locator(
        ".outline-section-heading > div:first-child"
    ).bounding_box()
    primary_actions_box = page.locator(".outline-primary-actions").bounding_box()
    assert heading_copy_box is not None and primary_actions_box is not None
    assert primary_actions_box["y"] >= heading_copy_box["y"] + heading_copy_box["height"]
    if screenshot_dir:
        page.screenshot(
            path=Path(screenshot_dir) / "outline-cards-mobile.png", full_page=True
        )
