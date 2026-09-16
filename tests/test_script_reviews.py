# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from core.job import CrawlJob
from creative.repository import CreativeRepository
from screenplay_review.api import decode_script_upload
from screenplay_review import worker
from screenplay_review.service import (
    _assert_outline_ref,
    _review_messages,
    build_review_markdown,
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


def test_project_source_requires_a_locked_story_context() -> None:
    project = {
        "id": 7,
        "name": "测试项目",
        "episodes": [
            _script(1, 1, "旧版本"),
            _script(1, 2, "新版本"),
            _script(2, 1, "第二集"),
        ],
    }

    with pytest.raises(ValueError, match="尚未锁定 Brief"):
        build_project_source(project, "multiple", [1, 2])


def test_free_project_source_uses_selected_heads_for_formal_review() -> None:
    project = {
        "id": 8,
        "name": "自由审核项目",
        "workflow_mode": "free",
        "blueprints": [
            {"id": 11, "status": "brief_locked", "version": 1, "content": {"tone": "逆袭"}},
        ],
        "characters": [
            {"id": 21, "batch_id": "cast-1", "status": "locked", "version": 1, "content": {"name": "主角"}},
        ],
        "relationships": [],
        "episodes": [
            {"id": 31, "stage": "outline", "batch_id": "outline-1", "episode_number": 1, "status": "locked", "version": 1, "content": {"core_event": "反击"}},
            {"id": 41, "stage": "beats", "episode_number": 1, "status": "locked", "version": 1, "content": {"beats": [{"order": 1}]}},
            _script(1, 1, "当前 Head"),
            _script(1, 2, "未选中的新版本"),
        ],
        "resource_heads": [
            {"stage": "brief", "episode_number": 0, "resource_ref": "11"},
            {"stage": "characters", "episode_number": 0, "resource_ref": "cast-1"},
            {"stage": "outline", "episode_number": 0, "resource_ref": "outline-1"},
            {"stage": "beats", "episode_number": 1, "resource_ref": "41"},
            {"stage": "script", "episode_number": 1, "resource_ref": "11"},
        ],
    }

    source = build_project_source(project, "single", [1])

    assert source["episodes"][0]["resource_id"] == 11
    assert "当前 Head" in source["episodes"][0]["text"]
    assert "未选中的新版本" not in source["episodes"][0]["text"]


def test_project_source_freezes_the_locked_outline_beats_and_story_context() -> None:
    project = {
        "id": 7,
        "name": "测试项目",
        "locked_brief_id": 11,
        "character_batch_id": "characters-v1",
        "outline_batch_id": "outlines-v1",
        "blueprints": [
            {"id": 11, "status": "brief_locked", "version": 2, "content": {"tone": "逆袭"}}
        ],
        "characters": [
            {"id": 21, "batch_id": "characters-v1", "status": "locked", "version": 1, "content": {"name": "主角"}}
        ],
        "relationships": [
            {"id": 22, "batch_id": "characters-v1", "status": "locked", "version": 1, "content": {"from": "主角", "to": "反派"}}
        ],
        "episodes": [
            {
                "id": 31, "stage": "outline", "batch_id": "outlines-v1", "episode_number": 1,
                "status": "locked", "version": 1, "content": {"episode": 1, "core_event": "主角反击"},
            },
            {
                "id": 41, "stage": "beats", "episode_number": 1, "status": "locked", "version": 1,
                "upstream_refs": {"brief_id": 11, "character_batch_id": "characters-v1", "outline_batch_id": "outlines-v1", "outline_id": 31, "outline_version": 1, "outline_content_fingerprint": "80f052f643b47fe486fa710c4035c2aa7e1bcf31639055ce8374c72a2f955901"},
                "content": {"beats": [{"order": 1, "goal": "公开反击"}]},
            },
            {
                "id": 51, "stage": "script", "episode_number": 1, "status": "draft", "version": 1,
                "upstream_refs": {"brief_id": 11, "character_batch_id": "characters-v1", "outline_batch_id": "outlines-v1", "outline_id": 31, "outline_version": 1, "outline_content_fingerprint": "80f052f643b47fe486fa710c4035c2aa7e1bcf31639055ce8374c72a2f955901", "beats_id": 41, "beats_version": 1},
                "content": {"episode_title": "第 1 集", "scenes": []},
            },
        ],
    }

    source = build_project_source(project, "single", [1])

    assert source["formal"] is True
    assert source["brief"]["id"] == 11
    assert source["episodes"][0]["outline"]["content"]["core_event"] == "主角反击"
    assert source["episodes"][0]["beats"]["id"] == 41


def test_outline_reference_accepts_authority_hash_after_series_overview_changes() -> None:
    content = {"episode": 1, "core_event": "主角反击"}
    fingerprint = CreativeRepository._outline_content_fingerprint(content)
    outline = {
        "id": 31,
        "version": 1,
        "content": {**content, "series_overview": {"theme": "后补的全剧概览"}},
    }
    beats = {
        "upstream_refs": {
            "outline_id": 31,
            "outline_version": 1,
            "outline_content_fingerprint": fingerprint,
        }
    }

    _assert_outline_ref(beats, outline, "第 1 集 Beats")


def test_formal_review_prompt_requires_an_independent_beats_audit() -> None:
    messages = _review_messages({"formal": True, "episode_numbers": [1], "episodes": [{}]})

    assert "逐项核对 Beats" in messages[0]["content"]
    assert "beats_then_script" in messages[0]["content"]


def test_review_template_changes_focus_without_replacing_report_contract() -> None:
    snapshot = {
        "stage": "review",
        "mode": "single",
        "effective_instruction": "优先检查身份信息差是否清楚。",
    }

    messages = _review_messages(
        {"formal": True, "episode_numbers": [1], "episodes": [{}]},
        snapshot,
    )

    system = messages[0]["content"]
    assert "优先检查身份信息差是否清楚" in system
    assert "ai_verdict、summary、core_findings" in system
    assert "outline_revision_required" in system
    assert "不得覆盖前述 JSON 契约" in system


def test_uploaded_script_text_accepts_markdown_and_common_chinese_encoding() -> None:
    filename, text = decode_script_upload("第一集.markdown", "# 第一集\n主角登场".encode("gb18030"))

    assert filename == "第一集.markdown"
    assert "主角登场" in text


def test_uploaded_script_text_rejects_non_text_extensions() -> None:
    with pytest.raises(ValueError, match="仅支持文本剧本文件"):
        decode_script_upload("剧本.docx", b"not a document")


def test_review_report_exports_as_writer_facing_markdown() -> None:
    markdown = build_review_markdown({
        "id": 12,
        "source_type": "text",
        "mode": "single",
        "status": "pending_confirmation",
        "episode_numbers": [1],
        "source_payload": {"source_filename": "第一集.md", "formal": False},
        "report": {
            "ai_verdict": "建议修改",
            "summary": "钩子有效，但转折不足。",
            "core_findings": [{
                "severity": "P1", "title": "结尾钩子不清晰",
                "outline_anchor": {"field": "cliffhanger", "value": "收到匿名短信"},
                "beat_orders": [3], "evidence": "结尾没有明确威胁。",
                "suggestion": "补足威胁来源。", "change_route": "script_only",
            }],
            "episode_reviews": [], "cross_episode_findings": [],
        },
    })

    assert markdown.startswith("# 剧本审核报告 #12")
    assert "剧本文件：第一集.md（非正式）" in markdown
    assert "对应大纲：结尾悬念：收到匿名短信" in markdown
    assert "field=" not in markdown


def test_project_source_rejects_scripts_without_an_exact_outline_reference() -> None:
    project = {
        "id": 7, "name": "测试项目", "locked_brief_id": 11,
        "character_batch_id": "characters-v1", "outline_batch_id": "outlines-v1",
        "blueprints": [{"id": 11, "status": "brief_locked", "content": {}}],
        "characters": [{"id": 21, "batch_id": "characters-v1", "status": "locked", "content": {}}],
        "relationships": [],
        "episodes": [
            {"id": 31, "stage": "outline", "batch_id": "outlines-v1", "episode_number": 1, "status": "locked", "version": 1, "content": {}},
            {"id": 41, "stage": "beats", "episode_number": 1, "status": "locked", "version": 1, "upstream_refs": {"brief_id": 11, "character_batch_id": "characters-v1", "outline_batch_id": "outlines-v1"}, "content": {}},
            {"id": 51, "stage": "script", "episode_number": 1, "status": "draft", "version": 1, "upstream_refs": {"brief_id": 11, "character_batch_id": "characters-v1", "outline_batch_id": "outlines-v1", "beats_id": 41, "beats_version": 1}, "content": {"scenes": []}},
        ],
    }

    with pytest.raises(ValueError, match="分集大纲版本已过期"):
        build_project_source(deepcopy(project), "single", [1])


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

    monkeypatch.setattr(worker, "create_provider", lambda **_: FakeProvider())
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


@pytest.mark.asyncio
async def test_review_worker_keeps_a_viewable_checkpoint_when_multi_episode_review_is_interrupted(
    tmp_path, monkeypatch
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    source = {
        "fingerprint": "checkpoint-source",
        "mode": "multiple",
        "formal": False,
        "episode_numbers": [1, 2],
        "episodes": [
            {"episode_number": 1, "resource_id": 1, "version": 1, "text": "第一集"},
            {"episode_number": 2, "resource_id": 2, "version": 1, "text": "第二集"},
        ],
    }
    review = repository.create_script_review(
        source_type="text",
        mode="multiple",
        project_id=None,
        episode_numbers=[1, 2],
        source_fingerprint=source["fingerprint"],
        source_payload=source,
        created_by="writer-1",
    )

    class InterruptedProvider:
        calls = 0

        async def complete_json(self, messages):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("服务中断")
            return {
                "ai_verdict": "建议修改",
                "summary": "第一集钩子有效。",
                "core_findings": [{"title": "结尾钩子", "evidence": "危机出现", "suggestion": "加强威胁"}],
                "episode_reviews": [],
                "cross_episode_findings": [],
            }

    monkeypatch.setattr(worker, "create_provider", lambda **_: InterruptedProvider())
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="服务中断"):
        await worker.run(
            CrawlJob(
                job_id="review-checkpoint-job",
                platform="creative",
                crawler_type="screenplay_review",
                extra={"review_id": review["id"], "db_path": str(repository.db_path)},
            )
        )

    interrupted = repository.get_script_review(review["id"])
    assert interrupted is not None
    assert interrupted["status"] == "failed"
    assert interrupted["report"]["partial"] is True
    assert interrupted["report"]["completed_episode_count"] == 1
    assert interrupted["report"]["total_episode_count"] == 2
    assert interrupted["report"]["episode_reviews"][0]["episode_number"] == 1
