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

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from analysis.drama_repository import DramaRepository
from analysis.metrics import MetricsService
from analysis.today_rank_service import TodayRankService
from analysis.weekly_report_service import WeeklyReportService
from identity.dependencies import require_permission
from identity.schema import Principal


class AnalyticsSyncRequest(BaseModel):
    job_id: str
    platform: str
    rebuild: bool = False


def build_analytics_router(
    root: str | Path = ".",
    runtime_dir: str | Path = ".runtime",
    rank_service: TodayRankService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/analytics", tags=["analytics"])
    services: dict[str, Any] = {}

    def get_services() -> tuple[DramaRepository, MetricsService]:
        if not services:
            repository = DramaRepository(db_path=Path(runtime_dir) / "rag" / "analytics.db", root=root)
            services["repository"] = repository
            services["metrics"] = MetricsService(repository)
        return services["repository"], services["metrics"]

    def get_weekly_reports() -> WeeklyReportService:
        if "weekly_reports" not in services:
            central_rank_service = rank_service or TodayRankService.from_environment(
                root=root
            )
            services["weekly_reports"] = WeeklyReportService(
                root=root,
                runtime_dir=runtime_dir,
                rank_service=central_rank_service,
            )
        return services["weekly_reports"]

    @router.post("/sync")
    async def sync(
        payload: AnalyticsSyncRequest,
        _: Principal = Depends(require_permission("*")),
    ):
        if payload.platform not in {"dy", "hg"}:
            raise HTTPException(status_code=400, detail="platform must be dy or hg")
        try:
            repository, _ = get_services()
            return repository.sync_job(payload.job_id, payload.platform, rebuild=payload.rebuild)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/overview")
    async def overview(
        platform: str = "",
        start: str = "",
        end: str = "",
        _: Principal = Depends(require_permission("analytics.read")),
    ):
        _, metrics = get_services()
        return metrics.overview(platform=platform, start=start, end=end)

    @router.get("/dramas")
    async def dramas(
        platform: str = "",
        category: str = "",
        sort: str = "heat",
        limit: int = Query(50, ge=1, le=500),
        date: str = "",
        _: Principal = Depends(require_permission("analytics.read")),
    ):
        _, metrics = get_services()
        return {"items": metrics.dramas(platform=platform, category=category, sort=sort, limit=limit, date=date)}

    @router.get("/tags")
    async def tags(
        platform: str = "",
        time_window: str = "",
        _: Principal = Depends(require_permission("analytics.read")),
    ):
        _, metrics = get_services()
        return metrics.tags(platform=platform, time_window=time_window)

    @router.get("/trends")
    async def trends(
        platform: str = "",
        metric: str = "heat",
        interval: str = "day",
        _: Principal = Depends(require_permission("analytics.read")),
    ):
        _, metrics = get_services()
        return metrics.trends(platform=platform, metric=metric, interval=interval)

    @router.get("/assets")
    async def assets(
        _: Principal = Depends(require_permission("analytics.read")),
    ):
        _, metrics = get_services()
        return metrics.assets()

    @router.get("/sentiment")
    async def sentiment(
        platform: str = "",
        _: Principal = Depends(require_permission("analytics.read")),
    ):
        _, metrics = get_services()
        return {"items": metrics.sentiment(platform=platform)}

    @router.get("/weekly-reports")
    async def weekly_reports(
        platform: str = "",
        _: Principal = Depends(require_permission("analytics.read")),
    ):
        if platform and platform not in {"dy", "hg"}:
            raise HTTPException(status_code=400, detail="platform must be dy or hg")
        items = get_weekly_reports().list_reports(platform=platform)
        return {"items": items}

    @router.get("/weekly-reports/{report_id}")
    async def weekly_report_detail(
        report_id: str,
        _: Principal = Depends(require_permission("analytics.read")),
    ):
        report = get_weekly_reports().get_report(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="weekly report not found")
        return report

    @router.get("/weekly-reports/{report_id}/export")
    async def weekly_report_export(
        report_id: str,
        format: str = "markdown",
        _: Principal = Depends(require_permission("analytics.read")),
    ):
        if format not in {"markdown", "excel"}:
            raise HTTPException(status_code=400, detail="format must be markdown or excel")
        report = get_weekly_reports().repository.get(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="weekly report not found")
        path = Path(str(report["markdown_path"] if format == "markdown" else report["excel_path"])).resolve()
        if not path.is_file():
            raise HTTPException(status_code=404, detail="weekly report export not found")
        media_type = "text/markdown; charset=utf-8" if format == "markdown" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return FileResponse(path, media_type=media_type, filename=path.name)

    return router


def register_analytics_routes(app: FastAPI, root: str | Path = ".", runtime_dir: str | Path = ".runtime") -> None:
    app.include_router(build_analytics_router(root=root, runtime_dir=runtime_dir))
