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
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


SUPPORTED_PLATFORMS = {"dy", "hg"}
RANK_PATTERNS = {
    "dy": "data/dy/jobs/*/rank/douyin_series_rank_*.json",
    "hg": "data/hg/jobs/*/rank/hongguo*.json",
}
DEFAULT_HONGGUO_EXPORTS_ROOT = Path(os.environ.get("HONGGUO_EXPORTS_DIR", r"E:\hg\exports"))
HONGGUO_SECTION_ORDER = ["全部", "真人剧", "漫剧", "AI剧"]
HONGGUO_RANKING_ORDER = ["热播榜", "推荐榜", "必看榜", "热搜榜", "收藏榜", "新剧榜", "预约榜", "高分臻果榜"]
DOUYIN_RANKING_ORDER = ["榜单", "热榜"]
logger = logging.getLogger("aiworker.web.today_rank")

RANK_COMMON_COLUMNS = (
    {"key": "rank", "label": "排名", "kind": "rank", "width": 55},
    {"key": "title", "label": "剧名", "kind": "title", "width": 220},
    {"key": "category", "label": "分类", "kind": "category", "width": 100},
    {"key": "status", "label": "状态", "kind": "status", "width": 100},
)
RANK_DATA_COLUMNS = (
    {"key": "subject", "label": "标签", "kind": "text", "width": 150},
    {"key": "description", "label": "简介", "kind": "text", "width": 260},
    {"key": "score", "label": "评分", "kind": "metric", "width": 90},
    {"key": "share_count", "label": "分享", "kind": "metric", "width": 90},
    {"key": "like_count", "label": "点赞", "kind": "metric", "width": 95, "icon": "bi-hand-thumbs-up"},
    {"key": "favorite_count", "label": "收藏", "kind": "metric", "width": 95, "icon": "bi-star"},
    {"key": "heat", "label": "热度", "kind": "metric", "width": 95, "icon": "bi-fire"},
    {"key": "comment_count", "label": "评论", "kind": "metric", "width": 95, "icon": "bi-chat-dots"},
    {"key": "recommend_index", "label": "推荐指数", "kind": "metric", "width": 105},
    {"key": "rating_count", "label": "评分人数", "kind": "metric", "width": 105},
    {"key": "expectation_index", "label": "期待指数", "kind": "metric", "width": 105},
    {"key": "search_index", "label": "热搜指数", "kind": "metric", "width": 105},
    {"key": "max_heat", "label": "最高热度", "kind": "metric", "width": 105},
    {"key": "hot_comment", "label": "热评", "kind": "text", "width": 240},
    {"key": "rank_duration", "label": "在榜时长", "kind": "rank_duration", "width": 210},
)
RANK_CHANGE_COLUMNS = (
    {"key": "previous_rank", "label": "上期排名", "kind": "metric", "width": 90},
    {"key": "rank_delta", "label": "排名变化", "kind": "rank_delta", "width": 105},
)


@dataclass(frozen=True)
class RankSnapshot:
    platform: str
    job_id: str
    source_path: Path
    captured_at: datetime
    records: list[dict[str, Any]]
    validation: dict[str, Any]

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "job_id": self.job_id,
            "source_path": _relative_path(self.source_path, root),
            "captured_at": self.captured_at.strftime("%Y-%m-%d %H:%M:%S"),
            "date": self.captured_at.date().isoformat(),
            "record_count": len(self.records),
        }


class TodayRankService:
    def __init__(self, root: str | Path = ".", hongguo_exports_root: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        if hongguo_exports_root is not None:
            self.hongguo_exports_root = Path(hongguo_exports_root)
        elif self.root == Path.cwd().resolve():
            self.hongguo_exports_root = DEFAULT_HONGGUO_EXPORTS_ROOT
        else:
            self.hongguo_exports_root = self.root / "exports"

    def get_today_rank(self, platform: str, date: str = "", limit: int = 10) -> dict[str, Any]:
        platform = platform.strip().lower()
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError("platform must be dy or hg")

        snapshots = self.list_snapshots(platform)
        available_dates = _available_dates(snapshots)
        selected = self._select_snapshot(snapshots, date)
        if selected is None:
            return self._empty_response(platform, date, available_dates)

        previous = self._previous_snapshot(snapshots, selected)
        selected = _annotate_rank_durations(selected, snapshots)
        previous = _annotate_rank_durations(previous, snapshots) if previous else None
        comparison = self._compare(selected, previous, limit=limit)
        return {
            "platform": platform,
            "date": selected.captured_at.date().isoformat(),
            "available_dates": available_dates,
            "selected_snapshot": selected.to_dict(self.root),
            "previous_snapshot": previous.to_dict(self.root) if previous else None,
            "validation": selected.validation,
            "records_with_warnings": [record for record in selected.records if record.get("validation_warnings")][:20],
            **comparison,
        }

    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        if platform == "hg":
            return self._merge_hongguo_snapshots(
                self._list_hongguo_import_snapshots(),
                self._list_hongguo_csv_snapshots(),
            )

        pattern = RANK_PATTERNS[platform]
        snapshots: list[RankSnapshot] = []
        for path in sorted(self.root.glob(pattern)):
            snapshot = self._load_snapshot(platform, path)
            if snapshot:
                snapshots.append(snapshot)
        return sorted(snapshots, key=lambda item: (item.captured_at, str(item.source_path)))

    @staticmethod
    def _merge_hongguo_snapshots(
        imported_snapshots: list[RankSnapshot],
        csv_snapshots: list[RankSnapshot],
    ) -> list[RankSnapshot]:
        """Keep one snapshot per day, with the external CSV export as the authority."""
        by_date: dict[date, RankSnapshot] = {}
        for snapshot in imported_snapshots:
            date = snapshot.captured_at.date()
            previous = by_date.get(date)
            if previous is None or snapshot.captured_at >= previous.captured_at:
                by_date[date] = snapshot
        for snapshot in csv_snapshots:
            # CSV exports are the current operational source and supersede imports
            # for the same calendar day, regardless of their capture time.
            by_date[snapshot.captured_at.date()] = snapshot
        return sorted(by_date.values(), key=lambda item: (item.captured_at, str(item.source_path)))

    def _list_hongguo_import_snapshots(self) -> list[RankSnapshot]:
        snapshots: list[RankSnapshot] = []
        for path in sorted(self.root.glob(RANK_PATTERNS["hg"])):
            snapshot = self._load_snapshot("hg", path)
            if snapshot and snapshot.validation.get("source") != "legacy_hongguo_api":
                snapshots.append(snapshot)
        return sorted(snapshots, key=lambda item: (item.captured_at, str(item.source_path)))

    def _list_hongguo_csv_snapshots(self) -> list[RankSnapshot]:
        snapshots: list[RankSnapshot] = []
        exports_root = self.hongguo_exports_root
        if not exports_root.exists():
            return snapshots
        for path in sorted(exports_root.glob("*/top10_rankings.csv")):
            snapshot = self._load_hongguo_csv_snapshot(path)
            if snapshot:
                snapshots.append(snapshot)
        return sorted(snapshots, key=lambda item: (item.captured_at, str(item.source_path)))

    def _load_hongguo_csv_snapshot(self, path: Path) -> RankSnapshot | None:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                rows = [row for row in csv.DictReader(f) if isinstance(row, dict)]
        except OSError as exc:
            logger.warning("榜单数据导入失败：platform=hg source=%s error=%s", path, exc)
            return None

        captured_at = _parse_datetime_from_export_path(path) or datetime.fromtimestamp(path.stat().st_mtime)
        job_id = f"csv-{captured_at.strftime('%Y%m%d')}"
        normalized = [
            record
            for index, row in enumerate(rows)
            if (record := _normalize_hongguo_csv_record(row, path, self.root, job_id, captured_at, index)) is not None
        ]
        _suppress_hongguo_csv_unavailable_metrics(normalized)
        validation = _validate_records(normalized)
        snapshot = RankSnapshot(
            platform="hg",
            job_id=job_id,
            source_path=path,
            captured_at=captured_at,
            records=normalized,
            validation=validation,
        )
        self._log_snapshot_audit(snapshot)
        return snapshot

    def _load_snapshot(self, platform: str, path: Path) -> RankSnapshot | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("榜单数据导入失败：platform=%s source=%s error=%s", platform, path, exc)
            return None

        records = payload.get("records")
        if not isinstance(records, list):
            records = []
        captured_at = _parse_datetime(payload.get("captured_at")) or _parse_datetime_from_filename(path)
        if captured_at is None:
            captured_at = datetime.fromtimestamp(path.stat().st_mtime)
        job_id = _job_id_from_path(path)
        analysis_index = self._analysis_index(platform, job_id)
        if platform == "hg" and payload.get("source") == "csv_import":
            normalized = [
                record
                for index, raw_record in enumerate(records)
                if (
                    record := _normalize_hongguo_csv_record(
                        raw_record,
                        path,
                        self.root,
                        job_id,
                        captured_at,
                        index,
                    )
                )
                is not None
            ]
        else:
            normalized = [
                record
                for index, raw_record in enumerate(records)
                if (record := self._normalize_record(platform, raw_record, path, job_id, captured_at, index, analysis_index)) is not None
            ]
        if platform == "hg" and payload.get("source") != "csv_import":
            normalized = _hongguo_business_records(normalized)
        validation = _validate_records(normalized)
        snapshot = RankSnapshot(
            platform=platform,
            job_id=job_id,
            source_path=path,
            captured_at=captured_at,
            records=normalized,
            validation=validation,
        )
        self._log_snapshot_audit(snapshot)
        return snapshot

    def _log_snapshot_audit(self, snapshot: RankSnapshot) -> None:
        validation = snapshot.validation
        source_path = _relative_path(snapshot.source_path, self.root)
        logger.info(
            "榜单数据导入：platform=%s job_id=%s source=%s records=%d captured_at=%s",
            snapshot.platform,
            snapshot.job_id,
            source_path,
            len(snapshot.records),
            snapshot.captured_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        issue_counts = {
            key: value
            for key, value in validation.items()
            if key.endswith("_count") and value
        }
        logger.log(
            logging.WARNING if validation["has_warnings"] else logging.INFO,
            "数据质量审查：platform=%s job_id=%s records=%d result=%s issues=%s",
            snapshot.platform,
            snapshot.job_id,
            len(snapshot.records),
            "warning" if validation["has_warnings"] else "passed",
            json.dumps(issue_counts, ensure_ascii=False, sort_keys=True),
        )

    def _normalize_record(
        self,
        platform: str,
        raw_record: Any,
        path: Path,
        job_id: str,
        captured_at: datetime,
        index: int,
        analysis_index: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not isinstance(raw_record, dict):
            return None
        if platform == "dy":
            return _normalize_douyin_record(raw_record, path, self.root, job_id, captured_at, index, analysis_index)
        return _normalize_hongguo_record(raw_record, path, self.root, job_id, captured_at, index, analysis_index)

    def _analysis_index(self, platform: str, job_id: str) -> dict[str, dict[str, Any]]:
        analysis_dir = self.root / "data" / platform / "jobs" / job_id / "analysis"
        if not analysis_dir.exists():
            return {}
        index: dict[str, dict[str, Any]] = {}
        for path in sorted(analysis_dir.glob("*.json")):
            if path.name.endswith("_overview.json") or path.name in {"hongguo_llm_overview.json"}:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            candidates = payload.get("items") if isinstance(payload.get("items"), list) else [payload]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else item
                if not isinstance(analysis, dict):
                    continue
                entry = _analysis_entry(analysis)
                for key in _analysis_keys(analysis):
                    if key:
                        index.setdefault(key, entry)
        return index

    def _select_snapshot(self, snapshots: list[RankSnapshot], date: str) -> RankSnapshot | None:
        if not snapshots:
            return None
        if date:
            selected = [item for item in snapshots if item.captured_at.date().isoformat() == date]
            return selected[-1] if selected else None
        return snapshots[-1]

    def _previous_snapshot(self, snapshots: list[RankSnapshot], selected: RankSnapshot) -> RankSnapshot | None:
        previous = [item for item in snapshots if item.captured_at < selected.captured_at]
        return previous[-1] if previous else None

    def _compare_records(
        self,
        current_records: list[dict[str, Any]],
        previous_records: list[dict[str, Any]],
        limit: int,
    ) -> dict[str, Any]:
        current_by_id = _record_map(current_records)
        previous_by_id = _record_map(previous_records)

        current_ids = set(current_by_id)
        previous_ids = set(previous_by_id)
        common_ids = current_ids & previous_ids

        new_entries = [_with_delta(current_by_id[key]) for key in _sort_record_ids(current_ids - previous_ids, current_by_id)]
        dropped_entries = [_with_delta(previous_by_id[key]) for key in _sort_record_ids(previous_ids - current_ids, previous_by_id)]

        rank_up: list[dict[str, Any]] = []
        rank_down: list[dict[str, Any]] = []
        heat_growth: list[dict[str, Any]] = []
        like_growth: list[dict[str, Any]] = []
        for key in common_ids:
            current = current_by_id[key]
            old = previous_by_id[key]
            merged = _with_delta(
                current,
                previous_rank=old.get("rank"),
                rank_delta=_rank_delta(current, old),
                heat_delta=_number_delta(current, old, "heat"),
                like_delta=_number_delta(current, old, "like_count"),
            )
            if merged["rank_delta"] > 0:
                rank_up.append(merged)
            elif merged["rank_delta"] < 0:
                rank_down.append(merged)
            if merged["heat_delta"] > 0:
                heat_growth.append(merged)
            if merged["like_delta"] > 0:
                like_growth.append(merged)

        rank_up.sort(key=lambda item: (-item["rank_delta"], item["rank"], item["title"]))
        rank_down.sort(key=lambda item: (item["rank_delta"], item["rank"], item["title"]))
        heat_growth.sort(key=lambda item: (-item["heat_delta"], item["rank"], item["title"]))
        like_growth.sort(key=lambda item: (-item["like_delta"], item["rank"], item["title"]))

        current_entries: list[dict[str, Any]] = []
        for key in _sort_record_ids(current_ids, current_by_id):
            current = current_by_id[key]
            old = previous_by_id.get(key)
            current_entries.append(
                _with_delta(
                    current,
                    previous_rank=old.get("rank") if old else None,
                    rank_delta=_rank_delta(current, old) if old else 0,
                    heat_delta=_number_delta(current, old, "heat") if old else 0,
                    like_delta=_number_delta(current, old, "like_count") if old else 0,
                )
            )

        return {
            "summary": {
                "current_count": len(current_records),
                "previous_count": len(previous_records),
                "common_count": len(common_ids),
                "new_count": len(new_entries),
                "dropped_count": len(dropped_entries),
                "rank_up_count": len(rank_up),
                "rank_down_count": len(rank_down),
            },
            "current_entries": current_entries[:limit],
            "new_entries": new_entries[:limit],
            "dropped_entries": dropped_entries[:limit],
            "rank_up": rank_up[:limit],
            "rank_down": rank_down[:limit],
            "columns": {
                "current": _rank_display_columns(current_entries[:limit]),
                "new": _rank_display_columns(new_entries[:limit]),
                "dropped": _rank_display_columns(dropped_entries[:limit]),
                "rank_up": _rank_display_columns(rank_up[:limit], include_rank_change=True),
                "rank_down": _rank_display_columns(rank_down[:limit], include_rank_change=True),
            },
            "heat_growth_top": heat_growth[:limit],
            "like_growth_top": like_growth[:limit],
        }

    def _compare(self, selected: RankSnapshot, previous: RankSnapshot | None, limit: int) -> dict[str, Any]:
        current_records = selected.records
        previous_records = previous.records if previous else []
        comparison = self._compare_records(current_records, previous_records, limit=limit)
        if selected.platform == "hg":
            comparison["sections"] = self._hongguo_sections(current_records, previous_records, limit=limit)
        elif selected.platform == "dy":
            comparison["sections"] = self._douyin_sections(current_records, previous_records, limit=limit)
        return comparison

    def _douyin_sections(
        self,
        current_records: list[dict[str, Any]],
        previous_records: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        current_groups = _douyin_rank_groups(current_records)
        previous_groups = _douyin_rank_groups(previous_records)
        ranking_names = _ordered_values(set(current_groups) | set(previous_groups), DOUYIN_RANKING_ORDER)
        sections: list[dict[str, Any]] = []
        for ranking_name in ranking_names:
            current = current_groups.get(ranking_name, [])
            previous = previous_groups.get(ranking_name, [])
            comparison = self._compare_records(current, previous, limit=limit)
            ranking = {
                "key": ranking_name,
                "label": ranking_name,
                "section": ranking_name,
                "summary": comparison["summary"],
                "validation": _validate_records(current),
                "columns": comparison["columns"],
                "current_entries": comparison["current_entries"],
                "new_entries": comparison["new_entries"],
                "dropped_entries": comparison["dropped_entries"],
                "rank_up": comparison["rank_up"],
                "rank_down": comparison["rank_down"],
                "heat_growth_top": comparison["heat_growth_top"],
                "like_growth_top": comparison["like_growth_top"],
            }
            sections.append(
                {
                    "key": ranking_name,
                    "label": ranking_name,
                    "summary": comparison["summary"],
                    "validation": _validate_records(current),
                    "rankings": [ranking],
                }
            )
        return sections

    def _hongguo_sections(
        self,
        current_records: list[dict[str, Any]],
        previous_records: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        current_groups = _hongguo_rank_groups(current_records)
        previous_groups = _hongguo_rank_groups(previous_records)
        group_keys = set(current_groups) | set(previous_groups)
        section_names = _ordered_values(
            {key.split("|", 1)[0] for key in group_keys},
            HONGGUO_SECTION_ORDER,
        )

        sections: list[dict[str, Any]] = []
        for section_name in section_names:
            section_rankings: list[dict[str, Any]] = []
            ranking_names = _ordered_values(
                {key.split("|", 1)[1] for key in group_keys if key.startswith(f"{section_name}|")},
                HONGGUO_RANKING_ORDER,
            )
            for ranking_name in ranking_names:
                group_key = f"{section_name}|{ranking_name}"
                current = current_groups.get(group_key, [])
                previous = previous_groups.get(group_key, [])
                if not current and not previous:
                    continue
                ranking_comparison = self._compare_records(current, previous, limit=limit)
                section_rankings.append(
                    {
                        "key": group_key,
                        "label": ranking_name,
                        "section": section_name,
                        "summary": ranking_comparison["summary"],
                        "validation": _validate_records(current),
                        "columns": ranking_comparison["columns"],
                        "current_entries": ranking_comparison["current_entries"],
                        "new_entries": ranking_comparison["new_entries"],
                        "dropped_entries": ranking_comparison["dropped_entries"],
                        "rank_up": ranking_comparison["rank_up"],
                        "rank_down": ranking_comparison["rank_down"],
                        "heat_growth_top": ranking_comparison["heat_growth_top"],
                        "like_growth_top": ranking_comparison["like_growth_top"],
                    }
                )
            section_summary = self._compare_records(
                [record for records in current_groups.values() for record in records if record.get("board_section") == section_name],
                [record for records in previous_groups.values() for record in records if record.get("board_section") == section_name],
                limit=limit,
            )["summary"]
            sections.append(
                {
                    "key": section_name,
                    "label": section_name,
                    "summary": section_summary,
                    "validation": _validate_records(
                        [record for records in current_groups.values() for record in records if record.get("board_section") == section_name]
                    ),
                    "rankings": section_rankings,
                }
            )
        return sections

    def _empty_response(self, platform: str, date: str, available_dates: list[str]) -> dict[str, Any]:
        return {
            "platform": platform,
            "date": date,
            "available_dates": available_dates,
            "selected_snapshot": None,
            "previous_snapshot": None,
            "validation": _empty_validation(),
            "records_with_warnings": [],
            "summary": {
                "current_count": 0,
                "previous_count": 0,
                "common_count": 0,
                "new_count": 0,
                "dropped_count": 0,
                "rank_up_count": 0,
                "rank_down_count": 0,
            },
            "current_entries": [],
            "new_entries": [],
            "dropped_entries": [],
            "rank_up": [],
            "rank_down": [],
            "sections": [],
            "heat_growth_top": [],
            "like_growth_top": [],
            "message": "No rank snapshots found" if not available_dates else "No rank snapshot found for selected date",
        }


def _normalize_douyin_record(
    record: dict[str, Any],
    path: Path,
    root: Path,
    job_id: str,
    captured_at: datetime,
    index: int,
    analysis_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    category = _clean_text(record.get("rank_category") or record.get("category"))
    tab = _clean_text(record.get("tab"))
    category_label = _douyin_category_label(category)
    group = " / ".join(item for item in [category_label, tab] if item)
    rank = int(_parse_number(record.get("rank")) or index + 1)
    title = _clean_text(record.get("title")) or f"Untitled {rank}"
    identity = _first_text(record.get("series_id"), record.get("item_id")) or f"title:{title}|{group}"
    analysis = _lookup_analysis(analysis_index, identity, title, record.get("series_id"), record.get("item_id"))
    stats = analysis.get("stats") if isinstance(analysis.get("stats"), dict) else {}
    fallback = _douyin_fallback_analysis(record, title, category_label, tab)
    heat = _parse_optional_number(record.get("heat_text"))
    if heat is None:
        heat = _parse_optional_number(stats.get("heat"))
    like_count = _parse_optional_number(record.get("like_count"))
    favorite_count = _parse_optional_number(record.get("favorite_count"))
    comment_count = _parse_optional_number(record.get("comment_count"))
    metric_status, warnings = _metric_status(
        heat=heat,
        like_count=like_count,
        favorite_count=favorite_count,
        comment_count=comment_count,
    )
    if not title:
        warnings.append("title_missing")
    if category_label == "未知类型":
        warnings.append("category_missing")
    return {
        "id": identity,
        "record_id": identity,
        "platform": "dy",
        "job_id": job_id,
        "source_path": _relative_path(path, root),
        "captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S"),
        "title": title,
        "rank": rank,
        "category": category_label,
        "category_label": category_label,
        "tab": tab,
        "group": group or "-",
        "heat": heat,
        "heat_text": str(record.get("heat_text") or stats.get("heat") or ""),
        "like_count": like_count,
        "favorite_count": favorite_count,
        "comment_count": comment_count,
        "topic_tags": _as_text_list(analysis.get("plot_tags")) or fallback["plot_tags"],
        "selling_points": _as_text_list(analysis.get("selling_points")) or fallback["selling_points"],
        "subject": _subject_from_analysis(analysis) or fallback["subject"],
        "metric_status": metric_status,
        "validation_warnings": warnings,
        "description": _clean_text(record.get("description")),
        "cover_url": _clean_text(record.get("cover_url")),
    }


def _normalize_hongguo_record(
    record: dict[str, Any],
    path: Path,
    root: Path,
    job_id: str,
    captured_at: datetime,
    index: int,
    analysis_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rank_data = record.get("rank") if isinstance(record.get("rank"), dict) else {}
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    rank = int(_parse_number(rank_data.get("rowNum")) or index + 1)
    title = _clean_text(rank_data.get("play_name") or meta.get("title")) or f"Untitled {rank}"
    play_type = _clean_text(rank_data.get("play_type"))
    category = _clean_text(meta.get("category") or rank_data.get("category") or play_type)
    source_category_label = _hongguo_category_label(play_type, category)
    category_label = _hongguo_business_category(source_category_label)
    group = category_label or "-"
    identity = _first_text(rank_data.get("book_id"), meta.get("book_id")) or f"title:{title}|{group}"
    analysis = _lookup_analysis(analysis_index, identity, title, rank_data.get("book_id"), meta.get("book_id"))
    heat = _parse_optional_number(rank_data.get("internal_heat_score") or rank_data.get("heat") or rank_data.get("like_count"))
    like_count = _parse_optional_number(rank_data.get("like_count"))
    favorite_count = _favorite_number(
        rank_data.get("favorite_count"),
        rank_data.get("collect_count"),
        (rank_data.get("internal_heat_metrics") or {}).get("favorites") if isinstance(rank_data.get("internal_heat_metrics"), dict) else None,
        meta.get("favorite_count"),
        _details_sum(record.get("details"), "favorite_count", "collect_count"),
    )
    comment_count = _hongguo_comment_number(
        rank_data.get("comment_count"),
        rank_data.get("detail_comment_count"),
        (rank_data.get("internal_heat_metrics") or {}).get("comments") if isinstance(rank_data.get("internal_heat_metrics"), dict) else None,
        _details_sum(record.get("details"), "comment_count", "comments"),
    )
    metric_status, warnings = _metric_status(
        heat=heat,
        like_count=like_count,
        favorite_count=favorite_count,
        comment_count=comment_count,
    )
    if not title:
        warnings.append("title_missing")
    if category_label == "未知类型":
        warnings.append("category_missing")
    return {
        "id": identity,
        "record_id": identity,
        "platform": "hg",
        "job_id": job_id,
        "source_path": _relative_path(path, root),
        "captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S"),
        "title": title,
        "rank": rank,
        "category": category_label,
        "category_label": category_label,
        "source_category_label": source_category_label,
        "tab": "",
        "group": group,
        "heat": heat,
        "heat_text": str(rank_data.get("internal_heat_score") or heat or ""),
        "like_count": like_count,
        "favorite_count": favorite_count,
        "comment_count": comment_count,
        "topic_tags": _as_text_list(analysis.get("plot_tags")),
        "selling_points": _as_text_list(analysis.get("selling_points")),
        "subject": _subject_from_analysis(analysis),
        "metric_status": metric_status,
        "validation_warnings": warnings,
        "description": _clean_text(rank_data.get("desc") or meta.get("description")),
        "cover_url": _clean_text(rank_data.get("cover") or meta.get("cover_url")),
    }


def _normalize_hongguo_csv_record(
    record: dict[str, Any],
    path: Path,
    root: Path,
    job_id: str,
    captured_at: datetime,
    index: int,
) -> dict[str, Any] | None:
    rank = int(_parse_number(_first_text(record.get("排名"), record.get("rank")))) or index + 1
    title = _first_text(record.get("剧名"), record.get("title"), record.get("play_name"))
    ranking_type = _first_text(record.get("榜单类型"), record.get("ranking_type"), record.get("rank_type"))
    if not title or not ranking_type:
        return None

    board_section, board_type, normalized_ranking_type = _parse_hongguo_csv_ranking_type(ranking_type)
    identity = f"{board_section}|{board_type}|{title}"
    tags_text = _first_text(record.get("标签"), record.get("tags"), record.get("category"))
    heat = _parse_csv_optional_number(_first_text(record.get("热度"), record.get("heat")))
    like_count = _parse_csv_optional_number(_first_text(record.get("点赞"), record.get("like_count")))
    favorite_count = _parse_csv_optional_number(_first_text(record.get("收藏"), record.get("favorite_count")))
    comment_count = _parse_csv_optional_number(_first_text(record.get("评论"), record.get("comment_count")))
    metric_status, warnings = _metric_status(
        heat=heat,
        like_count=like_count,
        favorite_count=favorite_count,
        comment_count=comment_count,
    )
    return {
        "id": identity,
        "record_id": identity,
        "platform": "hg",
        "job_id": job_id,
        "source_path": _relative_path(path, root),
        "captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S"),
        "title": title,
        "rank": rank,
        "category": board_type,
        "category_label": board_type,
        "source_category_label": ranking_type,
        "tab": board_type,
        "group": normalized_ranking_type,
        "board_section": board_section,
        "board_type": board_type,
        "rank_list": normalized_ranking_type,
        "raw_ranking_type": ranking_type,
        "heat": heat,
        "heat_text": _first_text(record.get("热度"), record.get("heat")),
        "like_count": like_count,
        "favorite_count": favorite_count,
        "comment_count": comment_count,
        "share_count": _parse_csv_optional_number(_first_text(record.get("分享"), record.get("share_count"))),
        "score": _parse_csv_optional_number(_first_text(record.get("评分"), record.get("score"))),
        "recommend_index": _parse_csv_optional_number(_first_text(record.get("推荐指数"), record.get("recommend_index"))),
        "rating_count": _parse_csv_optional_number(_first_text(record.get("评分人数"), record.get("rating_count"))),
        "expectation_index": _parse_csv_optional_number(_first_text(record.get("期待指数"), record.get("expectation_index"))),
        "search_index": _parse_csv_optional_number(_first_text(record.get("热搜指数"), record.get("search_index"))),
        "max_heat": _parse_csv_optional_number(_first_text(record.get("最高热度"), record.get("max_heat"))),
        "topic_tags": _split_hongguo_tags(tags_text),
        "selling_points": [_clean_text(record.get("简介"))] if _clean_text(record.get("简介")) else [],
        "subject": tags_text,
        "metric_status": metric_status,
        "validation_warnings": warnings,
        "description": _clean_text(record.get("简介")),
        "hot_comment": _clean_text(record.get("热评")),
        "cover_url": "",
    }


def _parse_hongguo_csv_ranking_type(value: Any) -> tuple[str, str, str]:
    text = _clean_text(value)
    normalized = text.replace("红果短剧", "红果").replace("短剧", "红果")
    section = "全部"
    board_type = normalized
    for candidate in ("真人剧", "漫剧", "AI剧", "红果"):
        if normalized.startswith(candidate):
            section = "全部" if candidate == "红果" else candidate
            board_type = normalized[len(candidate) :]
            break
    board_type = board_type or normalized or "未分类"
    rank_list = f"{section}{board_type}" if section != "全部" else f"全部{board_type}"
    return section, board_type, rank_list


def _suppress_hongguo_csv_unavailable_metrics(records: list[dict[str, Any]]) -> None:
    by_board: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        board = _clean_text(record.get("rank_list") or record.get("group"))
        if board:
            by_board.setdefault(board, []).append(record)

    for board_records in by_board.values():
        for metric, warning in (
            ("favorite_count", "favorite_missing"),
            ("comment_count", "comment_missing"),
        ):
            if any(record.get(metric) is not None for record in board_records):
                continue
            for record in board_records:
                status = record.get("metric_status") if isinstance(record.get("metric_status"), dict) else {}
                status[metric] = "not_applicable"
                record["metric_status"] = status
                record["validation_warnings"] = [item for item in record.get("validation_warnings", []) if item != warning]


def _record_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("id") or "")
        if not key:
            continue
        existing = mapped.get(key)
        if existing is None or int(record.get("rank") or 0) < int(existing.get("rank") or 0):
            mapped[key] = record
    return mapped


def _rank_history_group(record: dict[str, Any]) -> str:
    return _clean_text(
        record.get("rank_list")
        or record.get("group")
        or "|".join(
            part
            for part in (
                _clean_text(record.get("board_section")),
                _clean_text(record.get("board_type")),
            )
            if part
        )
        or "default"
    )


def _rank_history_identity(record: dict[str, Any]) -> str:
    return _clean_text(record.get("id") or record.get("record_id")) or _normalize_title(record.get("title"))


def _recorded_rank_dates_are_continuous(previous: Any, current: Any) -> bool:
    """Treat a gap as continuous only when every skipped calendar day is a weekend."""
    if current <= previous:
        return False
    date = previous + timedelta(days=1)
    while date < current:
        if date.weekday() < 5:
            return False
        date += timedelta(days=1)
    return True


def _annotate_rank_durations(snapshot: RankSnapshot, snapshots: list[RankSnapshot]) -> RankSnapshot:
    eligible = [item for item in snapshots if item.captured_at <= snapshot.captured_at]
    daily_groups: dict[Any, dict[str, set[str]]] = {}
    for item in eligible:
        groups = daily_groups.setdefault(item.captured_at.date(), {})
        for record in item.records:
            group = _rank_history_group(record)
            identity = _rank_history_identity(record)
            if identity:
                groups.setdefault(group, set()).add(identity)

    target_date = snapshot.captured_at.date()
    annotated: list[dict[str, Any]] = []
    for record in snapshot.records:
        item = dict(record)
        group = _rank_history_group(record)
        identity = _rank_history_identity(record)
        appearance_dates = sorted(
            date
            for date, groups in daily_groups.items()
            if identity in groups.get(group, set())
        )
        cumulative_days = len(appearance_dates)
        continuous_days = 0
        current_streak = 0
        previous_date = None
        for date in appearance_dates:
            if previous_date is not None and _recorded_rank_dates_are_continuous(previous_date, date):
                current_streak += 1
            else:
                current_streak = 1
            continuous_days = max(continuous_days, current_streak)
            previous_date = date
        item["continuous_rank_days"] = continuous_days
        item["cumulative_rank_days"] = cumulative_days
        item["rank_duration"] = (
            f"连续在榜{continuous_days}天 / 累计在榜{cumulative_days}天"
            if continuous_days and cumulative_days
            else ""
        )
        annotated.append(item)

    return RankSnapshot(
        platform=snapshot.platform,
        job_id=snapshot.job_id,
        source_path=snapshot.source_path,
        captured_at=snapshot.captured_at,
        records=annotated,
        validation=snapshot.validation,
    )


def _hongguo_rank_groups(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        section = _clean_text(record.get("board_section")) or "全部"
        board_type = _clean_text(record.get("board_type")) or _clean_text(record.get("category_label")) or "未分类"
        grouped.setdefault(f"{section}|{board_type}", []).append(record)
    return grouped


def _douyin_rank_groups(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        ranking_name = _clean_text(record.get("tab")) or "榜单"
        grouped.setdefault(ranking_name, []).append(record)
    return {ranking_name: list(_record_map(items).values()) for ranking_name, items in grouped.items()}


def _ordered_values(values: set[str], preferred_order: list[str]) -> list[str]:
    return sorted(values, key=lambda value: (preferred_order.index(value) if value in preferred_order else len(preferred_order), value))


def _sort_record_ids(record_ids: set[str], records: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(record_ids, key=lambda key: (records[key].get("rank") or 999999, records[key].get("title") or ""))


def _rank_display_columns(records: list[dict[str, Any]], include_rank_change: bool = False) -> list[dict[str, Any]]:
    columns = [dict(column) for column in RANK_COMMON_COLUMNS]
    columns.extend(
        dict(column)
        for column in RANK_DATA_COLUMNS
        if any(_has_display_value(record.get(column["key"])) for record in records)
    )
    if include_rank_change:
        columns.extend(dict(column) for column in RANK_CHANGE_COLUMNS)
    return columns


def _has_display_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() not in {"", "-", "无", "暂无", "null", "None"}
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _available_dates(snapshots: list[RankSnapshot]) -> list[str]:
    return sorted({item.captured_at.date().isoformat() for item in snapshots}, reverse=True)


def _with_delta(
    record: dict[str, Any],
    previous_rank: int | None = None,
    rank_delta: int = 0,
    heat_delta: float = 0,
    like_delta: float = 0,
) -> dict[str, Any]:
    item = dict(record)
    item["previous_rank"] = previous_rank
    item["rank_delta"] = rank_delta
    item["heat_delta"] = round(heat_delta, 4)
    item["like_delta"] = round(like_delta, 4)
    return item


def _rank_delta(current: dict[str, Any], previous: dict[str, Any]) -> int:
    if current.get("group") != previous.get("group"):
        return 0
    current_rank = int(current.get("rank") or 0)
    previous_rank = int(previous.get("rank") or 0)
    if current_rank <= 0 or previous_rank <= 0:
        return 0
    return previous_rank - current_rank


def _number_delta(current: dict[str, Any], previous: dict[str, Any], key: str) -> float:
    return float(current.get(key) or 0) - float(previous.get(key) or 0)


def _analysis_entry(analysis: dict[str, Any]) -> dict[str, Any]:
    stats = analysis.get("stats") if isinstance(analysis.get("stats"), dict) else {}
    return {
        "title": _clean_text(analysis.get("title")),
        "book_id": _clean_text(analysis.get("book_id")),
        "series_id": _clean_text(analysis.get("series_id")),
        "item_id": _clean_text(analysis.get("item_id")),
        "plot_tags": _as_text_list(analysis.get("plot_tags")),
        "selling_points": _as_text_list(analysis.get("selling_points")),
        "subject": _subject_from_analysis(analysis),
        "stats": stats,
    }


def _analysis_keys(analysis: dict[str, Any]) -> list[str]:
    return [
        _clean_text(analysis.get("book_id")),
        _clean_text(analysis.get("series_id")),
        _clean_text(analysis.get("item_id")),
        _clean_text(analysis.get("title")),
        _normalize_title(analysis.get("title")),
    ]


def _lookup_analysis(index: dict[str, dict[str, Any]], *keys: Any) -> dict[str, Any]:
    for key in keys:
        text = _clean_text(key)
        if text and text in index:
            return index[text]
        normalized = _normalize_title(text)
        if normalized and normalized in index:
            return index[normalized]
    return {}


def _subject_from_analysis(analysis: dict[str, Any]) -> str:
    llm_analysis = analysis.get("llm_analysis") if isinstance(analysis.get("llm_analysis"), dict) else {}
    for key in (
        "剧情类型",
        "题材",
        "题材类型",
        "剧集题材",
        "plot_type",
        "genre",
        "subject",
        "鍓ф儏绫诲瀷",
    ):
        value = _clean_text(llm_analysis.get(key) or analysis.get(key))
        if value:
            return value
    tags = _as_text_list(analysis.get("plot_tags"))
    return " / ".join(tags[:3])


def _douyin_category_label(value: Any) -> str:
    text = _clean_text(value)
    if text in {"短剧", "漫剧"}:
        return text
    if text in {"鐭墽"}:
        return "短剧"
    if text in {"婕墽"}:
        return "漫剧"
    return "未知类型" if not text or text == "one_click" else text


def _hongguo_category_label(play_type: Any, category: Any = "") -> str:
    text = _clean_text(play_type)
    mapping = {"0": "短剧", "1": "漫剧", "2": "AI漫剧"}
    if text in mapping:
        return mapping[text]
    category_text = _clean_text(category)
    if category_text in {"短剧", "漫剧", "AI漫剧"}:
        return category_text
    if category_text in {"鐭墽"}:
        return "短剧"
    if category_text in {"婕墽"}:
        return "漫剧"
    return category_text or "未知类型"


def _hongguo_business_category(value: Any) -> str:
    text = _clean_text(value)
    if text in {"漫剧", "AI漫剧"}:
        return "漫剧"
    if text == "短剧":
        return "短剧"
    return text


def _douyin_fallback_analysis(record: dict[str, Any], title: str, category_label: str, tab: str) -> dict[str, Any]:
    seed_parts = [
        title,
        category_label,
        tab,
        _clean_text(record.get("card_category_text")),
        _clean_text(record.get("description")),
    ]
    comments = record.get("comments")
    if isinstance(comments, list):
        for item in comments[:6]:
            if isinstance(item, dict):
                seed_parts.append(_clean_text(item.get("content")))
            else:
                seed_parts.append(_clean_text(item))
    seed = " ".join(item for item in seed_parts if item)
    tags = _infer_douyin_tags(seed)
    selling_points = [
        item
        for item in (
            _clean_text(record.get("description")),
            "高互动评论可作为投放素材的情绪反馈来源" if comments else "",
        )
        if item
    ]
    return {
        "plot_tags": tags,
        "selling_points": selling_points,
        "subject": " / ".join(tags[:3]),
    }


def _infer_douyin_tags(text: str) -> list[str]:
    rules = [
        ("虐恋", ("虐", "离婚", "前夫", "前妻", "错爱", "复婚", "再婚")),
        ("复仇", ("复仇", "报仇", "逆袭", "重生", "讨债")),
        ("豪门", ("豪门", "总裁", "首富", "千金", "商总", "少夫人", "顾先生")),
        ("身份反转", ("马甲", "藏不住", "身份", "曝光")),
        ("家庭", ("妈妈", "爸妈", "父母", "婆婆", "孩子", "一家人", "三代")),
        ("职场", ("职场", "上司", "老板", "公司", "保镖")),
        ("悬疑", ("谜案", "失踪", "秘密", "真相", "案")),
        ("甜宠", ("甜", "宠", "心动", "偏爱")),
        ("神医", ("神医", "医", "金瞳")),
        ("年代", ("年代", "古代", "片场", "东北")),
        ("热血", ("龙头", "出狱", "绝地", "生还")),
    ]
    result: list[str] = []
    for tag, keywords in rules:
        if any(keyword in text for keyword in keywords):
            result.append(tag)
    return result[:3]


def _metric_status(
    heat: float | None,
    like_count: float | None,
    favorite_count: float | None,
    comment_count: float | None,
) -> tuple[dict[str, str], list[str]]:
    pairs = {
        "heat": heat,
        "like_count": like_count,
        "favorite_count": favorite_count,
        "comment_count": comment_count,
    }
    status: dict[str, str] = {}
    warnings: list[str] = []
    warning_names = {
        "heat": "heat_missing",
        "like_count": "like_missing",
        "favorite_count": "favorite_missing",
        "comment_count": "comment_missing",
    }
    for key, value in pairs.items():
        if value is None:
            status[key] = "missing"
            warnings.append(warning_names[key])
        else:
            status[key] = "ok"
    return status, warnings


def _validate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    validation = _empty_validation()
    seen: set[str] = set()
    for record in records:
        warnings = set(record.get("validation_warnings") or [])
        if "heat_missing" in warnings:
            validation["missing_heat_count"] += 1
        if "favorite_missing" in warnings:
            validation["missing_favorite_count"] += 1
        if "comment_missing" in warnings:
            validation["missing_comment_count"] += 1
        if "title_missing" in warnings:
            validation["missing_title_count"] += 1
        if "category_missing" in warnings:
            validation["missing_category_count"] += 1
        rank = int(record.get("rank") or 0)
        if rank <= 0:
            validation["abnormal_rank_count"] += 1
        record_id = "|".join([
            _clean_text(record.get("record_id")),
            _clean_text(record.get("category_label") or record.get("category")),
            _clean_text(record.get("tab")),
        ])
        if record_id:
            if record_id in seen:
                validation["duplicate_id_count"] += 1
            seen.add(record_id)
    validation["has_warnings"] = any(
        validation[key] > 0
        for key in (
            "missing_heat_count",
            "missing_favorite_count",
            "missing_comment_count",
            "missing_title_count",
            "duplicate_id_count",
            "abnormal_rank_count",
            "missing_category_count",
        )
    )
    return validation


def _empty_validation() -> dict[str, Any]:
    return {
        "missing_heat_count": 0,
        "missing_favorite_count": 0,
        "missing_comment_count": 0,
        "missing_title_count": 0,
        "duplicate_id_count": 0,
        "abnormal_rank_count": 0,
        "missing_category_count": 0,
        "has_warnings": False,
    }


def _parse_optional_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and (not value.strip() or value.strip() in {"无", "-", "暂无", "null", "None"}):
        return None
    return _parse_number(value)


def _parse_csv_optional_number(value: Any) -> float | None:
    return _parse_optional_number(value)


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _parse_optional_number(value)
        if parsed is not None:
            return parsed
    return None


def _favorite_number(*values: Any) -> float | None:
    for index, value in enumerate(values):
        parsed = _parse_optional_number(value)
        if parsed is None:
            continue
        if index == 2 and parsed <= 0:
            continue
        return parsed
    return None


def _hongguo_comment_number(*values: Any) -> float | None:
    for index, value in enumerate(values):
        parsed = _parse_optional_number(value)
        if parsed is None:
            continue
        if parsed <= 0:
            continue
        return parsed
    return None


def _details_sum(details: Any, *keys: str) -> float | None:
    if not isinstance(details, list):
        return None
    total = 0.0
    found = False
    for item in details:
        if not isinstance(item, dict):
            continue
        for key in keys:
            parsed = _parse_optional_number(item.get(key))
            if parsed is not None:
                total += parsed
                found = True
                break
    return total if found else None


def _as_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text and text not in result:
            result.append(text)
    return result


def _split_hongguo_tags(value: Any) -> list[str]:
    text = _clean_text(value)
    if not text:
        return []
    return [part for part in re.split(r"[·,，/|、\s]+", text) if part]


def _hongguo_business_records(records: list[dict[str, Any]], per_type: int = 10) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {"短剧": [], "漫剧": []}
    for record in records:
        category = _hongguo_business_category(record.get("category_label") or record.get("category"))
        if category in grouped:
            grouped[category].append(record)

    selected: list[dict[str, Any]] = []
    for category in ("短剧", "漫剧"):
        selected.extend(
            sorted(
                grouped[category],
                key=lambda item: (
                    int(item.get("rank") or 999999),
                    -(float(item.get("like_count") or 0)),
                    _clean_text(item.get("title")),
                ),
            )[:per_type]
        )
    return selected


def _normalize_title(value: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(value)).lower()


def _parse_number(value: Any) -> float:
    if value is None:
        return 0
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    lower = text.lower()
    multiplier = 1
    if "亿" in text or "億" in text:
        multiplier = 100000000
    elif "万" in text or "w" in lower:
        multiplier = 10000
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0
    return float(match.group(0)) * multiplier


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _parse_datetime_from_filename(path: Path) -> datetime | None:
    match = re.search(r"_(\d{8})_(\d{6})", path.stem)
    if not match:
        return None
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _parse_datetime_from_export_path(path: Path) -> datetime | None:
    for part in reversed(path.parts):
        match = re.search(r"(?<!\d)(\d{8})(?!\d)", part)
        if match:
            try:
                return datetime.strptime(match.group(1) + " 00:00:00", "%Y%m%d %H:%M:%S")
            except ValueError:
                return None
    return None


def _job_id_from_path(path: Path) -> str:
    parts = path.parts
    if "jobs" in parts:
        index = parts.index("jobs")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""
