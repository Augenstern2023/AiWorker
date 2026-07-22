# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .password_service import PasswordService
from .repository import IdentityRepository
from .session_service import SessionContext, SessionService


AUTHENTICATION_ERROR = "用户名或密码错误"
TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates" / "auth"


class SetupRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    password: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def build_identity_router(
    repository: IdentityRepository, session_service: SessionService
) -> APIRouter:
    router = APIRouter()
    password_service = PasswordService()
    dummy_password_hash = password_service.hash("Dummy-Password-123!")
    repository.seed_defaults()

    def session_context(request: Request, *, require_csrf: bool = False) -> SessionContext:
        raw_token = request.cookies.get(session_service.cookie_name)
        context = session_service.resolve_session(raw_token)
        if context is None:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        if require_csrf and not session_service.verify_csrf(
            raw_token or "", request.headers.get("X-CSRF-Token")
        ):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        return context

    @router.get("/login", response_class=HTMLResponse)
    def login_page() -> HTMLResponse:
        return HTMLResponse(_read_template("login.html"))

    @router.get("/setup", response_class=HTMLResponse)
    def setup_page() -> HTMLResponse:
        if repository.count_active_system_admins() > 0:
            raise HTTPException(status_code=404, detail="初始化页面不可用")
        return HTMLResponse(_read_template("setup.html"))

    @router.post("/api/auth/setup", status_code=201)
    def setup(payload: SetupRequest, request: Request) -> JSONResponse:
        if repository.count_active_system_admins() > 0:
            raise HTTPException(status_code=409, detail="系统已经完成初始化")
        username = payload.username.strip()
        display_name = payload.display_name.strip()
        if not username or not display_name:
            raise HTTPException(status_code=422, detail="用户名和显示名称不能为空")
        try:
            password_hash = password_service.hash(payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        role = repository.get_role_by_code("system_admin")
        if role is None:
            raise HTTPException(status_code=500, detail="系统管理员角色尚未初始化")
        user_id = str(uuid.uuid4())
        try:
            with repository.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                active_admins = connection.execute(
                    """
                    SELECT COUNT(DISTINCT u.id)
                    FROM users AS u
                    JOIN user_roles AS ur ON ur.user_id = u.id
                    JOIN roles AS r ON r.id = ur.role_id
                    WHERE u.status = 'active' AND u.is_deleted = 0
                      AND r.code = 'system_admin' AND r.is_enabled = 1
                    """
                ).fetchone()[0]
                if active_admins:
                    raise HTTPException(status_code=409, detail="系统已经完成初始化")
                connection.execute(
                    """
                    INSERT INTO users
                        (id, username, normalized_username, display_name, password_hash)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, username, username.casefold(), display_name, password_hash),
                )
                connection.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                    (user_id, role["id"]),
                )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc

        return authenticated_response(
            user_id, request, status_code=201, update_last_login=True
        )

    @router.post("/api/auth/login")
    def login(payload: LoginRequest, request: Request) -> JSONResponse:
        normalized_username = payload.username.strip().casefold()
        with repository.connect() as connection:
            user = connection.execute(
                """
                SELECT id, password_hash, status, is_deleted
                FROM users WHERE normalized_username = ? COLLATE NOCASE
                """,
                (normalized_username,),
            ).fetchone()
        password_hash = user["password_hash"] if user is not None else dummy_password_hash
        password_matches = password_service.verify(password_hash, payload.password)
        if (
            user is None
            or not password_matches
            or user["status"] != "active"
            or user["is_deleted"]
        ):
            raise HTTPException(status_code=401, detail=AUTHENTICATION_ERROR)
        return authenticated_response(
            user["id"], request, status_code=200, update_last_login=True
        )

    @router.post("/api/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        session_context(request, require_csrf=True)
        raw_token = request.cookies.get(session_service.cookie_name)
        if raw_token:
            session_service.revoke_session(raw_token)
        response = Response(status_code=204)
        response.delete_cookie(
            session_service.cookie_name,
            path="/",
            secure=session_service.secure_cookie,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/api/auth/me")
    def me(request: Request) -> dict[str, Any]:
        context = session_context(request)
        return _auth_body(context)

    @router.post("/api/auth/change-password", status_code=204)
    def change_password(payload: ChangePasswordRequest, request: Request) -> Response:
        context = session_context(request, require_csrf=True)
        with repository.connect() as connection:
            user = connection.execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (context.principal.user_id,),
            ).fetchone()
        if user is None or not password_service.verify(
            user["password_hash"], payload.current_password
        ):
            raise HTTPException(status_code=400, detail="当前密码错误")
        try:
            new_hash = password_service.hash(payload.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with repository.connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_hash, context.principal.user_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (context.principal.user_id,),
            )
        response = Response(status_code=204)
        response.delete_cookie(
            session_service.cookie_name,
            path="/",
            secure=session_service.secure_cookie,
            httponly=True,
            samesite="lax",
        )
        return response

    def authenticated_response(
        user_id: str,
        request: Request,
        *,
        status_code: int,
        update_last_login: bool,
    ) -> JSONResponse:
        raw_token, _ = session_service.create_session(user_id, request)
        context = session_service.resolve_session(raw_token)
        if context is None:
            raise HTTPException(status_code=500, detail="无法创建登录会话")
        if update_last_login:
            with repository.connect() as connection:
                connection.execute(
                    "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (user_id,),
                )
        response = JSONResponse(_auth_body(context), status_code=status_code)
        response.set_cookie(
            session_service.cookie_name,
            raw_token,
            max_age=int(session_service.session_ttl.total_seconds()),
            path="/",
            secure=session_service.secure_cookie,
            httponly=True,
            samesite="lax",
        )
        return response

    return router


def _auth_body(context: SessionContext) -> dict[str, Any]:
    principal = context.principal
    return {
        "user": {
            "id": principal.user_id,
            "username": principal.username,
            "display_name": principal.display_name,
            "role_codes": list(principal.role_codes),
            "permissions": sorted(principal.permissions),
            "data_scope": principal.data_scope,
        },
        "csrf_token": context.csrf_token,
        "landing_route": _landing_route(principal.permissions),
    }


def _landing_route(permissions: frozenset[str]) -> str:
    if "*" in permissions:
        return "/tasks"
    for permission, route in (
        ("page.selection", "/selection/dashboard"),
        ("page.planning", "/planning/projects"),
        ("page.writing", "/writing/projects"),
        ("page.review", "/review/tasks"),
        ("page.admin.delivery", "/admin/deliveries"),
    ):
        if permission in permissions:
            return route
    return "/tasks"


def _read_template(name: str) -> str:
    return (TEMPLATE_ROOT / name).read_text(encoding="utf-8")
