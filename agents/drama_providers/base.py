# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from agents.drama_providers.models import (
    DownloadResult,
    DramaCandidate,
    DramaEpisode,
    PlayableMedia,
    ProviderHealth,
)


class DramaProvider(Protocol):
    provider_id: str
    display_name: str

    def health(self) -> ProviderHealth: ...

    def search(self, title: str, page: int = 1) -> list[DramaCandidate]: ...

    def search_exact(self, title: str) -> list[DramaCandidate]: ...

    def episodes(self, series_ref: str) -> list[DramaEpisode]: ...

    def resolve_playable(
        self, series_ref: str, episode_ref: str
    ) -> PlayableMedia: ...

    def download_episode(
        self,
        media: PlayableMedia,
        target: Path,
        *,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        on_progress: Callable[[int, int], None] | None = None,
        wait_if_paused: Callable[[], None] | None = None,
    ) -> DownloadResult: ...
