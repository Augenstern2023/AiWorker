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

from screenplay_review.service import _semantic_unit_source, review_source
from screenplay_review.strategy import plan_review_execution


def _source(count: int, *, mode: str) -> dict:
    return {
        "fingerprint": "strategy-source",
        "project_id": 7,
        "project_name": "分层审核",
        "mode": mode,
        "formal": True,
        "episode_numbers": list(range(1, count + 1)),
        "brief": {"id": 1, "version": 1, "content": {"theme": "成长"}},
        "characters": [],
        "relationships": [],
        "episodes": [
            {
                "episode_number": number,
                "text": f"第 {number} 集重复文本",
                "script": {
                    "id": 100 + number,
                    "version": 1,
                    "content": {
                        "episode_title": f"第 {number} 集",
                        "scenes": [{"blocks": [{"text": f"正文标记-{number}"}]}],
                    },
                },
                "outline": {"id": 200 + number, "version": 1, "content": {"episode": number}},
                "beats": {"id": 300 + number, "version": 1, "content": {"beats": [{"order": 1}]}},
            }
            for number in range(1, count + 1)
        ],
    }


@pytest.mark.parametrize(
    ("count", "mode", "scale", "uses_batches"),
    [
        (1, "single", "single", False),
        (5, "multiple", "small_multiple", False),
        (6, "multiple", "large_multiple", True),
        (20, "collection", "collection", True),
        (100, "collection", "collection", True),
    ],
)
def test_review_strategy_changes_with_scope(
    count: int, mode: str, scale: str, uses_batches: bool
) -> None:
    plan = plan_review_execution(_source(count, mode=mode))

    assert plan.scale == scale
    assert plan.use_batch_summaries is uses_batches
    assert [number for batch in plan.batches for number in batch] == list(
        range(1, count + 1)
    )
    if uses_batches:
        assert all(3 <= len(batch) <= 5 for batch in plan.batches)


def test_semantic_budget_removes_duplicate_body_without_truncating_episode() -> None:
    source = _source(1, mode="single")
    marker = "完整正文" * 20_000
    source["episodes"][0]["text"] = marker
    source["episodes"][0]["script"]["content"]["scenes"][0]["blocks"][0]["text"] = marker
    plan = plan_review_execution(source, unit_target_tokens=10)

    compact = _semantic_unit_source(source, source["episodes"][0], plan)
    encoded = json.dumps(compact, ensure_ascii=False)

    assert compact["episodes"][0].get("text") is None
    assert marker in encoded
    assert compact["semantic_budget"]["source_was_truncated"] is False


@pytest.mark.asyncio
async def test_collection_review_uses_units_batches_and_body_free_final_summary() -> None:
    source = _source(6, mode="collection")
    requests: list[dict] = []

    class Provider:
        async def complete_json(self, messages):
            payload = json.loads(messages[-1]["content"])
            requests.append(payload)
            kind = payload.get("request_kind")
            if kind == "batch_summary":
                return {"summary": "批次正常", "findings": [], "continuity_state": {}}
            if kind == "final_summary":
                return {
                    "ai_verdict": "通过", "summary": "全集正常", "core_findings": [],
                    "episode_reviews": [], "cross_episode_findings": [],
                }
            number = payload["episode_numbers"][0]
            return {
                "ai_verdict": "通过", "summary": f"第 {number} 集正常", "core_findings": [],
                "episode_reviews": [], "cross_episode_findings": [], "continuity_state": {},
            }

    report = await review_source(Provider(), source)

    assert [item.get("request_kind", "unit") for item in requests].count("unit") == 6
    assert [item.get("request_kind") for item in requests].count("batch_summary") == 2
    final_payload = requests[-1]
    assert final_payload["request_kind"] == "final_summary"
    assert "batch_summaries" in final_payload
    assert all(
        "content" not in reference
        for episode in final_payload["source_manifest"]["episodes"]
        for reference in (episode["script"], episode["outline"], episode["beats"])
    )
    assert "正文标记" not in json.dumps(final_payload, ensure_ascii=False)
    assert len(report["episode_reviews"]) == 6
    assert report["strategy"]["scale"] == "collection"


@pytest.mark.asyncio
async def test_multi_review_continues_after_one_episode_fails_and_saves_others() -> None:
    source = _source(3, mode="multiple")
    checkpoints = []

    class Provider:
        async def complete_json(self, messages):
            payload = json.loads(messages[-1]["content"])
            if payload.get("request_kind") == "final_summary":
                raise AssertionError("存在失败集时不应生成总评")
            number = payload["episode_numbers"][0]
            if number == 2:
                raise RuntimeError("第二集暂时不可用")
            return {
                "ai_verdict": "通过", "summary": f"第 {number} 集正常", "core_findings": [],
                "episode_reviews": [], "cross_episode_findings": [],
            }

    with pytest.raises(RuntimeError, match="第二集暂时不可用"):
        await review_source(
            Provider(), source, on_checkpoint=lambda report: checkpoints.append(report)
        )

    last = checkpoints[-1]
    assert [item["episode_number"] for item in last["episode_reviews"]] == [1, 3]
    assert last["failed_episodes"][0]["episode_number"] == 2
