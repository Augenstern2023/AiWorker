# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from system_database.schema import users as users_table

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from audit.service import AuditService

from .dependencies import current_principal
from .password_service import PasswordService
from .permission_catalog import (
    DELEGABLE_PERMISSIONS,
    DELEGABLE_TASK_TYPES,
    PERMISSIONS,
    PERMISSION_DEPENDENCIES,
)
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
from .session_service import SessionService


class PasswordResetRequest(BaseModel):
    password: str


class ProjectOwnerAssignmentRequest(BaseModel):
    user_id: str


class UserRolesRequest(BaseModel):
    role_ids: list[str]


class TeamRoleCloneRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


def build_team_management_router(
    repository: IdentityRepository,
    session_service: SessionService,
    audit_service: AuditService,
    *,
    permission_service: PermissionService,
) -> APIRouter:
    password_service = PasswordService()

    def bind_permission_service(request: Request) -> None:
        request.state.identity_permission_service = permission_service

    router = APIRouter(dependencies=[Depends(bind_permission_service)])

    def required(code: str, *, write: bool = False) -> Callable:
        def dependency(
            request: Request,
            principal: Principal = Depends(current_principal),
        ) -> Principal:
            if not principal.is_system_admin and code not in principal.permissions:
                raise HTTPException(403, "没有执行此操作的权限")
            if write:
                raw_token = request.cookies.get(session_service.cookie_name)
                if not session_service.verify_csrf(
                    raw_token or "", request.headers.get("X-CSRF-Token")
                ):
                    raise HTTPException(403, "CSRF 校验失败")
            return principal

        return dependency

    team_user_read = required("identity.user.read")
    team_user_create = required("identity.user.create", write=True)
    team_user_write = required("identity.user.manage_created", write=True)
    team_role_read = required("identity.role.read")
    team_role_create = required("identity.role.create", write=True)
    team_role_write = required("identity.role.manage_created", write=True)
    admin_write = required("*", write=True)

    def creator_summary(
        user_id: str | None,
        connection: Connection | None = None,
    ) -> dict[str, str] | None:
        if not user_id:
            return None
        if connection is not None:
            row = connection.execute(
                "SELECT id, username, display_name FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        else:
            with repository.connect() as owned_connection:
                row = owned_connection.execute(
                    "SELECT id, username, display_name FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
        return dict(row) if row is not None else {"id": user_id, "display_name": "已删除账号"}

    def user_payload(user: dict[str, Any], principal: Principal) -> dict[str, Any]:
        role_codes = set(user.get("role_codes") or [])
        is_admin = "system_admin" in role_codes
        is_owner = "project_owner" in role_codes
        owns = user.get("created_by_user_id") == principal.user_id
        can_manage = principal.is_system_admin or (owns and not is_admin and not is_owner)
        return {
            **user,
            "created_by": creator_summary(user.get("created_by_user_id")),
            "is_system_admin": is_admin,
            "is_project_owner": is_owner,
            "can_edit": can_manage,
            "can_delete": can_manage and user["id"] != principal.user_id,
            "can_reset_password": can_manage,
            "can_assign": can_manage,
        }

    def role_is_assignable(
        role: dict[str, Any],
        connection: Connection | None = None,
    ) -> bool:
        if not role.get("is_enabled") or role.get("is_deleted"):
            return False
        if role["code"] in {"system_admin", "project_owner"}:
            return False
        grants = repository.list_role_permissions(role["id"], connection=connection)
        return all(
            item["permission_code"] in DELEGABLE_PERMISSIONS
            and item["data_scope"] != "all"
            for item in grants
        )

    def role_payload(
        role: dict[str, Any],
        principal: Principal,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        owns = role.get("created_by_user_id") == principal.user_id
        can_manage = principal.is_system_admin or (owns and not role["is_system"])
        return {
            **role,
            "permissions": repository.list_role_permissions(
                role["id"], connection=connection
            ),
            "claimable_task_types": repository.list_role_claimable_task_types(
                role["id"], connection=connection
            ),
            "created_by": creator_summary(
                role.get("created_by_user_id"), connection=connection
            ),
            "can_edit": can_manage,
            "can_delete": can_manage and not role["is_system"],
            "can_assign": role_is_assignable(role, connection=connection),
        }

    def require_owned_user(user_id: str, principal: Principal) -> dict[str, Any]:
        user = repository.get_user(user_id)
        if user is None:
            raise HTTPException(404, "用户不存在")
        payload = user_payload(user, principal)
        if not payload["can_edit"]:
            raise HTTPException(403, "只能管理自己创建的普通账号")
        return user

    def require_owned_role(role_id: str, principal: Principal) -> dict[str, Any]:
        role = repository.get_role(role_id)
        if role is None:
            raise HTTPException(404, "角色不存在")
        if not role_payload(role, principal)["can_edit"]:
            raise HTTPException(403, "只能管理自己创建的自定义角色")
        return role

    def validate_role_ids(role_ids: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(role_ids))
        for role_id in normalized:
            role = repository.get_role(role_id)
            if role is None:
                raise HTTPException(422, "角色不存在")
            if not role_is_assignable(role):
                raise HTTPException(422, f"角色 {role['name']} 不允许由项目负责人分配")
        return normalized

    def validate_role_definition(
        permissions: list[Any], claimable_task_types: list[str]
    ) -> list[tuple[str, str]]:
        grants = [
            (item.permission_code, item.data_scope)
            for item in permissions
        ]
        unknown = {code for code, _ in grants} - set(PERMISSIONS)
        if unknown:
            raise HTTPException(422, f"未知权限: {', '.join(sorted(unknown))}")
        expanded = repository._expanded_permissions(tuple(grants))
        forbidden = [
            code
            for code, scope in expanded
            if code not in DELEGABLE_PERMISSIONS or scope == "all"
        ]
        if forbidden:
            raise HTTPException(422, f"包含不可委派权限: {', '.join(sorted(forbidden))}")
        invalid_tasks = set(claimable_task_types) - DELEGABLE_TASK_TYPES
        if invalid_tasks:
            raise HTTPException(422, f"不可领取的任务类型: {', '.join(sorted(invalid_tasks))}")
        return list(expanded)

    def audit(
        request: Request,
        principal: Principal,
        action: str,
        target_type: str,
        target_id: str,
        before: Any,
        after: Any,
        connection: Connection,
    ) -> None:
        audit_service.record(
            actor=principal,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            reason=None,
            result="success",
            request=request,
            connection=connection,
        )

    @router.get("/api/team/users")
    def list_team_users(
        principal: Principal = Depends(team_user_read),
    ) -> list[dict[str, Any]]:
        return [user_payload(user, principal) for user in repository.list_users()]

    @router.post("/api/team/users", status_code=201)
    def create_team_user(
        payload: UserCreateRequest,
        request: Request,
        principal: Principal = Depends(team_user_create),
    ) -> dict[str, Any]:
        role_ids = validate_role_ids(payload.role_ids)
        try:
            password_hash = password_service.hash(payload.password)
            with repository.transaction() as connection:
                user = repository.create_user(
                    payload.username.strip(),
                    payload.display_name.strip(),
                    password_hash,
                    created_by_user_id=principal.user_id,
                    connection=connection,
                )
                repository.replace_user_roles(
                    user["id"], role_ids, connection=connection
                )
                created = repository.get_user(user["id"], connection=connection)
                assert created is not None
                result = user_payload(created, principal)
                audit(
                    request,
                    principal,
                    "team.user.create",
                    "user",
                    user["id"],
                    None,
                    result,
                    connection,
                )
        except IntegrityError as exc:
            raise HTTPException(409, "用户名已存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return result

    @router.patch("/api/team/users/{user_id}")
    def update_team_user(
        user_id: str,
        payload: UserUpdateRequest,
        request: Request,
        principal: Principal = Depends(team_user_write),
    ) -> dict[str, Any]:
        before = require_owned_user(user_id, principal)
        if payload.role_ids is not None and "project_owner" in before["role_codes"]:
            raise HTTPException(422, "项目负责人角色只能通过专用接任操作变更")
        role_ids = (
            validate_role_ids(payload.role_ids)
            if payload.role_ids is not None
            else None
        )
        try:
            with repository.transaction() as connection:
                updated = repository.update_user(
                    user_id,
                    display_name=(
                        payload.display_name.strip()
                        if payload.display_name is not None
                        else None
                    ),
                    status=payload.status,
                    role_ids=role_ids,
                    connection=connection,
                )
                result = user_payload(updated, principal)
                audit(
                    request,
                    principal,
                    "team.user.update",
                    "user",
                    user_id,
                    before,
                    result,
                    connection,
                )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return result

    @router.put("/api/team/users/{user_id}/roles")
    def replace_team_user_roles(
        user_id: str,
        payload: UserRolesRequest,
        request: Request,
        principal: Principal = Depends(team_user_write),
    ) -> dict[str, Any]:
        before = require_owned_user(user_id, principal)
        if "project_owner" in before["role_codes"]:
            raise HTTPException(422, "项目负责人角色只能通过专用接任操作变更")
        role_ids = validate_role_ids(payload.role_ids)
        with repository.transaction() as connection:
            repository.replace_user_roles(user_id, role_ids, connection=connection)
            updated = repository.get_user(user_id, connection=connection)
            assert updated is not None
            result = user_payload(updated, principal)
            audit(
                request,
                principal,
                "team.user.roles.replace",
                "user",
                user_id,
                {"role_codes": before["role_codes"]},
                {"role_codes": result["role_codes"]},
                connection,
            )
        return result

    @router.post("/api/team/users/{user_id}/reset-password", status_code=204)
    def reset_team_user_password(
        user_id: str,
        payload: PasswordResetRequest,
        request: Request,
        principal: Principal = Depends(team_user_write),
    ) -> Response:
        require_owned_user(user_id, principal)
        try:
            password_hash = password_service.hash(payload.password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (password_hash, user_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,),
            )
            audit(
                request,
                principal,
                "team.user.password.reset",
                "user",
                user_id,
                None,
                {"sessions_revoked": True},
                connection,
            )
        return Response(status_code=204)

    @router.delete("/api/team/users/{user_id}", status_code=204)
    def delete_team_user(
        user_id: str,
        request: Request,
        principal: Principal = Depends(team_user_write),
    ) -> Response:
        before = require_owned_user(user_id, principal)
        if user_id == principal.user_id:
            raise HTTPException(409, "不能删除当前登录账号")
        if "project_owner" in before["role_codes"] and before["status"] == "active":
            raise HTTPException(409, "请先停用当前项目负责人再删除账号")
        with repository.transaction() as connection:
            repository.soft_delete_user(
                user_id,
                deleted_by_user_id=principal.user_id,
                connection=connection,
            )
            audit(
                request,
                principal,
                "team.user.delete",
                "user",
                user_id,
                before,
                {"is_deleted": True},
                connection,
            )
        return Response(status_code=204)

    @router.get("/api/team/roles")
    def list_team_roles(
        principal: Principal = Depends(team_role_read),
    ) -> list[dict[str, Any]]:
        return [role_payload(role, principal) for role in repository.list_roles()]

    @router.get("/api/team/permissions")
    def list_team_permissions(
        _: Principal = Depends(team_role_read),
    ) -> list[dict[str, Any]]:
        return [
            {
                "code": code,
                "label": PERMISSIONS[code],
                "dependencies": sorted(PERMISSION_DEPENDENCIES.get(code, ())),
                "allowed_scopes": ["task", "project", "managed_projects"],
            }
            for code in sorted(DELEGABLE_PERMISSIONS)
        ]

    @router.post("/api/team/roles", status_code=201)
    def create_team_role(
        payload: RoleCreateRequest,
        request: Request,
        principal: Principal = Depends(team_role_create),
    ) -> dict[str, Any]:
        grants = validate_role_definition(
            payload.permissions, payload.claimable_task_types
        )
        try:
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
                result = role_payload(role, principal, connection=connection)
                audit(
                    request,
                    principal,
                    "team.role.create",
                    "role",
                    role["id"],
                    None,
                    result,
                    connection,
                )
        except IntegrityError as exc:
            raise HTTPException(409, "角色编码已存在") from exc
        return result

    @router.patch("/api/team/roles/{role_id}")
    def update_team_role(
        role_id: str,
        payload: RoleUpdateRequest,
        request: Request,
        principal: Principal = Depends(team_role_write),
    ) -> dict[str, Any]:
        role = require_owned_role(role_id, principal)
        before = role_payload(role, principal)
        grants = None
        if payload.permissions is not None:
            grants = validate_role_definition(
                payload.permissions, payload.claimable_task_types or []
            )
        if payload.claimable_task_types is not None:
            invalid = set(payload.claimable_task_types) - DELEGABLE_TASK_TYPES
            if invalid:
                raise HTTPException(422, "包含不可领取的任务类型")
        with repository.transaction() as connection:
            if grants is not None:
                repository.replace_role_permissions(
                    role_id, grants, connection=connection
                )
            if payload.claimable_task_types is not None:
                repository.replace_role_claimable_task_types(
                    role_id, payload.claimable_task_types, connection=connection
                )
            updated = repository.update_role(
                role_id,
                name=payload.name.strip() if payload.name is not None else None,
                description=payload.description,
                is_enabled=payload.is_enabled,
                connection=connection,
            )
            result = role_payload(updated, principal, connection=connection)
            audit(
                request,
                principal,
                "team.role.update",
                "role",
                role_id,
                before,
                result,
                connection,
            )
        return result

    @router.delete("/api/team/roles/{role_id}", status_code=204)
    def delete_team_role(
        role_id: str,
        request: Request,
        principal: Principal = Depends(team_role_write),
    ) -> Response:
        role = require_owned_role(role_id, principal)
        before = role_payload(role, principal)
        with repository.transaction() as connection:
            repository.soft_delete_role(
                role_id,
                deleted_by_user_id=principal.user_id,
                connection=connection,
            )
            audit(
                request,
                principal,
                "team.role.delete",
                "role",
                role_id,
                before,
                {"is_deleted": True},
                connection,
            )
        return Response(status_code=204)

    @router.post("/api/team/roles/{role_id}/clone", status_code=201)
    def clone_team_role(
        role_id: str,
        payload: TeamRoleCloneRequest,
        request: Request,
        principal: Principal = Depends(team_role_create),
    ) -> dict[str, Any]:
        source = repository.get_role(role_id)
        if source is None:
            raise HTTPException(404, "角色不存在")
        if not role_is_assignable(source):
            raise HTTPException(422, "该角色包含不可委派权限，不能克隆")
        source_permissions = repository.list_role_permissions(role_id)
        source_tasks = repository.list_role_claimable_task_types(role_id)
        grants = validate_role_definition(
            [
                RolePermissionRequest(
                    permission_code=item["permission_code"],
                    data_scope=item["data_scope"],
                )
                for item in source_permissions
            ],
            source_tasks,
        )
        try:
            with repository.transaction() as connection:
                role = repository.create_role(
                    payload.code,
                    payload.name.strip(),
                    source["description"] if payload.description is None else payload.description,
                    created_by_user_id=principal.user_id,
                    connection=connection,
                )
                repository.replace_role_permissions(
                    role["id"], grants, connection=connection
                )
                repository.replace_role_claimable_task_types(
                    role["id"], source_tasks, connection=connection
                )
                result = role_payload(role, principal, connection=connection)
                audit(
                    request,
                    principal,
                    "team.role.clone",
                    "role",
                    role["id"],
                    {"source_role_id": role_id},
                    result,
                    connection,
                )
        except IntegrityError as exc:
            raise HTTPException(409, "角色编码已存在") from exc
        return result

    @router.post("/api/admin/project-owner")
    def assign_project_owner(
        payload: ProjectOwnerAssignmentRequest,
        request: Request,
        principal: Principal = Depends(admin_write),
    ) -> dict[str, Any]:
        before = next(
            (
                user
                for user in repository.list_users()
                if "project_owner" in user["role_codes"]
            ),
            None,
        )
        try:
            with repository.transaction() as connection:
                assigned = repository.assign_project_owner(
                    payload.user_id, connection=connection
                )
                result = user_payload(assigned, principal)
                audit(
                    request,
                    principal,
                    "project_owner.assign",
                    "user",
                    payload.user_id,
                    before,
                    result,
                    connection,
                )
        except KeyError as exc:
            raise HTTPException(404, "用户不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return result

    return router
