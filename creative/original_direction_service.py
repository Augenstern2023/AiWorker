# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from typing import Any

from analysis.llm import LLMError, is_retryable_llm_exception
from creative.imitation_service import _flatten_text, _ngrams
from creative.prompt_templates import (
    ORIGINAL_PROMPT_TEMPLATE_VERSION,
    ORIGINAL_VARIANT_PROMPT_TEMPLATE_VERSION,
    original_direction_messages,
    original_variant_messages,
)


ORIGINAL_REQUIRED_FIELDS = {
    "title", "logline", "theme", "protagonist", "protagonist_desire",
    "protagonist_flaw", "pressure_response", "main_opposition",
    "core_conflict", "main_goal", "background", "worldview", "story_engine",
    "first_three_hooks", "highlights", "differentiators", "audience",
    "market_fit", "cost_level", "originality_statement", "risks",
    "assumptions", "open_questions",
}
_LIST_FIELDS = {
    "first_three_hooks", "highlights", "differentiators", "risks",
    "assumptions", "open_questions",
}
_TEXT_FIELDS = ORIGINAL_REQUIRED_FIELDS - _LIST_FIELDS


def _reference_ids(market_context: list[dict[str, Any]]) -> list[str]:
    return [
        f"{item.get('resource_type')}:{int(item.get('resource_id') or 0)}"
        for item in market_context
        if item.get("resource_type") in {"topic", "breakdown"}
        and int(item.get("resource_id") or 0) > 0
    ]


def validate_original_direction_content(
    content: dict[str, Any],
    *,
    market_context: list[dict[str, Any]],
    blocked_terms: list[str],
) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise ValueError("原创方向必须是对象")
    missing = ORIGINAL_REQUIRED_FIELDS - set(content)
    if missing:
        raise ValueError(f"原创方向缺少字段：{sorted(missing)}")
    normalized = dict(content)
    for field in _TEXT_FIELDS:
        value = _flatten_text(content.get(field)).strip()
        if not value:
            raise ValueError(f"原创方向字段不能为空：{field}")
        normalized[field] = value
    for field in _LIST_FIELDS:
        value = content.get(field)
        if not isinstance(value, list):
            raise ValueError(f"原创方向字段必须是数组：{field}")
        items = [_flatten_text(item).strip() for item in value]
        normalized[field] = [item for item in items if item]
        if not normalized[field]:
            raise ValueError(f"原创方向字段不能为空：{field}")

    flattened = _flatten_text(normalized).casefold()
    matched = next(
        (
            term.strip() for term in blocked_terms
            if len(term.strip()) >= 2 and term.strip().casefold() in flattened
        ),
        "",
    )
    if matched:
        raise ValueError(f"原创方向命中来源专有表达：{matched}")

    if market_context:
        normalized["reference_ids"] = _reference_ids(market_context)
    elif "创作判断" not in normalized["market_fit"]:
        normalized["market_fit"] = f"创作判断：{normalized['market_fit']}"
    return normalized


def _dimension_values(content: dict[str, Any]) -> tuple[Any, ...]:
    return (
        content.get("protagonist"),
        content.get("background"),
        {"conflict": content.get("core_conflict"), "engine": content.get("story_engine")},
        {"opposition": content.get("main_opposition"), "response": content.get("pressure_response")},
        {"theme": content.get("theme"), "highlights": content.get("highlights")},
    )


def _dimensions_different(left: dict[str, Any], right: dict[str, Any]) -> int:
    different = 0
    for left_value, right_value in zip(_dimension_values(left), _dimension_values(right)):
        left_grams = _ngrams(left_value)
        right_grams = _ngrams(right_value)
        overlap = len(left_grams & right_grams) / max(
            1, min(len(left_grams), len(right_grams))
        )
        if overlap < 0.75:
            different += 1
    return different


def _accept_candidates(
    payload: dict[str, Any],
    accepted: list[dict[str, Any]],
    *,
    market_context: list[dict[str, Any]],
    blocked_terms: list[str],
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_items = payload.get("directions")
    if not isinstance(raw_items, list):
        raise ValueError("directions 必须是数组")
    errors: list[str] = []
    for raw in raw_items:
        if len(accepted) >= limit:
            break
        try:
            item = validate_original_direction_content(
                raw, market_context=market_context, blocked_terms=blocked_terms
            )
            if any(_dimensions_different(item, other) < 2 for other in accepted):
                raise ValueError("原创方向差异不足两个维度")
            accepted.append(item)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
    return accepted, errors


def _source_refs(
    seed: dict[str, Any], market_context: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "seed_id": int(seed["id"]),
        "seed_version": int(seed.get("version") or 0),
        "reference_ids": _reference_ids(market_context),
    }


async def generate_original_directions(
    provider: Any,
    project: dict[str, Any],
    seed: dict[str, Any],
    market_context: list[dict[str, Any]],
    blocked_terms: list[str],
    count: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not 1 <= int(count) <= 5:
        raise ValueError("单次原创方向生成数量必须在 1-5 之间")
    request_payload = {
        "project": {
            key: project.get(key) for key in (
                "name", "genre", "target_platform", "target_audience",
                "target_episode_count", "episode_duration_seconds",
            )
        },
        "seed": {
            key: seed.get(key) for key in (
                "id", "version", "seed_text", "constraints", "reference_refs",
            )
        },
        "market_context": market_context,
    }
    accepted: list[dict[str, Any]] = []
    repair_attempted = False
    try:
        first = await provider.complete_json(
            original_direction_messages(request_payload, int(count))
        )
        accepted, first_errors = _accept_candidates(
            first, accepted, market_context=market_context,
            blocked_terms=blocked_terms, limit=int(count),
        )
        if not accepted:
            reason = "; ".join(first_errors) or "没有合格候选"
            raise LLMError(f"原创方向无法通过校验：{reason}")
        missing = int(count) - len(accepted)
        if missing:
            repair_attempted = True
            repair_payload = {
                **request_payload,
                "accepted_directions": accepted,
                "repair_reason": "; ".join(first_errors),
                "missing_count": missing,
            }
            second = await provider.complete_json(
                original_direction_messages(repair_payload, missing)
            )
            accepted, _ = _accept_candidates(
                second, accepted, market_context=market_context,
                blocked_terms=blocked_terms, limit=int(count),
            )
    except LLMError:
        raise
    except Exception as exc:
        if is_retryable_llm_exception(exc):
            raise
        raise LLMError(f"原创方向生成失败：{exc}") from exc

    refs = _source_refs(seed, market_context)
    generated = [
        {
            "content": item,
            "risk_score": 0,
            "risk_level": "low",
            "source_refs": refs,
        }
        for item in accepted
    ]
    return generated, {
        "provider": str(getattr(provider, "provider_name", "")),
        "prompt_template_version": ORIGINAL_PROMPT_TEMPLATE_VERSION,
        "repair_attempted": repair_attempted,
        "missing_count": int(count) - len(generated),
    }


async def derive_original_direction(
    provider: Any,
    project: dict[str, Any],
    seed: dict[str, Any],
    parent_direction: dict[str, Any],
    *,
    instruction: str,
    market_context: list[dict[str, Any]],
    blocked_terms: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "project": project,
        "seed": seed,
        "parent_direction": parent_direction.get("content") or {},
        "instruction": instruction.strip(),
        "market_context": market_context,
    }
    try:
        response = await provider.complete_json(original_variant_messages(payload))
        raw = response.get("direction") if isinstance(response, dict) else None
        content = validate_original_direction_content(
            raw, market_context=market_context, blocked_terms=blocked_terms
        )
    except Exception as exc:
        if is_retryable_llm_exception(exc):
            raise
        raise LLMError(f"原创方向派生失败：{exc}") from exc
    refs = {
        **_source_refs(seed, market_context),
        "parent_blueprint_id": int(parent_direction["id"]),
    }
    return {
        "content": content,
        "risk_score": 0,
        "risk_level": "low",
        "source_refs": refs,
    }, {
        "provider": str(getattr(provider, "provider_name", "")),
        "prompt_template_version": ORIGINAL_VARIANT_PROMPT_TEMPLATE_VERSION,
        "repair_attempted": False,
    }
