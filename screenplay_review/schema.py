# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Stable contracts shared by the screenplay review pipeline and SemIf sidecar."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Scope = Literal["project", "episode", "scene"]
QualityStatus = Literal["passed", "needs_revision", "rejected", "manual_review"]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class ProjectContext(_ContractModel):
    project_id: str = Field(min_length=1)
    title: str = ""


class EvidenceRef(_ContractModel):
    """A short quote addressed by Unicode code-point offsets, never byte offsets."""

    source_ref: str = Field(min_length=1)
    episode_id: str | None = None
    scene_id: str | None = None
    resource_id: str | None = None
    version: int | None = Field(default=None, ge=1)
    source_url: str = ""
    block_index: int | None = Field(default=None, ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=500)
    text_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_offsets(self) -> "EvidenceRef":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class ScenePackage(_ContractModel):
    scene_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    scene_number: str = Field(min_length=1)
    sequence_number: int = Field(ge=1)
    resource_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    content_fingerprint: str = Field(min_length=1)
    text: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_slices(self) -> "ScenePackage":
        for item in self.evidence:
            if item.scene_id != self.scene_id or item.episode_id != self.episode_id:
                raise ValueError("evidence must reference its containing episode and scene")
            if self.text[item.start : item.end] != item.text:
                raise ValueError("evidence offsets must address the quoted scene text")
        return self


class EpisodePackage(_ContractModel):
    episode_id: str = Field(min_length=1)
    episode_number: int = Field(ge=1)
    resource_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    content_fingerprint: str = Field(min_length=1)
    title: str = ""
    outline: dict[str, Any] = Field(default_factory=dict)
    beats: dict[str, Any] = Field(default_factory=dict)
    scenes: list[ScenePackage] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scene_identity(self) -> "EpisodePackage":
        ids = [item.scene_id for item in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("scene_id values must be unique within an episode")
        if any(item.episode_id != self.episode_id for item in self.scenes):
            raise ValueError("scene episode_id must match its containing episode")
        return self


class InsufficientEvidence(_ContractModel):
    scope: Scope
    scope_ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ScriptPackage(_ContractModel):
    schema_version: Literal["script-package-v1"] = "script-package-v1"
    source_fingerprint: str = Field(min_length=1)
    project: ProjectContext
    brief: dict[str, Any] = Field(default_factory=dict)
    characters: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    episodes: list[EpisodePackage] = Field(min_length=1)
    insufficient_evidence: list[InsufficientEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_episode_identity(self) -> "ScriptPackage":
        ids = [item.episode_id for item in self.episodes]
        numbers = [item.episode_number for item in self.episodes]
        if len(ids) != len(set(ids)) or len(numbers) != len(set(numbers)):
            raise ValueError("episode ids and numbers must be unique")
        return self


class SemIfDecision(_ContractModel):
    dimension_id: str = Field(min_length=1)
    scope: Scope
    scope_ref: str = Field(min_length=1)
    selected_option: str = Field(min_length=1)
    option_probabilities: dict[str, float] = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    reason_code: str = ""
    request_id: str = ""

    @model_validator(mode="after")
    def validate_probabilities(self) -> "SemIfDecision":
        if any(
            not math.isfinite(value) or value < 0
            for value in self.option_probabilities.values()
        ):
            raise ValueError("option probabilities must be finite and non-negative")
        total = sum(self.option_probabilities.values())
        if not math.isfinite(total) or total <= 0:
            raise ValueError("option probabilities must have a positive sum")
        if self.selected_option not in self.option_probabilities:
            raise ValueError("selected option must be present in option probabilities")
        return self


class SemIfHealth(_ContractModel):
    status: Literal["ok"]
    model_name: str = ""
    model_revision: str = ""


class SemIfReviewRequest(_ContractModel):
    schema_version: Literal["semif-review-request-v1"] = "semif-review-request-v1"
    client_request_id: str = Field(min_length=1)
    rule_package: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
    scope: Scope
    scope_ref: str = Field(min_length=1)
    context_summary: dict[str, Any] = Field(default_factory=dict)
    content_windows: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(min_length=1)


class SemIfReviewResponse(_ContractModel):
    schema_version: Literal["semif-review-response-v1"] = "semif-review-response-v1"
    request_id: str = Field(min_length=1)
    model: dict[str, str]
    decisions: list[SemIfDecision] = Field(min_length=1)


class HardGateResult(_ContractModel):
    passed: bool
    failed_items: list[dict[str, Any]] = Field(default_factory=list)
    insufficient_items: list[dict[str, Any]] = Field(default_factory=list)


class ReviewDimension(_ContractModel):
    id: str
    label: str
    selected_option: str
    ordinal: float = Field(ge=0, le=3)
    weight: int = Field(ge=0)
    weighted_score: float = Field(ge=0)
    probabilities: dict[str, float]
    scope_refs: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ReviewReport(_ContractModel):
    schema_version: Literal["screenplay-review-report-v2"] = "screenplay-review-report-v2"
    rule_package: str
    review_version: str = "review-v001"
    quality_status: QualityStatus
    overall_score: int = Field(ge=0, le=100)
    hard_gate: HardGateResult
    dimensions: list[ReviewDimension]
    blocking_issues: list[dict[str, Any]] = Field(default_factory=list)
    revision_plan: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    engine: Literal["semif"] = "semif"
    model: dict[str, str]
    prompt_hash: str = ""
    input_snapshot_hash: str
    semif_request_ids: list[str] = Field(default_factory=list)
    semif_decisions: list[SemIfDecision] = Field(default_factory=list)
    ai_verdict: Literal["通过", "建议修改"]
    summary: str = ""
    core_findings: list[dict[str, Any]] = Field(default_factory=list)
    episode_reviews: list[dict[str, Any]] = Field(default_factory=list)
    cross_episode_findings: list[dict[str, Any]] = Field(default_factory=list)
    partial: bool = False
    checkpoint_key: str = "final_summary"
