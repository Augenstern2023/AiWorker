# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_job_manager.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""Tests for JobManager state transitions."""

import io
import json
import sqlite3
import subprocess
import threading

import pytest

from core.events import WorkerEvent
from core.job import CrawlJob
from agents.storage_agent import StorageAgent
from runtime.job_manager import (
    JobConflictError,
    JobManager,
    JobNotFoundError,
    WorkerTerminationError,
    creative_job_scope,
)
from runtime.job_state import JobState


class FakeProcess:
    pid = 123

    def __init__(self):
        self.stdout = io.StringIO(
            WorkerEvent(
                event="job_started",
                job_id="job-1",
                worker_id="dy-worker",
                platform="dy",
                level="info",
                message="started",
            ).to_json()
            + "\n"
        )
        self._returncode = 0

    def wait(self, timeout=None):
        return self._returncode

    def poll(self):
        return None

    def terminate(self):
        self._returncode = -15

    def kill(self):
        self._returncode = -9


def test_job_manager_create_job(monkeypatch, tmp_path):
    monkeypatch.setattr("runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess())
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob(job_id="job-1", platform="dy", crawler_type="search", keywords=["k"])

    state = manager.start_job(job)

    assert state.job_id == "job-1"
    assert (tmp_path / "jobs" / "job-1.json").exists()


def test_job_manager_allows_different_drama_downloads_to_run_together(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    first = CrawlJob(
        job_id="drama-a",
        platform="hg",
        crawler_type="drama_download",
        extra={"title": "剧目甲", "book_id": "book-a"},
    )
    manager.jobs[first.job_id] = JobState(job=first, status="running")
    manager.processes[first.job_id] = FakeProcess()

    second = manager.start_job(
        CrawlJob(
            job_id="drama-b",
            platform="hg",
            crawler_type="drama_download",
            extra={"title": "剧目乙", "book_id": "book-b"},
        )
    )

    assert second.job_id == "drama-b"
    with pytest.raises(JobConflictError, match="platform 'hg'"):
        manager.start_job(CrawlJob(job_id="rank-c", platform="hg", crawler_type="rank"))


def test_job_manager_allows_different_breakdowns_to_run_together(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    first = CrawlJob(
        job_id="breakdown-a",
        platform="creative",
        crawler_type="breakdown_generate",
        extra={"topic_id": 101, "title": "剧目甲"},
    )
    manager.jobs[first.job_id] = JobState(job=first, status="running")
    manager.processes[first.job_id] = FakeProcess()

    second = manager.start_job(
        CrawlJob(
            job_id="breakdown-b",
            platform="creative",
            crawler_type="breakdown_generate",
            extra={"topic_id": 102, "title": "剧目乙"},
        )
    )

    assert second.job_id == "breakdown-b"
    with pytest.raises(JobConflictError, match="platform 'creative'"):
        manager.start_job(
            CrawlJob(
                job_id="breakdown-a-duplicate",
                platform="creative",
                crawler_type="breakdown_generate",
                extra={"topic_id": 101, "title": "剧目甲"},
            )
        )


def test_job_manager_allows_independent_script_reviews_to_run_together(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    first = CrawlJob(
        job_id="review-a",
        platform="creative",
        crawler_type="screenplay_review",
        extra={"review_id": 101},
    )
    manager.jobs[first.job_id] = JobState(job=first, status="running")
    manager.processes[first.job_id] = FakeProcess()

    second = manager.start_job(
        CrawlJob(
            job_id="review-b",
            platform="creative",
            crawler_type="screenplay_review",
            extra={"review_id": 102},
        )
    )

    assert second.job_id == "review-b"
    with pytest.raises(JobConflictError, match="platform 'creative'"):
        manager.start_job(
            CrawlJob(
                job_id="review-a-duplicate",
                platform="creative",
                crawler_type="screenplay_review",
                extra={"review_id": 101},
            )
        )


def test_job_manager_allows_independent_creative_generation_jobs(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    first = CrawlJob(
        job_id="beats-episode-1",
        platform="creative",
        crawler_type="phase4_generate",
        extra={"project_id": 1, "action": "beats", "episode_number": 1},
    )
    manager.jobs[first.job_id] = JobState(job=first, status="running")
    manager.processes[first.job_id] = FakeProcess()

    second_episode = manager.start_job(
        CrawlJob(
            job_id="beats-episode-2",
            platform="creative",
            crawler_type="phase4_generate",
            extra={"project_id": 1, "action": "beats", "episode_number": 2},
        )
    )
    scene_script = manager.start_job(
        CrawlJob(
            job_id="script-episode-1",
            platform="creative",
            crawler_type="phase4_generate",
            extra={"project_id": 1, "action": "script", "episode_number": 1},
        )
    )
    another_project = manager.start_job(
        CrawlJob(
            job_id="outlines-project-2",
            platform="creative",
            crawler_type="phase4_generate",
            extra={"project_id": 2, "action": "outlines"},
        )
    )

    assert second_episode.job_id == "beats-episode-2"
    assert scene_script.job_id == "script-episode-1"
    assert another_project.job_id == "outlines-project-2"


@pytest.mark.parametrize(
    ("extra", "duplicate_extra"),
    [
        (
            {"project_id": 1, "action": "beats", "episode_number": 1},
            {"project_id": 1, "action": "beats", "episode_number": 1},
        ),
        (
            {"project_id": 1, "action": "script", "episode_number": 1},
            {"project_id": 1, "action": "script", "episode_number": 1},
        ),
        (
            {"project_id": 1, "action": "outlines"},
            {"project_id": 1, "action": "outlines"},
        ),
    ],
)
def test_job_manager_rejects_duplicate_creative_generation_job(
    monkeypatch, tmp_path, extra, duplicate_extra
):
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    first = CrawlJob(
        job_id="creative-first",
        platform="creative",
        crawler_type="phase4_generate",
        extra=extra,
    )
    manager.jobs[first.job_id] = JobState(job=first, status="running")
    manager.processes[first.job_id] = FakeProcess()

    with pytest.raises(JobConflictError, match="platform 'creative'"):
        manager.start_job(
            CrawlJob(
                job_id="creative-duplicate",
                platform="creative",
                crawler_type="phase4_generate",
                extra=duplicate_extra,
            )
        )


@pytest.mark.parametrize(
    ("existing_extra", "independent_extra"),
    [
        (
            {"project_id": 1, "action": "blueprints", "seed_id": 7},
            {"project_id": 1, "action": "blueprints", "seed_id": 8},
        ),
        (
            {
                "project_id": 1, "action": "blueprint_variant", "seed_id": 7,
                "parent_blueprint_id": 11,
            },
            {
                "project_id": 1, "action": "blueprint_variant", "seed_id": 7,
                "parent_blueprint_id": 12,
            },
        ),
    ],
)
def test_original_direction_jobs_share_project_brief_conflict_scope(
    monkeypatch, tmp_path, existing_extra, independent_extra,
):
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    first = CrawlJob(
        job_id="original-direction-first", platform="creative",
        crawler_type="phase4_generate", extra=existing_extra,
    )
    manager.jobs[first.job_id] = JobState(job=first, status="running")
    manager.processes[first.job_id] = FakeProcess()

    with pytest.raises(JobConflictError, match="platform 'creative'"):
        manager.start_job(CrawlJob(
            job_id="original-direction-same-stage", platform="creative",
            crawler_type="phase4_generate", extra=independent_extra,
        ))


def test_job_manager_normalizes_job_ownership_in_extra_and_public_state(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)

    state = manager.create_job(
        {"job_id": "job-owned", "platform": "dy", "crawler_type": "search"},
        created_by_user_id="user-1",
        workflow_task_id="task-1",
        project_id="project-1",
    )

    assert state.job.extra["created_by_user_id"] == "user-1"
    assert state.job.extra["workflow_task_id"] == "task-1"
    assert state.job.extra["project_id"] == "project-1"
    public = manager.get_job("job-owned")
    assert public["created_by_user_id"] == "user-1"
    assert public["workflow_task_id"] == "task-1"
    assert public["project_id"] == "project-1"


def test_job_manager_delete_terminal_job(tmp_path):
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob(job_id="job-delete", platform="dy", crawler_type="search")
    state = JobState(job=job)
    state.status = "failed"
    manager.jobs[state.job_id] = state
    manager._save_state(state)
    job.save(tmp_path / "jobs" / "job-delete.json")
    (tmp_path / "logs" / "job-delete.jsonl").write_text("log\n", encoding="utf-8")
    manager.storage.upsert_job(state)

    manager.delete_job("job-delete")

    assert manager.list_jobs() == []
    assert not (tmp_path / "jobs" / "job-delete.json").exists()
    assert not (tmp_path / "state" / "job-delete.json").exists()
    assert not (tmp_path / "logs" / "job-delete.jsonl").exists()
    with pytest.raises(JobNotFoundError):
        manager.get_job("job-delete")


def test_job_manager_delete_orphan_running_job_is_allowed(tmp_path):
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob(job_id="job-running", platform="dy", crawler_type="search")
    state = JobState(job=job)
    state.status = "running"
    manager.jobs[state.job_id] = state

    manager.delete_job("job-running")

    assert manager.list_jobs() == []


def test_job_manager_delete_job_loaded_from_disk(tmp_path):
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob(job_id="job-disk", platform="hg", crawler_type="rank")
    state = JobState(job=job)
    state.status = "failed"
    manager._save_state(state)
    job.save(tmp_path / "jobs" / "job-disk.json")
    (tmp_path / "logs" / "job-disk.jsonl").write_text("log\n", encoding="utf-8")
    manager.jobs.clear()

    manager.delete_job("job-disk")

    assert not (tmp_path / "jobs" / "job-disk.json").exists()
    assert not (tmp_path / "state" / "job-disk.json").exists()
    assert not (tmp_path / "logs" / "job-disk.jsonl").exists()


def test_delete_operation_job_preserves_replay_reservation(monkeypatch, tmp_path):
    """Deleting job history must not reopen an idempotent creative operation."""
    launches = []
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker",
        lambda *_args, **_kwargs: launches.append("launched"),
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    state = JobState(
        job=CrawlJob.from_dict(_creative_operation_payload(job_id="operation-job")),
        status="succeeded",
    )
    state.job.extra.update(
        {
            "operation_id": "operation-delete-replay",
            "created_by_user_id": "writer-1",
            "workflow_task_id": "task-1",
            "project_id": "9",
        }
    )
    manager.jobs[state.job_id] = state
    manager.storage.upsert_job(state)

    manager.delete_job(state.job_id)
    replayed = manager.get_or_create_job(
        "operation-delete-replay",
        _creative_operation_payload(job_id="must-not-launch"),
        created_by_user_id="writer-1",
        workflow_task_id="task-1",
        project_id=9,
    )

    assert replayed.job_id == "operation-job"
    assert replayed.status == "succeeded"
    assert launches == []


def test_force_delete_preserves_database_only_operation_tombstone(tmp_path):
    """force_missing must not erase an operation reservation after files vanished."""
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    state = JobState(
        job=CrawlJob.from_dict(_creative_operation_payload(job_id="db-only-job")),
        status="succeeded",
    )
    state.job.extra["operation_id"] = "db-only-operation"
    manager.storage.upsert_job(state)

    manager.delete_job(state.job_id, force_missing=True)
    manager.delete_job(state.job_id, force_missing=True)

    preserved = manager.storage.get_job_by_operation("db-only-operation")
    assert preserved is not None
    assert preserved.job_id == "db-only-job"


def test_terminal_disk_state_releases_stale_database_scope(tmp_path):
    """A crash between state JSON and DB writes must not reserve scope forever."""
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob.from_dict(_creative_operation_payload(job_id="terminal-on-disk"))
    job.extra["operation_id"] = "terminal-disk-operation"
    running = JobState(job=job, status="running")
    manager.storage.reserve_job_operation(
        "terminal-disk-operation",
        running,
        scope=creative_job_scope(job),
        launcher_owner="old-launcher",
        launcher_lease_expires_at=10**12,
    )
    manager.storage.upsert_job(running)
    assert manager.storage.finish_job_launch(
        "terminal-disk-operation",
        launcher_owner="old-launcher",
        phase="launched",
    )

    terminal = JobState(job=job, status="succeeded")
    terminal.finished_at = 123.0
    manager._save_state(terminal)
    job.save(tmp_path / "jobs" / f"{job.job_id}.json")

    reloaded = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    assert reloaded.list_jobs()[0]["status"] == "succeeded"
    with sqlite3.connect(tmp_path / "agent_state.db") as connection:
        status, scope_active = connection.execute(
            "SELECT status,scope_active FROM agent_jobs WHERE operation_id=?",
            ("terminal-disk-operation",),
        ).fetchone()
    assert status == "succeeded"
    assert scope_active == 0


def test_stale_terminal_disk_state_does_not_override_active_retry_lease(tmp_path):
    """A previous attempt's state file must not fence out a newly claimed retry."""
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob.from_dict(_creative_operation_payload(job_id="retry-with-stale-disk"))
    job.extra["operation_id"] = "retry-stale-disk-operation"
    failed = JobState(job=job, status="failed")
    manager.storage.reserve_job_operation(
        "retry-stale-disk-operation",
        failed,
        scope=creative_job_scope(job),
        launcher_owner="old-launcher",
        launcher_lease_expires_at=0,
    )
    manager.storage.upsert_job(failed)
    manager._save_state(failed)
    job.save(tmp_path / "jobs" / f"{job.job_id}.json")

    claimed, _ = manager.storage.claim_failed_job_operation(
        "retry-stale-disk-operation",
        launcher_owner="new-launcher",
        launcher_lease_expires_at=10**12,
    )
    assert claimed is True

    listed = JobManager(runtime_dir=tmp_path, cwd=tmp_path).list_jobs()
    assert listed[0]["status"] == "pending"

    with sqlite3.connect(tmp_path / "agent_state.db") as connection:
        status, scope_active, owner, phase = connection.execute(
            "SELECT status,scope_active,launcher_owner,launch_phase "
            "FROM agent_jobs WHERE operation_id=?",
            ("retry-stale-disk-operation",),
        ).fetchone()
    assert (status, scope_active, owner, phase) == (
        "pending",
        1,
        "new-launcher",
        "launching",
    )


def test_stale_terminal_disk_state_does_not_override_running_launch(
    monkeypatch, tmp_path
):
    """The running-CAS to state-file window must keep its active launch fence."""
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob.from_dict(_creative_operation_payload(job_id="running-stale-disk"))
    job.extra["operation_id"] = "running-stale-disk-operation"
    failed = JobState(job=job, status="failed")
    manager.storage.reserve_job_operation(
        "running-stale-disk-operation",
        failed,
        scope=creative_job_scope(job),
        launcher_owner="old-launcher",
        launcher_lease_expires_at=0,
    )
    manager.storage.upsert_job(failed)
    manager._save_state(failed)
    job.save(tmp_path / "jobs" / f"{job.job_id}.json")
    claimed, _ = manager.storage.claim_failed_job_operation(
        "running-stale-disk-operation",
        launcher_owner="new-launcher",
        launcher_lease_expires_at=10**12,
    )
    assert claimed is True
    running = JobState(job=job, status="running", pid=2468)
    monkeypatch.setattr(
        JobManager, "_process_id_is_alive", lambda _self, pid: pid == 2468
    )
    assert manager.storage.upsert_owned_job_launch(
        running,
        "running-stale-disk-operation",
        launcher_owner="new-launcher",
    )

    listed = JobManager(runtime_dir=tmp_path, cwd=tmp_path).list_jobs()

    assert listed[0]["status"] == "running"
    with sqlite3.connect(tmp_path / "agent_state.db") as connection:
        status, scope_active = connection.execute(
            "SELECT status,scope_active FROM agent_jobs WHERE operation_id=?",
            ("running-stale-disk-operation",),
        ).fetchone()
    assert (status, scope_active) == ("running", 1)


def test_stale_terminal_disk_state_does_not_override_launched_retry(
    monkeypatch, tmp_path
):
    """A prior attempt's terminal file must not supersede a newer launched worker."""
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob.from_dict(_creative_operation_payload(job_id="launched-stale-disk"))
    job.extra["operation_id"] = "launched-stale-disk-operation"
    failed = JobState(job=job, status="failed")
    failed.finished_at = 10.0
    manager.storage.reserve_job_operation(
        "launched-stale-disk-operation",
        failed,
        scope=creative_job_scope(job),
        launcher_owner="old-launcher",
        launcher_lease_expires_at=0,
    )
    manager.storage.upsert_job(failed)
    manager._save_state(failed)
    job.save(tmp_path / "jobs" / f"{job.job_id}.json")
    claimed, _ = manager.storage.claim_failed_job_operation(
        "launched-stale-disk-operation",
        launcher_owner="new-launcher",
        launcher_lease_expires_at=10**12,
    )
    assert claimed is True
    running = JobState(job=job, status="running", pid=2468)
    running.started_at = 20.0
    assert manager.storage.upsert_owned_job_launch(
        running,
        "launched-stale-disk-operation",
        launcher_owner="new-launcher",
    )
    assert manager.storage.finish_job_launch(
        "launched-stale-disk-operation",
        launcher_owner="new-launcher",
        phase="launched",
    )
    monkeypatch.setattr(
        JobManager, "_process_id_is_alive", lambda _self, pid: pid == 2468
    )

    listed = JobManager(runtime_dir=tmp_path, cwd=tmp_path).list_jobs()

    assert listed[0]["status"] == "running"
    with sqlite3.connect(tmp_path / "agent_state.db") as connection:
        status, scope_active = connection.execute(
            "SELECT status,scope_active FROM agent_jobs WHERE operation_id=?",
            ("launched-stale-disk-operation",),
        ).fetchone()
    assert (status, scope_active) == ("running", 1)


def test_job_manager_get_logs_loads_state_from_disk(tmp_path):
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob(job_id="job-history", platform="dy", crawler_type="rank")
    state = JobState(job=job)
    state.status = "succeeded"
    state.add_event(
        WorkerEvent(
            event="job_finished",
            job_id=job.job_id,
            worker_id="dy-worker",
            platform="dy",
            level="info",
            message="done",
        )
    )
    manager._save_state(state)
    job.save(tmp_path / "jobs" / "job-history.json")

    reloaded = JobManager(runtime_dir=tmp_path, cwd=tmp_path)

    assert any(job_info["job_id"] == "job-history" for job_info in reloaded.list_jobs())
    assert reloaded.get_logs("job-history")[-1].endswith("done")


def test_orphan_stopping_job_is_reconciled_and_does_not_block_new_job(monkeypatch, tmp_path):
    job = CrawlJob(job_id="job-orphan", platform="hg", crawler_type="rank")
    state = JobState(job=job)
    state.status = "stopping"
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    manager._save_state(state)
    job.save(tmp_path / "jobs" / "job-orphan.json")

    reloaded = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    jobs = reloaded.list_jobs()

    assert jobs[0]["status"] == "cancelled"

    monkeypatch.setattr("runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess())
    new_job = CrawlJob(job_id="job-new", platform="hg", crawler_type="rank")

    assert reloaded.start_job(new_job).job_id == "job-new"


def test_job_manager_keeps_a_live_worker_owned_by_another_server(monkeypatch, tmp_path):
    job = CrawlJob(job_id="job-shared", platform="creative", crawler_type="breakdown_generate")
    state = JobState(job=job, status="running", pid=2468)
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    manager._save_state(state)
    job.save(tmp_path / "jobs" / "job-shared.json")
    monkeypatch.setattr(
        JobManager, "_process_id_is_alive", lambda _self, pid: pid == 2468, raising=False
    )

    reloaded = JobManager(runtime_dir=tmp_path, cwd=tmp_path)

    assert reloaded.get_job("job-shared")["status"] == "running"


def test_job_manager_retries_terminal_job_with_original_payload(monkeypatch, tmp_path):
    monkeypatch.setattr("runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess())
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob(
        job_id="creative-old",
        platform="creative",
        crawler_type="phase4_generate",
        extra={"action": "blueprints", "project_id": 7},
    )
    state = JobState(job=job, status="failed")
    manager.jobs[job.job_id] = state
    manager._save_state(state)
    job.save(tmp_path / "jobs" / f"{job.job_id}.json")

    retried = manager.retry(job.job_id)

    assert retried.job_id != job.job_id
    assert retried.job.extra["action"] == "blueprints"
    assert retried.job.extra["project_id"] == 7
    assert retried.job.extra["retry_of_job_id"] == job.job_id


def test_job_manager_rejects_retry_for_running_job(tmp_path):
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob(job_id="creative-running", platform="creative", crawler_type="phase4_generate")
    manager.jobs[job.job_id] = JobState(job=job, status="running")
    manager.processes[job.job_id] = FakeProcess()

    with pytest.raises(JobConflictError, match="terminal"):
        manager.retry(job.job_id)


def test_job_manager_rejects_generic_retry_for_operation_bound_job(tmp_path):
    """Generic retry would duplicate or violate the operation reservation."""
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob.from_dict(_creative_operation_payload(job_id="operation-failed"))
    job.extra["operation_id"] = "operation-generic-retry"
    manager.jobs[job.job_id] = JobState(job=job, status="failed")

    with pytest.raises(JobConflictError, match="operation-bound"):
        manager.retry(job.job_id)


def test_agent_job_store_migrates_operation_id_column(tmp_path):
    """Missing the migration would make existing runtime databases unusable."""
    db_path = tmp_path / "agent.db"
    legacy_job = CrawlJob.from_dict(_creative_operation_payload(job_id="legacy-job"))
    legacy_job.extra["operation_id"] = "legacy-operation"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE agent_jobs ("
            "job_id TEXT PRIMARY KEY, platform TEXT NOT NULL, "
            "crawler_type TEXT NOT NULL, status TEXT NOT NULL, "
            "created_at REAL NOT NULL, started_at REAL, finished_at REAL, "
            "message TEXT, job_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO agent_jobs VALUES (?,?,?,?,?,?,?,?,?)",
            (
                legacy_job.job_id,
                legacy_job.platform,
                legacy_job.crawler_type,
                "succeeded",
                1.0,
                1.0,
                2.0,
                    "done",
                    json.dumps(legacy_job.to_dict()),
            ),
        )

    storage = StorageAgent(db_path)

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(agent_jobs)")
        }
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(agent_jobs)")
        }
    assert "operation_id" in columns
    assert "idx_agent_jobs_operation_id" in indexes
    recovered = storage.get_job_by_operation("legacy-operation")
    assert recovered is not None
    assert recovered.job_id == "legacy-job"


def test_agent_job_store_rejects_duplicate_legacy_operation_ids(tmp_path):
    """Choosing one of two historical reservations would permit duplicate work."""
    db_path = tmp_path / "agent.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE agent_jobs ("
            "job_id TEXT PRIMARY KEY, platform TEXT NOT NULL, "
            "crawler_type TEXT NOT NULL, status TEXT NOT NULL, "
            "created_at REAL NOT NULL, started_at REAL, finished_at REAL, "
            "message TEXT, job_json TEXT NOT NULL)"
        )
        for job_id in ("legacy-job-1", "legacy-job-2"):
            legacy_job = CrawlJob.from_dict(_creative_operation_payload(job_id=job_id))
            legacy_job.extra["operation_id"] = "duplicate-operation"
            connection.execute(
                "INSERT INTO agent_jobs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    legacy_job.job_id,
                    legacy_job.platform,
                    legacy_job.crawler_type,
                    "failed",
                    1.0,
                    1.0,
                    2.0,
                    "failed",
                    json.dumps(legacy_job.to_dict()),
                ),
            )

    with pytest.raises(ValueError, match="duplicate operation_id"):
        StorageAgent(db_path)


def test_get_or_create_job_reuses_operation_id_after_state_reload(
    monkeypatch, tmp_path
):
    """Losing the reservation on restart would launch the same generation twice."""
    launches = []

    def launch(*_args, **_kwargs):
        launches.append("launched")
        return FakeProcess()

    monkeypatch.setattr("runtime.job_manager.launch_worker", launch)
    payload = {
        "job_id": "operation-job",
        "platform": "creative",
        "crawler_type": "phase4_generate",
        "extra": {
            "action": "script",
            "project_id": 9,
            "episode_number": 2,
        },
    }
    first_manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    first = first_manager.get_or_create_job(
        "writer-op-1",
        payload,
        created_by_user_id="writer-1",
        workflow_task_id="task-1",
        project_id=9,
    )
    reloaded = JobManager(runtime_dir=tmp_path, cwd=tmp_path)

    replay = reloaded.get_or_create_job(
        "writer-op-1",
        {**payload, "job_id": "must-not-be-used"},
        created_by_user_id="writer-1",
        workflow_task_id="task-1",
        project_id=9,
    )

    assert replay.job_id == first.job_id == "operation-job"
    assert launches == ["launched"]


def test_creative_series_conflicts_with_overlapping_episode():
    """Treating inclusive ranges as disjoint at a shared edge causes duplicate work."""
    existing = CrawlJob(
        job_id="series-1",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "script",
            "series": True,
            "project_id": 7,
            "episode_start": 1,
            "episode_end": 10,
        },
    )
    incoming = CrawlJob(
        job_id="series-2",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "script",
            "series": True,
            "project_id": 7,
            "episode_start": 10,
            "episode_end": 20,
        },
    )

    assert JobManager._can_run_concurrently(existing, incoming) is False


def test_creative_non_overlapping_ranges_can_run():
    """Over-serializing disjoint episode ranges defeats partial batch execution."""
    first = CrawlJob(
        job_id="beats-1",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "beats",
            "series": True,
            "project_id": 7,
            "episode_start": 1,
            "episode_end": 10,
        },
    )
    second = CrawlJob(
        job_id="beats-2",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "beats",
            "series": True,
            "project_id": 7,
            "episode_start": 11,
            "episode_end": 20,
        },
    )

    assert JobManager._can_run_concurrently(first, second) is True


def test_worker_action_names_share_normalized_scope():
    """Worker aliases that describe one stage must not bypass conflict checks."""
    blueprints = CrawlJob(
        job_id="brief-1",
        platform="creative",
        crawler_type="phase4_generate",
        extra={"action": "blueprints", "project_id": 5},
    )
    variant = CrawlJob(
        job_id="brief-2",
        platform="creative",
        crawler_type="phase4_generate",
        extra={"action": "blueprint_variant", "project_id": 5},
    )
    outlines = CrawlJob(
        job_id="outline-1",
        platform="creative",
        crawler_type="phase4_generate",
        extra={"action": "outlines", "project_id": 5},
    )

    assert creative_job_scope(blueprints) == ("5", "brief", None)
    assert creative_job_scope(variant) == ("5", "brief", None)
    assert creative_job_scope(outlines) == ("5", "outline", None)
    assert JobManager._can_run_concurrently(blueprints, variant) is False


def _creative_operation_payload(job_id="operation-job", *, instruction="生成剧本"):
    return {
        "job_id": job_id,
        "platform": "creative",
        "crawler_type": "phase4_generate",
        "extra": {
            "action": "script",
            "project_id": 9,
            "episode_number": 2,
            "generation_instruction": instruction,
        },
    }


def test_get_or_create_job_rejects_operation_id_reused_for_different_payload(
    monkeypatch, tmp_path
):
    """Returning the first reservation for changed input would hide an idempotency bug."""
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    manager.get_or_create_job(
        "writer-op-conflict",
        _creative_operation_payload(),
        created_by_user_id="writer-1",
        workflow_task_id="task-1",
        project_id=9,
    )

    with pytest.raises(JobConflictError, match="different payload"):
        manager.get_or_create_job(
            "writer-op-conflict",
            _creative_operation_payload(
                job_id="ignored-new-id", instruction="改成喜剧"
            ),
            created_by_user_id="writer-1",
            workflow_task_id="task-1",
            project_id=9,
        )


def test_get_or_create_job_keeps_failed_reservation_after_launch_error(
    monkeypatch, tmp_path
):
    """Deleting a failed reservation would allow a replay to launch unknown work twice."""
    launches = []

    def fail_launch(*_args, **_kwargs):
        launches.append("attempted")
        raise RuntimeError("launcher unavailable")

    monkeypatch.setattr("runtime.job_manager.launch_worker", fail_launch)
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    with pytest.raises(RuntimeError, match="launcher unavailable"):
        manager.get_or_create_job(
            "writer-op-launch-failed",
            _creative_operation_payload(job_id="launch-failed-job"),
            created_by_user_id="writer-1",
            workflow_task_id="task-1",
            project_id=9,
        )

    replay = JobManager(runtime_dir=tmp_path, cwd=tmp_path).get_or_create_job(
        "writer-op-launch-failed",
        _creative_operation_payload(job_id="must-not-launch"),
        created_by_user_id="writer-1",
        workflow_task_id="task-1",
        project_id=9,
    )

    assert replay.job_id == "launch-failed-job"
    assert replay.status == "failed"
    assert launches == ["attempted"]


def test_launch_bookkeeping_failure_does_not_fail_a_running_worker(
    monkeypatch, tmp_path
):
    """A post-launch metadata error must not release scope for duplicate work."""
    release_worker = threading.Event()
    launches = []

    class RunningProcess(FakeProcess):
        def wait(self, timeout=None):
            del timeout
            assert release_worker.wait(5)
            return 0

    def launch(*_args, **_kwargs):
        launches.append("launched")
        return RunningProcess()

    monkeypatch.setattr("runtime.job_manager.launch_worker", launch)
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    original_finish = manager.storage.finish_job_launch

    def fail_launched_bookkeeping(operation_id, *, launcher_owner, phase):
        if phase == "launched":
            raise RuntimeError("launch bookkeeping unavailable")
        return original_finish(
            operation_id, launcher_owner=launcher_owner, phase=phase
        )

    monkeypatch.setattr(manager.storage, "finish_job_launch", fail_launched_bookkeeping)
    try:
        with pytest.raises(RuntimeError, match="launch bookkeeping unavailable"):
            manager.get_or_create_job(
                "writer-op-bookkeeping",
                _creative_operation_payload(job_id="bookkeeping-job"),
                created_by_user_id="writer-1",
                workflow_task_id="task-1",
                project_id=9,
            )

        state = manager.storage.get_job_by_operation("writer-op-bookkeeping")
        assert state is not None
        assert state.status == "running"
        with sqlite3.connect(tmp_path / "agent_state.db") as connection:
            scope_active = connection.execute(
                "SELECT scope_active FROM agent_jobs WHERE operation_id=?",
                ("writer-op-bookkeeping",),
            ).fetchone()[0]
        assert scope_active == 1

        replay = JobManager(runtime_dir=tmp_path, cwd=tmp_path).get_or_create_job(
            "writer-op-bookkeeping",
            _creative_operation_payload(job_id="must-not-launch"),
            created_by_user_id="writer-1",
            workflow_task_id="task-1",
            project_id=9,
        )
        assert replay.job_id == "bookkeeping-job"
        assert launches == ["launched"]
    finally:
        release_worker.set()


def test_lost_launcher_lease_stops_worker_without_overwriting_new_owner(
    monkeypatch, tmp_path
):
    """A fenced launcher must not publish or retain the worker it started."""
    lease_lost = threading.Event()
    worker_stopped = threading.Event()

    class LeaseLostProcess(FakeProcess):
        def wait(self, timeout=None):
            assert worker_stopped.wait(timeout or 5)
            return -15

        def poll(self):
            return -15 if worker_stopped.is_set() else None

        def terminate(self):
            worker_stopped.set()

    process = LeaseLostProcess()

    def launch(*_args, **_kwargs):
        assert lease_lost.wait(5)
        return process

    monkeypatch.setattr("runtime.job_manager._LAUNCH_LEASE_SECONDS", 0.03)
    monkeypatch.setattr("runtime.job_manager.launch_worker", launch)
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)

    def lose_lease(
        operation_id, *, launcher_owner, lease_expires_at
    ):
        del launcher_owner, lease_expires_at
        with sqlite3.connect(tmp_path / "agent_state.db") as connection:
            connection.execute(
                "UPDATE agent_jobs SET launcher_owner=?, "
                "launcher_lease_expires_at=? WHERE operation_id=?",
                ("replacement-launcher", 10**12, operation_id),
            )
        lease_lost.set()
        return False

    monkeypatch.setattr(manager.storage, "renew_job_launch_lease", lose_lease)
    try:
        with pytest.raises(JobConflictError, match="launcher lease"):
            manager.get_or_create_job(
                "writer-op-lost-launcher",
                _creative_operation_payload(job_id="lost-launcher-job"),
                created_by_user_id="writer-1",
                workflow_task_id="task-1",
                project_id=9,
            )
        assert worker_stopped.is_set()
        with sqlite3.connect(tmp_path / "agent_state.db") as connection:
            status, owner, scope_active = connection.execute(
                "SELECT status,launcher_owner,scope_active FROM agent_jobs "
                "WHERE operation_id=?",
                ("writer-op-lost-launcher",),
            ).fetchone()
        assert (status, owner, scope_active) == (
            "pending",
            "replacement-launcher",
            1,
        )
    finally:
        worker_stopped.set()


def test_running_state_file_failure_stops_unmonitored_worker(
    monkeypatch, tmp_path
):
    """A post-spawn disk failure must not release scope around a live worker."""
    worker_stopped = threading.Event()

    class UnmonitoredProcess(FakeProcess):
        def wait(self, timeout=None):
            assert worker_stopped.wait(timeout or 5)
            return -15

        def poll(self):
            return -15 if worker_stopped.is_set() else None

        def terminate(self):
            worker_stopped.set()

    monkeypatch.setattr(
        "runtime.job_manager.launch_worker",
        lambda *_args, **_kwargs: UnmonitoredProcess(),
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    original_save = manager._save_state

    def fail_running_state(state):
        if state.status == "running":
            raise OSError("state disk unavailable")
        return original_save(state)

    monkeypatch.setattr(manager, "_save_state", fail_running_state)

    with pytest.raises(OSError, match="state disk unavailable"):
        manager.get_or_create_job(
            "writer-op-state-save-failed",
            _creative_operation_payload(job_id="state-save-failed-job"),
            created_by_user_id="writer-1",
            workflow_task_id="task-1",
            project_id=9,
        )

    assert worker_stopped.is_set()
    assert "state-save-failed-job" not in manager.processes
    with sqlite3.connect(tmp_path / "agent_state.db") as connection:
        status, scope_active = connection.execute(
            "SELECT status,scope_active FROM agent_jobs WHERE operation_id=?",
            ("writer-op-state-save-failed",),
        ).fetchone()
    assert (status, scope_active) == ("failed", 0)


def test_unmonitored_worker_is_waited_after_kill(tmp_path):
    """Scope cannot be released until a force-killed worker is confirmed exited."""

    class KillThenExitProcess(FakeProcess):
        def __init__(self):
            super().__init__()
            self.wait_calls = 0
            self.killed = False
            self.exited = False

        def terminate(self):
            return None

        def wait(self, timeout=None):
            self.wait_calls += 1
            if not self.killed:
                raise subprocess.TimeoutExpired("worker", timeout)
            self.exited = True
            return -9

        def poll(self):
            return -9 if self.exited else None

        def kill(self):
            self.killed = True

    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    process = KillThenExitProcess()
    manager.processes["kill-wait-job"] = process

    manager._discard_unmonitored_process("kill-wait-job")

    assert process.killed is True
    assert process.wait_calls == 2
    assert process.exited is True
    assert "kill-wait-job" not in manager.processes


def test_worker_that_survives_kill_remains_monitored_and_reserved(
    monkeypatch, tmp_path
):
    """Failed termination must keep monitoring instead of releasing live scope."""
    monitor_wait_entered = threading.Event()
    release_worker = threading.Event()
    terminal_persisted = threading.Event()

    class KillResistantProcess(FakeProcess):
        def __init__(self):
            super().__init__()
            self.wait_calls = 0

        def terminate(self):
            return None

        def kill(self):
            return None

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls <= 2:
                raise subprocess.TimeoutExpired("worker", timeout)
            monitor_wait_entered.set()
            assert release_worker.wait(5)
            return -9

        def poll(self):
            return -9 if release_worker.is_set() else None

    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob(job_id="kill-resistant-job", platform="dy", crawler_type="rank")
    state = JobState(job=job, status="running")
    process = KillResistantProcess()
    manager.jobs[job.job_id] = state
    manager.processes[job.job_id] = process
    manager.storage.upsert_job(state)
    original_upsert = manager.storage.upsert_job

    def observe_terminal(current):
        original_upsert(current)
        if current.status in {"succeeded", "failed", "cancelled"}:
            terminal_persisted.set()

    monkeypatch.setattr(manager.storage, "upsert_job", observe_terminal)

    with pytest.raises(WorkerTerminationError, match="did not exit"):
        manager._discard_unmonitored_process(job.job_id)

    assert monitor_wait_entered.wait(5)
    assert job.job_id in manager.processes
    with sqlite3.connect(tmp_path / "agent_state.db") as connection:
        status = connection.execute(
            "SELECT status FROM agent_jobs WHERE job_id=?", (job.job_id,)
        ).fetchone()[0]
    assert status == "running"
    release_worker.set()
    assert terminal_persisted.wait(5)
    assert job.job_id not in manager.processes


def test_monitor_thread_start_failure_falls_back_to_synchronous_monitoring(
    monkeypatch, tmp_path
):
    """Thread startup failure must not leave a launched worker unmonitored."""
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob(job_id="monitor-fallback-job", platform="dy", crawler_type="rank")
    state = JobState(job=job, status="running")
    process = FakeProcess()
    manager.jobs[job.job_id] = state
    manager.processes[job.job_id] = process
    manager.storage.upsert_job(state)

    def fail_thread_start(_thread):
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(threading.Thread, "start", fail_thread_start)

    manager._start_monitor(state)

    assert state.status == "succeeded"
    assert job.job_id not in manager.processes
    with sqlite3.connect(tmp_path / "agent_state.db") as connection:
        status = connection.execute(
            "SELECT status FROM agent_jobs WHERE job_id=?", (job.job_id,)
        ).fetchone()[0]
    assert status == "succeeded"


def test_event_parse_failure_waits_for_worker_before_releasing_scope(
    monkeypatch, tmp_path
):
    """Monitor diagnostics must not treat a still-running worker as exited."""
    wait_entered = threading.Event()
    release_worker = threading.Event()

    class ParseFailureProcess(FakeProcess):
        def __init__(self):
            super().__init__()
            self.stdout = io.StringIO("malformed event\n")

        def wait(self, timeout=None):
            del timeout
            wait_entered.set()
            assert release_worker.wait(5)
            return 0

        def poll(self):
            return None if not release_worker.is_set() else 0

    def fail_parse(*_args, **_kwargs):
        raise ValueError("malformed event")

    monkeypatch.setattr("runtime.job_manager.parse_event_line", fail_parse)
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob.from_dict(_creative_operation_payload(job_id="parse-failure-job"))
    job.extra["operation_id"] = "parse-failure-operation"
    state = JobState(job=job, status="running")
    manager.storage.reserve_job_operation(
        "parse-failure-operation",
        state,
        scope=creative_job_scope(job),
        launcher_owner="parse-launcher",
        launcher_lease_expires_at=10**12,
    )
    manager.storage.upsert_job(state)
    process = ParseFailureProcess()
    manager.jobs[job.job_id] = state
    manager.processes[job.job_id] = process
    thread = threading.Thread(target=manager._monitor_process, args=(state, process))
    thread.start()
    try:
        assert wait_entered.wait(5)
        with sqlite3.connect(tmp_path / "agent_state.db") as connection:
            status, scope_active = connection.execute(
                "SELECT status,scope_active FROM agent_jobs WHERE operation_id=?",
                ("parse-failure-operation",),
            ).fetchone()
        assert (status, scope_active) == ("running", 1)
    finally:
        release_worker.set()
        thread.join(5)
    assert not thread.is_alive()
    with sqlite3.connect(tmp_path / "agent_state.db") as connection:
        status, scope_active = connection.execute(
            "SELECT status,scope_active FROM agent_jobs WHERE operation_id=?",
            ("parse-failure-operation",),
        ).fetchone()
    assert (status, scope_active) == ("failed", 0)


def test_concurrent_managers_launch_one_reserved_operation(monkeypatch, tmp_path):
    """A process-local lock cannot prevent two server processes launching one operation."""
    launches = []
    launch_entered = threading.Event()
    release_launch = threading.Event()

    def launch(*_args, **_kwargs):
        launches.append("launched")
        launch_entered.set()
        assert release_launch.wait(5)
        return FakeProcess()

    monkeypatch.setattr("runtime.job_manager.launch_worker", launch)
    first = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    second = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    results = []

    def reserve(manager, job_id):
        results.append(
            manager.get_or_create_job(
                "writer-op-concurrent",
                _creative_operation_payload(job_id=job_id),
                created_by_user_id="writer-1",
                workflow_task_id="task-1",
                project_id=9,
            )
        )

    first_thread = threading.Thread(target=reserve, args=(first, "concurrent-job"))
    first_thread.start()
    assert launch_entered.wait(5)
    second_thread = threading.Thread(target=reserve, args=(second, "unused-job"))
    second_thread.start()
    second_thread.join(5)
    observed_during_launch = second.get_job("concurrent-job")
    launch_lease = second.storage.job_launch_lease("writer-op-concurrent")
    release_launch.set()
    first_thread.join(5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert observed_during_launch["status"] == "pending"
    assert launch_lease is not None
    assert launch_lease["phase"] == "launching"
    assert float(launch_lease["expires_at"] or 0) > 0
    assert [item.job_id for item in results] == ["concurrent-job", "concurrent-job"]
    assert launches == ["launched"]


def test_pending_operation_uses_launcher_lease_instead_of_fixed_grace(tmp_path):
    """A live slow launcher must not be failed merely because 30 seconds elapsed."""
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    state = JobState(
        job=CrawlJob.from_dict(_creative_operation_payload(job_id="leased-launch"))
    )
    state.job.extra["operation_id"] = "leased-launch-operation"
    state.created_at = 0
    manager.storage.reserve_job_operation(
        "leased-launch-operation",
        state,
        scope=creative_job_scope(state.job),
        launcher_owner="launcher-1",
        launcher_lease_expires_at=10**12,
    )
    manager.jobs[state.job_id] = state

    manager._reconcile_orphan_job(state)

    assert state.status == "pending"
    with sqlite3.connect(tmp_path / "agent_state.db") as connection:
        connection.execute(
            "UPDATE agent_jobs SET launcher_lease_expires_at=0 "
            "WHERE operation_id='leased-launch-operation'"
        )
    manager._reconcile_orphan_job(state)
    assert state.status == "failed"


def test_expired_launcher_lease_releases_persisted_creative_scope(tmp_path):
    """A crash before writing state files must not block the scope forever."""
    storage = StorageAgent(tmp_path / "agent_state.db")
    first = JobState(
        job=CrawlJob.from_dict(_creative_operation_payload(job_id="orphan-launch"))
    )
    first.job.extra["operation_id"] = "orphan-launch-operation"
    storage.reserve_job_operation(
        "orphan-launch-operation",
        first,
        scope=creative_job_scope(first.job),
        launcher_owner="dead-launcher",
        launcher_lease_expires_at=0,
    )
    second = JobState(
        job=CrawlJob.from_dict(_creative_operation_payload(job_id="replacement-launch"))
    )
    second.job.extra["operation_id"] = "replacement-operation"

    reserved, _ = storage.reserve_job_operation(
        "replacement-operation",
        second,
        scope=creative_job_scope(second.job),
        launcher_owner="live-launcher",
        launcher_lease_expires_at=10**12,
    )

    assert reserved is True
    orphan = storage.get_job_by_operation("orphan-launch-operation")
    assert orphan is not None
    assert orphan.status == "failed"


def test_concurrent_managers_reserve_overlapping_creative_scopes_in_database(
    monkeypatch, tmp_path
):
    """Different operations can race before either manager writes a state file."""
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    first = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    second = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    reserved = threading.Event()
    release = threading.Event()
    original_start = first.start_job

    def hold_before_state_file(job, **kwargs):
        reserved.set()
        assert release.wait(5)
        return original_start(job, **kwargs)

    first.start_job = hold_before_state_file
    errors = []

    def start_first():
        try:
            first.get_or_create_job(
                "scope-operation-1",
                _creative_operation_payload(job_id="scope-job-1"),
                created_by_user_id="writer-1",
                workflow_task_id="task-1",
                project_id=9,
            )
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    thread = threading.Thread(target=start_first)
    thread.start()
    assert reserved.wait(5)
    try:
        with pytest.raises(JobConflictError, match="creative scope"):
            second.get_or_create_job(
                "scope-operation-2",
                _creative_operation_payload(job_id="scope-job-2"),
                created_by_user_id="writer-2",
                workflow_task_id="task-2",
                project_id=9,
            )
    finally:
        release.set()
        thread.join(5)
    assert errors == []


def test_terminal_operation_is_recovered_without_disk_state(monkeypatch, tmp_path):
    """Requiring state JSON for a terminal reservation would reopen completed work."""
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: FakeProcess()
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    state = JobState(
        job=CrawlJob.from_dict(_creative_operation_payload(job_id="terminal-job")),
        status="succeeded",
    )
    state.job.extra.update(
        {
            "operation_id": "writer-op-terminal",
            "created_by_user_id": "writer-1",
            "workflow_task_id": "task-1",
            "project_id": "9",
        }
    )
    manager.storage.upsert_job(state)

    recovered = manager.get_or_create_job(
        "writer-op-terminal",
        _creative_operation_payload(job_id="must-not-launch"),
        created_by_user_id="writer-1",
        workflow_task_id="task-1",
        project_id=9,
    )

    assert recovered.job_id == "terminal-job"
    assert recovered.status == "succeeded"


def test_failed_operation_can_be_atomically_relaunched_for_retry(
    monkeypatch, tmp_path
):
    """Returning the old failed reservation makes Writer retries fail forever."""
    launches = []

    def launch(*_args, **_kwargs):
        launches.append("launched")
        return FakeProcess()

    monkeypatch.setattr("runtime.job_manager.launch_worker", launch)
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    failed = JobState(
        job=CrawlJob.from_dict(_creative_operation_payload(job_id="failed-job")),
        status="failed",
    )
    failed.job.extra.update(
        {
            "operation_id": "writer-op-retry",
            "created_by_user_id": "writer-1",
            "workflow_task_id": "task-1",
            "project_id": "9",
        }
    )
    manager.storage.upsert_job(failed)
    retry_payload = _creative_operation_payload(job_id="unused-retry-job")
    retry_payload["extra"]["retry_failed_operation"] = True

    retried = manager.get_or_create_job(
        "writer-op-retry",
        retry_payload,
        created_by_user_id="writer-1",
        workflow_task_id="task-1",
        project_id=9,
    )

    assert retried.job_id == "failed-job"
    assert launches == ["launched"]


def test_retry_ignores_its_own_recovered_pending_reservation(
    monkeypatch, tmp_path
):
    """Loading the previous state file must not make a retry conflict with itself."""
    launches = []
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker",
        lambda *_args, **_kwargs: launches.append("launched") or FakeProcess(),
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    job = CrawlJob.from_dict(_creative_operation_payload(job_id="self-retry-job"))
    job.extra.update(
        {
            "operation_id": "writer-op-self-retry",
            "created_by_user_id": "writer-1",
            "workflow_task_id": "task-1",
            "project_id": "9",
        }
    )
    failed = JobState(job=job, status="failed")
    manager.storage.reserve_job_operation(
        "writer-op-self-retry",
        failed,
        scope=creative_job_scope(job),
        launcher_owner="old-launcher",
        launcher_lease_expires_at=0,
    )
    manager.storage.upsert_job(failed)
    manager._save_state(failed)
    job.save(tmp_path / "jobs" / f"{job.job_id}.json")
    retry_payload = _creative_operation_payload(job_id="ignored-retry-job")
    retry_payload["extra"]["retry_failed_operation"] = True

    retried = JobManager(runtime_dir=tmp_path, cwd=tmp_path).get_or_create_job(
        "writer-op-self-retry",
        retry_payload,
        created_by_user_id="writer-1",
        workflow_task_id="task-1",
        project_id=9,
    )

    assert retried.job_id == "self-retry-job"
    assert launches == ["launched"]


def test_storage_upsert_persists_operation_id_on_every_path(tmp_path):
    """Only preserving operation_id on reservation inserts loses normal job identities."""
    storage = StorageAgent(tmp_path / "agent.db")
    state = JobState(
        job=CrawlJob.from_dict(_creative_operation_payload(job_id="upsert-job"))
    )
    state.job.extra["operation_id"] = "writer-op-upsert"

    storage.upsert_job(state)
    state.status = "succeeded"
    storage.upsert_job(state)

    recovered = storage.get_job_by_operation("writer-op-upsert")
    assert recovered is not None
    assert recovered.job_id == "upsert-job"
    assert recovered.status == "succeeded"


def test_creative_series_without_explicit_range_is_rejected_before_launch(
    monkeypatch, tmp_path
):
    """Accepting an unscoped series job bypasses overlap conflict protection."""
    launches = []
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker",
        lambda *_args, **_kwargs: launches.append("launched"),
    )
    manager = JobManager(runtime_dir=tmp_path, cwd=tmp_path)
    payload = _creative_operation_payload(job_id="range-less-series")
    payload["extra"].update({"series": True})

    with pytest.raises(ValueError, match="requires an episode range"):
        manager.get_or_create_job(
            "writer-op-no-range",
            payload,
            created_by_user_id="writer-1",
            workflow_task_id="task-1",
            project_id=9,
        )

    assert launches == []
