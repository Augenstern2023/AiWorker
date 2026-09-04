# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Deterministic weekly trend reports built from normalized rank snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment

from analysis.today_rank_service import RankSnapshot, TodayRankService


PLATFORMS = frozenset({"dy", "hg"})
WEEKDAY_COUNT = 5
RECOMMENDATION_LIMIT = 5
REPORT_VERSION = 10
NON_THEME_TAGS = frozenset({"短剧", "漫剧", "真人剧", "AI剧", "AI短剧", "3D", "2D", "竖屏", "横屏"})


class WeeklyReportRepository:
    """Small durable store that keeps report payloads independent of raw artifacts."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekly_reports (
                    report_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    week_start TEXT NOT NULL,
                    week_end TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    excel_path TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    UNIQUE(platform, week_start)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekly_report_cache_state (
                    platform TEXT PRIMARY KEY,
                    source_revision TEXT NOT NULL,
                    script_fingerprint TEXT NOT NULL,
                    checked_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM weekly_reports WHERE report_id = ?", (report_id,)).fetchone()
        return _decode_report_row(row) if row else None

    def list(self, platform: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM weekly_reports"
        params: list[Any] = []
        if platform:
            sql += " WHERE platform = ?"
            params.append(platform)
        sql += " ORDER BY week_start DESC, generated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decode_report_row(row) for row in rows]

    def fingerprints(self, platform: str = "") -> dict[str, str]:
        sql = "SELECT report_id, source_fingerprint FROM weekly_reports"
        params: list[Any] = []
        if platform:
            sql += " WHERE platform = ?"
            params.append(platform)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {str(row["report_id"]): str(row["source_fingerprint"]) for row in rows}

    def exports_available(self, platform: str) -> bool:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT markdown_path, excel_path FROM weekly_reports WHERE platform = ?",
                (platform,),
            ).fetchall()
        return bool(rows) and all(
            Path(str(row["markdown_path"])).is_file() and Path(str(row["excel_path"])).is_file()
            for row in rows
        )

    def get_cache_state(self, platform: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_revision, script_fingerprint, checked_at FROM weekly_report_cache_state WHERE platform = ?",
                (platform,),
            ).fetchone()
        return dict(row) if row else None

    def set_cache_state(
        self,
        platform: str,
        source_revision: str,
        script_fingerprint: str,
        *,
        checked_at: datetime | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO weekly_report_cache_state (platform, source_revision, script_fingerprint, checked_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    source_revision=excluded.source_revision,
                    script_fingerprint=excluded.script_fingerprint,
                    checked_at=excluded.checked_at
                """,
                (
                    platform,
                    source_revision,
                    script_fingerprint,
                    (checked_at or datetime.now()).isoformat(timespec="seconds"),
                ),
            )

    def upsert(self, report: dict[str, Any], markdown_path: Path, excel_path: Path) -> dict[str, Any]:
        generated_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO weekly_reports (
                    report_id, platform, week_start, week_end, source_fingerprint, status,
                    payload_json, markdown_path, excel_path, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, week_start) DO UPDATE SET
                    report_id=excluded.report_id,
                    week_end=excluded.week_end,
                    source_fingerprint=excluded.source_fingerprint,
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    markdown_path=excluded.markdown_path,
                    excel_path=excluded.excel_path,
                    generated_at=excluded.generated_at
                """,
                (
                    report["report_id"], report["platform"], report["period"]["start"], report["period"]["end"],
                    report["source_fingerprint"], report["status"], json.dumps(report, ensure_ascii=False),
                    str(markdown_path), str(excel_path), generated_at,
                ),
            )
        return self.get(report["report_id"]) or report


class WeeklyReportService:
    def __init__(
        self,
        root: str | Path = ".",
        runtime_dir: str | Path = ".runtime",
        analysis_script_path: str | Path | None = None,
        rank_service: TodayRankService | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.runtime_dir = Path(runtime_dir)
        if not self.runtime_dir.is_absolute():
            self.runtime_dir = self.root / self.runtime_dir
        self.analysis_script_path = Path(analysis_script_path or __file__).resolve()
        self.rank_service = rank_service or TodayRankService.from_environment(
            root=self.root
        )
        self.repository = WeeklyReportRepository(self.runtime_dir / "rag" / "weekly_reports.db")
        self.output_dir = self.root / "data" / "reports" / "weekly"

    def build_latest(self, platform: str, as_of: datetime | None = None, with_llm: bool = False) -> dict[str, Any]:
        platform = platform.strip().lower()
        if platform not in PLATFORMS:
            raise ValueError("platform must be dy or hg")
        snapshots = self._complete_week_snapshots(platform, as_of or datetime.now())
        if snapshots is None:
            return {"platform": platform, "status": "insufficient_data"}
        report = self._build_report(platform, snapshots)
        if with_llm:
            report["llm"] = self._llm_summary(report)
        return report

    def generate_latest(self, platform: str, as_of: datetime | None = None, with_llm: bool = True) -> dict[str, Any]:
        platform = platform.strip().lower()
        if platform not in PLATFORMS:
            raise ValueError("platform must be dy or hg")
        snapshots = self._complete_week_snapshots(platform, as_of or datetime.now())
        if snapshots is None:
            return {"platform": platform, "status": "insufficient_data"}
        return self._generate_snapshots(platform, snapshots, with_llm=with_llm)

    def generate_all_available(
        self,
        platform: str = "",
        as_of: datetime | None = None,
        with_llm: bool = True,
    ) -> list[dict[str, Any]]:
        selected_platforms = (platform.strip().lower(),) if platform else tuple(sorted(PLATFORMS))
        if any(value not in PLATFORMS for value in selected_platforms):
            raise ValueError("platform must be dy or hg")
        now = as_of or datetime.now()
        reports: list[dict[str, Any]] = []
        for value in selected_platforms:
            source_revision = self._source_revision(value)
            script_fingerprint = _file_fingerprint(self.analysis_script_path)
            cache_state = self.repository.get_cache_state(value)
            if (
                cache_state
                and cache_state.get("source_revision") == source_revision
                and cache_state.get("script_fingerprint") == script_fingerprint
                and _cache_covers_latest_complete_week(cache_state, now)
                and self.repository.exports_available(value)
                and (not with_llm or self._platform_llm_cache_ready(value))
            ):
                reports.extend(
                    {
                        "report_id": report_id,
                        "platform": value,
                        "status": "unchanged",
                        "cache_status": "hit",
                    }
                    for report_id in self.repository.fingerprints(value)
                )
                continue
            cached_fingerprints = self.repository.fingerprints(value)
            for snapshots in self._complete_weeks_snapshots(value, now):
                report_id = f"{value}-{snapshots[0].captured_at.date().isoformat()}"
                source_fingerprint = self._fingerprint(value, snapshots)
                existing = self.repository.get(report_id)
                exports_available = bool(
                    existing
                    and Path(str(existing.get("markdown_path") or "")).is_file()
                    and Path(str(existing.get("excel_path") or "")).is_file()
                )
                if (
                    cached_fingerprints.get(report_id) == source_fingerprint
                    and exports_available
                    and (not with_llm or (existing.get("llm") or {}).get("status") == "ready")
                ):
                    reports.append({"report_id": report_id, "platform": value, "status": "unchanged", "cache_status": "hit"})
                    continue
                generated = self._generate_snapshots(value, snapshots, with_llm=with_llm)
                reports.append({**generated, "cache_status": "generated"})
            self.repository.set_cache_state(
                value,
                source_revision,
                script_fingerprint,
                checked_at=now,
            )
        return reports

    def _generate_snapshots(self, platform: str, snapshots: list[RankSnapshot], with_llm: bool) -> dict[str, Any]:
        report = self._build_report(platform, snapshots)
        existing = self.repository.get(report["report_id"])
        if (
            existing
            and existing.get("source_fingerprint") == report["source_fingerprint"]
            and Path(str(existing.get("markdown_path") or "")).is_file()
            and Path(str(existing.get("excel_path") or "")).is_file()
            and (not with_llm or (existing.get("llm") or {}).get("status") == "ready")
        ):
            return {**existing, "status": "unchanged"}
        if with_llm:
            report["llm"] = self._llm_summary(report)
        markdown_path, excel_path = self._write_exports(report)
        return self.repository.upsert(report, markdown_path, excel_path)

    def _fingerprint(self, platform: str, snapshots: list[RankSnapshot]) -> str:
        return _fingerprint(platform, snapshots, self.analysis_script_path)

    def _platform_llm_cache_ready(self, platform: str) -> bool:
        reports = self.repository.list(platform)
        return bool(reports) and all(
            (report.get("llm") or {}).get("status") == "ready"
            for report in reports
        )

    def _source_revision(self, platform: str) -> str:
        return self.rank_service.source_revision(platform)

    def generate_all_latest(self, as_of: datetime | None = None, with_llm: bool = True) -> list[dict[str, Any]]:
        return [self.generate_latest(platform, as_of=as_of, with_llm=with_llm) for platform in sorted(PLATFORMS)]

    def list_reports(
        self,
        platform: str = "",
        as_of: datetime | None = None,
        ensure_generated: bool = True,
    ) -> list[dict[str, Any]]:
        now = as_of or datetime.now()
        if ensure_generated:
            generation_results = self.generate_all_available(platform=platform, as_of=now, with_llm=False)
        else:
            generation_results = []
        cache_status_by_report = {
            str(item.get("report_id")): str(item.get("cache_status"))
            for item in generation_results
            if item.get("report_id") and item.get("cache_status")
        }
        reports = self.repository.list(platform=platform)
        latest_week_by_platform: dict[str, str] = {}
        for value in (platform,) if platform else PLATFORMS:
            generated_periods = [
                report["period"]["start"]
                for report in reports
                if report.get("platform") == value and report.get("period", {}).get("start")
            ]
            if generated_periods:
                latest_week_by_platform[value] = max(generated_periods)
        for report in reports:
            quality = dict(report.get("data_quality") or {})
            quality["is_stale"] = bool(latest_week_by_platform.get(report["platform"], report["period"]["start"]) > report["period"]["start"])
            report["data_quality"] = quality
            report["cache_status"] = cache_status_by_report.get(report["report_id"], "hit")
        return reports

    def get_report(self, report_id: str, as_of: datetime | None = None) -> dict[str, Any] | None:
        report = self.repository.get(report_id)
        if report is None:
            return None
        return next(
            (
                item
                for item in self.list_reports(
                    report["platform"], as_of=as_of, ensure_generated=False
                )
                if item["report_id"] == report_id
            ),
            report,
        )

    def _complete_week_snapshots(self, platform: str, as_of: datetime) -> list[RankSnapshot] | None:
        weeks = self._complete_weeks_snapshots(platform, as_of)
        return weeks[0] if weeks else None

    def _complete_weeks_snapshots(self, platform: str, as_of: datetime) -> list[list[RankSnapshot]]:
        by_day: dict[date, RankSnapshot] = {}
        for snapshot in self.rank_service.list_snapshots(platform):
            snapshot_as_of = as_of
            if snapshot.captured_at.tzinfo is not None and as_of.tzinfo is None:
                snapshot_as_of = as_of.replace(tzinfo=snapshot.captured_at.tzinfo)
            elif snapshot.captured_at.tzinfo is None and as_of.tzinfo is not None:
                snapshot_as_of = as_of.replace(tzinfo=None)
            if snapshot.captured_at > snapshot_as_of:
                continue
            snapshot_day = snapshot.captured_at.date()
            if snapshot_day not in by_day or snapshot.captured_at > by_day[snapshot_day].captured_at:
                by_day[snapshot_day] = snapshot
        weeks: dict[date, dict[date, RankSnapshot]] = defaultdict(dict)
        for snapshot_day, snapshot in by_day.items():
            week_start = snapshot_day - timedelta(days=snapshot_day.weekday())
            if week_start + timedelta(days=6) >= as_of.date():
                continue
            weeks[week_start][snapshot_day] = snapshot
        complete_weeks: list[list[RankSnapshot]] = []
        for week_start in sorted(weeks, reverse=True):
            days = weeks[week_start]
            weekdays = [week_start + timedelta(days=offset) for offset in range(WEEKDAY_COUNT)]
            if all(day in days for day in weekdays):
                complete_weeks.append([days[day] for day in weekdays])
        return complete_weeks

    def _build_report(self, platform: str, snapshots: list[RankSnapshot]) -> dict[str, Any]:
        week_start = snapshots[0].captured_at.date()
        week_end = week_start + timedelta(days=6)
        boards = _group_boards(snapshots)
        recommendations = _recommendations(boards)
        movements = _movements(boards)
        tags = _tag_analysis(boards)
        heat_analysis = _heat_analysis(snapshots)
        recommendation_metrics = _recommendation_metrics(boards, recommendations, snapshots)
        source_dates = [snapshot.captured_at.strftime("%Y-%m-%d") for snapshot in snapshots]
        data_quality = {
            "status": "complete",
            "snapshot_days": len(snapshots),
            "expected_weekdays": WEEKDAY_COUNT,
            "source_dates": source_dates,
            "latest_snapshot_at": snapshots[-1].captured_at.strftime("%Y-%m-%d %H:%M:%S"),
            "metric_coverage": _metric_coverage(snapshots),
        }
        report_id = f"{platform}-{week_start.isoformat()}"
        report = {
            "report_id": report_id,
            "platform": platform,
            "status": "ready",
            "period": {"start": week_start.isoformat(), "end": week_end.isoformat()},
            "source_fingerprint": self._fingerprint(platform, snapshots),
            "data_quality": data_quality,
            "summary": _summary(platform, recommendations, movements, tags, data_quality),
            "recommendations": recommendations,
            "tags": tags,
            "movements": movements,
            "heat_analysis": heat_analysis,
            "recommendation_metrics": recommendation_metrics,
            "llm": {"status": "not_requested", "content": {}},
        }
        return report

    def _llm_summary(self, report: dict[str, Any]) -> dict[str, Any]:
        try:
            import config
            from analysis.llm import create_llm_provider_from_config

            payload = {
                "platform": report["platform"], "period": report["period"], "data_quality": report["data_quality"],
                "recommendations": report["recommendations"], "recommendation_metrics": report["recommendation_metrics"],
                "theme_tags": report["tags"]["theme_tags"], "movements": report["movements"],
                "instruction": (
                    "只根据输入数据输出合法 JSON，字段为 management_summary、trend_analysis、risks、actions。"
                    "trend_analysis 必须为不少于100个中文字符的趋势观察与综合分析，必须结合推荐剧目的题材标签、简介、"
                    "热度/点赞/收藏/评论等可用指标、周均趋势、排名和在榜稳定性；说明上升或回落的观察依据。"
                    "趋势正文至少点名3部推荐剧目；每部都要把已提供的题材标签或简介原文信息，同可用指标和排名/在榜天数相连。"
                    "存在点赞、收藏、评论时要说明其可用性或变化，不得只把热度当作唯一依据；不得把未提供的内容类型、剧情或受众当作事实。"
                    "不得编造未提供的剧情、受众、平台或因果事实，不得跨平台比较原始数值。"
                ),
            }
            provider = create_llm_provider_from_config(config)
            content = asyncio.run(provider.complete_json([
                {"role": "system", "content": "你是短剧管理层数据分析助手，只输出合法 JSON。"},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]))
            if not isinstance(content, dict):
                raise ValueError("LLM response must be an object")
            trend_analysis = str(content.get("trend_analysis") or "").strip()
            if len("".join(trend_analysis.split())) < 100:
                raise ValueError("LLM trend_analysis must contain at least 100 characters")
            return {"status": "ready", "content": {key: content.get(key, "") for key in ("management_summary", "trend_analysis", "risks", "actions")}}
        except Exception as exc:
            return {"status": "unavailable", "content": {}, "error": str(exc)[:300]}

    def _write_exports(self, report: dict[str, Any]) -> tuple[Path, Path]:
        output = self.output_dir / report["platform"] / report["period"]["start"]
        output.mkdir(parents=True, exist_ok=True)
        period_label = _export_period_label(report["period"])
        platform_label = {"dy": "抖音", "hg": "红果"}.get(str(report["platform"]), str(report["platform"]))
        export_name = f"{platform_label}周度流行趋势报告_{period_label}"
        markdown_path = output / f"{export_name}.md"
        excel_path = output / f"{export_name}.xlsx"
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        write_excel(report, excel_path)
        return markdown_path, excel_path


def _group_boards(snapshots: list[RankSnapshot]) -> dict[str, list[dict[str, dict[str, Any]]]]:
    daily_boards: list[dict[str, dict[str, dict[str, Any]]]] = []
    board_keys: set[str] = set()
    for snapshot in snapshots:
        per_board: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for record in snapshot.records:
            identity = str(record.get("id") or record.get("record_id") or record.get("title") or "")
            if identity:
                per_board[_board_key(record)][identity] = record
        board_keys.update(per_board)
        daily_boards.append(per_board)
    return {
        board: [daily.get(board, {}) for daily in daily_boards]
        for board in sorted(board_keys)
    }


def _recommendations(boards: dict[str, list[dict[str, dict[str, Any]]]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for board, days in boards.items():
        if not days or not days[-1]:
            continue
        last = days[-1]
        max_rank = max([int(record.get("rank") or 0) for records in days for record in records.values()] or [1])
        raw: list[dict[str, Any]] = []
        for identity, current in last.items():
            history = [records.get(identity) for records in days]
            first = next((record for record in history if record), None)
            appearances = [record for record in history if record]
            current_rank = int(current.get("rank") or max_rank)
            first_rank = int(first.get("rank") or current_rank) if first else current_rank
            metric_name, metric_delta = _metric_delta(first, current)
            raw.append({
                "identity": identity, "board": board, "title": str(current.get("title") or ""), "category": str(current.get("category") or "未知类型"),
                "description": str(current.get("description") or ""), "tags": _record_tags(current),
                "metrics": {name: _number(current.get(name)) for name in ("heat", "like_count", "favorite_count", "comment_count")},
                "current_rank": current_rank, "best_rank": min(int(record.get("rank") or max_rank) for record in appearances),
                "rank_change": first_rank - current_rank, "rank_strength": _rank_strength(current_rank, max_rank),
                "rank_momentum": _rank_momentum(first_rank, current_rank, max_rank) if first else None,
                "metric": metric_name, "metric_delta": metric_delta, "persistence": len(appearances) / len(days),
            })
        _apply_recommendation_scores(raw)
        candidates.extend(raw)
    candidates.sort(key=lambda item: (-item["score"], item["current_rank"], item["title"]))
    deduplicated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        key = item["title"]
        if key in seen:
            continue
        seen.add(key)
        item["recommendation"] = "可关注" if item["score"] >= 75 else "观察" if item["score"] >= 50 else "风险"
        item["reason"] = _recommendation_reason(item)
        deduplicated.append(item)
        if len(deduplicated) == RECOMMENDATION_LIMIT:
            break
    return deduplicated


def _apply_recommendation_scores(items: list[dict[str, Any]]) -> None:
    metric_values = [item["metric_delta"] for item in items if item["metric_delta"] is not None]
    lower, upper = (min(metric_values), max(metric_values)) if metric_values else (0, 0)
    for item in items:
        components = [(35, item["rank_strength"]), (15, item["persistence"])]
        if item["rank_momentum"] is not None:
            components.append((30, item["rank_momentum"]))
        if item["metric_delta"] is not None:
            normalized = 1.0 if upper == lower and item["metric_delta"] > 0 else _normalize(item["metric_delta"], lower, upper)
            components.append((20, normalized))
        weight = sum(part[0] for part in components)
        item["score"] = round(100 * sum(weight * value for weight, value in components) / weight, 1)


def _recommendation_reason(item: dict[str, Any]) -> str:
    parts = [f"当前第 {item['current_rank']} 名"]
    rank_change = int(item.get("rank_change") or 0)
    if rank_change > 0:
        parts.append(f"较周初上升 {rank_change} 名")
    elif rank_change < 0:
        parts.append(f"较周初下降 {abs(rank_change)} 名")
    else:
        parts.append("较周初排名持平")
    persistence = float(item.get("persistence") or 0)
    parts.append(f"连续在榜 {round(persistence * 5)}/5 天")
    metric_delta = item.get("metric_delta")
    metric_labels = {"heat": "热度", "like_count": "点赞", "favorite_count": "收藏", "comment_count": "评论"}
    if metric_delta is not None:
        label = metric_labels.get(str(item.get("metric") or ""), "互动")
        if metric_delta > 0:
            parts.append(f"{label}较周初增长")
        elif metric_delta < 0:
            parts.append(f"{label}较周初回落")
    return "；".join(parts)


def _movements(boards: dict[str, list[dict[str, dict[str, Any]]]]) -> dict[str, Any]:
    result: dict[str, list[dict[str, Any]]] = {"new_entries": [], "dropped_entries": [], "rank_up": [], "rank_down": []}
    for board, days in boards.items():
        for previous, current in zip(days, days[1:]):
            for identity in set(current) - set(previous):
                result["new_entries"].append(_movement_item(current[identity], board))
            for identity in set(previous) - set(current):
                result["dropped_entries"].append(_movement_item(previous[identity], board))
            for identity in set(previous) & set(current):
                delta = int(previous[identity].get("rank") or 0) - int(current[identity].get("rank") or 0)
                if delta > 0:
                    result["rank_up"].append({**_movement_item(current[identity], board), "rank_delta": delta})
                elif delta < 0:
                    result["rank_down"].append({**_movement_item(current[identity], board), "rank_delta": delta})
    for key, values in result.items():
        if key in {"rank_up", "rank_down"}:
            values.sort(key=lambda item: (-item["rank_delta"] if key == "rank_up" else item["rank_delta"], item["rank"], item["title"]))
        else:
            values.sort(key=lambda item: (item["rank"], item["title"]))
        result[key] = values[:20]
    result["summary"] = {key: len(value) for key, value in result.items() if isinstance(value, list)}
    return result


def _tag_analysis(boards: dict[str, list[dict[str, dict[str, Any]]]]) -> dict[str, list[dict[str, Any]]]:
    category_rows: list[dict[str, Any]] = []
    tag_rows: list[dict[str, Any]] = []
    for board, days in boards.items():
        first, last = days[0], days[-1]
        for kind, getter, target in (("category", lambda record: [str(record.get("category") or "未知类型")], category_rows), ("topic_tags", _record_tags, tag_rows)):
            start = _tag_counts(first, getter)
            end = _tag_counts(last, getter)
            heat_by_tag = _tag_heat(last, getter)
            for tag in sorted(set(start) | set(end)):
                target.append({"board": board, "tag": tag, "start_count": start[tag], "end_count": end[tag], "net_change": end[tag] - start[tag], "heat_contribution": heat_by_tag.get(tag, 0)})
    for rows in (category_rows, tag_rows):
        rows.sort(key=lambda item: (-item["net_change"], -item["end_count"], item["tag"]))
    theme_tags: dict[str, dict[str, Any]] = {}
    for row in tag_rows:
        tag = row["tag"]
        if not _is_theme_tag(tag):
            continue
        item = theme_tags.setdefault(tag, {"tag": tag, "count": 0, "net_change": 0, "heat_contribution": 0, "board_count": 0})
        item["count"] += row["end_count"]
        item["net_change"] += row["net_change"]
        item["heat_contribution"] += row["heat_contribution"]
        item["board_count"] += 1
    theme_rows = sorted(theme_tags.values(), key=lambda item: (-item["count"], -item["net_change"], item["tag"]))
    return {"categories": category_rows[:30], "topic_tags": tag_rows[:30], "theme_tags": theme_rows[:30]}


def _heat_analysis(snapshots: list[RankSnapshot]) -> dict[str, Any]:
    boards = _group_boards(snapshots)
    board_indices: list[list[float]] = []
    metric_names: set[str] = set()
    for days in boards.values():
        metric = next(
            (
                name
                for name in ("heat", "like_count", "favorite_count", "comment_count")
                if any(_number(record.get(name)) > 0 for records in days for record in records.values())
            ),
            None,
        )
        if metric is None:
            continue
        values = [sum(_number(record.get(metric)) for record in records.values()) for records in days]
        baseline = values[0] if values else 0
        if baseline <= 0:
            continue
        metric_names.add(metric)
        board_indices.append([round(value * 100 / baseline, 1) for value in values])
    points = [
        {
            "date": snapshot.captured_at.strftime("%Y-%m-%d"),
            "value": round(sum(index[day] for index in board_indices) / len(board_indices), 1) if board_indices else 0.0,
        }
        for day, snapshot in enumerate(snapshots)
    ]
    metric = next(iter(metric_names)) if len(metric_names) == 1 else "mixed"
    metric_labels = {"heat": "热度", "like_count": "点赞", "favorite_count": "收藏", "comment_count": "评论", "mixed": "热度/互动"}
    label = metric_labels[metric]
    return {
        "metric": metric,
        "metric_label": label,
        "unit": "index",
        "source": "rank_snapshot",
        "source_label": f"来源：平台榜单快照{label}字段；各榜单首日=100后取均值",
        "points": points,
        "change": round(points[-1]["value"] - points[0]["value"], 1) if points else 0.0,
    }


def _recommendation_metrics(
    boards: dict[str, list[dict[str, dict[str, Any]]]],
    recommendations: list[dict[str, Any]],
    snapshots: list[RankSnapshot],
) -> dict[str, Any]:
    metric_labels = {"heat": "热度", "like_count": "点赞", "favorite_count": "收藏", "comment_count": "评论"}
    candidates: list[tuple[dict[str, Any], list[dict[str, Any] | None]]] = []
    for item in recommendations:
        days = boards.get(str(item.get("board") or ""), [])
        identity = str(item.get("identity") or "")
        records = [day.get(identity) if identity else None for day in days]
        if records:
            candidates.append((item, records))

    metric_candidates = {
        name: [
            (item, records)
            for item, records in candidates
            if all(record is not None and _number(record.get(name)) > 0 for record in records)
        ]
        for name in metric_labels
    }
    selected_metric = max(
        metric_labels,
        key=lambda name: (len(metric_candidates[name]), -list(metric_labels).index(name)),
    )
    selected = metric_candidates[selected_metric]
    series: list[dict[str, Any]] = []
    for item, records in selected:
        values = [_number(record.get(selected_metric)) for record in records if record is not None]
        weekly_average = round(sum(values) / len(values), 1)
        series.append(
            {
                "title": item["title"],
                "board": item["board"],
                "metric": selected_metric,
                "metric_label": metric_labels[selected_metric],
                "weekly_average": weekly_average,
                "values": values,
                "index_values": [round(value * 100 / weekly_average, 1) for value in values],
            }
        )
    source_label = (
        f"来源：推荐剧目的完整非零{metric_labels[selected_metric]}指标；全部曲线统一为{metric_labels[selected_metric]}，每部剧周均=100"
        if series else "暂无同时满足完整非零要求的推荐剧目指标"
    )
    return {
        "unit": "weekly_average_index",
        "source": "recommended_dramas",
        "source_label": source_label,
        "points": [snapshot.captured_at.strftime("%Y-%m-%d") for snapshot in snapshots],
        "series": series,
    }


def _summary(platform: str, recommendations: list[dict[str, Any]], movements: dict[str, Any], tags: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    hot_titles = "、".join(item["title"] for item in recommendations[:3]) or "暂无"
    tag = next((item["tag"] for item in tags["theme_tags"] if item["net_change"] > 0), "暂无明显上升标签")
    return {
        "management_summary": f"{platform} 本周重点关注：{hot_titles}。标签趋势：{tag}。",
        "trend_analysis": _rule_trend_analysis(platform, recommendations, tags, movements),
        "actions": ["优先复盘可关注剧目的开篇钩子与情绪节点", "将上升标签纳入下周选题观察池"],
        "risks": ["指标仅在同平台同榜单内归一化，不用于跨平台热度比较"] if quality["metric_coverage"] < 1 else [],
        "movement_counts": movements["summary"],
    }


def _rule_trend_analysis(
    platform: str,
    recommendations: list[dict[str, Any]],
    tags: dict[str, Any],
    movements: dict[str, Any],
) -> str:
    metric_labels = {"heat": "热度", "like_count": "点赞", "favorite_count": "收藏", "comment_count": "评论"}
    focus: list[str] = []
    for item in recommendations[:3]:
        tags_text = "、".join(item.get("tags") or []) or "暂无标签"
        description = str(item.get("description") or "").strip()[:42] or "暂无简介"
        metric_values = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        metrics_text = "、".join(
            f"{label}{_number(metric_values.get(name))}"
            for name, label in metric_labels.items()
            if _number(metric_values.get(name)) > 0
        ) or "暂无可用热度或互动指标"
        focus.append(
            f"《{item['title']}》题材标签为{tags_text}，简介呈现{description}，可用指标为{metrics_text}；{item['reason']}，推荐分{item['score']}，可作为题材、互动和排名联动的重点样本"
        )
    themes = "、".join(item["tag"] for item in tags["theme_tags"][:5]) or "暂无明显题材聚集"
    summary = movements["summary"]
    analysis = (
        f"{platform}本周的推荐剧目观察显示，{'；'.join(focus) or '暂无足够推荐样本'}。"
        f"题材标签主要集中在{themes}，应结合各剧的在榜天数和排名变化判断题材是否形成持续供给，而非仅以单日指标判断。"
        f"本周榜单出现新上榜{summary['new_entries']}部、下榜{summary['dropped_entries']}部、上升{summary['rank_up']}部、下降{summary['rank_down']}部，"
        "建议优先复盘排名上行且指标高于周均的剧目简介、题材组合与内容钩子，并将排名回落或在榜稳定性不足的同类标签列入观察名单。"
    )
    return analysis if len(analysis) >= 100 else analysis + "当前数据样本有限，后续应在连续周报中持续验证上述题材与互动指标的同步变化。"


def _metric_coverage(snapshots: list[RankSnapshot]) -> float:
    records = [record for snapshot in snapshots for record in snapshot.records]
    if not records:
        return 0.0
    values = sum(1 for record in records for metric in ("heat", "like_count", "favorite_count", "comment_count") if _number(record.get(metric)) > 0)
    return round(values / (len(records) * 4), 3)


def _record_tags(record: dict[str, Any]) -> list[str]:
    tags = [str(tag).strip() for tag in record.get("topic_tags") or [] if str(tag).strip()]
    subject = str(record.get("subject") or "")
    tags.extend(part.strip() for part in subject.replace("/", "、").replace("|", "、").split("、") if part.strip())
    if tags:
        return list(dict.fromkeys(tags))
    text = f"{record.get('title', '')} {record.get('description', '')}"
    vocabulary = {"逆袭": ("逆袭", "翻身", "打脸"), "家庭": ("父", "母", "家庭", "女儿", "儿子"), "悬疑": ("悬疑", "真相", "秘密", "案件"), "复仇": ("复仇", "报仇", "重生"), "甜宠": ("甜", "宠", "霸总")}
    return [tag for tag, words in vocabulary.items() if any(word in text for word in words)]


def _is_theme_tag(tag: str) -> bool:
    normalized = tag.strip().replace(" ", "")
    return bool(normalized) and normalized not in NON_THEME_TAGS


def _metric_delta(first: dict[str, Any] | None, current: dict[str, Any]) -> tuple[str, int | None]:
    if first is None:
        return "", None
    for metric in ("heat", "like_count", "favorite_count", "comment_count"):
        before, after = _number(first.get(metric)), _number(current.get(metric))
        if before or after:
            return metric, after - before
    return "", None


def _tag_counts(records: dict[str, dict[str, Any]], getter) -> Counter[str]:
    return Counter(tag for record in records.values() for tag in getter(record))


def _tag_heat(records: dict[str, dict[str, Any]], getter) -> Counter[str]:
    values: Counter[str] = Counter()
    for record in records.values():
        heat = _number(record.get("heat")) or _number(record.get("like_count"))
        for tag in getter(record):
            values[tag] += heat
    return values


def _movement_item(record: dict[str, Any], board: str) -> dict[str, Any]:
    return {"board": board, "title": str(record.get("title") or ""), "rank": int(record.get("rank") or 0), "category": str(record.get("category") or "未知类型"), "tags": _record_tags(record)}


def _board_key(record: dict[str, Any]) -> str:
    return str(record.get("rank_list") or record.get("group") or record.get("tab") or "榜单")


def _rank_strength(rank: int, maximum: int) -> float:
    return 1.0 if maximum <= 1 else max(0.0, min(1.0, 1 - (rank - 1) / (maximum - 1)))


def _rank_momentum(first: int, current: int, maximum: int) -> float:
    if maximum <= 1:
        return 0.5
    return max(0.0, min(1.0, 0.5 + (first - current) / (2 * (maximum - 1))))


def _normalize(value: int, lower: int, upper: int) -> float:
    return 0.5 if upper == lower else max(0.0, min(1.0, (value - lower) / (upper - lower)))


def _number(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _export_period_label(period: dict[str, Any]) -> str:
    start = date.fromisoformat(str(period["start"]))
    end = date.fromisoformat(str(period["end"]))
    return f"{start.month}月{start.day}日-{end.month}月{end.day}日"


def _fingerprint(
    platform: str,
    snapshots: list[RankSnapshot],
    analysis_script_path: Path | None = None,
) -> str:
    payload = [{"captured_at": item.captured_at.isoformat(), "source": str(item.source_path), "records": item.records} for item in snapshots]
    script_fingerprint = _file_fingerprint(analysis_script_path or Path(__file__))
    return hashlib.sha256(json.dumps({"version": REPORT_VERSION, "script": script_fingerprint, "platform": platform, "snapshots": payload}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _file_fingerprint(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def _cache_covers_latest_complete_week(
    cache_state: dict[str, str], as_of: datetime
) -> bool:
    try:
        checked_at = datetime.fromisoformat(str(cache_state["checked_at"]))
    except (KeyError, TypeError, ValueError):
        return False
    return _latest_complete_week_start(checked_at) == _latest_complete_week_start(as_of)


def _latest_complete_week_start(value: datetime) -> date:
    current_week_start = value.date() - timedelta(days=value.weekday())
    return current_week_start - timedelta(days=7)


def _decode_report_row(row: sqlite3.Row) -> dict[str, Any]:
    report = json.loads(str(row["payload_json"]))
    report.update({"markdown_path": row["markdown_path"], "excel_path": row["excel_path"], "generated_at": row["generated_at"]})
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['platform']} 周度流行趋势报告", "", f"- 统计周期：{report['period']['start']} 至 {report['period']['end']}", f"- 数据快照：{report['data_quality']['snapshot_days']} 个工作日", "", "## 管理摘要", "", _report_management_summary(report), "", "## 综合趋势观察", "", _report_trend_analysis(report), "", "## 热剧推荐", "", "| 剧目 | 榜单 | 当前排名 | 推荐分 | 建议 | 推荐说明 |", "| --- | --- | ---: | ---: | --- | --- |"]
    lines.extend(f"| {item['title']} | {item['board']} | {item['current_rank']} | {item['score']} | {item['recommendation']} | {item['reason']} |" for item in report["recommendations"])
    lines.extend(["", "## 题材标签趋势", "", "| 标签 | 榜单覆盖 | 净增 |", "| --- | ---: | ---: |"])
    lines.extend(f"| {item['tag']} | {item['count']} | {item['net_change']} |" for item in report["tags"]["theme_tags"])
    return "\n".join(lines).rstrip() + "\n"


def write_excel(report: dict[str, Any], path: Path) -> None:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "管理摘要"
    summary.append(["平台", report["platform"]])
    summary.append(["统计周期", f"{report['period']['start']} 至 {report['period']['end']}"])
    summary.append(["摘要", _report_management_summary(report)])
    summary.append(["综合趋势观察", _report_trend_analysis(report)])
    summary.column_dimensions["A"].width = 18
    summary.column_dimensions["B"].width = 100
    summary["B4"].alignment = Alignment(wrap_text=True, vertical="top")
    recommendations = workbook.create_sheet("热剧推荐")
    recommendations.append(["剧目", "榜单", "分类", "当前排名", "最佳排名", "排名变化", "推荐分", "建议", "标签", "推荐说明"])
    for item in report["recommendations"]:
        recommendations.append([item["title"], item["board"], item["category"], item["current_rank"], item["best_rank"], item["rank_change"], item["score"], item["recommendation"], "、".join(item["tags"]), item["reason"]])
    tags = workbook.create_sheet("题材标签趋势")
    tags.append(["题材标签", "榜单覆盖", "净增", "热度贡献", "涉及榜单数"])
    for item in report["tags"]["theme_tags"]:
        tags.append([item["tag"], item["count"], item["net_change"], item["heat_contribution"], item["board_count"]])
    workbook.save(path)


def _report_management_summary(report: dict[str, Any]) -> str:
    llm_content = (report.get("llm") or {}).get("content") or {}
    return str(llm_content.get("management_summary") or (report.get("summary") or {}).get("management_summary") or "")


def _report_trend_analysis(report: dict[str, Any]) -> str:
    llm_content = (report.get("llm") or {}).get("content") or {}
    return str(llm_content.get("trend_analysis") or (report.get("summary") or {}).get("trend_analysis") or "")
