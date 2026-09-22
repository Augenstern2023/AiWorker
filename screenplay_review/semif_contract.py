# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Strict HTTP contracts for the isolated SemIf scoring sidecar."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SEMIF_CONTRACT_VERSION = "semif-score-v1"
SEMIF_UPSTREAM_COMMIT = "1f2dea3e25379f9dfc98cb83c324f00ab5deda37"
SEMIF_RULE_PACKAGE = "short-drama-semif-v1"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class SemIfOption(_ContractModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class SemIfCriterion(_ContractModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: list[SemIfOption] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def validate_option_ids(self) -> "SemIfCriterion":
        option_ids = [option.id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("criterion option IDs must be unique")
        return self


class SemIfScoreRequest(_ContractModel):
    schema_version: Literal["semif-score-v1"] = SEMIF_CONTRACT_VERSION
    request_id: str = Field(min_length=1)
    mode: Literal["direct", "shared"] = "shared"
    state: str | dict[str, Any] | list[Any]
    criteria: list[SemIfCriterion] = Field(min_length=1)
    state_refs: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_request(self) -> "SemIfScoreRequest":
        if not self.state:
            raise ValueError("state must be nonempty")
        criterion_ids = [criterion.id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion IDs must be unique")
        return self


class SemIfScoreRow(_ContractModel):
    id: str = Field(min_length=1)
    option_ids: list[str] = Field(min_length=2, max_length=16)
    probabilities: list[float] = Field(min_length=2, max_length=16)
    option_logits: list[float] = Field(min_length=2, max_length=16)
    input_tokens: int = Field(ge=1)
    prompt_sha256: str = Field(min_length=64, max_length=64)
    prompt_version: str = Field(min_length=1)
    model: dict[str, Any]
    readout: str = Field(min_length=1)
    probability_status: str = Field(min_length=1)
    answer_token_ids: list[int] | None = None
    allowed_token_mass: float | None = None
    full_vocab_argmax_id: int | None = None
    forward_seconds: float | None = None
    total_seconds: float | None = None

    @model_validator(mode="after")
    def validate_scores(self) -> "SemIfScoreRow":
        length = len(self.option_ids)
        if len(set(self.option_ids)) != length:
            raise ValueError("score option IDs must be unique")
        if len(self.probabilities) != length or len(self.option_logits) != length:
            raise ValueError("score arrays must align with option IDs")
        if any(not math.isfinite(value) or value < 0 for value in self.probabilities):
            raise ValueError("probabilities must be finite and non-negative")
        if any(not math.isfinite(value) for value in self.option_logits):
            raise ValueError("option logits must be finite")
        if sum(self.probabilities) <= 0:
            raise ValueError("probabilities must have positive mass")
        return self

    @property
    def selected_option_id(self) -> str:
        index = max(range(len(self.probabilities)), key=self.probabilities.__getitem__)
        return self.option_ids[index]


class SemIfScoreResponse(_ContractModel):
    schema_version: Literal["semif-score-v1"] = SEMIF_CONTRACT_VERSION
    request_id: str = Field(min_length=1)
    mode: Literal["direct", "shared"]
    upstream_commit: str = Field(min_length=40, max_length=40)
    scores: list[SemIfScoreRow] = Field(min_length=1)
    timing: dict[str, Any] = Field(default_factory=dict)


class SemIfHealth(_ContractModel):
    status: Literal["ok"]
    upstream_commit: str = Field(min_length=40, max_length=40)
    backend: str = Field(min_length=1)
    model: dict[str, Any] = Field(default_factory=dict)
