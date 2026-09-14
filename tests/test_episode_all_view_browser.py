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
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=creative_script)
        page.evaluate(
            """
            ([stage, items]) => {
              const methods = window.aiworkerModules.at(-1)();
              window.appData = () => Object.assign({}, methods, {
                creativeWorkspace: {
                  project: {id: 9, target_episode_count: 2, workflow_mode: 'free'},
                  selectedEpisode: 0,
                  episodeDownload: {stage: '', selectedEpisodes: [], allEpisodes: true, format: 'md'},
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
                    format: 'txt'
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
            "stage=script&episodes=all&format=txt"
        )
        assert result["downloaded"]["href"].startswith("blob:")
        assert result["downloaded"]["filename"] == "第5集.txt"
        assert result["notices"] == []
        browser.close()
