# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    topic_id: int
    breakdown_id: int
    name: str = ""
    genre: str = ""
    target_platform: str = ""
    target_audience: str = ""
    target_episode_count: int = Field(20, ge=1, le=100)
    episode_duration_seconds: int = Field(120, ge=30, le=1800)
    workflow_task_id: str | None = None
    creation_request_id: str = ""


class PlanningAssignmentRequest(BaseModel):
    assignee_user_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    target_platform: str = ""
    target_episode_count: int = Field(20, ge=1, le=100)
    creation_request_id: str = Field(min_length=1, max_length=200)


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    genre: str | None = None
    target_platform: str | None = None
    target_audience: str | None = None
    target_episode_count: int | None = Field(None, ge=1, le=100)
    episode_duration_seconds: int | None = Field(None, ge=30, le=1800)
    status: str | None = None


class BlueprintGenerateRequest(BaseModel):
    count: int = Field(3, ge=3, le=5)
    workflow_task_id: str | None = None


class BlueprintUpdateRequest(BaseModel):
    content: dict[str, Any]
    difference_note: str | None = None


class BriefLockRequest(BaseModel):
    difference_note: str = ""
    workflow_task_id: str = Field(min_length=1)


class CharacterBatchRequest(BaseModel):
    characters: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    workflow_task_id: str | None = None


class CreativeItemUpdateRequest(BaseModel):
    content: dict[str, Any]


class OutlineBatchRequest(BaseModel):
    series_arc: str = ""
    episodes: list[dict[str, Any]] = Field(default_factory=list)
    workflow_task_id: str | None = None


class EpisodeBeatsRequest(BaseModel):
    content: dict[str, Any]
    workflow_task_id: str | None = None


class WorkflowLinkRequest(BaseModel):
    workflow_task_id: str | None = None


class CreativeLockRequest(BaseModel):
    workflow_task_id: str = Field(min_length=1)


class CreativeSubmitRequest(BaseModel):
    workflow_task_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
