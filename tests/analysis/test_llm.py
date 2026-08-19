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

from creative.prompt_templates import script_messages
from screenplay_optimization.service import optimize_episode
from screenplay_review.service import _review_messages
from writer_agent.service import writer_messages
from analysis.llm import (
    LLMError,
    LLMTimeoutError,
    OpenAICompatibleLLMProvider,
    build_responses_request_body,
    build_video_analysis_messages,
    create_llm_provider_from_config,
    normalize_llm_analysis,
    parse_chat_completion_json,
    parse_json_object,
    parse_responses_text,
    resolve_reasoning_effort,
    resolve_llm_settings,
    select_reasoning_effort,
)


def test_parse_json_object_handles_markdown_fence():
    assert parse_json_object('```json\n{"summary": "摘要"}\n```') == {"summary": "摘要"}


def test_parse_json_object_uses_last_valid_object_after_think_block():
    text = '<think>example {"ok": true}</think>\n{"summary": "摘要"}'

    assert parse_json_object(text) == {"summary": "摘要"}


def test_parse_json_object_repairs_unescaped_dialogue_quotes_from_a_model_response():
    text = '{"episode_title":"测试","line":"她说："现在走""}'

    assert parse_json_object(text) == {
        "episode_title": "测试",
        "line": '她说："现在走"',
    }


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


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("请从短文本中提取标题并分类。", "low"),
        ("请总结本周数据并给出常规分析。", "medium"),
        ("请完成人物设计、故事梗概和分集规划。", "high"),
        (
            "根据已锁定 Beats 生成可拍摄的场次剧本，返回 episode_title 和 scenes。",
            "high",
        ),
        ("请审核剧本逻辑，交叉核验人物动机和时间线。", "xhigh"),
        (
            "你是短剧编剧审稿助手。根据逐集审核和剧本，汇总审核报告。",
            "xhigh",
        ),
        ("请深度优化剧本并审核全部伏笔。" + "长上下文" * 8000, "max"),
    ],
    ids=[
        "extraction",
        "analysis",
        "creation",
        "production-script",
        "review",
        "script-editor-review",
        "deep-long-review",
    ],
)
def test_select_reasoning_effort_routes_representative_tasks(content, expected):
    messages = [{"role": "user", "content": content}]

    assert select_reasoning_effort(messages) == expected


def test_real_screenplay_generation_messages_use_high_reasoning():
    messages = script_messages(
        {"beats": [{"order": 1, "goal": "主角发现线索"}]},
        episode_number=1,
    )

    assert select_reasoning_effort(messages) == "high"


def test_real_short_project_review_messages_use_xhigh_reasoning():
    messages = _review_messages(
        {
            "formal": True,
            "episode_numbers": [1],
            "episodes": [
                {"episode_number": 1, "script": {"scenes": [{"summary": "开场"}]}}
            ],
        }
    )

    assert select_reasoning_effort(messages) == "xhigh"


def test_real_long_project_review_messages_use_max_reasoning():
    messages = _review_messages(
        {
            "formal": True,
            "episode_numbers": [1],
            "episodes": [
                {
                    "episode_number": 1,
                    "script": {"scenes": [{"content": "长剧本" * 11_000}]},
                }
            ],
        }
    )

    assert select_reasoning_effort(messages) == "max"


def test_real_screenplay_optimization_messages_use_xhigh_reasoning():
    class CapturingProvider:
        def __init__(self):
            self.messages = []

        async def complete_json(self, messages):
            self.messages = messages
            return {"script": {"episode_title": "优化稿", "scenes": []}}

    provider = CapturingProvider()

    asyncio.run(
        optimize_episode(
            provider,
            mode="dialogue",
            episode_number=1,
            script_content={"episode_title": "原稿", "scenes": []},
            beats_content=None,
            review_context={"summary": "节奏需加快"},
            findings=[{"title": "对白拖沓"}],
            summaries=[],
        )
    )

    assert select_reasoning_effort(provider.messages) == "xhigh"


def test_real_writer_review_messages_use_xhigh_reasoning():
    messages = writer_messages(
        "第 1 集：主角在雨夜发现账本。",
        [],
        "请评审这一集并给出修改建议",
        review_requested=True,
    )

    assert select_reasoning_effort(messages) == "xhigh"


@pytest.mark.parametrize(
    "configured", ["none", "low", "medium", "high", "xhigh", "max"]
)
def test_resolve_reasoning_effort_preserves_explicit_override(configured):
    messages = [
        {"role": "user", "content": "请深度优化剧本" + "长上下文" * 8000}
    ]

    assert resolve_reasoning_effort(configured, messages) == configured


def test_resolve_reasoning_effort_treats_invalid_configuration_as_auto():
    messages = [{"role": "user", "content": "请提取标题并分类"}]

    assert resolve_reasoning_effort("turbo", messages) == "low"


def test_parse_responses_text_reads_output_text():
    payload = {"output": [{"content": [{"type": "output_text", "text": "{\"summary\":\"ok\"}"}]}]}

    assert parse_responses_text(payload) == "{\"summary\":\"ok\"}"


def test_responses_provider_falls_back_to_chat_completions_when_gateway_returns_no_json():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return httpx.Response(200, request=request, text="gateway returned no JSON")
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1",
        api_key="key",
        model="test-model",
        wire_api="responses",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(provider.complete_text([{"role": "user", "content": "test"}], json_mode=True)) == '{"ok": true}'
    assert paths == ["/v1/responses", "/v1/chat/completions"]


@pytest.mark.parametrize("status", [404, 405, 415, 422])
def test_responses_unsupported_falls_back_to_same_sub2api_chat_completions(status):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/responses"):
            return httpx.Response(status, request=request, text="route not found")
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="https://sub2api.example.test/v1",
        api_key="key",
        model="gpt-5.6-sol",
        provider_name="sub2api",
        wire_api="responses",
        reasoning_effort="auto",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    messages = [{"role": "user", "content": "请完成人物设计和分集规划"}]

    assert asyncio.run(provider.complete_text(messages, json_mode=True)) == '{"ok": true}'
    assert [path for path, _ in requests] == [
        "/v1/responses",
        "/v1/chat/completions",
    ]
    assert requests[0][1]["model"] == requests[1][1]["model"] == "gpt-5.6-sol"
    assert requests[0][1]["reasoning"] == {"effort": "high"}
    assert requests[1][1]["reasoning_effort"] == "high"
    assert "temperature" not in requests[1][1]


@pytest.mark.parametrize("status", [401, 403])
def test_responses_auth_errors_do_not_fall_back_to_chat_completions(status):
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(status, request=request, text="denied")

    provider = OpenAICompatibleLLMProvider(
        base_url="https://sub2api.example.test/v1",
        api_key="key",
        model="gpt-5.6-sol",
        wire_api="responses",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            provider.complete_text(
                [{"role": "user", "content": "普通分析"}]
            )
        )
    assert paths == ["/v1/responses"]


def test_sub2api_rejects_public_plaintext_http_before_sending_credentials():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json={"output_text": "ok"})

    provider = OpenAICompatibleLLMProvider(
        base_url="http://203.0.113.10:8080/v1",
        api_key="key",
        model="gpt-5.6-sol",
        provider_name="sub2api",
        wire_api="responses",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LLMError, match="HTTPS"):
        asyncio.run(provider.complete_text([{"role": "user", "content": "普通分析"}]))
    assert requests == []


def test_sub2api_allows_public_plaintext_http_when_explicitly_enabled():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json={"output_text": "ok"})

    provider = OpenAICompatibleLLMProvider(
        base_url="http://203.0.113.10:8080/v1",
        api_key="key",
        model="gpt-5.6-sol",
        provider_name="sub2api",
        wire_api="responses",
        allow_insecure_http=True,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        provider.complete_text([{"role": "user", "content": "普通分析"}])
    )

    assert result == "ok"
    assert [request.url.path for request in requests] == ["/v1/responses"]


def test_responses_timeout_does_not_fall_back_to_chat_completions():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        raise httpx.ReadTimeout("timed out", request=request)

    provider = OpenAICompatibleLLMProvider(
        base_url="https://sub2api.example.test/v1",
        api_key="key",
        model="gpt-5.6-sol",
        wire_api="responses",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LLMTimeoutError):
        asyncio.run(provider.complete_text([{"role": "user", "content": "普通分析"}]))
    assert paths == ["/v1/responses"]


def test_incomplete_responses_result_does_not_fall_back_to_chat_completions():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output": [],
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "unexpected fallback"}}]},
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="https://sub2api.example.test/v1",
        api_key="key",
        model="gpt-5.6-sol",
        wire_api="responses",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LLMError, match="incomplete.*max_output_tokens"):
        asyncio.run(provider.complete_text([{"role": "user", "content": "普通分析"}]))
    assert paths == ["/v1/responses"]


def test_responses_refusal_does_not_fall_back_to_chat_completions():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "refusal", "refusal": "request refused"}
                            ],
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "unexpected fallback"}}]},
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="https://sub2api.example.test/v1",
        api_key="key",
        model="gpt-5.6-sol",
        wire_api="responses",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LLMError, match="refused"):
        asyncio.run(provider.complete_text([{"role": "user", "content": "普通分析"}]))
    assert paths == ["/v1/responses"]


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
        base_url="https://primary.example.test/v1",
        api_key="primary-key",
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


def test_resolve_llm_settings_defaults_to_sub2api_gpt_5p6_sol(monkeypatch):
    class DummyConfig:
        LLM_PROVIDER = "sub2api"
        LLM_BASE_URL = ""
        LLM_API_KEY = ""
        LLM_MODEL = ""

    monkeypatch.setenv("SUB2API_KEY", "secret")

    settings = resolve_llm_settings(DummyConfig)

    assert settings["provider"] == "sub2api"
    assert settings["base_url"] == ""
    assert settings["api_key"] == "secret"
    assert settings["model"] == "gpt-5.6-sol"
    assert settings["wire_api"] == "responses"
    assert settings["reasoning_effort"] == "auto"


def test_configured_provider_ignores_legacy_secondary_route():
    class DummyConfig:
        LLM_PROVIDER = "sub2api"
        LLM_BASE_URL = "https://sub2api.example.test/v1"
        LLM_API_KEY = "key"
        LLM_MODEL = "gpt-5.6-sol"
        LLM_FALLBACK_MODEL = "legacy-fallback-model"
        LLM_SECONDARY_PROVIDER = "opencode"
        LLM_SECONDARY_BASE_URL = "https://opencode.example.test/v1"
        LLM_SECONDARY_API_KEY = "secondary-key"
        LLM_SECONDARY_MODEL = "fallback-model"

    provider = create_llm_provider_from_config(DummyConfig)

    assert provider.provider_name == "sub2api"
    assert provider.model == "gpt-5.6-sol"
    assert provider.fallback_model == ""
    assert provider.fallback_provider is None


def test_configured_provider_propagates_insecure_http_opt_in():
    class DummyConfig:
        LLM_PROVIDER = "sub2api"
        LLM_BASE_URL = "http://203.0.113.10:8080/v1"
        LLM_API_KEY = "key"
        LLM_ALLOW_INSECURE_HTTP = "true"

    provider = create_llm_provider_from_config(DummyConfig)

    assert provider.allow_insecure_http is True


def test_provider_uses_configured_request_timeout_for_long_generation():
    class DummyConfig:
        LLM_PROVIDER = "openai-compatible"
        LLM_ENV_KEY = "LLM_API_KEY"
        LLM_BASE_URL = "https://example.test/v1"
        LLM_API_KEY = "key"
        LLM_MODEL = "deepseek-v4-flash-0731"
        LLM_WIRE_API = "chat_completions"
        LLM_REASONING_EFFORT = "max"
        LLM_TIMEOUT_SECONDS = 600
        LLM_MAX_RETRIES = 4
        LLM_RETRY_BASE_DELAY = 2

    provider = create_llm_provider_from_config(DummyConfig)

    assert provider.timeout == 600
    assert provider.max_retries == 4
    assert provider.retry_base_delay == 2


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


def test_rag_llm_inherits_the_global_text_model_settings():
    primary_settings = resolve_llm_settings(config)
    settings = resolve_llm_settings(config, prefix="RAG_LLM")

    for field in ("provider", "base_url", "model", "wire_api", "reasoning_effort", "timeout_seconds"):
        assert settings[field] == primary_settings[field]
