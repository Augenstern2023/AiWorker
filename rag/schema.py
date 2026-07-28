# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Artifact(BaseModel):
    job_id: str
    platform: str
    path: str
    source_type: str
    content_type: Literal["json", "markdown", "text"]
    data: Any
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return str(Path(self.path).as_posix())


class Drama(BaseModel):
    drama_id: str = ""
    title: str
    platform: str
    job_id: str
    category: str = ""
    description: str = ""
    rank: int | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Episode(BaseModel):
    episode_id: str = ""
    drama_id: str = ""
    title: str = ""
    index: int | None = None
    transcript: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Comment(BaseModel):
    comment_id: str = ""
    drama_id: str = ""
    content: str
    user_name: str = ""
    like_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RankSnapshot(BaseModel):
    job_id: str
    platform: str
    captured_at: str = ""
    source_path: str
    records: list[Drama] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisReport(BaseModel):
    job_id: str
    platform: str
    title: str
    source_path: str
    summary: str = ""
    transcript: str = ""
    tags: list[str] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StandardDocument(BaseModel):
    document_id: str
    job_id: str
    platform: str
    source_path: str
    source_type: str
    title: str = ""
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagChunk(BaseModel):
    chunk_id: str
    job_id: str
    platform: str
    source_path: str
    source_type: str
    source_id: str
    chunk_index: int
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding_id: str = ""
    created_at: str = Field(default_factory=utc_now_iso)


class RetrievalResult(BaseModel):
    chunk: RagChunk
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    job_id: str
    platform: str
    source_path: str
    source_type: str
    chunk_index: int
    record_id: str = ""
    title: str = ""
    rank: int | None = None
    score: float | None = None


class AnswerWithCitations(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    retrieved_chunks: list[RetrievalResult] = Field(default_factory=list)
    degraded: bool = False
    error: str = ""


class IndexResult(BaseModel):
    status: str
    job_id: str
    platform: str
    artifact_count: int = 0
    document_count: int = 0
    chunk_count: int = 0
    skipped: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    indexed_at: str = Field(default_factory=utc_now_iso)
