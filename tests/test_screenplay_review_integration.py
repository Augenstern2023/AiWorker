# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

import pytest

from core.job import CrawlJob
from screenplay_review import worker
from screenplay_review.service import build_review_markdown, build_text_source


def test_v2_markdown_contains_score_gates_dimensions_and_evidence() -> None:
    markdown = build_review_markdown(
        {
            "id": 8,
            "source_type": "project",
            "mode": "single",
            "episode_numbers": [1],
            "status": "pending_confirmation",
            "source_payload": {
                "formal": True,
                "project_name": "测试项目",
                "episodes": [],
            },
            "report": {
                "schema_version": "screenplay-review-report-v2",
                "ai_verdict": "建议修改",
                "quality_status": "needs_revision",
                "overall_score": 68,
                "summary": "需要加强冲突。",
                "hard_gate": {"passed": True, "failed_items": []},
                "dimensions": [
                    {
                        "id": "conflict_strength",
                        "label": "冲突强度",
                        "weighted_score": 6.0,
                        "weight": 12,
                        "selected_option": "needs_revision",
                        "evidence": [
                            {
                                "episode_id": "ep-0001",
                                "scene_id": "scene-1",
                                "text": "冲突很快被化解。",
                            }
                        ],
                    }
                ],
                "blocking_issues": [],
                "revision_plan": [{"priority": "P1", "action": "提高失败代价"}],
                "model": {"name": "fake", "revision": "r1"},
                "review_version": "review-v001",
            },
        }
    )

    assert "质量状态：needs_revision" in markdown
    assert "综合得分：68/100" in markdown
    assert "冲突强度" in markdown
    assert "scene-1" in markdown
    assert "提高失败代价" in markdown


@pytest.mark.asyncio
async def test_worker_routes_frozen_semif_engine_without_calling_legacy_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    source = build_text_source("主角推门而入。")
    source["execution_snapshot"] = {
        "engine": "semif",
        "model_profile": "fake",
        "review_version": "review-v001",
    }

    class Repository:
        def __init__(self) -> None:
            self.review = {
                "id": 3,
                "mode": "single",
                "source_fingerprint": source["fingerprint"],
                "source_payload": source,
                "report": {},
            }
            self.completed = None

        def get_script_review(self, _review_id):
            return self.review

        def start_script_review(self, _review_id):
            return self.review

        def checkpoint_script_review(self, _review_id, report):
            self.review["report"] = report

        def complete_script_review(self, _review_id, report):
            self.completed = report
            return {**self.review, "source_fingerprint": source["fingerprint"]}

        def fail_script_review(self, _review_id, error):
            raise AssertionError(error)

    class Writer:
        async def complete_json(self, _messages):
            return {"summary": "说明", "revision_plan": []}

    class Adapter:
        async def close(self):
            return None

    async def fake_pipeline(_source, **kwargs):
        assert kwargs["model_profile"] == "fake"
        return {
            "schema_version": "screenplay-review-report-v2",
            "engine": "semif",
            "quality_status": "passed",
            "ai_verdict": "通过",
            "summary": "通过",
        }

    repository = Repository()
    monkeypatch.setattr(worker, "create_provider", lambda **_kwargs: Writer())
    monkeypatch.setattr(worker, "create_semif_adapter", lambda: Adapter())
    monkeypatch.setattr(worker, "run_semif_review", fake_pipeline)
    monkeypatch.setattr(worker, "review_source", lambda *_args, **_kwargs: pytest.fail("legacy called"))
    monkeypatch.chdir(tmp_path)

    await worker._run_with_repository(
        CrawlJob(
            job_id="semif-job",
            platform="creative",
            crawler_type="screenplay_review",
            extra={"review_id": 3},
        ),
        repository,
    )

    assert repository.completed["quality_status"] == "passed"
    assert (tmp_path / "data" / "script_reviews" / "semif-job" / "report.json").exists()


@pytest.mark.asyncio
async def test_worker_routes_versioned_rerun_from_immutable_parent(
    tmp_path: Path, monkeypatch
) -> None:
    source = build_text_source("主角推门而入。")
    source["execution_snapshot"] = {
        "engine": "semif",
        "model_profile": "frozen-profile",
        "parent_review_id": 8,
        "review_version": "review-v002",
        "rerun_scope": "affected_episodes",
    }
    parent_report = {
        "schema_version": "screenplay-review-report-v2",
        "semif_decisions": [],
        "dimensions": [],
    }

    class Repository:
        def __init__(self) -> None:
            self.child = {
                "id": 9,
                "mode": "single",
                "source_fingerprint": source["fingerprint"],
                "source_payload": source,
                "report": {},
            }
            self.parent = {
                "id": 8,
                "source_fingerprint": source["fingerprint"],
                "report": parent_report,
            }
            self.completed = None

        def get_script_review(self, review_id):
            return self.parent if int(review_id) == 8 else self.child

        def start_script_review(self, _review_id):
            return self.child

        def checkpoint_script_review(self, _review_id, report):
            self.child["report"] = report

        def complete_script_review(self, _review_id, report):
            self.completed = report
            return {**self.child, "source_fingerprint": source["fingerprint"]}

        def fail_script_review(self, _review_id, error):
            raise AssertionError(error)

    class Writer:
        async def complete_json(self, _messages):
            return {"summary": "说明", "revision_plan": []}

    class Adapter:
        async def close(self):
            return None

    async def fake_pipeline(_source, **kwargs):
        assert kwargs["initial_report"] == parent_report
        assert kwargs["rerun_scope"] == "affected_episodes"
        assert kwargs["review_version"] == "review-v002"
        assert kwargs["model_profile"] == "frozen-profile"
        return {
            "schema_version": "screenplay-review-report-v2",
            "engine": "semif",
            "quality_status": "needs_revision",
            "ai_verdict": "建议修改",
            "summary": "完成复审",
        }

    repository = Repository()
    monkeypatch.setattr(worker, "create_provider", lambda **_kwargs: Writer())
    monkeypatch.setattr(worker, "create_semif_adapter", lambda: Adapter())
    monkeypatch.setattr(worker, "run_semif_review", fake_pipeline)
    monkeypatch.chdir(tmp_path)

    await worker._run_with_repository(
        CrawlJob(
            job_id="semif-rerun-job",
            platform="creative",
            crawler_type="screenplay_review",
            extra={"review_id": 9},
        ),
        repository,
    )

    assert repository.completed["summary"] == "完成复审"
