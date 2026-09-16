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
import os
import json
import re
from typing import Any, Callable, Dict, List
from urllib.parse import urlsplit

import httpx
from json_repair import repair_json


_CONFIGURED_LLM_SETTINGS_REPOSITORY: Any | None = None


def configure_llm_settings_repository(repository: Any | None) -> None:
    global _CONFIGURED_LLM_SETTINGS_REPOSITORY
    _CONFIGURED_LLM_SETTINGS_REPOSITORY = repository


def _default_llm_settings_repository() -> Any | None:
    if _CONFIGURED_LLM_SETTINGS_REPOSITORY is not None:
        return _CONFIGURED_LLM_SETTINGS_REPOSITORY
    from analysis.llm_settings import runtime_settings_repository

    return runtime_settings_repository()


SUPPORTED_REASONING_EFFORTS = frozenset(
    {"none", "low", "medium", "high", "xhigh", "max"}
)
LOW_REASONING_MARKERS = (
    "提取",
    "抽取",
    "分类",
    "格式转换",
    "修复json",
    "extract",
    "classify",
    "format",
)
HIGH_REASONING_MARKERS = (
    "人物设计",
    "人物关系",
    "故事梗概",
    "分集",
    "分集大纲",
    "剧本生成",
    "场次剧本",
    "创作",
    "策划",
    "beats",
    "screenplay",
    "outline",
)
DEEP_REASONING_MARKERS = (
    "审核剧本",
    "剧本审核",
    "审稿",
    "评审",
    "审核报告",
    "深度优化",
    "剧本优化",
    "交叉核验",
    "时间线",
    "review screenplay",
    "optimize screenplay",
)
LONG_CONTEXT_CHARS = 20_000
MAX_CONTEXT_CHARS = 30_000


class LLMError(RuntimeError):
    pass


class LLMTimeoutError(LLMError):
    pass


class ResponsesAPIUnsupportedError(LLMError):
    pass


class LLMIncompleteResponseError(LLMError):
    pass


class LLMOutputTruncatedError(LLMIncompleteResponseError):
    """The provider stopped because the generated output exhausted its budget."""

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str,
        returned_chars: int = 0,
        partial_text: str = "",
    ) -> None:
        super().__init__(message)
        self.finish_reason = str(finish_reason or "").strip().lower()
        self.returned_chars = max(0, int(returned_chars or 0))
        self.partial_text = partial_text


class LLMRefusalError(LLMError):
    pass


class LLMProvider:
    async def analyze_video(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class OpenAICompatibleLLMProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        fallback_model: str = "",
        fallback_provider: LLMProvider | None = None,
        provider_name: str = "openai-compatible",
        wire_api: str = "chat_completions",
        reasoning_effort: str = "",
        max_output_tokens: int | None = None,
        allow_insecure_http: bool = False,
        timeout: int = 180,
        max_retries: int = 2,
        retry_base_delay: float = 1.0,
        retry_read_timeouts: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_callback: Callable[[int, int, str], Any] | None = None,
        api_key_resolver: Callable[[], str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_key_resolver = api_key_resolver
        self.model = model
        self.fallback_model = fallback_model.strip()
        self.fallback_provider = fallback_provider
        self.provider_name = provider_name
        self.wire_api = wire_api
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = _max_output_tokens(max_output_tokens)
        self.allow_insecure_http = bool(allow_insecure_http)
        self.timeout = timeout
        self.max_retries = max(0, min(5, int(max_retries)))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        self.retry_read_timeouts = bool(retry_read_timeouts)
        self.transport = transport
        self.retry_callback = retry_callback

    async def analyze_video(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self.complete_json(build_video_analysis_messages(payload))
        return normalize_llm_analysis(result, provider=self.provider_name)

    async def complete_json(self, messages: List[Dict[str, str]]) -> Any:
        result = parse_json_object(await self.complete_text(messages, json_mode=True))
        if isinstance(result, dict):
            result["provider"] = self.provider_name
        return result

    async def complete_text(self, messages: List[Dict[str, str]], json_mode: bool = False) -> str:
        # One snapshot per call, including its retries and protocol fallback.
        api_key = self.api_key_resolver() if self.api_key_resolver else self.api_key
        if not self.base_url:
            raise LLMError("LLM_BASE_URL is required.")
        if not api_key:
            raise LLMError("LLM_API_KEY is required.")
        if (
            self.provider_name.lower() == "sub2api"
            and not self.allow_insecure_http
            and not is_secure_llm_base_url(self.base_url)
        ):
            raise LLMError(
                "Sub2api LLM_BASE_URL must use HTTPS or a loopback tunnel."
            )

        try:
            return await self._complete_text_with_model(self.model, messages, json_mode, api_key)
        except Exception as exc:
            if self.fallback_model and self.fallback_model != self.model and is_model_unavailable_exception(exc):
                try:
                    return await self._complete_text_with_model(self.fallback_model, messages, json_mode, api_key)
                except Exception as fallback_exc:
                    if self.fallback_provider and is_model_unavailable_exception(fallback_exc):
                        return await self.fallback_provider.complete_text(messages, json_mode)
                    raise
            if self.fallback_provider and is_model_unavailable_exception(exc):
                return await self.fallback_provider.complete_text(messages, json_mode)
            raise

    async def _complete_text_with_model(self, model: str, messages: List[Dict[str, str]], json_mode: bool, api_key: str) -> str:
        if self.wire_api == "responses":
            try:
                return await self._complete_responses_text(messages, json_mode, model, api_key)
            except Exception as exc:
                if not is_responses_api_unsupported_exception(exc):
                    raise
                return await self._complete_chat_completions_text(messages, json_mode, model, api_key)
        return await self._complete_chat_completions_text(messages, json_mode, model, api_key)

    async def _complete_responses_text(self, messages: List[Dict[str, str]], json_mode: bool, model: str, api_key: str) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        request_body = build_responses_request_body(
            model or self.model,
            messages,
            json_mode=json_mode,
            reasoning_effort=resolve_reasoning_effort(
                self.reasoning_effort, messages
            ),
            max_output_tokens=self.max_output_tokens,
        )
        endpoint = f"{self.base_url}/responses"
        response = await self._post_with_retry(endpoint, headers, request_body)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResponsesAPIUnsupportedError(
                "Responses API returned an empty or non-JSON response."
            ) from exc
        return parse_responses_text(payload)

    async def _complete_chat_completions_text(self, messages: List[Dict[str, str]], json_mode: bool, model: str, api_key: str) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        request_body: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
        }
        reasoning_effort = resolve_reasoning_effort(
            self.reasoning_effort, messages
        )
        if reasoning_effort:
            request_body["reasoning_effort"] = reasoning_effort
        if reasoning_effort == "none":
            request_body["temperature"] = 0.2
        if self.max_output_tokens and self.max_output_tokens > 0:
            request_body["max_tokens"] = self.max_output_tokens
        if json_mode:
            request_body["response_format"] = {"type": "json_object"}
        endpoint = f"{self.base_url}/chat/completions"
        response = await self._post_with_retry(endpoint, headers, request_body)
        return parse_chat_completion_text(response.json())

    async def _post_with_retry(
        self,
        endpoint: str,
        headers: Dict[str, str],
        request_body: Dict[str, Any],
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            trust_env=False,
        ) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(endpoint, headers=headers, json=request_body)
                    response.raise_for_status()
                    return response
                except Exception as exc:
                    reason = (
                        f"模型响应超时（{self.timeout} 秒）"
                        if isinstance(exc, httpx.TimeoutException)
                        else str(exc)
                    )
                    retryable = is_retryable_llm_exception(exc) and not (
                        isinstance(exc, httpx.ReadTimeout)
                        and not self.retry_read_timeouts
                    )
                    if not retryable or attempt >= self.max_retries:
                        if isinstance(exc, httpx.TimeoutException):
                            raise LLMTimeoutError(reason) from exc
                        raise
                    retry_number = attempt + 1
                    if self.retry_callback:
                        try:
                            self.retry_callback(retry_number, self.max_retries, reason)
                        except Exception:
                            pass
                    delay = self.retry_base_delay * (2 ** (retry_number - 1))
                    if delay:
                        await asyncio.sleep(delay)
        raise LLMError("LLM request retry loop exited unexpectedly.")


def is_retryable_llm_exception(exc: Exception) -> bool:
    if isinstance(exc, LLMTimeoutError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = int(exc.response.status_code)
        return status in {408, 409, 425, 429} or 500 <= status <= 599
    return isinstance(exc, httpx.RequestError)


def is_model_unavailable_exception(exc: Exception) -> bool:
    if is_retryable_llm_exception(exc):
        return True
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    if int(exc.response.status_code) == 404:
        return True
    if int(exc.response.status_code) not in {400, 422}:
        return False
    message = exc.response.text.lower()
    return "model" in message and any(marker in message for marker in ("not found", "unavailable", "does not exist", "unsupported"))


def is_responses_api_unsupported_exception(exc: Exception) -> bool:
    if isinstance(exc, ResponsesAPIUnsupportedError):
        return True
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and int(exc.response.status_code) in {404, 405, 415, 422}
    )


def is_secure_llm_base_url(base_url: str) -> bool:
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme.lower() == "https":
        return True
    return parsed.scheme.lower() == "http" and (parsed.hostname or "").lower() in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def select_reasoning_effort(messages: List[Dict[str, str]]) -> str:
    text = "\n".join(
        str(message.get("content", "")) for message in messages
    )
    normalized = text.lower()
    if any(marker in normalized for marker in DEEP_REASONING_MARKERS):
        return "max" if len(text) >= MAX_CONTEXT_CHARS else "xhigh"
    if any(marker in normalized for marker in HIGH_REASONING_MARKERS):
        return "xhigh" if len(text) >= LONG_CONTEXT_CHARS else "high"
    if len(text) >= LONG_CONTEXT_CHARS:
        return "high"
    if (
        any(marker in normalized for marker in LOW_REASONING_MARKERS)
        and len(text) < 8_000
    ):
        return "low"
    return "medium"


def resolve_reasoning_effort(
    configured: str, messages: List[Dict[str, str]]
) -> str:
    normalized = str(configured or "auto").strip().lower()
    if normalized in SUPPORTED_REASONING_EFFORTS:
        return normalized
    return select_reasoning_effort(messages)


def provider_defaults(provider: str) -> Dict[str, str]:
    if provider == "sub2api":
        return {
            "name": "sub2api",
            "base_url": "",
            "env_key": "SUB2API_KEY",
            "model": "gpt-5.6-sol",
            "wire_api": "chat_completions",
            "reasoning_effort": "auto",
        }
    return {
        "name": provider or "openai-compatible",
        "base_url": "",
        "env_key": "LLM_API_KEY",
        "model": "MiniMax-M3",
        "wire_api": "responses" if provider == "openai" else "chat_completions",
        "reasoning_effort": "",
    }


def resolve_llm_settings(
    config_module: Any,
    prefix: str = "LLM",
    *,
    settings_repository: Any | None = None,
    database_default: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if database_default is None:
        if settings_repository is None:
            settings_repository = _default_llm_settings_repository()
        database_default = (
            settings_repository.effective_default()
            if settings_repository is not None
            else None
        ) or {}
    else:
        database_default = dict(database_default)
    scenario_prefix = prefix if prefix != "LLM" else ""
    scenario_provider = (
        getattr(config_module, f"{scenario_prefix}_PROVIDER", "")
        if scenario_prefix
        else ""
    )
    provider = str(
        scenario_provider
        or database_default.get("provider")
        or getattr(config_module, "LLM_PROVIDER", "")
        or "minimax"
    )
    defaults = provider_defaults(provider)
    scenario_env_key = (
        getattr(config_module, f"{scenario_prefix}_ENV_KEY", "")
        if scenario_prefix
        else ""
    )
    env_key = str(
        scenario_env_key
        or (
            getattr(config_module, "LLM_ENV_KEY", "")
            if not database_default
            else ""
        )
        or defaults["env_key"]
    )
    scenario_api_key = (
        str(getattr(config_module, f"{scenario_prefix}_API_KEY", "") or "")
        if scenario_prefix
        else ""
    )
    scenario_environment_key = (
        os.getenv(env_key, "") if scenario_env_key else ""
    )
    if scenario_api_key or scenario_environment_key:
        api_key = scenario_api_key or scenario_environment_key
    elif database_default:
        api_key = str(database_default.get("api_key") or "")
    else:
        api_key = (
            str(getattr(config_module, "LLM_API_KEY", "") or "")
            or os.getenv(env_key, "")
            or os.getenv("LLM_API_KEY", "")
        )
    prefixed_max_retries = getattr(
        config_module, f"{prefix}_MAX_RETRIES", ""
    )
    max_retries_value = (
        prefixed_max_retries
        if prefixed_max_retries not in (None, "")
        else getattr(config_module, "LLM_MAX_RETRIES", "")
    )
    prefixed_retry_read_timeouts = getattr(
        config_module, f"{prefix}_RETRY_READ_TIMEOUTS", ""
    )
    retry_read_timeouts_value = (
        prefixed_retry_read_timeouts
        if prefixed_retry_read_timeouts not in (None, "")
        else getattr(config_module, "LLM_RETRY_READ_TIMEOUTS", True)
    )
    scenario_base_url = (
        getattr(config_module, f"{scenario_prefix}_BASE_URL", "")
        if scenario_prefix
        else ""
    )
    scenario_model = (
        getattr(config_module, f"{scenario_prefix}_MODEL", "")
        if scenario_prefix
        else ""
    )
    scenario_wire_api = (
        getattr(config_module, f"{scenario_prefix}_WIRE_API", "")
        if scenario_prefix
        else ""
    )
    scenario_allow_insecure = (
        getattr(config_module, f"{scenario_prefix}_ALLOW_INSECURE_HTTP", "")
        if scenario_prefix
        else ""
    )
    scenario_max_output_tokens = (
        getattr(config_module, f"{scenario_prefix}_MAX_OUTPUT_TOKENS", None)
        if scenario_prefix
        else None
    )
    if scenario_prefix and scenario_max_output_tokens in (None, ""):
        scenario_environment_value = os.getenv(
            f"{scenario_prefix}_MAX_OUTPUT_TOKENS"
        )
        if scenario_environment_value is not None:
            scenario_max_output_tokens = scenario_environment_value
    model_max_output_tokens = database_default.get("max_output_tokens")
    global_max_output_tokens = getattr(
        config_module, "LLM_MAX_OUTPUT_TOKENS", None
    )
    if global_max_output_tokens in (None, ""):
        global_environment_value = os.getenv("LLM_MAX_OUTPUT_TOKENS")
        if global_environment_value is not None:
            global_max_output_tokens = global_environment_value
    if scenario_max_output_tokens not in (None, ""):
        max_output_tokens_value = scenario_max_output_tokens
    elif model_max_output_tokens is not None:
        max_output_tokens_value = model_max_output_tokens
    else:
        max_output_tokens_value = global_max_output_tokens
    return {
        "provider": provider,
        "provider_id": (
            str(database_default.get("provider_id") or "")
            if database_default and not (scenario_api_key or scenario_environment_key)
            else ""
        ),
        "base_url": str(
            scenario_base_url
            or database_default.get("base_url")
            or getattr(config_module, "LLM_BASE_URL", "")
            or defaults["base_url"]
        ),
        "api_key": api_key,
        "model": str(
            scenario_model
            or database_default.get("model")
            or getattr(config_module, "LLM_MODEL", "")
            or defaults["model"]
        ),
        "wire_api": str(
            scenario_wire_api
            or database_default.get("wire_api")
            or getattr(config_module, "LLM_WIRE_API", "")
            or defaults["wire_api"]
        ),
        "reasoning_effort": str(
            getattr(config_module, f"{prefix}_REASONING_EFFORT", "")
            or getattr(config_module, "LLM_REASONING_EFFORT", "")
            or defaults["reasoning_effort"]
        ),
        "max_output_tokens": _max_output_tokens(max_output_tokens_value),
        "allow_insecure_http": _boolean_setting(
            scenario_allow_insecure
            or database_default.get("allow_insecure_http", "")
            or getattr(config_module, "LLM_ALLOW_INSECURE_HTTP", "")
        ),
        "timeout_seconds": _timeout_seconds(
            getattr(config_module, f"{prefix}_TIMEOUT_SECONDS", "")
            or getattr(config_module, "LLM_TIMEOUT_SECONDS", "")
        ),
        "max_retries": _max_retries(
            max_retries_value
        ),
        "retry_base_delay": _retry_base_delay(
            getattr(config_module, f"{prefix}_RETRY_BASE_DELAY", "")
            or getattr(config_module, "LLM_RETRY_BASE_DELAY", "")
        ),
        "retry_read_timeouts": _boolean_setting_with_default(
            retry_read_timeouts_value, True
        ),
    }


def _boolean_setting(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _boolean_setting_with_default(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return bool(default)
    return _boolean_setting(value)


def _timeout_seconds(value: Any, default: int = 180) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def _max_retries(value: Any, default: int = 2) -> int:
    try:
        normalized = default if value in (None, "") else value
        return max(0, min(5, int(normalized)))
    except (TypeError, ValueError):
        return default


def _max_output_tokens(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise LLMError("LLM_MAX_OUTPUT_TOKENS 必须是大于或等于 0 的整数") from exc
    if isinstance(value, float) and not value.is_integer():
        raise LLMError("LLM_MAX_OUTPUT_TOKENS 必须是大于或等于 0 的整数")
    if normalized < 0:
        raise LLMError("LLM_MAX_OUTPUT_TOKENS 必须是大于或等于 0 的整数")
    return normalized


def _retry_base_delay(value: Any, default: float = 1.0) -> float:
    try:
        return max(0.0, min(30.0, float(value or default)))
    except (TypeError, ValueError):
        return default


def create_llm_provider_from_config(
    config_module: Any,
    prefix: str = "LLM",
    *,
    model_record_id: str | None = None,
    retry_callback: Callable[[int, int, str], None] | None = None,
) -> OpenAICompatibleLLMProvider:
    settings_repository = _default_llm_settings_repository()
    selected_model_record_id = str(model_record_id or "").strip()
    selected_settings: Dict[str, Any] | None = None
    if selected_model_record_id:
        if settings_repository is None:
            raise LLMError("模型配置存储不可用，无法使用所选模型")
        try:
            selected_settings = settings_repository.effective_model(
                selected_model_record_id
            )
        except (ValueError, RuntimeError) as exc:
            raise LLMError(str(exc)) from exc
    settings = resolve_llm_settings(
        config_module,
        prefix=prefix,
        settings_repository=settings_repository,
        database_default=selected_settings,
    )
    resolved_max_output_tokens = settings["max_output_tokens"]
    if selected_settings is not None:
        settings.update(selected_settings)
        settings["max_output_tokens"] = resolved_max_output_tokens
    provider_id = str(settings.get("provider_id") or "")
    api_key_resolver = None
    if settings_repository is not None and provider_id:
        api_key_resolver = lambda: settings_repository.provider_connection(provider_id)["api_key"]
    provider = OpenAICompatibleLLMProvider(
        base_url=settings["base_url"],
        api_key=settings["api_key"],
        model=settings["model"],
        fallback_model="",
        fallback_provider=None,
        provider_name=settings["provider"],
        wire_api=settings["wire_api"],
        reasoning_effort=settings["reasoning_effort"],
        max_output_tokens=settings["max_output_tokens"],
        allow_insecure_http=settings["allow_insecure_http"],
        timeout=settings["timeout_seconds"],
        max_retries=settings["max_retries"],
        retry_base_delay=settings["retry_base_delay"],
        retry_read_timeouts=settings["retry_read_timeouts"],
        retry_callback=retry_callback,
        api_key_resolver=api_key_resolver,
    )
    provider.provider_id = str(settings.get("provider_id") or "")
    provider.provider_display_name = str(
        settings.get("provider_display_name") or settings["provider"]
    )
    provider.model_record_id = str(
        settings.get("model_record_id") or selected_model_record_id
    )
    provider.model_display_name = str(
        settings.get("model_display_name") or settings["model"]
    )
    return provider


def build_responses_request_body(
    model: str,
    messages: List[Dict[str, str]],
    json_mode: bool = False,
    reasoning_effort: str = "",
    max_output_tokens: int | None = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": model,
        "input": messages,
    }
    if json_mode:
        body["text"] = {"format": {"type": "json_object"}}
    if reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort}
    if max_output_tokens and max_output_tokens > 0:
        body["max_output_tokens"] = max_output_tokens
    return body


def build_video_analysis_messages(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是严谨的视频内容分析助手。请只输出 JSON，不要输出 Markdown。"
                "字段必须包含 summary, key_points, tags, content_type, sentiment, audience, "
                "action_items, risk_flags。risk_flags 用于记录争议、广告、隐私、安全、医疗/法律/金融等风险。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


def parse_chat_completion_json(response_json: Dict[str, Any]) -> Dict[str, Any]:
    return parse_json_object(parse_chat_completion_text(response_json))


def parse_chat_completion_text(response_json: Dict[str, Any]) -> str:
    choices = response_json.get("choices") or []
    if not choices:
        raise LLMError("LLM response has no choices.")

    choice = choices[0]
    finish_reason = str(choice.get("finish_reason") or "").strip().lower()
    message = choice.get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)
    if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
        returned_chars = len(str(content))
        raise LLMOutputTruncatedError(
            "模型输出被输出上限截断"
            f"（finish_reason={finish_reason}，已返回 {returned_chars} 字符）",
            finish_reason=finish_reason,
            returned_chars=returned_chars,
            partial_text=str(content),
        )

    return str(content).strip()


def parse_responses_text(response_json: Dict[str, Any]) -> str:
    if response_json.get("status") == "incomplete":
        details = response_json.get("incomplete_details") or {}
        reason = (
            details.get("reason", "unknown")
            if isinstance(details, dict)
            else str(details)
        )
        normalized_reason = str(reason or "unknown").strip().lower()
        if normalized_reason in {"length", "max_tokens", "max_output_tokens"}:
            partial_text = str(response_json.get("output_text") or "")
            if not partial_text:
                partial_text = "".join(
                    str(content.get("text") or "")
                    for item in response_json.get("output") or []
                    if isinstance(item, dict)
                    for content in item.get("content") or []
                    if isinstance(content, dict)
                    and content.get("type") in {"output_text", "text"}
                )
            returned_chars = len(partial_text)
            raise LLMOutputTruncatedError(
                "模型输出被输出上限截断"
                f"（incomplete_reason={normalized_reason}，已返回 {returned_chars} 字符）",
                finish_reason=normalized_reason,
                returned_chars=returned_chars,
                partial_text=partial_text,
            )
        raise LLMIncompleteResponseError(
            f"Responses API response is incomplete: {reason}."
        )

    if response_json.get("output_text"):
        return str(response_json["output_text"]).strip()

    pieces: List[str] = []
    refusals: List[str] = []
    for item in response_json.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                pieces.append(str(content["text"]))
            if content.get("type") == "refusal" and content.get("refusal"):
                refusals.append(str(content["refusal"]))
    if pieces:
        return "".join(pieces).strip()
    if refusals:
        raise LLMRefusalError(
            f"Responses API request was refused: {'; '.join(refusals)}"
        )
    raise LLMError("Responses API response has no output text.")


def parse_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for candidate in reversed(_extract_json_object_candidates(text)):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        repaired = repair_json(text, return_objects=True)
        if isinstance(repaired, (dict, list)):
            return repaired
        raise


def _extract_json_object_candidates(text: str) -> List[str]:
    candidates: List[str] = []
    stack = 0
    start = -1
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if stack == 0:
                start = index
            stack += 1
        elif char == "}" and stack:
            stack -= 1
            if stack == 0 and start >= 0:
                candidates.append(text[start:index + 1])
                start = -1
    return candidates


def normalize_llm_analysis(payload: Dict[str, Any], provider: str = "") -> Dict[str, Any]:
    return {
        "summary": str(payload.get("summary", "")).strip(),
        "key_points": _as_str_list(payload.get("key_points")),
        "tags": _as_str_list(payload.get("tags")),
        "content_type": str(payload.get("content_type", "")).strip(),
        "sentiment": str(payload.get("sentiment", "")).strip(),
        "audience": str(payload.get("audience", "")).strip(),
        "action_items": _as_str_list(payload.get("action_items")),
        "risk_flags": _as_str_list(payload.get("risk_flags")),
        "provider": provider,
    }


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []
