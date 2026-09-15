# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Browser coverage for the administrator LLM settings workspace."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ALPINE_SCRIPT = ROOT / "static/js/alpine.min.js"
BOOTSTRAP_STYLE = ROOT / "static/css/bootstrap.min.css"
WORKSPACE_STYLE = ROOT / "static/css/workspaces.css"
ADMIN_SCRIPT = ROOT / "static/js/admin/workspace.js"
TASKS_SCRIPT = ROOT / "static/js/common/tasks.js"
ADMIN_TEMPLATE = ROOT / "templates/admin/config.html"


SETTINGS_PAYLOAD = {
    "presets": {
        "openai": {
            "label": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "wire_api": "responses",
            "env_key": "OPENAI_API_KEY",
        },
        "deepseek": {
            "label": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "wire_api": "chat_completions",
            "env_key": "DEEPSEEK_API_KEY",
        },
        "minimax": {
            "label": "MiniMax",
            "base_url": "https://api.minimax.io/v1",
            "wire_api": "chat_completions",
            "env_key": "MINIMAX_API_KEY",
        },
        "sub2api": {
            "label": "Sub2API",
            "base_url": "",
            "wire_api": "responses",
            "env_key": "SUB2API_KEY",
        },
        "custom": {
            "label": "自定义",
            "base_url": "",
            "wire_api": "chat_completions",
            "env_key": "",
        },
    },
    "providers": [
        {
            "id": "provider-1",
            "name": "内部网关",
            "preset": "custom",
            "base_url": "http://models.example.test/v1",
            "wire_api": "chat_completions",
            "api_key_env_var": "INTERNAL_GATEWAY_KEY",
            "credential_status": "configured",
            "credential_source": "environment",
            "has_api_key": True,
            "security_warning": "该端点使用公网或局域网 HTTP，API Key 和请求内容可能被明文传输。",
            "is_enabled": True,
            "is_default": True,
            "models": [
                {
                    "id": "model-record-1",
                    "provider_id": "provider-1",
                    "model_id": "model-a",
                    "display_name": "Model A",
                    "source": "manual",
                    "is_enabled": True,
                    "is_default": True,
                }
            ],
        }
    ],
    "default_model_id": "model-record-1",
}


def _llm_state() -> dict:
    return {
        "loading": False,
        "saving": False,
        "syncing": False,
        "testing": False,
        "providerDeletingId": "",
        "modelSaving": False,
        "modelDeletingId": "",
        "defaultSavingId": "",
        "error": "",
        "notice": "",
        "presets": {},
        "providers": [],
        "defaultModelId": "",
        "selectedProviderId": "",
        "editor": {
            "id": "",
            "name": "",
            "preset": "custom",
            "base_url": "",
            "wire_api": "chat_completions",
            "api_key_env_var": "",
            "api_key": "",
            "credential_status": "missing",
            "credential_source": "missing",
            "has_api_key": False,
            "is_enabled": True,
        },
        "modelDraft": {"model_id": "", "display_name": ""},
        "modelEditor": None,
        "test": {"model_id": "", "result": None},
    }


def _mount_llm_page(page: Page, payload: dict = SETTINGS_PAYLOAD) -> None:
    page.set_default_timeout(3_000)
    template = ADMIN_TEMPLATE.read_text(encoding="utf-8")
    content = template.split("{% block content %}", 1)[1].split(
        '{% include "common/workspace/admin_config.html" %}', 1
    )[0]
    page.set_content(
        '<div id="ui-live-region" aria-live="polite"></div>'
        f'<main class="container-fluid" x-data="appData()">{content}</main>'
    )
    page.add_style_tag(path=BOOTSTRAP_STYLE)
    page.add_style_tag(path=WORKSPACE_STYLE)
    page.evaluate("window.aiworkerModules = []")
    page.add_script_tag(path=ADMIN_SCRIPT)
    page.evaluate(
        """
        ({payload, llmState}) => {
          window.requests = [];
          window.settingsPayload = structuredClone(payload);
          window.resolveLLMTest = null;
          window.settingsFetches = 0;
          window.credentialPutFailure = null;
          window.credentialDeleteFailure = null;
          window.testFailure = null;
          window.testSuccess = null;
          window.aiworkerApi = {
            async request(url, options = {}) {
              const body = options.body ? JSON.parse(options.body) : null;
              window.requests.push({url, method: options.method || 'GET', body});
              if (url === '/api/llm/settings') {
                window.settingsFetches += 1;
                return {ok: true, status: 200, async json() {
                  return structuredClone(window.settingsPayload);
                }};
              }
              if (url === '/api/llm/test') {
                return await new Promise(resolve => {
                  window.resolveLLMTest = () => resolve({
                    ok: !window.testFailure, status: window.testFailure ? 400 : 200, async json() {
                      return window.testFailure || window.testSuccess || {
                        status: 'success', message: '模型连接测试成功',
                        latency_ms: 37, response_preview: 'OK'
                      };
                    }
                  });
                });
              }
              if (url.endsWith('/credential') && options.method === 'PUT') {
                if (window.credentialPutFailure) {
                  return {ok: false, status: 503, async json() { return window.credentialPutFailure; }};
                }
                const providerId = url.split('/').at(-2);
                const provider = window.settingsPayload.providers.find(item => item.id === providerId);
                provider.credential_status = 'configured';
                provider.credential_source = 'credential_store';
                provider.has_api_key = true;
                return {ok: true, status: 200, async json() {
                  return {provider_id: providerId, credential_status: 'configured', credential_source: 'credential_store'};
                }};
              }
              if (url.endsWith('/credential') && options.method === 'DELETE') {
                if (window.credentialDeleteFailure) {
                  return {ok: false, status: 503, async json() { return window.credentialDeleteFailure; }};
                }
                const providerId = url.split('/').at(-2);
                const provider = window.settingsPayload.providers.find(item => item.id === providerId);
                provider.credential_status = 'configured';
                provider.credential_source = 'environment';
                provider.has_api_key = true;
                return {ok: true, status: 200, async json() {
                  return {provider_id: providerId, credential_status: 'configured', credential_source: 'environment'};
                }};
              }
              if (url.endsWith('/models/sync')) {
                return {ok: false, status: 400, async json() {
                  return {detail: '模型列表响应缺少 data 数组，请改用手工新增'};
                }};
              }
              if (url.startsWith('/api/llm/models/') && options.method === 'DELETE') {
                const recordId = url.split('/').at(-1);
                for (const provider of window.settingsPayload.providers || []) {
                  provider.models = (provider.models || []).filter(model => model.id !== recordId);
                }
                return {ok: true, status: 200, async json() {
                  return {status: 'success', model_id: recordId};
                }};
              }
              if (url.startsWith('/api/llm/providers/') && options.method === 'DELETE') {
                const providerId = url.split('/').at(-1);
                window.settingsPayload.providers = (window.settingsPayload.providers || [])
                  .filter(provider => provider.id !== providerId);
                return {ok: true, status: 200, async json() {
                  return {status: 'success', provider_id: providerId};
                }};
              }
              if (url.startsWith('/api/llm/providers/') && options.method === 'PUT') {
                const providerId = url.split('/').at(-1);
                const provider = window.settingsPayload.providers.find(item => item.id === providerId);
                Object.assign(provider, body);
                return {ok: true, status: 200, async json() {
                  return {id: providerId};
                }};
              }
              return {ok: true, status: 200, async json() {
                return {
                  id: 'created-provider',
                  api_key_env_var: body?.api_key_env_var || '',
                  credential_status: 'configured',
                  credential_source: 'environment',
                  has_api_key: true
                };
              }};
            }
          };
          const module = window.aiworkerModules.at(-1)();
          window.appData = () => Object.assign({
            llmSettings: llmState,
            dramaAnalysis: {
              loading: false, interfaceTesting: false, error: '', interfaceTest: null,
              settings: {
                config_url: '', activation_code: '', has_activation_code: false,
                capture_root: 'E:\\\\hg\\\\raw\\\\video_urls',
                capture_service_url: 'http://127.0.0.1:8776',
                wait_timeout_seconds: 600
              }
            }
          }, module);
        }
        """,
        {"payload": payload, "llmState": _llm_state()},
    )
    page.add_script_tag(path=ALPINE_SCRIPT)


def test_llm_settings_load_master_detail_with_environment_credential() -> None:
    """Would catch variable names being hidden or a broken mobile layout."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _mount_llm_page(page)

        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )
        page.get_by_role("heading", name="LLM 模型管理").wait_for()

        assert page.get_by_text("内部网关", exact=True).is_visible()
        assert page.get_by_label("模型列表").get_by_text("Model A", exact=True).is_visible()
        assert page.get_by_label("API Key 环境变量").input_value() == (
            "INTERNAL_GATEWAY_KEY"
        )
        api_key = page.get_by_label("API Key", exact=True)
        assert api_key.get_attribute("type") == "password"
        assert api_key.get_attribute("autocomplete") == "new-password"
        assert api_key.input_value() == ""
        assert page.get_by_text("环境变量", exact=True).is_visible()
        assert page.get_by_text("可能被明文传输", exact=False).is_visible()
        assert not page.locator("#llm-settings-error").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        browser.close()


def test_llm_settings_visibility_updates_do_not_raise_alpine_errors() -> None:
    """Would catch conditional panels corrupting Alpine's hide-transition queue."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        _mount_llm_page(page)

        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )
        page.get_by_text("内部网关", exact=True).wait_for()
        page.wait_for_timeout(100)

        assert not page_errors, "\n".join(page_errors)
        browser.close()


def test_llm_settings_marks_missing_environment_credential() -> None:
    """Would catch a missing environment value being shown as ready for use."""

    payload = deepcopy(SETTINGS_PAYLOAD)
    payload["providers"][0]["credential_status"] = "missing"
    payload["providers"][0]["has_api_key"] = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page, payload)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        assert page.get_by_text("凭据缺失", exact=True).is_visible()
        assert page.get_by_label("API Key 环境变量").input_value() == (
            "INTERNAL_GATEWAY_KEY"
        )
        browser.close()


def test_llm_provider_save_rotates_key_then_clears_the_password_field() -> None:
    """Would catch a rotation skipping the vault or retaining the secret in the UI."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        page.get_by_role("button", name="新增提供商").click()
        page.get_by_label("提供商预设").select_option("deepseek")
        assert page.get_by_label("Base URL").input_value() == "https://api.deepseek.com"
        assert page.get_by_label("调用协议").input_value() == "chat_completions"
        assert page.get_by_label("API Key 环境变量").input_value() == (
            "DEEPSEEK_API_KEY"
        )
        assert page.get_by_label("API Key", exact=True).input_value() == ""

        page.get_by_text("内部网关", exact=True).click()
        page.get_by_label("API Key", exact=True).fill("  rotated-secret  ")
        page.get_by_role("button", name="保存提供商").click()
        page.wait_for_function(
            "window.requests.some(item => item.url === '/api/llm/providers/provider-1/credential')"
        )

        request = page.evaluate(
            "window.requests.find(item => item.url === '/api/llm/providers/provider-1/credential')"
        )
        assert request["method"] == "PUT"
        assert request["body"] == {"api_key": "  rotated-secret  "}
        assert page.get_by_label("API Key", exact=True).input_value() == ""
        assert page.get_by_text("新密钥已生效", exact=True).is_visible()
        assert page.get_by_text("系统凭据库", exact=True).is_visible()
        browser.close()


def test_llm_connection_test_uses_unsaved_fields_and_announces_result() -> None:
    """Would catch the test action using stale saved values or lacking progress feedback."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        page.get_by_label("Base URL").fill("https://draft.example.test/v1")
        page.get_by_label("API Key 环境变量").fill("DRAFT_API_KEY")
        page.get_by_label("API Key", exact=True).fill("  temporary-secret  ")
        page.get_by_label("测试模型 ID").fill("draft-model")
        test_button = page.get_by_role("button", name="测试连接")
        test_button.click()
        page.wait_for_function("window.resolveLLMTest !== null")
        assert test_button.is_disabled()
        assert page.get_by_text("测试中…", exact=True).is_visible()

        request = page.evaluate(
            "window.requests.find(item => item.url === '/api/llm/test')"
        )
        assert request["body"] == {
            "provider_id": "provider-1",
            "base_url": "https://draft.example.test/v1",
            "api_key_env_var": "DRAFT_API_KEY",
            "api_key": "  temporary-secret  ",
            "model_id": "draft-model",
            "wire_api": "chat_completions",
        }

        page.evaluate("window.resolveLLMTest()")
        page.get_by_text("使用新密钥测试成功", exact=True).wait_for()
        assert page.get_by_label("API Key", exact=True).input_value() == ""
        assert not page.get_by_text("37 ms", exact=False).is_visible()
        assert not page.get_by_text("OK", exact=True).is_visible()
        browser.close()


def test_llm_credential_put_failure_refreshes_metadata_and_redacts_server_detail() -> None:
    """Would catch a failed rotation leaving stale state or reflecting a server secret."""

    payload = deepcopy(SETTINGS_PAYLOAD)
    payload["providers"][0]["is_default"] = False
    payload["providers"][0]["models"][0]["is_default"] = False
    payload["default_model_id"] = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page, payload)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )
        page.evaluate(
            "window.credentialPutFailure = {detail: 'encoded-secret-fragment: c2VjcmV0'}"
        )

        page.get_by_label("提供商名称").fill("已保存但密钥失败")
        page.get_by_label("API Key", exact=True).fill("fresh-secret")
        page.get_by_role("button", name="保存提供商").click()
        page.wait_for_function("window.settingsFetches >= 2")

        assert page.get_by_text(
            "提供商设置已保存，但新密钥未保存，请重试", exact=True
        ).is_visible()
        assert "encoded-secret-fragment" not in page.locator("body").inner_text()
        assert "c2VjcmV0" not in page.locator("body").inner_text()
        assert page.get_by_label("API Key", exact=True).input_value() == ""
        browser.close()


def test_llm_sensitive_request_errors_are_fixed_and_controls_lock_during_test() -> None:
    """Would catch secret-reflecting errors or edits while a test has an in-flight key."""

    payload = deepcopy(SETTINGS_PAYLOAD)
    payload["providers"][0]["credential_source"] = "credential_store"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page, payload)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        page.get_by_label("API Key", exact=True).fill("temporary-secret")
        page.get_by_role("button", name="编辑模型 Model A").click()
        page.get_by_label("测试模型 ID").fill("model-a")
        page.get_by_role("button", name="测试连接").click()
        page.wait_for_function("window.resolveLLMTest !== null")
        assert page.locator(".list-group-item-action").first.is_disabled()
        assert page.get_by_role("button", name="保存提供商").is_disabled()
        assert page.get_by_role("button", name="测试连接").is_disabled()
        assert page.get_by_role("button", name="清除页面密钥").is_disabled()
        assert page.get_by_label("提供商名称").is_disabled()
        assert page.get_by_label("API Key", exact=True).is_disabled()
        assert page.locator("#llm-edit-model-id-model-record-1").is_disabled()
        assert page.locator("#llm-edit-model-name-model-record-1").is_disabled()
        assert page.get_by_label("启用", exact=True).is_disabled()
        assert page.get_by_role("button", name="保存", exact=True).is_disabled()
        assert page.get_by_role("button", name="取消", exact=True).is_disabled()

        page.evaluate(
            "window.testFailure = {detail: 'truncated key: temporar', message: 'encoded dGVtcG9yYXJ5LXNlY3JldA=='}; window.resolveLLMTest()"
        )
        page.locator("#llm-settings-error").get_by_text(
            "使用新密钥测试失败，请检查密钥或网络设置", exact=True
        ).wait_for()
        body_text = page.locator("body").inner_text()
        assert "temporar" not in body_text
        assert "dGVtcG9yYXJ5LXNlY3JldA==" not in body_text

        page.evaluate(
            "window.credentialDeleteFailure = {detail: 'encoded dGVtcG9yYXJ5LXNlY3JldA=='}"
        )
        page.get_by_role("button", name="清除页面密钥").click()
        page.get_by_text("清除页面密钥失败，请重试", exact=True).wait_for()
        assert "dGVtcG9yYXJ5LXNlY3JldA==" not in page.locator("body").inner_text()
        browser.close()


def test_llm_temporary_key_success_discards_untrusted_response_data() -> None:
    """Would catch a successful temporary-key test reflecting server-controlled secrets."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )
        page.evaluate(
            "window.testSuccess = {status: 'success', message: 'temporary-secret', response_preview: 'temporary-secret%20truncated-temporar', latency_ms: 37, diagnostic: 'dGVtcG9yYXJ5LXNlY3JldA=='}"
        )

        page.get_by_label("API Key", exact=True).fill("temporary-secret")
        page.get_by_label("测试模型 ID").fill("model-a")
        page.get_by_role("button", name="测试连接").click()
        page.wait_for_function("window.resolveLLMTest !== null")
        page.evaluate("window.resolveLLMTest()")
        page.get_by_text("使用新密钥测试成功", exact=True).wait_for()

        body_text = page.locator("body").inner_text()
        state_text = page.evaluate(
            "JSON.stringify(document.querySelector('main')._x_dataStack[0].llmSettings.test.result)"
        )
        for forbidden in (
            "temporary-secret",
            "temporary-secret%20",
            "truncated-temporar",
            "dGVtcG9yYXJ5LXNlY3JldA==",
            "response_preview",
            "latency_ms",
        ):
            assert forbidden not in body_text
            assert forbidden not in state_text
        assert state_text == '{"status":"success","message":"使用新密钥测试成功"}'
        browser.close()


def test_llm_sync_failure_keeps_manual_model_fallback_available() -> None:
    """Would catch synchronization errors hiding the manual model workflow."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        page.get_by_role("button", name="同步模型").click()
        page.get_by_text("请改用手工新增", exact=False).wait_for()

        page.get_by_label("手工模型 ID").fill("manual-fallback")
        page.get_by_label("模型显示名").fill("Manual Fallback")
        page.get_by_role("button", name="添加模型").click()
        page.wait_for_function(
            "window.requests.some(item => item.url.endsWith('/models') && !item.url.endsWith('/models/sync'))"
        )
        request = page.evaluate(
            "window.requests.find(item => item.url.endsWith('/models') && !item.url.endsWith('/models/sync'))"
        )
        assert request["method"] == "POST"
        assert request["body"] == {
            "model_id": "manual-fallback",
            "display_name": "Manual Fallback",
            "is_enabled": True,
        }
        browser.close()


def test_llm_default_items_cannot_be_disabled_before_switching_default() -> None:
    """Would catch the editor offering an invalid direct disable of active defaults."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        assert page.get_by_label("启用提供商").is_disabled()
        page.get_by_role("button", name="编辑模型 Model A").click()
        assert page.get_by_label("启用", exact=True).is_disabled()
        assert page.get_by_label("模型列表").get_by_text(
            "请先切换全局默认模型", exact=True
        ).is_visible()
        browser.close()


def test_llm_provider_update_keeps_empty_key_out_of_metadata_request() -> None:
    """Would catch an empty password field overwriting or transmitting credentials."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        payload = deepcopy(SETTINGS_PAYLOAD)
        payload["providers"][0]["is_default"] = False
        payload["providers"][0]["models"][0]["is_default"] = False
        payload["default_model_id"] = None
        _mount_llm_page(page, payload)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        page.get_by_label("API Key 环境变量").fill("UPDATED_GATEWAY_KEY")
        page.get_by_role("button", name="保存提供商").click()
        page.wait_for_function(
            "window.requests.some(item => item.url === '/api/llm/providers/provider-1' && item.method === 'PUT')"
        )

        request = page.evaluate(
            "window.requests.find(item => item.url === '/api/llm/providers/provider-1' && item.method === 'PUT')"
        )
        assert request["body"]["api_key_env_var"] == "UPDATED_GATEWAY_KEY"
        assert "api_key" not in request["body"]
        assert not page.evaluate(
            "window.requests.some(item => item.url.endsWith('/credential'))"
        )
        browser.close()


def test_llm_connection_test_omits_empty_password_and_clear_uses_credential_endpoint() -> None:
    """Would catch secret query-string leakage or treating an empty test key as a value."""

    payload = deepcopy(SETTINGS_PAYLOAD)
    payload["providers"][0]["credential_source"] = "credential_store"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page, payload)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        page.get_by_label("测试模型 ID").fill("model-a")
        page.get_by_role("button", name="测试连接").click()
        page.wait_for_function("window.resolveLLMTest !== null")
        test_request = page.evaluate(
            "window.requests.find(item => item.url === '/api/llm/test')"
        )
        assert "api_key" not in test_request["body"]
        page.evaluate("window.resolveLLMTest()")

        page.get_by_role("button", name="清除页面密钥").click()
        page.wait_for_function(
            "window.requests.some(item => item.url === '/api/llm/providers/provider-1/credential' && item.method === 'DELETE')"
        )
        clear_request = page.evaluate(
            "window.requests.find(item => item.url === '/api/llm/providers/provider-1/credential' && item.method === 'DELETE')"
        )
        assert clear_request["body"] is None
        assert clear_request["url"] == "/api/llm/providers/provider-1/credential"
        assert page.get_by_text("环境变量", exact=True).is_visible()
        browser.close()


def test_llm_model_can_be_edited_and_selected_as_global_default() -> None:
    """Would catch model maintenance controls targeting a model ID instead of its record."""

    payload = deepcopy(SETTINGS_PAYLOAD)
    payload["providers"][0]["models"].append(
        {
            "id": "model-record-2",
            "provider_id": "provider-1",
            "model_id": "model-b",
            "display_name": "Model B",
            "source": "synced",
            "is_enabled": True,
            "is_default": False,
        }
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_llm_page(page, payload)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        page.get_by_role("button", name="编辑模型 Model B").click()
        page.locator("#llm-edit-model-name-model-record-2").wait_for()
        assert not page.get_by_role("button", name="编辑模型 Model B").is_visible()
        page.locator("#llm-edit-model-name-model-record-2").fill("Model B Updated")
        page.locator("#llm-edit-model-name-model-record-2").press("Enter")
        page.wait_for_function(
            "window.requests.some(item => item.url === '/api/llm/models/model-record-2')"
        )
        update_request = page.evaluate(
            "window.requests.find(item => item.url === '/api/llm/models/model-record-2')"
        )
        assert update_request["body"] == {
            "model_id": "model-b",
            "display_name": "Model B Updated",
            "is_enabled": True,
        }

        page.get_by_role("button", name="设为默认模型 Model B").click()
        page.wait_for_function(
            "window.requests.some(item => item.url === '/api/llm/default-model')"
        )
        default_request = page.evaluate(
            "window.requests.find(item => item.url === '/api/llm/default-model')"
        )
        assert default_request["body"] == {"model_id": "model-record-2"}
        browser.close()


def test_llm_non_default_model_can_be_deleted_after_confirmation() -> None:
    payload = deepcopy(SETTINGS_PAYLOAD)
    payload["providers"][0]["models"].append(
        {
            "id": "model-record-2",
            "provider_id": "provider-1",
            "model_id": "broken-model",
            "display_name": "Broken Model",
            "source": "manual",
            "is_enabled": True,
            "is_default": False,
        }
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        _mount_llm_page(page, payload)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        assert page.get_by_role(
            "button", name="删除模型 Model A"
        ).is_disabled()
        page.get_by_role("button", name="删除模型 Broken Model").click()
        page.wait_for_function(
            "window.requests.some(item => item.url === '/api/llm/models/model-record-2' && item.method === 'DELETE')"
        )

        assert not page.get_by_text("Broken Model", exact=True).is_visible()
        page.get_by_text("模型 Broken Model 已删除", exact=True).wait_for()
        browser.close()


def test_llm_non_default_provider_can_be_deleted_after_confirmation() -> None:
    payload = deepcopy(SETTINGS_PAYLOAD)
    payload["providers"].append(
        {
            "id": "provider-2",
            "name": "备用网关",
            "preset": "custom",
            "base_url": "https://backup.example.test/v1",
            "wire_api": "chat_completions",
            "api_key_env_var": "BACKUP_GATEWAY_KEY",
            "credential_status": "configured",
            "credential_source": "environment",
            "has_api_key": True,
            "is_enabled": True,
            "is_default": False,
            "models": [
                {
                    "id": "model-record-2",
                    "provider_id": "provider-2",
                    "model_id": "backup-model",
                    "display_name": "Backup Model",
                    "source": "manual",
                    "is_enabled": True,
                    "is_default": False,
                }
            ],
        }
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        dialogs: list[str] = []

        def accept_dialog(dialog) -> None:
            dialogs.append(dialog.message)
            dialog.accept()

        page.on("dialog", accept_dialog)
        _mount_llm_page(page, payload)
        page.evaluate(
            "document.querySelector('main')._x_dataStack[0].fetchLLMSettings()"
        )

        assert page.get_by_role(
            "button", name="删除提供商 内部网关"
        ).is_disabled()
        page.get_by_text("备用网关", exact=True).click()
        page.get_by_role("button", name="删除提供商 备用网关").click()
        page.wait_for_function(
            "window.requests.some(item => item.url === '/api/llm/providers/provider-2' && item.method === 'DELETE')"
        )

        assert "1 个模型" in dialogs[0]
        page.get_by_text("提供商 备用网关 已删除", exact=True).wait_for()
        assert not page.get_by_text("备用网关", exact=True).is_visible()
        browser.close()


def test_admin_config_initialization_fetches_llm_settings() -> None:
    """Would catch the config page rendering an inert LLM section until manual refresh."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(3_000)
        page.set_content(
            '<body data-workspace-page="admin-config" x-data="appData()" x-init="init()">'
            '<main><span x-text="llmSettings.providers.length"></span></main></body>'
        )
        page.evaluate(
            """
            () => {
              window.requests = [];
              window.aiworkerModules = [];
              window.bootstrap = {
                Modal: {getOrCreateInstance() { return null; }},
                Tooltip: {getOrCreateInstance() { return null; }}
              };
              window.aiworkerApi = {
                async request(url) {
                  window.requests.push(url);
                  let payload = {};
                  if (url === '/api/config/form') payload = {root: {}, items: []};
                  else if (url === '/api/config/backups') payload = [];
                  else if (url === '/api/llm/settings') payload = {
                    presets: {}, providers: [{
                      id: 'runtime-provider', name: 'Runtime', preset: 'custom',
                      base_url: 'https://runtime.example.test/v1',
                      wire_api: 'chat_completions', api_key_env_var: 'RUNTIME_KEY',
                      credential_status: 'missing', credential_source: 'environment',
                      has_api_key: false,
                      is_enabled: true, models: []
                    }], default_model_id: null
                  };
                  else if (url === '/api/hongguo/drama/settings') payload = {};
                  return {ok: true, status: 200, async json() { return payload; }};
                }
              };
            }
            """
        )
        page.add_script_tag(path=TASKS_SCRIPT)
        page.add_script_tag(path=ADMIN_SCRIPT)
        page.add_script_tag(path=ALPINE_SCRIPT)

        page.wait_for_function(
            "window.requests.includes('/api/llm/settings') && document.body._x_dataStack?.[0]?.llmSettings.providers.length === 1"
        )
        assert set(page.evaluate("window.requests")) >= {
            "/api/config",
            "/api/config/form",
            "/api/config/backups",
            "/api/llm/settings",
            "/api/hongguo/drama/settings",
        }
        browser.close()
