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
    copy_workbooks_to_share,
    discover_today_sources,
    generate_today_workbooks,
    load_delivery_environment,
    validate_hongguo_export,
    push_latest_rank_updates,
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


def test_generate_today_workbooks_builds_date_platform_filenames(tmp_path, monkeypatch):
    calls = []

    def fake_build(platform, date, **kwargs):
        path = Path(kwargs["output_dir"]) / f"{date}{'抖音榜单' if platform == 'dy' else '红果榜单'}.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"xlsx")
        calls.append((platform, date, kwargs))
        return path

    monkeypatch.setattr("scripts.push_latest_rank_updates.build_rank_workbook", fake_build)

    outputs = generate_today_workbooks(
        tmp_path,
        tmp_path / "hg-exports",
        "2026-09-02",
        {"dy": tmp_path / "dy.json", "hg": tmp_path / "hg.csv"},
    )

    assert [path.name for path in outputs.values()] == ["2026-09-02红果榜单.xlsx", "2026-09-02抖音榜单.xlsx"]
    assert [call[0] for call in calls] == ["hg", "dy"]


def test_copy_workbooks_to_share_returns_download_links(tmp_path):
    workbooks = {}
    for platform, filename in (("hg", "2026-09-02红果榜单.xlsx"), ("dy", "2026-09-02抖音榜单.xlsx")):
        path = tmp_path / "local" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(platform.encode("utf-8"))
        workbooks[platform] = path

    copied = copy_workbooks_to_share(workbooks, tmp_path / "share")

    assert [item["path"].name for item in copied.values()] == ["2026-09-02红果榜单.xlsx", "2026-09-02抖音榜单.xlsx"]
    assert copied["hg"]["path"].read_bytes() == b"hg"
    assert copied["hg"]["url"].startswith("file://")
    assert "%E7%BA%A2%E6%9E%9C%E6%A6%9C%E5%8D%95.xlsx" in copied["hg"]["url"]


def test_push_latest_rank_updates_passes_shared_links_to_delivery(tmp_path, monkeypatch):
    source = tmp_path / "data/dy/jobs/new/rank/douyin_series_rank_20260902_101010.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({"captured_at": "2026-09-02 10:10:10", "records": [_dy_source_row()]}), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.push_latest_rank_updates.generate_today_workbooks",
        lambda *args: {"dy": tmp_path / "2026-09-02抖音榜单.xlsx"},
    )
    monkeypatch.setattr(
        "scripts.push_latest_rank_updates.copy_workbooks_to_share",
        lambda *args: {"dy": {"path": Path(r"\\server\共享\2026-09-02抖音榜单.xlsx"), "url": "file://server/share/dy.xlsx"}},
    )
    calls = []
    monkeypatch.setattr(
        "scripts.push_latest_rank_updates.deliver_rank_update",
        lambda platform, path, **kwargs: calls.append((platform, path, kwargs)) or {"status": "sent"},
    )

    push_latest_rank_updates(tmp_path, tmp_path / "hg", "2026-09-02")

    assert calls[0][2]["workbook_url"] == "file://server/share/dy.xlsx"
    assert calls[0][2]["workbook_path"] == r"\\server\共享\2026-09-02抖音榜单.xlsx"


def _dy_source_row() -> dict:
    return {"rank": 1, "title": "剧", "series_id": "1", "rank_category": "短剧", "tab": "榜单"}


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
