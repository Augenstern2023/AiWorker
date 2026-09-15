# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Behavioural contracts for resumable complete-project reviews."""

from __future__ import annotations

import json

import pytest

from project_review.source import build_project_review_source
from project_review.service import (
    build_project_review_markdown,
    build_unit_messages,
    normalize_project_review_report,
    review_project_source,
)


def _project(episode_count: int) -> dict:
    episodes = []
    for number in range(1, episode_count + 1):
        episodes.extend([
            {
                "id": 100 + number,
                "stage": "outline",
                "batch_id": "outline-v1",
                "episode_number": number,
                "version": 1,
                "status": "draft",
                "content": {"event": f"第{number}集大纲"},
            },
            {
                "id": 200 + number,
                "stage": "beats",
                "episode_number": number,
                "version": 1,
                "status": "draft",
                "content": {"beats": [f"第{number}集节拍"]},
            },
            {
                "id": 300 + number,
                "stage": "script",
                "episode_number": number,
                "version": 1,
                "status": "draft",
                "content": {"scenes": [f"第{number}集剧本"]},
            },
        ])
    return {
        "id": 17,
        "name": "长审核测试项目",
        "revision": 4,
        "target_episode_count": episode_count,
        "outline_batch_id": "outline-v1",
        "blueprints": [{"id": 1, "version": 1, "status": "draft", "content": {"title": "Brief"}}],
        "episodes": episodes,
    }


@pytest.fixture
def source() -> dict:
    return build_project_review_source(_project(31), audit_scope="project")


def test_outline_unit_prompt_loads_only_current_unit_and_bounded_context(source) -> None:
    """Removing current content or inserting complete history must fail this contract."""
    messages = build_unit_messages(
        source,
        stage_kind="outline",
        episode_number=31,
        checkpoint={"continuity_state": {"unresolved_conflicts": ["失踪真相"]}},
    )

    payload = json.loads(messages[-1]["content"])

    assert payload["current_unit"]["episode_number"] == 31
    assert payload["current_unit"]["resource"]["content"] == {"event": "第31集大纲"}
    assert "outline" not in payload["current_unit"]
    assert "beats" not in payload["current_unit"]
    assert "script" not in payload["current_unit"]
    assert payload["loaded_resource_refs"]
    assert payload["checkpoint_context"]["unresolved_conflicts"] == ["失踪真相"]
    assert "完整项目历史正文" not in payload
    system_prompt = messages[0]["content"]
    for field in ("resource_id", "version", "field", "evidence", "suggestion"):
        assert field in system_prompt
    assert "不得改写来源内容" in system_prompt


def test_script_unit_prompt_contains_only_its_outline_beats_and_script(source) -> None:
    """Dropping a script dependency or loading unrelated episodes must fail this contract."""
    messages = build_unit_messages(source, stage_kind="script", episode_number=31, checkpoint={})

    payload = json.loads(messages[-1]["content"])
    unit = payload["current_unit"]

    assert unit["resource"]["content"] == {"scenes": ["第31集剧本"]}
    assert unit["outline"]["content"] == {"event": "第31集大纲"}
    assert unit["beats"]["content"] == {"beats": ["第31集节拍"]}
    assert "第30集剧本" not in messages[-1]["content"]


class _Provider:
    def __init__(self, *, fail_episode: int | None = None) -> None:
        self.fail_episode = fail_episode
        self.requests: list[dict] = []
        self.system_prompts: list[str] = []

    async def complete_json(self, messages: list[dict[str, str]]) -> dict:
        self.system_prompts.append(messages[0]["content"])
        payload = json.loads(messages[-1]["content"])
        self.requests.append(payload)
        if payload["request_kind"] == "unit_review":
            episode_number = payload["current_unit"]["episode_number"]
            if episode_number == self.fail_episode:
                raise RuntimeError(f"episode {episode_number} failed")
            return {
                "summary": f"第{episode_number}集完成",
                "findings": [{
                    "severity": "invalid",
                    "category": "节奏",
                    "resource_id": payload["current_unit"]["resource"]["id"],
                    "version": 1,
                    "field": "content",
                    "evidence": f"第{episode_number}集证据",
                    "suggestion": "调整节奏",
                }],
                "continuity_state": {"last_episode": episode_number},
            }
        if payload["request_kind"] == "batch_summary":
            return {"summary": f"批次 {payload['batch_index']}", "findings": []}
        return {
            "ai_verdict": "非法结论",
            "summary": "最终汇总",
            "core_findings": [{"severity": "invalid", "evidence": "跨集证据"}],
        }


@pytest.mark.asyncio
async def test_series_review_template_applies_to_every_request_without_replacing_protocols() -> None:
    source = build_project_review_source(_project(1), audit_scope="outline")
    provider = _Provider()

    await review_project_source(
        provider,
        source,
        prompt_snapshot={
            "stage": "review",
            "mode": "series",
            "effective_instruction": "优先核对跨集身份信息差。",
        },
    )

    assert len(provider.system_prompts) == 3
    assert all("优先核对跨集身份信息差" in item for item in provider.system_prompts)
    assert any("resource_id" in item for item in provider.system_prompts)
    assert any("批次摘要" in item for item in provider.system_prompts)
    assert any("ai_verdict" in item for item in provider.system_prompts)
    assert all("不得覆盖前述只读边界" in item for item in provider.system_prompts)


@pytest.mark.asyncio
async def test_resume_skips_checkpointed_episodes_and_fills_missing_summaries() -> None:
    """Replaying saved unit results must not trigger a second unit request."""
    source = build_project_review_source(_project(12), audit_scope="outline")
    checkpoints: list[dict] = []
    first = _Provider(fail_episode=7)

    with pytest.raises(RuntimeError, match="episode 7 failed"):
        await review_project_source(first, source, on_checkpoint=checkpoints.append)

    partial = checkpoints[-1]
    assert [item["episode_number"] for item in partial["stage_results"]["outline"]] == list(range(1, 7))
    assert partial["cursor"] == {"stage_kind": "outline", "episode_number": 7, "batch_index": 1}

    resumed = _Provider()
    report = await review_project_source(resumed, source, initial_report=partial)

    unit_requests = [
        request["current_unit"]["episode_number"]
        for request in resumed.requests
        if request["request_kind"] == "unit_review"
    ]
    batch_requests = [
        request["batch_index"]
        for request in resumed.requests
        if request["request_kind"] == "batch_summary"
    ]
    assert unit_requests == list(range(7, 13))
    assert batch_requests == [1, 2]
    assert [item["episode_number"] for item in report["stage_results"]["outline"]] == list(range(1, 13))
    assert len({(item["stage_kind"], item["episode_number"]) for item in report["all_unit_results"]}) == 12


@pytest.mark.asyncio
async def test_completed_report_does_not_reissue_the_final_summary() -> None:
    """Discarding a saved final summary must cause this resume guard to fail."""
    source = build_project_review_source(_project(1), audit_scope="outline")
    completed = await review_project_source(_Provider(), source)
    resumed = _Provider()

    report = await review_project_source(resumed, source, initial_report=completed)

    assert report["summary"] == "最终汇总"
    assert resumed.requests == []


@pytest.mark.asyncio
async def test_normalized_partial_report_still_requests_missing_final_summary() -> None:
    """Normalizing a checkpoint must not fabricate a completed final summary."""
    source = build_project_review_source(_project(1), audit_scope="outline")
    partial = {
        "partial": True,
        "audit_scope": "outline",
        "stage_results": {"outline": [], "script": []},
        "batch_summaries": [],
        "continuity_state": {},
        "cursor": {"stage_kind": "outline", "episode_number": 1, "batch_index": 1},
    }
    normalized_partial = normalize_project_review_report(partial, source)
    provider = _Provider()

    report = await review_project_source(provider, source, initial_report=normalized_partial)

    assert normalized_partial.get("final_summary") is None
    assert [request["request_kind"] for request in provider.requests][-1] == "final_summary"
    assert report["partial"] is False


@pytest.mark.asyncio
async def test_report_contract_normalizes_verdict_and_renders_evidence() -> None:
    """Invalid verdicts/severities and omitted report sections must be observable failures."""
    source = build_project_review_source(_project(1), audit_scope="project")
    report = await review_project_source(_Provider(), source)
    normalized = normalize_project_review_report(report, source)
    markdown = build_project_review_markdown(normalized)

    assert normalized["ai_verdict"] == "建议修改"
    assert normalized["summary"] == "最终汇总"
    assert normalized["core_findings"][0]["severity"] == "P2"
    assert set(normalized["stage_results"]) == {"outline", "script"}
    assert normalized["batch_summaries"]
    assert normalized["source_manifest"] == source["manifest"]
    assert normalized["upstream_mismatches"] == source["upstream_mismatches"]
    assert "长审核测试项目" in markdown
    assert source["fingerprint"] in markdown
    assert "资源版本" in markdown
    assert "第1集证据" in markdown
    assert "跨集问题" in markdown
