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
from typing import Literal

from pydantic import BaseModel, Field

from identity.dependencies import current_principal
from identity.schema import Principal


class NavigationPresentation(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    icon: str = Field(max_length=80)
    sort_order: int
    is_enabled: bool


class NavigationAccountOverride(BaseModel):
    user_id: str = Field(min_length=1)
    access_mode: Literal["inherit", "allow", "deny"]


class NavigationAccountsUpdate(BaseModel):
    overrides: list[NavigationAccountOverride] = Field(default_factory=list)


def build_navigation_router() -> APIRouter:
    router = APIRouter()

    def admin(principal: Principal = Depends(current_principal)) -> Principal:
        if "system_admin" not in principal.role_codes:
            raise HTTPException(403, "仅系统管理员可以配置导航")
        return principal

    def repository(request: Request):
        value = getattr(request.app.state, "navigation_repository", None)
        if value is None:
            raise HTTPException(503, "PostgreSQL 导航服务未就绪")
        return value

    @router.get("/api/admin/navigation")
    def list_items(request: Request, _: Principal = Depends(admin)):
        repo = repository(request)
        items = repo.list_items()
        access_reader = getattr(repo, "list_page_access_user_ids", None)
        counts: dict[str, int] = {}
        for item in items:
            route = item.get("route")
            if route not in counts:
                try:
                    counts[route] = len(access_reader(route)) if access_reader else 0
                except ValueError:
                    counts[route] = 0
            item["authorized_user_count"] = counts[route]
        return items

    def navigation_item(repo, item_id: str, *, connection=None):
        getter = getattr(repo, "get_item", None)
        item = getter(item_id, connection=connection) if getter else next(
            (item for item in repo.list_items() if item["id"] == item_id), None
        )
        if item is None:
            raise HTTPException(404, "导航项不存在")
        return item

    @router.get("/api/admin/navigation/{item_id}/accounts")
    def list_accounts(
        item_id: str,
        request: Request,
        _: Principal = Depends(admin),
    ):
        repo = repository(request)
        item = navigation_item(repo, item_id)
        try:
            overrides = repo.list_page_access_overrides(item["route"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        identity_repository = getattr(request.app.state, "identity_repository", None)
        if identity_repository is None:
            raise HTTPException(503, "账号服务未就绪")
        accounts = []
        for user in identity_repository.list_users():
            role_codes = list(user.get("role_codes", []))
            is_system_admin = "system_admin" in role_codes
            is_active = user.get("status") == "active"
            disabled_reason = None
            if is_system_admin:
                disabled_reason = "系统管理员无需配置页面访问权限"
            elif not is_active:
                disabled_reason = "账号已停用"
            override = overrides.get(user["id"])
            access_mode = (
                "allow" if override is True else "deny" if override is False else "inherit"
            )
            accounts.append(
                {
                    "id": user["id"],
                    "username": user["username"],
                    "display_name": user["display_name"],
                    "status": user["status"],
                    "role_codes": role_codes,
                    "access_mode": access_mode,
                    "is_editable": disabled_reason is None,
                    "disabled_reason": disabled_reason,
                }
            )
        return {"item_id": item_id, "route": item["route"], "accounts": accounts}

    @router.put("/api/admin/navigation/{item_id}/accounts")
    def replace_accounts(
        item_id: str,
        payload: NavigationAccountsUpdate,
        request: Request,
        principal: Principal = Depends(admin),
    ):
        repo = repository(request)
        audit_service = getattr(request.app.state, "audit_service", None)
        if audit_service is None:
            raise HTTPException(503, "审计服务未就绪")
        try:
            with repo.database.transaction() as connection:
                item = navigation_item(repo, item_id, connection=connection)
                before = repo.list_page_access_overrides(
                    item["route"], connection=connection
                )
                override_map = {
                    item.user_id: item.access_mode == "allow"
                    for item in payload.overrides
                    if item.access_mode != "inherit"
                }
                if len(override_map) != len(
                    {item.user_id for item in payload.overrides if item.access_mode != "inherit"}
                ):
                    raise ValueError("同一账号只能配置一次页面访问")
                updated = repo.replace_page_access_overrides(
                    item["route"], override_map, connection=connection
                )
                audit_service.record(
                    actor=principal,
                    action="navigation.accounts.update",
                    target_type="navigation_item",
                    target_id=item_id,
                    before={"route": item["route"], "overrides": before},
                    after={"route": item["route"], "overrides": updated},
                    reason=None,
                    result="success",
                    request=request,
                    connection=connection,
                )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"overrides": updated}

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
