# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProviderFailure(RuntimeError):
    """A provider error safe to expose without leaking request credentials."""

    def __init__(
        self, code: str, safe_message: str, *, retryable: bool = False
    ) -> None:
        super().__init__(safe_message)
        self.code = str(code).strip() or "provider_unavailable"
        self.safe_message = str(safe_message).strip() or "来源暂时不可用"
        self.retryable = bool(retryable)


@dataclass(frozen=True)
class DramaCandidate:
    provider_id: str
    series_ref: str
    title: str
    cover: str = ""
    summary: str = ""
    episode_count: int | str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "source_platform": self.provider_id,
            "series_ref": self.series_ref,
            "book_id": self.series_ref,
            "title": self.title,
            "cover": self.cover,
            "summary": self.summary,
            "episode_count": self.episode_count,
        }


@dataclass(frozen=True)
class DramaEpisode:
    provider_id: str
    series_ref: str
    episode_ref: str
    episode: int
    title: str
    subtitle_url: str = ""
    is_paid: bool = False
    paid_reason: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "source_platform": self.provider_id,
            "series_ref": self.series_ref,
            "book_id": self.series_ref,
            "episode_ref": self.episode_ref,
            "video_id": self.episode_ref,
            "episode": self.episode,
            "title": self.title,
            "subtitle_url": self.subtitle_url,
            "is_paid": self.is_paid,
            "paid_reason": self.paid_reason,
        }


@dataclass(frozen=True)
class PlayableMedia:
    provider_id: str
    series_ref: str
    episode_ref: str
    url: str
    subtitle_url: str = ""


@dataclass(frozen=True)
class DownloadResult:
    provider_id: str
    path: Path
    bytes: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ProviderHealth:
    provider_id: str
    display_name: str
    status: str
    message: str = ""


@dataclass(frozen=True)
class ProviderSearchStatus:
    provider_id: str
    display_name: str
    status: str
    elapsed_ms: int
    candidates: tuple[DramaCandidate, ...] = ()
    error_code: str = ""
    error: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "candidates": [item.public_dict() for item in self.candidates],
            "error_code": self.error_code,
            "error": self.error,
        }


@dataclass(frozen=True)
class ProviderSearchReport:
    candidates: tuple[DramaCandidate, ...]
    providers: tuple[ProviderSearchStatus, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "candidates": [item.public_dict() for item in self.candidates],
            "providers": [item.public_dict() for item in self.providers],
        }
