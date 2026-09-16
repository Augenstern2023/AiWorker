# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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
    workflow_mode: Literal["sequential", "free"] = "sequential"
    seed_text: str = Field(default="", max_length=500)


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
    workflow_mode: Literal["sequential", "free"] = "sequential"


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
    draft_brief_id: int | None = Field(None, ge=1)
    expected_project_revision: int | None = Field(None, ge=0)


class BlueprintGenerateRequest(BaseModel):
    count: int = Field(3, ge=3, le=5)
    preserve_blueprint_ids: list[int] = Field(default_factory=list, max_length=5)
    workflow_task_id: str | None = None
    llm_model_record_id: str | None = Field(None, min_length=1, max_length=64)
    operation_id: str | None = Field(None, max_length=200)
    generation_instruction: str = Field("", max_length=2000)
    source_snapshot_id: str | None = Field(None, max_length=200)
    source_snapshot: dict[str, Any] | None = None
    prompt_template_key: str | None = Field(None, min_length=1, max_length=120)
    prompt_template_revision: int | None = Field(None, ge=1)
    prompt_override: dict[str, Any] = Field(default_factory=dict)


class BlueprintCreateRequest(BaseModel):
    content: dict[str, Any]
    workflow_task_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1, max_length=200)
    replaces_resource_id: str | None = Field(None, max_length=200)
    expected_project_revision: int | None = Field(None, ge=0)


class BlueprintDeriveRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    workflow_task_id: str = Field(min_length=1)
    llm_model_record_id: str | None = Field(None, min_length=1, max_length=64)
    operation_id: str | None = Field(None, max_length=200)
    source_snapshot_id: str | None = Field(None, max_length=200)
    source_snapshot: dict[str, Any] | None = None
    prompt_template_key: str | None = Field(None, min_length=1, max_length=120)
    prompt_template_revision: int | None = Field(None, ge=1)
    prompt_override: dict[str, Any] = Field(default_factory=dict)


class BlueprintUpdateRequest(BaseModel):
    content: dict[str, Any]
    difference_note: str | None = None
    operation_id: str | None = Field(None, max_length=200)


class BriefLockRequest(BaseModel):
    difference_note: str = ""
    workflow_task_id: str = Field(min_length=1)
    operation_id: str | None = Field(None, max_length=200)
    expected_project_revision: int | None = Field(None, ge=0)


class CharacterBatchRequest(BaseModel):
    characters: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    replaces_resource_id: str | None = Field(None, max_length=200)
    expected_project_revision: int | None = Field(None, ge=0)
    workflow_task_id: str | None = None
    operation_id: str | None = Field(None, max_length=200)


class CreativeItemUpdateRequest(BaseModel):
    content: dict[str, Any]
    operation_id: str | None = Field(None, max_length=200)


class OutlineBatchRequest(BaseModel):
    series_arc: str = ""
    replaces_resource_id: str | None = Field(None, max_length=200)
    expected_project_revision: int | None = Field(None, ge=0)
    episodes: list[dict[str, Any]] = Field(default_factory=list)
    workflow_task_id: str | None = None
    operation_id: str | None = Field(None, max_length=200)


class OutlineOverviewRequest(BaseModel):
    series_overview: dict[str, Any] = Field(default_factory=dict)
    operation_id: str | None = Field(None, max_length=200)


class OutlineFieldRewriteRequest(BaseModel):
    instruction: str = Field("", max_length=2000)


class OutlineFieldRewriteApplyRequest(BaseModel):
    candidate_value: str = Field(min_length=1, max_length=6000)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    expected_project_revision: int = Field(ge=0)
    expected_head_revision: int | None = Field(None, ge=0)
    operation_id: str | None = Field(None, max_length=200)

    @field_validator("candidate_value")
    @classmethod
    def candidate_value_must_contain_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("candidate_value 不能为空")
        return normalized


class EpisodeBeatsRequest(BaseModel):
    content: dict[str, Any]
    replaces_resource_id: str | None = Field(None, max_length=200)
    expected_project_revision: int | None = Field(None, ge=0)
    workflow_task_id: str | None = None
    operation_id: str | None = Field(None, max_length=200)


class WorkflowLinkRequest(BaseModel):
    workflow_task_id: str | None = None
    llm_model_record_id: str | None = Field(None, min_length=1, max_length=64)
    operation_id: str | None = Field(None, max_length=200)
    generation_instruction: str = Field("", max_length=2000)
    source_snapshot_id: str | None = Field(None, max_length=200)
    source_snapshot: dict[str, Any] | None = None
    expected_project_revision: int | None = Field(None, ge=0)
    prompt_template_key: str | None = Field(None, min_length=1, max_length=120)
    prompt_template_revision: int | None = Field(None, ge=1)
    prompt_override: dict[str, Any] = Field(default_factory=dict)


class PromptProjectEnableRequest(BaseModel):
    template_key: str = Field("classic-compatible", min_length=1, max_length=120)


class PromptBindingRequest(BaseModel):
    template_key: str = Field(min_length=1, max_length=120)
    template_revision: int | None = Field(None, ge=1)
    episode_number: int = Field(0, ge=0, le=100)
    override: dict[str, Any] = Field(default_factory=dict)


class PromptTemplateCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=500)
    owner_scope: Literal["user", "project"] = "user"
    content: dict[str, Any]


class PromptTemplateRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    content: dict[str, Any]


class PromptMarketplaceSubmissionRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=200)
    expected_template_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    attribution_name: str = Field(min_length=1, max_length=80)
    license_code: Literal[
        "CC-BY-4.0",
        "CC-BY-NC-4.0",
        "CC0-1.0",
    ]
    source_type: Literal[
        "original", "authorized", "open_license", "public_domain"
    ]
    source_url: str = Field("", max_length=1000)
    rights_statement: str = Field(min_length=10, max_length=2000)
    publication_confirmed: bool = False
    rights_confirmed: bool = False


class PromptMarketplaceReviewRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=200)
    action: Literal["approve", "reject", "remove", "withdraw"]
    expected_review_revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=2000)


class PromptMarketplaceInstallRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=200)
    license_accepted: bool = False


class PromptMarketplaceRatingRequest(BaseModel):
    score: int = Field(ge=1, le=5)
    review_text: str = Field("", max_length=1000)
    expected_revision: int = Field(0, ge=0)


class PromptGovernanceActionRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=200)
    action: Literal[
        "request_review", "approve", "deprecate", "archive", "restore"
    ]
    expected_governance_revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=2000)


class PromptImportPreviewRequest(BaseModel):
    source_text: str = Field(min_length=1, max_length=50_000)
    target_stage: Literal[
        "brief",
        "characters",
        "outline",
        "beats",
        "script",
        "review",
        "optimization",
    ] | None = None


class CreativeLockRequest(BaseModel):
    workflow_task_id: str = Field(min_length=1)
    operation_id: str | None = Field(None, max_length=200)


class CreativeModeChangeRequest(BaseModel):
    workflow_mode: Literal["sequential", "free"]
    expected_mode_revision: int = Field(ge=0)
    seed_text: str = Field(default="", max_length=500)
    operation_id: str | None = Field(None, max_length=200)


class CreativeModeMigrationRequest(BaseModel):
    expected_mode_revision: int = Field(ge=0)
    seed_text: str = Field(default="", max_length=500)
    operation_id: str = Field(min_length=1, max_length=200)


class FreeCreativeContextRefs(BaseModel):
    brief_id: int | None = Field(None, ge=1)
    character_batch_id: str | None = Field(None, max_length=200)
    outline_batch_id: str | None = Field(None, max_length=200)
    beats_id: int | None = Field(None, ge=1)


class FreeCreativeGenerateRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=200)
    expected_head_revision: int = Field(0, ge=0)
    count: int = Field(1, ge=1, le=5)
    llm_model_record_id: str | None = Field(None, min_length=1, max_length=64)
    generation_instruction: str = Field("", max_length=2000)
    context_refs: FreeCreativeContextRefs = Field(default_factory=FreeCreativeContextRefs)
    prompt_template_key: str | None = Field(None, min_length=1, max_length=120)
    prompt_template_revision: int | None = Field(None, ge=1)
    prompt_override: dict[str, Any] = Field(default_factory=dict)


class FreeCreativeSeriesGenerateRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=200)
    expected_head_revisions: dict[int, int]
    llm_model_record_id: str | None = Field(None, min_length=1, max_length=64)
    generation_instruction: str = Field("", max_length=2000)
    context_refs: FreeCreativeContextRefs = Field(default_factory=FreeCreativeContextRefs)
    prompt_template_key: str | None = Field(None, min_length=1, max_length=120)
    prompt_template_revision: int | None = Field(None, ge=1)
    prompt_override: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_head_revisions")
    @classmethod
    def validate_expected_head_revisions(
        cls, value: dict[int, int]
    ) -> dict[int, int]:
        if not value:
            raise ValueError("expected_head_revisions is required")
        if any(number < 1 or number > 100 for number in value):
            raise ValueError("episode number must be between 1 and 100")
        if any(revision < 0 for revision in value.values()):
            raise ValueError("head revision must be non-negative")
        return value


class FreeCreativeSeriesLockRequest(BaseModel):
    expected_head_revisions: dict[int, int]

    @field_validator("expected_head_revisions")
    @classmethod
    def validate_expected_head_revisions(
        cls, value: dict[int, int]
    ) -> dict[int, int]:
        if not value:
            raise ValueError("expected_head_revisions is required")
        if any(number < 1 or number > 100 for number in value):
            raise ValueError("episode number must be between 1 and 100")
        if any(revision < 0 for revision in value.values()):
            raise ValueError("head revision must be non-negative")
        return value


class FreeBriefCreateRequest(BaseModel):
    content: dict[str, Any] = Field(default_factory=dict)
    expected_head_revision: int = Field(0, ge=0)


class FreeCharacterBatchCreateRequest(BaseModel):
    characters: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    expected_head_revision: int = Field(0, ge=0)


class FreeOutlineCreateRequest(BaseModel):
    series_arc: str = ""
    series_overview: dict[str, Any] | None = None
    episodes: list[dict[str, Any]] = Field(default_factory=list)
    expected_head_revision: int = Field(0, ge=0)


class FreeEpisodeCreateRequest(BaseModel):
    content: dict[str, Any] = Field(default_factory=dict)
    expected_head_revision: int = Field(0, ge=0)


class FreeCreativeHeadRequest(BaseModel):
    resource_ref: str = Field(min_length=1, max_length=200)
    expected_head_revision: int = Field(ge=0)


class FreeCreativeLockRequest(BaseModel):
    expected_head_revision: int = Field(ge=0)
    operation_id: str | None = Field(None, max_length=200)


class CreativeSubmitRequest(BaseModel):
    workflow_task_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    operation_id: str | None = Field(None, max_length=200)
