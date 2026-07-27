# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Schedule definitions, execution tracking, and local failure alerts."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from core.job import CrawlJob


logger = logging.getLogger("aiworker.scheduler")

_ACTIVE_STATUSES = frozenset({"queued", "running"})
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_JOB_TYPES = {
    "crawl": frozenset({"search", "detail", "creator"}),
    "ranking_update": frozenset({"rank"}),
    "asset_download": frozenset(
        {"drama_download", "douyin_fallback_analysis", "drama_reviews"}
    ),
}
_RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))


@dataclass(frozen=True)
class ScheduleDefinition:
    id: str
    name: str
    job_type: str
    payload: dict[str, Any]
    trigger_type: str
    trigger_config: dict[str, Any]
    timezone: str
    is_enabled: bool
    max_retries: int = 3
    max_concurrent_runs: int = 1
    misfire_policy: str = "skip"
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class ScheduleRun:
    id: str
    schedule_id: str
    attempt: int
    status: str
    scheduled_at: datetime
    job_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_summary: str | None = None
    retry_of_run_id: str | None = None


@dataclass(frozen=True)
class ScheduleAlert:
    id: str
    schedule_id: str
    run_id: str
    severity: str
    message: str
    created_at: datetime


class ScheduleRepository(Protocol):
    def create_definition(self, definition: ScheduleDefinition) -> None: ...

    def get_definition(self, schedule_id: str) -> ScheduleDefinition: ...

    def list_enabled_definitions(self) -> list[ScheduleDefinition]: ...

    def set_definition_enabled(self, schedule_id: str, enabled: bool) -> None: ...

    def reserve_run(self, schedule_id: str, scheduled_at: datetime) -> ScheduleRun: ...

    def get_run(self, run_id: str) -> ScheduleRun: ...

    def list_active_runs(self) -> list[ScheduleRun]: ...

    def update_run(self, run_id: str, **changes: Any) -> ScheduleRun: ...

    def create_retry_run(self, run: ScheduleRun, scheduled_at: datetime) -> ScheduleRun: ...

    def list_retry_runs(self, schedule_id: str) -> list[ScheduleRun]: ...

    def record_alert(self, alert: ScheduleAlert) -> None: ...

    def list_alerts(self, schedule_id: str) -> list[ScheduleAlert]: ...

    def consecutive_failures(self, schedule_id: str) -> int: ...


class LocalScheduleService:
    """Schedules whitelisted jobs without taking ownership of worker processes."""

    def __init__(
        self,
        repository: ScheduleRepository,
        job_manager: Any,
        scheduler: BackgroundScheduler | None = None,
    ) -> None:
        self.repository = repository
        self.job_manager = job_manager
        self.scheduler = scheduler or BackgroundScheduler(timezone="Asia/Shanghai")
        self._started = False
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self.scheduler.start()
            self.scheduler.add_job(
                self.reconcile_active_runs,
                "interval",
                seconds=10,
                id="local-schedule-reconcile",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            for definition in self.repository.list_enabled_definitions():
                self._schedule_definition(definition)
            self._started = True

    def shutdown(self) -> None:
        """Stop future triggers only; JobManager remains responsible for submitted jobs."""
        with self._lock:
            if self._started:
                self.scheduler.shutdown(wait=False)
                self._started = False

    def trigger(self, schedule_id: str, scheduled_at: datetime | None = None) -> ScheduleRun:
        now = scheduled_at or datetime.now(UTC)
        run = self.repository.reserve_run(schedule_id, now)
        if run.status == "skipped":
            return run
        return self._submit_run(run, now)

    def trigger_retry(self, run_id: str, scheduled_at: datetime | None = None) -> ScheduleRun:
        now = scheduled_at or datetime.now(UTC)
        run = self.repository.get_run(run_id)
        if run.status != "queued" or run.retry_of_run_id is None:
            raise ValueError("only queued retry runs can be triggered")
        return self._submit_run(run, now)

    def reconcile_active_runs(self, now: datetime | None = None) -> None:
        completed_at = now or datetime.now(UTC)
        for run in self.repository.list_active_runs():
            if not run.job_id:
                continue
            job = self.job_manager.get_job(run.job_id)
            status = str(job.get("status") or "")
            if status not in _TERMINAL_STATUSES:
                continue
            error_summary = self._error_summary(job) if status == "failed" else None
            self.repository.update_run(
                run.id,
                status=status,
                finished_at=completed_at,
                error_summary=error_summary,
            )
            if status == "failed":
                self._handle_failure(self.repository.get_run(run.id), completed_at)

    def _submit_run(self, run: ScheduleRun, started_at: datetime) -> ScheduleRun:
        definition = self.repository.get_definition(run.schedule_id)
        if not definition.is_enabled:
            return self.repository.update_run(
                run.id, status="skipped", finished_at=started_at, error_summary="schedule disabled"
            )
        payload = self._job_payload(definition, run.id)
        try:
            state = self.job_manager.create_job(payload)
        except Exception as exc:
            failed = self.repository.update_run(
                run.id,
                status="failed",
                started_at=started_at,
                finished_at=started_at,
                error_summary=self._error_summary({"message": str(exc)}),
            )
            self._handle_failure(failed, started_at)
            return failed
        return self.repository.update_run(
            run.id, job_id=str(state.job_id), status="running", started_at=started_at
        )

    def _handle_failure(self, run: ScheduleRun, now: datetime) -> None:
        definition = self.repository.get_definition(run.schedule_id)
        message = run.error_summary or "scheduled job failed"
        self.repository.record_alert(
            ScheduleAlert(
                id=str(uuid.uuid4()),
                schedule_id=run.schedule_id,
                run_id=run.id,
                severity="error",
                message=message,
                created_at=now,
            )
        )
        logger.error(
            "Scheduled job failed: schedule_id=%s run_id=%s job_id=%s message=%s",
            run.schedule_id,
            run.id,
            run.job_id,
            message,
        )
        if self.repository.consecutive_failures(run.schedule_id) >= 3:
            self.repository.set_definition_enabled(run.schedule_id, False)
            logger.error("Scheduled job paused after repeated failures: schedule_id=%s", run.schedule_id)
            return
        if run.attempt <= definition.max_retries:
            retry_at = now + _RETRY_DELAYS[min(run.attempt - 1, len(_RETRY_DELAYS) - 1)]
            retry = self.repository.create_retry_run(run, retry_at)
            if self._started:
                self._schedule_retry(retry)

    def _schedule_definition(self, definition: ScheduleDefinition) -> None:
        if definition.trigger_type == "cron":
            trigger = CronTrigger(timezone=definition.timezone, **definition.trigger_config)
        elif definition.trigger_type == "date":
            run_date = datetime.fromisoformat(str(definition.trigger_config["run_date"]))
            trigger = DateTrigger(run_date=run_date, timezone=definition.timezone)
        else:
            raise ValueError("trigger_type must be cron or date")
        self.scheduler.add_job(
            self.trigger,
            trigger=trigger,
            args=[definition.id],
            id=f"local-schedule:{definition.id}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1 if definition.misfire_policy == "skip" else None,
        )

    def _schedule_retry(self, run: ScheduleRun) -> None:
        self.scheduler.add_job(
            self.trigger_retry,
            trigger=DateTrigger(run_date=run.scheduled_at),
            args=[run.id],
            id=f"local-schedule:retry:{run.id}",
            replace_existing=True,
            misfire_grace_time=1,
        )

    @staticmethod
    def _job_payload(definition: ScheduleDefinition, run_id: str) -> dict[str, Any]:
        allowed_types = _JOB_TYPES.get(definition.job_type)
        if allowed_types is None:
            raise ValueError("unsupported scheduled job type")
        payload = dict(definition.payload)
        crawler_type = str(payload.get("crawler_type") or "")
        if crawler_type not in allowed_types:
            raise ValueError("scheduled job payload is not allowed for its job type")
        payload["job_id"] = str(payload.get("job_id") or uuid.uuid4())
        job = CrawlJob.from_dict(payload)
        job.extra = dict(job.extra)
        job.extra["schedule_id"] = definition.id
        job.extra["schedule_run_id"] = run_id
        return job.to_dict()

    @staticmethod
    def _error_summary(job: dict[str, Any]) -> str:
        message = str(job.get("message") or job.get("error") or "scheduled job failed")
        return message.replace("\n", " ").strip()[:500]

