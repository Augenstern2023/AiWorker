# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Behavioural contracts for the resumable complete-project review worker."""

from __future__ import annotations

import json

import pytest

from core.job import CrawlJob
from creative.repository import CreativeRepository
from project_review.source import build_project_review_source


def _source(episode_count: int = 12) -> dict:
    episodes = []
    for number in range(1, episode_count + 1):
        episodes.append(
            {
                "id": 100 + number,
                "stage": "outline",
                "batch_id": "outline-v1",
                "episode_number": number,
                "version": 1,
                "status": "draft",
                "content": {"event": f"第{number}集大纲正文"},
            }
        )
    project = {
        "id": 17,
        "name": "Worker 测试项目",
        "revision": 3,
        "target_episode_count": episode_count,
        "outline_batch_id": "outline-v1",
        "blueprints": [
            {
                "id": 1,
                "version": 1,
                "status": "draft",
                "content": {"title": "项目简报正文"},
            }
        ],
        "episodes": episodes,
    }
    return build_project_review_source(project, audit_scope="outline")


class _Provider:
    reasoning_effort = "high"
    max_retries = 2

    def __init__(
        self,
        *,
        retry_callback=None,
        fail_episode: int | None = None,
        fail_batch_index: int | None = None,
        fail_final: bool = False,
        retry_episode: int | None = None,
    ) -> None:
        self.retry_callback = retry_callback
        self.fail_episode = fail_episode
        self.fail_batch_index = fail_batch_index
        self.fail_final = fail_final
        self.retry_episode = retry_episode
        self.requests: list[dict] = []

    async def complete_json(self, messages: list[dict[str, str]]) -> dict:
        payload = json.loads(messages[-1]["content"])
        self.requests.append(payload)
        kind = payload["request_kind"]
        if kind == "unit_review":
            episode_number = payload["current_unit"]["episode_number"]
            if episode_number == self.retry_episode and self.retry_callback:
                self.retry_callback(1, 2, "项目简报正文不应进入重试事件")
            if episode_number == self.fail_episode:
                raise RuntimeError(f"episode {episode_number} failed")
            return {
                "summary": f"第{episode_number}集完成",
                "findings": [],
                "continuity_state": {"last_episode": episode_number},
            }
        if kind == "batch_summary":
            if payload["batch_index"] == self.fail_batch_index:
                raise RuntimeError(f"batch {payload['batch_index']} failed")
            return {
                "summary": f"批次 {payload['batch_index']} 完成",
                "findings": [],
                "continuity_state": {},
            }
        if self.fail_final:
            raise RuntimeError("final summary failed")
        return {
            "ai_verdict": "通过",
            "summary": "最终汇总完成",
            "core_findings": [],
            "continuity_state": {},
        }


def _review_job(tmp_path, source: dict, *, suffix: str) -> tuple[CrawlJob, int]:
    db_path = tmp_path / f"creative-{suffix}.db"
    repository = CreativeRepository(db_path)
    review, created = repository.create_project_review_once(
        project_id=17,
        episode_numbers=list(range(1, 13)),
        source_fingerprint=source["fingerprint"],
        source_payload=source,
        created_by="tester",
        request_key=f"request-{suffix}",
    )
    assert created is True
    job = CrawlJob(
        job_id=f"project-review-{suffix}",
        platform="creative",
        crawler_type="project_review",
        extra={"review_id": review["id"]},
    )
    return job, int(review["id"]), repository


def _capture_events(monkeypatch, worker) -> list[dict]:
    emitted = []

    def capture(_job, event, message, level="info", payload=None):
        emitted.append(
            {
                "event": event,
                "message": message,
                "level": level,
                "payload": payload or {},
            }
        )

    monkeypatch.setattr(worker, "emit", capture)
    return emitted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_kind",
    ["unit_review", "batch_summary", "final_summary"],
)
async def test_worker_request_start_events_expose_safe_input_resource_refs(
    monkeypatch, tmp_path, request_kind
) -> None:
    from project_review import worker

    source = _source()
    job, _review_id, repository = _review_job(tmp_path, source, suffix=f"refs-{request_kind}")
    provider = _Provider()
    emitted = _capture_events(monkeypatch, worker)
    monkeypatch.setattr(
        worker,
        "create_provider",
        lambda *, retry_callback=None: (
            setattr(provider, "retry_callback", retry_callback) or provider
        ),
    )
    monkeypatch.chdir(tmp_path)

    await worker._run_with_repository(job, repository)

    request_payloads = [
        item["payload"]
        for item in emitted
        if item["payload"].get("request_attempt") == 1
        and item["payload"].get("request_kind") == request_kind
    ]
    assert request_payloads
    allowed_ref_keys = {
        "kind",
        "id",
        "revision",
        "version",
        "stage_kind",
        "episode_number",
        "episode_numbers",
        "batch_id",
        "content_fingerprint",
        "source_fingerprint",
        "audit_scope",
        "unit_count",
    }
    for payload in request_payloads:
        refs = payload["loaded_resource_refs"]
        assert refs
        assert all(set(ref) <= allowed_ref_keys for ref in refs)
        forbidden_ref_keys = {
            "content",
            "summary",
            "evidence",
            "finding",
            "findings",
            "reason",
            "retry_reason",
        }
        assert all(forbidden_ref_keys.isdisjoint(ref) for ref in refs)
        serialized_refs = json.dumps(refs, ensure_ascii=False)
        for forbidden in (
            "retry reason",
            "项目简报正文",
            "大纲正文",
        ):
            assert forbidden not in serialized_refs

        if request_kind == "unit_review":
            assert any(
                ref.get("stage_kind") == payload["stage_kind"]
                and ref.get("episode_number") == payload["current_episode"]
                for ref in refs
            )
        elif request_kind == "batch_summary":
            assert refs == [
                {
                    "stage_kind": payload["stage_kind"],
                    "episode_numbers": list(
                        range(payload["batch_start"], payload["batch_end"] + 1)
                    ),
                    "source_fingerprint": source["fingerprint"],
                }
            ]
        else:
            assert {ref["kind"] for ref in refs} == {"project", "stage"}
            assert next(ref for ref in refs if ref["kind"] == "project") == {
                "kind": "project",
                "id": 17,
                "revision": 3,
                "audit_scope": "outline",
            }
            assert next(ref for ref in refs if ref["kind"] == "stage") == {
                "kind": "stage",
                "stage_kind": "outline",
                "episode_numbers": list(range(1, 13)),
                "batch_id": "outline-v1",
                "unit_count": 12,
            }


@pytest.mark.asyncio
async def test_worker_checkpoints_failure_emits_safe_request_details_and_resumes(
    monkeypatch, tmp_path
) -> None:
    from project_review import worker

    source = _source()
    job, review_id, repository = _review_job(tmp_path, source, suffix="episode-resume")
    emitted = _capture_events(monkeypatch, worker)
    first = _Provider(fail_episode=7, retry_episode=7)
    monkeypatch.setattr(
        worker,
        "create_provider",
        lambda *, retry_callback=None: (
            setattr(first, "retry_callback", retry_callback) or first
        ),
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="episode 7 failed"):
        await worker._run_with_repository(job, repository)

    failed = repository.get_script_review(review_id)
    assert failed is not None
    assert failed["status"] == "failed"
    assert [
        item["episode_number"]
        for item in failed["report"]["stage_results"]["outline"]
    ] == list(range(1, 7))

    event = next(
        item for item in emitted if item["payload"].get("current_episode") == 7
    )
    payload = event["payload"]
    assert payload["audit_scope"] == "outline"
    assert payload["stage_kind"] == "outline"
    assert payload["batch_size"] == 10
    assert (payload["batch_start"], payload["batch_end"]) == (1, 10)
    assert payload["reasoning_effort"] == "high"
    assert payload["prompt_chars"] > 0
    assert (payload["request_attempt"], payload["request_max_attempts"]) == (1, 3)
    assert payload["loaded_resource_refs"]
    assert "第7集大纲正文" not in json.dumps(emitted, ensure_ascii=False)
    assert "项目简报正文" not in json.dumps(emitted, ensure_ascii=False)
    retry_event = next(
        item
        for item in emitted
        if item["payload"].get("current_episode") == 7
        and item["payload"].get("request_attempt") == 2
    )
    assert retry_event["payload"]["request_max_attempts"] == 3

    resumed = _Provider()
    monkeypatch.setattr(
        worker,
        "create_provider",
        lambda *, retry_callback=None: (
            setattr(resumed, "retry_callback", retry_callback) or resumed
        ),
    )

    await worker._run_with_repository(job, repository)

    unit_numbers = [
        request["current_unit"]["episode_number"]
        for request in resumed.requests
        if request["request_kind"] == "unit_review"
    ]
    completed = repository.get_script_review(review_id)
    assert unit_numbers == list(range(7, 13))
    assert completed is not None
    assert completed["status"] == "completed"
    report_path = tmp_path / "data" / "project_reviews" / job.job_id / "report.json"
    assert report_path.exists()
    artifact = next(item for item in emitted if item["event"] == "artifact")
    assert artifact["payload"] == {
        "path": str(report_path.relative_to(tmp_path)),
        "artifact_type": "json",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "provider", "expected_request_kinds", "expected_batch_indexes"),
    [
        (
            "batch-resume",
            _Provider(fail_batch_index=2),
            ["batch_summary", "final_summary"],
            [2],
        ),
        (
            "final-resume",
            _Provider(fail_final=True),
            ["final_summary"],
            [],
        ),
    ],
)
async def test_worker_resume_reissues_only_missing_summary_stages(
    monkeypatch,
    tmp_path,
    suffix,
    provider,
    expected_request_kinds,
    expected_batch_indexes,
) -> None:
    from project_review import worker

    source = _source()
    job, review_id, repository = _review_job(tmp_path, source, suffix=suffix)
    _capture_events(monkeypatch, worker)
    monkeypatch.setattr(
        worker,
        "create_provider",
        lambda *, retry_callback=None: (
            setattr(provider, "retry_callback", retry_callback) or provider
        ),
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError):
        await worker._run_with_repository(job, repository)

    assert repository.get_script_review(review_id)["status"] == "failed"
    resumed = _Provider()
    monkeypatch.setattr(
        worker,
        "create_provider",
        lambda *, retry_callback=None: (
            setattr(resumed, "retry_callback", retry_callback) or resumed
        ),
    )

    await worker._run_with_repository(job, repository)

    assert [request["request_kind"] for request in resumed.requests] == expected_request_kinds
    assert [
        request["batch_index"]
        for request in resumed.requests
        if request["request_kind"] == "batch_summary"
    ] == expected_batch_indexes
    assert repository.get_script_review(review_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_worker_marks_located_review_failed_when_frozen_source_is_invalid(
    monkeypatch, tmp_path
) -> None:
    from project_review import worker

    db_path = tmp_path / "invalid-source.db"
    repository = CreativeRepository(db_path)
    review = repository.create_script_review(
        source_type="project",
        mode="collection",
        project_id=17,
        episode_numbers=[],
        source_fingerprint="invalid",
        source_payload={},
        created_by="tester",
    )
    job = CrawlJob(
        job_id="project-review-invalid-source",
        platform="creative",
        crawler_type="project_review",
        extra={"review_id": review["id"]},
    )
    _capture_events(monkeypatch, worker)

    with pytest.raises(ValueError, match="冻结来源快照"):
        await worker._run_with_repository(job, repository)

    failed = repository.get_script_review(review["id"])
    assert failed is not None
    assert failed["status"] == "failed"


@pytest.mark.asyncio
async def test_worker_keeps_completed_report_resumable_when_artifact_write_fails(
    monkeypatch, tmp_path
) -> None:
    from project_review import worker

    source = _source()
    job, review_id, repository = _review_job(tmp_path, source, suffix="artifact-failure")
    provider = _Provider()
    _capture_events(monkeypatch, worker)
    monkeypatch.setattr(
        worker,
        "create_provider",
        lambda *, retry_callback=None: (
            setattr(provider, "retry_callback", retry_callback) or provider
        ),
    )
    monkeypatch.setattr(
        worker.Path,
        "write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(OSError, match="disk full"):
        await worker._run_with_repository(job, repository)

    failed = repository.get_script_review(review_id)
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["report"]["final_summary"] is True
