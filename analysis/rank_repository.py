# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError, OperationalError

from analysis.rank_models import (
    RankDatabaseUnavailable,
    RankSnapshot,
    RankWriteResult,
)
from system_database.database import SystemDatabase
from system_database.schema import rank_entries, rank_snapshots


COUNT_FIELDS = (
    "share_count",
    "like_count",
    "favorite_count",
    "comment_count",
    "rating_count",
)
NUMERIC_FIELDS = (
    "score",
    "heat",
    "recommend_index",
    "expectation_index",
    "search_index",
    "max_heat",
)


class RankRepository:
    def __init__(self, database: SystemDatabase) -> None:
        self.database = database

    def store_snapshot(
        self,
        snapshot: RankSnapshot,
        *,
        source_kind: str,
        source_ref: str,
        source_checksum: str,
        parser_version: int,
    ) -> RankWriteResult:
        try:
            with self.database.transaction() as connection:
                return self._store_in_transaction(
                    connection,
                    snapshot,
                    source_kind=source_kind,
                    source_ref=source_ref,
                    source_checksum=source_checksum,
                    parser_version=parser_version,
                )
        except OperationalError as exc:
            raise RankDatabaseUnavailable("榜单数据库暂不可用") from exc
        except DBAPIError as exc:
            if exc.connection_invalidated:
                raise RankDatabaseUnavailable("榜单数据库暂不可用") from exc
            raise

    def _store_in_transaction(
        self,
        connection,
        snapshot: RankSnapshot,
        *,
        source_kind: str,
        source_ref: str,
        source_checksum: str,
        parser_version: int,
    ) -> RankWriteResult:
        checksum_match = connection.execute(
            select(
                rank_snapshots.c.id,
                rank_snapshots.c.record_count,
            ).where(
                rank_snapshots.c.platform == snapshot.platform,
                rank_snapshots.c.source_checksum == source_checksum,
            )
        ).mappings().first()
        if checksum_match is not None:
            return RankWriteResult(
                action="skipped",
                snapshot_id=int(checksum_match["id"]),
                record_count=int(checksum_match["record_count"]),
            )

        source_match = connection.execute(
            select(rank_snapshots.c.id).where(
                rank_snapshots.c.platform == snapshot.platform,
                rank_snapshots.c.source_kind == source_kind,
                rank_snapshots.c.source_ref == source_ref,
            )
        ).scalar_one_or_none()
        values = {
            "platform": snapshot.platform,
            "captured_at": snapshot.captured_at,
            "snapshot_date": snapshot.captured_at.date(),
            "job_id": snapshot.job_id or None,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "source_checksum": source_checksum,
            "parser_version": parser_version,
            "record_count": len(snapshot.records),
            "validation_json": snapshot.validation,
            "imported_at": func.now(),
        }
        if source_match is None:
            snapshot_id = int(
                connection.execute(
                    insert(rank_snapshots).values(**values).returning(rank_snapshots.c.id)
                ).scalar_one()
            )
            action = "imported"
        else:
            snapshot_id = int(source_match)
            connection.execute(
                delete(rank_entries).where(rank_entries.c.snapshot_id == snapshot_id)
            )
            connection.execute(
                update(rank_snapshots)
                .where(rank_snapshots.c.id == snapshot_id)
                .values(**values)
            )
            action = "updated"

        entry_values = [
            self._entry_values(snapshot_id, record)
            for record in snapshot.records
        ]
        if entry_values:
            connection.execute(insert(rank_entries), entry_values)
        return RankWriteResult(
            action=action,
            snapshot_id=snapshot_id,
            record_count=len(entry_values),
        )

    @staticmethod
    def _entry_values(snapshot_id: int, record: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "section_name": _text(
                record.get("board_section")
                or record.get("tab")
                or record.get("category_label")
            ),
            "ranking_name": _text(
                record.get("rank_list")
                or record.get("category_label")
                or record.get("group")
            ),
            "rank": _positive_integer(record.get("rank")),
            "series_key": _text(
                record.get("series_id")
                or record.get("record_id")
                or record.get("id")
            ),
            "title": _text(record.get("title")),
            "category": _optional_text(record.get("category")),
            "status": _optional_text(record.get("status")),
            "subject": _optional_text(record.get("subject")),
            "description": _optional_text(record.get("description")),
            "hot_comment": _optional_text(record.get("hot_comment")),
            "source_record_json": record,
            "validation_warnings_json": list(
                record.get("validation_warnings") or []
            ),
        }
        values.update(
            {field: _optional_count(record.get(field)) for field in COUNT_FIELDS}
        )
        values.update(
            {field: _optional_decimal(record.get(field)) for field in NUMERIC_FIELDS}
        )
        return values

    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        if platform not in {"dy", "hg"}:
            raise ValueError("platform must be dy or hg")
        try:
            with self.database.connection() as connection:
                snapshot_rows = connection.execute(
                    select(rank_snapshots)
                    .where(rank_snapshots.c.platform == platform)
                    .order_by(
                        rank_snapshots.c.captured_at,
                        rank_snapshots.c.source_ref,
                    )
                ).mappings().all()
                if not snapshot_rows:
                    return []
                snapshot_ids = [int(row["id"]) for row in snapshot_rows]
                entry_rows = connection.execute(
                    select(
                        rank_entries.c.snapshot_id,
                        rank_entries.c.source_record_json,
                    )
                    .where(rank_entries.c.snapshot_id.in_(snapshot_ids))
                    .order_by(rank_entries.c.snapshot_id, rank_entries.c.id)
                ).mappings().all()
        except OperationalError as exc:
            raise RankDatabaseUnavailable("榜单数据库暂不可用") from exc
        except DBAPIError as exc:
            if exc.connection_invalidated:
                raise RankDatabaseUnavailable("榜单数据库暂不可用") from exc
            raise

        records_by_snapshot: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in entry_rows:
            records_by_snapshot[int(row["snapshot_id"])].append(
                _json_value(row["source_record_json"])
            )
        return [
            RankSnapshot(
                platform=str(row["platform"]),
                job_id=str(row["job_id"] or ""),
                source_path=Path(str(row["source_ref"])),
                captured_at=row["captured_at"],
                records=records_by_snapshot[int(row["id"])],
                validation=_json_value(row["validation_json"]),
            )
            for row in snapshot_rows
        ]

    def revision(self, platform: str) -> str:
        if platform not in {"dy", "hg"}:
            raise ValueError("platform must be dy or hg")
        try:
            with self.database.connection() as connection:
                rows = connection.execute(
                    select(
                        rank_snapshots.c.id,
                        rank_snapshots.c.source_checksum,
                        rank_snapshots.c.imported_at,
                    )
                    .where(rank_snapshots.c.platform == platform)
                    .order_by(rank_snapshots.c.id)
                ).all()
        except OperationalError as exc:
            raise RankDatabaseUnavailable("榜单数据库暂不可用") from exc
        except DBAPIError as exc:
            if exc.connection_invalidated:
                raise RankDatabaseUnavailable("榜单数据库暂不可用") from exc
            raise
        payload = [
            (int(row.id), str(row.source_checksum), row.imported_at.isoformat())
            for row in rows
        ]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _positive_integer(value: Any) -> int:
    number = int(Decimal(str(value)))
    if number <= 0:
        raise ValueError("rank must be positive")
    return number


def _optional_count(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if number != number.to_integral_value():
        return None
    return int(number)


def _optional_decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value
