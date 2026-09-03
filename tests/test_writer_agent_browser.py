# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        current = browser.new_page(viewport={"width": 1280, "height": 900})
        current.set_default_timeout(2_000)
        yield current
        browser.close()


def mount_writer_agent(page: Page, *, session: dict) -> None:
    page.set_content(
        '<main id="writer-agent-workbench">'
        '<section id="writer-agent-project-context">'
        '<button id="writer-agent-create-project" type="button">创建原创项目</button>'
        '<button id="writer-agent-link-project" type="button">关联已有项目</button>'
        '<div id="writer-agent-project-picker" hidden></div></section>'
        '<aside id="writer-agent-project-progress"></aside>'
        '<section id="writer-agent-messages"></section>'
        '<form id="writer-agent-message-form">'
        '<textarea id="writer-agent-message"></textarea>'
        '<button type="submit">发送</button></form>'
        '<aside id="writer-project-review-drawer" hidden aria-hidden="true">'
        '<button id="writer-project-review-close" type="button">关闭</button>'
        '<div id="writer-project-review-content"></div></aside></main>'
    )
    page.add_script_tag(
        content="""
        window.__writerActionRequests = [];
        window.aiworkerApi = {
          async request(url, options = {}) {
            window.__writerActionRequests.push({
              url, method: options.method || 'GET',
              body: JSON.parse(options.body || '{}')
            });
            return {id: url.split('/').at(-2), status: 'queued'};
          }
        };
        """
    )
    page.add_script_tag(path=ROOT / "static/js/review/writer_project_review.js")
    page.add_script_tag(path=ROOT / "static/js/review/writer_agent.js")
    page.evaluate(
        "session => window.writerAgentWorkbench.mount(session)", session
    )


def mount_writer_agent_controller_without_sessions(page: Page) -> None:
    page.set_content(
        '<main id="writer-agent-workbench">'
        '<div id="writer-agent-error" class="d-none"></div>'
        '<div class="writer-agent-layout">'
        '<aside><button id="writer-agent-new" type="button">新建会话</button>'
        '<button id="writer-agent-refresh" type="button">刷新会话</button>'
        '<div id="writer-agent-sessions"></div>'
        '<section id="writer-agent-session-detail" class="d-none">'
        '<div id="writer-agent-session-title"></div>'
        '<div id="writer-agent-material-name"></div></section></aside>'
        '<section><div id="writer-agent-project-context"></div>'
        '<div id="writer-agent-empty"></div>'
        '<div id="writer-agent-conversation" class="d-none"></div>'
        '<textarea id="writer-agent-message"></textarea>'
        '<input id="writer-agent-file" type="file">'
        '<button id="writer-agent-attach" type="button">上传</button>'
        '<span id="writer-agent-file-name"></span>'
        '<button id="writer-agent-send" type="button">发送</button></section>'
        '<aside><button id="writer-agent-toggle-reports" type="button">'
        '<i></i></button><span id="writer-agent-report-status"></span>'
        '<div id="writer-agent-reports"></div></aside></div>'
        '<aside id="writer-project-review-drawer" hidden aria-hidden="true">'
        '<button id="writer-project-review-close" type="button">关闭</button>'
        '<div id="writer-project-review-content"></div></aside></main>'
    )
    page.add_script_tag(
        content="""
        window.__writerControllerRequests = [];
        window.aiworkerApi = {
          async request(url, options = {}) {
            const body = JSON.parse(options.body || '{}');
            window.__writerControllerRequests.push({
              url, method: options.method || 'GET', body
            });
            let payload;
            if (url.startsWith('/api/creative/projects')) {
              payload = {items: [{id: 23, name: '可关联项目'}]};
            } else if (url === '/api/writer-agent/sessions' && !options.method) {
              payload = {items: []};
            } else if (url === '/api/writer-agent/sessions') {
              payload = {
                id: 41, title: body.title, content: body.content,
                filename: '', project_id: null,
                messages: [{
                  kind: 'material', role: 'user',
                  material_name: body.title, material_length: body.content.length,
                  material_preview: body.content, display_user_message: true
                }], actions: [], reports: []
              };
            } else if (url === '/api/writer-agent/sessions/41/messages') {
              payload = {
                id: 41, title: '创建原创项目', content: '创建原创项目',
                filename: '', project_id: null,
                messages: [{
                  kind: 'material', role: 'user', material_name: '创建原创项目',
                  material_length: 6, material_preview: '创建原创项目',
                  display_user_message: true
                }, window.__completeProjectReviewOffer
                  ? {role: 'assistant', kind: 'project_review_offer', audit_scope: 'outline', content: '请确认冻结内容'}
                  : {role: 'assistant', content: '请选择原创方向'}],
                actions: [], reports: []
              };
            } else {
              throw new Error(`unexpected request: ${url}`);
            }
            const response = {ok: true, async json() { return payload; }};
            if (url.endsWith('/messages') && window.__rejectNextWriterMessageResponse) {
              window.__rejectNextWriterMessageResponse = false;
              throw new Error('消息提交失败');
            }
            if (url.endsWith('/messages') && window.__deferWriterMessageResponse) {
              return await new Promise((resolve) => {
                window.__resolveWriterMessageResponse = () => resolve(response);
              });
            }
            return response;
          }
        };
        """
    )
    page.add_script_tag(path=ROOT / "static/js/review/writer_project_review.js")
    page.add_script_tag(path=ROOT / "static/js/review/writer_agent.js")


def mount_writer_agent_controller_with_rollback_history(page: Page) -> None:
    page.set_content(
        '<main id="writer-agent-workbench">'
        '<div id="writer-agent-error" class="d-none"></div>'
        '<div class="writer-agent-layout">'
        '<aside><button id="writer-agent-new" type="button">新建会话</button>'
        '<button id="writer-agent-refresh" type="button">刷新会话</button>'
        '<div id="writer-agent-sessions"></div>'
        '<section id="writer-agent-session-detail" class="d-none">'
        '<div id="writer-agent-session-title"></div>'
        '<div id="writer-agent-material-name"></div></section></aside>'
        '<section><div id="writer-agent-project-context"></div>'
        '<div id="writer-agent-empty"></div>'
        '<div id="writer-agent-conversation" class="d-none"></div>'
        '<textarea id="writer-agent-message"></textarea>'
        '<input id="writer-agent-file" type="file">'
        '<button id="writer-agent-attach" type="button">上传</button>'
        '<span id="writer-agent-file-name"></span>'
        '<button id="writer-agent-send" type="button">发送</button></section>'
        '<aside><button id="writer-agent-toggle-reports" type="button"><i></i></button>'
        '<span id="writer-agent-report-status"></span>'
        '<div id="writer-agent-reports"></div></aside></div>'
        '<dialog id="writer-turn-change-dialog" aria-labelledby="writer-turn-change-title">'
        '<h2 id="writer-turn-change-title"></h2>'
        '<p id="writer-turn-change-summary"></p>'
        '<button id="writer-turn-change-cancel" type="button">取消</button>'
        '<button id="writer-turn-change-confirm" type="button">确认</button>'
        '</dialog>'
        '<aside id="writer-project-review-drawer" hidden aria-hidden="true">'
        '<button id="writer-project-review-close" type="button">关闭</button>'
        '<div id="writer-project-review-content"></div></aside></main>'
    )
    page.add_script_tag(
        content="""
        window.__writerRollbackRequests = [];
        window.__writerRollbackSession = {
          id: 41, title: '历史回档', content: '原始材料', filename: '',
          project_id: null, history_revision: 2, reports: [], actions: [],
          messages: [
            {kind: 'material', role: 'user', message_id: 'material-1',
             material_name: '原始材料', material_length: 4,
             material_preview: '原始材料'},
            {role: 'user', content: '修改第一幕', message_id: 'message-1',
             turn_controls: {can_rollback: true, can_resend: true, blocked_reason: ''}},
            {role: 'assistant', content: '已完成修改'}
          ]
        };
        window.aiworkerApi = {
          async request(url, options = {}) {
            window.__writerRollbackRequests.push({
              url, method: options.method || 'GET',
              body: JSON.parse(options.body || '{}')
            });
            let payload;
            if (url === '/api/writer-agent/sessions' && !options.method) {
              payload = {items: [window.__writerRollbackSession]};
            } else if (url === '/api/writer-agent/sessions/41') {
              payload = window.__writerRollbackSession;
            } else if (url.endsWith('/rollback-preview')) {
              payload = {
                history_revision: 2, messages_to_remove: 3,
                reports_to_remove: 0, actions_to_rollback: 1,
                project_changes_to_rollback: 2, running_actions: 0,
                can_rollback: true, blockers: []
              };
            } else if (url.endsWith('/rollback') || url.endsWith('/resend')) {
              if (window.__rejectNextRollbackResponse) {
                window.__rejectNextRollbackResponse = false;
                const code = window.__rejectNextRollbackCode || 'ROLLBACK_PARTIAL_FAILURE';
                const message = code === 'ROLLBACK_PARTIAL_FAILURE'
                  ? '回档已部分完成，请使用相同请求重试'
                  : '项目内容已从其他入口更新，不能覆盖';
                return {ok: false, async json() {
                  return {detail: {code, message}};
                }};
              }
              if (window.__deferRollbackResponse) {
                return await new Promise((resolve) => {
                  window.__resolveRollbackResponse = () => resolve({
                    ok: true,
                    async json() {
                      return {...window.__writerRollbackSession,
                        history_revision: 3,
                        messages: [window.__writerRollbackSession.messages[0]]};
                    }
                  });
                });
              }
              payload = {
                ...window.__writerRollbackSession, history_revision: 3,
                messages: [window.__writerRollbackSession.messages[0]]
              };
              window.__writerRollbackSession = payload;
            } else {
              throw new Error(`unexpected request: ${url}`);
            }
            return {ok: true, async json() { return payload; }};
          }
        };
        """
    )
    page.add_script_tag(path=ROOT / "static/js/review/writer_project_review.js")
    page.add_script_tag(path=ROOT / "static/js/review/writer_agent.js")
    page.get_by_text("修改第一幕").wait_for()


def test_message_rollback_controls_preview_before_mutating_history(
    page: Page,
) -> None:
    """Would catch a history control that mutates immediately without impact review."""

    mount_writer_agent_controller_with_rollback_history(page)

    page.get_by_role("button", name="回档到这条消息之前").click()

    page.get_by_text("将撤销 3 条消息、1 个动作、2 项项目内容。").wait_for()
    requests = page.evaluate("window.__writerRollbackRequests")
    assert requests[-1]["url"].endswith("/message-1/rollback-preview")
    assert requests[-1]["method"] == "GET"
    page.get_by_role("button", name="取消", exact=True).click()
    assert not any(request["method"] == "POST" for request in requests)


def test_message_resend_uses_server_endpoint_and_refreshes_the_whole_session(
    page: Page,
) -> None:
    """Would catch resend appending a duplicate locally instead of server rollback."""

    mount_writer_agent_controller_with_rollback_history(page)
    page.get_by_role("button", name="重新发送这条消息").click()
    page.get_by_role("button", name="确认重新发送").click()
    page.wait_for_function(
        "window.__writerRollbackRequests.some(item => item.url.endsWith('/resend') && item.method === 'POST')"
    )

    requests = page.evaluate("window.__writerRollbackRequests")
    resend = next(request for request in requests if request["url"].endswith("/resend"))
    assert resend["body"]["expected_history_revision"] == 2
    assert resend["body"]["client_request_id"]
    assert page.get_by_text("修改第一幕").count() == 0


def test_message_rollback_retry_reuses_request_id_after_partial_failure(
    page: Page,
) -> None:
    """Would catch a partial rollback retry starting a conflicting new operation."""

    mount_writer_agent_controller_with_rollback_history(page)
    page.evaluate("window.__rejectNextRollbackResponse = true")

    for attempt in range(2):
        page.get_by_role("button", name="回档到这条消息之前").click()
        page.get_by_role("button", name="确认回档").click()
        page.wait_for_function(
            f"window.__writerRollbackRequests.filter(item => item.url.endsWith('/rollback') && item.method === 'POST').length === {attempt + 1}"
        )
        if attempt == 0:
            page.get_by_text("回档已部分完成，请使用相同请求重试").wait_for()

    requests = page.evaluate(
        "window.__writerRollbackRequests.filter(item => item.url.endsWith('/rollback') && item.method === 'POST')"
    )
    assert len(requests) == 2
    assert requests[0]["body"]["client_request_id"] == requests[1]["body"]["client_request_id"]


def test_message_rollback_conflict_uses_new_request_id_for_fresh_preview(
    page: Page,
) -> None:
    """Would catch a resolved conflict being trapped behind its old idempotency key."""

    mount_writer_agent_controller_with_rollback_history(page)
    page.evaluate(
        """
        window.__rejectNextRollbackResponse = true;
        window.__rejectNextRollbackCode = 'ROLLBACK_CONFLICT';
        """
    )

    for attempt in range(2):
        page.get_by_role("button", name="回档到这条消息之前").click()
        page.get_by_role("button", name="确认回档").click()
        page.wait_for_function(
            "window.__writerRollbackRequests.filter(item => "
            "item.url.endsWith('/rollback') && item.method === 'POST').length "
            f"=== {attempt + 1}"
        )
        if attempt == 0:
            page.get_by_text("项目内容已从其他入口更新，不能覆盖").wait_for()

    requests = page.evaluate(
        "window.__writerRollbackRequests.filter(item => "
        "item.url.endsWith('/rollback') && item.method === 'POST')"
    )
    assert requests[0]["body"]["client_request_id"] != requests[1]["body"][
        "client_request_id"
    ]


def test_rollback_busy_state_disables_every_session_mutation_control(
    page: Page,
) -> None:
    """Would catch action or review buttons starting side effects during rollback."""

    mount_writer_agent_controller_with_rollback_history(page)
    page.evaluate(
        """
        window.__writerRollbackSession = {
          ...window.__writerRollbackSession,
          actions: [
            {id: 'action-1', status: 'pending', revision: 1,
             action_type: 'creative.run_plan', title: '生成大纲', summary: '确认生成'},
            {id: 'action-2', status: 'running', phase: 'cancelling', revision: 2,
             action_type: 'creative.run_plan', title: '生成剧本', summary: '正在取消'}
          ],
          messages: [...window.__writerRollbackSession.messages,
            {role: 'assistant', kind: 'project_review_offer', audit_scope: 'outline',
             content: '确认完整项目审核'}]
        };
        window.writerAgentWorkbench.mount(window.__writerRollbackSession);
        window.writerProjectReviewUI.renderMessage(
          document.querySelector('#writer-agent-conversation'),
          window.__writerRollbackSession.messages.at(-1),
          {session: window.__writerRollbackSession}
        );
        window.__deferRollbackResponse = true;
        """
    )

    page.get_by_role("button", name="回档到这条消息之前").click()
    page.get_by_role("button", name="确认回档").click()
    page.wait_for_function("Boolean(window.__resolveRollbackResponse)")

    rollback_button = page.get_by_role("button", name="回档到这条消息之前")
    assert rollback_button.get_attribute("aria-busy") == "true"
    assert "处理中" in rollback_button.inner_text()
    assert page.get_by_role("button", name="确认生成").is_disabled()
    assert page.get_by_role("button", name="取消", exact=True).is_disabled()
    assert page.get_by_role("button", name="查看本次加载内容").is_disabled()
    page.evaluate("window.__resolveRollbackResponse()")
    page.get_by_role("button", name="取消中").wait_for()
    assert page.get_by_role("button", name="取消中").is_disabled()


def test_create_original_project_starts_a_session_before_sending_message(
    page: Page,
) -> None:
    """Would catch the empty-state shortcut dereferencing activeSession.id."""

    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    mount_writer_agent_controller_without_sessions(page)
    page.wait_for_function("window.__writerControllerRequests.length === 1")
    page.evaluate("window.__deferWriterMessageResponse = true")

    page.get_by_role("button", name="创建原创项目", exact=True).click()
    page.wait_for_function("window.__writerControllerRequests.length === 3")

    assert page_errors == []
    assert page.locator(".writer-agent-message.is-user").count() == 1
    requests = page.evaluate("window.__writerControllerRequests")
    assert [request["url"] for request in requests] == [
        "/api/writer-agent/sessions",
        "/api/writer-agent/sessions",
        "/api/writer-agent/sessions/41/messages",
    ]
    assert requests[1]["method"] == "POST"
    assert requests[1]["body"] == {
        "title": "创建原创项目",
        "content": "创建原创项目",
    }
    assert requests[2]["body"]["message"] == "创建原创项目"
    assert requests[2]["body"]["display_user_message"] is False
    page.evaluate("window.__resolveWriterMessageResponse()")
    page.get_by_text("请选择原创方向").wait_for()
    assert page.locator(".writer-agent-message.is-user").count() == 1


def test_create_project_retry_reuses_bootstrap_message_without_duplication(
    page: Page,
) -> None:
    """Would catch a failed bootstrap retry becoming a second visible command."""

    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    mount_writer_agent_controller_without_sessions(page)
    page.wait_for_function("window.__writerControllerRequests.length === 1")
    page.evaluate("window.__rejectNextWriterMessageResponse = true")

    page.get_by_role("button", name="创建原创项目", exact=True).click()
    page.get_by_text("消息提交失败").wait_for()
    assert page.locator(".writer-agent-message.is-user").count() == 1

    page.get_by_role("button", name="创建原创项目", exact=True).click()
    page.wait_for_function("window.__writerControllerRequests.length === 4")
    page.get_by_text("请选择原创方向").wait_for()

    assert page_errors == []
    assert page.locator(".writer-agent-message.is-user").count() == 1
    requests = page.evaluate("window.__writerControllerRequests")
    first_message = requests[2]["body"]
    retried_message = requests[3]["body"]
    assert first_message["message_id"]
    assert retried_message["message_id"] == first_message["message_id"]
    assert first_message["display_user_message"] is False
    assert retried_message["display_user_message"] is False


def test_writer_agent_slow_reply_explains_that_request_is_still_bounded(
    page: Page,
) -> None:
    mount_writer_agent_controller_without_sessions(page)
    page.wait_for_function("window.__writerControllerRequests.length === 1")
    page.evaluate(
        """
        () => {
          window.__writerNow = Date.now();
          Date.now = () => window.__writerNow;
          window.setInterval = (callback) => {
            window.__writerThinkingTick = callback;
            return 71;
          };
          window.clearInterval = () => {};
          window.__deferWriterMessageResponse = true;
        }
        """
    )
    page.locator("#writer-agent-message").fill("请审核更新后的大纲")
    page.locator("#writer-agent-send").click()
    page.wait_for_function("window.__writerControllerRequests.length === 3")

    page.evaluate(
        "window.__writerNow += 61_000; window.__writerThinkingTick();"
    )

    page.get_by_text("模型响应较慢，仍在处理；超时后可直接重试").wait_for()


def test_link_project_starts_a_session_before_sending_project_id(
    page: Page,
) -> None:
    """Would catch the empty-state project picker dereferencing activeSession.id."""

    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    mount_writer_agent_controller_without_sessions(page)
    page.wait_for_function("window.__writerControllerRequests.length === 1")
    page.evaluate("window.__deferWriterMessageResponse = true")

    page.get_by_role("button", name="关联已有项目").click()
    page.get_by_role("button", name="可关联项目").click()
    page.wait_for_function("window.__writerControllerRequests.length === 4")

    assert page_errors == []
    assert page.locator(".writer-agent-message.is-user").count() == 1
    requests = page.evaluate("window.__writerControllerRequests")
    assert [request["url"] for request in requests] == [
        "/api/writer-agent/sessions",
        "/api/creative/projects?participating_only=true",
        "/api/writer-agent/sessions",
        "/api/writer-agent/sessions/41/messages",
    ]
    assert requests[2]["body"] == {
        "title": "关联已有项目",
        "content": "关联已有项目",
    }
    assert requests[3]["body"]["message"] == "关联已有项目"
    assert requests[3]["body"]["link_project_id"] == 23
    assert requests[3]["body"]["display_user_message"] is False
    page.evaluate("window.__resolveWriterMessageResponse()")
    page.get_by_text("请选择原创方向").wait_for()
    assert page.locator(".writer-agent-message.is-user").count() == 1


def test_link_project_retry_reuses_bootstrap_message_without_duplication(
    page: Page,
) -> None:
    """Would catch a failed project-link retry creating a second command."""

    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    mount_writer_agent_controller_without_sessions(page)
    page.wait_for_function("window.__writerControllerRequests.length === 1")
    page.evaluate("window.__rejectNextWriterMessageResponse = true")

    page.get_by_role("button", name="关联已有项目", exact=True).click()
    page.get_by_role("button", name="可关联项目").click()
    page.get_by_text("消息提交失败").wait_for()
    assert page.locator(".writer-agent-message.is-user").count() == 1

    page.get_by_role("button", name="关联已有项目", exact=True).click()
    page.get_by_role("button", name="可关联项目").click()
    page.wait_for_function("window.__writerControllerRequests.length === 6")
    page.get_by_text("请选择原创方向").wait_for()

    assert page_errors == []
    assert page.locator(".writer-agent-message.is-user").count() == 1
    requests = page.evaluate("window.__writerControllerRequests")
    first_message = requests[3]["body"]
    retried_message = requests[5]["body"]
    assert first_message["message_id"]
    assert retried_message["message_id"] == first_message["message_id"]
    assert first_message["display_user_message"] is False
    assert retried_message["display_user_message"] is False


def test_writer_agent_renders_project_context_and_confirms_action(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "project_id": 9,
            "project": {"id": 9, "name": "情绪公司", "current_stage": "outline"},
            "messages": [],
            "actions": [
                {
                    "id": "a1",
                    "action_type": "creative.run_plan",
                    "status": "pending",
                    "revision": 2,
                    "title": "生成第1-5集剧本",
                    "summary": "复用已锁定大纲",
                    "payload": {
                        "target_stage": "script",
                        "episode_start": 1,
                        "episode_end": 5,
                    },
                }
            ],
        },
    )

    assert page.locator("#writer-agent-project-context").inner_text().startswith(
        "情绪公司"
    )
    page.get_by_role("button", name="确认生成").click()
    request = page.evaluate("window.__writerActionRequests[0]")
    assert request["url"].endswith("/actions/a1/decision")
    assert request["body"]["expected_revision"] == 2


def test_writer_agent_collapses_successful_retry_chain_into_read_only_history(
    page: Page,
) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {
                    "id": "original",
                    "root_action_id": "original",
                    "status": "failed",
                    "retryable": True,
                    "title": "创建原创项目",
                    "completed_steps": 0,
                    "total_steps": 1,
                    "created_at": 10,
                },
                {
                    "id": "retry-1",
                    "root_action_id": "original",
                    "retry_of_action_id": "original",
                    "status": "succeeded",
                    "title": "创建原创项目",
                    "completed_steps": 1,
                    "total_steps": 1,
                    "created_at": 20,
                },
                {
                    "id": "retry-2",
                    "root_action_id": "original",
                    "retry_of_action_id": "original",
                    "status": "succeeded",
                    "title": "创建原创项目",
                    "completed_steps": 1,
                    "total_steps": 1,
                    "created_at": 30,
                },
            ],
        },
    )

    assert page.locator(".writer-action-card").count() == 1
    assert page.get_by_role("button", name="重试").count() == 0
    history = page.get_by_text("查看执行历史（2）")
    assert history.is_visible()
    history.click()
    assert page.locator(".writer-action-history-item").count() == 2
    assert page.locator(".writer-action-history-item button").count() == 0


def test_writer_agent_retries_only_the_latest_failed_action_in_a_chain(
    page: Page,
) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {
                    "id": "original",
                    "root_action_id": "original",
                    "status": "failed",
                    "retryable": True,
                    "title": "生成剧本",
                    "created_at": 10,
                },
                {
                    "id": "retry-latest",
                    "root_action_id": "original",
                    "retry_of_action_id": "original",
                    "status": "failed",
                    "retryable": True,
                    "title": "生成剧本",
                    "created_at": 20,
                },
            ],
        },
    )

    assert page.locator(".writer-action-card").count() == 1
    retry = page.get_by_role("button", name="重试")
    assert retry.count() == 1
    retry.click()
    page.wait_for_function("window.__writerActionRequests.length === 1")
    assert page.evaluate("window.__writerActionRequests[0].url").endswith(
        "/actions/retry-latest/retry"
    )


def test_multiple_action_cards_disable_only_the_clicked_card(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {"id": "a1", "action_type": "creative.run_plan", "status": "pending", "revision": 1, "title": "大纲"},
                {"id": "a2", "action_type": "creative.run_plan", "status": "pending", "revision": 1, "title": "剧本"},
            ],
        },
    )

    cards = page.locator(".writer-action-card")
    cards.nth(0).get_by_role("button", name="确认生成").click()
    assert cards.nth(1).get_by_role("button", name="确认生成").is_enabled()


def test_writer_agent_renders_model_text_without_executing_html(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {
                    "id": "a1",
                    "action_type": "creative.run_plan",
                    "status": "pending",
                    "revision": 1,
                    "title": '<img src=x onerror="window.__xss=1">',
                    "summary": "安全文本",
                }
            ],
        },
    )

    assert page.locator(".writer-action-card img").count() == 0
    assert page.evaluate("window.__xss || 0") == 0


def test_writer_agent_delegates_complete_project_review_messages_to_drawer_ui(page: Page) -> None:
    """Would catch project-review messages falling through the ordinary material/action path."""
    mount_writer_agent_controller_without_sessions(page)
    page.wait_for_function("window.__writerControllerRequests.length === 1")
    page.evaluate("window.__completeProjectReviewOffer = true")
    page.get_by_role("button", name="创建原创项目", exact=True).click()
    assert page.get_by_text("请确认冻结内容").is_visible()
    assert page.get_by_role("button", name="查看本次加载内容").is_visible()


def test_action_card_renders_server_ledger_progress_and_result_link(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [{
                "id": "ledger-action",
                "action_type": "creative.run_plan",
                "status": "succeeded",
                "revision": 4,
                "title": "生成第1-2集剧本",
                "summary": "共 2 个可审计步骤",
                "completed_steps": 2,
                "total_steps": 2,
                "resource_refs": [
                    {"resource_type": "script", "resource_id": "501"}
                ],
                "open_url": "/writing/projects",
            }],
        },
    )

    card = page.locator(".writer-action-card")
    assert card.get_by_text("2 / 2").is_visible()
    assert card.get_by_text("script").is_visible()
    assert card.get_by_role("link", name="打开成果").get_attribute("href") == (
        "/writing/projects"
    )


def test_writer_agent_has_no_horizontal_overflow_at_390px(page: Page) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {"id": "a1", "action_type": "creative.run_plan", "status": "pending", "revision": 1, "title": "生成全剧大纲"}
            ],
        },
    )

    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def test_writer_agent_ignores_server_owned_cards_in_message_history(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [
                {
                    "role": "assistant",
                    "content": "已整理创作方案",
                    "cards": [
                        {
                            "card_id": "directions-1",
                            "card_type": "directions",
                            "items": [
                                {
                                    "title": "情绪审判庭",
                                    "protagonist": "劳动仲裁律师",
                                    "background": "情绪量化城市",
                                    "core_conflict": "替同事夺回被清零的工资",
                                    "relationship_engine": "律师与前主管互相试探",
                                    "emotional_payoff": "普通人夺回情绪解释权",
                                    "difference_dimensions": ["protagonist", "conflict"],
                                }
                            ],
                        },
                        {
                            "card_id": "brief-1",
                            "card_type": "brief",
                            "content": {"title": "情绪公司", "logline": "愤怒会让工资归零"},
                            "assumptions": ["每集 2 分钟"],
                        },
                        {
                            "card_id": "plan-1",
                            "card_type": "plan",
                            "title": "生成全剧大纲",
                            "summary": "补齐创作基线",
                            "steps": [{"stage": "brief", "kind": "lock"}],
                        },
                        {
                            "card_id": "progress-1",
                            "card_type": "progress",
                            "action_id": "a1",
                            "status": "running",
                            "completed_steps": 2,
                            "total_steps": 5,
                        },
                        {
                            "card_id": "result-1",
                            "card_type": "result",
                            "project_id": 9,
                            "resource_refs": [{"resource_type": "outline", "version": 2}],
                            "open_url": "/writing/projects/9/outlines",
                        },
                        {
                            "card_id": "conflict-1",
                            "card_type": "conflict",
                            "code": "STALE_PROJECT_CONTEXT",
                            "message": "项目版本已经变化",
                            "current_revision": 12,
                        },
                    ],
                }
            ],
            "actions": [],
        },
    )

    cards = page.locator(".writer-creative-card")
    assert cards.count() == 4
    assert cards.nth(0).get_by_text("情绪审判庭").is_visible()
    assert cards.nth(1).get_by_text("愤怒会让工资归零").is_visible()
    assert cards.nth(2).get_by_text("补齐创作基线").is_visible()
    assert cards.nth(3).get_by_text("项目版本已经变化").is_visible()
    assert page.get_by_text("2 / 5").count() == 0
    assert page.get_by_role("link", name="打开项目").count() == 0
    assert cards.nth(3).get_by_role("button", name="重新生成计划").is_visible()


def test_writer_agent_project_context_shows_mode_stage_and_valid_entry(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "project_id": 9,
            "project": {
                "id": 9,
                "name": "情绪公司",
                "creation_mode": "original",
                "outline_batch_id": "outlines-v2",
            },
            "messages": [],
            "actions": [],
        },
    )

    context = page.locator("#writer-agent-project-context")
    assert "原创" in context.inner_text()
    assert "全剧大纲" in context.inner_text()
    assert context.get_by_role("link", name="打开项目").get_attribute("href") == "/writing/projects/9/outlines"


def test_writer_agent_empty_project_progress_keeps_dark_theme_text_readable(
    page: Page,
) -> None:
    """Would catch a light-theme text utility making the dark inspector unreadable."""
    mount_writer_agent(
        page,
        session={"id": 1, "project_id": None, "messages": [], "actions": []},
    )
    template = (ROOT / "templates/review/writer_agent.html").read_text(
        encoding="utf-8"
    )
    inline_styles = template.split("<style>", 1)[1].split("</style>", 1)[0]
    page.add_style_tag(path=ROOT / "static/css/bootstrap.min.css")
    page.add_style_tag(path=ROOT / "static/css/workspaces.css")
    page.add_style_tag(content=inline_styles)

    contrast = page.locator(".writer-project-progress-state").evaluate(
        """
        state => {
          const rgb = value => (value.match(/[\\d.]+/g) || []).slice(0, 3).map(Number);
          const luminance = value => {
            const channels = rgb(value).map(channel => {
              const normalized = channel / 255;
              return normalized <= 0.04045
                ? normalized / 12.92
                : ((normalized + 0.055) / 1.055) ** 2.4;
            });
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
          };
          const ratio = (foreground, background) => {
            const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
            return (values[0] + 0.05) / (values[1] + 0.05);
          };
          const background = getComputedStyle(document.body).backgroundColor;
          return {
            heading: ratio(getComputedStyle(state.querySelector('h5')).color, background),
            copy: ratio(getComputedStyle(state.querySelector('span')).color, background),
          };
        }
        """
    )

    assert contrast["heading"] >= 4.5
    assert contrast["copy"] >= 4.5
    assert contrast["heading"] > contrast["copy"]


def test_writer_agent_right_panel_shows_project_stage_and_episode_progress(
    page: Page,
) -> None:
    """Would catch leaving the desktop inspector blank after a project is linked."""
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "project_id": 11,
            "project": {
                "id": 11,
                "name": "跑不完的落日，疗不好的伤",
                "creation_mode": "original",
                "target_platform": "长篇小说",
                "target_episode_count": 100,
                "episode_duration_seconds": 900,
                "locked_brief_id": None,
                "character_batch_id": None,
                "outline_batch_id": None,
                "blueprints": [{"id": 37, "status": "draft"}],
                "characters": [],
                "episodes": [],
            },
            "messages": [],
            "actions": [],
        },
    )

    progress = page.locator("#writer-agent-project-progress")
    assert progress.get_by_role("heading", name="项目进度").is_visible()
    assert progress.get_by_text("目标 100 章").is_visible()
    assert progress.get_by_text("单章 15 分钟").is_visible()
    assert progress.get_by_text("方向 / Brief").is_visible()
    assert progress.get_by_text("1 个方向 · 待锁定").is_visible()
    assert progress.get_by_text("全剧大纲").is_visible()
    assert progress.get_by_text("0 / 100 章", exact=True).count() == 3
    assert progress.get_by_role("link", name="打开当前阶段").get_attribute(
        "href"
    ) == "/planning/projects/11/brief"


def test_writer_agent_progress_ignores_stale_versions_and_opens_active_episode(
    page: Page,
) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 2,
            "project_id": 12,
            "project": {
                "id": 12,
                "name": "当前权威链",
                "creation_mode": "original",
                "target_episode_count": 2,
                "locked_brief_id": 5,
                "character_batch_id": "characters-v2",
                "outline_batch_id": "outlines-v2",
                "blueprints": [],
                "characters": [],
                "episodes": [
                    {"id": 21, "stage": "outline", "episode_number": 1, "batch_id": "outlines-v2", "version": 2, "status": "locked"},
                    {"id": 22, "stage": "outline", "episode_number": 2, "batch_id": "outlines-v2", "version": 2, "status": "locked"},
                    {"id": 31, "stage": "beats", "episode_number": 1, "version": 2, "status": "locked", "upstream_refs": {"outline_batch_id": "outlines-v2"}},
                    {"id": 32, "stage": "beats", "episode_number": 2, "version": 2, "status": "draft", "upstream_refs": {"outline_batch_id": "outlines-v2"}},
                    {"id": 33, "stage": "beats", "episode_number": 2, "version": 8, "status": "locked", "upstream_refs": {"outline_batch_id": "outlines-v1"}},
                    {"id": 41, "stage": "script", "episode_number": 1, "version": 1, "status": "draft", "upstream_refs": {"beats_id": 31, "beats_version": 2}},
                    {"id": 42, "stage": "script", "episode_number": 2, "version": 4, "status": "locked", "upstream_refs": {"beats_id": 33, "beats_version": 8}},
                ],
            },
            "messages": [],
            "actions": [],
        },
    )

    progress = page.locator("#writer-agent-project-progress")
    assert progress.get_by_text("2 / 2 集", exact=True).count() == 2
    assert progress.get_by_text("1 / 2 集", exact=True).count() == 1
    assert progress.get_by_role("link", name="打开当前阶段").get_attribute(
        "href"
    ) == "/writing/projects/12/episodes/1/scripts"


def test_writer_agent_project_picker_submits_structured_project_id(page: Page) -> None:
    mount_writer_agent(
        page,
        session={"id": 1, "messages": [], "actions": []},
    )
    page.evaluate(
        """
        window.__linkedProjectIds = [];
        window.writerAgentWorkbench.configure({
          sendProjectLink(projectId) { window.__linkedProjectIds.push(projectId); }
        });
        """
    )
    page.evaluate(
        """
        () => {
          window.aiworkerApi.request = async (url, options = {}) => {
            window.__writerActionRequests.push({url, method: options.method || 'GET'});
            if (url.startsWith('/api/creative/projects')) {
              return {items: [{id: 23, name: '可关联项目'}]};
            }
            return {};
          };
        }
        """
    )

    page.get_by_role("button", name="关联已有项目").click()
    page.get_by_role("button", name="可关联项目").click()

    assert page.evaluate("window.__linkedProjectIds") == [23]
