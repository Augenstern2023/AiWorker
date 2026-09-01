# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def test_api_permission_error_preserves_backend_detail() -> None:
    script = (ROOT / "static/js/common/api.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<main></main>")
        page.evaluate(
            """
            () => {
              window.permissionMessages = [];
              window.addEventListener('aiworker:permission-denied', event => {
                window.permissionMessages.push(event.detail.message);
              });
              window.fetch = async () => new Response(
                JSON.stringify({detail: {
                  code: 'PROJECT_ACCESS_DENIED',
                  message: '成员关系已失效，不能编辑该项目'
                }}),
                {status: 403, headers: {'Content-Type': 'application/json'}}
              );
            }
            """
        )
        page.add_script_tag(content=script)
        result = page.evaluate(
            """
            async () => {
              try {
                await window.aiworkerApi.request('/api/creative/projects/7');
              } catch (error) {
                return {
                  message: error.message,
                  eventMessage: window.permissionMessages[0],
                  status: error.status,
                };
              }
              return null;
            }
            """
        )
        browser.close()

    assert result == {
        "message": "成员关系已失效，不能编辑该项目",
        "eventMessage": "成员关系已失效，不能编辑该项目",
        "status": 403,
    }


def test_write_request_refreshes_stale_csrf_token_and_retries_once() -> None:
    """Catch regressions that turn a stale page token into a false permission error."""

    script = (ROOT / "static/js/common/api.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<meta name="csrf-token" content="stale-csrf"><main></main>'
        )
        page.evaluate(
            """
            () => {
              window.csrfCalls = [];
              window.fetch = async (url, options = {}) => {
                const headers = new Headers(options.headers || {});
                window.csrfCalls.push({
                  url,
                  method: options.method || 'GET',
                  csrf: headers.get('X-CSRF-Token'),
                });
                if (url === '/api/auth/me') {
                  return new Response(JSON.stringify({csrf_token: 'fresh-csrf'}), {
                    status: 200,
                    headers: {'Content-Type': 'application/json'},
                  });
                }
                if (headers.get('X-CSRF-Token') === 'fresh-csrf') {
                  return new Response(JSON.stringify({status: 'success'}), {
                    status: 200,
                    headers: {'Content-Type': 'application/json'},
                  });
                }
                return new Response(JSON.stringify({detail: 'CSRF 校验失败'}), {
                  status: 403,
                  headers: {'Content-Type': 'application/json'},
                });
              };
            }
            """
        )
        page.add_script_tag(content=script)
        result = page.evaluate(
            """
            async () => {
              const response = await window.aiworkerApi.request(
                '/api/creative/projects/8/blueprints/generate',
                {method: 'POST'}
              );
              return {
                status: response.status,
                csrf: document.querySelector('meta[name="csrf-token"]').content,
                calls: window.csrfCalls,
              };
            }
            """
        )
        browser.close()

    assert result == {
        "status": 200,
        "csrf": "fresh-csrf",
        "calls": [
            {
                "url": "/api/creative/projects/8/blueprints/generate",
                "method": "POST",
                "csrf": "stale-csrf",
            },
            {"url": "/api/auth/me", "method": "GET", "csrf": None},
            {
                "url": "/api/creative/projects/8/blueprints/generate",
                "method": "POST",
                "csrf": "fresh-csrf",
            },
        ],
    }


def test_project_member_reuses_and_starts_another_members_open_task() -> None:
    script = (ROOT / "static/js/common/creative.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<main></main>")
        page.evaluate(
            """
            () => {
              window.aiworkerModules = [];
              window.memberStageCalls = [];
              window.aiworkerApi = {
                request: async (url, options = {}) => {
                  window.memberStageCalls.push({url, method: options.method || 'GET'});
                  if (url === '/api/workflow/tasks') {
                    return new Response(JSON.stringify([{
                      id: 'brief-task', task_type: 'brief', project_id: '7',
                      status: 'assigned', assignee_user_id: 'another-member'
                    }]), {status: 200});
                  }
                  if (url === '/api/workflow/tasks/brief-task/start') {
                    return new Response(JSON.stringify({
                      id: 'brief-task', task_type: 'brief', project_id: '7',
                      status: 'in_progress', assignee_user_id: 'another-member'
                    }), {status: 200});
                  }
                  return new Response(JSON.stringify({detail: 'unexpected request'}), {status: 500});
                }
              };
            }
            """
        )
        page.add_script_tag(content=script)
        result = page.evaluate(
            """
            async () => {
              const methods = window.aiworkerModules[0]();
              const task = await methods.resolveCreativeWorkflowTask('brief', 7);
              return {task, calls: window.memberStageCalls};
            }
            """
        )
        browser.close()

    assert result["task"]["status"] == "in_progress"
    assert result["calls"] == [
        {"url": "/api/workflow/tasks", "method": "GET"},
        {"url": "/api/workflow/tasks/brief-task/start", "method": "POST"},
    ]
