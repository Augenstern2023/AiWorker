# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from creative.api import build_merged_script_download, build_script_text
from creative.repository import CreativeRepository
from creative.lifecycle_service import TopicLifecycleService, script_resource_fingerprint
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
        "episode_title": "开局", "scenes": [{
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
