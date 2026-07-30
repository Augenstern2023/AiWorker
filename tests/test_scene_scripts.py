# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from creative.api import build_merged_script_download, build_script_text
from creative.delivery import DeliveryService
from creative.repository import CreativeRepository
from creative.lifecycle_service import TopicLifecycleService, script_resource_fingerprint
from identity.schema import Principal
import pytest


def test_script_text_uses_jubaopen_scene_format() -> None:
    text = build_script_text(
        {"name": "聚宝盆"},
        {
            "episode_title": "破锅与馊饭",
            "scenes": [{
                "sequence_no": 1,
                "scene_no": 1,
                "time_of_day": "夜",
                "interior_exterior": "内",
                "location": "破旧柴房",
                "props": ["破铁锅"],
                "characters": ["沈辞霄"],
                "blocks": [
                    {"type": "action", "text": "沈辞霄揭开锅盖，丹药静静躺在锅底。"},
                    {"type": "dialogue", "speaker": "沈辞霄", "modifier": "震惊", "text": "极品聚气丹！"},
                    {"type": "inner_voice", "speaker": "沈辞霄", "text": "这破锅果然不凡。"},
                ],
            }],
        },
        episode_number=1,
    )

    assert text == (
        "第 1 集:破锅与馊饭\n\n"
        "[1]-[1] [夜] [内] [破旧柴房]\n"
        "道具:破铁锅 出场人物:沈辞霄\n\n"
        "△ 沈辞霄揭开锅盖，丹药静静躺在锅底。\n\n"
        "沈辞霄(震惊):极品聚气丹！\n\n"
        "沈辞霄(内心OS):这破锅果然不凡。\n"
    )


def test_script_text_retains_cut_direction() -> None:
    text = build_script_text(
        {"name": "聚宝盆"},
        {
            "episode_title": "转折",
            "scenes": [{
                "sequence_no": 1,
                "scene_no": 1,
                "time_of_day": "夜",
                "interior_exterior": "内",
                "location": "柴房",
                "props": [],
                "characters": [],
                "blocks": [{
                    "type": "cut",
                    "text": "定格在主角按住账本的手上。",
                }],
            }],
        },
        episode_number=1,
    )

    assert "【切镜：定格在主角按住账本的手上。】" in text


def test_combined_script_download_uses_current_scripts_and_episode_dividers() -> None:
    project = {
        "name": "项目名称",
        "topic_title": "聚宝盆",
        "episodes": [
            {
                "id": 101,
                "stage": "beats",
                "episode_number": 1,
                "version": 1,
                "status": "locked",
            },
            {
                "id": 103,
                "stage": "beats",
                "episode_number": 3,
                "version": 2,
                "status": "locked",
            },
            {
                "id": 201,
                "stage": "script",
                "episode_number": 1,
                "version": 1,
                "status": "locked",
                "upstream_refs": {"beats_id": 101, "beats_version": 1},
                "content": {"episode_title": "开局", "scenes": []},
            },
            {
                "id": 203,
                "stage": "script",
                "episode_number": 3,
                "version": 1,
                "status": "draft",
                "upstream_refs": {"beats_id": 103, "beats_version": 2},
                "content": {"episode_title": "转折", "scenes": []},
            },
            {
                "id": 202,
                "stage": "script",
                "episode_number": 2,
                "version": 1,
                "status": "draft",
                "upstream_refs": {"beats_id": 102, "beats_version": 1},
                "content": {"episode_title": "旧版", "scenes": []},
            },
        ],
    }

    filename, text = build_merged_script_download(project)

    assert filename == "聚宝盆第1集-第3集.txt"
    assert "========== 第 1 集 ==========" in text
    assert "========== 第 3 集 ==========" in text
    assert "第 1 集:开局" in text
    assert "第 3 集:转折" in text
    assert text.index("第 1 集:开局") < text.index("第 3 集:转折")
    assert "第 2 集:旧版" not in text


def test_scene_script_requires_locked_beats_and_records_its_source(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": "job-1", "source_path": "rank.json",
        "captured_at": "2026-07-30", "title": "测试剧", "description": "",
        "category": "都市", "rank_type": "热播榜", "score": 90,
        "confidence": "high", "score_details": {}, "recommendation": "推荐", "evidence": [],
    }])[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"title": "测试剧"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(topic["id"], breakdown["id"], "测试项目", 1)
    brief = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向"}, "risk_score": 1, "risk_level": "low",
    }])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1}]})
    repository.lock_episode_beats(beats["id"])
    script = repository.create_episode_script(project["id"], 1, {
        "episode_title": "开局",
        "scenes": [{
            "sequence_no": 1, "scene_no": 1, "time_of_day": "日",
            "interior_exterior": "内", "location": "客厅", "props": [],
            "characters": ["主角"], "blocks": [{"type": "action", "text": "主角进门。"}],
        }],
    })

    assert script["stage"] == "script"
    assert script["upstream_refs"]["beats_id"] == beats["id"]
    assert script["upstream_refs"]["beats_version"] == 1
    assert len(script["upstream_refs"]["beats_content_fingerprint"]) == 64


def test_scene_script_save_validates_structured_content(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": "job-1", "source_path": "rank.json",
        "captured_at": "2026-07-30", "title": "校验剧", "description": "",
        "category": "都市", "rank_type": "热播榜", "score": 90,
        "confidence": "high", "score_details": {}, "recommendation": "推荐", "evidence": [],
    }])[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"title": "校验剧"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(topic["id"], breakdown["id"], "项目", 1)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}, "risk_score": 1, "risk_level": "low"}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1}]})
    repository.lock_episode_beats(beats["id"])
    script = repository.create_episode_script(project["id"], 1, {
        "episode_title": "开局", "scenes": [{"sequence_no": 1, "scene_no": 1,
        "time_of_day": "日", "interior_exterior": "内", "location": "客厅",
        "props": [], "characters": ["主角"], "blocks": [{"type": "action", "text": "进门。"}]}],
    })

    with pytest.raises(ValueError, match="场次剧本缺少"):
        repository.update_episode(script["id"], {"episode_title": "开局", "scenes": []})


def test_draft_delivery_preview_does_not_export_or_complete_workflow(
    tmp_path, monkeypatch
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": "job-1", "source_path": "rank.json",
        "captured_at": "2026-07-30", "title": "草稿交付剧", "description": "",
        "category": "都市", "rank_type": "热播榜", "score": 90,
        "confidence": "high", "score_details": {}, "recommendation": "推荐", "evidence": [],
    }])[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"title": "草稿交付剧"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(topic["id"], breakdown["id"], "草稿项目", 1)
    brief = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向"}, "risk_score": 1, "risk_level": "low",
    }])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1}]})
    repository.lock_episode_beats(beats["id"])
    script = repository.create_episode_script(project["id"], 1, {
        "episode_title": "开局",
        "scenes": [{
            "sequence_no": 1, "scene_no": 1, "time_of_day": "日",
            "interior_exterior": "内", "location": "客厅", "props": [],
            "characters": ["主角"], "blocks": [{"type": "action", "text": "主角进门。"}],
        }],
    })
    draft_beats = repository.create_episode_beats(
        project["id"], 1, {"beats": [{"order": 1, "scene": "客厅"}]}
    )

    class ExistingAudit:
        def query(self, **_kwargs):
            return [{"id": "existing"}]

    service = DeliveryService(
        root=tmp_path,
        creative_repository=repository,
        workflow_repository=object(),
        audit_service=ExistingAudit(),
    )
    task_calls: list[int] = []
    monkeypatch.setattr(
        service,
        "_resolve_delivery_task",
        lambda project_id, _task_id: task_calls.append(project_id) or {"id": "delivery-task"},
    )

    options = service.options()
    option = next(
        item for item in options
        if item["stage"] == "beats" and item["resource_id"] == str(draft_beats["id"])
    )
    calls_before_preview = len(task_calls)
    preview = service.create(
        project_id=project["id"],
        stage="beats",
        resource_id=str(draft_beats["id"]),
        format="md",
        actor=Principal(
            user_id="admin", username="admin", display_name="Admin",
            role_codes=("system_admin",), permissions=frozenset({"*"}),
            data_scope="all", session_id="session",
        ),
    )
    script_preview = service.create(
        project_id=project["id"],
        stage="script",
        resource_id=str(script["id"]),
        format="txt",
        actor=Principal(
            user_id="admin", username="admin", display_name="Admin",
            role_codes=("system_admin",), permissions=frozenset({"*"}),
            data_scope="all", session_id="session",
        ),
    )

    assert option["resource_status"] == "draft"
    assert option["is_formal"] is False
    assert option["workflow_task_id"] is None
    assert {item["stage"] for item in options} >= {
        "brief", "characters", "outline", "beats", "script"
    }
    formal_option = next(item for item in options if item["stage"] == "brief")
    assert formal_option["is_formal"] is True
    assert formal_option["workflow_task_id"] == "delivery-task"
    assert preview["resource_status"] == "draft"
    assert preview["is_formal"] is False
    assert (tmp_path / ".runtime" / "deliveries" / f"{preview['id']}.md").is_file()
    assert script_preview["is_formal"] is False
    assert (tmp_path / ".runtime" / "deliveries" / f"{script_preview['id']}.txt").is_file()
    assert len(task_calls) == calls_before_preview
    assert not repository.has_stage_export(
        project["id"], "beats", preview["stage_content_fingerprint"]
    )


def test_scene_script_lifecycle_tracks_current_locked_beats(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": "job-1", "source_path": "rank.json", "captured_at": "2026-07-30",
        "title": "生命周期剧", "description": "", "category": "都市", "rank_type": "热播榜", "score": 90,
        "confidence": "high", "score_details": {}, "recommendation": "推荐", "evidence": [],
    }])[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"title": "生命周期剧"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(topic["id"], breakdown["id"], "项目", 1)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}, "risk_score": 1, "risk_level": "low"}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1}]})
    repository.lock_episode_beats(beats["id"])
    service = TopicLifecycleService(repository, tmp_path)

    assert service.enrich_topic(topic["id"])["lifecycle_status"] == "pending_script"
    script = repository.create_episode_script(project["id"], 1, {"episode_title": "开局", "scenes": [{"sequence_no": 1, "scene_no": 1, "time_of_day": "日", "interior_exterior": "内", "location": "客厅", "props": [], "characters": ["主角"], "blocks": [{"type": "action", "text": "进门。"}]}]})
    assert service.enrich_topic(topic["id"])["lifecycle_status"] == "script_generated"
    repository.lock_episode_script(script["id"])
    repository.record_stage_export(project["id"], "script", "txt", script_resource_fingerprint(repository.get_episode(script["id"])))
    assert service.enrich_topic(topic["id"])["lifecycle_status"] == "script_exported"
