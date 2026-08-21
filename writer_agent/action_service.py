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

from .schema import (
    ActionCommand,
    CreativeCard,
    DirectionCard,
    OriginalProjectActionPayload,
    ProposedAction,
    WriterActionError,
)
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


class ActionService:
    """Single decision boundary shared by HTTP buttons and chat commands."""

    def __init__(
        self,
        *,
        store: WriterAgentStore,
        runner: Any,
        audit_service: Any | None = None,
        permission_service: Any | None = None,
        workflow_repository: Any | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.audit_service = audit_service
        self.permission_service = permission_service
        self.workflow_repository = workflow_repository

    def _require_owned_action(self, command: ActionCommand) -> dict[str, Any]:
        session = self.store.get(command.session_id, command.actor.user_id)
        if session is None:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "动作不存在或无权访问")
        action = self.store.require_action(command.action_id, command.actor.user_id)
        if int(action["session_id"]) != int(command.session_id):
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "动作不存在或无权访问")
        return action

    def _require_current_write_authority(self, action: dict[str, Any], actor: Any) -> None:
        """Recheck the live authority required by the selected action."""
        if action["action_type"] == "project.create_original":
            if not (
                actor.is_system_admin or "project.create" in actor.permissions
            ):
                raise WriterActionError("PROJECT_CREATE_DENIED", "无权创建原创项目")
            return
        project_id = action.get("project_id")
        if project_id is None:
            raise WriterActionError("PROJECT_ACCESS_DENIED", "关联项目不可写")
        if self.permission_service is None or self.workflow_repository is None:
            return
        project_key = str(project_id)
        if action["action_type"] == "project.link":
            if not self.permission_service.can_read_project(actor, project_key):
                raise WriterActionError("PROJECT_ACCESS_DENIED", "无权关联该项目")
            return
        is_active_member = self.workflow_repository.is_active_project_member(
            project_key, actor
        )
        can_write = self.permission_service.can_create_project_content(
            actor, project_key, active_member=is_active_member
        )
        if not can_write:
            raise WriterActionError("PROJECT_ACCESS_DENIED", "无权编辑当前项目")

    def _audit(
        self,
        *,
        actor: Any,
        event: str,
        action: dict[str, Any],
        error_code: str | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        record = getattr(self.audit_service, "record", None)
        if not callable(record):
            return
        try:
            record(
                actor=actor,
                action=event,
                target_type="writer_action",
                target_id=str(action["id"]),
                project_id=(
                    str(action["project_id"])
                    if action.get("project_id") is not None
                    else None
                ),
                before=None,
                after={
                    "action_id": action["id"],
                    "project_id": action.get("project_id"),
                    "stage": action.get("target_stage"),
                    "status": action.get("status"),
                    "phase": action.get("phase"),
                    **({"error_code": error_code} if error_code else {}),
                },
                reason=None,
                result="success",
            )
        except TypeError:
            # Older audit adapters use positional request-oriented signatures.
            return

    def decide(self, command: ActionCommand) -> dict[str, Any]:
        owned = self._require_owned_action(command)
        self._require_current_write_authority(owned, command.actor)
        if command.decision == "cancel":
            action, requested = self.store.request_action_cancellation_once(
                command.action_id,
                expected_revision=command.expected_revision,
                client_request_id=command.client_request_id,
            )
            if not requested:
                if (
                    action["status"] in {"queued", "running"}
                    and action.get("phase") == "cancelling"
                ):
                    return self.runner.cancel(action["id"], command.actor)
                return action
            cancelled = (
                action
                if action["status"] == "cancelled"
                else self.runner.cancel(action["id"], command.actor)
            )
            self._audit(
                actor=command.actor, event="writer.action.cancel", action=cancelled
            )
            return cancelled
        action, claimed = self.store.claim_pending_action_once(
            command.action_id,
            expected_revision=command.expected_revision,
            client_request_id=command.client_request_id,
        )
        if not claimed:
            return action
        if command.decision == "confirm":
            self._audit(
                actor=command.actor, event="writer.action.claim", action=action
            )
            self.runner.enqueue(action["id"], command.actor)
            return action
        raise WriterActionError("ACTION_STATE_INVALID", "动作决策无效")

    def retry(
        self,
        *,
        session_id: int,
        action_id: str,
        actor: Any,
        client_request_id: str,
    ) -> dict[str, Any]:
        session = self.store.get(session_id, actor.user_id)
        if session is None:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "动作不存在或无权访问")
        source = self.store.require_action(action_id, actor.user_id)
        if int(source["session_id"]) != int(session_id):
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "动作不存在或无权访问")
        self._require_current_write_authority(source, actor)
        retry, created = self.runner.retry_failed_once(
            action_id,
            actor,
            retry_request_id=client_request_id,
        )
        if created:
            self._audit(actor=actor, event="writer.action.retry", action=retry)
            self.runner.enqueue(retry["id"], actor)
        return retry


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


def build_project_authority_context(
    repository: Any, project_id: int | None
) -> dict[str, Any]:
    """Load locked creative outcomes from the server-owned project repository."""
    if repository is None or project_id is None:
        return {}
    project = repository.get_project(project_id)
    if project is None:
        raise WriterActionError("PROJECT_NOT_AVAILABLE", "关联的创作项目不存在")

    context: dict[str, Any] = {
        "project_id": int(project_id),
        "revision": int(project.get("revision") or 0),
        "resource_refs": [],
    }
    locked_brief_id = project.get("locked_brief_id")
    locked_brief = next(
        (
            item
            for item in project.get("blueprints") or []
            if item.get("id") == locked_brief_id
        ),
        None,
    )
    if locked_brief is not None:
        context["locked_brief"] = {
            "id": int(locked_brief["id"]),
            "version": int(locked_brief.get("version") or 1),
            "content": locked_brief.get("content") or {},
        }
        context["resource_refs"].append({
            "resource_type": "brief",
            "resource_id": str(locked_brief["id"]),
            "version": int(locked_brief.get("version") or 1),
        })

    character_batch_id = project.get("character_batch_id")
    if character_batch_id:
        characters = [
            {
                "id": int(item["id"]),
                "version": int(item.get("version") or 1),
                "content": item.get("content") or {},
            }
            for item in project.get("characters") or []
            if item.get("batch_id") == character_batch_id
            and item.get("status") in {"locked", "approved"}
        ]
        relationships = [
            {
                "id": int(item["id"]),
                "version": int(item.get("version") or 1),
                "content": item.get("content") or {},
            }
            for item in project.get("relationships") or []
            if item.get("batch_id") == character_batch_id
            and item.get("status") in {"locked", "approved"}
        ]
        if characters:
            version = max(item["version"] for item in [*characters, *relationships])
            context["locked_characters"] = {
                "batch_id": str(character_batch_id),
                "version": version,
                "characters": characters,
                "relationships": relationships,
            }
            context["resource_refs"].append({
                "resource_type": "characters",
                "resource_id": f"{project_id}:{character_batch_id}",
                "version": version,
            })

    outline_batch_id = project.get("outline_batch_id")
    if outline_batch_id:
        outlines = [
            {
                "id": int(item["id"]),
                "episode_number": int(item["episode_number"]),
                "version": int(item.get("version") or 1),
                "content": item.get("content") or {},
            }
            for item in project.get("episodes") or []
            if item.get("stage") == "outline"
            and item.get("batch_id") == outline_batch_id
            and item.get("status") in {"locked", "approved"}
        ]
        if outlines:
            version = max(item["version"] for item in outlines)
            context["locked_outline"] = {
                "batch_id": str(outline_batch_id),
                "version": version,
                "episodes": outlines,
            }
            context["resource_refs"].append({
                "resource_type": "outline",
                "resource_id": f"{project_id}:{outline_batch_id}",
                "version": version,
            })

    for stage in ("beats", "script"):
        locked = [
            {
                "id": int(item["id"]),
                "episode_number": int(item["episode_number"]),
                "version": int(item.get("version") or 1),
                "content": item.get("content") or {},
            }
            for item in project.get("episodes") or []
            if item.get("stage") == stage
            and item.get("status") in {"locked", "approved"}
        ]
        if locked:
            context[f"locked_{stage}"] = locked
            context["resource_refs"].extend(
                {
                    "resource_type": stage,
                    "resource_id": str(item["id"]),
                    "version": item["version"],
                    "episode_number": item["episode_number"],
                }
                for item in locked
            )
    return context


def validate_project_authority(
    cards: list[CreativeCard],
    actions: list[ProposedAction],
    project_context: dict[str, Any],
) -> None:
    """Reject model output that would silently replace a locked outcome."""
    if not project_context:
        return

    authority_keys = {
        "brief": "locked_brief",
        "characters": "locked_characters",
        "outline": "locked_outline",
        "beats": "locked_beats",
        "script": "locked_script",
    }
    allowed_brief_cards: set[str] = set()

    for action in actions:
        stage = action.target_stage
        if stage is None:
            continue
        locked = project_context.get(authority_keys[stage])
        rework_stage = action.payload.get("rework_stage")
        explicit_rework = (
            action.action_type == "creative.run_plan"
            and rework_stage == stage
        )
        if rework_stage is not None and not explicit_rework:
            raise WriterActionError(
                "LOCKED_RESULT_CONFLICT", "重做阶段必须与目标阶段一致"
            )
        if explicit_rework and not locked:
            raise WriterActionError(
                "LOCKED_RESULT_CONFLICT", f"没有可重做的已锁定 {stage}"
            )
        if not locked:
            continue

        expected_ids: list[str]
        if stage == "brief":
            expected_ids = [str(locked["id"])]
        elif stage in {"characters", "outline"}:
            expected_ids = [
                f"{int(project_context['project_id'])}:{locked['batch_id']}"
            ]
        else:
            start = action.episode_start
            end = action.episode_end
            locked_by_episode = {
                int(item["episode_number"]): str(item["id"])
                for item in locked
            }
            expected_ids = (
                [
                    locked_by_episode[number]
                    for number in range(start, end + 1)
                    if number in locked_by_episode
                ]
                if start is not None and end is not None
                else []
            )

        if explicit_rework:
            if stage not in {"beats", "script"} and (
                action.episode_start is not None or action.episode_end is not None
            ):
                raise WriterActionError(
                    "LOCKED_RESULT_CONFLICT", "非分集成果重做不能指定集数范围"
                )
            if stage in {"beats", "script"}:
                start = action.episode_start
                end = action.episode_end
                if (
                    start is None
                    or end is None
                    or len(expected_ids) != end - start + 1
                ):
                    raise WriterActionError(
                        "LOCKED_RESULT_CONFLICT",
                        f"重做范围必须全部对应当前已锁定 {stage}",
                    )
            supplied_scalar = next(
                (
                    action.payload.get(key)
                    for key in (
                        "replaces_resource_id",
                        "current_resource_id",
                        "resource_id",
                    )
                    if action.payload.get(key) is not None
                ),
                None,
            )
            supplied_list = next(
                (
                    action.payload.get(key)
                    for key in ("replaces_resource_ids", "resource_ids")
                    if action.payload.get(key) is not None
                ),
                None,
            )
            step_payloads = action.payload.get("step_payloads")
            nested_replacements = (
                [
                    str(step_payload["replaces_resource_id"])
                    for step_payload in step_payloads.values()
                    if isinstance(step_payload, dict)
                    and step_payload.get("replaces_resource_id") is not None
                ]
                if isinstance(step_payloads, dict)
                else []
            )
            if supplied_scalar is not None and (
                len(expected_ids) != 1
                or str(supplied_scalar) != expected_ids[0]
            ):
                raise WriterActionError(
                    "LOCKED_RESULT_CONFLICT", "重做成果与当前权威版本不一致"
                )
            if supplied_list is not None and (
                not isinstance(supplied_list, list)
                or [str(item) for item in supplied_list] != expected_ids
            ):
                raise WriterActionError(
                    "LOCKED_RESULT_CONFLICT", "重做成果与当前权威版本不一致"
                )
            if nested_replacements and sorted(nested_replacements) != sorted(
                expected_ids
            ):
                raise WriterActionError(
                    "LOCKED_RESULT_CONFLICT", "重做成果与当前权威版本不一致"
                )
            if stage == "brief" and action.source_card_id:
                allowed_brief_cards.add(action.source_card_id)
            continue

        if stage in {"brief", "characters", "outline"}:
            raise WriterActionError(
                "LOCKED_RESULT_CONFLICT", f"模型动作不得覆盖已锁定 {stage}"
            )
        locked_episodes = {int(item["episode_number"]) for item in locked}
        start = action.episode_start
        end = action.episode_end
        if start is not None and end is not None and locked_episodes.intersection(
            range(start, end + 1)
        ):
            raise WriterActionError(
                "LOCKED_RESULT_CONFLICT", f"模型动作不得覆盖已锁定 {stage}"
            )

    if project_context.get("locked_brief") and any(
        getattr(card, "card_type", "") == "directions"
        or (
            getattr(card, "card_type", "") == "brief"
            and getattr(card, "card_id", "") not in allowed_brief_cards
        )
        for card in cards
    ):
        raise WriterActionError(
            "LOCKED_RESULT_CONFLICT", "模型输出不得覆盖已锁定 Brief"
        )


def requires_direction_card(
    materials: list[dict[str, Any]],
    message: str,
    project_context: dict[str, Any],
) -> bool:
    if (
        project_context.get("project_id")
        or len(materials) != 1
        or "跳过方向" in message
    ):
        return False
    inspiration = _normal_text(materials[0].get("content")).strip()
    if (
        not inspiration
        or "完整故事" in inspiration
        or "\n" in inspiration
        or len(inspiration) > 200
    ):
        return False
    if re.search(r"第\s*\d+\s*集", inspiration):
        return False
    return any(word in message for word in ("方向", "灵感", "创意", "故事方案"))


def validate_direction_requirement(
    cards: list[CreativeCard], direction_required: bool
) -> None:
    if direction_required and not any(
        isinstance(card, DirectionCard) for card in cards
    ):
        raise WriterActionError(
            "RESULT_VALIDATION_FAILED", "一句话灵感必须先生成 3–5 个创作方向"
        )


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
    if any(
        isinstance(card, dict)
        and card.get("card_type") in {"progress", "result"}
        for card in raw_cards
    ):
        raise WriterActionError(
            "RESULT_VALIDATION_FAILED",
            "进度与成果卡只能由服务端动作账本生成",
        )
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


def validate_original_project_action_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only complete project-creation inputs from a durable action."""
    try:
        return OriginalProjectActionPayload.model_validate(payload).model_dump(
            exclude={"assumptions"}
        )
    except ValidationError as exc:
        raise WriterActionError(
            "RESULT_VALIDATION_FAILED", "原创项目创建资料不完整或无效"
        ) from exc


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
    client_message_id: str | None = None,
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
    for index, action in enumerate(proposed_actions):
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
            idempotency_key=(
                f"session:{session_id}:message:{client_message_id}:action:{index}"
                if client_message_id
                else f"model-{uuid4().hex}"
            ),
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
    confirm_positions = [text.find(word) for word in _CONFIRM_WORDS if word in text]
    cancel_positions = [text.find(word) for word in _CANCEL_WORDS if word in text]
    is_confirm = bool(confirm_positions)
    is_cancel = bool(cancel_positions) and (
        not confirm_positions or min(cancel_positions) < min(confirm_positions)
    )
    candidates_by_state = [
        action
        for action in session_actions
        if action.get("status")
        in ({"pending", "queued", "running"} if is_cancel else {"pending"})
    ]
    if not candidates_by_state or not (is_confirm or is_cancel):
        return NaturalLanguageDecision(action_id=None, decision=None)
    candidates = _candidate_actions(text, candidates_by_state)
    if len(candidates) != 1:
        raise WriterActionError("ACTION_AMBIGUOUS", "无法唯一匹配待确认动作")
    return NaturalLanguageDecision(
        action_id=str(candidates[0]["id"]),
        decision="cancel" if is_cancel else "confirm",
        revision_required=not is_cancel and bool(_NEW_CONSTRAINT_PATTERN.search(text)),
    )
