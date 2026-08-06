# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from analysis.llm import LLMError, is_retryable_llm_exception
from creative.prompt_templates import blueprint_messages, repair_messages


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


async def complete_with_one_repair(
    provider: Any,
    messages: list[dict[str, str]],
    validator: Callable[[Any], Any],
    *,
    max_attempts: int = 2,
) -> tuple[Any, dict[str, Any]]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        current = messages if attempt == 0 else repair_messages(messages, str(last_error or "unknown error"))
        try:
            payload = await provider.complete_json(current)
            return validator(payload), {
                "provider": str(
                    (payload.get("provider") if isinstance(payload, dict) else "")
                    or getattr(provider, "provider_name", "")
                ),
                "repair_attempted": bool(attempt),
            }
        except Exception as exc:
            if is_retryable_llm_exception(exc):
                raise
            last_error = exc
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
        blueprint_messages(payload, int(count)),
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
