# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Monthly trend reports assembled from rank snapshots and weekly reports."""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment

from analysis.rank_models import RankSnapshot
from analysis.today_rank_service import TodayRankService
from analysis.weekly_report_service import (
    WeeklyReportService,
    _board_key,
    _is_theme_tag,
    _metric_coverage,
    _number,
    _record_tags,
)


PLATFORMS = frozenset({"dy", "hg"})
REPORT_VERSION = 1
RECOMMENDATION_LIMIT = 10
CHART_SERIES_LIMIT = 5
METRIC_LABELS = {
    "heat": "热度",
    "like_count": "点赞",
    "favorite_count": "收藏",
    "comment_count": "评论",
}


class MonthlyReportRepository:
    """Durable monthly-report payload and export metadata store."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS monthly_reports (
                    report_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    month_start TEXT NOT NULL,
                    month_end TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    excel_path TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    UNIQUE(platform, month_start)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM monthly_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        return _decode_report_row(row) if row else None

    def list(self, platform: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM monthly_reports"
        params: list[Any] = []
        if platform:
            sql += " WHERE platform = ?"
            params.append(platform)
        sql += " ORDER BY month_start DESC, generated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decode_report_row(row) for row in rows]

    def upsert(
        self,
        report: dict[str, Any],
        markdown_path: Path,
        excel_path: Path,
    ) -> dict[str, Any]:
        generated_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO monthly_reports (
                    report_id, platform, month_start, month_end,
                    source_fingerprint, status, payload_json,
                    markdown_path, excel_path, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO UPDATE SET
                    source_fingerprint=excluded.source_fingerprint,
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    markdown_path=excluded.markdown_path,
                    excel_path=excluded.excel_path,
                    generated_at=excluded.generated_at
                """,
                (
                    report["report_id"],
                    report["platform"],
                    report["period"]["start"],
                    report["period"]["end"],
                    report["source_fingerprint"],
                    report["status"],
                    json.dumps(report, ensure_ascii=False),
                    str(markdown_path),
                    str(excel_path),
                    generated_at,
                ),
            )
        return self.get(report["report_id"]) or report


class MonthlyReportService:
    """Build monthly reports without changing the weekly report pipeline."""

    def __init__(
        self,
        root: str | Path = ".",
        runtime_dir: str | Path = ".runtime",
        analysis_script_path: str | Path | None = None,
        rank_service: TodayRankService | None = None,
        weekly_service: WeeklyReportService | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.runtime_dir = Path(runtime_dir)
        if not self.runtime_dir.is_absolute():
            self.runtime_dir = self.root / self.runtime_dir
        self.analysis_script_path = Path(analysis_script_path or __file__).resolve()
        self.rank_service = rank_service or TodayRankService.from_environment(
            root=self.root
        )
        self.weekly_service = weekly_service or WeeklyReportService(
            root=self.root,
            runtime_dir=self.runtime_dir,
            rank_service=self.rank_service,
        )
        self.repository = MonthlyReportRepository(
            self.runtime_dir / "rag" / "monthly_reports.db"
        )
        self.output_dir = self.root / "data" / "reports" / "monthly"

    def build_month(
        self,
        platform: str,
        month: str | date,
        *,
        as_of: datetime | None = None,
        with_llm: bool = False,
    ) -> dict[str, Any]:
        value = _validate_platform(platform)
        month_start = _parse_month(month)
        now = as_of or datetime.now()
        snapshots = self._month_snapshots(value, month_start, now)
        if not snapshots:
            return {
                "platform": value,
                "month": month_start.strftime("%Y-%m"),
                "status": "insufficient_data",
            }
        weekly_reports = self._weekly_reports_for_month(value, month_start, now)
        report = self._build_report(value, month_start, snapshots, weekly_reports, now)
        if with_llm:
            report["llm"] = self._llm_summary(report)
        return report

    def generate_all_available(
        self,
        platform: str = "",
        as_of: datetime | None = None,
        with_llm: bool = True,
    ) -> list[dict[str, Any]]:
        platforms = (_validate_platform(platform),) if platform else tuple(sorted(PLATFORMS))
        now = as_of or datetime.now()
        generated: list[dict[str, Any]] = []
        for value in platforms:
            snapshots_by_month = self._snapshots_by_month(value, now)
            weekly_reports = self.weekly_service.list_reports(
                platform=value,
                as_of=now,
            )
            for month_start in sorted(snapshots_by_month, reverse=True):
                source_weeklies = _weekly_reports_for_month(weekly_reports, month_start)
                report = self._build_report(
                    value,
                    month_start,
                    snapshots_by_month[month_start],
                    source_weeklies,
                    now,
                )
                existing = self.repository.get(report["report_id"])
                exports_available = bool(
                    existing
                    and Path(str(existing.get("markdown_path") or "")).is_file()
                    and Path(str(existing.get("excel_path") or "")).is_file()
                )
                llm_ready = bool((existing or {}).get("llm", {}).get("status") == "ready")
                if (
                    existing
                    and existing.get("source_fingerprint") == report["source_fingerprint"]
                    and exports_available
                    and (not with_llm or llm_ready)
                ):
                    generated.append({**existing, "cache_status": "hit"})
                    continue
                if with_llm:
                    report["llm"] = self._llm_summary(report)
                markdown_path, excel_path = self._write_exports(report)
                stored = self.repository.upsert(report, markdown_path, excel_path)
                generated.append({**stored, "cache_status": "generated"})
        return generated

    def list_reports(
        self,
        platform: str = "",
        as_of: datetime | None = None,
        ensure_generated: bool = True,
    ) -> list[dict[str, Any]]:
        if platform:
            _validate_platform(platform)
        generation_results = (
            self.generate_all_available(
                platform=platform,
                as_of=as_of,
                with_llm=False,
            )
            if ensure_generated
            else []
        )
        cache_statuses = {
            item["report_id"]: item.get("cache_status", "hit")
            for item in generation_results
            if item.get("report_id")
        }
        reports = self.repository.list(platform=platform)
        for report in reports:
            report["cache_status"] = cache_statuses.get(report["report_id"], "hit")
        return reports

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        return self.repository.get(report_id)

    def _snapshots_by_month(
        self,
        platform: str,
        as_of: datetime,
    ) -> dict[date, list[RankSnapshot]]:
        latest_by_day: dict[date, RankSnapshot] = {}
        for snapshot in self.rank_service.list_snapshots(platform):
            if snapshot.captured_at.weekday() >= 5 or _is_after(snapshot.captured_at, as_of):
                continue
            snapshot_day = snapshot.captured_at.date()
            existing = latest_by_day.get(snapshot_day)
            if existing is None or snapshot.captured_at > existing.captured_at:
                latest_by_day[snapshot_day] = snapshot
        grouped: dict[date, list[RankSnapshot]] = defaultdict(list)
        for snapshot_day, snapshot in latest_by_day.items():
            grouped[snapshot_day.replace(day=1)].append(snapshot)
        return {
            month_start: sorted(items, key=lambda item: item.captured_at)
            for month_start, items in grouped.items()
        }

    def _month_snapshots(
        self,
        platform: str,
        month_start: date,
        as_of: datetime,
    ) -> list[RankSnapshot]:
        return self._snapshots_by_month(platform, as_of).get(month_start, [])

    def _weekly_reports_for_month(
        self,
        platform: str,
        month_start: date,
        as_of: datetime,
    ) -> list[dict[str, Any]]:
        reports = self.weekly_service.list_reports(platform=platform, as_of=as_of)
        return _weekly_reports_for_month(reports, month_start)

    def _build_report(
        self,
        platform: str,
        month_start: date,
        snapshots: list[RankSnapshot],
        weekly_reports: list[dict[str, Any]],
        as_of: datetime,
    ) -> dict[str, Any]:
        month_end = _month_end(month_start)
        report_status = "partial" if month_start == as_of.date().replace(day=1) else "ready"
        boards = _group_boards(snapshots)
        recommendations = _recommendations(boards, len(snapshots))
        movements = _movements(boards)
        tags = _tag_analysis(boards)
        recommendation_metrics = _weekly_recommendation_metrics(
            boards,
            recommendations,
            snapshots,
            as_of,
        )
        data_quality = _data_quality(month_start, month_end, snapshots, as_of)
        weekly_highlights = [_weekly_highlight(item) for item in weekly_reports]
        summary = _summary(
            platform,
            recommendations,
            movements,
            tags,
            data_quality,
            weekly_highlights,
        )
        source_fingerprint = _fingerprint(
            platform,
            month_start,
            snapshots,
            weekly_highlights,
            report_status,
            data_quality["effective_end"],
            self.analysis_script_path,
        )
        return {
            "report_id": f"{platform}-{month_start.strftime('%Y-%m')}",
            "platform": platform,
            "month": month_start.strftime("%Y-%m"),
            "status": report_status,
            "period": {
                "start": month_start.isoformat(),
                "end": month_end.isoformat(),
            },
            "source_fingerprint": source_fingerprint,
            "source_weekly_report_ids": [
                item["report_id"] for item in weekly_highlights
            ],
            "data_quality": data_quality,
            "summary": summary,
            "weekly_highlights": weekly_highlights,
            "recommendations": recommendations,
            "tags": tags,
            "movements": movements,
            "recommendation_metrics": recommendation_metrics,
            "llm": {"status": "not_requested", "content": {}},
        }

    def _llm_summary(self, report: dict[str, Any]) -> dict[str, Any]:
        try:
            import config
            from analysis.llm import create_llm_provider_from_config

            payload = {
                "platform": report["platform"],
                "period": report["period"],
                "status": report["status"],
                "data_quality": report["data_quality"],
                "weekly_highlights": report["weekly_highlights"],
                "recommendations": report["recommendations"],
                "recommendation_metrics": report["recommendation_metrics"],
                "theme_tags": report["tags"]["theme_tags"],
                "movements": report["movements"],
                "instruction": (
                    "只根据输入的月度结构化数据和来源周报输出合法 JSON，字段为 "
                    "management_summary、trend_analysis、risks、actions。"
                    "trend_analysis 不少于100个中文字符，必须说明各周变化、月度Top剧目、"
                    "题材标签、排名稳定性和可用互动指标。数据不完整时必须明确缺失日期，"
                    "不得补写未提供的剧情、受众、因果或跨平台比较。"
                ),
            }
            provider = create_llm_provider_from_config(config)
            content = asyncio.run(
                provider.complete_json(
                    [
                        {
                            "role": "system",
                            "content": "你是短剧管理层月度数据分析助手，只输出合法 JSON。",
                        },
                        {
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii=False),
                        },
                    ]
                )
            )
            if not isinstance(content, dict):
                raise ValueError("LLM response must be an object")
            trend_analysis = str(content.get("trend_analysis") or "").strip()
            if len("".join(trend_analysis.split())) < 100:
                raise ValueError("LLM trend_analysis must contain at least 100 characters")
            return {
                "status": "ready",
                "content": {
                    "management_summary": str(content.get("management_summary") or ""),
                    "trend_analysis": trend_analysis,
                    "risks": _as_text_list(content.get("risks")),
                    "actions": _as_text_list(content.get("actions")),
                },
            }
        except Exception as exc:
            return {
                "status": "unavailable",
                "content": {},
                "error": str(exc)[:300],
            }

    def _write_exports(self, report: dict[str, Any]) -> tuple[Path, Path]:
        output = self.output_dir / report["platform"] / report["month"]
        output.mkdir(parents=True, exist_ok=True)
        platform_label = {
            "dy": "抖音",
            "hg": "红果",
        }.get(str(report["platform"]), str(report["platform"]))
        export_name = f"{platform_label}月度流行趋势报告_{report['month']}"
        markdown_path = output / f"{export_name}.md"
        excel_path = output / f"{export_name}.xlsx"
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        write_excel(report, excel_path)
        return markdown_path, excel_path


def _validate_platform(platform: str) -> str:
    value = platform.strip().lower()
    if value not in PLATFORMS:
        raise ValueError("platform must be dy or hg")
    return value


def _parse_month(value: str | date) -> date:
    if isinstance(value, date):
        return value.replace(day=1)
    try:
        return date.fromisoformat(f"{str(value).strip()}-01")
    except ValueError as exc:
        raise ValueError("month must use YYYY-MM") from exc


def _month_end(month_start: date) -> date:
    return month_start.replace(
        day=calendar.monthrange(month_start.year, month_start.month)[1]
    )


def _is_after(captured_at: datetime, as_of: datetime) -> bool:
    comparable = as_of
    if captured_at.tzinfo is not None and as_of.tzinfo is None:
        comparable = as_of.replace(tzinfo=captured_at.tzinfo)
    elif captured_at.tzinfo is None and as_of.tzinfo is not None:
        comparable = as_of.replace(tzinfo=None)
    return captured_at > comparable


def _group_boards(
    snapshots: list[RankSnapshot],
) -> dict[str, list[dict[str, dict[str, Any]]]]:
    daily_boards: list[dict[str, dict[str, dict[str, Any]]]] = []
    board_keys: set[str] = set()
    for snapshot in snapshots:
        per_board: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for record in snapshot.records:
            identity = str(
                record.get("id")
                or record.get("record_id")
                or record.get("series_id")
                or record.get("title")
                or ""
            )
            if identity:
                per_board[_board_key(record)][identity] = record
        board_keys.update(per_board)
        daily_boards.append(per_board)
    return {
        board: [daily.get(board, {}) for daily in daily_boards]
        for board in sorted(board_keys)
    }


def _recommendations(
    boards: dict[str, list[dict[str, dict[str, Any]]]],
    snapshot_count: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for board, days in boards.items():
        max_rank = max(
            [
                int(record.get("rank") or 0)
                for records in days
                for record in records.values()
            ]
            or [1]
        )
        identities = set().union(*(set(records) for records in days)) if days else set()
        raw: list[dict[str, Any]] = []
        for identity in identities:
            history = [records.get(identity) for records in days]
            appearances = [record for record in history if record]
            if not appearances:
                continue
            first = appearances[0]
            latest = appearances[-1]
            ranks = [int(record.get("rank") or max_rank) for record in appearances]
            metric, metric_delta = _metric_delta(appearances)
            persistence = len(appearances) / max(snapshot_count, 1)
            average_rank = round(sum(ranks) / len(ranks), 1)
            first_rank = ranks[0]
            latest_rank = ranks[-1]
            present_at_end = history[-1] is not None
            raw.append(
                {
                    "identity": identity,
                    "board": board,
                    "title": str(latest.get("title") or first.get("title") or ""),
                    "category": str(latest.get("category") or "未知类型"),
                    "description": str(latest.get("description") or ""),
                    "tags": _record_tags(latest),
                    "metrics": {
                        name: _number(latest.get(name)) for name in METRIC_LABELS
                    },
                    "current_rank": latest_rank,
                    "average_rank": average_rank,
                    "best_rank": min(ranks),
                    "rank_change": first_rank - latest_rank,
                    "appearances": len(appearances),
                    "expected_appearances": snapshot_count,
                    "persistence": round(persistence, 3),
                    "present_at_end": present_at_end,
                    "average_rank_strength": _rank_strength(average_rank, max_rank),
                    "end_rank_strength": (
                        _rank_strength(latest_rank, max_rank) if present_at_end else 0.0
                    ),
                    "rank_momentum": (
                        _rank_momentum(first_rank, latest_rank, max_rank)
                        if present_at_end
                        else 0.0
                    ),
                    "metric": metric,
                    "metric_delta": metric_delta,
                }
            )
        _apply_recommendation_scores(raw)
        candidates.extend(raw)
    candidates.sort(
        key=lambda item: (
            -item["score"],
            -item["persistence"],
            item["average_rank"],
            item["title"],
        )
    )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        item["recommendation"] = (
            "重点关注"
            if item["score"] >= 75
            else "持续观察"
            if item["score"] >= 50
            else "风险观察"
        )
        item["reason"] = _recommendation_reason(item)
        result.append(item)
        if len(result) == RECOMMENDATION_LIMIT:
            break
    return result


def _metric_delta(records: list[dict[str, Any]]) -> tuple[str, int | None]:
    for metric in METRIC_LABELS:
        values = [_number(record.get(metric)) for record in records]
        non_zero = [value for value in values if value > 0]
        if len(non_zero) >= 2:
            return metric, non_zero[-1] - non_zero[0]
    return "", None


def _apply_recommendation_scores(items: list[dict[str, Any]]) -> None:
    metric_values = [
        item["metric_delta"]
        for item in items
        if item["metric_delta"] is not None
    ]
    lower, upper = (
        (min(metric_values), max(metric_values)) if metric_values else (0, 0)
    )
    for item in items:
        metric_score = 0.5
        if item["metric_delta"] is not None:
            if upper == lower:
                metric_score = 1.0 if item["metric_delta"] > 0 else 0.5
            else:
                metric_score = _normalize(item["metric_delta"], lower, upper)
        item["score_components"] = {
            "persistence": round(item["persistence"] * 100, 1),
            "average_rank": round(item["average_rank_strength"] * 100, 1),
            "month_end_rank": round(item["end_rank_strength"] * 100, 1),
            "rank_momentum": round(item["rank_momentum"] * 100, 1),
            "metric_trend": round(metric_score * 100, 1),
        }
        item["score"] = round(
            100
            * (
                0.40 * item["persistence"]
                + 0.25 * item["average_rank_strength"]
                + 0.10 * item["end_rank_strength"]
                + 0.10 * item["rank_momentum"]
                + 0.15 * metric_score
            ),
            1,
        )


def _recommendation_reason(item: dict[str, Any]) -> str:
    parts = [
        f"在榜 {item['appearances']}/{item['expected_appearances']} 个有效工作日",
        f"月均第 {item['average_rank']} 名",
        f"最佳第 {item['best_rank']} 名",
    ]
    if item["present_at_end"]:
        parts.append(f"月末第 {item['current_rank']} 名")
    else:
        parts.append("月末未在榜")
    rank_change = int(item.get("rank_change") or 0)
    if rank_change > 0:
        parts.append(f"较首次上升 {rank_change} 名")
    elif rank_change < 0:
        parts.append(f"较首次下降 {abs(rank_change)} 名")
    metric_delta = item.get("metric_delta")
    if metric_delta is not None:
        label = METRIC_LABELS.get(str(item.get("metric") or ""), "互动")
        parts.append(f"{label}{'增长' if metric_delta > 0 else '回落' if metric_delta < 0 else '持平'}")
    return "；".join(parts)


def _movements(
    boards: dict[str, list[dict[str, dict[str, Any]]]],
) -> dict[str, Any]:
    result: dict[str, list[dict[str, Any]]] = {
        "new_entries": [],
        "dropped_entries": [],
        "rank_up": [],
        "rank_down": [],
    }
    for board, days in boards.items():
        if not days:
            continue
        first, last = days[0], days[-1]
        for identity in set(last) - set(first):
            result["new_entries"].append(_movement_item(last[identity], board))
        for identity in set(first) - set(last):
            result["dropped_entries"].append(_movement_item(first[identity], board))
        for identity in set(first) & set(last):
            delta = int(first[identity].get("rank") or 0) - int(
                last[identity].get("rank") or 0
            )
            if delta > 0:
                result["rank_up"].append(
                    {**_movement_item(last[identity], board), "rank_delta": delta}
                )
            elif delta < 0:
                result["rank_down"].append(
                    {**_movement_item(last[identity], board), "rank_delta": delta}
                )
    for key, values in result.items():
        if key == "rank_up":
            values.sort(key=lambda item: (-item["rank_delta"], item["rank"], item["title"]))
        elif key == "rank_down":
            values.sort(key=lambda item: (item["rank_delta"], item["rank"], item["title"]))
        else:
            values.sort(key=lambda item: (item["rank"], item["title"]))
        result[key] = values[:20]
    result["summary"] = {
        key: len(value) for key, value in result.items() if isinstance(value, list)
    }
    return result


def _movement_item(record: dict[str, Any], board: str) -> dict[str, Any]:
    return {
        "board": board,
        "title": str(record.get("title") or ""),
        "rank": int(record.get("rank") or 0),
        "category": str(record.get("category") or "未知类型"),
        "tags": _record_tags(record),
    }


def _tag_analysis(
    boards: dict[str, list[dict[str, dict[str, Any]]]],
) -> dict[str, list[dict[str, Any]]]:
    category_rows: list[dict[str, Any]] = []
    tag_rows: list[dict[str, Any]] = []
    for board, days in boards.items():
        if not days:
            continue
        first, last = days[0], days[-1]
        for kind, getter, target in (
            (
                "category",
                lambda record: [str(record.get("category") or "未知类型")],
                category_rows,
            ),
            ("topic_tags", _record_tags, tag_rows),
        ):
            del kind
            start = _tag_counts(first, getter)
            end = _tag_counts(last, getter)
            heat_by_tag = _tag_heat(last, getter)
            for tag in sorted(set(start) | set(end)):
                target.append(
                    {
                        "board": board,
                        "tag": tag,
                        "start_count": start[tag],
                        "end_count": end[tag],
                        "net_change": end[tag] - start[tag],
                        "heat_contribution": heat_by_tag.get(tag, 0),
                    }
                )
    for rows in (category_rows, tag_rows):
        rows.sort(key=lambda item: (-item["net_change"], -item["end_count"], item["tag"]))
    themes: dict[str, dict[str, Any]] = {}
    for row in tag_rows:
        tag = row["tag"]
        if not _is_theme_tag(tag):
            continue
        item = themes.setdefault(
            tag,
            {
                "tag": tag,
                "count": 0,
                "net_change": 0,
                "heat_contribution": 0,
                "board_count": 0,
            },
        )
        item["count"] += row["end_count"]
        item["net_change"] += row["net_change"]
        item["heat_contribution"] += row["heat_contribution"]
        item["board_count"] += 1
    theme_rows = sorted(
        themes.values(),
        key=lambda item: (-item["count"], -item["net_change"], item["tag"]),
    )
    return {
        "categories": category_rows[:30],
        "topic_tags": tag_rows[:30],
        "theme_tags": theme_rows[:30],
    }


def _tag_counts(records: dict[str, dict[str, Any]], getter: Any) -> Counter[str]:
    return Counter(tag for record in records.values() for tag in getter(record))


def _tag_heat(records: dict[str, dict[str, Any]], getter: Any) -> Counter[str]:
    values: Counter[str] = Counter()
    for record in records.values():
        heat = _number(record.get("heat")) or _number(record.get("like_count"))
        for tag in getter(record):
            values[tag] += heat
    return values


def _weekly_recommendation_metrics(
    boards: dict[str, list[dict[str, dict[str, Any]]]],
    recommendations: list[dict[str, Any]],
    snapshots: list[RankSnapshot],
    as_of: datetime,
) -> dict[str, Any]:
    week_starts = sorted(
        {
            snapshot.captured_at.date()
            - timedelta(days=snapshot.captured_at.date().weekday())
            for snapshot in snapshots
        }
    )
    buckets = {
        week_start: [
            index
            for index, snapshot in enumerate(snapshots)
            if snapshot.captured_at.date()
            - timedelta(days=snapshot.captured_at.date().weekday())
            == week_start
        ]
        for week_start in week_starts
    }
    candidates: list[tuple[dict[str, Any], list[dict[str, Any] | None]]] = []
    for item in recommendations[:CHART_SERIES_LIMIT]:
        days = boards.get(str(item.get("board") or ""), [])
        identity = str(item.get("identity") or "")
        candidates.append((item, [day.get(identity) for day in days]))
    metric_coverage = {
        metric: sum(
            1
            for _, records in candidates
            for record in records
            if record is not None and _number(record.get(metric)) > 0
        )
        for metric in METRIC_LABELS
    }
    selected_metric = max(
        METRIC_LABELS,
        key=lambda name: (metric_coverage[name], -list(METRIC_LABELS).index(name)),
    )
    points: list[str] = []
    for week_start in week_starts:
        week_dates = [snapshots[index].captured_at.date() for index in buckets[week_start]]
        label = f"{week_dates[0].strftime('%m-%d')}~{week_dates[-1].strftime('%m-%d')}"
        if week_start <= as_of.date() <= week_start + timedelta(days=6):
            label += "（进行中）"
        points.append(label)
    series: list[dict[str, Any]] = []
    for item, records in candidates:
        weekly_values: list[float | None] = []
        all_values = [
            _number(record.get(selected_metric))
            for record in records
            if record is not None and _number(record.get(selected_metric)) > 0
        ]
        for week_start in week_starts:
            values = [
                _number(records[index].get(selected_metric))
                for index in buckets[week_start]
                if records[index] is not None
                and _number(records[index].get(selected_metric)) > 0
            ]
            weekly_values.append(round(sum(values) / len(values), 1) if values else None)
        if not all_values:
            continue
        monthly_average = round(sum(all_values) / len(all_values), 1)
        series.append(
            {
                "title": item["title"],
                "board": item["board"],
                "metric": selected_metric,
                "metric_label": METRIC_LABELS[selected_metric],
                "monthly_average": monthly_average,
                "period_average": monthly_average,
                "values": weekly_values,
                "index_values": [
                    round(value * 100 / monthly_average, 1) if value is not None else None
                    for value in weekly_values
                ],
            }
        )
    source_label = (
        f"来源：月度推荐剧目的{METRIC_LABELS[selected_metric]}工作日周均值；每部剧月均=100"
        if series
        else "暂无可用于分周比较的推荐剧目指标"
    )
    return {
        "unit": "monthly_average_index",
        "source": "monthly_recommended_dramas",
        "source_label": source_label,
        "points": points,
        "series": series,
    }


def _data_quality(
    month_start: date,
    month_end: date,
    snapshots: list[RankSnapshot],
    as_of: datetime,
) -> dict[str, Any]:
    effective_end = min(month_end, as_of.date())
    expected_dates = _weekdays(month_start, effective_end)
    source_dates = [snapshot.captured_at.date() for snapshot in snapshots]
    source_date_set = set(source_dates)
    missing_dates = [value.isoformat() for value in expected_dates if value not in source_date_set]
    expected_full_month = _weekdays(month_start, month_end)
    coverage = round(len(source_date_set) / len(expected_dates), 3) if expected_dates else 0.0
    return {
        "status": "complete" if not missing_dates and month_end < as_of.date() else "partial",
        "snapshot_days": len(source_date_set),
        "expected_weekdays": len(expected_full_month),
        "elapsed_weekdays": len(expected_dates),
        "coverage": coverage,
        "source_dates": [value.isoformat() for value in source_dates],
        "missing_dates": missing_dates,
        "effective_end": effective_end.isoformat(),
        "latest_snapshot_at": snapshots[-1].captured_at.strftime("%Y-%m-%d %H:%M:%S"),
        "metric_coverage": _metric_coverage(snapshots),
        "has_gaps": bool(missing_dates),
        "warning": (
            f"缺少 {len(missing_dates)} 个已过去工作日快照，结论需结合覆盖率判断。"
            if missing_dates
            else ""
        ),
    }


def _weekdays(start: date, end: date) -> list[date]:
    if end < start:
        return []
    return [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)).weekday() < 5
    ]


def _weekly_reports_for_month(
    reports: list[dict[str, Any]],
    month_start: date,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for report in reports:
        try:
            week_start = date.fromisoformat(str(report["period"]["start"]))
        except (KeyError, TypeError, ValueError):
            continue
        workdays = [week_start + timedelta(days=offset) for offset in range(5)]
        ownership = Counter(value.replace(day=1) for value in workdays)
        owner = max(ownership, key=lambda value: (ownership[value], value))
        if owner == month_start:
            selected.append(report)
    return sorted(selected, key=lambda item: item["period"]["start"])


def _weekly_highlight(report: dict[str, Any]) -> dict[str, Any]:
    llm = (report.get("llm") or {}).get("content") or {}
    summary = report.get("summary") or {}
    return {
        "report_id": report["report_id"],
        "period": report["period"],
        "source_fingerprint": report.get("source_fingerprint", ""),
        "management_summary": str(
            llm.get("management_summary") or summary.get("management_summary") or ""
        ),
        "trend_analysis": str(
            llm.get("trend_analysis") or summary.get("trend_analysis") or ""
        ),
        "risks": _as_text_list(llm.get("risks") or summary.get("risks") or []),
        "actions": _as_text_list(llm.get("actions") or summary.get("actions") or []),
    }


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _summary(
    platform: str,
    recommendations: list[dict[str, Any]],
    movements: dict[str, Any],
    tags: dict[str, Any],
    quality: dict[str, Any],
    weekly_highlights: list[dict[str, Any]],
) -> dict[str, Any]:
    titles = "、".join(item["title"] for item in recommendations[:3]) or "暂无"
    themes = "、".join(item["tag"] for item in tags["theme_tags"][:5]) or "暂无明显聚集"
    movement = movements["summary"]
    weekly_context = "；".join(
        item["management_summary"] for item in weekly_highlights if item["management_summary"]
    )
    trend_analysis = (
        f"{platform}本月基于{quality['snapshot_days']}个有效工作日快照进行观察，"
        f"重点剧目为{titles}，主要题材标签集中在{themes}。"
        f"月初与月末榜单相比，新上榜{movement['new_entries']}部、下榜{movement['dropped_entries']}部、"
        f"排名上升{movement['rank_up']}部、排名下降{movement['rank_down']}部。"
        "月度推荐分综合在榜覆盖率、平均排名、月末排名、排名动量和可用互动指标趋势，"
        "用于识别整月稳定上行样本，不把单周高点直接视为月度持续趋势。"
    )
    if weekly_context:
        trend_analysis += f"分周管理摘要显示：{weekly_context}"
    if quality["missing_dates"]:
        trend_analysis += (
            f"当前缺少{len(quality['missing_dates'])}个已过去工作日快照，"
            "涉及缺失区间的周间变化仅供参考。"
        )
    risks = ["同平台、同榜单内比较归一化指标，不用于抖音与红果原始数值横向比较。"]
    if quality["missing_dates"]:
        risks.insert(0, quality["warning"])
    actions = [
        "优先复盘月度高覆盖、高排名且周间指标持续上行的剧目。",
        "将连续两周升温的题材标签纳入下月选题观察池。",
    ]
    return {
        "management_summary": (
            f"{platform} 本月重点关注：{titles}。题材趋势：{themes}。"
            f"数据覆盖率 {round(quality['coverage'] * 100, 1)}%。"
        ),
        "trend_analysis": trend_analysis,
        "risks": risks,
        "actions": actions,
        "movement_counts": movement,
    }


def _rank_strength(rank: float, maximum: int) -> float:
    if maximum <= 1:
        return 1.0
    return max(0.0, min(1.0, 1 - (rank - 1) / (maximum - 1)))


def _rank_momentum(first: int, latest: int, maximum: int) -> float:
    if maximum <= 1:
        return 0.5
    return max(0.0, min(1.0, 0.5 + (first - latest) / (2 * (maximum - 1))))


def _normalize(value: int, lower: int, upper: int) -> float:
    if upper == lower:
        return 0.5
    return max(0.0, min(1.0, (value - lower) / (upper - lower)))


def _fingerprint(
    platform: str,
    month_start: date,
    snapshots: list[RankSnapshot],
    weekly_highlights: list[dict[str, Any]],
    status: str,
    effective_end: str,
    script_path: Path,
) -> str:
    snapshot_payload = [
        {
            "captured_at": item.captured_at.isoformat(),
            "source": str(item.source_path),
            "records": item.records,
        }
        for item in snapshots
    ]
    try:
        script_fingerprint = hashlib.sha256(script_path.read_bytes()).hexdigest()
    except OSError:
        script_fingerprint = "missing"
    return hashlib.sha256(
        json.dumps(
            {
                "version": REPORT_VERSION,
                "script": script_fingerprint,
                "platform": platform,
                "month": month_start.isoformat(),
                "status": status,
                "effective_end": effective_end,
                "snapshots": snapshot_payload,
                "weekly_highlights": weekly_highlights,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _decode_report_row(row: sqlite3.Row) -> dict[str, Any]:
    report = json.loads(str(row["payload_json"]))
    report.update(
        {
            "markdown_path": row["markdown_path"],
            "excel_path": row["excel_path"],
            "generated_at": row["generated_at"],
        }
    )
    return report


def _report_content(report: dict[str, Any], key: str) -> Any:
    llm_content = (report.get("llm") or {}).get("content") or {}
    summary = report.get("summary") or {}
    return llm_content.get(key) or summary.get(key) or ([] if key in {"risks", "actions"} else "")


def render_markdown(report: dict[str, Any]) -> str:
    quality = report["data_quality"]
    state = "月度进展" if report["status"] == "partial" else "正式月报"
    lines = [
        f"# {report['platform']} 月度流行趋势报告",
        "",
        f"- 统计周期：{report['period']['start']} 至 {report['period']['end']}",
        f"- 报告状态：{state}",
        f"- 有效快照：{quality['snapshot_days']}/{quality['elapsed_weekdays']} 个已过去工作日",
        f"- 数据覆盖率：{round(quality['coverage'] * 100, 1)}%",
        f"- 来源周报：{', '.join(report['source_weekly_report_ids']) or '暂无完整周报'}",
        "",
        "## 管理摘要",
        "",
        str(_report_content(report, "management_summary")),
        "",
        "## 月度综合趋势",
        "",
        str(_report_content(report, "trend_analysis")),
        "",
        "## 分周回顾",
        "",
    ]
    if report["weekly_highlights"]:
        for item in report["weekly_highlights"]:
            lines.extend(
                [
                    f"### {item['period']['start']} 至 {item['period']['end']}",
                    "",
                    item["management_summary"] or item["trend_analysis"] or "暂无摘要",
                    "",
                ]
            )
    else:
        lines.extend(["暂无已完成周报，月度进展仅基于原始日榜规则计算。", ""])
    lines.extend(
        [
            "## 月度 Top 10",
            "",
            "| 剧目 | 榜单 | 月均排名 | 月末排名 | 在榜 | 推荐分 | 推荐说明 |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    lines.extend(
        f"| {item['title']} | {item['board']} | {item['average_rank']} | "
        f"{'第 ' + str(item['current_rank']) + ' 名' if item['present_at_end'] else '未在榜'} | "
        f"{item['appearances']}/{item['expected_appearances']} | {item['score']} | {item['reason']} |"
        for item in report["recommendations"]
    )
    lines.extend(["", "## 题材标签趋势", "", "| 标签 | 月末覆盖 | 净增 |", "| --- | ---: | ---: |"])
    lines.extend(
        f"| {item['tag']} | {item['count']} | {item['net_change']} |"
        for item in report["tags"]["theme_tags"]
    )
    for title, key in (("风险", "risks"), ("行动建议", "actions")):
        lines.extend(["", f"## {title}", ""])
        values = _as_text_list(_report_content(report, key))
        lines.extend(f"- {value}" for value in values)
    lines.extend(["", "## 来源追溯", "", f"- 有效日期：{', '.join(quality['source_dates'])}"])
    lines.append(f"- 缺失日期：{', '.join(quality['missing_dates']) or '无'}")
    lines.extend(
        f"- 周报 {item['report_id']}：{item['period']['start']} 至 {item['period']['end']}"
        for item in report["weekly_highlights"]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_excel(report: dict[str, Any], path: Path) -> None:
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "管理摘要"
    summary_sheet.append(["平台", report["platform"]])
    summary_sheet.append(["统计月份", report["month"]])
    summary_sheet.append(["报告状态", report["status"]])
    summary_sheet.append(["数据覆盖率", report["data_quality"]["coverage"]])
    summary_sheet.append(["管理摘要", _report_content(report, "management_summary")])
    summary_sheet.append(["月度综合趋势", _report_content(report, "trend_analysis")])
    summary_sheet.column_dimensions["A"].width = 18
    summary_sheet.column_dimensions["B"].width = 100
    for cell in ("B5", "B6"):
        summary_sheet[cell].alignment = Alignment(wrap_text=True, vertical="top")

    weekly_sheet = workbook.create_sheet("分周回顾")
    weekly_sheet.append(["周报ID", "开始日期", "结束日期", "管理摘要", "趋势分析"])
    for item in report["weekly_highlights"]:
        weekly_sheet.append(
            [
                item["report_id"],
                item["period"]["start"],
                item["period"]["end"],
                item["management_summary"],
                item["trend_analysis"],
            ]
        )

    recommendation_sheet = workbook.create_sheet("月度推荐")
    recommendation_sheet.append(
        [
            "剧目",
            "榜单",
            "分类",
            "月均排名",
            "月末排名",
            "最佳排名",
            "在榜天数",
            "推荐分",
            "建议",
            "推荐说明",
        ]
    )
    for item in report["recommendations"]:
        recommendation_sheet.append(
            [
                item["title"],
                item["board"],
                item["category"],
                item["average_rank"],
                item["current_rank"] if item["present_at_end"] else "未在榜",
                item["best_rank"],
                item["appearances"],
                item["score"],
                item["recommendation"],
                item["reason"],
            ]
        )

    tag_sheet = workbook.create_sheet("题材标签")
    tag_sheet.append(["题材标签", "月末覆盖", "净增", "热度贡献", "涉及榜单数"])
    for item in report["tags"]["theme_tags"]:
        tag_sheet.append(
            [
                item["tag"],
                item["count"],
                item["net_change"],
                item["heat_contribution"],
                item["board_count"],
            ]
        )

    source_sheet = workbook.create_sheet("来源追溯")
    source_sheet.append(["有效日期", "、".join(report["data_quality"]["source_dates"])])
    source_sheet.append(["缺失日期", "、".join(report["data_quality"]["missing_dates"])])
    source_sheet.append(["来源周报", "、".join(report["source_weekly_report_ids"])])
    workbook.save(path)
