# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from unicodedata import normalize
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .schema import CreativeCard, DirectionCard, ProposedAction, WriterActionError
from .store import WriterAgentStore


_CARDS = TypeAdapter(list[CreativeCard])
_STAGE_WORDS = {
    "brief": ("简介", "梗概", "立项", "brief"),
    "characters": ("人物", "角色", "characters"),
    "outline": ("大纲", "outline"),
    "beats": ("节拍", "beats"),
    "script": ("剧本", "脚本", "script"),
}
_CONFIRM_WORDS = ("采用", "确认", "开始", "生成", "继续")
_CANCEL_WORDS = ("取消", "放弃", "不要")
_NEW_CONSTRAINT_PATTERN = re.compile(r"(?:但|不过|改成|改为|调整|要求|必须|不要|限制|同时)")


@dataclass(frozen=True)
class NaturalLanguageDecision:
    action_id: str | None
    decision: str | None
    revision_required: bool = False


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normal_text(value: Any) -> str:
    return normalize("NFC", str(value or "").replace("\r\n", "\n").replace("\r", "\n"))


def build_source_snapshot(
    *,
    materials: list[dict[str, Any]],
    selected_messages: list[dict[str, Any]],
    project_context: dict[str, Any],
    reference_refs: list[dict[str, Any]],
    assumptions: list[str],
    model_version: str,
    prompt_version: str,
) -> dict[str, Any]:
    """Create a canonical, auditable input snapshot without reading external resources."""
    normalized_materials = [
        {"name": _normal_text(item.get("name")), "content": _normal_text(item.get("content"))}
        for item in materials
    ]
    normalized_messages = [
        {key: _normal_text(value) if isinstance(value, str) else value for key, value in item.items()}
        for item in selected_messages
        if isinstance(item, dict)
    ]
    references = [
        {
            "resource_type": _normal_text(item.get("resource_type")),
            "resource_id": item.get("resource_id"),
            "name": _normal_text(item.get("name")),
            "version": item.get("version"),
            "readable": bool(item.get("readable")),
        }
        for item in reference_refs
    ]
    snapshot = {
        "project_context": project_context,
        "materials": normalized_materials,
        "selected_messages": normalized_messages,
        "references": references,
        "assumptions": [_normal_text(item) for item in assumptions],
        "model_version": _normal_text(model_version),
        "prompt_version": _normal_text(prompt_version),
    }
    snapshot["content_fingerprint"] = hashlib.sha256(
        _canonical(snapshot).encode("utf-8")
    ).hexdigest()
    return snapshot


def validate_cards(cards: list[CreativeCard]) -> list[CreativeCard]:
    for card in cards:
        if isinstance(card, DirectionCard):
            for index, left in enumerate(card.items):
                for right in card.items[index + 1:]:
                    values = {
                        "protagonist": (left.protagonist, right.protagonist),
                        "background": (left.background, right.background),
                        "conflict": (left.core_conflict, right.core_conflict),
                        "relationship": (left.relationship_engine, right.relationship_engine),
                        "emotional_payoff": (left.emotional_payoff, right.emotional_payoff),
                    }
                    actual = {key for key, pair in values.items() if _normal_text(pair[0]) != _normal_text(pair[1])}
                    left_declared = set(left.difference_dimensions)
                    right_declared = set(right.difference_dimensions)
                    if (
                        len(actual) < 2
                        or len(actual & left_declared) < 2
                        or len(actual & right_declared) < 2
                    ):
                        raise WriterActionError(
                            "RESULT_VALIDATION_FAILED", "方向候选缺少两个实际且声明的差异维度"
                        )
    return cards


def parse_creative_cards(payload: dict[str, Any]) -> list[CreativeCard]:
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        return []
    try:
        return validate_cards(_CARDS.validate_python(raw_cards))
    except ValidationError as exc:
        raise WriterActionError("RESULT_VALIDATION_FAILED", "创作卡片格式无效") from exc


def parse_proposed_actions(payload: dict[str, Any]) -> list[ProposedAction]:
    raw_actions = payload.get("proposed_actions")
    if not isinstance(raw_actions, list):
        return []
    actions: list[ProposedAction] = []
    for raw_action in raw_actions:
        try:
            actions.append(ProposedAction.model_validate(raw_action))
        except ValidationError:
            # Model output is untrusted: leave its reply visible, but never persist it as an action.
            continue
    return actions


def persist_proposed_actions(
    store: WriterAgentStore,
    *,
    session_id: int,
    actor_user_id: str,
    proposed_actions: list[ProposedAction],
    materials: list[dict[str, Any]],
    selected_messages: list[dict[str, Any]],
    project_context: dict[str, Any],
    reference_refs: list[dict[str, Any]] | None = None,
    model_version: str = "writer-agent",
    prompt_version: str = "creative-v1",
) -> list[dict[str, Any]]:
    if not proposed_actions:
        return []
    assumptions = [item for action in proposed_actions for item in action.assumptions]
    snapshot = build_source_snapshot(
        materials=materials,
        selected_messages=selected_messages,
        project_context=project_context,
        reference_refs=reference_refs or [],
        assumptions=assumptions,
        model_version=model_version,
        prompt_version=prompt_version,
    )
    saved_snapshot = store.save_source_snapshot(snapshot)
    saved_actions: list[dict[str, Any]] = []
    for action in proposed_actions:
        payload = action.payload.copy()
        payload.update({
            "title": action.title,
            "summary": action.summary,
            "source_card_id": action.source_card_id,
            "target_stage": action.target_stage,
            "episode_start": action.episode_start,
            "episode_end": action.episode_end,
            "assumptions": action.assumptions,
        })
        project_id = action.project_id
        if project_id is None and isinstance(project_context.get("project_id"), int):
            project_id = project_context["project_id"]
        request_hash = hashlib.sha256(
            _canonical({"action_type": action.action_type, "project_id": project_id, "payload": payload, "snapshot": saved_snapshot["fingerprint"]}).encode("utf-8")
        ).hexdigest()
        saved_actions.append(store.create_action(
            session_id=session_id,
            actor_user_id=actor_user_id,
            action_type=action.action_type,
            project_id=project_id,
            idempotency_key=f"model-{uuid4().hex}",
            request_hash=request_hash,
            payload=payload,
            expected_context=project_context,
            source_snapshot_id=saved_snapshot["id"],
        ))
    return saved_actions


def _candidate_actions(message: str, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stage_matches = [
        stage for stage, words in _STAGE_WORDS.items()
        if any(word in message.lower() for word in words)
    ]
    if stage_matches:
        return [action for action in actions if action.get("target_stage") in stage_matches]
    if "项目" in message:
        return [action for action in actions if str(action.get("action_type", "")).startswith("project.")]
    return actions


def match_natural_language_decision(
    message: str, session_actions: list[dict[str, Any]]
) -> NaturalLanguageDecision:
    text = _normal_text(message).strip()
    pending = [action for action in session_actions if action.get("status") == "pending"]
    is_cancel = any(word in text for word in _CANCEL_WORDS)
    is_confirm = any(word in text for word in _CONFIRM_WORDS)
    if not pending or not (is_confirm or is_cancel):
        return NaturalLanguageDecision(action_id=None, decision=None)
    candidates = _candidate_actions(text, pending)
    if len(candidates) != 1:
        raise WriterActionError("ACTION_AMBIGUOUS", "无法唯一匹配待确认动作")
    return NaturalLanguageDecision(
        action_id=str(candidates[0]["id"]),
        decision="cancel" if is_cancel else "confirm",
        revision_required=not is_cancel and bool(_NEW_CONSTRAINT_PATTERN.search(text)),
    )
