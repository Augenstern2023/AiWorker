# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Browser coverage for all-episode writing views and episode downloads."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("page_name", "url"),
    [
        ("planning-brief", "http://aiworker.test/planning/projects/9/brief"),
        ("writing-outlines", "http://aiworker.test/writing/projects/9/outlines"),
        (
            "writing-beats",
            "http://aiworker.test/writing/projects/9/episodes/1/beats?episode=all",
        ),
        (
            "writing-scripts",
            "http://aiworker.test/writing/projects/9/episodes/1/scripts?episode=all",
        ),
    ],
)
def test_episode_workspace_initial_state_matches_the_all_episode_url(
    page_name: str, url: str
) -> None:
    """Project entry and all-view URLs must render with the all view immediately."""
    tasks_script = ROOT / "static/js/common/tasks.js"
    creative_script = ROOT / "static/js/common/creative.js"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "http://aiworker.test/**",
            lambda route: route.fulfill(
                content_type="text/html",
                body=f'<body data-workspace-page="{page_name}"></body>',
            ),
        )
        page.goto(url)
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=tasks_script)
        page.add_script_tag(path=creative_script)

        state = page.evaluate(
            """
            () => {
              const data = appData();
              data.creativeWorkspace.project = {id: 9, target_episode_count: 2};
              return {
                selectedEpisode: data.creativeWorkspace.selectedEpisode,
                outlineUrl: data.creativeModuleUrl('outline'),
                beatsUrl: data.creativeModuleUrl('beats'),
                scriptUrl: data.creativeModuleUrl('script'),
              };
            }
            """
        )

        assert state == {
            "selectedEpisode": 0,
            "outlineUrl": "/writing/projects/9/outlines?episode=all",
            "beatsUrl": "/writing/projects/9/episodes/1/beats?episode=all",
            "scriptUrl": "/writing/projects/9/episodes/1/scripts?episode=all",
        }
        browser.close()


@pytest.mark.parametrize(
    ("stage", "template_name", "visible_selector", "expected_text"),
    [
        ("beats", "project_beats.html", "section.border", "第二集场景"),
        ("script", "project_scripts.html", "section.border", "△ 第二集动作"),
    ],
)
def test_episode_workspace_all_view_and_multi_download_modal(
    stage: str, template_name: str, visible_selector: str, expected_text: str
) -> None:
    template = (ROOT / "templates/common/workspace" / template_name).read_text(
        encoding="utf-8"
    )
    modal = (
        ROOT / "templates/common/workspace/creative_episode_download_modal.html"
    ).read_text(encoding="utf-8")
    creative_script = ROOT / "static/js/common/creative.js"
    alpine_script = ROOT / "static/js/alpine.min.js"

    items = [
        {
            "id": number,
            "episode_number": number,
            "version": 1,
            "status": "draft",
            "content": (
                {
                    "beats": [{"order": 1, "scene": f"第{'一二'[number - 1]}集场景"}]
                }
                if stage == "beats"
                else {
                    "episode_title": f"第{'一二'[number - 1]}集",
                    "scenes": [
                        {
                            "sequence_no": 1,
                            "scene_no": 1,
                            "time_of_day": "日",
                            "interior_exterior": "内",
                            "location": "客厅",
                            "props": [],
                            "characters": ["主角"],
                            "blocks": [
                                {"type": "action", "text": f"第{'一二'[number - 1]}集动作"}
                            ],
                        }
                    ],
                }
            ),
        }
        for number in (1, 2)
    ]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(f'<main x-data="appData()">{template}{modal}</main>')
        page.add_style_tag(path=ROOT / "static/css/bootstrap.min.css")
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=creative_script)
        page.evaluate(
            """
            ([stage, items]) => {
              const methods = window.aiworkerModules.at(-1)();
              window.aiworkerApi = {
                async request(url) {
                  if (!url.endsWith('/episodes/export/templates')) {
                    return new Response('{}', {status: 404});
                  }
                  return new Response(JSON.stringify({
                    default_template_id: 'classic',
                    items: [
                      {id: 'classic', name: '标准场次格式', description: '标准', previewable: true},
                      {id: 'wife_revenge', name: '全案审阅格式', description: '审阅', previewable: true},
                      {id: 'character_outsource', name: '紧凑编剧格式', description: '紧凑', previewable: true},
                      {id: 'scholar_beggar', name: 'EP 分集格式', description: '分集', previewable: true}
                    ]
                  }), {status: 200, headers: {'content-type': 'application/json'}});
                }
              };
              window.appData = () => Object.assign({}, methods, {
                creativeWorkspace: {
                  project: {id: 9, target_episode_count: 2, workflow_mode: 'free'},
                  selectedEpisode: 0,
                  episodeDownload: {stage: '', selectedEpisodes: [], allEpisodes: true, format: 'md'},
                  videoParameters: {
                    record: null, analyzing: false, loading: false, confirming: '',
                    error: '', job: null, analysisDeadlineSeconds: 60
                  },
                  startingAction: '', activeAction: ''
                },
                stageItems: items,
                projectStageEpisodeItems(value) { return value === stage ? this.stageItems : []; },
                projectBeatsGenerationProgress() { return {generated: 2, required: 2}; },
                lockedProjectBeatsProgress() { return {locked: 0, required: 2}; },
                projectScriptsSeriesBlockReason() { return ''; },
                projectEpisodeScript(number) {
                  return stage === 'script'
                    ? this.stageItems.find(item => item.episode_number === Number(number)) || null
                    : null;
                },
                selectedProjectBeats() { return null; },
                selectedProjectScript() { return null; },
                projectBeatEpisodeLabel(number) { return `第 ${number} 集`; },
                creativeWorkflowStatusLabel() { return '草稿'; },
                creativeActionDisabled() { return false; },
                creativeActionRunning() { return false; },
                freeStageCan() { return true; },
                projectBeatGenerationActionLabel() { return '生成 Beats'; },
                projectScriptGenerationActionLabel() { return '生成场次剧本'; },
                seriesGenerationActionLabel() { return '全剧生成'; },
                canBatchLockProjectBeats() { return false; },
                canBatchLockProjectEpisodes() { return false; },
                canGenerateProjectScriptsSeries() { return true; },
                currentGeneratedResourceId() { return ''; },
                listText(value) { return Array.isArray(value) ? value.join('、') : String(value || ''); },
                scriptWorkspaceEmptyMessage() { return ''; },
                creativeModuleUrl(value, number) { return `/${value}/${number}`; },
                showCreativeNotice() {},
                modal(id) {
                  const element = document.getElementById(id);
                  return {
                    show() { element.style.display = 'block'; element.classList.add('show'); },
                    hide() { element.style.display = 'none'; element.classList.remove('show'); }
                  };
                },
                generateProjectBeats() {}, generateProjectBeatsSeries() {},
                completeProjectBeatsBatch() {}, openProjectScriptWorkspace() {},
                generateProjectScript() {}, generateProjectScriptsSeries() {},
                completeFreeProjectEpisodeSeries() {}, createFreeBlankResource() {}
              });
            }
            """,
            [stage, items],
        )
        page.add_script_tag(path=alpine_script)
        page.wait_for_function(
            "document.querySelectorAll('section.border').length >= 2"
        )

        assert page.locator(visible_selector).count() >= 2
        assert page.get_by_text(expected_text, exact=True).is_visible()
        page.get_by_role("button", name="多集/全集下载").first.click()
        page.wait_for_function(
            "document.getElementById('creativeEpisodeDownloadModal').classList.contains('show')"
        )
        if stage == "script":
            page.wait_for_function(
                "Alpine.$data(document.querySelector('main')).creativeWorkspace.episodeDownload.templateOptions.length === 4"
            )
        state = page.evaluate(
            """
            () => {
              const data = Alpine.$data(document.querySelector('main'));
              return data.creativeWorkspace.episodeDownload;
            }
            """
        )
        assert state["selectedEpisodes"] == [1, 2]
        assert state["allEpisodes"] is True
        if stage == "script":
            assert [item["id"] for item in state["templateOptions"]] == [
                "classic",
                "wife_revenge",
                "character_outsource",
                "scholar_beggar",
            ]
        assert page.evaluate(
            """
            () => [...document.querySelectorAll('#creativeEpisodeDownloadModal label.form-check')]
              .every(label => {
                const input = label.querySelector('input[type="checkbox"]');
                if (!input) return true;
                const outer = label.getBoundingClientRect();
                const inner = input.getBoundingClientRect();
                return inner.left >= outer.left && inner.right <= outer.right;
              })
            """
        )
        assert not errors
        browser.close()


def test_episode_download_moves_focus_and_uses_blob_download() -> None:
    """Catch modal focus being hidden and HTTP navigation-based downloads."""

    creative_script = ROOT / "static/js/common/creative.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <main>
              <button id="outside">下载入口</button>
              <div id="creativeEpisodeDownloadModal" aria-hidden="false">
                <button id="confirm">下载全集</button>
              </div>
            </main>
            """
        )
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=creative_script)
        result = page.evaluate(
            """
            async () => {
              const methods = window.aiworkerModules.at(-1)();
              const modal = document.getElementById('creativeEpisodeDownloadModal');
              const notices = [];
              const state = Object.assign({}, methods, {
                creativeWorkspace: {
                  project: {id: 15},
                      episodeDownload: {
                        stage: 'script',
                        selectedEpisodes: [5],
                        allEpisodes: true,
                        format: 'txt',
                        templateId: 'classic'
                  }
                },
                modal() {
                  return {
                    hide() {
                      window.focusWasInsideWhenHidden = modal.contains(document.activeElement);
                      modal.setAttribute('aria-hidden', 'true');
                    }
                  };
                },
                showCreativeNotice(message, kind) { notices.push({message, kind}); }
              });
              let requestUrl = '';
              window.aiworkerApi = {
                async request(url) {
                  requestUrl = url;
                  return new Response(new Blob(['scene script']), {
                    status: 200,
                    headers: {
                      'content-disposition': "attachment; filename*=UTF-8''%E7%AC%AC5%E9%9B%86.txt"
                    }
                  });
                }
              };
              let downloaded = null;
              const originalClick = HTMLAnchorElement.prototype.click;
              HTMLAnchorElement.prototype.click = function () {
                downloaded = {href: this.href, filename: this.download};
              };
              document.getElementById('confirm').focus();
              try {
                await state.downloadSelectedEpisodes();
              } catch (error) {
                notices.push({message: error.message, kind: 'exception'});
              } finally {
                HTMLAnchorElement.prototype.click = originalClick;
              }
              return {
                focusWasInsideWhenHidden: window.focusWasInsideWhenHidden,
                activeElementId: document.activeElement.id,
                requestUrl,
                downloaded,
                notices
              };
            }
            """
        )

        assert result["focusWasInsideWhenHidden"] is False
        assert result["activeElementId"] != "confirm"
        assert result["requestUrl"] == (
            "/api/creative/projects/15/episodes/download?"
            "stage=script&episodes=all&template=classic&format=txt"
        )
        assert result["downloaded"]["href"].startswith("blob:")
        assert result["downloaded"]["filename"] == "第5集.txt"
        assert result["notices"] == []
        browser.close()


def test_screenplay_template_preview_uses_selected_template_and_sorted_episodes() -> None:
    creative_script = ROOT / "static/js/common/creative.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<main></main>")
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=creative_script)
        result = page.evaluate(
            """
            async () => {
              const methods = window.aiworkerModules.at(-1)();
              const state = Object.assign({}, methods, {
                creativeWorkspace: {
                  project: {id: 21},
                  episodeDownload: {
                    stage: 'script', selectedEpisodes: [3, 1, 3], allEpisodes: false,
                    templateId: 'wife_revenge', format: 'pdf', previewUrl: '',
                    previewLoading: false, previewError: ''
                  }
                }
              });
              let requestUrl = '';
              window.aiworkerApi = {
                async request(url) {
                  requestUrl = url;
                  return new Response('<!doctype html><title>preview</title>', {
                    status: 200, headers: {'content-type': 'text/html'}
                  });
                }
              };
              await state.previewSelectedEpisodes();
              const result = {
                requestUrl,
                previewUrl: state.creativeWorkspace.episodeDownload.previewUrl,
                previewError: state.creativeWorkspace.episodeDownload.previewError,
                previewLoading: state.creativeWorkspace.episodeDownload.previewLoading
              };
              state.clearScreenplayExportPreview();
              return result;
            }
            """
        )

        assert result["requestUrl"] == (
            "/api/creative/projects/21/episodes/export/preview?"
            "stage=script&episodes=1%2C3&template=wife_revenge"
        )
        assert result["previewUrl"].startswith("blob:")
        assert result["previewError"] == ""
        assert result["previewLoading"] is False
        browser.close()


def test_scene_script_history_selection_is_read_only() -> None:
    """A superseded script can be selected without becoming the editable head."""

    creative_script = ROOT / "static/js/common/creative.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<main></main>")
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=creative_script)
        result = page.evaluate(
            """
            () => {
              const methods = window.aiworkerModules.at(-1)();
              const oldScript = {
                id: 43, stage: 'script', episode_number: 1, version: 1, status: 'draft',
                upstream_refs: {beats_id: 21, beats_version: 1},
                content: {episode_title: '旧稿', scenes: []}
              };
              const currentBeats = {
                id: 1294, stage: 'beats', episode_number: 1, version: 3, status: 'locked',
                upstream_refs: {outline_batch_id: 'outline-current'}, content: {beats: []}
              };
              const state = Object.assign({}, methods, {
                creativeWorkspace: {
                  selectedEpisode: 1,
                  selectedScriptVersionId: '',
                  project: {
                    workflow_mode: 'sequential', outline_batch_id: 'outline-current',
                    episodes: [currentBeats, oldScript]
                  }
                }
              });

              const beforeSelection = state.selectedProjectScript();
              const historyIds = state.projectEpisodeHistoricalScripts(1).map(item => item.id);
              state.creativeWorkspace.selectedScriptVersionId = '43';
              const selectedOld = state.selectedProjectScript();
              const oldIsHistorical = state.selectedProjectScriptIsHistorical();
              const oldIsEditable = state.selectedProjectScriptEditable();

              const currentScript = {
                id: 1375, stage: 'script', episode_number: 1, version: 2, status: 'draft',
                upstream_refs: {beats_id: 1294, beats_version: 3},
                content: {episode_title: '当前稿', scenes: []}
              };
              state.creativeWorkspace.project.episodes.push(currentScript);
              state.resetProjectScriptVersionSelection();
              const selectedCurrent = state.selectedProjectScript();

              return {
                beforeSelection,
                historyIds,
                selectedOldId: selectedOld?.id,
                oldIsHistorical,
                oldIsEditable,
                selectedCurrentId: selectedCurrent?.id,
                currentIsHistorical: state.selectedProjectScriptIsHistorical(),
                currentIsEditable: state.selectedProjectScriptEditable()
              };
            }
            """
        )

        assert result == {
            "beforeSelection": None,
            "historyIds": [43],
            "selectedOldId": 43,
            "oldIsHistorical": True,
            "oldIsEditable": False,
            "selectedCurrentId": 1375,
            "currentIsHistorical": False,
            "currentIsEditable": True,
        }
        browser.close()


def test_scene_script_history_selector_renders_old_script_read_only() -> None:
    """The writing page exposes an old script even when no current script exists."""

    template = (
        ROOT / "templates/common/workspace/project_scripts.html"
    ).read_text(encoding="utf-8")
    creative_script = ROOT / "static/js/common/creative.js"
    alpine_script = ROOT / "static/js/alpine.min.js"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(f'<main x-data="appData()">{template}</main>')
        page.add_style_tag(path=ROOT / "static/css/bootstrap.min.css")
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=creative_script)
        page.evaluate(
            """
            () => {
              const methods = window.aiworkerModules.at(-1)();
              const oldScript = {
                id: 43, stage: 'script', episode_number: 1, version: 1, status: 'draft',
                upstream_refs: {beats_id: 21, beats_version: 1},
                generation_meta: {},
                content: {
                  episode_title: '旧版第一集',
                  scenes: [{
                    sequence_no: 1, scene_no: 1, time_of_day: '夜',
                    interior_exterior: '内', location: '旧客厅', props: [], characters: [],
                    blocks: [{type: 'action', text: '旧稿动作'}]
                  }]
                }
              };
              const currentBeats = {
                id: 1294, stage: 'beats', episode_number: 1, version: 3, status: 'locked',
                upstream_refs: {outline_batch_id: 'outline-current'}, content: {beats: []}
              };
              window.appData = () => Object.assign({}, methods, {
                creativeWorkspace: {
                  project: {
                    id: 9, target_episode_count: 1, workflow_mode: 'sequential',
                    outline_batch_id: 'outline-current', episodes: [currentBeats, oldScript]
                  },
                  selectedEpisode: 1,
                  selectedScriptVersionId: '',
                  videoParameters: {
                    record: null, analyzing: false, loading: false, confirming: '',
                    error: '', job: null, analysisDeadlineSeconds: 60
                  },
                  moduleCapabilities: {},
                  startingAction: '', activeAction: '', promptLibrary: {}
                },
                modal() { return null; },
                showCreativeNotice() {},
                creativeTemplateProvenance() { return ''; }
              });
            }
            """
        )
        page.add_script_tag(path=alpine_script)
        selector = page.locator("#project-script-version")
        selector.wait_for(state="visible")

        assert "第 1 集 · 有历史版本" in page.locator(
            r'select[x-model\.number="creativeWorkspace.selectedEpisode"] option'
        ).all_text_contents()
        assert selector.locator("option").all_text_contents() == [
            "选择历史版本",
            "历史 · v1 · Beats v1 · 草稿",
        ]
        selector.select_option("43")
        selected_state = page.evaluate(
            """
            () => {
              const data = Alpine.$data(document.querySelector('main'));
              return {
                selectedScriptVersionId: data.creativeWorkspace.selectedScriptVersionId,
                selectedScriptId: data.selectedProjectScript()?.id || null
              };
            }
            """
        )
        assert selected_state == {
            "selectedScriptVersionId": "43",
            "selectedScriptId": 43,
        }
        assert not errors
        title_input = page.locator('input[x-model*="episode_title"]')
        assert title_input.is_visible()
        assert title_input.input_value() == "旧版第一集"

        assert page.get_by_text("历史版本 · 只读", exact=True).is_visible()
        assert page.get_by_text("历史版本仅供查看", exact=False).is_visible()
        assert page.get_by_role("button", name="保存").is_disabled()
        assert page.get_by_role("button", name="完成并锁定", exact=True).is_disabled()
        assert title_input.is_disabled()
        assert page.get_by_role("link", name="下载此版本").is_visible()
        assert not errors
        browser.close()
