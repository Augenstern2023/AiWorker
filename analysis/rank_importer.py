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
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from analysis.rank_file_source import RankFileSource
from analysis.rank_repository import RankRepository
from system_database import SystemDatabase, SystemDatabaseSettings


@dataclass(frozen=True)
class RankImportResult:
    platform: str
    source_ref: str
    status: str
    record_count: int
    error: str = ""


@dataclass(frozen=True)
class RankImportReport:
    discovered: int
    imported: int
    updated: int
    skipped: int
    failed: int
    record_count: int
    results: list[RankImportResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RankImporter:
    def __init__(
        self,
        parser: RankFileSource,
        repository: RankRepository,
        project_root: str | Path,
        hongguo_exports_root: str | Path,
    ) -> None:
        self.parser = parser
        self.repository = repository
        self.project_root = Path(project_root).resolve()
        self.hongguo_exports_root = Path(hongguo_exports_root).resolve()

    def import_path(
        self,
        platform: str,
        path: str | Path,
        *,
        source_kind: str,
        source_root: str | Path,
    ) -> RankImportResult:
        source = Path(path).resolve()
        source_ref = source.relative_to(Path(source_root).resolve()).as_posix()
        snapshot = self.parser.parse_path(platform, source)
        return self._store_snapshot(
            snapshot,
            source=source,
            source_kind=source_kind,
            source_ref=source_ref,
        )

    def _store_snapshot(
        self,
        snapshot,
        *,
        source: Path,
        source_kind: str,
        source_ref: str,
    ) -> RankImportResult:
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        write = self.repository.store_snapshot(
            snapshot,
            source_kind=source_kind,
            source_ref=source_ref,
            source_checksum=checksum,
            parser_version=1,
        )
        return RankImportResult(
            platform=snapshot.platform,
            source_ref=source_ref,
            status=write.action,
            record_count=write.record_count,
        )

    def import_all(
        self,
        *,
        platform: str = "all",
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> RankImportReport:
        if platform not in {"all", "dy", "hg"}:
            raise ValueError("platform must be all, dy, or hg")
        if start_date and end_date and start_date > end_date:
            raise ValueError("start_date must not be after end_date")

        sources = self._discover_sources(platform)
        results: list[RankImportResult] = []
        for source_platform, source_kind, source_root, path in sources:
            source_ref = _source_ref(path, source_root)
            try:
                snapshot = self.parser.parse_path(source_platform, path)
                snapshot_date = snapshot.captured_at.date()
                if (
                    (start_date is not None and snapshot_date < start_date)
                    or (end_date is not None and snapshot_date > end_date)
                ):
                    results.append(
                        RankImportResult(
                            platform=source_platform,
                            source_ref=source_ref,
                            status="skipped",
                            record_count=0,
                            error="outside-date-range",
                        )
                    )
                    continue
                results.append(
                    self._store_snapshot(
                        snapshot,
                        source=path,
                        source_kind=source_kind,
                        source_ref=source_ref,
                    )
                )
            except Exception as exc:
                results.append(
                    RankImportResult(
                        platform=source_platform,
                        source_ref=source_ref,
                        status="failed",
                        record_count=0,
                        error=_safe_error(exc),
                    )
                )
        return _build_report(len(sources), results)

    def _discover_sources(
        self, platform: str
    ) -> list[tuple[str, str, Path, Path]]:
        sources: list[tuple[str, str, Path, Path]] = []
        if platform in {"all", "dy"}:
            sources.extend(
                (
                    "dy",
                    "douyin_json",
                    self.project_root,
                    path.resolve(),
                )
                for path in self.project_root.glob(
                    "data/dy/jobs/*/rank/douyin_series_rank_*.json"
                )
            )
        if platform in {"all", "hg"}:
            sources.extend(
                (
                    "hg",
                    "hongguo_csv",
                    self.hongguo_exports_root,
                    path.resolve(),
                )
                for path in self.hongguo_exports_root.glob(
                    "*/top10_rankings.csv"
                )
            )
        return sorted(sources, key=lambda item: str(item[3]).lower())


def rank_importer_from_environment(
    project_root: str | Path,
    hongguo_exports_root: str | Path,
) -> tuple[RankImporter, SystemDatabase]:
    database = SystemDatabase(SystemDatabaseSettings.from_environment())
    parser = RankFileSource(
        root=project_root,
        hongguo_exports_root=hongguo_exports_root,
    )
    return (
        RankImporter(
            parser,
            RankRepository(database),
            project_root,
            hongguo_exports_root,
        ),
        database,
    )


def _source_ref(path: Path, source_root: Path) -> str:
    return path.resolve().relative_to(source_root.resolve()).as_posix()


def _safe_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    return re.sub(
        r"postgresql(?:\+psycopg)?://[^\s@]+@",
        "postgresql://***@",
        message,
        flags=re.IGNORECASE,
    )


def _build_report(
    discovered: int,
    results: list[RankImportResult],
) -> RankImportReport:
    return RankImportReport(
        discovered=discovered,
        imported=sum(item.status == "imported" for item in results),
        updated=sum(item.status == "updated" for item in results),
        skipped=sum(item.status == "skipped" for item in results),
        failed=sum(item.status == "failed" for item in results),
        record_count=sum(
            item.record_count for item in results if item.status != "failed"
        ),
        results=results,
    )
