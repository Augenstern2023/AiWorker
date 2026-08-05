# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import csv
import uuid
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection, and_, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from system_database.schema import (
    permissions as permissions_table,
    roles as roles_table,
    sessions as sessions_table,
    user_roles as user_roles_table,
    users as users_table,
)

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field

from audit.service import AuditService

from .dependencies import require_page_api
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
    UserProfileUpdateRequest,
    UserUpdateRequest,
)
from .session_service import SessionContext, SessionService
from web.page_routes import PAGE_ROUTES


AUTHENTICATION_ERROR = "用户名或密码错误"
TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates" / "auth"
REGISTRATION_ROLE_CODES = frozenset(
    {"selection_operator", "creative_planner", "screenwriter"}
)


class SetupRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    password: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str


class RegistrationRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    password: str
    role_codes: list[str] = Field(min_length=1)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    gender: Literal["unspecified", "male", "female"] = "unspecified"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    password: str


class UserRolesRequest(BaseModel):
    role_ids: list[str]


class RolePermissionsRequest(BaseModel):
    permissions: list[RolePermissionRequest]


class RolePagesRequest(BaseModel):
    page_routes: list[str]


class RoleCloneRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


def build_identity_router(
    repository: IdentityRepository,
    session_service: SessionService,
    audit_service: AuditService | None = None,
    *,
    password_service: PasswordService | None = None,
    permission_service: PermissionService | None = None,
) -> APIRouter:
    permission_service = permission_service or PermissionService(
        repository, session_service
    )

    def bind_permission_service(request: Request) -> None:
        request.state.identity_permission_service = permission_service

    router = APIRouter(dependencies=[Depends(bind_permission_service)])
    password_service = password_service or PasswordService()
    dummy_password_hash = password_service.hash("Dummy-Password-123!")
    repository.seed_defaults()

    def record_audit(
        *,
        actor: Principal | None,
        action: str,
        target_type: str,
        target_id: str | None,
        before: Any,
        after: Any,
        result: str,
        request: Request,
        connection: Connection | None = None,
    ) -> None:
        if audit_service is not None:
            audit_service.record(
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                before=before,
                after=after,
                reason=None,
                result=result,
                request=request,
                connection=connection,
            )

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

    @router.get("/register", response_class=HTMLResponse)
    def register_page() -> HTMLResponse:
        return HTMLResponse(_read_template("register.html"))

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
        response: JSONResponse | None = None
        try:
            with repository.transaction() as connection:
                role = connection.execute(
                    select(roles_table.c.id).where(roles_table.c.code == "system_admin")
                ).mappings().first()
                if role is None:
                    raise HTTPException(
                        status_code=500, detail="系统管理员角色尚未初始化"
                    )
                bound_active_admins = connection.execute(
                    select(func.count(func.distinct(users_table.c.id)))
                    .select_from(
                        users_table.join(
                            user_roles_table,
                            user_roles_table.c.user_id == users_table.c.id,
                        )
                    )
                    .where(
                        users_table.c.status == "active",
                        users_table.c.is_deleted == 0,
                        user_roles_table.c.role_id == role["id"],
                    )
                ).scalar_one()
                if bound_active_admins:
                    setup_already_complete = True
                else:
                    connection.execute(
                        update(roles_table)
                        .where(roles_table.c.id == role["id"])
                        .values(is_enabled=1, updated_at=func.now())
                    )
                    connection.execute(
                        insert(users_table).values(
                            id=user_id,
                            username=username,
                            normalized_username=username.casefold(),
                            display_name=display_name,
                            password_hash=password_hash,
                        )
                    )
                    connection.execute(
                        insert(user_roles_table).values(
                            user_id=user_id, role_id=role["id"]
                        )
                    )
                    response = authenticated_response(
                        user_id,
                        request,
                        status_code=201,
                        update_last_login=True,
                        audit_action="user.setup",
                        connection=connection,
                    )
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        if setup_already_complete:
            raise HTTPException(status_code=409, detail="系统已经完成初始化")
        assert response is not None
        return response

    @router.post("/api/auth/register", status_code=201)
    def register(payload: RegistrationRequest, request: Request) -> dict[str, str]:
        username = payload.username.strip()
        display_name = payload.display_name.strip()
        role_codes = {code.strip().casefold() for code in payload.role_codes if code.strip()}
        if not username or not display_name:
            raise HTTPException(status_code=422, detail="用户名和显示名称不能为空")
        if not role_codes:
            raise HTTPException(status_code=422, detail="请至少选择一个角色")
        if not role_codes <= REGISTRATION_ROLE_CODES:
            raise HTTPException(status_code=422, detail="所选角色不可注册")
        try:
            password_hash = password_service.hash(payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            with repository.transaction() as connection:
                role_rows = connection.execute(
                    select(roles_table.c.id, roles_table.c.code).where(
                        roles_table.c.code.in_(role_codes),
                        roles_table.c.is_enabled == 1,
                        roles_table.c.is_deleted == 0,
                    )
                ).mappings().all()
                if {role["code"] for role in role_rows} != role_codes:
                    raise HTTPException(status_code=422, detail="所选角色不可用")
                user = repository.create_user(
                    username,
                    display_name,
                    password_hash,
                    email=payload.email,
                    phone=payload.phone,
                    gender=payload.gender,
                    connection=connection,
                )
                connection.execute(
                    update(users_table)
                    .where(users_table.c.id == user["id"])
                    .values(status="disabled", updated_at=func.now())
                )
                repository.replace_user_roles(
                    user["id"], [role["id"] for role in role_rows], connection=connection
                )
                record_audit(
                    actor=None,
                    action="user.register",
                    target_type="user",
                    target_id=user["id"],
                    before=None,
                    after={
                        "username": username,
                        "role_codes": sorted(role_codes),
                        "status": "disabled",
                    },
                    result="success",
                    request=request,
                    connection=connection,
                )
                return {"detail": "注册申请已提交，请等待管理员激活"}
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc

    @router.post("/api/auth/login")
    def login(payload: LoginRequest, request: Request) -> JSONResponse:
        normalized_username = payload.username.strip().casefold()
        with repository.connect() as connection:
            user = connection.execute(
                select(
                    users_table.c.id,
                    users_table.c.password_hash,
                    users_table.c.status,
                    users_table.c.is_deleted,
                ).where(users_table.c.normalized_username == normalized_username)
            ).mappings().first()
        password_hash = user["password_hash"] if user is not None else dummy_password_hash
        password_matches = password_service.verify(password_hash, payload.password)
        if (
            user is None
            or not password_matches
            or user["status"] != "active"
            or user["is_deleted"]
        ):
            record_audit(
                actor=None,
                action="auth.login",
                target_type="user",
                target_id=user["id"] if user is not None else normalized_username,
                before=None,
                after={"username": normalized_username},
                result="failure",
                request=request,
            )
            raise HTTPException(status_code=401, detail=AUTHENTICATION_ERROR)
        return authenticated_response(
            user["id"],
            request,
            status_code=200,
            update_last_login=True,
            audit_action="auth.login",
        )

    @router.post("/api/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        context = session_context(request, require_csrf=True)
        raw_token = request.cookies.get(session_service.cookie_name)
        with repository.transaction() as connection:
            if raw_token:
                session_service.revoke_session(
                    raw_token, connection=connection
                )
            record_audit(
                actor=context.principal,
                action="auth.logout",
                target_type="session",
                target_id=context.principal.session_id,
                before={"active": True},
                after={"active": False},
                result="success",
                request=request,
                connection=connection,
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

    @router.get("/api/auth/me")
    def me(request: Request) -> dict[str, Any]:
        context = session_context(request)
        return _auth_body(context)

    @router.post("/api/auth/change-password", status_code=204)
    def change_password(payload: ChangePasswordRequest, request: Request) -> Response:
        context = session_context(request, require_csrf=True)
        with repository.connect() as connection:
            user = connection.execute(
                select(users_table.c.password_hash).where(
                    users_table.c.id == context.principal.user_id
                )
            ).mappings().first()
        if user is None or not password_service.verify(
            user["password_hash"], payload.current_password
        ):
            raise HTTPException(status_code=400, detail="当前密码错误")
        try:
            new_hash = password_service.hash(payload.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with repository.transaction() as connection:
            connection.execute(
                update(users_table)
                .where(users_table.c.id == context.principal.user_id)
                .values(password_hash=new_hash, updated_at=func.now())
            )
            connection.execute(
                update(sessions_table)
                .where(
                    and_(
                        sessions_table.c.user_id == context.principal.user_id,
                        sessions_table.c.revoked_at.is_(None),
                    )
                )
                .values(revoked_at=func.now())
            )
            record_audit(
                actor=context.principal,
                action="auth.password.change",
                target_type="user",
                target_id=context.principal.user_id,
                before=None,
                after={"sessions_revoked": True},
                result="success",
                request=request,
                connection=connection,
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

    admin_read = require_page_api()

    def admin_write(
        request: Request, principal: Principal = Depends(admin_read)
    ) -> Principal:
        session_context(request, require_csrf=True)
        return principal

    @router.get("/api/profile")
    def get_current_profile(request: Request) -> dict[str, Any]:
        return _user_response(session_context(request).principal.user_id)

    @router.patch("/api/profile")
    def update_current_profile(
        payload: UserProfileUpdateRequest, request: Request
    ) -> dict[str, Any]:
        context = session_context(request, require_csrf=True)
        if not payload.model_fields_set:
            raise HTTPException(status_code=422, detail="至少提交一项个人资料")
        try:
            with repository.transaction() as connection:
                before = repository.get_user(
                    context.principal.user_id, connection=connection
                )
                assert before is not None
                repository.update_user(
                    context.principal.user_id,
                    email=(payload.email if "email" in payload.model_fields_set else repository._UNSET),
                    phone=(payload.phone if "phone" in payload.model_fields_set else repository._UNSET),
                    gender=(payload.gender if "gender" in payload.model_fields_set else repository._UNSET),
                    connection=connection,
                )
                updated = _user_response(
                    context.principal.user_id, connection=connection
                )
                record_audit(
                    actor=context.principal,
                    action="user.profile.update",
                    target_type="user",
                    target_id=context.principal.user_id,
                    before=_masked_user_audit_snapshot(before),
                    after=_masked_user_audit_snapshot(updated),
                    result="success",
                    request=request,
                    connection=connection,
                )
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="邮箱或手机号已存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return updated

    @router.get("/api/admin/users")
    def list_users(
        username: str | None = None,
        role_code: str | None = None,
        department_id: str | None = None,
        gender: str | None = None,
        status: str | None = None,
        principal: Principal = Depends(admin_read),
    ) -> list[dict[str, Any]]:
        users = repository.list_users(
            username=username,
            role_code=role_code,
            department_id=department_id,
            gender=gender,
            status=status,
        )
        return [
            {**user, "can_delete": user["id"] != principal.user_id}
            for user in users
        ]

    @router.get("/api/admin/users/export")
    def export_users(
        request: Request,
        format: Literal["csv", "xlsx"] = "csv",
        username: str | None = None,
        role_code: str | None = None,
        department_id: str | None = None,
        gender: str | None = None,
        status: str | None = None,
        principal: Principal = Depends(admin_read),
    ) -> Response:
        rows = repository.list_users(
            username=username,
            role_code=role_code,
            department_id=department_id,
            gender=gender,
            status=status,
        )
        headers = [
            "用户名", "显示名称", "部门", "邮箱", "手机号", "性别",
            "创建时间", "状态", "角色",
        ]
        gender_labels = {"unspecified": "未填写", "male": "男", "female": "女"}
        values = [
            [
                user["username"],
                user["display_name"],
                "、".join(department["name"] for department in user["departments"]) or "-",
                user.get("email") or "",
                user.get("phone") or "",
                gender_labels.get(user.get("gender"), "未填写"),
                str(user.get("created_at") or ""),
                "启用" if user.get("status") == "active" else "停用",
                "、".join(user.get("role_codes") or []),
            ]
            for user in rows
        ]
        record_audit(
            actor=principal,
            action="user.export",
            target_type="user",
            target_id=None,
            before=None,
            after={"format": format, "exported_count": len(rows)},
            result="success",
            request=request,
        )
        filename = "用户人员表"
        if format == "csv":
            output = StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(headers)
            writer.writerows(values)
            return Response(
                content=("\ufeff" + output.getvalue()).encode("utf-8"),
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''%E7%94%A8%E6%88%B7%E4%BA%BA%E5%91%98%E8%A1%A8.csv"
                },
            )
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = filename
        worksheet.append(headers)
        for value in values:
            worksheet.append(value)
        output = BytesIO()
        workbook.save(output)
        return Response(
            content=output.getvalue(),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''%E7%94%A8%E6%88%B7%E4%BA%BA%E5%91%98%E8%A1%A8.xlsx"
            },
        )

    @router.post("/api/admin/users", status_code=201)
    def create_user(
        payload: UserCreateRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
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
            with repository.transaction() as connection:
                user = repository.create_user(
                    username,
                    display_name,
                    password_hash,
                    email=payload.email,
                    phone=payload.phone,
                    gender=payload.gender,
                    created_by_user_id=principal.user_id,
                    connection=connection,
                )
                repository.replace_user_roles(
                    user["id"], payload.role_ids, connection=connection
                )
                created = _user_response(user["id"], connection=connection)
                record_audit(
                    actor=principal,
                    action="user.create",
                    target_type="user",
                    target_id=user["id"],
                    before=None,
                    after=_masked_user_audit_snapshot(created),
                    result="success",
                    request=request,
                    connection=connection,
                )
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名、邮箱或手机号已存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return created

    @router.patch("/api/admin/users/{user_id}")
    def update_user(
        user_id: str,
        payload: UserUpdateRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            if payload.role_ids is not None:
                _validate_role_ids(payload.role_ids)
            with repository.transaction() as connection:
                before = repository.get_user(user_id, connection=connection)
                repository.update_user(
                    user_id,
                    display_name=(
                        payload.display_name.strip()
                        if payload.display_name is not None
                        else None
                    ),
                    status=payload.status,
                    role_ids=payload.role_ids,
                    email=(payload.email if "email" in payload.model_fields_set else repository._UNSET),
                    phone=(payload.phone if "phone" in payload.model_fields_set else repository._UNSET),
                    gender=(payload.gender if "gender" in payload.model_fields_set else repository._UNSET),
                    connection=connection,
                )
                updated = repository.get_user(user_id, connection=connection)
                assert updated is not None
                record_audit(
                    actor=principal,
                    action="user.update",
                    target_type="user",
                    target_id=user_id,
                    before=_masked_user_audit_snapshot(before or {}),
                    after=_masked_user_audit_snapshot(updated),
                    result="success",
                    request=request,
                    connection=connection,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="用户不存在") from exc
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名、邮箱或手机号已存在") from exc
        except ValueError as exc:
            status_code = 409 if "系统必须至少保留" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return updated

    @router.delete("/api/admin/users/{user_id}", status_code=204)
    def delete_user(
        user_id: str,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> Response:
        try:
            with repository.transaction() as connection:
                before = repository.get_user(user_id, connection=connection)
                if before is None:
                    raise KeyError(user_id)
                if user_id == principal.user_id:
                    raise ValueError("不能删除当前登录账号")
                repository.soft_delete_user(
                    user_id,
                    deleted_by_user_id=principal.user_id,
                    connection=connection,
                )
                record_audit(
                    actor=principal,
                    action="user.delete",
                    target_type="user",
                    target_id=user_id,
                    before=before,
                    after={"is_deleted": True},
                    result="success",
                    request=request,
                    connection=connection,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="用户不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=204)

    @router.post("/api/admin/users/{user_id}/reset-password", status_code=204)
    def reset_password(
        user_id: str,
        payload: ResetPasswordRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> Response:
        try:
            password_hash = password_service.hash(payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with repository.transaction() as connection:
            if repository.get_user(user_id, connection=connection) is None:
                raise HTTPException(status_code=404, detail="用户不存在")
            connection.execute(
                update(users_table)
                .where(users_table.c.id == user_id)
                .values(password_hash=password_hash, updated_at=func.now())
            )
            connection.execute(
                update(sessions_table)
                .where(
                    and_(
                        sessions_table.c.user_id == user_id,
                        sessions_table.c.revoked_at.is_(None),
                    )
                )
                .values(revoked_at=func.now())
            )
            record_audit(
                actor=principal,
                action="user.password.reset",
                target_type="user",
                target_id=user_id,
                before=None,
                after={"sessions_revoked": True},
                result="success",
                request=request,
                connection=connection,
            )
        return Response(status_code=204)

    @router.put("/api/admin/users/{user_id}/roles")
    def replace_user_roles(
        user_id: str,
        payload: UserRolesRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            _validate_role_ids(payload.role_ids)
            with repository.transaction() as connection:
                user_before = repository.get_user(user_id, connection=connection)
                repository.replace_user_roles(
                    user_id, payload.role_ids, connection=connection
                )
                user_after = _user_response(user_id, connection=connection)
                record_audit(
                    actor=principal,
                    action="user.roles.replace",
                    target_type="user",
                    target_id=user_id,
                    before={
                        "role_codes": (
                            user_before["role_codes"] if user_before else []
                        )
                    },
                    after={"role_codes": user_after["role_codes"]},
                    result="success",
                    request=request,
                    connection=connection,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="用户不存在") from exc
        except ValueError as exc:
            status_code = 409 if "系统管理员" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return user_after

    @router.get("/api/admin/roles")
    def list_roles(
        keyword: str | None = None,
        status: str | None = None,
        _: Principal = Depends(admin_read),
    ) -> list[dict[str, Any]]:
        return [
            _role_response(role)
            for role in repository.list_roles(
                keyword=keyword, status=_role_filter_status(status)
            )
        ]

    @router.get("/api/admin/roles/page-catalog")
    def list_role_page_catalog(
        _: Principal = Depends(admin_read),
    ) -> list[dict[str, str]]:
        return [
            {"route": route, "label": label}
            for route, _, label in PAGE_ROUTES
            if "{" not in route
        ]

    @router.get("/api/admin/roles/export")
    def export_roles(
        request: Request,
        format: Literal["csv", "xlsx"] = "csv",
        keyword: str | None = None,
        status: str | None = None,
        principal: Principal = Depends(admin_read),
    ) -> Response:
        normalized_status = _role_filter_status(status)
        rows = [
            _role_response(role)
            for role in repository.list_roles(
                keyword=keyword, status=normalized_status
            )
        ]
        headers = [
            "角色名称", "角色代码", "说明", "系统角色", "状态", "权限数",
            "权限", "可领取任务类型", "创建时间",
        ]
        values = [
            [
                role["name"],
                role["code"],
                role.get("description") or "",
                "是" if role.get("is_system") else "否",
                "启用" if role.get("is_enabled") else "停用",
                len(role.get("permissions") or []),
                "、".join(
                    f"{grant['permission_code']} ({grant['data_scope']})"
                    for grant in role.get("permissions") or []
                ),
                "、".join(role.get("claimable_task_types") or []),
                str(role.get("created_at") or ""),
            ]
            for role in rows
        ]
        record_audit(
            actor=principal,
            action="role.export",
            target_type="role",
            target_id=None,
            before=None,
            after={
                "format": format,
                "filters": {
                    "keyword": keyword or "", "status": normalized_status or ""
                },
                "exported_count": len(rows),
            },
            result="success",
            request=request,
        )
        filename = "角色权限表"
        if format == "csv":
            output = StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(headers)
            writer.writerows(values)
            return Response(
                content=("\ufeff" + output.getvalue()).encode("utf-8"),
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''%E8%A7%92%E8%89%B2%E6%9D%83%E9%99%90%E8%A1%A8.csv"
                },
            )
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = filename
        worksheet.append(headers)
        for value in values:
            worksheet.append(value)
        output = BytesIO()
        workbook.save(output)
        return Response(
            content=output.getvalue(),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''%E8%A7%92%E8%89%B2%E6%9D%83%E9%99%90%E8%A1%A8.xlsx"
            },
        )

    @router.post("/api/admin/roles", status_code=201)
    def create_role(
        payload: RoleCreateRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            grants = _permission_pairs(payload.permissions)
            _validate_permission_pairs(grants)
            with repository.transaction() as connection:
                role = repository.create_role(
                    payload.code,
                    payload.name.strip(),
                    payload.description,
                    created_by_user_id=principal.user_id,
                    connection=connection,
                )
                repository.replace_role_permissions(
                    role["id"], grants, connection=connection
                )
                repository.replace_role_claimable_task_types(
                    role["id"], payload.claimable_task_types, connection=connection
                )
                created = _role_response(role, connection=connection)
                record_audit(
                    actor=principal,
                    action="role.create",
                    target_type="role",
                    target_id=role["id"],
                    before=None,
                    after=created,
                    result="success",
                    request=request,
                    connection=connection,
                )
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="角色编码已存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return created

    @router.patch("/api/admin/roles/{role_id}")
    def update_role(
        role_id: str,
        payload: RoleUpdateRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            with repository.transaction() as connection:
                existing_role = repository.get_role(role_id, connection=connection)
                before = (
                    _role_response(existing_role, connection=connection)
                    if existing_role is not None
                    else None
                )
                if payload.permissions is not None:
                    _replace_permissions(
                        role_id, payload.permissions, connection=connection
                    )
                if payload.claimable_task_types is not None:
                    repository.replace_role_claimable_task_types(
                        role_id,
                        payload.claimable_task_types,
                        connection=connection,
                    )
                role = repository.update_role(
                    role_id,
                    name=(
                        payload.name.strip() if payload.name is not None else None
                    ),
                    description=payload.description,
                    is_enabled=payload.is_enabled,
                    connection=connection,
                )
                updated = _role_response(role, connection=connection)
                record_audit(
                    actor=principal,
                    action="role.update",
                    target_type="role",
                    target_id=role_id,
                    before=before,
                    after=updated,
                    result="success",
                    request=request,
                    connection=connection,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except ValueError as exc:
            status_code = 409 if "系统管理员" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return updated

    @router.post("/api/admin/roles/{role_id}/clone", status_code=201)
    def clone_role(
        role_id: str,
        payload: RoleCloneRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            with repository.transaction() as connection:
                source = repository.get_role(role_id, connection=connection)
                if source is None:
                    raise KeyError(role_id)
                role = repository.create_role(
                    payload.code,
                    payload.name.strip(),
                    (
                        source["description"]
                        if payload.description is None
                        else payload.description
                    ),
                    created_by_user_id=principal.user_id,
                    connection=connection,
                )
                source_permissions = repository.list_role_permissions(
                    role_id, connection=connection
                )
                repository.replace_role_permissions(
                    role["id"],
                    [
                        (item["permission_code"], item["data_scope"])
                        for item in source_permissions
                    ],
                    connection=connection,
                )
                repository.replace_role_claimable_task_types(
                    role["id"],
                    repository.list_role_claimable_task_types(
                        role_id, connection=connection
                    ),
                    connection=connection,
                )
                repository.replace_role_page_access(
                    role["id"],
                    repository.list_role_page_access(role_id, connection=connection),
                    connection=connection,
                )
                cloned = _role_response(role, connection=connection)
                record_audit(
                    actor=principal,
                    action="role.clone",
                    target_type="role",
                    target_id=role["id"],
                    before={"source_role_id": role_id},
                    after=cloned,
                    result="success",
                    request=request,
                    connection=connection,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="角色编码已存在") from exc
        return cloned

    @router.delete("/api/admin/roles/{role_id}", status_code=204)
    def delete_role(
        role_id: str,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> Response:
        try:
            with repository.transaction() as connection:
                before_role = repository.get_role(role_id, connection=connection)
                if before_role is None:
                    raise KeyError(role_id)
                before = _role_response(before_role, connection=connection)
                repository.soft_delete_role(
                    role_id,
                    deleted_by_user_id=principal.user_id,
                    connection=connection,
                )
                record_audit(
                    actor=principal,
                    action="role.delete",
                    target_type="role",
                    target_id=role_id,
                    before=before,
                    after={"is_deleted": True},
                    result="success",
                    request=request,
                    connection=connection,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(status_code=204)

    @router.put("/api/admin/roles/{role_id}/permissions")
    def replace_role_permissions(
        role_id: str,
        payload: RolePermissionsRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            with repository.transaction() as connection:
                before = repository.list_role_permissions(
                    role_id, connection=connection
                )
                _replace_permissions(
                    role_id, payload.permissions, connection=connection
                )
                role = repository.get_role(role_id, connection=connection)
                assert role is not None
                updated = _role_response(role, connection=connection)
                record_audit(
                    actor=principal,
                    action="role.permissions.replace",
                    target_type="role",
                    target_id=role_id,
                    before={"permissions": before},
                    after={"permissions": updated["permissions"]},
                    result="success",
                    request=request,
                    connection=connection,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return updated

    @router.put("/api/admin/roles/{role_id}/pages")
    def replace_role_pages(
        role_id: str,
        payload: RolePagesRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        try:
            with repository.transaction() as connection:
                before = repository.list_role_page_access(role_id, connection=connection)
                routes = repository.replace_role_page_access(
                    role_id, payload.page_routes, connection=connection
                )
                role = repository.get_role(role_id, connection=connection)
                assert role is not None
                updated = _role_response(role, connection=connection)
                record_audit(
                    actor=principal,
                    action="role.pages.replace",
                    target_type="role",
                    target_id=role_id,
                    before={"page_routes": before},
                    after={"page_routes": sorted(routes)},
                    result="success",
                    request=request,
                    connection=connection,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return updated

    @router.get("/api/admin/permissions")
    def list_permissions(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
        with repository.connect() as connection:
            rows = connection.execute(
                select(permissions_table.c.code, permissions_table.c.label).order_by(
                    permissions_table.c.code
                )
            ).mappings().all()
        return [
            {
                "code": row["code"],
                "label": row["label"],
                "dependencies": sorted(PERMISSION_DEPENDENCIES.get(row["code"], ())),
            }
            for row in rows
        ]

    def _validate_role_ids(role_ids: list[str]) -> None:
        roles = {role["id"]: role for role in repository.list_roles()}
        known = set(roles)
        if not set(role_ids) <= known:
            raise HTTPException(status_code=422, detail="角色不存在")
        if any(roles[role_id]["code"] == "project_owner" for role_id in role_ids):
            raise HTTPException(
                status_code=422,
                detail="项目负责人角色只能通过专用接任操作变更",
            )

    def _role_filter_status(status: str | None) -> str | None:
        normalized = (status or "").strip().casefold()
        if normalized not in {"", "enabled", "disabled"}:
            raise HTTPException(status_code=422, detail="角色状态筛选值无效")
        return normalized or None

    def _user_response(
        user_id: str, *, connection: Connection | None = None
    ) -> dict[str, Any]:
        user = repository.get_user(user_id, connection=connection)
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        return user

    def _masked_user_audit_snapshot(user: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(user)
        email = snapshot.get("email")
        phone = snapshot.get("phone")
        if email:
            local, _, domain = str(email).partition("@")
            snapshot["email"] = f"{local[:1]}***@{domain}"
        if phone:
            snapshot["phone"] = f"*******{str(phone)[-4:]}"
        return snapshot

    def _role_response(
        role: dict[str, Any], *, connection: Connection | None = None
    ) -> dict[str, Any]:
        return {
            **role,
            "permissions": repository.list_role_permissions(
                role["id"], connection=connection
            ),
            "claimable_task_types": repository.list_role_claimable_task_types(
                role["id"], connection=connection
            ),
            "page_routes": repository.list_role_page_access(
                role["id"], connection=connection
            ),
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
        role_id: str,
        permissions: list[RolePermissionRequest],
        *,
        connection: Connection | None = None,
    ) -> None:
        role = repository.get_role(role_id, connection=connection)
        if role is None:
            raise KeyError(role_id)
        grants = _permission_pairs(permissions)
        _validate_permission_pairs(grants)
        requested_codes = {code for code, _ in grants}
        if role["code"].casefold() == "system_admin" and "*" not in requested_codes:
            raise ValueError("系统管理员角色必须保留通配权限")
        repository.replace_role_permissions(
            role_id, grants, connection=connection
        )

    def authenticated_response(
        user_id: str,
        request: Request,
        *,
        status_code: int,
        update_last_login: bool,
        audit_action: str | None = None,
        connection: Connection | None = None,
    ) -> JSONResponse:
        if connection is None:
            with repository.transaction() as atomic_connection:
                return authenticated_response(
                    user_id,
                    request,
                    status_code=status_code,
                    update_last_login=update_last_login,
                    audit_action=audit_action,
                    connection=atomic_connection,
                )
        raw_token, _ = session_service.create_session(
            user_id, request, connection=connection
        )
        context = session_service.resolve_session(raw_token, connection=connection)
        if context is None:
            raise HTTPException(status_code=500, detail="无法创建登录会话")
        principal = permission_service.principal_for_user(
            context.principal.user_id,
            context.principal.session_id,
            connection=connection,
        )
        if principal is None:
            raise HTTPException(status_code=500, detail="无法创建登录会话")
        context = SessionContext(principal=principal, csrf_token=context.csrf_token)
        if update_last_login:
            connection.execute(
                update(users_table)
                .where(users_table.c.id == user_id)
                .values(last_login_at=text("CURRENT_TIMESTAMP"))
            )
        if audit_action is not None:
            record_audit(
                actor=principal,
                action=audit_action,
                target_type="user",
                target_id=user_id,
                before=None,
                after={"username": principal.username},
                result="success",
                request=request,
                connection=connection,
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
    return "/profile"


def _read_template(name: str) -> str:
    return (TEMPLATE_ROOT / name).read_text(encoding="utf-8")
