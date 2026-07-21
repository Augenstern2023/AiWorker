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

from analysis.llm import (
    LLMError,
    OpenAICompatibleLLMProvider,
    build_responses_request_body,
    build_video_analysis_messages,
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

    async def unavailable(_messages, _json_mode):
        raise LLMError("Responses API returned an empty or non-JSON response.")

    async def chat_fallback(_messages, _json_mode):
        return '{"ok": true}'

    monkeypatch.setattr(provider, "_complete_responses_text", unavailable)
    monkeypatch.setattr(provider, "_complete_chat_completions_text", chat_fallback)

    assert asyncio.run(provider.complete_text([{"role": "user", "content": "test"}], json_mode=True)) == '{"ok": true}'


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
