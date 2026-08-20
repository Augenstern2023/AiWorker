# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable

from creative.repository import CreativeRepository


@dataclass(frozen=True)
class MarketReferenceBundle:
    context: list[dict[str, Any]]
    source_refs: list[dict[str, Any]]
    blocked_terms: list[str]
    captured_at: float


class MarketReferenceService:
    """Resolve authorized references into non-story market signals."""

    _BREAKDOWN_CONTEXT_FIELDS = (
        "category", "target_audience", "emotional_curve", "production_difficulty",
    )
    _TOPIC_CONTEXT_FIELDS = (
        "platform", "rank_type", "category", "score", "confidence", "captured_at",
    )
    _TOPIC_BLOCKED_FIELDS = ("title", "description", "recommendation", "evidence")

    def __init__(self, repository: CreativeRepository) -> None:
        self.repository = repository

    def resolve(
        self,
        references: list[dict[str, Any]],
        *,
        can_read: Callable[[str, int], bool],
    ) -> MarketReferenceBundle:
        unique: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for raw in references:
            resource_type = str(raw.get("resource_type") or "").strip()
            resource_id = int(raw.get("resource_id") or 0)
            if resource_type not in {"topic", "breakdown"} or resource_id < 1:
                raise ValueError(f"无效市场参考：{resource_type}/{resource_id}")
            key = (resource_type, resource_id)
            if key not in seen:
                seen.add(key)
                unique.append(key)
        if len(unique) > 10:
            raise ValueError("市场参考最多 10 项")

        context: list[dict[str, Any]] = []
        source_refs: list[dict[str, Any]] = []
        blocked_terms: list[str] = []
        for resource_type, resource_id in unique:
            if not can_read(resource_type, resource_id):
                raise PermissionError(f"无权读取市场参考：{resource_type}/{resource_id}")
            resource = (
                self.repository.get_topic(resource_id)
                if resource_type == "topic"
                else self.repository.get_breakdown(resource_id)
            )
            if resource is None:
                raise ValueError(f"市场参考不存在：{resource_type}/{resource_id}")
            if resource_type == "topic":
                safe = self._pick(resource, self._TOPIC_CONTEXT_FIELDS)
                story_values = [resource.get(key) for key in self._TOPIC_BLOCKED_FIELDS]
            else:
                content = resource.get("content") or {}
                safe = self._pick(content, self._BREAKDOWN_CONTEXT_FIELDS)
                story_values = [
                    value for key, value in content.items()
                    if key not in self._BREAKDOWN_CONTEXT_FIELDS
                ]
            context.append({
                "resource_type": resource_type,
                "resource_id": resource_id,
                **safe,
            })
            source_refs.append({"resource_type": resource_type, "resource_id": resource_id})
            blocked_terms.extend(self._strings(story_values))

        return MarketReferenceBundle(
            context=context,
            source_refs=source_refs,
            blocked_terms=list(dict.fromkeys(blocked_terms)),
            captured_at=time.time(),
        )

    @staticmethod
    def _pick(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        return {key: source[key] for key in fields if source.get(key) not in (None, "", [], {})}

    @classmethod
    def _strings(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            text = value.strip()
            return [text] if len(text) >= 2 else []
        if isinstance(value, dict):
            items: list[str] = []
            for nested in value.values():
                items.extend(cls._strings(nested))
            return items
        if isinstance(value, (list, tuple, set)):
            items = []
            for nested in value:
                items.extend(cls._strings(nested))
            return items
        return []
