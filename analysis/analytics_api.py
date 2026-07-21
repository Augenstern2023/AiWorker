# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel

from analysis.drama_repository import DramaRepository
from analysis.metrics import MetricsService


class AnalyticsSyncRequest(BaseModel):
    job_id: str
    platform: str
    rebuild: bool = False


class AnalyticsSyncAllRequest(BaseModel):
    platforms: list[str] = ["dy", "hg"]


def build_analytics_router(root: str | Path = ".", runtime_dir: str | Path = ".runtime") -> APIRouter:
    router = APIRouter(prefix="/api/analytics", tags=["analytics"])
    services: dict[str, Any] = {}

    def get_services() -> tuple[DramaRepository, MetricsService]:
        if not services:
            repository = DramaRepository(db_path=Path(runtime_dir) / "rag" / "analytics.db", root=root)
            services["repository"] = repository
            services["metrics"] = MetricsService(repository)
        return services["repository"], services["metrics"]

    @router.post("/sync")
    async def sync(payload: AnalyticsSyncRequest):
        if payload.platform not in {"dy", "hg"}:
            raise HTTPException(status_code=400, detail="platform must be dy or hg")
        try:
            repository, _ = get_services()
            return repository.sync_job(payload.job_id, payload.platform, rebuild=payload.rebuild)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/sync-all")
    async def sync_all(payload: AnalyticsSyncAllRequest):
        try:
            repository, _ = get_services()
            return repository.sync_all_jobs(payload.platforms)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/overview")
    async def overview(platform: str = "", start: str = "", end: str = ""):
        _, metrics = get_services()
        return metrics.overview(platform=platform, start=start, end=end)

    @router.get("/dramas")
    async def dramas(
        platform: str = "",
        category: str = "",
        sort: str = "heat",
        limit: int = Query(50, ge=1, le=500),
        date: str = "",
    ):
        _, metrics = get_services()
        return {"items": metrics.dramas(platform=platform, category=category, sort=sort, limit=limit, date=date)}

    @router.get("/tags")
    async def tags(platform: str = "", time_window: str = ""):
        _, metrics = get_services()
        return metrics.tags(platform=platform, time_window=time_window)

    @router.get("/trends")
    async def trends(platform: str = "", metric: str = "heat", interval: str = "day"):
        _, metrics = get_services()
        return metrics.trends(platform=platform, metric=metric, interval=interval)

    @router.get("/assets")
    async def assets():
        _, metrics = get_services()
        return metrics.assets()

    @router.get("/sentiment")
    async def sentiment(platform: str = ""):
        _, metrics = get_services()
        return {"items": metrics.sentiment(platform=platform)}

    return router


def register_analytics_routes(app: FastAPI, root: str | Path = ".", runtime_dir: str | Path = ".runtime") -> None:
    app.include_router(build_analytics_router(root=root, runtime_dir=runtime_dir))
