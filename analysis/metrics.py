# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from analysis.drama_repository import DramaRepository


class MetricsService:
    def __init__(self, repository: DramaRepository):
        self.repository = repository

    def overview(self, platform: str = "", start: str = "", end: str = "") -> dict[str, object]:
        return self.repository.overview(platform=platform, start=start, end=end)

    def dramas(
        self,
        platform: str = "",
        category: str = "",
        sort: str = "heat",
        limit: int = 50,
        date: str = "",
    ) -> list[dict[str, object]]:
        return self.repository.list_dramas(platform=platform, category=category, sort=sort, limit=limit, date=date)

    def tags(self, platform: str = "", time_window: str = "") -> dict[str, list[dict[str, object]]]:
        return {
            "categories": self.repository.tag_distribution(platform=platform, tag_type="category"),
            "plot_tags": self.repository.tag_distribution(platform=platform, tag_type="plot_tag"),
            "selling_points": self.repository.tag_distribution(platform=platform, tag_type="selling_point"),
            "hooks": self.repository.tag_distribution(platform=platform, tag_type="hook"),
            "time_window": time_window,
        }

    def trends(self, platform: str = "", metric: str = "heat", interval: str = "day") -> dict[str, object]:
        metric_map = {
            "heat": "heat",
            "likes": "like_count",
            "like_count": "like_count",
            "favorites": "favorite_count",
            "favorite_count": "favorite_count",
            "comments": "comment_count",
            "comment_count": "comment_count",
        }
        selected = metric_map.get(metric, "heat")
        points = self.repository.trends(platform=platform, metric=selected)
        if not points and selected == "heat":
            for fallback in ("like_count", "favorite_count", "comment_count"):
                points = self.repository.trends(platform=platform, metric=fallback)
                if points:
                    selected = fallback
                    break
        return {
            "metric": selected,
            "interval": interval,
            "points": points,
        }

    def assets(self) -> dict[str, object]:
        return self.repository.assets()

    def sentiment(self, platform: str = "") -> list[dict[str, object]]:
        return self.repository.sentiment_distribution(platform=platform)
