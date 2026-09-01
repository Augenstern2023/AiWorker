# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import csv
import json
from pathlib import Path

from scripts.push_latest_rank_updates import (
    discover_today_sources,
    load_delivery_environment,
    validate_hongguo_export,
)


def test_discover_today_sources_finds_external_hongguo_and_latest_douyin(tmp_path):
    project = tmp_path / "project"
    exports = tmp_path / "exports"
    old_dy = project / "data/dy/jobs/old/rank/douyin_series_rank_20260831_101010.json"
    new_dy = project / "data/dy/jobs/new/rank/douyin_series_rank_20260901_100341.json"
    old_dy.parent.mkdir(parents=True)
    new_dy.parent.mkdir(parents=True)
    old_dy.write_text(json.dumps({"records": [{"title": "旧"}]}), encoding="utf-8")
    new_dy.write_text(json.dumps({"records": [{"title": "新"}]}), encoding="utf-8")
    hg = exports / "20260901_top10/top10_rankings.csv"
    _write_hongguo_export(hg, exports / "20260901_top10/group_audit.csv")

    sources = discover_today_sources(project, exports, "2026-09-01")

    assert sources == {"dy": new_dy, "hg": hg}


def test_validate_hongguo_export_requires_25_complete_top10_groups(tmp_path):
    export = tmp_path / "20260901_top10/top10_rankings.csv"
    audit = tmp_path / "20260901_top10/group_audit.csv"
    _write_hongguo_export(export, audit)

    assert validate_hongguo_export(export) == {"groups": 25, "rows": 250}

    rows = list(csv.DictReader(audit.open(encoding="utf-8-sig", newline="")))
    rows[-1]["Count"] = "9"
    with audit.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    try:
        validate_hongguo_export(export)
    except ValueError as exc:
        assert "25 个完整 Top10" in str(exc)
    else:
        raise AssertionError("incomplete export should be rejected")


def test_load_delivery_environment_reads_project_env(tmp_path, monkeypatch):
    monkeypatch.delenv("ZENTAO_PUSH_ENDPOINT", raising=False)
    monkeypatch.delenv("ZENTAO_PUSH_TOKEN", raising=False)
    monkeypatch.delenv("ZENTAO_PUSH_GID", raising=False)
    (tmp_path / ".env").write_text(
        "ZENTAO_PUSH_ENDPOINT=http://internal/zentao\n"
        "ZENTAO_PUSH_TOKEN=test-token\n"
        "ZENTAO_PUSH_GID=test-group\n",
        encoding="utf-8",
    )

    load_delivery_environment(tmp_path)

    assert __import__("os").environ["ZENTAO_PUSH_ENDPOINT"] == "http://internal/zentao"
    assert __import__("os").environ["ZENTAO_PUSH_TOKEN"] == "test-token"
    assert __import__("os").environ["ZENTAO_PUSH_GID"] == "test-group"


def _write_hongguo_export(export: Path, audit: Path) -> None:
    export.parent.mkdir(parents=True, exist_ok=True)
    with export.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["榜单类型", "排名", "剧名"])
        writer.writeheader()
        for group in range(25):
            for rank in range(1, 11):
                writer.writerow({"榜单类型": f"榜单{group + 1}", "排名": rank, "剧名": f"剧{group + 1}-{rank}"})
    with audit.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["榜单类型", "Count", "status"])
        writer.writeheader()
        for group in range(25):
            writer.writerow({"榜单类型": f"榜单{group + 1}", "Count": 10, "status": "ok"})
