# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


UserStatus = Literal["active", "disabled"]
DataScope = Literal["task", "project", "managed_projects", "all"]
DATA_SCOPE_ORDER = {"task": 1, "project": 2, "managed_projects": 3, "all": 4}


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    role_codes: tuple[str, ...]
    permissions: frozenset[str]
    data_scope: str
    session_id: str

    @property
    def is_system_admin(self) -> bool:
        return "system_admin" in self.role_codes or "*" in self.permissions


class RolePermissionRequest(BaseModel):
    permission_code: str
    data_scope: DataScope = "task"


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1)
    role_ids: list[str] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    status: UserStatus | None = None
    role_ids: list[str] | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    status: UserStatus
    role_codes: list[str] = Field(default_factory=list)
    last_login_at: str | None = None
    created_at: str


class RoleCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    permissions: list[RolePermissionRequest] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    is_enabled: bool | None = None
    permissions: list[RolePermissionRequest] | None = None


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    description: str
    is_system: bool
    is_enabled: bool
    permissions: list[RolePermissionRequest] = Field(default_factory=list)
