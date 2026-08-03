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
from creative.repository import CreativeRepository
from screenplay_review import worker
from screenplay_review.service import (
    build_text_source,
    build_project_source,
    normalize_review_report,
    review_source,
    validate_review_request,
)


def _script(episode_number: int, version: int, text: str) -> dict:
    return {
        "id": episode_number * 10 + version,
        "stage": "script",
        "episode_number": episode_number,
        "version": version,
        "content": {
            "episode_title": f"第 {episode_number} 集",
            "scenes": [
                {
                    "scene_no": 1,
                    "blocks": [{"type": "dialogue", "speaker": "主角", "text": text}],
                }
            ],
        },
    }


def test_project_source_uses_latest_selected_script_versions() -> None:
    project = {
        "id": 7,
        "name": "测试项目",
        "episodes": [
            _script(1, 1, "旧版本"),
            _script(1, 2, "新版本"),
            _script(2, 1, "第二集"),
        ],
    }

    source = build_project_source(project, "multiple", [1, 2])

    assert source["project_id"] == 7
    assert source["episode_numbers"] == [1, 2]
    assert [item["version"] for item in source["episodes"]] == [2, 1]
    assert "新版本" in source["episodes"][0]["text"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"source_type": "text", "mode": "multiple", "text": "剧本"},
            "粘贴文本仅支持单集审核",
        ),
        (
            {"source_type": "project", "mode": "multiple", "project_id": 1, "episode_numbers": [1]},
            "多集审核至少选择 2 集",
        ),
    ],
)
def test_review_request_rejects_invalid_scope(payload: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_review_request(payload)


def test_normalize_review_report_limits_core_findings_and_requires_supported_verdict() -> None:
    report = normalize_review_report(
        {
            "ai_verdict": "通过",
            "summary": "整体节奏清晰。",
            "core_findings": [
                {"title": f"问题 {index}", "severity": "P1", "evidence": "证据", "suggestion": "修改"}
                for index in range(7)
            ],
            "episode_reviews": [],
            "cross_episode_findings": [],
        }
    )

    assert report["ai_verdict"] == "通过"
    assert len(report["core_findings"]) == 5
    assert report["core_findings"][0]["evidence"] == "证据"


def test_normalize_review_report_keeps_episode_and_cross_episode_findings() -> None:
    report = normalize_review_report(
        {
            "ai_verdict": "建议修改",
            "summary": "存在承接问题。",
            "core_findings": [],
            "episode_reviews": [
                {
                    "episode_number": 2,
                    "summary": "转场突兀。",
                    "findings": [{"title": "动机缺失", "evidence": "突然合作", "suggestion": "补充铺垫"}],
                }
            ],
            "cross_episode_findings": [{"title": "状态矛盾", "evidence": "两集描述不同"}],
        }
    )

    assert report["episode_reviews"][0]["episode_number"] == 2
    assert report["episode_reviews"][0]["findings"][0]["title"] == "动机缺失"
    assert report["episode_reviews"][0]["findings"][0]["severity"] == "P2"
    assert report["cross_episode_findings"][0]["title"] == "状态矛盾"


def test_normalize_review_report_supplies_a_readable_title_when_ai_omits_one() -> None:
    report = normalize_review_report(
        {
            "ai_verdict": "建议修改",
            "summary": "需要补强。",
            "core_findings": [
                {
                    "rule_id": "logic-01",
                    "evidence": "手续办理缺少合理铺垫。",
                    "suggestion": "补充前因。",
                }
            ],
            "episode_reviews": [],
            "cross_episode_findings": [],
        }
    )

    assert report["core_findings"][0]["title"] == "剧情逻辑待补强"


def test_review_record_requires_fresh_source_before_human_confirmation(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    source = build_text_source("主角在危机中反击。")
    review = repository.create_script_review(
        source_type="text",
        mode="single",
        project_id=None,
        episode_numbers=[1],
        source_fingerprint=source["fingerprint"],
        source_payload=source,
        created_by="writer-1",
    )
    repository.complete_script_review(
        review["id"],
        {
            "ai_verdict": "通过",
            "summary": "首集冲突清晰。",
            "core_findings": [],
            "episode_reviews": [],
            "cross_episode_findings": [],
        },
    )

    confirmed = repository.confirm_script_review(
        review["id"],
        "建议修改",
        "补强结尾钩子",
        "writer-1",
        source["fingerprint"],
    )

    assert confirmed["status"] == "confirmed"
    assert confirmed["ai_verdict"] == "通过"
    assert confirmed["final_verdict"] == "建议修改"
    assert confirmed["confirmed_by"] == "writer-1"

    with pytest.raises(ValueError, match="剧本已更新"):
        repository.confirm_script_review(
            review["id"],
            "通过",
            "",
            "writer-1",
            "changed-fingerprint",
        )


def test_pending_review_is_marked_stale_when_project_source_changes(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    source = build_text_source("主角在危机中反击。")
    review = repository.create_script_review(
        source_type="project",
        mode="single",
        project_id=9,
        episode_numbers=[1],
        source_fingerprint=source["fingerprint"],
        source_payload=source,
        created_by="writer-1",
    )
    repository.complete_script_review(
        review["id"],
        {
            "ai_verdict": "通过",
            "summary": "首集冲突清晰。",
            "core_findings": [],
            "episode_reviews": [],
            "cross_episode_findings": [],
        },
    )

    stale = repository.mark_script_review_stale_if_changed(
        review["id"], "changed-fingerprint"
    )

    assert stale["status"] == "stale"
    assert "剧本已更新" in stale["error"]
    with pytest.raises(ValueError, match="剧本已更新"):
        repository.confirm_script_review(
            review["id"], "通过", "", "writer-1", "changed-fingerprint"
        )


@pytest.mark.asyncio
async def test_review_source_uses_two_outcome_report_contract() -> None:
    class FakeProvider:
        async def complete_json(self, messages):
            assert "首集生死线" in messages[0]["content"]
            assert "short-drama-review-v1" in messages[0]["content"]
            return {
                "ai_verdict": "建议修改",
                "summary": "结尾没有明确续看钩子。",
                "core_findings": [
                    {
                        "title": "结尾钩子不足",
                        "severity": "P1",
                        "rule_id": "E02",
                        "episode_number": 1,
                        "evidence": "主角离开后故事结束。",
                        "suggestion": "在结尾加入新的危机。",
                    }
                ],
                "episode_reviews": [],
                "cross_episode_findings": [],
            }

    report = await review_source(FakeProvider(), build_text_source("主角遭遇危机后离开。"))

    assert report["ai_verdict"] == "建议修改"
    assert report["core_findings"][0]["rule_id"] == "E02"


@pytest.mark.asyncio
async def test_review_worker_persists_ai_report_for_confirmation(tmp_path, monkeypatch) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    source = build_text_source("主角在危机中公开反击。")
    review = repository.create_script_review(
        source_type="text",
        mode="single",
        project_id=None,
        episode_numbers=[1],
        source_fingerprint=source["fingerprint"],
        source_payload=source,
        created_by="writer-1",
    )

    class FakeProvider:
        async def complete_json(self, messages):
            return {
                "ai_verdict": "通过",
                "summary": "冲突清晰。",
                "core_findings": [],
                "episode_reviews": [],
                "cross_episode_findings": [],
            }

    monkeypatch.setattr(worker, "create_provider", lambda: FakeProvider())
    monkeypatch.chdir(tmp_path)
    await worker.run(
        CrawlJob(
            job_id="review-job-1",
            platform="creative",
            crawler_type="screenplay_review",
            extra={
                "review_id": review["id"],
                "db_path": str(repository.db_path),
                "runtime_dir": str(tmp_path / ".runtime"),
            },
        )
    )

    completed = repository.get_script_review(review["id"])
    assert completed is not None
    assert completed["status"] == "pending_confirmation"
    assert completed["report"]["ai_verdict"] == "通过"
    assert (Path("data") / "script_reviews" / "review-job-1" / "report.json").exists()
