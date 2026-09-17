# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Script-grounded video generation parameter recommendations."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


RECOMMENDATION_SCHEMA_VERSION = 1
RECOMMENDATION_PROMPT_VERSION = "video-parameters-v1"
DEFAULT_ANALYSIS_DEADLINE_SECONDS = 60
SCENARIOS = ("platform_fit", "content_optimal")
MODEL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "minimax_h3": {
        "vendor": "MiniMax",
        "display_name": "MiniMax H3",
        "model_id": "MiniMax-H3",
        "resolution_tiers": ["768P", "2K"],
        "duration_seconds": {"min": 4, "max": 15},
        "supported_ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
        "fps": {
            "value": 24,
            "editable": False,
            "evidence": "官方仅明确记录 H3 768P 再生成输入验收规格为 24 FPS；不扩展声称为全部 2K 输出规格。",
        },
        "source_url": "https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create",
    },
    "minimax_h3_max": {
        "vendor": "MiniMax",
        "display_name": "MiniMax H3 Max",
        "model_id": "MiniMax-H3-Max",
        "resolution_tiers": ["480P", "768P"],
        "duration_seconds": {"min": 5, "max": 15},
        "supported_ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
        "fps": {
            "value": None,
            "editable": False,
            "evidence": "公开创建接口未提供可配置 FPS 参数。",
        },
        "source_url": "https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create",
    },
    "seedance_2_5": {
        "vendor": "Seedance",
        "display_name": "Seedance 2.5",
        "model_id": "dreamina-seedance-2-5-260628",
        "provider_region": "BytePlus international",
        "resolution_tiers": ["480p", "720p", "1080p"],
        "duration_seconds": {"min": 4, "max": 30},
        "supported_ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive"],
        "fps": {
            "value": 24,
            "editable": False,
            "evidence": "BytePlus 官方产品页明确标注输出为 24 FPS。",
        },
        "source_url": "https://docs.byteplus.com/en/docs/ModelArk/2607688",
    },
}

_WIDE_SIGNALS = ("群像", "战场", "追车", "山河", "广场", "宴会", "舞台", "大全景")
_VERTICAL_SIGNALS = ("手机", "直播", "自拍", "近景", "独白", "电梯", "走廊", "聊天")
_FAST_SIGNALS = ("追逐", "打斗", "爆炸", "逃跑", "快切", "反转")


def video_parameter_analysis_deadline_seconds(config_module: Any) -> int:
    """Return a bounded end-to-end deadline for this fast recommendation task."""
    raw = getattr(
        config_module,
        "VIDEO_PARAMETER_ANALYSIS_DEADLINE_SECONDS",
        DEFAULT_ANALYSIS_DEADLINE_SECONDS,
    )
    try:
        return max(15, min(90, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_ANALYSIS_DEADLINE_SECONDS


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _version_key(item: dict[str, Any]) -> tuple[int, int]:
    return int(item.get("version") or 0), int(item.get("id") or 0)


def current_project_scripts(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the same effective script versions shown in the writing workspace."""

    episodes = list(project.get("episodes") or [])
    target_count = int(project.get("target_episode_count") or 0)
    if str(project.get("workflow_mode") or "sequential") == "free":
        scripts_by_id = {
            str(item.get("id")): item
            for item in episodes
            if item.get("stage") == "script"
        }
        selected: dict[int, dict[str, Any]] = {}
        for head in project.get("resource_heads") or []:
            if str(head.get("stage") or "") != "script":
                continue
            number = int(head.get("episode_number") or 0)
            item = scripts_by_id.get(str(head.get("resource_ref") or ""))
            if item is not None and (target_count < 1 or number <= target_count):
                selected[number] = item
        return [selected[number] for number in sorted(selected)]

    outline_batch_id = str(project.get("outline_batch_id") or "")
    beats_by_episode: dict[int, dict[str, Any]] = {}
    for item in episodes:
        if item.get("stage") != "beats" or item.get("status") not in {"locked", "approved"}:
            continue
        refs = item.get("upstream_refs") or {}
        if str(refs.get("outline_batch_id") or "") != outline_batch_id:
            continue
        number = int(item.get("episode_number") or 0)
        if number < 1 or (target_count > 0 and number > target_count):
            continue
        if number not in beats_by_episode or _version_key(item) > _version_key(beats_by_episode[number]):
            beats_by_episode[number] = item

    selected: dict[int, dict[str, Any]] = {}
    for item in episodes:
        if item.get("stage") != "script":
            continue
        number = int(item.get("episode_number") or 0)
        beats = beats_by_episode.get(number)
        if beats is None:
            continue
        refs = item.get("upstream_refs") or {}
        if (
            int(refs.get("beats_id") or 0),
            int(refs.get("beats_version") or 0),
        ) != (int(beats.get("id") or 0), int(beats.get("version") or 0)):
            continue
        if number not in selected or _version_key(item) > _version_key(selected[number]):
            selected[number] = item
    return [selected[number] for number in sorted(selected)]


def build_video_parameter_source(project: dict[str, Any]) -> dict[str, Any]:
    scripts = current_project_scripts(project)
    if not scripts:
        raise ValueError("项目尚无可分析的场次剧本")
    frozen = [
        {
            "id": int(item.get("id") or 0),
            "episode_number": int(item.get("episode_number") or 0),
            "version": int(item.get("version") or 0),
            "status": str(item.get("status") or ""),
            "content": item.get("content") or {},
        }
        for item in scripts
    ]
    fingerprint_payload = {
        "project_id": int(project["id"]),
        "target_platform": str(project.get("target_platform") or ""),
        "target_episode_count": int(project.get("target_episode_count") or 0),
        "scripts": frozen,
    }
    return {
        **fingerprint_payload,
        "project_name": str(project.get("name") or ""),
        "episode_numbers": [item["episode_number"] for item in frozen],
        "complete": len(frozen) >= int(project.get("target_episode_count") or 0) > 0,
        "fingerprint": _canonical_hash(fingerprint_payload),
    }


def _scene_text(scene: dict[str, Any]) -> str:
    header = " ".join(
        str(scene.get(key) or "")
        for key in ("sequence_no", "scene_no", "time_of_day", "interior_exterior", "location")
    )
    characters = "、".join(str(value) for value in scene.get("characters") or [])
    blocks = " ".join(
        " ".join(
            value
            for value in (
                str(block.get("speaker") or "").strip(),
                str(block.get("modifier") or "").strip(),
                str(block.get("text") or "").strip(),
            )
            if value
        )
        for block in scene.get("blocks") or []
    )
    return f"{header} 人物:{characters} {blocks}".strip()


def compact_source_for_prompt(source: dict[str, Any], max_chars: int = 120_000) -> dict[str, Any]:
    scripts = list(source.get("scripts") or [])
    per_episode = max(2_500, max_chars // max(1, len(scripts)))
    compact: list[dict[str, Any]] = []
    for item in scripts:
        content = item.get("content") or {}
        text = "\n".join(_scene_text(scene) for scene in content.get("scenes") or [])
        compact.append(
            {
                "episode_number": item["episode_number"],
                "episode_title": str(content.get("episode_title") or ""),
                "script_excerpt": text[:per_episode],
                "truncated": len(text) > per_episode,
            }
        )
    return {
        "project_name": source.get("project_name"),
        "target_platform": source.get("target_platform"),
        "target_episode_count": source.get("target_episode_count"),
        "episode_numbers": source.get("episode_numbers"),
        "scripts": compact,
    }


def _industry_delivery(ratio: str, platform: str) -> dict[str, Any]:
    pixels = "1080×1920" if ratio == "9:16" else "1920×1080"
    notes = ["行业通行高清母版建议，不代表平台强制上传门槛。"]
    normalized = str(platform or "").strip().lower()
    if normalized in {"dy", "douyin", "抖音"}:
        notes.append("抖音短剧媒资官方推荐 MP4 720P 转码档；该建议不是源母版最低分辨率。")
    elif normalized in {"hg", "hongguo", "红果"}:
        notes.append("红果未发现公开可验证的强制技术规范，本项按移动端短剧行业惯例给出。")
    return {
        "ratio": ratio,
        "orientation": "portrait" if ratio == "9:16" else "landscape",
        "master_resolution": pixels,
        "evidence_level": "official_recommendation" if normalized in {"dy", "douyin", "抖音"} else "industry_convention",
        "notes": notes,
    }


def _rule_ratio(source: dict[str, Any]) -> str:
    text = json.dumps(source.get("scripts") or [], ensure_ascii=False)
    wide = sum(text.count(term) for term in _WIDE_SIGNALS)
    vertical = sum(text.count(term) for term in _VERTICAL_SIGNALS)
    return "16:9" if wide > vertical else "9:16"


def _default_profile(model_key: str, scenario: str, source: dict[str, Any]) -> dict[str, Any]:
    capability = MODEL_CAPABILITIES[model_key]
    platform = str(source.get("target_platform") or "")
    content_ratio = _rule_ratio(source)
    platform_ratio = "9:16" if platform.lower() in {"dy", "douyin", "抖音", "hg", "hongguo", "红果"} else content_ratio
    ratio = platform_ratio if scenario == "platform_fit" else content_ratio
    text = json.dumps(source.get("scripts") or [], ensure_ascii=False)
    fast = any(signal in text for signal in _FAST_SIGNALS)
    duration = 6 if fast else 8
    duration_bounds = capability["duration_seconds"]
    duration = max(duration_bounds["min"], min(duration_bounds["max"], duration))
    resolution = {
        "minimax_h3": "2K",
        "minimax_h3_max": "768P",
        "seedance_2_5": "1080p",
    }[model_key]
    return {
        "scenario": scenario,
        "ratio": ratio,
        "orientation": "portrait" if ratio == "9:16" else "landscape",
        "resolution_tier": resolution,
        "clip_duration_seconds": duration,
        "fps": capability["fps"],
        "delivery": _industry_delivery(ratio, platform),
        "confidence": 0.35,
        "rationale": "模型分析不可用时生成的规则建议，需重新分析后再确认。",
        "evidence": [],
        "special_scene_risks": [],
        "recommendation_basis": "rules_fallback",
    }


def fallback_recommendation(source: dict[str, Any], error: str = "") -> dict[str, Any]:
    return {
        "schema_version": RECOMMENDATION_SCHEMA_VERSION,
        "prompt_version": RECOMMENDATION_PROMPT_VERSION,
        "analysis_mode": "rules_fallback",
        "analysis_error": str(error or ""),
        "source_fingerprint": source["fingerprint"],
        "source_episode_numbers": list(source["episode_numbers"]),
        "source_complete": bool(source["complete"]),
        "capabilities": MODEL_CAPABILITIES,
        "models": {
            key: {
                "capability": capability,
                "scenarios": {
                    scenario: _default_profile(key, scenario, source)
                    for scenario in SCENARIOS
                },
            }
            for key, capability in MODEL_CAPABILITIES.items()
        },
    }


def _clean_text(value: Any, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _normalize_special_risks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in value[:6]:
        if not isinstance(raw, dict):
            continue
        items.append(
            {
                "episode_number": max(0, int(raw.get("episode_number") or 0)),
                "scene": _clean_text(raw.get("scene"), 120),
                "risk": _clean_text(raw.get("risk"), 240),
                "suggestion": _clean_text(raw.get("suggestion"), 240),
            }
        )
    return items


def normalize_recommendation(raw: Any, source: dict[str, Any]) -> dict[str, Any]:
    fallback = fallback_recommendation(source)
    if not isinstance(raw, dict):
        return fallback
    raw_models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
    for model_key, capability in MODEL_CAPABILITIES.items():
        model_raw = raw_models.get(model_key) if isinstance(raw_models.get(model_key), dict) else {}
        raw_scenarios = model_raw.get("scenarios") if isinstance(model_raw.get("scenarios"), dict) else {}
        for scenario in SCENARIOS:
            profile = fallback["models"][model_key]["scenarios"][scenario]
            candidate = raw_scenarios.get(scenario) if isinstance(raw_scenarios.get(scenario), dict) else {}
            ratio = str(candidate.get("ratio") or profile["ratio"])
            if ratio not in {"9:16", "16:9"}:
                ratio = profile["ratio"]
            resolution = str(candidate.get("resolution_tier") or profile["resolution_tier"])
            if resolution not in capability["resolution_tiers"]:
                resolution = profile["resolution_tier"]
            try:
                duration = int(candidate.get("clip_duration_seconds") or profile["clip_duration_seconds"])
            except (TypeError, ValueError):
                duration = int(profile["clip_duration_seconds"])
            duration = max(
                int(capability["duration_seconds"]["min"]),
                min(int(capability["duration_seconds"]["max"]), duration),
            )
            try:
                confidence = float(candidate.get("confidence", 0.6))
            except (TypeError, ValueError):
                confidence = 0.6
            evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), list) else []
            profile.update(
                {
                    "ratio": ratio,
                    "orientation": "portrait" if ratio == "9:16" else "landscape",
                    "resolution_tier": resolution,
                    "clip_duration_seconds": duration,
                    "delivery": _industry_delivery(ratio, str(source.get("target_platform") or "")),
                    "confidence": max(0.0, min(1.0, confidence)),
                    "rationale": _clean_text(candidate.get("rationale"), 800) or profile["rationale"],
                    "evidence": [_clean_text(item, 300) for item in evidence[:6] if _clean_text(item, 300)],
                    "special_scene_risks": _normalize_special_risks(candidate.get("special_scene_risks")),
                    "recommendation_basis": "script_ai_analysis",
                }
            )
    fallback["analysis_mode"] = "ai"
    fallback["analysis_error"] = ""
    return fallback


def recommendation_messages(source: dict[str, Any]) -> list[dict[str, str]]:
    capabilities = {
        key: {
            "model_id": value["model_id"],
            "resolution_tiers": value["resolution_tiers"],
            "duration_seconds": value["duration_seconds"],
            "allowed_recommendation_ratios": ["9:16", "16:9"],
        }
        for key, value in MODEL_CAPABILITIES.items()
    }
    system = """你是短剧视频生成制作参数分析师。只分析提供的场次剧本，不改写剧本。
必须为三个目标型号分别给出 platform_fit（当前平台适配）和 content_optimal（内容最优）方案。
每个方案只能从给定 capability 白名单选择 ratio、resolution_tier 和 clip_duration_seconds。
clip_duration_seconds 是单次生成片段时长，不是单集时长。不要推荐 adaptive。
输出严格 JSON：{"models": {model_key: {"scenarios": {scenario: {"ratio": "9:16|16:9", "resolution_tier": "白名单值", "clip_duration_seconds": 整数, "confidence": 0到1, "rationale": "理由", "evidence": ["剧本证据"], "special_scene_risks": [{"episode_number": 1, "scene": "场景", "risk": "构图风险", "suggestion": "建议"}]}}}}}。
保持简洁：每个方案的 rationale 不超过 100 个汉字，evidence 最多 2 条，special_scene_risks 最多 2 条。
不要添加模型能力之外的参数，不要把平台行业惯例描述成官方强制规则。"""
    user = json.dumps(
        {"capabilities": capabilities, "script_source": compact_source_for_prompt(source)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def analyze_video_parameters(provider: Any, source: dict[str, Any]) -> dict[str, Any]:
    raw = await provider.complete_json(recommendation_messages(source))
    return normalize_recommendation(raw, source)


def attach_recommendation_staleness(
    project: dict[str, Any], record: dict[str, Any] | None
) -> dict[str, Any] | None:
    if record is None:
        return None
    viewed = dict(record)
    try:
        current = build_video_parameter_source(project)
        viewed["stale"] = str(record.get("source_fingerprint") or "") != current["fingerprint"]
        viewed["current_source_fingerprint"] = current["fingerprint"]
        viewed["current_episode_numbers"] = current["episode_numbers"]
        viewed["current_source_complete"] = current["complete"]
    except ValueError:
        viewed["stale"] = True
        viewed["current_source_fingerprint"] = ""
        viewed["current_episode_numbers"] = []
        viewed["current_source_complete"] = False
    return viewed
