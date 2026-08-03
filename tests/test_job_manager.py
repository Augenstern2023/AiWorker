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

import pytest

from core.events import WorkerEvent
from core.job import CrawlJob
from runtime.job_manager import JobConflictError, JobManager, JobNotFoundError
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
