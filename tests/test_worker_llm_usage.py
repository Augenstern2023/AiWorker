# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Terminal events retain actual HTTP usage across worker success and failure."""

import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from analysis.llm import OpenAICompatibleLLMProvider
from core.job import CrawlJob
from creative.free_mode_service import FreeCreativeModeService
from creative.prompt_observability import _terminal_payload, _usage_from_payload
from creative.repository import CreativeRepository
from tests.test_creative_free_generation import _free_project
from tests.test_creative_phase4_generation import (
    blueprint_payload, generation_contract, seed_worker_project,
)
from tests.test_project_review_worker import _review_job, _source
from tests.test_script_optimizations import _project_with_review
from screenplay_review.service import build_project_source, build_text_source


KINDS = ["creative", "free", "free_series", "project_review", "screenplay_review",
         "screenplay_review_series", "screenplay_optimization"]


def _worker_module(kind):
    if kind in {"creative", "free", "free_series"}:
        return "creative.phase4_worker"
    return f"{kind.removesuffix('_series')}.worker"


def _setup_worker(kind, tmp_path, monkeypatch):
    module = _worker_module(kind)
    worker = importlib.import_module(module)
    repository = CreativeRepository(tmp_path / "creative.db")
    extra = {"db_path": str(repository.db_path)}
    content = {"ai_verdict": "通过", "summary": "完成", "core_findings": [],
               "episode_reviews": [], "cross_episode_findings": [], "findings": [],
               "continuity_state": {}}
    if kind == "creative":
        project = seed_worker_project(repository)
        extra.update(action="blueprints", count=3, project_id=project["id"],
                     **generation_contract(repository, project["id"]))
        content = blueprint_payload()
    elif kind in {"free", "free_series"}:
        project = _free_project(repository)
        context = FreeCreativeModeService(repository).create_generation_context(
            context_id="usage-context", project_id=project["id"], stage="brief",
            episode_number=None, instruction="生成", context_refs={}, actor_user_id="owner-1",
        )
        extra.update(action="blueprints", normalized_stage="brief", workflow_mode="free",
                     project_id=project["id"], created_by_user_id="owner-1",
                     operation_id="usage-operation", free_context_id=context["id"],
                     expected_head_revision=0)
        from creative import free_phase4_worker

        async def generate(provider, *args, **kwargs):
            await provider.complete_json([])
            return [{"content": {"title": "方向", "logline": "选择"}}], {}

        monkeypatch.setattr(free_phase4_worker, "generate_original_directions", generate)
        if kind == "free_series":
            contexts = {
                str(number): FreeCreativeModeService(repository).create_generation_context(
                    context_id=f"usage-beats-{number}", project_id=project["id"], stage="beats",
                    episode_number=number, instruction="生成", context_refs={}, actor_user_id="owner-1",
                )["id"] for number in range(1, 4)
            }
            extra.update(action="beats", normalized_stage="beats", series=True,
                         episode_start=1, episode_end=3, free_context_ids=contexts,
                         expected_head_revisions={"1": 0, "2": 0, "3": 0})

            async def generate_beats(provider, *args, **kwargs):
                await provider.complete_json([])
                return {"beats": [{"order": 1, "event": "选择"}]}, {}

            monkeypatch.setattr(free_phase4_worker, "generate_episode_beats", generate_beats)
    elif kind == "project_review":
        job, _ = _review_job(tmp_path, _source(1), suffix="usage")
        return worker, job, content
    elif kind in {"screenplay_review", "screenplay_review_series"}:
        source = build_text_source("主角在危机中反击。")
        project_id = None
        if kind == "screenplay_review_series":
            project, _, _ = _project_with_review(repository, episode_count=2)
            project_id = project["id"]
            source = build_project_source(project, "multiple", [1, 2])
        review = repository.create_script_review(
            source_type="project" if project_id else "text",
            mode="multiple" if project_id else "single", project_id=project_id,
            episode_numbers=[1, 2] if project_id else [1],
            source_fingerprint=source["fingerprint"], source_payload=source, created_by="writer-1",
        )
        extra["review_id"] = review["id"]
    else:
        project, review, _ = _project_with_review(repository)
        optimization = repository.create_script_optimization(
            review_id=review["id"], project_id=project["id"], mode="direct",
            source_fingerprint=review["source_fingerprint"],
            selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
            target_episode_numbers=[1], created_by="admin-1",
        )
        extra["optimization_id"] = optimization["id"]
        content = {"script": {"episode_title": "优化", "scenes": []}}
    return worker, CrawlJob(job_id=f"usage-{kind}", platform="creative",
                            crawler_type="phase4_generate" if kind in {"creative", "free"} else kind,
                            extra=extra), content


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("fail", [False, True])
def test_worker_terminal_events_include_real_usage(kind, fail, tmp_path, monkeypatch, capsys):
    worker, job, content = _setup_worker(kind, tmp_path, monkeypatch)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            "choices": [{"message": {"content": json.dumps(content)},
                         "finish_reason": "length" if fail else "stop"}],
        })

    def create(*args, **kwargs):
        return OpenAICompatibleLLMProvider(
            "https://llm.test/v1", "secret", "model", transport=httpx.MockTransport(handler),
        )

    if kind in {"free", "free_series"}:
        from creative import free_phase4_worker
        monkeypatch.setattr(free_phase4_worker, "create_llm_provider_from_config", create)
    else:
        monkeypatch.setattr(worker, "create_provider", create)
    monkeypatch.setattr(CrawlJob, "load", lambda *args: job)
    monkeypatch.setattr(sys, "argv", ["worker", "--job-file", "unused.json"])
    monkeypatch.chdir(tmp_path)
    assert worker.main() == (1 if fail else 0)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    terminal = [event for event in events if event["event"] in {"job_finished", "job_failed"}]
    assert len(terminal) == 1
    assert requests
    payload = _terminal_payload({"status": "failed" if fail else "succeeded", "events": events})
    assert "llm_usage" in payload
    usage = _usage_from_payload(payload)
    assert usage["call_count"] == len(requests)
    assert usage["input_tokens"] == 11 * len(requests)
    assert usage["output_tokens"] == 5 * len(requests)
    assert usage["total_tokens"] == 16 * len(requests)
    assert usage["complete"] is True
    # Transient provider references must never enter persisted job input.
    assert "_llm_usage_providers" not in job.to_dict()


@pytest.mark.parametrize("kind", KINDS)
def test_failure_before_provider_creation_does_not_invent_tokens(kind, tmp_path, monkeypatch, capsys):
    module = _worker_module(kind)
    worker = importlib.import_module(module)
    job = CrawlJob(job_id="invalid", platform="creative", crawler_type=kind,
                   extra={"text": "x" * 10000})
    monkeypatch.setattr(CrawlJob, "load", lambda *args: job)
    monkeypatch.setattr(sys, "argv", ["worker", "--job-file", "unused.json"])
    monkeypatch.chdir(tmp_path)
    assert worker.main() == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    usage = events[-1]["payload"]["llm_usage"]
    assert usage["call_count"] == usage["total_tokens"] == 0
    assert usage["complete"] is False


def test_idempotent_generation_terminal_has_empty_usage_for_this_run(tmp_path, monkeypatch, capsys):
    from creative import phase4_worker
    from tests.test_creative_phase4_generation import _prepare_outline_worker_project

    repository = CreativeRepository(tmp_path / "creative.db")
    project = _prepare_outline_worker_project(repository)
    extra = generation_contract(repository, project["id"])
    reserved = repository.reserve_generation_operation(
        operation_id=extra["operation_id"], project_id=project["id"], stage="outline",
        request_hash=extra["generation_request_hash"], owner_id="previous-run",
    )
    repository.complete_generation_operation(
        operation_id=extra["operation_id"], request_hash=extra["generation_request_hash"],
        batch_id=reserved["batch_id"], result={"action": "outlines", "episode_ids": [1]},
        owner_id="previous-run", expected_authority=extra["source_authority"],
    )
    job = CrawlJob(job_id="cached-result", platform="creative", crawler_type="phase4_generate",
                   extra={**extra, "project_id": project["id"], "action": "outlines",
                          "db_path": str(repository.db_path)})
    monkeypatch.setattr(CrawlJob, "load", lambda *args: job)
    monkeypatch.setattr(sys, "argv", ["worker", "--job-file", "unused.json"])
    monkeypatch.chdir(tmp_path)
    assert phase4_worker.main() == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    usage = events[-1]["payload"]["llm_usage"]
    assert usage["call_count"] == usage["total_tokens"] == 0
    assert usage["complete"] is False


@pytest.mark.parametrize("kind", ["screenplay_review", "screenplay_optimization"])
@pytest.mark.parametrize("failure", ["refusal", "truncated"])
def test_screenplay_failure_terminal_redacts_provider_text_and_keeps_diagnostics(
    kind, failure, tmp_path, monkeypatch, capsys,
):
    worker, job, _ = _setup_worker(kind, tmp_path, monkeypatch)
    secret = "PROVIDER_PRIVATE_RESPONSE_SENTINEL"
    calls = []

    def handler(request):
        calls.append(request)
        response = {
            "status": "completed", "usage": {"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
            "output": [{"type": "message", "content": [{"type": "refusal", "refusal": secret}]}],
        }
        if failure == "truncated":
            response.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"},
                            output=[{"type": "message", "content": [{"type": "output_text", "text": secret}]}])
        return httpx.Response(200, json=response)

    monkeypatch.setattr(worker, "create_provider", lambda **_: OpenAICompatibleLLMProvider(
        "https://llm.test/v1", "secret", "model", wire_api="responses",
        transport=httpx.MockTransport(handler), max_retries=0,
    ))
    monkeypatch.setattr(CrawlJob, "load", lambda *_: job)
    monkeypatch.setattr(sys, "argv", ["worker", "--job-file", "unused.json"])
    monkeypatch.chdir(tmp_path)
    assert worker.main() == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    terminal = [event for event in events if event["event"] == "job_failed"]
    assert len(terminal) == 1
    assert secret not in json.dumps(events)
    payload = terminal[0]["payload"]
    diagnostic = json.loads(Path(payload["error_debug_path"]).read_text(encoding="utf-8"))
    assert secret in json.dumps(diagnostic)
    if failure == "truncated":
        assert diagnostic["finish_reason"] == "max_output_tokens"
        assert diagnostic["returned_chars"] == len(secret)
        assert diagnostic["partial_text"] == secret
    usage = payload["llm_usage"]
    assert usage["input_tokens"] == 11 * len(calls)
    assert usage["output_tokens"] == 5 * len(calls)
    assert usage["complete"] is True
    repository = CreativeRepository(job.extra["db_path"])
    record = (repository.get_script_review(job.extra["review_id"]) if kind == "screenplay_review"
              else repository.get_script_optimization(job.extra["optimization_id"]))
    assert record["status"] == "failed"
