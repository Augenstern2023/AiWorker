# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any

from creative.hit_breakdown import breakdown_field_guide
from creative.imitation_service import complete_with_one_repair
from creative.prompt_templates import BREAKDOWN_PROMPT_TEMPLATE_VERSION, breakdown_messages


_ARRAY_FIELDS = {
    "hook_points",
    "selling_points",
    "emotional_curve",
    "reversal_nodes",
    "imitable_elements",
    "risk_notes",
}
_TEXT_FIELDS = {item["key"] for item in breakdown_field_guide()} - _ARRAY_FIELDS


def _path_key(value: Any) -> str:
    return os.path.normcase(os.path.normpath(str(value or "")))


def _excerpt_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _evidence_text(value: Any) -> str:
    return re.sub(
        r"[^0-9a-z\u4e00-\u9fff]",
        "",
        str(value or "").casefold(),
    )


def _sentence_candidates(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    sentences = [
        match.group(0).strip()
        for match in re.finditer(r"[^。！？!?；;\r\n]+[。！？!?；;]?", text)
        if match.group(0).strip()
    ]
    candidates: list[str] = []
    for start in range(len(sentences)):
        for size in range(1, min(3, len(sentences) - start) + 1):
            candidates.append("".join(sentences[start:start + size]))
    return candidates or [text]


def _bigram_coverage(query: str, candidate: str) -> float:
    if len(query) < 2:
        return float(query == candidate)
    query_grams = {query[index:index + 2] for index in range(len(query) - 1)}
    candidate_grams = {
        candidate[index:index + 2] for index in range(len(candidate) - 1)
    }
    return len(query_grams & candidate_grams) / max(1, len(query_grams))


def _matching_transcript_sentence(excerpt: str, transcript: Any) -> str | None:
    query = _evidence_text(excerpt)
    if len(query) < 4:
        return None
    best: tuple[float, float, int, str] | None = None
    for candidate in _sentence_candidates(transcript):
        candidate_key = _evidence_text(candidate)
        if not candidate_key:
            continue
        matcher = SequenceMatcher(None, query, candidate_key, autojunk=False)
        ratio = matcher.ratio()
        coverage = _bigram_coverage(query, candidate_key)
        longest_match = max(block.size for block in matcher.get_matching_blocks())
        score = (max(ratio, coverage), ratio, longest_match, candidate)
        if best is None or score[:3] > best[:3]:
            best = score
    if best is None:
        return None
    similarity, ratio, longest_match, sentence = best
    required_match = 3 if len(query) < 8 else max(4, min(8, len(query) // 3))
    if longest_match < required_match:
        return None
    if ratio < 0.48 and similarity < 0.45:
        return None
    return sentence


def _mark_as_transcript_inference(value: Any) -> str:
    text = str(value or "").strip()
    if any(marker in text for marker in ("推断", "待验证", "未知", "证据不足")):
        return text
    return f"{text}（基于现有转写推断）"


def _episode_sources(bundle: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    sources: dict[tuple[int, str], dict[str, Any]] = {}
    for episode in bundle.get("episodes") or []:
        number = int(episode.get("episode_number") or 0)
        for key in ("script_path", "transcript_path"):
            path = str(episode.get(key) or "").strip()
            if path:
                sources[(number, _path_key(path))] = episode
    return sources


def _validate_content(content: Any, expected_title: str) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise ValueError("content 必须是对象")
    missing = (_TEXT_FIELDS | _ARRAY_FIELDS) - set(content)
    if missing:
        raise ValueError(f"content 缺少字段：{sorted(missing)}")
    if str(content.get("title") or "").strip() != expected_title.strip():
        raise ValueError("content.title 必须与来源剧名一致")
    normalized = dict(content)
    for field in _TEXT_FIELDS:
        if not isinstance(content.get(field), str):
            raise ValueError(f"{field} 必须是字符串")
        normalized[field] = content[field].strip()
    for field in _ARRAY_FIELDS:
        value = content.get(field)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field} 必须是字符串数组")
        normalized[field] = [item.strip() for item in value if item.strip()]
    comment = normalized["comment_resonance"]
    if "未提供评论" not in comment and "未验证评论" not in comment:
        raise ValueError("未提供评论正文时，comment_resonance 必须明确标记证据缺失")
    return normalized


def _validate_sources(
    value: Any,
    content: dict[str, Any],
    source_index: dict[tuple[int, str], dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    if not isinstance(value, dict):
        raise ValueError("field_sources 必须是对象")
    normalized: dict[str, list[dict[str, Any]]] = {}
    evidence_gaps: list[str] = []
    for field in _TEXT_FIELDS | _ARRAY_FIELDS:
        items = value.get(field)
        if field == "title":
            normalized[field] = []
            continue
        if field == "comment_resonance" and (
            "未提供评论" in content[field] or "未验证评论" in content[field]
        ):
            normalized[field] = []
            continue
        if field == "risk_notes" and isinstance(items, list) and not items:
            normalized[field] = []
            continue
        if field == "target_audience" and (
            not isinstance(items, list) or not items
        ):
            content[field] = _mark_as_transcript_inference(content[field])
            normalized[field] = []
            continue
        if not isinstance(items, list) or (content.get(field) and not items):
            raise ValueError(f"field_sources.{field} 必须提供证据数组")
        normalized[field] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"field_sources.{field} 的证据必须是对象")
            try:
                episode_number = int(item.get("episode_number"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"field_sources.{field} 的 episode_number 无效") from exc
            source_path = str(item.get("source_path") or "").strip()
            excerpt = str(item.get("excerpt") or "").strip()
            episode = source_index.get((episode_number, _path_key(source_path)))
            if episode is None:
                raise ValueError(f"field_sources.{field} 引用了输入范围外的集数或路径")
            if not excerpt or _excerpt_key(excerpt) not in _excerpt_key(episode.get("text")):
                matched_excerpt = _matching_transcript_sentence(
                    excerpt,
                    episode.get("text"),
                )
                if matched_excerpt is None:
                    evidence_gaps.append(
                        f"{field} 的第 {episode_number} 集证据无法与转写原文可靠匹配，"
                        "已移除，需人工核验。"
                    )
                    continue
                excerpt = matched_excerpt
            normalized[field].append({
                "episode_number": episode_number,
                "source_path": source_path,
                "excerpt": excerpt,
            })
    return normalized, evidence_gaps


def _validate_breakdown_payload(
    payload: dict[str, Any],
    expected_title: str,
    source_index: dict[tuple[int, str], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("LLM 响应必须是对象")
    content = _validate_content(payload.get("content"), expected_title)
    sources, evidence_gaps = _validate_sources(
        payload.get("field_sources"),
        content,
        source_index,
    )
    risks = payload.get("risks")
    if not isinstance(risks, list) or any(not isinstance(item, str) for item in risks):
        raise ValueError("risks 必须是字符串数组")
    normalized_risks = [item.strip() for item in risks if item.strip()]
    for gap in evidence_gaps:
        if gap not in content["risk_notes"]:
            content["risk_notes"].append(gap)
        if gap not in normalized_risks:
            normalized_risks.append(gap)
    return content, sources, normalized_risks


async def generate_breakdown(
    provider: Any,
    topic: dict[str, Any],
    bundle: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str], dict[str, Any]]:
    episodes = list(bundle.get("episodes") or [])
    if not episodes:
        raise ValueError("请先下载剧目脚本再拆解")
    title = str(topic.get("title") or bundle.get("title") or "").strip()
    source_index = _episode_sources(bundle)
    payload = {
        "topic": {
            key: topic.get(key)
            for key in ("title", "description", "category", "recommendation", "platform")
        },
        "transcripts": [{
            "episode_number": int(episode.get("episode_number") or 0),
            "source_path": str(episode.get("script_path") or episode.get("transcript_path") or ""),
            "transcript_source": str(episode.get("transcript_source") or ""),
            "text": str(episode.get("text") or ""),
        } for episode in episodes],
        "field_guide": breakdown_field_guide(),
        "comments_available": False,
    }
    result, meta = await complete_with_one_repair(
        provider,
        breakdown_messages(payload),
        lambda response: _validate_breakdown_payload(response, title, source_index),
    )
    content, sources, risks = result
    metadata_episode = min(episodes, key=lambda item: int(item.get("episode_number") or 0))
    sources["title"] = [{
        "episode_number": int(metadata_episode.get("episode_number") or 1),
        "source_path": str(
            metadata_episode.get("metadata_path")
            or metadata_episode.get("script_path")
            or metadata_episode.get("transcript_path")
            or ""
        ),
        "excerpt": title,
    }]
    return content, sources, risks, {
        **meta,
        "prompt_version": BREAKDOWN_PROMPT_TEMPLATE_VERSION,
        "coverage": sorted(int(episode.get("episode_number") or 0) for episode in episodes),
        "transcript_count": len(episodes),
    }
