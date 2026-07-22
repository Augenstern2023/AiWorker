# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


TaskStatus = Literal[
    "pool",
    "assigned",
    "in_progress",
    "waiting_review",
    "returned",
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
