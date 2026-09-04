# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import json
from pathlib import Path

from openpyxl import load_workbook

from analysis.rank_excel_export import build_rank_workbook
from analysis.today_rank_service import TodayRankService


EXPECTED_FRONTEND_HEADERS = [
    "排名", "剧名", "分类", "状态", "标签", "简介", "评分", "分享", "点赞", "收藏",
    "热度", "评论", "推荐指数", "评分人数", "期待指数", "热搜指数", "最高热度", "热评",
    "在榜时长", "上期排名", "排名变化",
]


def test_build_douyin_workbook_uses_one_sheet_per_frontend_ranking(tmp_path):
    _write_douyin_snapshot(tmp_path, "old", "2026-09-01", "旧榜单剧", "旧热榜剧")
    _write_douyin_snapshot(tmp_path, "new", "2026-09-02", "新榜单剧", "新热榜剧")

    output = build_rank_workbook(
        "dy",
        "2026-09-02",
        root=tmp_path,
        output_dir=tmp_path / "exports",
        rank_service=TodayRankService.from_files(root=tmp_path),
    )
    workbook = load_workbook(output, data_only=False)

    assert output.name == "2026-09-02抖音榜单.xlsx"
    assert workbook.sheetnames == ["榜单", "热榜"]
    assert [cell.value for cell in workbook["榜单"][1]] == EXPECTED_FRONTEND_HEADERS
    assert workbook["榜单"][2][1].value == "新榜单剧"
    assert workbook["热榜"][2][1].value == "新热榜剧"
    assert workbook["榜单"].freeze_panes == "A2"
    assert workbook["榜单"].auto_filter.ref


def test_build_hongguo_workbook_uses_one_sheet_per_section_and_ranking(tmp_path):
    exports = tmp_path / "hg-exports"
    _write_hongguo_csv(exports, "20260901", "昨日剧")
    _write_hongguo_csv(exports, "20260902", "今日剧")

    output = build_rank_workbook(
        "hg",
        "2026-09-02",
        root=tmp_path,
        hongguo_exports_root=exports,
        output_dir=tmp_path / "exports",
        rank_service=TodayRankService.from_files(
            root=tmp_path,
            hongguo_exports_root=exports,
        ),
    )
    workbook = load_workbook(output, data_only=False)

    assert output.name == "2026-09-02红果榜单.xlsx"
    assert workbook.sheetnames == ["全部-热播榜", "真人剧-推荐榜"]
    assert [cell.value for cell in workbook["全部-热播榜"][1]] == EXPECTED_FRONTEND_HEADERS
    assert workbook["全部-热播榜"][2][1].value == "今日剧"
    assert workbook["真人剧-推荐榜"][2][2].value == "推荐榜"


def test_build_rank_workbook_keeps_all_rows_beyond_frontend_page_limit(tmp_path):
    tag = "20260902"
    path = tmp_path / f"data/dy/jobs/new/rank/douyin_series_rank_{tag}_101010.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "captured_at": "2026-09-02 10:10:10",
                "records": [
                    {**_dy_record(f"剧{rank}", "榜单", f"series-{rank}"), "rank": rank}
                    for rank in range(1, 61)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = build_rank_workbook(
        "dy",
        "2026-09-02",
        root=tmp_path,
        output_dir=tmp_path / "exports",
        rank_service=TodayRankService.from_files(root=tmp_path),
    )
    workbook = load_workbook(output, data_only=False)

    assert workbook["榜单"].max_row == 61


def test_build_rank_workbook_accepts_an_explicit_rank_service(tmp_path):
    _write_douyin_snapshot(
        tmp_path,
        "database-fixture",
        "2026-09-02",
        "注入的榜单剧",
        "注入的热榜剧",
    )
    rank_service = TodayRankService.from_files(root=tmp_path)

    output = build_rank_workbook(
        "dy",
        "2026-09-02",
        root=tmp_path,
        output_dir=tmp_path / "exports",
        rank_service=rank_service,
    )
    workbook = load_workbook(output, data_only=False)

    assert workbook["榜单"][2][1].value == "注入的榜单剧"


def _write_douyin_snapshot(root: Path, job: str, date: str, ranking_title: str, heat_title: str) -> None:
    tag = date.replace("-", "")
    path = root / f"data/dy/jobs/{job}/rank/douyin_series_rank_{tag}_101010.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "captured_at": f"{date} 10:10:10",
                "records": [
                    _dy_record(ranking_title, "榜单", f"{job}-ranking"),
                    _dy_record(heat_title, "热榜", f"{job}-heat"),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _dy_record(title: str, tab: str, series_id: str) -> dict:
    return {
        "rank": 1,
        "title": title,
        "series_id": series_id,
        "rank_category": "短剧",
        "tab": tab,
        "heat_text": "10000",
        "like_count": "200",
        "favorite_count": "30",
        "comment_count": "4",
        "description": "简介",
    }


def _write_hongguo_csv(exports: Path, tag: str, title: str) -> None:
    path = exports / f"{tag}_top10/top10_rankings.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "榜单类型,排名,series_id,剧名,标签,简介,评分,分享,点赞,收藏,热度,推荐指数,评分人数,期待指数,热搜指数,最高热度,热评\n"
        f"红果热播榜,1,{tag}-all,{title},标签,简介,9.3,10,20,30,40,50,60,70,80,90,热评\n"
        f"真人剧推荐榜,1,{tag}-human,{title}真人版,标签,简介,9.1,11,21,31,41,51,61,71,81,91,热评\n",
        encoding="utf-8-sig",
    )
