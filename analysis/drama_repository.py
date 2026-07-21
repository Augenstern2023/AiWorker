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
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from rag.loaders import load_job_artifacts
from rag.normalizer import normalize
from rag.schema import Artifact, StandardDocument


class DramaRepository:
    def __init__(self, db_path: str | Path = ".runtime/rag/analytics.db", root: str | Path = "."):
        self.db_path = Path(db_path)
        self.root = Path(root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_jobs (
                    job_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    crawler_type TEXT,
                    artifact_count INTEGER NOT NULL DEFAULT 0,
                    drama_count INTEGER NOT NULL DEFAULT 0,
                    comment_count INTEGER NOT NULL DEFAULT 0,
                    transcript_count INTEGER NOT NULL DEFAULT 0,
                    indexed INTEGER NOT NULL DEFAULT 0,
                    synced_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (platform, job_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_dramas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT,
                    rank INTEGER,
                    heat INTEGER,
                    like_count INTEGER,
                    favorite_count INTEGER,
                    comment_count INTEGER,
                    captured_at TEXT,
                    summary TEXT,
                    selling_points_json TEXT NOT NULL,
                    hook_points_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    synced_at REAL NOT NULL,
                    UNIQUE(platform, job_id, source_path, record_id, title)
                )
                """
            )
            _ensure_column(conn, "analytics_dramas", "captured_at", "TEXT")
            _backfill_drama_captured_at(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    value INTEGER NOT NULL,
                    captured_at TEXT NOT NULL,
                    source_path TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    tag_type TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    title TEXT,
                    source_path TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    title TEXT,
                    content TEXT NOT NULL,
                    sentiment TEXT NOT NULL,
                    source_path TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_sync_state (
                    platform TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    synced_at REAL NOT NULL,
                    PRIMARY KEY (platform, job_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_dramas_platform ON analytics_dramas(platform)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_dramas_category ON analytics_dramas(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_tags_type ON analytics_tags(tag_type)")

    def sync_job(self, job_id: str, platform: str, rebuild: bool = False) -> dict[str, Any]:
        if platform not in {"dy", "hg"}:
            raise ValueError("platform must be dy or hg")
        artifacts = load_job_artifacts(job_id=job_id, platform=platform, root=self.root)
        synced_at = time.time()
        drama_rows: list[dict[str, Any]] = []
        comment_rows: list[dict[str, str]] = []
        tag_rows: list[dict[str, str]] = []
        snapshot_rows: list[dict[str, Any]] = []
        transcript_count = 0

        for artifact in artifacts:
            if artifact.source_type == "asr":
                transcript_count += 1
            for document in normalize(artifact, platform):
                row = self._drama_row_from_document(artifact, document, synced_at)
                if row:
                    drama_rows.append(row)
                    snapshot_rows.extend(_snapshot_rows(row))
                    tag_rows.extend(_tag_rows(row))
            raw_rows = _extract_raw_rows(artifact)
            comment_rows.extend(raw_rows["comments"])
            tag_rows.extend(raw_rows["tags"])
            for analysis_row in raw_rows["analysis_rows"]:
                analysis_row["synced_at"] = synced_at
                drama_rows.append(analysis_row)
                tag_rows.extend(_tag_rows(analysis_row))

        with self._connect() as conn:
            self._delete_job_rows(conn, job_id, platform)
            for row in drama_rows:
                self._upsert_drama(conn, row)
            conn.executemany(
                """
                INSERT INTO analytics_snapshots (platform, job_id, title, metric, value, captured_at, source_path)
                VALUES (:platform, :job_id, :title, :metric, :value, :captured_at, :source_path)
                """,
                snapshot_rows,
            )
            conn.executemany(
                """
                INSERT INTO analytics_tags (platform, job_id, tag_type, tag, title, source_path)
                VALUES (:platform, :job_id, :tag_type, :tag, :title, :source_path)
                """,
                _dedupe_tag_rows(tag_rows),
            )
            conn.executemany(
                """
                INSERT INTO analytics_comments (platform, job_id, title, content, sentiment, source_path)
                VALUES (:platform, :job_id, :title, :content, :sentiment, :source_path)
                """,
                comment_rows,
            )
            drama_count = len({row["title"] for row in drama_rows if row.get("title")})
            conn.execute(
                """
                INSERT INTO analytics_jobs (
                    job_id, platform, artifact_count, drama_count, comment_count,
                    transcript_count, indexed, synced_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, job_id) DO UPDATE SET
                    artifact_count=excluded.artifact_count,
                    drama_count=excluded.drama_count,
                    comment_count=excluded.comment_count,
                    transcript_count=excluded.transcript_count,
                    indexed=excluded.indexed,
                    synced_at=excluded.synced_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    job_id,
                    platform,
                    len(artifacts),
                    drama_count,
                    len(comment_rows),
                    transcript_count,
                    int(_is_indexed(self.root, job_id)),
                    synced_at,
                    json.dumps({"source": "analytics_sync"}, ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                INSERT INTO analytics_sync_state (platform, job_id, status, message, synced_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, job_id) DO UPDATE SET
                    status=excluded.status,
                    message=excluded.message,
                    synced_at=excluded.synced_at
                """,
                (platform, job_id, "success", "", synced_at),
            )
        return {
            "status": "success",
            "platform": platform,
            "job_id": job_id,
            "artifact_count": len(artifacts),
            "drama_count": drama_count,
            "comment_count": len(comment_rows),
            "transcript_count": transcript_count,
            "synced_at": synced_at,
        }

    def sync_all_jobs(self, platforms: list[str] | None = None) -> dict[str, Any]:
        selected_platforms = platforms or ["dy", "hg"]
        results = []
        errors = []
        for platform in selected_platforms:
            if platform not in {"dy", "hg"}:
                continue
            jobs_dir = self.root / "data" / platform / "jobs"
            if not jobs_dir.exists():
                continue
            for job_dir in sorted([path for path in jobs_dir.iterdir() if path.is_dir()], reverse=True):
                try:
                    result = self.sync_job(job_dir.name, platform, rebuild=True)
                    if result["artifact_count"]:
                        results.append(result)
                except Exception as exc:
                    errors.append(f"{platform}/{job_dir.name}: {exc}")
        return {
            "status": "success" if not errors else "partial",
            "synced_jobs": len(results),
            "drama_count": sum(item.get("drama_count", 0) for item in results),
            "artifact_count": sum(item.get("artifact_count", 0) for item in results),
            "results": results,
            "errors": errors,
        }

    def delete_job(self, job_id: str, platform: str) -> None:
        with self._connect() as conn:
            self._delete_job_rows(conn, job_id, platform)

    def _delete_job_rows(self, conn: sqlite3.Connection, job_id: str, platform: str) -> None:
        for table in (
            "analytics_jobs",
            "analytics_dramas",
            "analytics_snapshots",
            "analytics_tags",
            "analytics_comments",
            "analytics_sync_state",
        ):
            conn.execute(f"DELETE FROM {table} WHERE job_id = ? AND platform = ?", (job_id, platform))

    def list_dramas(
        self,
        platform: str = "",
        category: str = "",
        sort: str = "heat",
        limit: int = 50,
        date: str = "",
    ) -> list[dict[str, Any]]:
        order_by = {
            "rank": "rank ASC NULLS LAST",
            "comments": "comment_count DESC",
            "likes": "like_count DESC",
            "heat": (
                "CASE "
                "WHEN heat > 0 THEN heat "
                "WHEN like_count > 0 THEN like_count "
                "WHEN comment_count > 0 THEN comment_count "
                "ELSE 0 END DESC, synced_at DESC"
            ),
            "synced": "synced_at DESC",
        }.get(sort, "CASE WHEN heat > 0 THEN heat WHEN like_count > 0 THEN like_count WHEN comment_count > 0 THEN comment_count ELSE 0 END DESC, synced_at DESC")
        where, params = _where_clause(platform=platform, category=category, captured_at=date)
        sql = f"""
            SELECT platform, job_id, source_path, record_id, title, category, rank, heat,
                   like_count, favorite_count, comment_count, summary,
                   captured_at, selling_points_json, hook_points_json, tags_json, metadata_json, synced_at
            FROM analytics_dramas
            {where}
            ORDER BY {order_by}
            LIMIT ?
        """
        query_limit = max(1, min(limit * 4, 1000))
        with self._connect() as conn:
            rows = conn.execute(sql, (*params, query_limit)).fetchall()
        return _merge_drama_rows([_decode_drama_row(row) for row in rows])[:limit]

    def overview(self, platform: str = "", start: str = "", end: str = "") -> dict[str, Any]:
        where, params = _where_clause(platform=platform, start=start, end=end, date_column="synced_at")
        with self._connect() as conn:
            jobs = conn.execute(f"SELECT COUNT(*) FROM analytics_jobs {where}", params).fetchone()[0]
            dramas = conn.execute(f"SELECT COUNT(DISTINCT title) FROM analytics_dramas {where}", params).fetchone()[0]
            comments = conn.execute(f"SELECT COUNT(*) FROM analytics_comments {where}", params).fetchone()[0]
            transcripts = conn.execute(f"SELECT COALESCE(SUM(transcript_count), 0) FROM analytics_jobs {where}", params).fetchone()[0]
            indexed = conn.execute(f"SELECT COUNT(*) FROM analytics_jobs {where} AND indexed = 1" if where else "SELECT COUNT(*) FROM analytics_jobs WHERE indexed = 1", params).fetchone()[0]
            latest = conn.execute("SELECT MAX(synced_at) FROM analytics_jobs").fetchone()[0]
        return {
            "task_count": jobs,
            "drama_count": dramas,
            "comment_count": comments,
            "transcript_count": transcripts or 0,
            "indexed_jobs": indexed,
            "unindexed_jobs": max(0, jobs - indexed),
            "last_updated": latest or 0,
        }

    def assets(self) -> dict[str, Any]:
        with self._connect() as conn:
            job_rows = conn.execute(
                """
                SELECT platform, job_id, artifact_count, drama_count, comment_count,
                       transcript_count, indexed, synced_at
                FROM analytics_jobs
                ORDER BY synced_at DESC
                LIMIT 100
                """
            ).fetchall()
            platform_rows = conn.execute(
                """
                SELECT platform, COUNT(*) AS jobs, SUM(drama_count) AS dramas
                FROM analytics_jobs
                GROUP BY platform
                ORDER BY jobs DESC
                """
            ).fetchall()
        return {
            "overview": self.overview(),
            "jobs": [dict(row) for row in job_rows],
            "platform_distribution": [dict(row) for row in platform_rows],
        }

    def tag_distribution(self, platform: str = "", tag_type: str = "") -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        if tag_type:
            clauses.append("tag_type = ?")
            params.append(tag_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT tag_type, tag, COUNT(*) AS count
                FROM analytics_tags
                {where}
                GROUP BY tag_type, tag
                ORDER BY count DESC, tag ASC
                LIMIT 100
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def trends(self, platform: str = "", metric: str = "heat") -> list[dict[str, Any]]:
        clauses = ["metric = ?"]
        params: list[Any] = [metric]
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        where = "WHERE " + " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT substr(captured_at, 1, 10) AS date, platform, SUM(value) AS value, COUNT(*) AS count
                FROM analytics_snapshots
                {where}
                GROUP BY date, platform
                ORDER BY date ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def sentiment_distribution(self, platform: str = "") -> list[dict[str, Any]]:
        where, params = _where_clause(platform=platform)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sentiment, COUNT(*) AS count
                FROM analytics_comments
                {where}
                GROUP BY sentiment
                ORDER BY count DESC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _drama_row_from_document(
        self,
        artifact: Artifact,
        document: StandardDocument,
        synced_at: float,
    ) -> dict[str, Any] | None:
        if document.source_type not in {"rank", "analysis", "library"}:
            return None
        stats = _stats_from_text(document.text)
        metadata = dict(document.metadata)
        return {
            "platform": document.platform,
            "job_id": document.job_id,
            "source_path": document.source_path,
            "source_type": document.source_type,
            "record_id": str(metadata.get("record_id") or document.document_id),
            "title": document.title or str(metadata.get("title") or ""),
            "category": str(metadata.get("category") or _extract_label(document.text, "类型")),
            "rank": _to_int(metadata.get("rank")),
            "heat": stats.get("heat", 0),
            "like_count": stats.get("like_count", 0),
            "favorite_count": stats.get("favorite_count", 0),
            "comment_count": stats.get("comment_count", 0),
            "captured_at": _captured_at(document.source_path, synced_at),
            "summary": _extract_label(document.text, "摘要") or _extract_label(document.text, "简介"),
            "selling_points": _split_values(_extract_label(document.text, "卖点")),
            "hook_points": _split_values(_extract_label(document.text, "钩子")),
            "tags": _split_values(_extract_label(document.text, "标签")),
            "metadata": {**artifact.metadata, **metadata},
            "synced_at": synced_at,
        }

    def _upsert_drama(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO analytics_dramas (
                platform, job_id, source_path, source_type, record_id, title, category,
                rank, heat, like_count, favorite_count, comment_count, captured_at, summary,
                selling_points_json, hook_points_json, tags_json, metadata_json, synced_at
            )
            VALUES (
                :platform, :job_id, :source_path, :source_type, :record_id, :title, :category,
                :rank, :heat, :like_count, :favorite_count, :comment_count, :captured_at, :summary,
                :selling_points_json, :hook_points_json, :tags_json, :metadata_json, :synced_at
            )
            ON CONFLICT(platform, job_id, source_path, record_id, title) DO UPDATE SET
                category=excluded.category,
                rank=excluded.rank,
                heat=excluded.heat,
                like_count=excluded.like_count,
                favorite_count=excluded.favorite_count,
                comment_count=excluded.comment_count,
                captured_at=excluded.captured_at,
                summary=excluded.summary,
                selling_points_json=excluded.selling_points_json,
                hook_points_json=excluded.hook_points_json,
                tags_json=excluded.tags_json,
                metadata_json=excluded.metadata_json,
                synced_at=excluded.synced_at
            """,
            {
                **row,
                "selling_points_json": json.dumps(row.get("selling_points") or [], ensure_ascii=False),
                "hook_points_json": json.dumps(row.get("hook_points") or [], ensure_ascii=False),
                "tags_json": json.dumps(row.get("tags") or [], ensure_ascii=False),
                "metadata_json": json.dumps(row.get("metadata") or {}, ensure_ascii=False, default=str),
            },
        )


def _extract_raw_rows(artifact: Artifact) -> dict[str, list[dict[str, Any]]]:
    if artifact.content_type != "json":
        return {"comments": [], "tags": [], "analysis_rows": []}
    payload = artifact.data
    comments: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    analysis_rows: list[dict[str, Any]] = []
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        for record in payload["records"]:
            if not isinstance(record, dict):
                continue
            title = _record_title(record, artifact.platform)
            for comment in _record_comments(record):
                comments.append(
                    {
                        "platform": artifact.platform,
                        "job_id": artifact.job_id,
                        "title": title,
                        "content": comment,
                        "sentiment": classify_sentiment(comment),
                        "source_path": artifact.path,
                    }
                )
    if isinstance(payload, dict) and isinstance(payload.get("analysis"), dict):
        analysis = payload["analysis"]
        title = str(analysis.get("title") or analysis.get("book_id") or "")
        selling_points = _as_str_list(analysis.get("selling_points")) or _as_str_list((analysis.get("llm_analysis") or {}).get("核心卖点") if isinstance(analysis.get("llm_analysis"), dict) else [])
        hook_points = _as_str_list(analysis.get("hook_points"))
        plot_tags = _as_str_list(analysis.get("plot_tags"))
        analysis_rows.append(
            {
                "platform": artifact.platform,
                "job_id": artifact.job_id,
                "source_path": artifact.path,
                "source_type": artifact.source_type,
                "record_id": str(analysis.get("book_id") or analysis.get("series_id") or title),
                "title": title,
                "category": str(analysis.get("category") or ""),
                "rank": None,
                "heat": 0,
                "like_count": 0,
                "favorite_count": 0,
                "comment_count": len(_record_comments(analysis)),
                "captured_at": _captured_at(artifact.path, 0),
                "summary": str(analysis.get("summary") or ""),
                "selling_points": selling_points,
                "hook_points": hook_points,
                "tags": plot_tags,
                "metadata": {"transcript_source": analysis.get("transcript_source", "")},
            }
        )
        for tag_type, values in (("selling_point", selling_points), ("hook", hook_points), ("plot_tag", plot_tags)):
            for value in values:
                tags.append(
                    {
                        "platform": artifact.platform,
                        "job_id": artifact.job_id,
                        "tag_type": tag_type,
                        "tag": value,
                        "title": title,
                        "source_path": artifact.path,
                    }
                )
    return {"comments": comments, "tags": tags, "analysis_rows": analysis_rows}


def classify_sentiment(text: str) -> str:
    negative = ("差", "烂", "尬", "不好", "失望", "弃", "骗", "难看")
    positive = ("好看", "喜欢", "爽", "精彩", "上头", "期待", "推荐", "感动")
    if any(word in text for word in negative):
        return "negative"
    if any(word in text for word in positive):
        return "positive"
    return "neutral"


def _record_title(record: dict[str, Any], platform: str) -> str:
    if platform == "hg":
        rank = record.get("rank") if isinstance(record.get("rank"), dict) else record
        return str(rank.get("play_name") or rank.get("title") or "")
    return str(record.get("title") or record.get("name") or "")


def _record_comments(record: dict[str, Any]) -> list[str]:
    values = record.get("comments") if isinstance(record.get("comments"), list) else []
    result = []
    for item in values:
        if isinstance(item, dict):
            text = str(item.get("content") or item.get("text") or item.get("comment") or "")
        else:
            text = str(item)
        if text.strip():
            result.append(text.strip())
    return result


def _stats_from_text(text: str) -> dict[str, int]:
    return {
        "heat": _to_int(_extract_label(text, "热度")) or 0,
        "like_count": _to_int(_extract_label(text, "点赞")) or 0,
        "favorite_count": _to_int(_extract_label(text, "收藏")) or 0,
        "comment_count": _to_int(_extract_label(text, "评论数")) or 0,
    }


def _snapshot_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    captured_at = _captured_at(row.get("source_path", ""), row.get("synced_at", 0))
    rows = []
    for metric in ("heat", "like_count", "favorite_count", "comment_count"):
        value = int(row.get(metric) or 0)
        if value:
            rows.append(
                {
                    "platform": row["platform"],
                    "job_id": row["job_id"],
                    "title": row["title"],
                    "metric": metric,
                    "value": value,
                    "captured_at": captured_at,
                    "source_path": row["source_path"],
                }
            )
    return rows


def _tag_rows(row: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for tag_type, key in (("category", "category"), ("selling_point", "selling_points"), ("hook", "hook_points"), ("plot_tag", "tags")):
        values = [row.get(key)] if isinstance(row.get(key), str) else row.get(key) or []
        for value in values:
            text = str(value).strip()
            if text:
                rows.append(
                    {
                        "platform": row["platform"],
                        "job_id": row["job_id"],
                        "tag_type": tag_type,
                        "tag": text,
                        "title": row["title"],
                        "source_path": row["source_path"],
                    }
                )
    return rows


def _dedupe_tag_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    result = []
    for row in rows:
        key = (row["platform"], row["job_id"], row["tag_type"], row["tag"], row.get("title", ""), row["source_path"])
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _decode_drama_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for key in ("selling_points_json", "hook_points_json", "tags_json", "metadata_json"):
        out_key = key.replace("_json", "")
        payload[out_key] = json.loads(payload.pop(key) or "[]" if key != "metadata_json" else payload.pop(key) or "{}")
    return payload


def _merge_drama_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("platform", ""), row.get("job_id", ""), row.get("title", ""))
        if key not in merged:
            merged[key] = row
            continue
        current = merged[key]
        for field in ("rank", "heat", "like_count", "favorite_count", "comment_count"):
            if not current.get(field) and row.get(field):
                current[field] = row[field]
        for field in ("summary", "category", "record_id"):
            if not current.get(field) and row.get(field):
                current[field] = row[field]
        for field in ("selling_points", "hook_points", "tags"):
            values = list(current.get(field) or [])
            for item in row.get(field) or []:
                if item not in values:
                    values.append(item)
            current[field] = values
    return list(merged.values())


def _where_clause(
    platform: str = "",
    category: str = "",
    start: str = "",
    end: str = "",
    date_column: str = "",
    captured_at: str = "",
) -> tuple[str, list[Any]]:
    clauses = []
    params: list[Any] = []
    if platform:
        clauses.append("platform = ?")
        params.append(platform)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if start and date_column:
        clauses.append(f"{date_column} >= ?")
        params.append(_date_to_ts(start))
    if end and date_column:
        clauses.append(f"{date_column} <= ?")
        params.append(_date_to_ts(end, end_of_day=True))
    if captured_at:
        normalized_date = _normalize_date(captured_at)
        compact_date = normalized_date.replace("-", "")
        clauses.append(
            "("
            "captured_at = ? OR "
            "((captured_at IS NULL OR captured_at = '') AND source_path LIKE ?)"
            ")"
        )
        params.extend([normalized_date, f"%{compact_date}%"])
    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _backfill_drama_captured_at(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, source_path, synced_at
        FROM analytics_dramas
        WHERE captured_at IS NULL OR captured_at = ''
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE analytics_dramas SET captured_at = ? WHERE id = ?",
            (_captured_at(row["source_path"] or "", row["synced_at"] or 0), row["id"]),
        )


def _extract_label(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}[:：]\s*([^\n]+)", text or "")
    return match.group(1).strip() if match else ""


def _split_values(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[、,，;；]+", value) if item.strip()]


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    text = re.sub(r"[^\d-]", "", str(value))
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _captured_at(source_path: str, synced_at: float) -> str:
    match = re.search(r"(20\d{6})", source_path)
    if match:
        value = match.group(1)
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return time.strftime("%Y-%m-%d", time.localtime(synced_at or time.time()))


def _normalize_date(value: str) -> str:
    value = (value or "").strip().replace("/", "-")
    match = re.fullmatch(r"(20\d{2})-?(\d{2})-?(\d{2})", value)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return value


def _date_to_ts(value: str, end_of_day: bool = False) -> float:
    suffix = " 23:59:59" if end_of_day else " 00:00:00"
    return time.mktime(time.strptime(value + suffix, "%Y-%m-%d %H:%M:%S"))


def _is_indexed(root: Path, job_id: str) -> bool:
    status_path = root / ".runtime" / "rag" / "index_status.json"
    if not status_path.exists():
        return False
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return job_id in (payload.get("jobs") or {})
