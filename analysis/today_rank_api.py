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

from fastapi import APIRouter, FastAPI, HTTPException, Query

from analysis.rank_models import RankDatabaseUnavailable
from analysis.today_rank_service import TodayRankService


def build_today_rank_router(
    root: str | Path = ".",
    service: TodayRankService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/rank", tags=["rank"])
    rank_service = service or TodayRankService.from_environment(root=root)

    @router.get("/today")
    async def today_rank(
        platform: str = Query(..., description="dy or hg"),
        date: str = "",
        limit: int = Query(10, ge=1, le=50),
        refresh: bool = False,
    ):
        try:
            return rank_service.get_today_rank(
                platform=platform,
                date=date,
                limit=limit,
                force_refresh=refresh,
            )
        except RankDatabaseUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="榜单数据库暂不可用",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router


def register_today_rank_routes(app: FastAPI, root: str | Path = ".") -> None:
    app.include_router(build_today_rank_router(root=root))
