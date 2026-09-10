# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal[
    "pool",
    "assigned",
    "in_progress",

    "completed",
    "cancelled",
]
SubmissionStatus = Literal[
    "draft",
    "submitted",
    "approved",
    "returned",
    "superseded",
]
ProjectMemberStatus = Literal["active", "inactive"]


class TaskAssignmentRequest(BaseModel):
    assignee_user_id: str = Field(min_length=1)


class ProjectMemberCreateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    project_role: str = Field(min_length=1, max_length=128)
    status: ProjectMemberStatus = "active"


class SubmissionCreateRequest(BaseModel):
    resource_type: str = Field(min_length=1, max_length=128)
    resource_id: str = Field(min_length=1, max_length=256)
    content: Any


class SubmissionReturnRequest(BaseModel):
    review_comment: str = Field(min_length=1)
