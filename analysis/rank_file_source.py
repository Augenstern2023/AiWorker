# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from analysis.rank_models import RankSnapshot
from analysis.today_rank_service import (
    _LegacyRankEngine,
    _enrich_hongguo_csv_snapshots,
)


class RankFileSource:
    """File-backed snapshot source used by importers and compatibility tests."""

    empty_revision = hashlib.sha256(
        json.dumps([], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    def __init__(
        self,
        root: str | Path = ".",
        hongguo_exports_root: str | Path | None = None,
        cache_dir: str | Path | None = None,
    ) -> None:
        self._engine = _LegacyRankEngine(
            root=root,
            hongguo_exports_root=hongguo_exports_root,
            cache_dir=cache_dir,
        )

    @property
    def root(self) -> Path:
        return self._engine.root

    @property
    def hongguo_exports_root(self) -> Path:
        return self._engine.hongguo_exports_root

    @property
    def hongguo_raw_root(self) -> Path:
        return self._engine.hongguo_raw_root

    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        if platform not in {"dy", "hg"}:
            raise ValueError("platform must be dy or hg")
        return self._engine.list_snapshots(platform)

    def parse_path(self, platform: str, path: str | Path) -> RankSnapshot:
        source = Path(path)
        if platform == "hg" and source.name == "top10_rankings.csv":
            snapshot = self._engine._load_hongguo_csv_snapshot(source)
            if snapshot is not None:
                snapshot = _enrich_hongguo_csv_snapshots(
                    [snapshot],
                    raw_root=self.hongguo_raw_root,
                )[0]
        elif platform in {"dy", "hg"}:
            snapshot = self._engine._load_snapshot(platform, source)
        else:
            raise ValueError("platform must be dy or hg")
        if snapshot is None:
            raise ValueError(f"unable to parse {platform} rank source: {source}")
        return snapshot

    def revision(self, platform: str) -> str:
        if platform not in {"dy", "hg"}:
            raise ValueError("platform must be dy or hg")
        return self._engine._source_revision(platform)
