# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

from analysis.llm import (
    LLMError,
    LLMOutputTruncatedError,
    is_retryable_llm_exception,
)
from creative.llm_observability import (
    active_attempt_reporter,
    summarize_payload_shape,
    truncate_text,
)
from creative.prompt_templates import blueprint_messages, repair_messages


logger = logging.getLogger(__name__)

RAW_RESPONSE_LOG_LIMIT = 4000


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value or "")


def _normalize_risk_score(value: Any) -> float:
    labels = {
        "低": 20.0, "low": 20.0,
        "中": 50.0, "medium": 50.0,
        "高": 80.0, "high": 80.0,
    }
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in labels:
            return labels[normalized]
    try:
        return max(0.0, min(100.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _ngrams(value: Any, size: int = 4) -> set[str]:
    text = re.sub(r"\s+|[^0-9A-Za-z\u4e00-\u9fff]", "", _flatten_text(value)).casefold()
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[index:index + size] for index in range(len(text) - size + 1)}


def assess_similarity_risk(source: dict[str, Any], blueprint: dict[str, Any]) -> dict[str, Any]:
    source_grams = _ngrams(source)
    target_grams = _ngrams({key: value for key, value in blueprint.items() if key not in {"risk_score", "risk_level"}})
    overlap = len(source_grams & target_grams) / max(1, min(len(source_grams), len(target_grams)))
    overlap_score = round(overlap * 100, 1)
    model_score = _normalize_risk_score(blueprint.get("risk_score"))
    score = max(model_score, overlap_score)
    level = "high" if score >= 70 else "medium" if score >= 40 else "low"
    return {
        "risk_score": score,
        "risk_level": level,
        "model_score": model_score,
        "overlap_score": overlap_score,
    }


async def complete_json_payload(
    provider: Any,
    messages: list[dict[str, str]],
    json_schema: dict[str, Any] | None = None,
) -> Any:
    """调用 provider.complete_json；仅当 provider 支持 schema 参数时才传 JSON Schema。

    测试替身与旧 provider 的签名是 ``complete_json(messages)``，因此这里按签名探测，
    避免为了严格模式而破坏既有实现。
    """
    if json_schema is None:
        return await provider.complete_json(messages)
    complete = provider.complete_json
    try:
        parameters = inspect.signature(complete).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_schema = "schema" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if not accepts_schema:
        return await provider.complete_json(messages)
    return await complete(messages, schema=json_schema)


def _repair_error(last_error: Exception | None, payload: Any) -> str:
    error = str(last_error or "unknown error")
    if payload is None:
        return error
    return f"{error}；上一次响应{summarize_payload_shape(payload)}"


async def _report_attempt(
    reporter: Callable[[dict[str, Any]], Any] | None, info: dict[str, Any]
) -> None:
    if reporter is None:
        return
    try:
        outcome = reporter(info)
        if inspect.isawaitable(outcome):
            await outcome
    except Exception:
        logger.warning("模型结构校验尝试上报失败", exc_info=True)


def _log_attempt_failure(
    attempt: int, max_attempts: int, exc: Exception, payload: Any
) -> None:
    raw = (
        json.dumps(payload, ensure_ascii=False) if payload is not None else ""
    )
    logger.warning(
        "模型响应未通过结构校验（第 %s/%s 次）：%s；%s；原始响应：%s",
        attempt,
        max_attempts,
        exc,
        summarize_payload_shape(payload),
        truncate_text(raw, RAW_RESPONSE_LOG_LIMIT),
    )


async def complete_with_one_repair(
    provider: Any,
    messages: list[dict[str, str]],
    validator: Callable[[Any], Any],
    *,
    max_attempts: int = 2,
    on_attempt: Callable[[dict[str, Any]], Any] | None = None,
    json_schema: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    reporter = on_attempt or active_attempt_reporter()
    last_error: Exception | None = None
    last_payload: Any = None
    for attempt in range(max_attempts):
        current = (
            messages
            if attempt == 0
            else repair_messages(messages, _repair_error(last_error, last_payload))
        )
        started_at = time.monotonic()
        payload: Any = None
        try:
            payload = await complete_json_payload(provider, current, json_schema)
            last_payload = payload
            result = validator(payload)
        except Exception as exc:
            if is_retryable_llm_exception(exc):
                raise
            last_error = exc
            elapsed_seconds = time.monotonic() - started_at
            _log_attempt_failure(attempt + 1, max_attempts, exc, payload)
            await _report_attempt(
                reporter,
                {
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "ok": False,
                    "error": str(exc),
                    "elapsed_seconds": elapsed_seconds,
                    "payload": payload,
                    "request": dict(
                        getattr(provider, "last_request_summary", None) or {}
                    ),
                    "truncated_text": str(getattr(exc, "partial_text", "") or ""),
                },
            )
            # 输出被截断属于调用参数问题，重发同一份请求只会再被截断一次；
            # 直接抛出，让任务日志显示真实原因（提高输出上限或缩小单次生成范围）。
            if isinstance(exc, LLMOutputTruncatedError):
                raise
            continue
        await _report_attempt(
            reporter,
            {
                "attempt": attempt + 1,
                "max_attempts": max_attempts,
                "ok": True,
                "error": "",
                "elapsed_seconds": time.monotonic() - started_at,
                "payload": payload,
                "request": dict(
                    getattr(provider, "last_request_summary", None) or {}
                ),
            },
        )
        return result, {
            "provider": str(
                (payload.get("provider") if isinstance(payload, dict) else "")
                or getattr(provider, "provider_name", "")
            ),
            "repair_attempted": bool(attempt),
        }
    raise LLMError(f"LLM 返回内容无法通过结构校验：{last_error}") from last_error


def _validate_blueprints(payload: dict[str, Any], count: int) -> list[dict[str, Any]]:
    items = payload.get("blueprints")
    if not isinstance(items, list) or len(items) != count:
        raise ValueError(f"blueprints 必须包含 {count} 项")
    required = {
        "title", "imitation_reason", "borrowed_elements", "different_elements", "protagonist", "core_conflict", "main_goal",
        "background", "worldview", "first_three_hooks", "highlights", "audience", "cost_level",
        "risk_score", "revision_suggestions", "priority",
    }
    normalized: list[dict[str, Any]] = []
    text_fields = {
        "title", "imitation_reason", "protagonist", "core_conflict", "main_goal", "background",
        "worldview", "audience", "cost_level",
    }
    list_fields = {
        "borrowed_elements", "different_elements", "first_three_hooks", "highlights",
        "revision_suggestions", "risk_reasons",
    }
    for item in items:
        if not isinstance(item, dict) or required - set(item):
            raise ValueError(f"仿制方向缺少字段：{sorted(required - set(item or {}))}")
        normalized_item = dict(item)
        for field in text_fields:
            normalized_item[field] = _flatten_text(item.get(field)).strip()
        for field in list_fields:
            value = item.get(field)
            values = value if isinstance(value, list) else [value]
            normalized_item[field] = [
                _flatten_text(entry).strip() for entry in values
                if _flatten_text(entry).strip()
            ]
        normalized_item["risk_score"] = _normalize_risk_score(item.get("risk_score"))
        normalized.append(normalized_item)
    return normalized


async def generate_blueprints(
    provider: Any,
    project: dict[str, Any],
    topic: dict[str, Any],
    breakdown: dict[str, Any],
    count: int = 3,
    prompt_snapshot: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not 3 <= int(count) <= 5:
        raise ValueError("仿制方向数量必须在 3-5 之间")
    payload = {
        "project": {key: project.get(key) for key in ("name", "genre", "target_platform", "target_audience", "target_episode_count")},
        "topic": {key: topic.get(key) for key in ("title", "description", "category", "recommendation")},
        "locked_breakdown": breakdown.get("content") or {},
        "source_evidence": breakdown.get("field_sources") or topic.get("evidence") or [],
    }
    items, meta = await complete_with_one_repair(
        provider,
        blueprint_messages(payload, int(count), prompt_snapshot),
        lambda result: _validate_blueprints(result, int(count)),
    )
    result: list[dict[str, Any]] = []
    for item in items:
        risk = assess_similarity_risk(breakdown.get("content") or {}, item)
        content = dict(item)
        content.pop("risk_score", None)
        content["risk_reasons"] = list(item.get("risk_reasons") or [])
        result.append({"content": content, **risk})
    return result, meta
