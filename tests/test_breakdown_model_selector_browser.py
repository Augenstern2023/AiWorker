# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Browser coverage for the model selector used by drama breakdown generation."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).parents[1]


def test_breakdown_generation_selects_and_submits_model_record() -> None:
    modal = (ROOT / "templates/common/workspace/breakdown_modals.html").read_text(
        encoding="utf-8"
    ).split('<div class="modal fade" id="breakdownEditorModal"', 1)[0]
    script = ROOT / "static/js/selection/breakdowns.js"
    alpine = ROOT / "static/js/alpine.min.js"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 800})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(f'<main x-data="appData()">{modal}</main>')
        page.add_style_tag(path=ROOT / "static/css/bootstrap.min.css")
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=script)
        page.evaluate(
            """
            () => {
              window.__breakdownRequests = [];
              window.aiworkerApi = {
                async request(url, options = {}) {
                  window.__breakdownRequests.push({url, body: options.body || null});
                  if (url === '/api/llm/available-models') {
                    return {
                      ok: true,
                      async json() {
                        return {
                          default_model_record_id: 'model-1',
                          items: [
                            {id: 'model-1', label: '默认模型', is_default: true,
                             reasoning_effort_options: []},
                            {id: 'model-2', label: '拆解专用模型', is_default: false,
                             reasoning_effort_options: [
                               {value: 'low', label: '低'},
                               {value: 'high', label: '高'}
                             ]}
                          ]
                        };
                      }
                    };
                  }
                  if (url === '/api/creative/breakdowns') {
                    return {
                      ok: true,
                      async json() {
                        return {
                          status: 'success',
                          coverage: [1],
                          requires_transcription: false,
                          job: {job_id: 'breakdown-job-1', status: 'running', message: '正在生成', progress: {}}
                        };
                      }
                    };
                  }
                  return {ok: false, async json() { return {detail: 'unexpected request'}; }};
                }
              };
              const methods = window.aiworkerModules.at(-1)();
              const modal = {
                show() {
                  const element = document.getElementById('breakdownGenerationProgressModal');
                  element.removeAttribute('aria-hidden');
                  element.style.display = 'block';
                  element.classList.add('show');
                },
                hide() {
                  const element = document.getElementById('breakdownGenerationProgressModal');
                  element.setAttribute('aria-hidden', 'true');
                  element.style.display = 'none';
                  element.classList.remove('show');
                }
              };
              window.appState = Object.assign({
                breakdownGeneration: {
                  topic: null, source: '', forceRegenerate: false, jobId: '', status: '',
                  message: '', progress: {}, coverage: [], requiresTranscription: false,
                  notifiedStatus: '', error: '', startedAt: 0, elapsedSeconds: 0,
                  logs: [], logCount: 0, showLogs: false, modelOptions: [],
                  modelRecordId: '', defaultModelRecordId: '',
                  reasoningEffortOptions: [], reasoningEffort: '',
                  modelsLoading: false
                },
                breakdownGenerationJobs: {},
                breakdownJobStatuses: {},
                breakdownGenerationProgressModal: modal,
                transcriptRequiredModal: {show() {}},
                breakdownEditorModal: {show() {}, hide() {}},
                topicLibrary: {error: '', breakdown: null},
                creative: {error: '', breakdown: null},
                jobs: [],
                async fetchTopicLibrary() {},
                async fetchCreativeTopics() {},
                async fetchJobs() {},
                openBreakdownEditor() {}
              }, methods);
              window.appData = () => window.appState;
            }
            """
        )
        page.add_script_tag(path=alpine)
        page.wait_for_function("window.Alpine && Alpine.$data(document.querySelector('main'))")

        page.evaluate(
            """
            () => Alpine.$data(document.querySelector('main')).startTranscriptBreakdown(
              {id: 7, title: '待拆解剧目'}, 'topic-library'
            )
            """
        )
        selector = page.locator("#breakdown-generation-model")
        selector.wait_for(state="visible")
        page.wait_for_function(
            "document.querySelectorAll('#breakdown-generation-model option').length === 3"
        )
        selector.select_option("model-2")
        effort_selector = page.locator("#breakdown-generation-reasoning-effort")
        effort_selector.select_option("high")
        page.get_by_role("button", name="开始生成").click()
        page.wait_for_function(
            "window.__breakdownRequests.some(item => item.url === '/api/creative/breakdowns')"
        )

        raw_body = page.evaluate(
            """
            () => window.__breakdownRequests.find(
              item => item.url === '/api/creative/breakdowns'
            ).body
            """
        )
        assert json.loads(raw_body) == {
            "topic_id": 7,
            "force_regenerate": False,
            "llm_model_record_id": "model-2",
            "reasoning_effort": "high",
        }
        assert selector.is_disabled()
        assert effort_selector.is_disabled()
        assert not errors
        browser.close()
