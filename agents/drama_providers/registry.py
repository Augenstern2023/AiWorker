# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Iterable

from agents.drama_providers.base import DramaProvider
from agents.drama_providers.models import (
    DramaCandidate,
    ProviderFailure,
    ProviderSearchReport,
    ProviderSearchStatus,
)


class DramaProviderRegistry:
    def __init__(
        self, providers: Iterable[DramaProvider], *, max_workers: int = 7
    ) -> None:
        self._providers: list[DramaProvider] = []
        self._by_id: dict[str, DramaProvider] = {}
        for provider in providers:
            provider_id = str(getattr(provider, "provider_id", "")).strip().lower()
            if not provider_id:
                raise ValueError("Provider ID 不能为空")
            if provider_id in self._by_id:
                raise ValueError(f"重复的 Provider ID: {provider_id}")
            self._providers.append(provider)
            self._by_id[provider_id] = provider
        self.max_workers = max(1, int(max_workers))

    @property
    def providers(self) -> tuple[DramaProvider, ...]:
        return tuple(self._providers)

    def get(self, provider_id: str) -> DramaProvider:
        key = str(provider_id).strip().lower()
        try:
            return self._by_id[key]
        except KeyError as exc:
            raise ProviderFailure(
                "configuration_error", f"未知或未启用的剧目来源：{key or '-'}"
            ) from exc

    @staticmethod
    def _search_one(
        provider: DramaProvider, title: str, *, exact: bool
    ) -> ProviderSearchStatus:
        started = time.perf_counter()
        try:
            candidates = tuple(
                provider.search_exact(title) if exact else provider.search(title)
            )
            return ProviderSearchStatus(
                provider_id=provider.provider_id,
                display_name=provider.display_name,
                status="available",
                elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
                candidates=candidates,
            )
        except ProviderFailure as exc:
            return ProviderSearchStatus(
                provider_id=provider.provider_id,
                display_name=provider.display_name,
                status="error",
                elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
                error_code=exc.code,
                error=exc.safe_message,
            )
        except Exception:
            return ProviderSearchStatus(
                provider_id=provider.provider_id,
                display_name=provider.display_name,
                status="error",
                elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
                error_code="provider_unavailable",
                error="来源检索失败",
            )

    def _search_all(self, title: str, *, exact: bool) -> ProviderSearchReport:
        query = str(title).strip()
        if not query:
            raise ProviderFailure("series_not_found", "请输入剧名")
        if not self._providers:
            return ProviderSearchReport(candidates=(), providers=())
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(self._providers)),
            thread_name_prefix="drama-provider-search",
        ) as executor:
            futures: dict[str, Future[ProviderSearchStatus]] = {
                provider.provider_id: executor.submit(
                    self._search_one, provider, query, exact=exact
                )
                for provider in self._providers
            }
            statuses = tuple(
                futures[provider.provider_id].result()
                for provider in self._providers
            )
        candidates = tuple(
            candidate
            for status in statuses
            for candidate in status.candidates
        )
        return ProviderSearchReport(candidates=candidates, providers=statuses)

    def search_exact_all(self, title: str) -> ProviderSearchReport:
        return self._search_all(title, exact=True)

    def search_all(self, title: str) -> ProviderSearchReport:
        return self._search_all(title, exact=False)
