# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import time

from creative.prompt_observability import PromptObservabilityService
from creative.prompt_registry import builtin_template
from creative.prompt_store import PromptStore
from creative.repository import CreativeRepository


class FakeJobManager:
    def __init__(self, jobs):
        self.jobs = {job["job_id"]: job for job in jobs}

    def list_jobs(self):
        return list(self.jobs.values())

    def get_job(self, job_id):
        return self.jobs[job_id]


def _project(repository: CreativeRepository) -> int:
    now = time.time()
    with repository._connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO creative_projects (
                name, target_episode_count, episode_duration_seconds,
                created_by, creation_mode, workflow_mode, created_at, updated_at
            ) VALUES ('可观测性项目', 12, 120, 'owner-1', 'original', 'sequential', ?, ?)
            """,
            (now, now),
        )
    return int(cursor.lastrowid)


def _job(
    project_id: int,
    job_id: str,
    *,
    revision: int | None,
    status: str,
    total_tokens: int = 100,
    duration: float = 10,
    include_usage: bool = True,
    template_key: str = "generic-vertical-short",
):
    extra = {
        "project_id": project_id,
        "normalized_stage": "brief",
        "llm_price_snapshot": {
            "model_record_id": "model-1",
            "input_price_per_million": 2,
            "output_price_per_million": 8,
            "price_currency": "CNY",
            "complete": True,
        },
    }
    if revision is not None:
        extra.update(
            {
                "prompt_template_key": template_key,
                "prompt_template_revision": revision,
                "prompt_content_hash": f"hash-{revision}",
            }
        )
    payload = {}
    if include_usage:
        payload["llm_usage"] = {
            "call_count": 1,
            "reported_call_count": 1,
            "input_reported_call_count": 1,
            "output_reported_call_count": 1,
            "total_reported_call_count": 1,
            "input_tokens": total_tokens // 2,
            "output_tokens": total_tokens - total_tokens // 2,
            "total_tokens": total_tokens,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "complete": True,
        }
    event = "job_finished" if status == "succeeded" else "job_failed"
    created_at = 1_700_000_000 + int(job_id.split("-")[-1])
    return {
        "job_id": job_id,
        "status": status,
        "created_at": created_at,
        "started_at": created_at,
        "finished_at": created_at + duration,
        "job": {
            "job_id": job_id,
            "crawler_type": "phase4_generate",
            "extra": extra,
        },
        "events": [{"event": event, "payload": payload}],
    }


def _brief_signal(
    repository: CreativeRepository,
    project_id: int,
    job_id: str,
    *,
    revision: int,
    adopted: bool,
) -> None:
    now = time.time()
    meta = {
        "job_id": job_id,
        "prompt_template_key": "generic-vertical-short",
        "prompt_template_revision": revision,
        "prompt_content_hash": f"hash-{revision}",
    }
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO creative_blueprints (
                project_id, candidate_group_id, status, content_json,
                generation_meta_json, created_at, updated_at
            ) VALUES (?, ?, ?, '{}', ?, ?, ?)
            """,
            (
                project_id,
                f"candidate-{job_id}",
                "brief_locked" if adopted else "draft",
                json.dumps(meta, ensure_ascii=False),
                now,
                now,
            ),
        )


def test_observability_uses_real_usage_frozen_prices_and_explains_regressions(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    PromptStore(repository.db_path)
    project_id = _project(repository)
    jobs = []
    for index in range(1, 6):
        job_id = f"job-{index}"
        jobs.append(
            _job(project_id, job_id, revision=1, status="succeeded")
        )
        _brief_signal(
            repository,
            project_id,
            job_id,
            revision=1,
            adopted=index <= 4,
        )
    for index in range(6, 11):
        succeeded = index <= 8
        job_id = f"job-{index}"
        jobs.append(
            _job(
                project_id,
                job_id,
                revision=2,
                status="succeeded" if succeeded else "failed",
                total_tokens=160,
                duration=20,
            )
        )
        if succeeded:
            _brief_signal(
                repository,
                project_id,
                job_id,
                revision=2,
                adopted=index == 6,
            )
    jobs.append(
        _job(
            project_id,
            "job-11",
            revision=3,
            status="succeeded",
            include_usage=False,
        )
    )
    _brief_signal(
        repository, project_id, "job-11", revision=3, adopted=False
    )
    jobs.append(
        _job(
            project_id,
            "job-12",
            revision=None,
            status="failed",
            include_usage=False,
        )
    )

    report = PromptObservabilityService(
        repository, FakeJobManager(jobs)
    ).report(project_id=project_id, actor_user_id="owner-1")

    versions = {
        item["template_revision"]: item for item in report["versions"]
    }
    assert report["summary"]["terminal_jobs_scanned"] == 12
    assert report["summary"]["unattributed_jobs"] == 1
    assert report["summary"]["attribution_coverage"] == 0.9167
    assert versions[1]["jobs"]["success_rate"] == 1
    assert versions[2]["jobs"]["failure_rate"] == 0.4
    assert versions[2]["tokens"]["average_total_tokens"] == 160
    assert versions[2]["tokens"]["observed"]["total_tokens"] == 800
    assert versions[2]["cost"]["by_currency"][0]["currency"] == "CNY"
    assert versions[2]["adoption"]["rate"] == 0.3333
    assert versions[3]["tokens"]["average_total_tokens"] is None
    assert versions[3]["tokens"]["job_coverage"] == 0
    metrics = {item["metric"] for item in report["alerts"]}
    assert {
        "failure_rate",
        "adoption_rate",
        "average_total_tokens",
        "average_cost",
        "p50_duration_seconds",
    } <= metrics
    assert report["watch_items"][0]["current_revision"] == 3


def test_observability_hides_another_users_private_template(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    store = PromptStore(repository.db_path)
    project_id = _project(repository)
    private = store.create_template(
        title="私人模板",
        description="不可泄露",
        content=builtin_template("generic-vertical-short"),
        owner_scope="user",
        owner_user_id="owner-2",
    )
    jobs = [
        _job(
            project_id,
            "job-1",
            revision=1,
            status="succeeded",
            template_key=private["template_key"],
        )
    ]

    report = PromptObservabilityService(
        repository, FakeJobManager(jobs)
    ).report(project_id=project_id, actor_user_id="owner-1")

    assert report["versions"] == []
    assert report["summary"]["privacy_hidden_jobs"] == 1
    assert report["summary"]["attributed_jobs"] == 1
