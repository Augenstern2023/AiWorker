# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Any

from analysis.today_rank_service import TodayRankService


RANK_SOURCE_OPTIONS = {
    "dy": ("榜单", "热榜"),
    "hg": ("全部", "真人剧", "漫剧", "AI剧"),
}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class TopicService:
    def __init__(self, root: str) -> None:
        self.rank_service = TodayRankService(root=root)

    def generate(
        self,
        platform: str,
        date: str = "",
        limit: int = 10,
        rank_source: str = "",
    ) -> list[dict[str, Any]]:
        selected_source = self.normalize_rank_source(platform, rank_source)
        response = self.rank_service.get_today_rank(platform=platform, date=date, limit=50)
        candidates = self._rank_candidates(response, platform)
        topics = []
        for candidate in candidates:
            if selected_source not in candidate["sections"]:
                continue
            topic = self.rank_entry_to_topic(candidate["entry"], response)
            topic["rank_type"] = candidate["sources"][0]
            topic["rank_source"] = "|".join(candidate["sources"])
            topics.append(topic)
        return sorted(topics, key=lambda item: item["score"], reverse=True)[: max(1, min(limit, 50))]

    @staticmethod
    def normalize_rank_source(platform: str, rank_source: str = "") -> str:
        options = RANK_SOURCE_OPTIONS.get(str(platform or "").strip().lower())
        if options is None:
            raise ValueError("platform must be dy or hg")
        source = str(rank_source or "").strip() or options[0]
        if source not in options:
            raise ValueError(f"invalid rank_source for {platform}")
        return source

    def _rank_candidates(
        self,
        response: dict[str, Any],
        platform: str,
    ) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        sections = response.get("sections") or []
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_name = str(section.get("key") or section.get("label") or "").strip()
            for ranking in section.get("rankings") or []:
                if not isinstance(ranking, dict):
                    continue
                source = self._ranking_source(platform, section_name, ranking)
                for entry in ranking.get("current_entries") or []:
                    self._merge_rank_candidate(candidates, entry, section_name, source)

        if not candidates:
            default_source = self.normalize_rank_source(platform)
            for entry in response.get("current_entries") or []:
                source = self._entry_rank_source(platform, entry) or default_source
                self._merge_rank_candidate(candidates, entry, default_source, source)
        return list(candidates.values())

    @staticmethod
    def _ranking_source(
        platform: str,
        section_name: str,
        ranking: dict[str, Any],
    ) -> str:
        label = str(ranking.get("label") or ranking.get("key") or section_name).strip()
        if platform == "dy":
            return section_name or label
        if label.startswith(section_name):
            return label
        return f"{section_name}{label}"

    @staticmethod
    def _entry_rank_source(platform: str, entry: dict[str, Any]) -> str:
        if platform == "dy":
            return str(entry.get("tab") or entry.get("rank_list") or "").strip()
        return str(entry.get("rank_list") or entry.get("group") or "").strip()

    @staticmethod
    def _merge_rank_candidate(
        candidates: dict[str, dict[str, Any]],
        entry: Any,
        section_name: str,
        source: str,
    ) -> None:
        if not isinstance(entry, dict):
            return
        title = str(entry.get("title") or "").strip()
        key = "".join(title.split()).casefold()
        if not key:
            return
        candidate = candidates.get(key)
        if candidate is None:
            candidates[key] = {
                "entry": dict(entry),
                "sections": [section_name],
                "sources": [source],
            }
            return
        if section_name and section_name not in candidate["sections"]:
            candidate["sections"].append(section_name)
        if source and source not in candidate["sources"]:
            candidate["sources"].append(source)
        current_rank = int(candidate["entry"].get("rank") or 999999)
        incoming_rank = int(entry.get("rank") or 999999)
        if incoming_rank < current_rank:
            candidate["entry"] = dict(entry)

    def find_rank_entry(self, platform: str, date: str, record_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        response = self.rank_service.get_today_rank(platform=platform, date=date, limit=50)
        if not response.get("selected_snapshot"):
            raise ValueError("榜单快照不存在或已过期")
        for entry in self._rank_entries(response):
            if str(entry.get("record_id") or entry.get("id") or "") == record_id:
                return entry, response
        raise ValueError("榜单剧目不存在或已过期")

    def rank_entry_to_topic(self, entry: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        snapshot = response.get("selected_snapshot") or {}
        platform = str(entry.get("platform") or snapshot.get("platform") or "")
        title = str(entry.get("title") or "").strip()
        if not title:
            raise ValueError("榜单剧目缺少标题")
        rank = _number(entry.get("rank"))
        rank_score = max(0.0, 100.0 - (rank - 1) * 5) if rank else 50.0
        rank_delta = _number(entry.get("rank_delta"))
        heat_delta = _number(entry.get("heat_delta"))
        growth = (
            min(100.0, max(0.0, 50.0 + (rank_delta or 0) * 15 + (20 if (heat_delta or 0) > 0 else 0)))
            if response.get("previous_snapshot")
            else None
        )
        details = {"heat": rank_score, "growth": growth, "freshness": 50.0, "platform_fit": 70.0}
        available = {key: value for key, value in details.items() if value is not None}
        weights = {"heat": 25, "growth": 20, "freshness": 10, "platform_fit": 5}
        total_weight = sum(weights[key] for key in available)
        score = sum(available[key] * weights[key] for key in available) / total_weight
        captured_at = str(entry.get("captured_at") or response.get("date") or "")
        job_id = str(entry.get("job_id") or snapshot.get("job_id") or "")
        source_path = str(entry.get("source_path") or snapshot.get("source_path") or "")
        evidence = [{
            "source_type": "snapshot", "platform": platform, "job_id": job_id,
            "source_path": source_path, "captured_at": captured_at,
            "field": "rank", "excerpt": f"排名 {entry.get('rank') or '-'}",
        }]
        subject = str(entry.get("subject") or "").strip() or "待补充"
        rank_type = str(entry.get("rank_list") or entry.get("tab") or entry.get("group") or "").strip()
        return {
            "platform": platform, "job_id": job_id, "source_path": source_path,
            "captured_at": captured_at, "title": title,
            "description": str(entry.get("description") or "").strip(),
            "category": subject, "rank_type": rank_type,
            "rank_source": self._entry_rank_source(platform, entry) or rank_type,
            "score": round(score, 1),
            "confidence": "high" if growth is not None else "medium",
            "score_details": {**details, "available_weight": total_weight},
            "recommendation": f"当前排名 {entry.get('rank') or '-'}" + (
                f"，排名上升 {int(rank_delta)} 位" if (rank_delta or 0) > 0 else "，建议结合拆解卡人工复核"
            ),
            "evidence": evidence,
        }

    @staticmethod
    def _rank_entries(response: dict[str, Any]) -> list[dict[str, Any]]:
        fields = ("current_entries", "new_entries", "dropped_entries", "rank_up", "rank_down")
        entries: list[dict[str, Any]] = []
        for field in fields:
            entries.extend(item for item in response.get(field, []) if isinstance(item, dict))
        for section in response.get("sections", []):
            for ranking in section.get("rankings", []) if isinstance(section, dict) else []:
                for field in fields:
                    entries.extend(item for item in ranking.get(field, []) if isinstance(item, dict))
        return entries
