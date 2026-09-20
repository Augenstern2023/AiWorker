# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import config
import pytest

from analysis.llm import resolve_llm_settings
from creative.api import (
    _recover_transient_video_parameter_conflict,
    build_episode_selection_download,
    build_merged_script_download,
    video_parameter_export_record,
)
from creative.video_parameter_recommendation import (
    MODEL_CAPABILITIES,
    build_video_parameter_source,
    fallback_recommendation,
    normalize_recommendation,
    video_parameter_analysis_deadline_seconds,
)
from core.job import CrawlJob
from creative import video_parameter_worker
from runtime.worker_launcher import build_worker_command


def script_project() -> dict:
    return {
        "id": 7,
        "name": "测试短剧",
        "workflow_mode": "sequential",
        "target_platform": "dy",
        "target_episode_count": 1,
        "locked_brief_id": 11,
        "character_batch_id": "characters-1",
        "outline_batch_id": "outline-1",
        "resource_heads": [],
        "episodes": [
            {
                "id": 21,
                "stage": "beats",
                "episode_number": 1,
                "version": 1,
                "status": "locked",
                "upstream_refs": {"outline_batch_id": "outline-1"},
            },
            {
                "id": 31,
                "stage": "script",
                "episode_number": 1,
                "version": 1,
                "status": "draft",
                "upstream_refs": {
                    "brief_id": 11,
                    "character_batch_id": "characters-1",
                    "outline_batch_id": "outline-1",
                    "beats_id": 21,
                    "beats_version": 1,
                },
                "content": {
                    "episode_title": "直播间反击",
                    "scenes": [
                        {
                            "sequence_no": 1,
                            "scene_no": 1,
                            "time_of_day": "夜",
                            "interior_exterior": "内",
                            "location": "狭长直播间",
                            "characters": ["林岚"],
                            "props": ["手机"],
                            "blocks": [
                                {"type": "action", "text": "林岚对准手机直播镜头。"}
                            ],
                        }
                    ],
                },
            },
        ],
    }


def test_build_source_uses_current_script_and_changes_fingerprint_with_content() -> None:
    project = script_project()
    first = build_video_parameter_source(project)

    assert first["episode_numbers"] == [1]
    assert first["complete"] is True

    project["episodes"][1]["content"]["episode_title"] = "更新后的直播反击"
    second = build_video_parameter_source(project)

    assert second["fingerprint"] != first["fingerprint"]


def test_normalizer_keeps_each_model_inside_its_capability_matrix() -> None:
    source = build_video_parameter_source(script_project())
    raw = {
        "models": {
            "minimax_h3": {
                "scenarios": {
                    "platform_fit": {
                        "ratio": "adaptive",
                        "resolution_tier": "1080p",
                        "clip_duration_seconds": 99,
                        "confidence": 2,
                        "rationale": "超出白名单的模型输出",
                    }
                }
            },
            "minimax_h3_max": {"scenarios": {}},
            "seedance_2_5": {
                "scenarios": {
                    "content_optimal": {
                        "ratio": "16:9",
                        "resolution_tier": "1080p",
                        "clip_duration_seconds": 30,
                        "confidence": 0.8,
                        "rationale": "群像调度需要横向空间",
                    }
                }
            },
        }
    }

    result = normalize_recommendation(raw, source)
    h3 = result["models"]["minimax_h3"]["scenarios"]["platform_fit"]
    seedance = result["models"]["seedance_2_5"]["scenarios"]["content_optimal"]

    assert h3["ratio"] in {"9:16", "16:9"}
    assert h3["resolution_tier"] in MODEL_CAPABILITIES["minimax_h3"]["resolution_tiers"]
    assert h3["clip_duration_seconds"] == 15
    assert h3["confidence"] == 1.0
    assert seedance["ratio"] == "16:9"
    assert seedance["resolution_tier"] == "1080p"
    assert seedance["clip_duration_seconds"] == 30


def test_repository_persists_three_independent_confirmations(postgres_creative) -> None:
    repository = postgres_creative
    project = repository.create_original_project(
        seed_text="一个直播间复仇故事",
        name="直播反击",
        target_episode_count=1,
        target_platform="dy",
        genre="都市",
        target_audience="年轻用户",
        constraints={},
        reference_refs=[],
        created_by="writer-1",
        creation_request_id="video-parameters-project",
    )
    source = {
        "fingerprint": "a" * 64,
        "episode_numbers": [1],
        "complete": True,
        "target_platform": "dy",
        "scripts": [],
    }
    recommendation = fallback_recommendation(source)

    repository.queue_video_parameter_recommendation(project["id"], created_by="writer-1")
    repository.complete_video_parameter_recommendation(
        project["id"],
        source_fingerprint=source["fingerprint"],
        source_episode_numbers=[1],
        source_complete=True,
        recommendation=recommendation,
    )
    for model_key in MODEL_CAPABILITIES:
        repository.confirm_video_parameter_recommendation(
            project["id"],
            model_key=model_key,
            scenario="platform_fit",
            source_fingerprint=source["fingerprint"],
            confirmed_by="writer-1",
        )

    record = repository.get_video_parameter_recommendation(project["id"])

    assert record is not None
    assert set(record["confirmed"]) == set(MODEL_CAPABILITIES)
    assert repository.get_project(project["id"])["video_parameter_recommendation"]["status"] == "ready"

    repository.queue_video_parameter_recommendation(project["id"], created_by="writer-1")
    assert set(repository.get_video_parameter_recommendation(project["id"])["confirmed"]) == set(MODEL_CAPABILITIES)


def test_repository_restores_previous_recommendation_after_job_conflict(
    postgres_creative,
) -> None:
    repository = postgres_creative
    project = repository.create_original_project(
        seed_text="一个直播间复仇故事",
        name="视频参数冲突恢复",
        target_episode_count=1,
        target_platform="dy",
        genre="都市",
        target_audience="年轻用户",
        constraints={},
        reference_refs=[],
        created_by="writer-1",
        creation_request_id="video-parameters-conflict-recovery",
    )
    source = {
        "fingerprint": "b" * 64,
        "episode_numbers": [1],
        "complete": True,
        "target_platform": "dy",
        "scripts": [],
    }
    recommendation = fallback_recommendation(source)
    repository.queue_video_parameter_recommendation(
        project["id"], created_by="writer-1"
    )
    ready = repository.complete_video_parameter_recommendation(
        project["id"],
        source_fingerprint=source["fingerprint"],
        source_episode_numbers=[1],
        source_complete=True,
        recommendation=recommendation,
    )

    repository.queue_video_parameter_recommendation(
        project["id"], created_by="writer-1"
    )
    repository.fail_video_parameter_recommendation(
        project["id"], "platform 'creative' already has a running job"
    )
    restored = repository.restore_video_parameter_recommendation(project["id"])

    assert restored is not None
    assert restored["status"] == "ready"
    assert restored["analysis_error"] == recommendation["analysis_error"]
    assert restored["recommendation"] == recommendation
    assert restored["generated_at"] == ready["generated_at"]


def test_transient_conflict_recovery_keeps_a_previous_recommendation() -> None:
    recovered = {
        "project_id": 23,
        "status": "ready",
        "analysis_error": "",
        "recommendation": {"models": {"minimax_h3": {}}},
    }

    class FakeRepository:
        restored_project_id = 0

        def restore_video_parameter_recommendation(self, project_id: int):
            self.restored_project_id = project_id
            return recovered

    repository = FakeRepository()
    result = _recover_transient_video_parameter_conflict(
        repository,
        23,
        {
            "project_id": 23,
            "status": "failed",
            "analysis_error": "platform 'creative' already has a running job",
            "source_fingerprint": "c" * 64,
            "recommendation": {"models": {"minimax_h3": {}}},
        },
    )

    assert result == recovered
    assert repository.restored_project_id == 23


def test_transient_conflict_without_a_previous_recommendation_is_hidden() -> None:
    class FakeRepository:
        def restore_video_parameter_recommendation(self, _project_id: int):
            raise AssertionError("empty conflicts must not be restored as ready")

    result = _recover_transient_video_parameter_conflict(
        FakeRepository(),
        3,
        {
            "project_id": 3,
            "status": "failed",
            "analysis_error": "platform 'creative' already has a running job",
            "source_fingerprint": "",
            "recommendation": {},
        },
    )

    assert result is None


def test_merged_script_download_adds_model_grouped_recommendations() -> None:
    project = script_project()
    source = build_video_parameter_source(project)
    recommendation = fallback_recommendation(source)
    project["video_parameter_recommendation"] = {
        "source_episode_numbers": [1],
        "source_complete": True,
        "stale": True,
        "recommendation": recommendation,
        "confirmed": {},
    }

    _, text = build_merged_script_download(project)

    assert "AI 视频规格建议" in text
    assert "MiniMax H3" in text
    assert "MiniMax H3 Max" in text
    assert "Seedance 2.5" in text
    assert "剧本已变化" in text
    assert "第 1 集:直播间反击" in text


def test_script_export_dimensions_are_adjustable_without_persisting() -> None:
    project = script_project()
    source = build_video_parameter_source(project)
    stored = {
        "source_episode_numbers": [1],
        "source_complete": True,
        "stale": False,
        "recommendation": fallback_recommendation(source),
        "confirmed": {},
    }
    export_record = video_parameter_export_record(
        stored,
        '{"models":[{"model_key":"minimax_h3","scenario":"platform_fit",'
        '"ratio":"16:9","resolution_tier":"768P"}]}',
    )

    _, body, _ = build_episode_selection_download(
        project,
        "script",
        None,
        "txt",
        video_parameter_record=export_record,
    )
    text = body.decode("utf-8")

    assert "MiniMax H3" in text
    assert "横屏 16:9" in text
    assert "生成档位 768P" in text
    assert "本次导出" in text
    assert "Seedance 2.5" not in text
    assert stored["confirmed"] == {}


def test_video_parameter_worker_command() -> None:
    assert build_worker_command(
        ".runtime/jobs/video-parameters.json",
        platform="creative",
        crawler_type="video_parameter_recommendation",
    )[-4:] == [
        "-m",
        "creative.video_parameter_worker",
        "--job-file",
        ".runtime/jobs/video-parameters.json",
    ]


def test_script_workspace_contains_model_specific_recommendation_ui() -> None:
    template = Path("templates/common/workspace/project_scripts.html").read_text(encoding="utf-8")
    modal = Path("templates/common/workspace/creative_episode_download_modal.html").read_text(encoding="utf-8")
    script = Path("static/js/common/creative.js").read_text(encoding="utf-8")

    assert "AI 视频规格建议" in template
    assert "minimax_h3_max" in template
    assert "seedance_2_5" in template
    assert "video-parameter-recommendation/analyze" in script
    assert "confirmVideoParameter" in script
    assert "videoParameterExportDefaults" in script
    assert "enabled: true" in script
    assert "导出视频尺寸建议" in modal
    assert 'x-model="item.ratio"' in modal
    assert 'x-model="item.resolutionTier"' in modal
    assert "videoParameterElapsedSeconds" in script
    assert "videoParameterRemainingSeconds" in template
    assert "不会一直等待" in template
    assert "progress-bar" in template


def test_video_parameter_llm_profile_is_bounded_for_fast_feedback() -> None:
    settings = resolve_llm_settings(config, prefix="VIDEO_PARAMETER_LLM")

    assert settings["reasoning_effort"] == "low"
    assert settings["max_output_tokens"] <= 8_192
    assert settings["timeout_seconds"] <= 90
    assert settings["max_retries"] <= 1
    assert settings["retry_read_timeouts"] is False
    assert video_parameter_analysis_deadline_seconds(config) == 60


@pytest.mark.asyncio
async def test_video_parameter_worker_falls_back_at_total_deadline(monkeypatch) -> None:
    project = script_project()
    completed: dict = {}

    class FakeRepository:
        def get_project(self, project_id: int) -> dict:
            assert project_id == project["id"]
            return project

        def start_video_parameter_recommendation(self, project_id: int) -> None:
            assert project_id == project["id"]

        def complete_video_parameter_recommendation(self, project_id: int, **values):
            assert project_id == project["id"]
            completed.update(values)
            return {
                "analysis_mode": values["recommendation"]["analysis_mode"]
            }

    async def never_finishes(_provider, _source):
        await asyncio.sleep(10)

    monkeypatch.setattr(
        video_parameter_worker,
        "create_llm_provider_from_config",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        video_parameter_worker, "analyze_video_parameters", never_finishes
    )
    monkeypatch.setattr(
        video_parameter_worker,
        "video_parameter_analysis_deadline_seconds",
        lambda _config: 0.01,
    )
    monkeypatch.setattr(video_parameter_worker, "emit", lambda *_args, **_kwargs: None)
    source = build_video_parameter_source(project)
    job = CrawlJob(
        job_id="video-parameter-timeout",
        platform="creative",
        crawler_type="video_parameter_recommendation",
        extra={
            "project_id": project["id"],
            "source_fingerprint": source["fingerprint"],
        },
    )

    await video_parameter_worker._run_with_repository(job, FakeRepository())

    assert completed["recommendation"]["analysis_mode"] == "rules_fallback"
    assert "超过" in completed["recommendation"]["analysis_error"]
