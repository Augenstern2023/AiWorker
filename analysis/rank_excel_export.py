# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from analysis.today_rank_service import TodayRankService


FRONTEND_COLUMNS = (
    ("rank", "排名", 8),
    ("title", "剧名", 30),
    ("category", "分类", 14),
    ("status", "状态", 12),
    ("subject", "标签", 24),
    ("description", "简介", 48),
    ("score", "评分", 12),
    ("share_count", "分享", 14),
    ("like_count", "点赞", 14),
    ("favorite_count", "收藏", 14),
    ("heat", "热度", 14),
    ("comment_count", "评论", 14),
    ("recommend_index", "推荐指数", 14),
    ("rating_count", "评分人数", 14),
    ("expectation_index", "期待指数", 14),
    ("search_index", "热搜指数", 14),
    ("max_heat", "最高热度", 14),
    ("hot_comment", "热评", 36),
    ("rank_duration", "在榜时长", 26),
    ("previous_rank", "上期排名", 12),
    ("rank_delta", "排名变化", 12),
)


def build_rank_workbook(
    platform: str,
    report_date: str,
    *,
    root: str | Path = ".",
    hongguo_exports_root: str | Path | None = None,
    output_dir: str | Path = ".runtime/rank-excel",
    rank_service: TodayRankService | None = None,
) -> Path:
    platform = platform.strip().lower()
    if platform not in {"dy", "hg"}:
        raise ValueError("platform must be dy or hg")
    service = rank_service or TodayRankService.from_environment(root=root)
    snapshots = service.list_snapshots(platform)
    selected = service._select_snapshot(snapshots, report_date)
    if selected is None:
        raise ValueError(f"未找到 {report_date} 的榜单快照：{platform}")
    previous = service._previous_snapshot(snapshots, selected)
    response = service._compare(selected, previous, limit=max(len(selected.records), len(previous.records) if previous else 0, 1))
    response["selected_snapshot"] = selected.to_dict(service.root)

    workbook = Workbook()
    workbook.remove(workbook.active)
    used_names: set[str] = set()
    for section in response.get("sections") or []:
        for ranking in section.get("rankings") or []:
            label = str(ranking.get("label") or section.get("label") or "榜单")
            if platform == "hg":
                label = f"{section.get('label') or section.get('key')}-{label}"
            sheet_name = _safe_sheet_name(label, used_names)
            _write_ranking_sheet(workbook.create_sheet(sheet_name), ranking.get("current_entries") or [])
    if not workbook.sheetnames:
        raise ValueError(f"{platform} 榜单没有可导出的子表")

    destination = Path(output_dir)
    if not destination.is_absolute():
        destination = Path(root) / destination
    destination.mkdir(parents=True, exist_ok=True)
    platform_name = "抖音榜单" if platform == "dy" else "红果榜单"
    output = destination / f"{report_date}{platform_name}.xlsx"
    workbook.save(output)
    return output


def _write_ranking_sheet(sheet, entries: list[dict[str, Any]]) -> None:
    headers = [label for _, label, _ in FRONTEND_COLUMNS]
    sheet.append(headers)
    for entry in entries:
        sheet.append([_excel_value(entry, key) for key, _, _ in FRONTEND_COLUMNS])

    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.row_dimensions[1].height = 24
    for index, (_, _, width) in enumerate(FRONTEND_COLUMNS, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _excel_value(entry: dict[str, Any], key: str) -> Any:
    if key == "category":
        return entry.get("board_type") or entry.get("category_label") or entry.get("category") or "-"
    if key == "status":
        delta = _number(entry.get("rank_delta"))
        if delta > 0:
            return f"上升 {int(delta)}"
        if delta < 0:
            return f"下降 {abs(int(delta))}"
        return "在榜" if entry.get("previous_rank") else "新上榜"
    if key == "subject":
        value = entry.get("subject")
        if value:
            return value
        tags = entry.get("topic_tags")
        return " / ".join(str(item) for item in tags) if isinstance(tags, list) else tags or "-"
    if key == "rank_duration":
        if entry.get("rank_duration"):
            return entry["rank_duration"]
        continuous = int(_number(entry.get("continuous_rank_days")))
        cumulative = int(_number(entry.get("cumulative_rank_days")))
        return f"连续在榜{continuous}天 / 累计在榜{cumulative}天" if continuous and cumulative else "-"
    value = entry.get(key)
    return "-" if value in (None, "") else value


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_sheet_name(label: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", label).strip(" '")[:31] or "榜单"
    name = base
    index = 2
    while name in used:
        suffix = f"-{index}"
        name = f"{base[:31 - len(suffix)]}{suffix}"
        index += 1
    used.add(name)
    return name
