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

import pytest

import WebGUI
from core.job import CrawlJob
from runtime.job_state import JobState


class RankCreationRecordingManager:
    def __init__(self):
        self.jobs: list[CrawlJob] = []
        self.jobs_by_operation: dict[str, JobState] = {}
        self.operation_ids: list[str] = []
        self.operation_payloads: list[dict] = []

    def generate_job_id(self, platform: str) -> str:
        return f"{platform}-daily-rank-{len(self.jobs) + 1}"

    def get_or_create_job(self, operation_id, payload, **_kwargs):
        self.operation_ids.append(operation_id)
        self.operation_payloads.append(dict(payload))
        existing = self.jobs_by_operation.get(operation_id)
        if existing is not None:
            return existing
        data = dict(payload)
        data.setdefault("job_id", self.generate_job_id(str(data["platform"])))
        state = JobState(job=CrawlJob.from_dict(data))
        self.jobs.append(state.job)
        self.jobs_by_operation[operation_id] = state
        return state

    def find_job_by_operation(self, operation_id):
        state = self.jobs_by_operation.get(operation_id)
        if state is None:
            return None
        return {"job_id": state.job_id, "status": state.status}

    def create_job(self, payload, **_kwargs):
        data = dict(payload)
        data.setdefault("job_id", self.generate_job_id(str(data["platform"])))
        state = JobState(job=CrawlJob.from_dict(data))
        self.jobs.append(state.job)
        return state

    def get_job(self, job_id: str):
        job = next(job for job in self.jobs if job.job_id == job_id)
        return {
            "job_id": job.job_id,
            "platform": job.platform,
            "crawler_type": job.crawler_type,
            "status": "pending",
            "job": job.to_dict(),
            "created_by_user_id": None,
            "workflow_task_id": None,
            "project_id": None,
        }


@pytest.mark.asyncio
async def test_rank_job_creation_is_rejected_after_today_snapshot_exists(monkeypatch):
    manager = RankCreationRecordingManager()
    monkeypatch.setattr(WebGUI, "job_manager", manager)
    monkeypatch.setattr(
        WebGUI,
        "today_rank_service",
        _RankStatusService(has_today_snapshot=True),
    )

    response = await WebGUI.create_job(
        WebGUI.JobCreateRequest(platform="dy", crawler_type="rank"),
        _system_admin(),
        None,
    )

    assert response.status_code == 409
    assert json.loads(response.body)["message"] == "今日抖音榜单已更新"
    assert manager.jobs == []


@pytest.mark.asyncio
async def test_rank_job_creation_reuses_one_daily_operation(monkeypatch):
    manager = RankCreationRecordingManager()
    monkeypatch.setattr(WebGUI, "job_manager", manager)
    monkeypatch.setattr(
        WebGUI,
        "today_rank_service",
        _RankStatusService(has_today_snapshot=False),
    )
    monkeypatch.setattr(WebGUI, "_record_job_audit", lambda *_args, **_kwargs: None)
    payload = WebGUI.JobCreateRequest(platform="dy", crawler_type="rank")
    admin = _system_admin()

    first = await WebGUI.create_job(payload, admin, None)
    second = await WebGUI.create_job(payload, admin, None)

    assert len(manager.jobs) == 1
    assert manager.operation_ids == [
        "daily-rank:dy:2026-09-02",
        "daily-rank:dy:2026-09-02",
    ]
    assert first["job"]["job_id"] == second["job"]["job_id"]


@pytest.mark.asyncio
async def test_hongguo_rank_job_keeps_the_existing_non_daily_creation_flow(monkeypatch):
    manager = RankCreationRecordingManager()

    class DouyinOnlyStatusService:
        @staticmethod
        def daily_status(platform: str) -> dict:
            raise AssertionError(f"unexpected daily status check for {platform}")

    monkeypatch.setattr(WebGUI, "job_manager", manager)
    monkeypatch.setattr(WebGUI, "today_rank_service", DouyinOnlyStatusService())
    monkeypatch.setattr(WebGUI, "_record_job_audit", lambda *_args, **_kwargs: None)

    response = await WebGUI.create_job(
        WebGUI.JobCreateRequest(platform="hg", crawler_type="rank"),
        _system_admin(),
        None,
    )

    assert response["job"]["platform"] == "hg"
    assert len(manager.jobs) == 1
    assert manager.operation_ids == []


@pytest.mark.asyncio
async def test_rank_job_that_finishes_after_status_scan_is_not_retried(monkeypatch):
    manager = RankCreationRecordingManager()
    operation_id = "daily-rank:dy:2026-09-02"
    running = JobState(
        job=CrawlJob(
            job_id="daily-rank-running",
            platform="dy",
            crawler_type="rank",
        ),
        status="running",
    )
    manager.jobs.append(running.job)
    manager.jobs_by_operation[operation_id] = running
    monkeypatch.setattr(WebGUI, "job_manager", manager)
    monkeypatch.setattr(
        WebGUI,
        "today_rank_service",
        _RankStatusService(has_today_snapshot=False),
    )
    monkeypatch.setattr(WebGUI, "_record_job_audit", lambda *_args, **_kwargs: None)

    response = await WebGUI.create_job(
        WebGUI.JobCreateRequest(platform="dy", crawler_type="rank"),
        _system_admin(),
        None,
    )

    assert response["job"]["job_id"] == "daily-rank-running"
    assert "retry_terminal_operation" not in manager.operation_payloads[0]["extra"]


class _RankStatusService:
    def __init__(self, has_today_snapshot: bool):
        self.has_today_snapshot = has_today_snapshot

    def daily_status(self, platform: str) -> dict:
        assert platform == "dy"
        return {
            "business_date": "2026-09-02",
            "has_today_snapshot": self.has_today_snapshot,
        }


def _system_admin() -> WebGUI.Principal:
    return WebGUI.Principal(
        user_id="rank-admin",
        username="rank-admin",
        display_name="系统管理员",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="rank-admin-session",
    )
