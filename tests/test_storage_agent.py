# -*- coding: utf-8 -*-
"""Tests for the SQLite storage agent."""

from agents.storage_agent import StorageAgent
from core.events import WorkerEvent
from core.job import CrawlJob
from runtime.job_state import JobState


def test_storage_agent_records_job_event_and_artifact(tmp_path):
    storage = StorageAgent(tmp_path / "agent.db")
    state = JobState(job=CrawlJob(job_id="job-1", platform="dy", crawler_type="search"))
    event = WorkerEvent(
        event="job_started",
        job_id="job-1",
        worker_id="dy-worker",
        platform="dy",
        level="info",
        message="started",
    )

    storage.upsert_job(state)
    storage.record_event(event)
    storage.record_artifact("job-1", "json", "data/dy/jobs/job-1/json/search_contents.json")

    artifacts = storage.list_artifacts("job-1")
    assert artifacts[0]["artifact_type"] == "json"
    assert artifacts[0]["path"].endswith("search_contents.json")
