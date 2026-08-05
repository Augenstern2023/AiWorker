# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import json

import httpx
import config
import pytest

from analysis.llm import (
    LLMError,
    OpenAICompatibleLLMProvider,
    build_responses_request_body,
    build_video_analysis_messages,
    create_llm_provider_from_config,
    normalize_llm_analysis,
    parse_chat_completion_json,
    parse_json_object,
    parse_responses_text,
    resolve_llm_settings,
)


def test_parse_json_object_handles_markdown_fence():
    assert parse_json_object('```json\n{"summary": "摘要"}\n```') == {"summary": "摘要"}


def test_parse_json_object_uses_last_valid_object_after_think_block():
    text = '<think>example {"ok": true}</think>\n{"summary": "摘要"}'

    assert parse_json_object(text) == {"summary": "摘要"}


def test_parse_chat_completion_json_extracts_message_content():
    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"summary":"摘要","key_points":["一"],"tags":["标签"],"risk_flags":["风险"]}'
                }
            }
        ]
    }

    assert parse_chat_completion_json(payload)["summary"] == "摘要"


def test_complete_json_preserves_a_top_level_array_for_schema_specific_handling():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '[{"order": 1}]'}}]},
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(provider.complete_json([{"role": "user", "content": "test"}])) == [{"order": 1}]


def test_normalize_llm_analysis_stabilizes_fields():
    result = normalize_llm_analysis(
        {
            "summary": " 摘要 ",
            "key_points": ["一", ""],
            "tags": "旅行",
            "content_type": "vlog",
            "sentiment": "中性",
            "audience": "游客",
            "action_items": ["核实"],
            "risk_flags": ["隐私"],
        },
        provider="minimax",
    )

    assert result["summary"] == "摘要"
    assert result["key_points"] == ["一"]
    assert result["tags"] == ["旅行"]
    assert result["risk_flags"] == ["隐私"]
    assert result["provider"] == "minimax"


def test_build_video_analysis_messages_contains_json_payload():
    messages = build_video_analysis_messages({"title": "标题", "transcript": "正文"})

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "标题" in messages[1]["content"]


def test_parse_responses_text_reads_output_text():
    payload = {"output": [{"content": [{"type": "output_text", "text": "{\"summary\":\"ok\"}"}]}]}

    assert parse_responses_text(payload) == "{\"summary\":\"ok\"}"


def test_responses_provider_falls_back_to_chat_completions_when_gateway_returns_no_json(monkeypatch):
    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="test-model",
        wire_api="responses",
    )

    async def unavailable(_messages, _json_mode, _model=None):
        raise LLMError("Responses API returned an empty or non-JSON response.")

    async def chat_fallback(_messages, _json_mode, _model=None):
        return '{"ok": true}'

    monkeypatch.setattr(provider, "_complete_responses_text", unavailable)
    monkeypatch.setattr(provider, "_complete_chat_completions_text", chat_fallback)

    assert asyncio.run(provider.complete_text([{"role": "user", "content": "test"}], json_mode=True)) == '{"ok": true}'


def test_chat_completions_retries_transient_gateway_errors_before_succeeding():
    attempts = 0
    retries = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(502, request=request, text="Bad Gateway")
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="test-model",
        max_retries=2,
        retry_base_delay=0,
        transport=httpx.MockTransport(handler),
        retry_callback=lambda number, total, reason: retries.append((number, total, reason)),
    )

    result = asyncio.run(provider.complete_text([{"role": "user", "content": "test"}], json_mode=True))

    assert result == '{"ok": true}'
    assert attempts == 3
    assert [(number, total) for number, total, _ in retries] == [(1, 2), (2, 2)]
    assert all("502" in reason for _, _, reason in retries)


def test_chat_completions_passes_reasoning_effort_to_compatible_gateway():
    request_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="deepseek-v4-pro",
        reasoning_effort="max",
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(provider.complete_text([{"role": "user", "content": "test"}], json_mode=True)) == '{"ok": true}'
    assert request_body["reasoning_effort"] == "max"


def test_chat_completions_falls_back_to_configured_model_when_primary_is_unavailable():
    requested_models = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_models.append(json.loads(request.content)["model"])
        if requested_models[-1] == "deepseek-v4-pro":
            return httpx.Response(503, request=request, text="model temporarily unavailable")
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="deepseek-v4-pro",
        fallback_model="deepseek-v4-flash",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(provider.complete_text([{"role": "user", "content": "test"}], json_mode=True)) == '{"ok": true}'
    assert requested_models == ["deepseek-v4-pro", "deepseek-v4-flash"]


def test_chat_completions_uses_secondary_provider_after_primary_fallback_is_unavailable():
    primary_models = []
    secondary_models = []

    def primary_handler(request: httpx.Request) -> httpx.Response:
        primary_models.append(json.loads(request.content)["model"])
        return httpx.Response(503, request=request, text="model temporarily unavailable")

    def secondary_handler(request: httpx.Request) -> httpx.Response:
        secondary_models.append(json.loads(request.content)["model"])
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    secondary = OpenAICompatibleLLMProvider(
        base_url="https://sub2api.example.test/v1",
        api_key="sub2-key",
        model="gpt-5.5",
        max_retries=0,
        transport=httpx.MockTransport(secondary_handler),
    )
    primary = OpenAICompatibleLLMProvider(
        base_url="https://yunwu.example.test/v1",
        api_key="yunwu-key",
        model="deepseek-v4-pro",
        fallback_model="deepseek-v4-flash",
        fallback_provider=secondary,
        max_retries=0,
        transport=httpx.MockTransport(primary_handler),
    )

    assert asyncio.run(primary.complete_text([{"role": "user", "content": "test"}], json_mode=True)) == '{"ok": true}'
    assert primary_models == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert secondary_models == ["gpt-5.5"]


def test_build_responses_request_body_sets_reasoning_effort():
    body = build_responses_request_body(
        "gpt-5.5",
        [{"role": "user", "content": "hi"}],
        json_mode=True,
        reasoning_effort="high",
    )

    assert body["model"] == "gpt-5.5"
    assert body["reasoning"] == {"effort": "high"}
    assert body["text"]["format"]["type"] == "json_object"


def test_resolve_llm_settings_supports_sub2api_env(monkeypatch):
    class DummyConfig:
        LLM_PROVIDER = "sub2api"
        LLM_BASE_URL = ""
        LLM_API_KEY = ""
        LLM_MODEL = ""

    monkeypatch.setenv("SUB2API_KEY", "secret")

    settings = resolve_llm_settings(DummyConfig)

    assert settings["provider"] == "sub2api"
    assert settings["base_url"] == "http://54.92.99.171:8080/v1"
    assert settings["api_key"] == "secret"
    assert settings["model"] == "gpt-5.5"
    assert settings["wire_api"] == "chat_completions"
    assert settings["reasoning_effort"] == "high"


def test_provider_uses_configured_request_timeout_for_long_generation():
    class DummyConfig:
        LLM_PROVIDER = "openai-compatible"
        LLM_ENV_KEY = "LLM_API_KEY"
        LLM_BASE_URL = "https://example.test/v1"
        LLM_API_KEY = "key"
        LLM_MODEL = "deepseek-v4-flash-0731"
        LLM_FALLBACK_MODEL = ""
        LLM_WIRE_API = "chat_completions"
        LLM_REASONING_EFFORT = "max"
        LLM_TIMEOUT_SECONDS = 600
        LLM_SECONDARY_PROVIDER = ""

    provider = create_llm_provider_from_config(DummyConfig)

    assert provider.timeout == 600


def test_chat_completions_reports_the_configured_timeout_after_retries():
    retries = []

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("", request=_request)

    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="test-model",
        timeout=600,
        max_retries=1,
        retry_base_delay=0,
        transport=httpx.MockTransport(handler),
        retry_callback=lambda number, total, reason: retries.append((number, total, reason)),
    )

    with pytest.raises(LLMError, match="模型响应超时（600 秒）"):
        asyncio.run(provider.complete_text([{"role": "user", "content": "test"}]))

    assert retries == [(1, 1, "模型响应超时（600 秒）")]


def test_rag_llm_uses_the_configured_global_text_model():
    settings = resolve_llm_settings(config, prefix="RAG_LLM")

    assert settings["provider"] == "openai-compatible"
    assert settings["base_url"] == "https://yunwu.ai/v1"
    assert settings["model"] == "deepseek-v4-flash-0731"
    assert settings["fallback_model"] == ""
    assert settings["reasoning_effort"] == "max"
    assert settings["timeout_seconds"] == 600
