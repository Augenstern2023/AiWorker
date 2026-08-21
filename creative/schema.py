# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from typing import Any, Literal

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


class MarketReferenceRequest(BaseModel):
    resource_type: Literal["topic", "breakdown"]
    resource_id: int = Field(ge=1)


class OriginalProjectCreateRequest(BaseModel):
    seed_text: str = Field(min_length=2, max_length=500)
    name: str = Field(default="", max_length=200)
    genre: str = Field(default="", max_length=100)
    target_platform: str = Field(default="", max_length=30)
    target_audience: str = Field(default="", max_length=500)
    target_episode_count: int = Field(20, ge=1, le=100)
    episode_duration_seconds: int = Field(120, ge=30, le=1800)
    cost_level: str = Field(default="", max_length=30)
    content_boundaries: list[str] = Field(default_factory=list, max_length=20)
    reference_refs: list[MarketReferenceRequest] = Field(default_factory=list, max_length=10)
    creation_request_id: str = Field(min_length=1, max_length=200)


class SeedUpdateRequest(BaseModel):
    seed_text: str | None = Field(None, min_length=2, max_length=500)
    constraints: dict[str, Any] | None = None
    reference_refs: list[MarketReferenceRequest] | None = Field(None, max_length=10)


class SeedVersionRequest(BaseModel):
    seed_text: str = Field(min_length=2, max_length=500)
    constraints: dict[str, Any] = Field(default_factory=dict)
    reference_refs: list[MarketReferenceRequest] = Field(default_factory=list, max_length=10)


class PlanningAssignmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_platform: str = ""
    target_episode_count: int = Field(20, ge=1, le=100)
    planner_member_ids: list[str] = Field(default_factory=list)
    writer_member_ids: list[str] = Field(default_factory=list)
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
    preserve_blueprint_ids: list[int] = Field(default_factory=list, max_length=5)
    workflow_task_id: str | None = None
    operation_id: str | None = Field(None, max_length=200)
    generation_instruction: str = Field("", max_length=2000)
    source_snapshot_id: str | None = Field(None, max_length=200)
    source_snapshot: dict[str, Any] | None = None


class BlueprintCreateRequest(BaseModel):
    content: dict[str, Any]
    workflow_task_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1, max_length=200)


class BlueprintDeriveRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    workflow_task_id: str = Field(min_length=1)
    operation_id: str | None = Field(None, max_length=200)
    source_snapshot_id: str | None = Field(None, max_length=200)
    source_snapshot: dict[str, Any] | None = None


class BlueprintUpdateRequest(BaseModel):
    content: dict[str, Any]
    difference_note: str | None = None
    operation_id: str | None = Field(None, max_length=200)


class BriefLockRequest(BaseModel):
    difference_note: str = ""
    workflow_task_id: str = Field(min_length=1)
    operation_id: str | None = Field(None, max_length=200)


class CharacterBatchRequest(BaseModel):
    characters: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    workflow_task_id: str | None = None
    operation_id: str | None = Field(None, max_length=200)


class CreativeItemUpdateRequest(BaseModel):
    content: dict[str, Any]
    operation_id: str | None = Field(None, max_length=200)


class OutlineBatchRequest(BaseModel):
    series_arc: str = ""
    episodes: list[dict[str, Any]] = Field(default_factory=list)
    workflow_task_id: str | None = None
    operation_id: str | None = Field(None, max_length=200)


class OutlineOverviewRequest(BaseModel):
    series_overview: dict[str, Any] = Field(default_factory=dict)
    operation_id: str | None = Field(None, max_length=200)


class EpisodeBeatsRequest(BaseModel):
    content: dict[str, Any]
    workflow_task_id: str | None = None
    operation_id: str | None = Field(None, max_length=200)


class WorkflowLinkRequest(BaseModel):
    workflow_task_id: str | None = None
    operation_id: str | None = Field(None, max_length=200)
    generation_instruction: str = Field("", max_length=2000)
    source_snapshot_id: str | None = Field(None, max_length=200)
    source_snapshot: dict[str, Any] | None = None


class CreativeLockRequest(BaseModel):
    workflow_task_id: str = Field(min_length=1)
    operation_id: str | None = Field(None, max_length=200)


class CreativeSubmitRequest(BaseModel):
    workflow_task_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    operation_id: str | None = Field(None, max_length=200)
