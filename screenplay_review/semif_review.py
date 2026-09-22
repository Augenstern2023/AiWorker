# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Experimental screenplay review orchestration over SemIf option scores."""

from __future__ import annotations

import hashlib
import inspect
from typing import Any

from .semif_contract import (
    SEMIF_RULE_PACKAGE,
    SEMIF_UPSTREAM_COMMIT,
    SemIfScoreRequest,
)
from .semif_rules import OPTION_LABELS, RULES, RULES_BY_ID, criteria
from .semif_state import STATE_PACKAGE_VERSION, build_semif_state_windows


SEMIF_REPORT_VERSION = "screenplay-semif-report-v2"
_OPTION_RANK = {
    "excellent": 0,
    "passed": 0,
    "good": 1,
    "needs_revision": 2,
    "failed": 3,
    "insufficient_evidence": 4,
}


async def review_source_with_semif(
    client: Any,
    source: dict[str, Any],
    *,
    on_progress: Any = None,
    on_checkpoint: Any = None,
    on_request: Any = None,
    initial_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score deterministic state windows while keeping all probabilities auditable."""
    windows = build_semif_state_windows(source)
    saved = _saved_states(initial_report)
    completed: list[dict[str, Any]] = []
    for state_index, window in enumerate(windows, start=1):
        previous = saved.get(window.id)
        if previous and previous.get("state_sha256") == window.to_manifest()["state_sha256"]:
            completed.append(previous)
            continue
        await _call(
            on_request,
            {
                "kind": "semif_state",
                "engine": "semif",
                "episode_number": window.episode_number,
                "state_id": window.id,
                "state_index": state_index,
                "state_total": len(windows),
            },
        )
        request = SemIfScoreRequest(
            request_id=_request_id(source, window.id),
            mode="shared",
            state=window.state,
            criteria=criteria(),
            state_refs=[dict(item) for item in window.state_refs],
        )
        response = await client.score(request)
        record = {
            **window.to_manifest(),
            "request_id": request.request_id,
            "mode": response.mode,
            "timing": response.timing,
            "scores": [
                {
                    **score.model_dump(mode="json", exclude_none=True),
                    "selected_option_id": score.selected_option_id,
                }
                for score in response.scores
            ],
        }
        completed.append(record)
        completed.sort(key=lambda item: str(item.get("id") or ""))
        report = _build_report(source, windows, completed, partial=True)
        await _call(on_checkpoint, report)
        await _call(on_progress, len(completed), len(windows))
    return _build_report(source, windows, completed, partial=False)


def _saved_states(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict) or report.get("partial") is not True:
        return {}
    if report.get("engine") != "semif":
        return {}
    payload = report.get("semif") or {}
    if payload.get("report_version") != SEMIF_REPORT_VERSION:
        return {}
    return {
        str(item.get("id") or ""): item
        for item in payload.get("states") or []
        if isinstance(item, dict) and item.get("id")
    }


def _build_report(
    source: dict[str, Any],
    windows: list[Any],
    states: list[dict[str, Any]],
    *,
    partial: bool,
) -> dict[str, Any]:
    decisions = _aggregate_decisions(states)
    findings = _findings(decisions)
    episode_reviews = _episode_reviews(states)
    completed = len(states)
    total = len(windows)
    status = "部分状态包已评分" if partial else "全部状态包已评分"
    context_was_compacted = any(
        bool(getattr(window, "state", {}).get("context_compacted"))
        for window in windows
    )
    summary = (
        f"本地初审{status}（{completed}/{total}）。结果仅表示给定选项之间的条件评分，"
        "尚未经过剧本业务校准，必须由人工复核；系统未生成因果解释或原句证据。"
    )
    if context_was_compacted:
        summary += " 辅助背景已自动精简，剧本正文未裁剪。"
    return {
        "ai_verdict": "建议修改",
        "summary": summary,
        "core_findings": findings[:5],
        "episode_reviews": episode_reviews,
        "cross_episode_findings": [],
        "engine": "semif",
        "quality_status": "manual_review",
        "manual_review_required": True,
        "partial": partial,
        "checkpoint_key": f"semif:{completed}/{total}",
        "strategy": {
            "engine": "semif",
            "mode": "shared",
            "state_package_version": STATE_PACKAGE_VERSION,
            "state_count": total,
            "completed_state_count": completed,
            "source_was_truncated": False,
            "context_was_compacted": context_was_compacted,
        },
        "semif": {
            "report_version": SEMIF_REPORT_VERSION,
            "rule_package": SEMIF_RULE_PACKAGE,
            "upstream_commit": SEMIF_UPSTREAM_COMMIT,
            "probability_status": (
                "conditional option scores; not calibrated as screenplay confidence"
            ),
            "decisions": decisions,
            "states": states,
            "source_fingerprint": source.get("fingerprint"),
        },
    }


def _aggregate_decisions(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for rule in RULES:
        observations = []
        for state in states:
            score = next(
                (
                    item
                    for item in state.get("scores") or []
                    if item.get("id") == rule.id
                ),
                None,
            )
            if score is None:
                continue
            observations.append(
                {
                    "state_id": state.get("id"),
                    "episode_number": state.get("episode_number"),
                    "selected_option_id": score.get("selected_option_id"),
                    "option_ids": score.get("option_ids"),
                    "probabilities": score.get("probabilities"),
                    "option_logits": score.get("option_logits"),
                    "prompt_sha256": score.get("prompt_sha256"),
                    "prompt_version": score.get("prompt_version"),
                    "input_tokens": score.get("input_tokens"),
                    "model": score.get("model"),
                    "state_refs": state.get("state_refs") or [],
                }
            )
        if not observations:
            continue
        selected = max(
            observations,
            key=lambda item: _OPTION_RANK.get(
                str(item.get("selected_option_id") or "insufficient_evidence"), 4
            ),
        )["selected_option_id"]
        result.append(
            {
                "id": rule.id,
                "label": rule.label,
                "scope": rule.scope,
                "kind": rule.kind,
                "selected_option_id": selected,
                "observations": observations,
            }
        )
    return result


def _findings(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for decision in decisions:
        selected = str(decision.get("selected_option_id") or "")
        if selected in {"passed", "excellent", "good"}:
            continue
        episodes = sorted(
            {
                int(item.get("episode_number") or 0)
                for item in decision.get("observations") or []
                if int(item.get("episode_number") or 0) > 0
            }
        )
        findings.append(
            {
                "severity": "P1" if selected == "failed" else "P2",
                "rule_id": decision.get("id"),
                "category": "semif_experimental",
                "episode_number": episodes[0] if len(episodes) == 1 else None,
                "title": (
                    f"{decision.get('label') or decision.get('id')}："
                    f"{OPTION_LABELS.get(selected, selected)}（待人工复核）"
                ),
                "outline_anchor": {},
                "beat_orders": [],
                "change_route": "script_only",
                "evidence": "",
                "suggestion": "",
                "state_refs": [
                    reference
                    for item in decision.get("observations") or []
                    for reference in item.get("state_refs") or []
                ],
            }
        )
    return findings


def _episode_reviews(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numbers = sorted(
        {
            int(state.get("episode_number") or 0)
            for state in states
            if int(state.get("episode_number") or 0) > 0
        }
    )
    result = []
    for number in numbers:
        issue_labels = []
        for state in states:
            if int(state.get("episode_number") or 0) != number:
                continue
            for score in state.get("scores") or []:
                selected = str(score.get("selected_option_id") or "")
                if selected not in {"passed", "excellent", "good"}:
                    rule = RULES_BY_ID.get(str(score.get("id") or ""))
                    issue_labels.append(rule.label if rule else str(score.get("id") or ""))
        labels = "、".join(dict.fromkeys(issue_labels))
        summary = "本地初审未标记需复核维度。" if not labels else f"待人工复核：{labels}。"
        result.append({"episode_number": number, "summary": summary, "findings": []})
    return result


def _request_id(source: dict[str, Any], state_id: str) -> str:
    seed = f"{source.get('fingerprint') or ''}:{state_id}:{SEMIF_RULE_PACKAGE}"
    return f"screenplay-{hashlib.sha256(seed.encode()).hexdigest()[:24]}"


async def _call(callback: Any, *args: Any) -> None:
    if callback is None:
        return
    result = callback(*args)
    if inspect.isawaitable(result):
        await result
