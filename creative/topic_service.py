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


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class TopicService:
    def __init__(self, root: str) -> None:
        self.rank_service = TodayRankService(root=root)

    def generate(self, platform: str, date: str = "", limit: int = 10) -> list[dict[str, Any]]:
        response = self.rank_service.get_today_rank(platform=platform, date=date, limit=50)
        snapshot = response.get("selected_snapshot") or {}
        entries = response.get("current_entries") or []
        topics = []
        for entry in entries:
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            rank = _number(entry.get("rank"))
            rank_score = max(0.0, 100.0 - (rank - 1) * 5) if rank else 50.0
            rank_delta = _number(entry.get("rank_delta"))
            heat_delta = _number(entry.get("heat_delta"))
            growth = min(100.0, max(0.0, 50.0 + (rank_delta or 0) * 15 + (20 if (heat_delta or 0) > 0 else 0))) if response.get("previous_snapshot") else None
            details = {"heat": rank_score, "growth": growth, "freshness": 50.0, "platform_fit": 70.0}
            available = {key: value for key, value in details.items() if value is not None}
            weights = {"heat": 25, "growth": 20, "freshness": 10, "platform_fit": 5}
            total_weight = sum(weights[key] for key in available)
            score = sum(available[key] * weights[key] for key in available) / total_weight
            confidence = "high" if growth is not None else "medium"
            source_path = str(snapshot.get("source_path") or "")
            captured_at = str(response.get("date") or "")
            evidence = [{"source_type": "snapshot", "platform": platform, "job_id": str(snapshot.get("job_id") or ""), "source_path": source_path, "captured_at": captured_at, "field": "rank", "excerpt": f"排名 {entry.get('rank') or '-'}"}]
            subject = str(entry.get("subject") or "").strip() or "待补充"
            rank_type = str(entry.get("rank_list") or entry.get("tab") or entry.get("group") or "").strip()
            description = str(entry.get("description") or "").strip()
            topics.append({"platform": platform, "job_id": str(snapshot.get("job_id") or ""), "source_path": source_path, "captured_at": captured_at, "title": title, "description": description, "category": subject, "rank_type": rank_type, "score": round(score, 1), "confidence": confidence, "score_details": {**details, "available_weight": total_weight}, "recommendation": f"当前排名 {entry.get('rank') or '-'}" + (f"，排名上升 {int(rank_delta)} 位" if (rank_delta or 0) > 0 else "，建议结合拆解卡人工复核"), "evidence": evidence})
        return sorted(topics, key=lambda item: item["score"], reverse=True)[: max(1, min(limit, 50))]
