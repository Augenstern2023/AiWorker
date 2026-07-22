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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .dependencies import require_permission
from .password_service import PasswordService
from .permission_catalog import PERMISSIONS, PERMISSION_DEPENDENCIES
from .permission_service import PermissionService
from .repository import IdentityRepository
from .schema import (
    Principal,
    RoleCreateRequest,
    RolePermissionRequest,
    RoleUpdateRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
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


class ResetPasswordRequest(BaseModel):
    password: str


class UserRolesRequest(BaseModel):
    role_ids: list[str]


class RolePermissionsRequest(BaseModel):
    permissions: list[RolePermissionRequest]


class RoleCloneRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


def build_identity_router(
    repository: IdentityRepository, session_service: SessionService
) -> APIRouter:
    permission_service = PermissionService(repository, session_service)

    def bind_permission_service(request: Request) -> None:
        request.state.identity_permission_service = permission_service

    router = APIRouter(dependencies=[Depends(bind_permission_service)])
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
        principal = permission_service.principal_for_user(
            context.principal.user_id, context.principal.session_id
        )
        if principal is None:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        return SessionContext(principal=principal, csrf_token=context.csrf_token)

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

        user_id = str(uuid.uuid4())
        setup_already_complete = False
        try:
            with repository.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                role = connection.execute(
                    "SELECT id FROM roles WHERE code = 'system_admin' COLLATE NOCASE"
                ).fetchone()
                if role is None:
                    raise HTTPException(
                        status_code=500, detail="系统管理员角色尚未初始化"
                    )
                bound_active_admins = connection.execute(
                    """
                    SELECT COUNT(DISTINCT u.id)
                    FROM users AS u
                    JOIN user_roles AS ur ON ur.user_id = u.id
                    WHERE u.status = 'active' AND u.is_deleted = 0
                      AND ur.role_id = ?
                    """,
                    (role["id"],),
                ).fetchone()[0]
                if bound_active_admins:
                    setup_already_complete = True
                else:
                    connection.execute(
                        """
                        UPDATE roles
                        SET is_enabled = 1, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (role["id"],),
                    )
                    connection.execute(
                        """
                        INSERT INTO users
                            (id, username, normalized_username, display_name, password_hash)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            username,
                            username.casefold(),
                            display_name,
                            password_hash,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                        (user_id, role["id"]),
                    )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        if setup_already_complete:
            raise HTTPException(status_code=409, detail="系统已经完成初始化")

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

    admin_read = require_permission("*")

    def admin_write(
        request: Request, principal: Principal = Depends(admin_read)
    ) -> Principal:
        session_context(request, require_csrf=True)
        return principal

    @router.get("/api/admin/users")
    def list_users(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
        return repository.list_users()

    @router.post("/api/admin/users", status_code=201)
    def create_user(
        payload: UserCreateRequest, _: Principal = Depends(admin_write)
    ) -> dict[str, Any]:
        username = payload.username.strip()
        display_name = payload.display_name.strip()
        if not username or not display_name:
            raise HTTPException(
                status_code=422, detail="用户名和显示名称不能为空"
            )
        _validate_role_ids(payload.role_ids)
        try:
            password_hash = password_service.hash(payload.password)
            user = repository.create_user(username, display_name, password_hash)
            repository.replace_user_roles(user["id"], payload.role_ids)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _user_response(user["id"])

    @router.patch("/api/admin/users/{user_id}")
    def update_user(
        user_id: str,
        payload: UserUpdateRequest,
        _: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            if payload.role_ids is not None:
                _validate_role_ids(payload.role_ids)
                repository.replace_user_roles(user_id, payload.role_ids)
            repository.update_user(
                user_id,
                display_name=(
                    payload.display_name.strip()
                    if payload.display_name is not None
                    else None
                ),
                status=payload.status,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="用户不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _user_response(user_id)

    @router.post("/api/admin/users/{user_id}/reset-password", status_code=204)
    def reset_password(
        user_id: str,
        payload: ResetPasswordRequest,
        _: Principal = Depends(admin_write),
    ) -> Response:
        if repository.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        try:
            password_hash = password_service.hash(payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with repository.connect() as connection:
            connection.execute(
                """
                UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (password_hash, user_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,),
            )
        return Response(status_code=204)

    @router.put("/api/admin/users/{user_id}/roles")
    def replace_user_roles(
        user_id: str,
        payload: UserRolesRequest,
        _: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            _validate_role_ids(payload.role_ids)
            repository.replace_user_roles(user_id, payload.role_ids)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="用户不存在") from exc
        except ValueError as exc:
            status_code = 409 if "系统管理员" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return _user_response(user_id)

    @router.get("/api/admin/roles")
    def list_roles(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
        return [_role_response(role) for role in repository.list_roles()]

    @router.post("/api/admin/roles", status_code=201)
    def create_role(
        payload: RoleCreateRequest, _: Principal = Depends(admin_write)
    ) -> dict[str, Any]:
        try:
            grants = _permission_pairs(payload.permissions)
            _validate_permission_pairs(grants)
            role = repository.create_role(
                payload.code, payload.name.strip(), payload.description
            )
            repository.replace_role_permissions(role["id"], grants)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="角色编码已存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _role_response(role)

    @router.patch("/api/admin/roles/{role_id}")
    def update_role(
        role_id: str,
        payload: RoleUpdateRequest,
        _: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            if payload.permissions is not None:
                _replace_permissions(role_id, payload.permissions)
            role = repository.update_role(
                role_id,
                name=payload.name.strip() if payload.name is not None else None,
                description=payload.description,
                is_enabled=payload.is_enabled,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except ValueError as exc:
            status_code = 409 if "系统管理员" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return _role_response(role)

    @router.post("/api/admin/roles/{role_id}/clone", status_code=201)
    def clone_role(
        role_id: str,
        payload: RoleCloneRequest,
        _: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        source = repository.get_role(role_id)
        if source is None:
            raise HTTPException(status_code=404, detail="角色不存在")
        try:
            role = repository.create_role(
                payload.code,
                payload.name.strip(),
                source["description"]
                if payload.description is None
                else payload.description,
            )
            source_permissions = repository.list_role_permissions(role_id)
            repository.replace_role_permissions(
                role["id"],
                [
                    (item["permission_code"], item["data_scope"])
                    for item in source_permissions
                ],
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="角色编码已存在") from exc
        return _role_response(role)

    @router.put("/api/admin/roles/{role_id}/permissions")
    def replace_role_permissions(
        role_id: str,
        payload: RolePermissionsRequest,
        _: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            _replace_permissions(role_id, payload.permissions)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        role = repository.get_role(role_id)
        assert role is not None
        return _role_response(role)

    @router.get("/api/admin/permissions")
    def list_permissions(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
        with repository.connect() as connection:
            rows = connection.execute(
                "SELECT code, label FROM permissions ORDER BY code"
            ).fetchall()
        return [
            {
                "code": row["code"],
                "label": row["label"],
                "dependencies": sorted(PERMISSION_DEPENDENCIES.get(row["code"], ())),
            }
            for row in rows
        ]

    def _validate_role_ids(role_ids: list[str]) -> None:
        known = {role["id"] for role in repository.list_roles()}
        if not set(role_ids) <= known:
            raise HTTPException(status_code=422, detail="角色不存在")

    def _user_response(user_id: str) -> dict[str, Any]:
        user = repository.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        return user

    def _role_response(role: dict[str, Any]) -> dict[str, Any]:
        return {
            **role,
            "permissions": repository.list_role_permissions(role["id"]),
        }

    def _permission_pairs(
        permissions: list[RolePermissionRequest],
    ) -> list[tuple[str, str]]:
        return [
            (permission.permission_code, permission.data_scope)
            for permission in permissions
        ]

    def _validate_permission_pairs(grants: list[tuple[str, str]]) -> None:
        unknown = {code for code, _ in grants} - set(PERMISSIONS)
        if unknown:
            raise ValueError(f"未知权限: {', '.join(sorted(unknown))}")

    def _replace_permissions(
        role_id: str, permissions: list[RolePermissionRequest]
    ) -> None:
        role = repository.get_role(role_id)
        if role is None:
            raise KeyError(role_id)
        grants = _permission_pairs(permissions)
        _validate_permission_pairs(grants)
        requested_codes = {code for code, _ in grants}
        required_codes = set(permission_service.expand_permissions(grants)) - requested_codes
        existing_codes = {
            item["permission_code"]
            for item in repository.list_role_permissions(role_id)
        }
        removed_dependencies = sorted(required_codes & existing_codes - requested_codes)
        if removed_dependencies:
            raise ValueError(
                "仍被高级权限依赖，不能移除: " + ", ".join(removed_dependencies)
            )
        if role["code"].casefold() == "system_admin" and "*" not in requested_codes:
            raise ValueError("系统管理员角色必须保留通配权限")
        repository.replace_role_permissions(role_id, grants)

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
        principal = permission_service.principal_for_user(
            context.principal.user_id, context.principal.session_id
        )
        if principal is None:
            raise HTTPException(status_code=500, detail="无法创建登录会话")
        context = SessionContext(principal=principal, csrf_token=context.csrf_token)
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
