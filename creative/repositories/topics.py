# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Topic library, breakdown versions and selection stage snapshots.

This mixin is the PostgreSQL replacement for the topic half of
``creative.legacy_sqlite_repository.CreativeRepository``. It assumes only the
:class:`~creative.repositories.base.CreativeRepositoryBase` contract, so the
coordinating repository composes it beside the other domains without any
cross-domain import, and every method keeps the legacy name, signature,
returned keys, ordering and Chinese error text.

Type mapping applied here (see ``creative/database_schema.py``):

* legacy epoch ``REAL`` columns (``created_at``/``updated_at``/``locked_at``)
  are ``TIMESTAMPTZ``: writes convert with :func:`~creative.repositories.base.utc_now`
  and reads come back as epoch seconds because ``row_dict`` converts them;
* legacy ``*_json TEXT`` columns are ``JSONB``: Python containers go straight to
  the driver and the ``_json`` suffix is popped on the way out;
* the legacy 0/1 ``deleted`` flag is ``BOOLEAN``;
* ``creative_topics.captured_at`` stays ``TEXT`` because it already held an ISO
  8601 string rather than an epoch float, exactly as before.

Lock order: a normalized-title advisory lock, then a per-(stage, topic)
advisory lock, then row locks. The title lock replaces the legacy
``BEGIN IMMEDIATE`` for the two title-key scans (``adopt_rank_topic`` and
``ensure_download_topic``): PostgreSQL would otherwise allow two writers to miss
each other's uncommitted row and both create a duplicate decision. Locks are
never taken in the reverse order.
"""

from __future__ import annotations

import json
import time
import unicodedata
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Text, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from creative.database_schema import (
    creative_breakdowns,
    creative_topics,
    selection_stage_snapshots,
)
from creative.legacy_sqlite_repository import (
    DownloadTopicDecisionConflict,
    DuplicateTopicError,
    TopicDecisionConflict,
    canonical_request_hash,
)
from creative.repositories.base import utc_now


_TOPIC_STATUSES = ("pending_review", "adopted", "rejected", "archived")
_DECIDED_STATUSES = ("adopted", "rejected", "archived")
_LOCKED_BREAKDOWN_STATUSES = ("locked", "approved")


class TopicsRepository:
    """Topic library, breakdown versions and stage snapshots."""

    # Category values that describe a board rather than a genre. Kept identical
    # to the legacy ``CreativeRepository._NON_GENRE_LABELS`` so migrated rows
    # read back with the same ``待补充`` fallback.
    _NON_GENRE_LABELS = {
        "短剧", "漫剧", "AI漫剧", "真人剧", "AI剧", "红果", "未分类",
        "新剧榜", "推荐榜", "热播榜", "热搜榜", "收藏榜", "预约榜", "必看榜", "高分臻果榜",
    }

    # -- locking -------------------------------------------------------------

    @staticmethod
    def _lock_title_key(connection: Connection, title_key: str) -> None:
        """Serialize the title-key scans against each other.

        Holding this transaction-scoped advisory lock for the whole decision is
        the PostgreSQL equivalent of the legacy ``BEGIN IMMEDIATE``: writers
        that touch the same normalized title queue up, writers that touch
        different titles do not.
        """

        connection.exec_driver_sql(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"creative-topic-title:{title_key}",),
        )

    @staticmethod
    def _lock_stage_version(
        connection: Connection, stage: str, topic_id: int
    ) -> None:
        """Serialize version allocation for one ``(stage, topic_id)`` pair."""

        connection.exec_driver_sql(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"creative-stage-snapshot:{stage}:{int(topic_id)}",),
        )

    # -- outbound conversions -------------------------------------------------

    @staticmethod
    def _topic(row: dict[str, Any]) -> dict[str, Any]:
        """Adapt the legacy ``_topic`` to JSONB and boolean columns."""

        for key in ("score_details_json", "evidence_json"):
            value = row.pop(key, None)
            # JSONB already handed back the parsed value; only an absent value
            # (or the legacy empty string) falls back to ``{}``.
            row[key.removesuffix("_json")] = {} if value in (None, "") else value
        row["score_details"].pop("imitability", None)
        row["score_details"].pop("production_cost", None)
        raw_platforms = row.pop("source_platforms_json", None) or []
        row["source_platforms"] = sorted(
            {str(item) for item in raw_platforms if str(item)}
        ) or [str(row.get("platform") or "")]
        if not str(row.get("rank_source") or "").strip():
            row["rank_source"] = str(row.get("rank_type") or "").strip()
        if str(row.get("category") or "").strip() in TopicsRepository._NON_GENRE_LABELS:
            row["category"] = "待补充"
        return row

    @staticmethod
    def _breakdown(row: dict[str, Any]) -> dict[str, Any]:
        """Adapt the legacy ``_breakdown`` to JSONB columns."""

        for key, fallback in (
            ("content_json", {}),
            ("field_sources_json", {}),
            ("risks_json", []),
        ):
            value = row.pop(key, None)
            row[key.removesuffix("_json")] = fallback if value in (None, "") else value
        return row

    @staticmethod
    def _selection_snapshot(row: dict[str, Any]) -> dict[str, Any]:
        """Adapt the legacy ``_selection_snapshot`` to a JSONB column."""

        row["snapshot"] = row.pop("snapshot_json")
        return row

    @staticmethod
    def _title_key(value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return "".join(
            char for char in normalized if char.isalnum() or "\u4e00" <= char <= "\u9fff"
        )

    @staticmethod
    def _join_rank_sources(*values: Any) -> str:
        sources: list[str] = []
        for value in values:
            for source in str(value or "").split("|"):
                source = source.strip()
                if source and source not in sources:
                    sources.append(source)
        return "|".join(sources)

    @staticmethod
    def _matches_rank_source(topic: dict[str, Any], rank_source: str) -> bool:
        selected = str(rank_source or "").strip()
        sources = [
            source.strip()
            for source in str(
                topic.get("rank_source") or topic.get("rank_type") or ""
            ).split("|")
            if source.strip()
        ]
        if str(topic.get("platform") or "") == "hg":
            return any(source.startswith(selected) for source in sources)
        return selected in sources

    @staticmethod
    def _merge_topics(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Collapse same-title records while retaining every platform and evidence."""

        merged: dict[str, dict[str, Any]] = {}
        for topic in topics:
            key = TopicsRepository._title_key(topic.get("title"))
            if not key:
                continue
            current = merged.get(key)
            if current is None:
                current = dict(topic)
                current["source_platforms"] = sorted(
                    set(current.get("source_platforms") or [current.get("platform", "")])
                )
                current["source_paths"] = (
                    [current["source_path"]] if current.get("source_path") else []
                )
                current["_decision_updated_at"] = (
                    float(current.get("updated_at") or 0)
                    if current.get("status") != "pending_review"
                    else -1.0
                )
                merged[key] = current
                continue
            current["source_platforms"] = sorted(
                set(current.get("source_platforms", []))
                | set(topic.get("source_platforms", []))
                | {str(topic.get("platform") or "")}
            )
            current["source_paths"] = sorted(
                set(current.get("source_paths", []))
                | ({topic["source_path"]} if topic.get("source_path") else set())
            )
            current["rank_source"] = TopicsRepository._join_rank_sources(
                current.get("rank_source"), topic.get("rank_source")
            )
            evidence = current.setdefault("evidence", [])
            seen = {
                _evidence_marker(item) for item in evidence
            }
            for item in topic.get("evidence") or []:
                marker = _evidence_marker(item)
                if marker not in seen:
                    evidence.append(item)
                    seen.add(marker)
            decision_updated_at = (
                float(topic.get("updated_at") or 0)
                if topic.get("status") != "pending_review"
                else -1.0
            )
            if (
                decision_updated_at >= 0
                and decision_updated_at
                >= float(current.get("_decision_updated_at") or -1.0)
            ):
                for field in (
                    "id", "platform", "job_id", "source_path", "captured_at", "title",
                    "description", "category", "rank_type", "status", "score",
                    "confidence", "score_details", "recommendation", "decision_note",
                    "created_at", "updated_at", "deleted", "locked_breakdown_id",
                    "latest_breakdown_id", "latest_breakdown_version",
                    "latest_breakdown_status",
                ):
                    current[field] = topic.get(field)
                current["_decision_updated_at"] = decision_updated_at
            if current.get("confidence") != "high" and topic.get("confidence") == "high":
                current["confidence"] = "high"
            if float(current.get("_decision_updated_at") or -1.0) < 0:
                if not current.get("locked_breakdown_id") and topic.get("locked_breakdown_id"):
                    current["locked_breakdown_id"] = topic["locked_breakdown_id"]
                if topic.get("latest_breakdown_id") and int(topic["latest_breakdown_id"]) > int(
                    current.get("latest_breakdown_id") or 0
                ):
                    current["id"] = topic["id"]
                    current["latest_breakdown_id"] = topic["latest_breakdown_id"]
                    current["latest_breakdown_version"] = topic.get(
                        "latest_breakdown_version"
                    )
                    current["latest_breakdown_status"] = topic.get(
                        "latest_breakdown_status"
                    )
        for topic in merged.values():
            topic.pop("_decision_updated_at", None)
        return list(merged.values())

    # -- topic writes ---------------------------------------------------------

    def upsert_topics(self, topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert or refresh scraped topics, never overwriting a manual decision.

        The legacy loop is kept one statement per topic: PostgreSQL rejects an
        ``ON CONFLICT DO UPDATE`` batch that touches the same row twice, while
        the legacy store silently let the last copy win. Concurrency between
        callers is settled by ``uq_creative_topics_source``, so the same source
        topic can only ever be one row.
        """

        now = utc_now()
        with self.transaction_scope() as connection:
            for topic in topics:
                statement = pg_insert(creative_topics).values(
                    platform=topic["platform"],
                    job_id=topic["job_id"],
                    source_path=topic["source_path"],
                    captured_at=topic["captured_at"],
                    title=topic["title"],
                    description=topic.get("description", ""),
                    category=topic.get("category", ""),
                    rank_type=topic.get("rank_type", ""),
                    rank_source=topic.get("rank_source", topic.get("rank_type", "")),
                    score=topic["score"],
                    confidence=topic["confidence"],
                    score_details_json=topic["score_details"],
                    recommendation=topic["recommendation"],
                    evidence_json=topic["evidence"],
                    source_platforms_json=sorted(
                        {str(topic.get("platform") or "")}
                    ),
                    created_at=now,
                    updated_at=now,
                )
                # ``where`` keeps a manual decision authoritative: a re-scraped
                # row only refreshes while it is still ``pending_review``.
                statement = statement.on_conflict_do_update(
                    constraint="uq_creative_topics_source",
                    set_={
                        "captured_at": statement.excluded.captured_at,
                        "description": statement.excluded.description,
                        "category": statement.excluded.category,
                        "rank_type": statement.excluded.rank_type,
                        "rank_source": statement.excluded.rank_source,
                        "score": statement.excluded.score,
                        "confidence": statement.excluded.confidence,
                        "score_details_json": statement.excluded.score_details_json,
                        "recommendation": statement.excluded.recommendation,
                        "evidence_json": statement.excluded.evidence_json,
                        "source_platforms_json": statement.excluded.source_platforms_json,
                        "updated_at": statement.excluded.updated_at,
                    },
                    where=creative_topics.c.status == "pending_review",
                )
                connection.execute(statement)
        return self.list_topics(limit=1000)

    def adopt_rank_topic(
        self, topic: dict[str, Any], actor_user_id: str
    ) -> dict[str, Any]:
        title_key = self._title_key(topic.get("title"))
        if not title_key:
            raise ValueError("榜单剧目缺少标题")
        now = utc_now()
        with self.transaction_scope() as connection:
            # Lock order: title advisory lock, then the (stage, topic) lock taken
            # while recording the decision snapshot.
            self._lock_title_key(connection, title_key)
            matching_rows = self._matching_topic_rows(connection, title_key)
            if any(row["status"] == "adopted" for row in matching_rows):
                raise DuplicateTopicError("剧目已收录")

            decided_rows = [
                row for row in matching_rows if row["status"] in {"rejected", "archived"}
            ]
            if decided_rows:
                latest_decision = max(
                    decided_rows,
                    key=lambda row: (float(row["updated_at"] or 0), int(row["id"])),
                )
                if latest_decision["status"] == "rejected":
                    raise TopicDecisionConflict(
                        "该剧目已被拒绝，请先在选题库恢复后再收录"
                    )
                raise TopicDecisionConflict(
                    "该剧目已归档，请先在选题库恢复后再收录"
                )

            current_pending_rows = [
                row
                for row in matching_rows
                if row["status"] == "pending_review"
                and str(row["platform"] or "") == str(topic.get("platform") or "")
                and str(row["job_id"] or "") == str(topic.get("job_id") or "")
                and str(row["source_path"] or "") == str(topic.get("source_path") or "")
            ]
            topic_values = {
                "platform": topic["platform"],
                "job_id": topic["job_id"],
                "source_path": topic["source_path"],
                "captured_at": topic["captured_at"],
                "title": topic["title"],
                "description": topic.get("description", ""),
                "category": topic.get("category", ""),
                "rank_type": topic.get("rank_type", ""),
                "rank_source": topic.get("rank_source", topic.get("rank_type", "")),
                "status": "adopted",
                "score": topic["score"],
                "confidence": topic["confidence"],
                "score_details_json": topic["score_details"],
                "recommendation": topic["recommendation"],
                "evidence_json": topic["evidence"],
                "source_platforms_json": sorted(
                    {str(topic.get("platform") or "")}
                ),
                "decision_note": "",
                "updated_at": now,
            }
            if current_pending_rows:
                current = max(
                    current_pending_rows,
                    key=lambda row: (
                        row["title"] == topic["title"],
                        float(row["updated_at"] or 0),
                        int(row["id"]),
                    ),
                )
                topic_id = int(current["id"])
                connection.execute(
                    update(creative_topics)
                    .where(
                        creative_topics.c.id == topic_id,
                        creative_topics.c.deleted.is_(False),
                    )
                    .values(**topic_values)
                )
            else:
                topic_id = int(
                    connection.execute(
                        pg_insert(creative_topics)
                        .values(**topic_values, created_at=now)
                        .returning(creative_topics.c.id)
                    ).scalar_one()
                )

            self._record_topic_decision(
                connection,
                topic_id=topic_id,
                snapshot={
                    "topic_id": topic_id,
                    "title": topic["title"],
                    "outcome": "adopted",
                    "decision_note": "",
                    "source": "today_rank",
                },
                actor_user_id=actor_user_id,
                source_job_id=str(topic["job_id"]),
                now=now,
            )
        return self.get_topic(topic_id) or {}

    def ensure_download_topic(
        self,
        *,
        title: str,
        platform: str,
        provider_id: str,
        source_ref: str,
        actor_user_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        normalized_title = str(title or "").strip()
        title_key = self._title_key(normalized_title)
        if not title_key:
            raise ValueError("剧名不能为空")
        normalized_platform = str(platform or "").strip().lower()
        if normalized_platform not in {"hg", "dy"}:
            raise ValueError("下载选题平台必须是 hg 或 dy")
        normalized_provider = str(provider_id or "").strip() or normalized_platform
        normalized_source_ref = str(source_ref or "").strip()
        if not normalized_source_ref:
            raise ValueError("下载选题缺少来源标识")

        now = utc_now()
        before: dict[str, Any] | None = None
        with self.transaction_scope() as connection:
            # Lock order: title advisory lock, then the (stage, topic) lock.
            self._lock_title_key(connection, title_key)
            matching_rows = self._matching_topic_rows(connection, title_key)
            decided_rows = [
                row for row in matching_rows if row["status"] != "pending_review"
            ]
            selected = max(
                decided_rows or matching_rows,
                key=lambda row: float(row["updated_at"] or 0),
                default=None,
            )

            if selected is not None:
                before = self._topic(dict(selected))
                if selected["status"] in {"rejected", "archived"}:
                    raise DownloadTopicDecisionConflict(
                        "该剧目已被拒绝或归档，请先在选题库恢复后再下载"
                    )
                topic_id = int(selected["id"])
                if selected["status"] == "adopted":
                    # The legacy store returned straight from inside its write
                    # transaction, before any snapshot was appended.
                    return self.get_topic(topic_id) or {}, before
                connection.execute(
                    update(creative_topics)
                    .where(
                        creative_topics.c.id == topic_id,
                        creative_topics.c.deleted.is_(False),
                    )
                    .values(
                        status="adopted",
                        decision_note="下载时自动采纳",
                        updated_at=now,
                    )
                )
                source_job_id = str(selected["job_id"] or "")
            else:
                source_path = (
                    f"download-search:{normalized_provider}:{normalized_source_ref}"
                )
                evidence = [
                    {
                        "source_type": "download_search",
                        "provider_id": normalized_provider,
                        "source_ref": normalized_source_ref,
                    }
                ]
                source_job_id = f"download:{normalized_source_ref}"
                topic_id = int(
                    connection.execute(
                        pg_insert(creative_topics)
                        .values(
                            platform=normalized_platform,
                            job_id=source_job_id,
                            source_path=source_path,
                            # ``captured_at`` is the legacy wall-clock TEXT
                            # column, so it keeps the legacy format verbatim.
                            captured_at=time.strftime(
                                "%Y-%m-%d %H:%M:%S", time.localtime(now.timestamp())
                            ),
                            title=normalized_title,
                            description="",
                            category="",
                            rank_type="download_search",
                            rank_source="download_search",
                            status="adopted",
                            score=0,
                            confidence="medium",
                            score_details_json={},
                            recommendation="下载时自动采纳",
                            evidence_json=evidence,
                            source_platforms_json=[normalized_platform],
                            decision_note="下载时自动采纳",
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(creative_topics.c.id)
                    ).scalar_one()
                )

            self._record_topic_decision(
                connection,
                topic_id=topic_id,
                snapshot={
                    "topic_id": topic_id,
                    "title": normalized_title,
                    "outcome": "adopted",
                    "decision_note": "下载时自动采纳",
                    "source": "download_search",
                    "provider_id": normalized_provider,
                    "source_ref": normalized_source_ref,
                },
                actor_user_id=actor_user_id,
                source_job_id=source_job_id,
                now=now,
            )

        return self.get_topic(topic_id) or {}, before

    def decided_title_keys(self) -> set[str]:
        with self.connection_scope() as connection:
            rows = connection.execute(
                select(creative_topics.c.title)
                .where(
                    creative_topics.c.deleted.is_(False),
                    creative_topics.c.status.in_(_DECIDED_STATUSES),
                )
                .order_by(creative_topics.c.id.asc())
            ).scalars()
            return {
                key
                for key in (self._title_key(title) for title in rows)
                if key
            }

    # -- topic reads ----------------------------------------------------------

    def list_topics(
        self,
        platform: str = "",
        status: str = "",
        rank_source: str = "",
        category: str = "",
        min_score: float | None = None,
        max_score: float | None = None,
        captured_date: str = "",
        has_locked_breakdown: bool | None = None,
        limit: int | None = 50,
    ) -> list[dict[str, Any]]:
        conditions = [creative_topics.c.deleted.is_(False)]
        if platform:
            conditions.append(
                or_(
                    creative_topics.c.platform == platform,
                    cast(creative_topics.c.source_platforms_json, Text).like(
                        f'%"{platform}"%'
                    ),
                )
            )
        if category:
            conditions.append(creative_topics.c.category.like(f"%{category}%"))
        if min_score is not None:
            conditions.append(creative_topics.c.score >= min_score)
        if max_score is not None:
            conditions.append(creative_topics.c.score <= max_score)
        if captured_date:
            conditions.append(creative_topics.c.captured_at.like(f"{captured_date}%"))

        locked_expr = (
            select(creative_breakdowns.c.id)
            .where(
                creative_breakdowns.c.topic_id == creative_topics.c.id,
                creative_breakdowns.c.status.in_(_LOCKED_BREAKDOWN_STATUSES),
            )
            .order_by(
                creative_breakdowns.c.version.desc(),
                creative_breakdowns.c.id.desc(),
            )
            .limit(1)
            .scalar_subquery()
            .label("locked_breakdown_id")
        )
        latest_id_expr = (
            select(creative_breakdowns.c.id)
            .where(creative_breakdowns.c.topic_id == creative_topics.c.id)
            .order_by(
                creative_breakdowns.c.version.desc(),
                creative_breakdowns.c.id.desc(),
            )
            .limit(1)
            .scalar_subquery()
            .label("latest_breakdown_id")
        )
        latest_version_expr = (
            select(creative_breakdowns.c.version)
            .where(creative_breakdowns.c.topic_id == creative_topics.c.id)
            .order_by(
                creative_breakdowns.c.version.desc(),
                creative_breakdowns.c.id.desc(),
            )
            .limit(1)
            .scalar_subquery()
            .label("latest_breakdown_version")
        )
        latest_status_expr = (
            select(creative_breakdowns.c.status)
            .where(creative_breakdowns.c.topic_id == creative_topics.c.id)
            .order_by(
                creative_breakdowns.c.version.desc(),
                creative_breakdowns.c.id.desc(),
            )
            .limit(1)
            .scalar_subquery()
            .label("latest_breakdown_status")
        )

        statement = (
            select(
                *creative_topics.c,
                locked_expr,
                latest_id_expr,
                latest_version_expr,
                latest_status_expr,
            )
            .where(*conditions)
            # ``id`` is the tie-breaker the legacy rowid scan produced, so equal
            # scores keep the order the SQLite query returned.
            .order_by(
                creative_topics.c.score.desc().nullslast(),
                creative_topics.c.updated_at.desc().nullslast(),
                creative_topics.c.id.asc(),
            )
        )
        if limit is not None and not rank_source:
            statement = statement.limit(max(1, min(limit, 500)))

        with self.connection_scope() as connection:
            rows = connection.execute(statement).mappings().all()
        merged = self._merge_topics(
            [self._topic(self.row_dict(row)) for row in rows]
        )
        if rank_source:
            merged = [
                item for item in merged
                if self._matches_rank_source(item, rank_source)
            ]
        if status:
            statuses = {value.strip() for value in status.split(",") if value.strip()}
            merged = [item for item in merged if item.get("status") in statuses]
        if has_locked_breakdown is True:
            merged = [item for item in merged if item.get("locked_breakdown_id")]
        elif has_locked_breakdown is False:
            merged = [item for item in merged if not item.get("locked_breakdown_id")]
        return merged if limit is None else merged[: max(1, min(limit, 500))]

    def get_topic(self, topic_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_topics).where(
                    creative_topics.c.id == topic_id,
                    creative_topics.c.deleted.is_(False),
                )
            ).mappings().first()
        return self._topic(self.row_dict(row)) if row else None

    def update_topic(
        self, topic_id: int, status: str, decision_note: str = ""
    ) -> dict[str, Any] | None:
        if status not in set(_TOPIC_STATUSES):
            raise ValueError("invalid topic status")
        with self.transaction_scope() as connection:
            connection.execute(
                update(creative_topics)
                .where(
                    creative_topics.c.id == topic_id,
                    creative_topics.c.deleted.is_(False),
                )
                .values(
                    status=status,
                    decision_note=decision_note,
                    updated_at=utc_now(),
                )
            )
        return self.get_topic(topic_id)

    def delete_topic(self, topic_id: int) -> dict[str, Any] | None:
        topic = self.get_topic(topic_id)
        if topic is None:
            return None
        if topic.get("status") != "archived":
            raise ValueError("只能删除已归档选题")
        with self.transaction_scope() as connection:
            connection.execute(
                update(creative_topics)
                .where(
                    creative_topics.c.id == topic_id,
                    creative_topics.c.deleted.is_(False),
                )
                .values(deleted=True, updated_at=utc_now())
            )
        return topic

    def decide_topic(
        self, topic_id: int, status: str, decision_note: str, actor_user_id: str
    ) -> dict[str, Any] | None:
        topic = self.update_topic(topic_id, status, decision_note)
        if topic is not None:
            self.append_selection_stage_snapshot(
                "topic_decision", topic_id, status,
                {"topic_id": topic_id, "title": topic["title"], "outcome": status,
                 "decision_note": decision_note},
                resource_type="topic", resource_id=topic_id,
                actor_user_id=actor_user_id,
            )
        return topic

    # -- selection stage snapshots -------------------------------------------

    def append_selection_stage_snapshot(
        self, stage: str, topic_id: int, outcome: str, snapshot: dict[str, Any],
        *, resource_type: str = "", resource_id: str | int = "",
        actor_user_id: str = "", source_job_id: str = "", deduplicate: bool = False,
    ) -> dict[str, Any]:
        fingerprint = canonical_request_hash(snapshot)
        with self.transaction_scope() as connection:
            # Lock order: this per-(stage, topic) lock is the innermost lock, so
            # callers that already hold a title lock never invert the order. The
            # ``uq_selection_stage_snapshots_version`` constraint stays as the
            # backstop for a version that still races past it.
            self._lock_stage_version(connection, stage, topic_id)
            if deduplicate:
                existing = connection.execute(
                    select(selection_stage_snapshots)
                    .where(
                        selection_stage_snapshots.c.stage == stage,
                        selection_stage_snapshots.c.topic_id == topic_id,
                        selection_stage_snapshots.c.content_fingerprint == fingerprint,
                    )
                    .order_by(selection_stage_snapshots.c.version.desc().nullslast())
                    .limit(1)
                ).mappings().first()
                if existing is not None:
                    return self._selection_snapshot(self.row_dict(existing))
            version = int(
                connection.execute(
                    select(
                        func.coalesce(
                            func.max(selection_stage_snapshots.c.version), 0
                        ) + 1
                    ).where(
                        selection_stage_snapshots.c.stage == stage,
                        selection_stage_snapshots.c.topic_id == topic_id,
                    )
                ).scalar_one()
            )
            row = connection.execute(
                pg_insert(selection_stage_snapshots)
                .values(
                    id=str(uuid.uuid4()),
                    stage=stage,
                    topic_id=topic_id,
                    version=version,
                    outcome=outcome,
                    snapshot_json=snapshot,
                    content_fingerprint=fingerprint,
                    resource_type=resource_type,
                    resource_id=str(resource_id),
                    actor_user_id=actor_user_id,
                    source_job_id=source_job_id,
                    created_at=utc_now(),
                )
                .returning(*selection_stage_snapshots.c)
            ).mappings().one()
        return self._selection_snapshot(self.row_dict(row))

    def get_latest_selection_stage_snapshot(
        self, topic_id: int, stage: str
    ) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(selection_stage_snapshots)
                .where(
                    selection_stage_snapshots.c.topic_id == topic_id,
                    selection_stage_snapshots.c.stage == stage,
                )
                .order_by(selection_stage_snapshots.c.version.desc().nullslast())
                .limit(1)
            ).mappings().first()
        return self._selection_snapshot(self.row_dict(row)) if row else None

    # -- breakdowns -----------------------------------------------------------

    def create_breakdown(
        self,
        topic_id: int,
        content: dict[str, Any],
        sources: dict[str, Any],
        risks: list[str],
        parent_version_id: int | None = None,
        workflow_task_id: str | None = None,
        source_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        with self.transaction_scope() as connection:
            # The per-(stage, topic) lock doubles as the version-allocation lock
            # for a topic, so two writers cannot both pick version N.
            self._lock_stage_version(connection, "breakdown", topic_id)
            row = connection.execute(
                select(
                    func.coalesce(func.max(creative_breakdowns.c.version), 0),
                    func.max(creative_breakdowns.c.id),
                ).where(creative_breakdowns.c.topic_id == topic_id)
            ).one()
            version = int(row[0]) + 1
            now = utc_now()
            breakdown_id = int(
                connection.execute(
                    pg_insert(creative_breakdowns)
                    .values(
                        topic_id=topic_id,
                        version=version,
                        parent_version_id=(
                            parent_version_id
                            if parent_version_id is not None
                            else row[1]
                        ),
                        content_json=content,
                        field_sources_json=sources,
                        risks_json=risks,
                        workflow_task_id=workflow_task_id,
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(creative_breakdowns.c.id)
                ).scalar_one()
            )
        result = self.get_breakdown(breakdown_id) or {}
        # The legacy store never persisted this value (its table had no such
        # column), it only echoed the caller's argument on the returned dict.
        result["source_snapshot_id"] = source_snapshot_id
        return result

    def get_latest_locked_breakdown(self, topic_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_breakdowns)
                .where(
                    creative_breakdowns.c.topic_id == topic_id,
                    creative_breakdowns.c.status.in_(_LOCKED_BREAKDOWN_STATUSES),
                )
                .order_by(creative_breakdowns.c.version.desc().nullslast())
                .limit(1)
            ).mappings().first()
        return self._breakdown(self.row_dict(row)) if row else None

    def get_latest_breakdown(self, topic_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_breakdowns)
                .where(creative_breakdowns.c.topic_id == topic_id)
                .order_by(
                    creative_breakdowns.c.version.desc().nullslast(),
                    creative_breakdowns.c.id.desc().nullslast(),
                )
                .limit(1)
            ).mappings().first()
        return self._breakdown(self.row_dict(row)) if row else None

    def list_locked_breakdowns(self, topic_id: int) -> list[dict[str, Any]]:
        with self.connection_scope() as connection:
            rows = connection.execute(
                select(creative_breakdowns)
                .where(
                    creative_breakdowns.c.topic_id == topic_id,
                    creative_breakdowns.c.status.in_(_LOCKED_BREAKDOWN_STATUSES),
                )
                .order_by(
                    creative_breakdowns.c.version.desc().nullslast(),
                    creative_breakdowns.c.id.desc().nullslast(),
                )
            ).mappings().all()
        return [self._breakdown(self.row_dict(row)) for row in rows]

    def get_breakdown(self, breakdown_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_breakdowns).where(
                    creative_breakdowns.c.id == breakdown_id
                )
            ).mappings().first()
        return self._breakdown(self.row_dict(row)) if row else None

    def update_breakdown(
        self, breakdown_id: int, content: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self.transaction_scope() as connection:
            row = connection.execute(
                select(creative_breakdowns.c.status)
                .where(creative_breakdowns.c.id == breakdown_id)
                .with_for_update()
            ).first()
            if row is None:
                return None
            if row[0] != "draft":
                raise ValueError("immutable breakdown cannot be edited")
            connection.execute(
                update(creative_breakdowns)
                .where(creative_breakdowns.c.id == breakdown_id)
                .values(content_json=content, updated_at=utc_now())
            )
        return self.get_breakdown(breakdown_id)

    def lock_breakdown(self, breakdown_id: int) -> dict[str, Any] | None:
        with self.transaction_scope() as connection:
            now = utc_now()
            connection.execute(
                update(creative_breakdowns)
                .where(
                    creative_breakdowns.c.id == breakdown_id,
                    creative_breakdowns.c.status == "draft",
                )
                .values(status="locked", locked_at=now, updated_at=now)
            )
        return self.get_breakdown(breakdown_id)

    def complete_breakdown(
        self, breakdown_id: int, actor_user_id: str
    ) -> dict[str, Any]:
        result = self.lock_breakdown(breakdown_id)
        if result is None:
            raise ValueError("拆解不存在")
        self.append_selection_stage_snapshot(
            "breakdown", int(result["topic_id"]), "completed",
            {"breakdown_id": breakdown_id, "version": result["version"],
             "status": result["status"], "content": result["content"],
             "field_sources": result["field_sources"], "risks": result["risks"]},
            resource_type="breakdown", resource_id=breakdown_id,
            actor_user_id=actor_user_id, deduplicate=True,
        )
        return result

    # -- internal helpers -----------------------------------------------------

    def _matching_topic_rows(
        self, connection: Connection, title_key: str
    ) -> list[dict[str, Any]]:
        """Return live rows whose normalized title matches ``title_key``.

        ``title_key`` is normalized in Python exactly like the legacy scan, so
        the filter cannot be pushed into SQL without changing which rows match.
        """

        rows = connection.execute(
            select(creative_topics)
            .where(creative_topics.c.deleted.is_(False))
            .order_by(creative_topics.c.id.asc())
        ).mappings().all()
        return [
            item
            for item in (self.row_dict(row) for row in rows)
            if self._title_key(item.get("title")) == title_key
        ]

    def _record_topic_decision(
        self,
        connection: Connection,
        *,
        topic_id: int,
        snapshot: dict[str, Any],
        actor_user_id: str,
        source_job_id: str,
        now: datetime,
    ) -> None:
        """Append the ``topic_decision`` snapshot that records an adoption."""

        self._lock_stage_version(connection, "topic_decision", topic_id)
        version = int(
            connection.execute(
                select(
                    func.coalesce(func.max(selection_stage_snapshots.c.version), 0) + 1
                ).where(
                    selection_stage_snapshots.c.stage == "topic_decision",
                    selection_stage_snapshots.c.topic_id == topic_id,
                )
            ).scalar_one()
        )
        connection.execute(
            pg_insert(selection_stage_snapshots).values(
                id=str(uuid.uuid4()),
                stage="topic_decision",
                topic_id=topic_id,
                version=version,
                outcome="adopted",
                snapshot_json=snapshot,
                content_fingerprint=canonical_request_hash(snapshot),
                resource_type="topic",
                resource_id=str(topic_id),
                actor_user_id=actor_user_id,
                source_job_id=source_job_id,
                created_at=now,
            )
        )


def _evidence_marker(item: Any) -> str:
    """Stable identity of an evidence item, matching the legacy dedupe key."""

    return json.dumps(item, ensure_ascii=False, sort_keys=True)


__all__ = ["TopicsRepository"]
