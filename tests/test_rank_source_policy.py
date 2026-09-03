# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from analysis.rank_models import RankSnapshot
from analysis.rank_repository import RankRepository
from analysis.today_rank_service import TodayRankService


class StaticSnapshotSource:
    def __init__(
        self,
        snapshots: list[RankSnapshot],
        revision: str = "db-v1",
    ) -> None:
        self.snapshots = snapshots
        self.current_revision = revision

    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        return [item for item in self.snapshots if item.platform == platform]

    def revision(self, platform: str) -> str:
        return self.current_revision


def test_default_service_uses_postgres_repository_and_disables_disk_cache(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "AIWORKER_SYSTEM_DATABASE_URL",
        "postgresql+psycopg://rank_user:not-a-secret@127.0.0.1:55432/rank_test",
    )

    service = TodayRankService(root=tmp_path)

    assert isinstance(service.snapshot_source, RankRepository)
    assert service.disk_response_cache_enabled is False
    service._owned_database.dispose()


def test_postgres_mode_ignores_matching_local_response_cache(tmp_path):
    source = StaticSnapshotSource([_snapshot("数据库榜单")], revision="same")
    cache = tmp_path / ".runtime/rank-cache/douyin-latest-limit-10.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {
                "source_revision": "same",
                "response": {
                    "platform": "dy",
                    "date": "2026-09-03",
                    "current_entries": [{"title": "本地缓存榜单"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = TodayRankService(root=tmp_path, snapshot_source=source)
    response = service.get_today_rank("dy")

    assert response["current_entries"][0]["title"] == "数据库榜单"


def _snapshot(title: str) -> RankSnapshot:
    return RankSnapshot(
        platform="dy",
        job_id="db-job",
        source_path=Path("database/dy.json"),
        captured_at=datetime(2026, 9, 3, 10, 0),
        records=[
            {
                "id": "series-1",
                "record_id": "series-1",
                "title": title,
                "rank": 1,
                "tab": "榜单",
                "group": "短剧 / 榜单",
                "validation_warnings": [],
            }
        ],
        validation={"status": "passed", "has_warnings": False},
    )
