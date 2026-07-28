# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/web\navigation_api.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

# Copyright (c) 2025 relakkes@gmail.com

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from identity.dependencies import current_principal
from identity.schema import Principal


class NavigationPresentation(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    icon: str = Field(max_length=80)
    sort_order: int
    is_enabled: bool


def build_navigation_router() -> APIRouter:
    router = APIRouter()

    def admin(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.is_system_admin:
            raise HTTPException(403, "仅系统管理员可以配置导航")
        return principal

    def repository(request: Request):
        value = getattr(request.app.state, "navigation_repository", None)
        if value is None:
            raise HTTPException(503, "PostgreSQL 导航服务未就绪")
        return value

    @router.get("/api/admin/navigation")
    def list_items(request: Request, _: Principal = Depends(admin)):
        return repository(request).list_items()

    @router.patch("/api/admin/navigation/{item_id}")
    def update_item(item_id: str, payload: NavigationPresentation, request: Request, principal: Principal = Depends(admin)):
        repo = repository(request)
        try:
            before = next(item for item in repo.list_items() if item["id"] == item_id)
            updated = repo.update_presentation(item_id, **payload.model_dump())
        except KeyError as exc:
            raise HTTPException(404, "导航项不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        request.app.state.audit_service.record(actor=principal, action="navigation.update", target_type="navigation_item", target_id=item_id, before=before, after=updated, reason=None, result="success")
        return updated

    return router
