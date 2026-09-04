# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import csv
import importlib
import importlib.util
import json
from pathlib import Path


def _rank_file_source_type():
    spec = importlib.util.find_spec("analysis.rank_file_source")
    assert spec is not None, "analysis.rank_file_source must provide the file-source boundary"
    return importlib.import_module("analysis.rank_file_source").RankFileSource


def test_rank_file_source_parses_douyin_and_exposes_revision(tmp_path: Path):
    rank_path = (
        tmp_path
        / "data/dy/jobs/job-1/rank/douyin_series_rank_20260901_100000.json"
    )
    rank_path.parent.mkdir(parents=True)
    rank_path.write_text(
        json.dumps(
            {
                "captured_at": "2026-09-01 10:00:00",
                "records": [
                    {
                        "rank": 1,
                        "title": "集中榜单剧",
                        "series_id": "dy-1",
                        "rank_category": "短剧",
                        "tab": "榜单",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rank_file_source = _rank_file_source_type()(root=tmp_path)

    snapshots = rank_file_source.list_snapshots("dy")

    assert len(snapshots) == 1
    assert snapshots[0].source_path == rank_path
    assert snapshots[0].records[0]["title"] == "集中榜单剧"
    assert snapshots[0].records[0]["heat"] is None
    assert rank_file_source.revision("dy") != rank_file_source.empty_revision


def test_rank_file_source_parses_hongguo_csv_board_fields(tmp_path: Path):
    exports = tmp_path / "exports"
    csv_path = exports / "20260901_top10" / "top10_rankings.csv"
    csv_path.parent.mkdir(parents=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["榜单类型", "排名", "剧名", "短剧ID", "热度", "评分"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "榜单类型": "真人剧热播榜",
                "排名": "1",
                "剧名": "红果集中榜单剧",
                "短剧ID": "hg-1",
                "热度": "88.5",
                "评分": "9.4",
            }
        )
    rank_file_source = _rank_file_source_type()(
        root=tmp_path,
        hongguo_exports_root=exports,
    )

    snapshot = rank_file_source.parse_path("hg", csv_path)

    assert snapshot.records[0]["board_section"] == "真人剧"
    assert snapshot.records[0]["rank_list"] == "真人剧热播榜"
    assert snapshot.records[0]["heat"] == 88.5
    assert snapshot.records[0]["score"] == 9.4
