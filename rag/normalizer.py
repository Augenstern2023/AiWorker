# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from rag.schema import Artifact, StandardDocument


def normalize(artifact: Artifact, platform: str | None = None) -> list[StandardDocument]:
    platform = platform or artifact.platform
    if artifact.content_type == "json":
        return _normalize_json_artifact(artifact, platform)
    return [_document_from_text(artifact, _title_from_markdown(artifact.text) or artifact.source_type)]


def _normalize_json_artifact(artifact: Artifact, platform: str) -> list[StandardDocument]:
    payload = artifact.data
    if isinstance(payload, dict) and isinstance(payload.get("analysis"), dict):
        return [_analysis_document(artifact, payload["analysis"])]
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return _rank_documents(artifact, payload["records"], platform)
    if isinstance(payload, list):
        return _rank_documents(artifact, payload, platform)
    return [_document_from_text(artifact, _title_from_payload(payload), _compact_json(payload))]


def _rank_documents(artifact: Artifact, records: list[Any], platform: str) -> list[StandardDocument]:
    documents: list[StandardDocument] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        normalized = _normalize_rank_record(record, platform)
        title = str(normalized.get("title") or f"record-{index + 1}").strip()
        text = _rank_text(normalized, record)
        metadata = {
            **artifact.metadata,
            "record_index": index,
            "record_id": normalized.get("record_id", ""),
            "title": title,
            "rank": normalized.get("rank"),
            "category": normalized.get("category", ""),
            "tab": normalized.get("tab", ""),
        }
        documents.append(
            StandardDocument(
                document_id=_document_id(artifact, title, index),
                job_id=artifact.job_id,
                platform=artifact.platform,
                source_path=artifact.path,
                source_type=artifact.source_type,
                title=title,
                text=text,
                metadata={key: value for key, value in metadata.items() if value not in (None, "")},
            )
        )
    return documents


def _normalize_rank_record(record: dict[str, Any], platform: str) -> dict[str, Any]:
    if platform == "hg":
        rank = record.get("rank") if isinstance(record.get("rank"), dict) else record
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        return {
            "record_id": _first(rank, "book_id", "id") or _first(meta, "book_id"),
            "title": _first(rank, "play_name", "title", "name") or _first(meta, "title", "play_name"),
            "rank": _to_int(_first(rank, "rowNum", "rank")),
            "category": _first(meta, "category") or _play_type_label(_first(rank, "play_type")),
            "description": _first(meta, "description", "desc") or _first(rank, "desc", "description"),
            "episode_count": _first(meta, "episode_count") or _first(rank, "episode_count"),
            "like_count": _first(rank, "like_count"),
            "favorite_count": _first(rank, "favorite_count"),
            "comment_count": _first(rank, "comment_count"),
            "library_source": _first(rank, "library_source"),
        }
    return {
        "record_id": _first(record, "series_id", "item_id", "aweme_id", "id"),
        "title": _first(record, "title", "name"),
        "rank": _to_int(_first(record, "rank")),
        "category": _first(record, "rank_category", "category"),
        "tab": _first(record, "tab"),
        "description": _first(record, "description", "desc"),
        "heat_text": _first(record, "heat_text", "heat"),
        "like_count": _first(record, "like_count"),
        "favorite_count": _first(record, "favorite_count"),
        "comment_count": _first(record, "comment_count"),
        "comments": record.get("comments") if isinstance(record.get("comments"), list) else [],
    }


def _rank_text(normalized: dict[str, Any], raw: dict[str, Any]) -> str:
    lines = [
        f"剧名：{normalized.get('title', '')}",
        f"排名：{normalized.get('rank', '')}",
        f"类型：{normalized.get('category', '')}",
        f"榜单页签：{normalized.get('tab', '')}",
        f"简介：{normalized.get('description', '')}",
        f"热度：{normalized.get('heat_text', '')}",
        f"点赞：{normalized.get('like_count', '')}",
        f"收藏：{normalized.get('favorite_count', '')}",
        f"评论数：{normalized.get('comment_count', '')}",
        f"集数：{normalized.get('episode_count', '')}",
        f"来源：{normalized.get('library_source', '')}",
    ]
    comments = normalized.get("comments") or []
    if comments:
        lines.append("评论：")
        lines.extend(_comment_text(item) for item in comments[:20])
    details = raw.get("details") if isinstance(raw.get("details"), list) else []
    if details:
        lines.append("剧集明细：")
        lines.extend(_compact_json(item) for item in details[:10])
    return "\n".join(line for line in lines if str(line).strip() and not str(line).endswith("："))


def _analysis_document(artifact: Artifact, analysis: dict[str, Any]) -> StandardDocument:
    title = str(analysis.get("title") or analysis.get("book_id") or artifact.source_type).strip()
    sections = [
        f"标题：{title}",
        f"类型：{analysis.get('category', '')}",
        f"摘要：{analysis.get('summary', '')}",
        f"标签：{'、'.join(_as_list(analysis.get('plot_tags')))}",
        f"钩子：{'；'.join(_as_list(analysis.get('hook_points')))}",
        f"卖点：{'；'.join(_as_list(analysis.get('selling_points')))}",
        f"风险：{'；'.join(_as_list(analysis.get('risk_flags')))}",
        f"转写：{analysis.get('transcript') or analysis.get('transcript_seed') or ''}",
        f"LLM分析：{_compact_json(analysis.get('llm_analysis'))}",
    ]
    return StandardDocument(
        document_id=_document_id(artifact, title, 0),
        job_id=artifact.job_id,
        platform=artifact.platform,
        source_path=artifact.path,
        source_type=artifact.source_type,
        title=title,
        text="\n".join(line for line in sections if not line.endswith("：")),
        metadata={
            **artifact.metadata,
            "record_id": str(analysis.get("book_id") or analysis.get("series_id") or ""),
            "title": title,
            "category": str(analysis.get("category") or ""),
        },
    )


def _document_from_text(artifact: Artifact, title: str, text: str | None = None) -> StandardDocument:
    text = artifact.text if text is None else text
    return StandardDocument(
        document_id=_document_id(artifact, title, 0),
        job_id=artifact.job_id,
        platform=artifact.platform,
        source_path=artifact.path,
        source_type=artifact.source_type,
        title=title,
        text=text,
        metadata={**artifact.metadata, "title": title},
    )


def _title_from_markdown(text: str) -> str:
    match = re.search(r"^\s*#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _title_from_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(_first(payload, "title", "name", "job_id") or "json-artifact")
    return "json-artifact"


def _document_id(artifact: Artifact, title: str, index: int) -> str:
    value = f"{artifact.job_id}:{artifact.path}:{title}:{index}"
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return ""


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _play_type_label(value: Any) -> str:
    return {"0": "短剧", "1": "漫剧", "2": "AI漫剧"}.get(str(value), "")


def _comment_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(_first(item, "content", "text", "comment", "评论") or _compact_json(item))
    return str(item)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

