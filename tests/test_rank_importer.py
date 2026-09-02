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
import importlib
import importlib.util
import json
from types import SimpleNamespace
from datetime import date
from pathlib import Path

from analysis.rank_file_source import RankFileSource
from analysis.rank_models import RankWriteResult
from typer.testing import CliRunner


class RecordingRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def store_snapshot(self, snapshot, **metadata):
        self.calls.append({"snapshot": snapshot, **metadata})
        return RankWriteResult("imported", len(self.calls), len(snapshot.records))


def _rank_importer_module():
    spec = importlib.util.find_spec("analysis.rank_importer")
    assert spec is not None, "analysis.rank_importer must coordinate file and database boundaries"
    return importlib.import_module("analysis.rank_importer")


def _write_douyin(path: Path, captured_at: str = "2026-09-01 10:00:00") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "captured_at": captured_at,
                "records": [
                    {
                        "rank": 1,
                        "title": "导入榜单剧",
                        "series_id": "dy-import-1",
                        "rank_category": "短剧",
                        "tab": "榜单",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_rank_importer_uses_portable_source_ref_and_sha256(tmp_path: Path):
    module = _rank_importer_module()
    rank_path = (
        tmp_path
        / "data/dy/jobs/job-1/rank/douyin_series_rank_20260901_100000.json"
    )
    _write_douyin(rank_path)
    repository = RecordingRepository()
    importer = module.RankImporter(
        RankFileSource(root=tmp_path, hongguo_exports_root=tmp_path / "exports"),
        repository,
        project_root=tmp_path,
        hongguo_exports_root=tmp_path / "exports",
    )

    result = importer.import_path(
        "dy",
        rank_path,
        source_kind="douyin_json",
        source_root=tmp_path,
    )

    assert result.status == "imported"
    assert result.source_ref == (
        "data/dy/jobs/job-1/rank/douyin_series_rank_20260901_100000.json"
    )
    assert result.record_count == 1
    assert repository.calls[0]["source_checksum"] == hashlib.sha256(
        rank_path.read_bytes()
    ).hexdigest()
    assert repository.calls[0]["parser_version"] == 1


def test_rank_importer_batch_isolates_bad_file_and_filters_dates(tmp_path: Path):
    module = _rank_importer_module()
    included = (
        tmp_path
        / "data/dy/jobs/included/rank/douyin_series_rank_20260901_100000.json"
    )
    excluded = (
        tmp_path
        / "data/dy/jobs/excluded/rank/douyin_series_rank_20260831_100000.json"
    )
    broken = (
        tmp_path
        / "data/dy/jobs/broken/rank/douyin_series_rank_20260902_100000.json"
    )
    _write_douyin(included, "2026-09-01 10:00:00")
    _write_douyin(excluded, "2026-08-31 10:00:00")
    broken.parent.mkdir(parents=True)
    broken.write_text("not-json", encoding="utf-8")
    repository = RecordingRepository()
    importer = module.RankImporter(
        RankFileSource(root=tmp_path, hongguo_exports_root=tmp_path / "exports"),
        repository,
        project_root=tmp_path,
        hongguo_exports_root=tmp_path / "exports",
    )

    report = importer.import_all(
        platform="dy",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
    )

    assert report.discovered == 3
    assert report.imported == 1
    assert report.failed == 1
    assert report.skipped == 1
    assert report.record_count == 1
    assert [item.status for item in report.results] == [
        "failed",
        "skipped",
        "imported",
    ]
    assert "ValueError: unable to parse dy rank source" in report.results[0].error


def test_rank_import_report_is_json_serializable_without_database_url():
    module = _rank_importer_module()
    report = module.RankImportReport(
        discovered=1,
        imported=0,
        updated=0,
        skipped=0,
        failed=1,
        record_count=0,
        results=[
            module.RankImportResult(
                platform="dy",
                source_ref="broken.json",
                status="failed",
                record_count=0,
                error="榜单数据库暂不可用",
            )
        ],
    )

    payload = json.dumps(report.to_dict(), ensure_ascii=False)

    assert '"failed": 1' in payload
    assert "postgresql+psycopg://" not in payload


def test_rank_migration_cli_returns_one_and_disposes_database_on_failed_file(
    tmp_path: Path,
    monkeypatch,
):
    spec = importlib.util.find_spec("scripts.migrate_rank_files_to_postgres")
    assert spec is not None, "the historical rank migration CLI must be importable"
    cli = importlib.import_module("scripts.migrate_rank_files_to_postgres")
    module = _rank_importer_module()
    disposed: list[bool] = []

    class FakeDatabase:
        def health(self):
            return SimpleNamespace(reachable=True, schema_current=True)

        def dispose(self):
            disposed.append(True)

    class FakeImporter:
        def import_all(self, **kwargs):
            return module.RankImportReport(
                discovered=1,
                imported=0,
                updated=0,
                skipped=0,
                failed=1,
                record_count=0,
                results=[
                    module.RankImportResult(
                        platform="dy",
                        source_ref="broken.json",
                        status="failed",
                        record_count=0,
                        error="ValueError: invalid rank source",
                    )
                ],
            )

    monkeypatch.setattr(
        cli,
        "rank_importer_from_environment",
        lambda *args: (FakeImporter(), FakeDatabase()),
    )
    exports = tmp_path / "exports"
    exports.mkdir()

    result = CliRunner().invoke(
        cli.app,
        [
            "--project-root",
            str(tmp_path),
            "--hongguo-exports-root",
            str(exports),
            "--platform",
            "dy",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["failed"] == 1
    assert payload["results"][0]["source_ref"] == "broken.json"
    assert disposed == [True]
