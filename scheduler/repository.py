# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""PostgreSQL persistence for local schedule governance."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import RowMapping

from scheduler.local_service import ScheduleAlert, ScheduleDefinition, ScheduleRun
from system_database.database import SystemDatabase
from system_database.schema import scheduling_alerts, scheduling_definitions, scheduling_runs


_ACTIVE_STATUSES = ("queued", "running")


class PostgresScheduleRepository:
    """Keeps schedule reservation and history writes in database transactions."""

    def __init__(self, database: SystemDatabase) -> None:
        self.database = database

    def create_definition(self, definition: ScheduleDefinition) -> None:
        with self.database.transaction() as connection:
            connection.execute(insert(scheduling_definitions).values(**self._definition_values(definition)))

    def get_definition(self, schedule_id: str) -> ScheduleDefinition:
        with self.database.connection() as connection:
            row = connection.execute(
                select(scheduling_definitions).where(scheduling_definitions.c.id == schedule_id)
            ).mappings().one()
        return self._definition_from_row(row)

    def list_enabled_definitions(self) -> list[ScheduleDefinition]:
        with self.database.connection() as connection:
            rows = connection.execute(
                select(scheduling_definitions).where(scheduling_definitions.c.is_enabled == 1)
            ).mappings()
            return [self._definition_from_row(row) for row in rows]

    def update_definition(
        self, schedule_id: str, definition: ScheduleDefinition
    ) -> ScheduleDefinition:
        values = self._definition_values(definition)
        values.pop("id", None)
        with self.database.transaction() as connection:
            result = connection.execute(
                update(scheduling_definitions)
                .where(scheduling_definitions.c.id == schedule_id)
                .values(**values, updated_at=func.now())
            )
            if result.rowcount != 1:
                raise KeyError(schedule_id)
            row = connection.execute(
                select(scheduling_definitions).where(
                    scheduling_definitions.c.id == schedule_id
                )
            ).mappings().one()
        return self._definition_from_row(row)

    def list_definitions(self) -> list[ScheduleDefinition]:
        with self.database.connection() as connection:
            rows = connection.execute(select(scheduling_definitions).order_by(scheduling_definitions.c.created_at.desc())).mappings()
            return [self._definition_from_row(row) for row in rows]

    def delete_definition(self, schedule_id: str) -> None:
        with self.database.transaction() as connection:
            result = connection.execute(scheduling_definitions.delete().where(scheduling_definitions.c.id == schedule_id))
            if result.rowcount != 1:
                raise KeyError(schedule_id)

    def set_definition_enabled(self, schedule_id: str, enabled: bool) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                update(scheduling_definitions)
                .where(scheduling_definitions.c.id == schedule_id)
                .values(is_enabled=1 if enabled else 0, updated_at=func.now())
            )

    def reserve_run(self, schedule_id: str, scheduled_at: datetime) -> ScheduleRun:
        with self.database.transaction() as connection:
            definition_row = connection.execute(
                select(scheduling_definitions)
                .where(scheduling_definitions.c.id == schedule_id)
                .with_for_update()
            ).mappings().one()
            definition = self._definition_from_row(definition_row)
            active_count = connection.execute(
                select(func.count())
                .select_from(scheduling_runs)
                .where(scheduling_runs.c.schedule_id == schedule_id)
                .where(scheduling_runs.c.status.in_(_ACTIVE_STATUSES))
            ).scalar_one()
            status = "queued" if definition.is_enabled and active_count < definition.max_concurrent_runs else "skipped"
            run = ScheduleRun(
                id=str(uuid.uuid4()),
                schedule_id=schedule_id,
                attempt=1,
                status=status,
                scheduled_at=scheduled_at,
                finished_at=scheduled_at if status == "skipped" else None,
            )
            connection.execute(insert(scheduling_runs).values(**self._run_values(run)))
        return run

    def get_run(self, run_id: str) -> ScheduleRun:
        with self.database.connection() as connection:
            row = connection.execute(
                select(scheduling_runs).where(scheduling_runs.c.id == run_id)
            ).mappings().one()
        return self._run_from_row(row)

    def list_active_runs(self) -> list[ScheduleRun]:
        with self.database.connection() as connection:
            rows = connection.execute(
                select(scheduling_runs).where(scheduling_runs.c.status.in_(_ACTIVE_STATUSES))
            ).mappings()
            return [self._run_from_row(row) for row in rows]

    def list_runs(self, schedule_id: str, limit: int = 100) -> list[ScheduleRun]:
        with self.database.connection() as connection:
            rows = connection.execute(select(scheduling_runs).where(scheduling_runs.c.schedule_id == schedule_id).order_by(scheduling_runs.c.scheduled_at.desc()).limit(max(1, min(limit, 500)))).mappings()
            return [self._run_from_row(row) for row in rows]

    def update_run(self, run_id: str, **changes: Any) -> ScheduleRun:
        allowed = {"job_id", "status", "started_at", "finished_at", "error_summary"}
        values = {key: value for key, value in changes.items() if key in allowed}
        with self.database.transaction() as connection:
            connection.execute(
                update(scheduling_runs)
                .where(scheduling_runs.c.id == run_id)
                .values(**values)
            )
            row = connection.execute(
                select(scheduling_runs).where(scheduling_runs.c.id == run_id)
            ).mappings().one()
        return self._run_from_row(row)

    def create_retry_run(self, run: ScheduleRun, scheduled_at: datetime) -> ScheduleRun:
        retry = ScheduleRun(
            id=str(uuid.uuid4()),
            schedule_id=run.schedule_id,
            attempt=run.attempt + 1,
            status="queued",
            scheduled_at=scheduled_at,
            retry_of_run_id=run.id,
        )
        with self.database.transaction() as connection:
            connection.execute(insert(scheduling_runs).values(**self._run_values(retry)))
        return retry

    def list_retry_runs(self, schedule_id: str) -> list[ScheduleRun]:
        with self.database.connection() as connection:
            rows = connection.execute(
                select(scheduling_runs)
                .where(scheduling_runs.c.schedule_id == schedule_id)
                .where(scheduling_runs.c.retry_of_run_id.is_not(None))
                .where(scheduling_runs.c.status == "queued")
            ).mappings()
            return [self._run_from_row(row) for row in rows]

    def record_alert(self, alert: ScheduleAlert) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                insert(scheduling_alerts).values(
                    id=alert.id,
                    schedule_id=alert.schedule_id,
                    run_id=alert.run_id,
                    severity=alert.severity,
                    message=alert.message,
                    created_at=alert.created_at,
                )
            )

    def list_alerts(self, schedule_id: str) -> list[ScheduleAlert]:
        with self.database.connection() as connection:
            rows = connection.execute(
                select(scheduling_alerts)
                .where(scheduling_alerts.c.schedule_id == schedule_id)
                .order_by(scheduling_alerts.c.created_at.desc())
            ).mappings()
            return [self._alert_from_row(row) for row in rows]

    def consecutive_failures(self, schedule_id: str) -> int:
        with self.database.connection() as connection:
            rows = connection.execute(
                select(scheduling_runs.c.status)
                .where(scheduling_runs.c.schedule_id == schedule_id)
                .order_by(scheduling_runs.c.finished_at.desc().nullslast())
            ).scalars()
            count = 0
            for status in rows:
                if status == "failed":
                    count += 1
                elif status == "succeeded":
                    break
            return count

    @staticmethod
    def _definition_values(definition: ScheduleDefinition) -> dict[str, Any]:
        return {
            "id": definition.id,
            "name": definition.name,
            "job_type": definition.job_type,
            "payload_json": definition.payload,
            "trigger_type": definition.trigger_type,
            "trigger_config_json": definition.trigger_config,
            "timezone": definition.timezone,
            "is_enabled": 1 if definition.is_enabled else 0,
            "max_retries": definition.max_retries,
            "max_concurrent_runs": definition.max_concurrent_runs,
            "misfire_policy": definition.misfire_policy,
            "created_by": definition.created_by,
            "updated_by": definition.updated_by,
        }

    @staticmethod
    def _run_values(run: ScheduleRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "schedule_id": run.schedule_id,
            "job_id": run.job_id,
            "attempt": run.attempt,
            "status": run.status,
            "scheduled_at": run.scheduled_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error_summary": run.error_summary,
            "retry_of_run_id": run.retry_of_run_id,
        }

    @staticmethod
    def _definition_from_row(row: RowMapping) -> ScheduleDefinition:
        return ScheduleDefinition(
            id=row["id"],
            name=row["name"],
            job_type=row["job_type"],
            payload=dict(row["payload_json"]),
            trigger_type=row["trigger_type"],
            trigger_config=dict(row["trigger_config_json"]),
            timezone=row["timezone"],
            is_enabled=bool(row["is_enabled"]),
            max_retries=row["max_retries"],
            max_concurrent_runs=row["max_concurrent_runs"],
            misfire_policy=row["misfire_policy"],
            created_by=row["created_by"],
            updated_by=row["updated_by"],
        )

    @staticmethod
    def _run_from_row(row: RowMapping) -> ScheduleRun:
        return ScheduleRun(
            id=row["id"],
            schedule_id=row["schedule_id"],
            job_id=row["job_id"],
            attempt=row["attempt"],
            status=row["status"],
            scheduled_at=row["scheduled_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error_summary=row["error_summary"],
            retry_of_run_id=row["retry_of_run_id"],
        )

    @staticmethod
    def _alert_from_row(row: RowMapping) -> ScheduleAlert:
        return ScheduleAlert(
            id=row["id"],
            schedule_id=row["schedule_id"],
            run_id=row["run_id"],
            severity=row["severity"],
            message=row["message"],
            created_at=row["created_at"],
        )
