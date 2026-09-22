# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Orchestrate normalized, scoped SemIf decisions and a legacy-compatible report."""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any, Callable

from .aggregator import aggregate_review
from .normalizer import normalize_script_package
from .rules import RULE_PACKAGE, decision_set_for
from .schema import (
    EpisodePackage,
    ScenePackage,
    ScriptPackage,
    SemIfDecision,
    SemIfReviewRequest,
)


MAX_WINDOW_CHARS = 4_000
MAX_EPISODE_WINDOW_CHARS = 12_000
MAX_EPISODE_WINDOWS = 16


async def run_semif_review(
    source: dict[str, Any],
    *,
    semif: Any,
    writer_provider: Any = None,
    model_profile: str,
    initial_report: dict[str, Any] | None = None,
    scopes: set[str] | None = None,
    on_progress: Callable[..., Any] | None = None,
    on_checkpoint: Callable[..., Any] | None = None,
    on_request: Callable[..., Any] | None = None,
    prompt_snapshot: dict[str, Any] | None = None,
    review_version: str = "review-v001",
    rerun_scope: str = "resume",
) -> dict[str, Any]:
    """Run project, episode and scene scopes with durable scope-key checkpoints."""

    package = normalize_script_package(source)
    checkpoint = _resume_checkpoint(initial_report, package.source_fingerprint)
    decisions = _prepare_rerun_decisions(
        package,
        initial_report=initial_report,
        decisions=list(checkpoint["decisions"]),
        rerun_scope=rerun_scope,
    )
    model = dict(checkpoint["model"])
    work = _work_items(package)
    selected_scopes = scopes or {"project", "episode", "scene"}
    completed = {
        scope_key
        for scope, scope_ref, scope_key, _summary, _windows in work
        if not _missing_rules(scope, scope_ref, decisions)
    }
    pending = [item for item in work if item[0] in selected_scopes and item[2] not in completed]
    total = len([item for item in work if item[0] in selected_scopes])
    done = total - len(pending)

    for scope, scope_ref, scope_key, summary, windows in pending:
        rules = _missing_rules(scope, scope_ref, decisions)
        if not rules:
            completed.add(scope_key)
            continue
        request = SemIfReviewRequest(
            client_request_id=_request_id(package.source_fingerprint, scope_key),
            rule_package=RULE_PACKAGE,
            model_profile=model_profile,
            scope=scope,  # type: ignore[arg-type]
            scope_ref=scope_ref,
            context_summary={
                **summary,
                "parent_decisions": _parent_decision_summary(
                    package,
                    scope=scope,
                    scope_ref=scope_ref,
                    decisions=decisions,
                ),
            },
            content_windows=windows,
            decisions=[item.to_contract() for item in rules],
        )
        await _call(
            on_request,
            {
                "kind": "semif_decision",
                "scope": scope,
                "scope_ref": scope_ref,
                "scope_key": scope_key,
                "client_request_id": request.client_request_id,
                "decision_count": len(rules),
                "content_window_count": len(windows),
                "content_chars": sum(len(str(item.get("text") or "")) for item in windows),
            },
        )
        response = await semif.review(request)
        decisions.extend(response.decisions)
        model = {str(key): str(value) for key, value in response.model.items()}
        completed.add(scope_key)
        done += 1
        partial = _checkpoint_payload(
            package,
            decisions=decisions,
            completed_scope_keys=completed,
            model=model,
            checkpoint_key=scope_key,
            completed_count=done,
            total_count=total,
        )
        await _call(on_checkpoint, partial)
        await _call_progress(on_progress, done, total)

    decisions.extend(_missing_scene_decisions(package, decisions))
    prompt_hash = _prompt_hash(prompt_snapshot)
    report = aggregate_review(
        decisions,
        input_snapshot_hash=package.source_fingerprint,
        model=model or {"name": model_profile, "revision": "unknown"},
        prompt_hash=prompt_hash,
        review_version=review_version,
    )
    payload = report.model_dump(mode="json")
    if writer_provider is not None:
        explanation = await writer_provider.complete_json(_writer_messages(payload))
        if isinstance(explanation, dict):
            if str(explanation.get("summary") or "").strip():
                payload["summary"] = str(explanation["summary"]).strip()
            payload["revision_plan"] = [
                dict(item)
                for item in explanation.get("revision_plan") or []
                if isinstance(item, dict)
            ]
            if explanation.get("core_findings"):
                payload["core_findings"] = [
                    dict(item)
                    for item in explanation.get("core_findings") or []
                    if isinstance(item, dict)
                ]
    payload["partial"] = False
    payload["checkpoint_key"] = "final_summary"
    return payload


def _work_items(
    package: ScriptPackage,
) -> list[tuple[str, str, str, dict[str, Any], list[dict[str, Any]]]]:
    work: list[tuple[str, str, str, dict[str, Any], list[dict[str, Any]]]] = [
        (
            "project",
            "project",
            "project:project",
            {
                "project": package.project.model_dump(mode="json"),
                "brief": package.brief,
                "episode_count": len(package.episodes),
                "character_count": len(package.characters),
                "relationship_count": len(package.relationships),
                "characters": package.characters,
                "relationships": package.relationships,
                "episode_titles": [item.title for item in package.episodes],
                "episode_structure": [
                    {
                        "episode_id": item.episode_id,
                        "episode_number": item.episode_number,
                        "title": item.title,
                        "outline": item.outline,
                        "beats": item.beats,
                    }
                    for item in package.episodes
                ],
            },
            [],
        )
    ]
    for episode in package.episodes:
        work.append(
            (
                "episode",
                episode.episode_id,
                f"episode:{episode.episode_id}",
                _episode_summary(episode),
                _episode_windows(episode),
            )
        )
    for episode in package.episodes:
        for scene in episode.scenes:
            if not scene.text.strip():
                continue
            work.append(
                (
                    "scene",
                    scene.scene_id,
                    f"scene:{scene.scene_id}",
                    {
                        "episode_id": episode.episode_id,
                        "episode_number": episode.episode_number,
                        "scene_id": scene.scene_id,
                        "scene_number": scene.scene_number,
                    },
                    [_scene_window(scene)],
                )
            )
    return work


def _parent_decision_summary(
    package: ScriptPackage,
    *,
    scope: str,
    scope_ref: str,
    decisions: list[SemIfDecision],
) -> list[dict[str, str]]:
    if scope == "project":
        return []
    episode_ref = scope_ref
    if scope == "scene":
        episode_ref = next(
            (
                episode.episode_id
                for episode in package.episodes
                for scene in episode.scenes
                if scene.scene_id == scope_ref
            ),
            "",
        )
    return [
        {
            "dimension_id": item.dimension_id,
            "scope": item.scope,
            "scope_ref": item.scope_ref,
            "selected_option": item.selected_option,
            "reason_code": item.reason_code,
        }
        for item in decisions
        if item.scope == "project"
        or (scope == "scene" and item.scope == "episode" and item.scope_ref == episode_ref)
    ]


def _episode_summary(episode: EpisodePackage) -> dict[str, Any]:
    return {
        "episode_id": episode.episode_id,
        "episode_number": episode.episode_number,
        "title": episode.title,
        "resource_id": episode.resource_id,
        "version": episode.version,
        "scene_count": len(episode.scenes),
        "outline": episode.outline,
        "beats": episode.beats,
    }


def _episode_windows(episode: EpisodePackage) -> list[dict[str, Any]]:
    scenes = [item for item in episode.scenes if item.text.strip()]
    if len(scenes) > MAX_EPISODE_WINDOWS:
        last = len(scenes) - 1
        indexes = sorted(
            {
                round(slot * last / (MAX_EPISODE_WINDOWS - 1))
                for slot in range(MAX_EPISODE_WINDOWS)
            }
        )
        scenes = [scenes[index] for index in indexes]
    if not scenes:
        return []
    per_scene_chars = min(
        MAX_WINDOW_CHARS,
        max(1, MAX_EPISODE_WINDOW_CHARS // len(scenes)),
    )
    return [_scene_window(item, max_chars=per_scene_chars) for item in scenes]


def _scene_window(
    scene: ScenePackage, *, max_chars: int = MAX_WINDOW_CHARS
) -> dict[str, Any]:
    text = scene.text[:max_chars]
    source_url = scene.evidence[0].source_url if scene.evidence else ""
    return {
        "scene_id": scene.scene_id,
        "episode_id": scene.episode_id,
        "resource_id": scene.resource_id,
        "version": scene.version,
        "start": 0,
        "end": len(text),
        "text": text,
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_url": source_url,
    }


def _missing_scene_decisions(
    package: ScriptPackage, existing: list[SemIfDecision]
) -> list[SemIfDecision]:
    result = []
    existing_keys = {
        (item.dimension_id, item.scope_ref)
        for item in existing
        if item.scope == "scene"
    }
    for episode in package.episodes:
        targets = [scene.scene_id for scene in episode.scenes if not scene.text.strip()]
        if not episode.scenes:
            targets.append(episode.episode_id)
        for scope_ref in targets:
            for rule in decision_set_for("scene"):
                if (rule.id, scope_ref) in existing_keys:
                    continue
                result.append(
                    SemIfDecision(
                        dimension_id=rule.id,
                        scope="scene",
                        scope_ref=scope_ref,
                        selected_option="insufficient_evidence",
                        option_probabilities={"insufficient_evidence": 1.0},
                        evidence=[],
                        reason_code="scene_has_no_text",
                        request_id="synthetic-insufficient-evidence",
                    )
                )
    return result


def _checkpoint_payload(
    package: ScriptPackage,
    *,
    decisions: list[SemIfDecision],
    completed_scope_keys: set[str],
    model: dict[str, str],
    checkpoint_key: str,
    completed_count: int,
    total_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": "screenplay-review-report-v2",
        "partial": True,
        "engine": "semif",
        "summary": f"已完成 {completed_count}/{total_count} 个 SemIf 审核范围。",
        "completed_episode_count": completed_count,
        "total_episode_count": total_count,
        "checkpoint_key": checkpoint_key,
        "_semif_checkpoint": {
            "input_snapshot_hash": package.source_fingerprint,
            "completed_scope_keys": sorted(completed_scope_keys),
            "decisions": [item.model_dump(mode="json") for item in decisions],
            "model": model,
        },
    }


def _resume_checkpoint(
    initial_report: dict[str, Any] | None, source_fingerprint: str
) -> dict[str, Any]:
    raw = initial_report if isinstance(initial_report, dict) else {}
    checkpoint = raw.get("_semif_checkpoint")
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    saved_hash = str(
        checkpoint.get("input_snapshot_hash") or raw.get("input_snapshot_hash") or ""
    )
    if saved_hash and saved_hash != source_fingerprint:
        raise ValueError("SemIf checkpoint source fingerprint does not match the frozen source")
    raw_decisions = checkpoint.get("decisions") or raw.get("semif_decisions") or []
    return {
        "completed_scope_keys": [
            str(item) for item in checkpoint.get("completed_scope_keys") or []
        ],
        "decisions": [
            SemIfDecision.model_validate(item)
            for item in raw_decisions
            if isinstance(item, dict)
        ],
        "model": dict(checkpoint.get("model") or raw.get("model") or {}),
    }


def _missing_rules(
    scope: str, scope_ref: str, decisions: list[SemIfDecision]
) -> tuple[Any, ...]:
    existing = {
        item.dimension_id
        for item in decisions
        if item.scope == scope and item.scope_ref == scope_ref
    }
    return tuple(
        item
        for item in decision_set_for(scope)  # type: ignore[arg-type]
        if item.id not in existing
    )


def _prepare_rerun_decisions(
    package: ScriptPackage,
    *,
    initial_report: dict[str, Any] | None,
    decisions: list[SemIfDecision],
    rerun_scope: str,
) -> list[SemIfDecision]:
    if rerun_scope == "resume":
        return decisions
    if rerun_scope == "full":
        return []
    report = initial_report if isinstance(initial_report, dict) else {}
    if not decisions:
        raise ValueError("局部复审缺少上一版本 SemIf 决策")
    weak_dimensions = {
        str(item.get("id") or item.get("dimension_id") or "")
        for item in report.get("dimensions") or []
        if isinstance(item, dict)
        and str(item.get("selected_option") or "")
        in {"failed", "needs_revision", "insufficient_evidence"}
    }
    weak_dimensions.update(
        str(item.get("dimension_id") or "")
        for item in report.get("blocking_issues") or []
        if isinstance(item, dict)
    )
    weak_dimensions.discard("")
    if rerun_scope == "failed_dimensions_only":
        if not weak_dimensions:
            raise ValueError("上一版本没有可复审的失败维度")
        return [item for item in decisions if item.dimension_id not in weak_dimensions]
    if rerun_scope != "affected_episodes":
        raise ValueError(f"未知复审范围: {rerun_scope}")
    affected_refs: set[str] = set()
    for item in report.get("dimensions") or []:
        if not isinstance(item, dict) or str(item.get("id") or "") not in weak_dimensions:
            continue
        affected_refs.update(str(value) for value in item.get("scope_refs") or [])
        for evidence in item.get("evidence") or []:
            if isinstance(evidence, dict):
                affected_refs.add(str(evidence.get("episode_id") or ""))
                affected_refs.add(str(evidence.get("scene_id") or ""))
    scene_episode = {
        scene.scene_id: episode.episode_id
        for episode in package.episodes
        for scene in episode.scenes
    }
    affected_episodes = {
        ref for ref in affected_refs if ref.startswith("ep-")
    } | {
        scene_episode[ref] for ref in affected_refs if ref in scene_episode
    }
    if not affected_episodes:
        raise ValueError("上一版本没有可定位的受影响集")
    return [
        item
        for item in decisions
        if not (
            item.scope == "episode" and item.scope_ref in affected_episodes
            or item.scope == "scene" and scene_episode.get(item.scope_ref) in affected_episodes
        )
    ]


def _request_id(source_fingerprint: str, scope_key: str) -> str:
    digest = hashlib.sha256(
        f"{RULE_PACKAGE}:{source_fingerprint}:{scope_key}".encode("utf-8")
    ).hexdigest()
    return f"semif-{digest[:32]}"


def _prompt_hash(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    value = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _writer_messages(report: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你只解释结构化剧本评审结果并给出修改计划。"
                "不得修改 quality_status、overall_score、hard_gate、dimensions 或 selected_option。"
                "只返回 summary、revision_plan 和可选 core_findings 的 JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(report, ensure_ascii=False, separators=(",", ":")),
        },
    ]


async def _call(callback: Callable[..., Any] | None, value: Any) -> None:
    if callback is None:
        return
    result = callback(value)
    if inspect.isawaitable(result):
        await result


async def _call_progress(
    callback: Callable[..., Any] | None, completed: int, total: int
) -> None:
    if callback is None:
        return
    result = callback(completed, total)
    if inspect.isawaitable(result):
        await result
