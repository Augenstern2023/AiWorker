# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from identity.schema import Principal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ActionType = Literal[
    "project.create_original", "project.link", "creative.run_plan", "creative.lock"
]
Stage = Literal["brief", "characters", "outline", "beats", "script"]
_SERVER_TRUSTED_PAYLOAD_FIELDS = frozenset({
    "actor",
    "actor_user_id",
    "expected_context",
    "idempotency_key",
    "permission",
    "permission_result",
    "permissions",
    "principal",
    "request_hash",
    "revision",
    "source_snapshot_id",
    "status",
    "target_key",
})


def _payload_contains_server_trusted_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key) in _SERVER_TRUSTED_PAYLOAD_FIELDS
            or _payload_contains_server_trusted_field(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_payload_contains_server_trusted_field(item) for item in value)
    return False


class _WriterActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReferenceRef(_WriterActionModel):
    resource_type: Literal["topic", "breakdown"]
    resource_id: int = Field(ge=1)


class OriginalProjectActionPayload(BaseModel):
    """The complete, user-visible inputs needed to create an original project."""

    model_config = ConfigDict(extra="ignore")

    seed_text: str = Field(min_length=2, max_length=500)
    name: str = Field(min_length=1, max_length=200)
    genre: str = Field(min_length=1, max_length=100)
    target_platform: str = Field(min_length=1, max_length=30)
    target_audience: str = Field(min_length=1, max_length=500)
    target_episode_count: int = Field(ge=1, le=100)
    episode_duration_seconds: int = Field(ge=30, le=1800)
    cost_level: str = Field(min_length=1, max_length=30)
    content_boundaries: list[str] = Field(max_length=20)
    reference_refs: list[ReferenceRef] = Field(default_factory=list, max_length=10)
    assumptions: list[str] = Field(max_length=20)


class ProposedAction(_WriterActionModel):
    """A model-suggested action, deliberately excluding server-trusted fields."""

    action_type: ActionType
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=1000)
    source_card_id: str | None = None
    project_id: int | None = None
    target_stage: Stage | None = None
    episode_start: int | None = Field(None, ge=1, le=100)
    episode_end: int | None = Field(None, ge=1, le=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_action_scope(self) -> "ProposedAction":
        if _payload_contains_server_trusted_field(self.payload):
            raise ValueError("动作负载不得包含服务端可信字段")
        if self.action_type == "project.create_original" and not (
            self.source_card_id or self.payload
        ):
            raise ValueError("创建原创项目必须引用创作卡片或提供创建资料")
        if self.action_type == "project.link" and self.project_id is None:
            raise ValueError("关联项目必须提供 project_id")
        if self.action_type == "creative.run_plan":
            if self.target_stage is None:
                raise ValueError("创作计划必须提供 target_stage")
            if self.target_stage in {"beats", "script"}:
                if self.episode_start is None or self.episode_end is None:
                    raise ValueError("分集创作必须同时提供起止集数")
                if self.episode_start > self.episode_end:
                    raise ValueError("起始集数不能大于结束集数")
        if self.action_type == "creative.lock":
            resource_id = self.payload.get("resource_id")
            resource_ids = self.payload.get("resource_ids")
            if self.project_id is None or self.target_stage is None or not (
                resource_id or resource_ids
            ):
                raise ValueError("锁定创作成果必须提供项目、阶段和资源 ID")
            if resource_id and resource_ids:
                raise ValueError("锁定创作成果只能使用一种资源 ID 形式")
            if resource_ids and (
                not isinstance(resource_ids, list) or len(resource_ids) != 1
            ):
                raise ValueError("独立锁定动作一次只能锁定一个成果")
            if self.target_stage in {"beats", "script"} and (
                self.episode_start is None
                or self.episode_end is None
                or self.episode_start != self.episode_end
            ):
                raise ValueError("分集锁定动作必须且只能指定一集")
        return self


DifferenceDimension = Literal[
    "protagonist", "background", "conflict", "relationship", "emotional_payoff"
]


class DirectionCandidate(_WriterActionModel):
    title: str
    protagonist: str
    background: str
    core_conflict: str
    relationship_engine: str
    emotional_payoff: str
    difference_dimensions: list[DifferenceDimension] = Field(min_length=2)


class DirectionCard(_WriterActionModel):
    card_id: str
    card_type: Literal["directions"] = "directions"
    items: list[DirectionCandidate] = Field(min_length=3, max_length=5)


class BriefCard(_WriterActionModel):
    card_id: str
    card_type: Literal["brief"] = "brief"
    content: dict[str, Any]
    assumptions: list[str] = Field(default_factory=list)


class CreativePlanCard(_WriterActionModel):
    card_id: str
    card_type: Literal["plan"] = "plan"
    title: str
    summary: str
    steps: list[dict[str, Any]]


class ProgressCard(_WriterActionModel):
    card_id: str
    card_type: Literal["progress"] = "progress"
    action_id: str
    status: str
    completed_steps: int
    total_steps: int


class ResultCard(_WriterActionModel):
    card_id: str
    card_type: Literal["result"] = "result"
    project_id: int
    resource_refs: list[dict[str, Any]]
    open_url: str

    @field_validator("open_url")
    @classmethod
    def validate_local_project_url(cls, value: str) -> str:
        if not re.fullmatch(
            r"/(?:planning/projects/[1-9]\d*/(?:brief|characters)|"
            r"writing/projects(?:/[1-9]\d*/(?:outlines|"
            r"episodes/[1-9]\d*/(?:beats|scripts)))?)",
            value,
        ):
            raise ValueError("成果链接必须是服务端项目路由")
        return value


class ConflictCard(_WriterActionModel):
    card_id: str
    card_type: Literal["conflict"] = "conflict"
    code: str
    message: str
    current_revision: int | None = None


CreativeCard = Annotated[
    DirectionCard | BriefCard | CreativePlanCard | ProgressCard | ResultCard | ConflictCard,
    Field(discriminator="card_type"),
]


class ActionCommand(_WriterActionModel):
    session_id: int
    action_id: str
    decision: Literal["confirm", "cancel"]
    actor: Principal
    expected_revision: int
    client_request_id: str


class PlanStep(_WriterActionModel):
    """One durable, independently executable creative-plan operation."""

    step_key: str
    stage: Stage
    kind: Literal["generate", "persist", "lock"]
    episode_number: int | None = Field(default=None, ge=1)
    depends_on: list[str] = Field(default_factory=list)
    expected_context: dict[str, Any]
    replaces_resource_id: str | None = None
    invalidates_downstream: bool = False


class CreativePlan(_WriterActionModel):
    """The read-only dependency plan shown to a writer before execution."""

    project_id: int | None
    target_stage: Stage
    episode_start: int | None = Field(default=None, ge=1)
    episode_end: int | None = Field(default=None, ge=1)
    source_snapshot_id: str
    assumptions: list[str]
    reused_resources: list[dict[str, Any]] = Field(default_factory=list)
    requires_selection: bool = False
    selection_stage: Stage | None = None
    steps: list[PlanStep]


class WriterActionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        conflicting_action_id: str | None = None,
        current_revision: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.retryable = retryable
        self.conflicting_action_id = conflicting_action_id
        self.current_revision = current_revision
