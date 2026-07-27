# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Behavior tests for local scheduled job governance."""

from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import replace
import threading
import uuid

import pytest

from scheduler.local_service import (
    LocalScheduleService,
    ScheduleAlert,
    ScheduleDefinition,
    ScheduleRun,
)


class InMemoryScheduleRepository:
    def __init__(self) -> None:
        self._definitions: dict[str, ScheduleDefinition] = {}
        self._runs: dict[str, ScheduleRun] = {}
        self._alerts: list[ScheduleAlert] = []
        self._lock = threading.RLock()

    def create_definition(self, item: ScheduleDefinition) -> None:
        self._definitions[item.id] = item

    def get_definition(self, schedule_id: str) -> ScheduleDefinition:
        return self._definitions[schedule_id]

    def list_enabled_definitions(self) -> list[ScheduleDefinition]:
        return [item for item in self._definitions.values() if item.is_enabled]

    def set_definition_enabled(self, schedule_id: str, enabled: bool) -> None:
        self._definitions[schedule_id] = replace(self._definitions[schedule_id], is_enabled=enabled)

    def reserve_run(self, schedule_id: str, scheduled_at: datetime) -> ScheduleRun:
        with self._lock:
            definition = self.get_definition(schedule_id)
            active = sum(
                run.schedule_id == schedule_id and run.status in {"queued", "running"}
                for run in self._runs.values()
            )
            status = "queued" if definition.is_enabled and active < definition.max_concurrent_runs else "skipped"
            run = ScheduleRun(
                id=str(uuid.uuid4()),
                schedule_id=schedule_id,
                attempt=1,
                status=status,
                scheduled_at=scheduled_at,
                finished_at=scheduled_at if status == "skipped" else None,
            )
            self._runs[run.id] = run
            return run

    def get_run(self, run_id: str) -> ScheduleRun:
        return self._runs[run_id]

    def list_active_runs(self) -> list[ScheduleRun]:
        return [run for run in self._runs.values() if run.status in {"queued", "running"}]

    def update_run(self, run_id: str, **changes) -> ScheduleRun:
        self._runs[run_id] = replace(self._runs[run_id], **changes)
        return self._runs[run_id]

    def create_retry_run(self, run: ScheduleRun, scheduled_at: datetime) -> ScheduleRun:
        retry = ScheduleRun(
            id=str(uuid.uuid4()),
            schedule_id=run.schedule_id,
            attempt=run.attempt + 1,
            status="queued",
            scheduled_at=scheduled_at,
            retry_of_run_id=run.id,
        )
        self._runs[retry.id] = retry
        return retry

    def list_retry_runs(self, schedule_id: str) -> list[ScheduleRun]:
        return [
            run
            for run in self._runs.values()
            if run.schedule_id == schedule_id and run.retry_of_run_id and run.status == "queued"
        ]

    def record_alert(self, alert: ScheduleAlert) -> None:
        self._alerts.append(alert)

    def list_alerts(self, schedule_id: str) -> list[ScheduleAlert]:
        return [alert for alert in self._alerts if alert.schedule_id == schedule_id]

    def consecutive_failures(self, schedule_id: str) -> int:
        runs = sorted(
            (run for run in self._runs.values() if run.schedule_id == schedule_id),
            key=lambda item: item.finished_at or item.scheduled_at,
            reverse=True,
        )
        count = 0
        for run in runs:
            if run.status == "failed":
                count += 1
            elif run.status == "succeeded":
                break
        return count


class RecordingJobManager:
    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.stop_calls: list[str] = []

    def create_job(self, payload: dict):
        self.payloads.append(payload)
        job_id = payload["job_id"]
        self.jobs[job_id] = {"job_id": job_id, "status": "running"}
        return type("JobState", (), {"job_id": job_id})()

    def get_job(self, job_id: str) -> dict:
        return self.jobs[job_id]

    def stop(self, job_id: str) -> None:
        self.stop_calls.append(job_id)


class RecordingScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def start(self) -> None:
        return None

    def shutdown(self, *, wait: bool) -> None:
        del wait

    def add_job(self, func, *args, **kwargs) -> None:
        del func, args
        self.jobs.append(kwargs)


@pytest.fixture
def repository() -> InMemoryScheduleRepository:
    return InMemoryScheduleRepository()


def definition(
    schedule_id: str,
    job_type: str,
    payload: dict,
    *,
    max_retries: int = 3,
    max_concurrent_runs: int = 1,
) -> ScheduleDefinition:
    return ScheduleDefinition(
        id=schedule_id,
        name=schedule_id,
        job_type=job_type,
        payload=payload,
        trigger_type="date",
        trigger_config={"run_date": "2026-07-27T10:00:00+08:00"},
        timezone="Asia/Shanghai",
        is_enabled=True,
        max_retries=max_retries,
        max_concurrent_runs=max_concurrent_runs,
    )


@pytest.mark.parametrize(
    ("job_type", "payload"),
    [
        ("crawl", {"platform": "dy", "crawler_type": "search", "keywords": ["drama"]}),
        ("ranking_update", {"platform": "dy", "crawler_type": "rank"}),
        (
            "asset_download",
            {
                "platform": "hg",
                "crawler_type": "drama_download",
                "extra": {"title": "Example", "book_id": "book-1"},
            },
        ),
    ],
)
def test_triggered_supported_job_is_tracked_and_linked_to_job_manager(
    repository, job_type: str, payload: dict
):
    schedule = definition(f"schedule-{job_type}", job_type, payload)
    repository.create_definition(schedule)
    manager = RecordingJobManager()
    service = LocalScheduleService(repository, manager)

    run = service.trigger(schedule.id, datetime(2026, 7, 27, 2, tzinfo=UTC))

    assert run.status == "running"
    assert run.job_id is not None
    assert manager.payloads[0]["extra"]["schedule_id"] == schedule.id
    assert manager.payloads[0]["extra"]["schedule_run_id"] == run.id


def test_active_run_at_concurrency_limit_is_skipped_without_creating_a_job(repository):
    schedule = definition(
        "schedule-crawl",
        "crawl",
        {"platform": "dy", "crawler_type": "search", "keywords": ["drama"]},
    )
    repository.create_definition(schedule)
    manager = RecordingJobManager()
    service = LocalScheduleService(repository, manager)

    first = service.trigger(schedule.id, datetime(2026, 7, 27, 2, tzinfo=UTC))
    second = service.trigger(schedule.id, datetime(2026, 7, 27, 3, tzinfo=UTC))

    assert first.status == "running"
    assert second.status == "skipped"
    assert len(manager.payloads) == 1


def test_failed_run_is_alerted_and_retried_until_retry_budget_is_exhausted(repository):
    schedule = definition(
        "schedule-retry",
        "ranking_update",
        {"platform": "dy", "crawler_type": "rank"},
        max_retries=1,
    )
    repository.create_definition(schedule)
    manager = RecordingJobManager()
    service = LocalScheduleService(repository, manager)
    first = service.trigger(schedule.id, datetime(2026, 7, 27, 2, tzinfo=UTC))
    manager.jobs[first.job_id]["status"] = "failed"

    service.reconcile_active_runs(now=datetime(2026, 7, 27, 2, 1, tzinfo=UTC))

    failed = repository.get_run(first.id)
    assert failed.status == "failed"
    assert repository.list_alerts(schedule.id)[0].run_id == first.id
    retry = repository.list_retry_runs(schedule.id)[0]
    assert retry.attempt == 2
    assert retry.retry_of_run_id == first.id

    retried = service.trigger_retry(retry.id, datetime(2026, 7, 27, 2, 2, tzinfo=UTC))
    manager.jobs[retried.job_id]["status"] = "failed"
    service.reconcile_active_runs(now=datetime(2026, 7, 27, 2, 3, tzinfo=UTC))

    assert repository.get_definition(schedule.id).is_enabled is True
    assert repository.list_retry_runs(schedule.id) == []


def test_shutdown_only_stops_future_triggers_and_keeps_submitted_jobs_running(repository):
    schedule = definition(
        "schedule-shutdown",
        "crawl",
        {"platform": "dy", "crawler_type": "search", "keywords": ["drama"]},
    )
    repository.create_definition(schedule)
    manager = RecordingJobManager()
    service = LocalScheduleService(repository, manager)
    run = service.trigger(schedule.id, datetime(2026, 7, 27, 2, tzinfo=UTC))

    service.shutdown()

    assert manager.stop_calls == []
    assert manager.jobs[run.job_id]["status"] == "running"


def test_process_shutdown_cancels_submitted_runs_before_database_disposal(repository):
    schedule = definition(
        "schedule-process-shutdown",
        "crawl",
        {"platform": "dy", "crawler_type": "search", "keywords": ["drama"]},
    )
    repository.create_definition(schedule)
    manager = RecordingJobManager()
    service = LocalScheduleService(repository, manager)
    run = service.trigger(schedule.id, datetime(2026, 7, 27, 2, tzinfo=UTC))

    cancelled_job_ids = service.cancel_active_runs_for_process_shutdown(
        now=datetime(2026, 7, 27, 2, 1, tzinfo=UTC)
    )

    assert cancelled_job_ids == {run.job_id}
    assert manager.stop_calls == [run.job_id]
    assert repository.get_run(run.id).status == "cancelled"


def test_start_restores_queued_retry_runs_after_restart(repository):
    schedule = definition(
        "schedule-retry-restore",
        "ranking_update",
        {"platform": "dy", "crawler_type": "rank"},
    )
    repository.create_definition(schedule)
    manager = RecordingJobManager()
    first_service = LocalScheduleService(repository, manager)
    first = first_service.trigger(schedule.id, datetime(2026, 7, 27, 2, tzinfo=UTC))
    manager.jobs[first.job_id]["status"] = "failed"
    first_service.reconcile_active_runs(now=datetime(2026, 7, 27, 2, 1, tzinfo=UTC))
    retry = repository.list_retry_runs(schedule.id)[0]
    scheduler = RecordingScheduler()

    LocalScheduleService(repository, manager, scheduler=scheduler).start()

    assert f"local-schedule:retry:{retry.id}" in {job["id"] for job in scheduler.jobs}
