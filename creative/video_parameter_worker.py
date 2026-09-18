# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import config

from analysis.llm import create_llm_provider_from_config
from core.events import emit_event
from core.job import CrawlJob
from creative.postgres_repository import PostgresCreativeRepository
from creative.video_parameter_recommendation import (
    analyze_video_parameters,
    build_video_parameter_source,
    fallback_recommendation,
    video_parameter_analysis_deadline_seconds,
)
from system_database import SystemDatabase, SystemDatabaseSettings


def emit(
    job: CrawlJob,
    event: str,
    message: str,
    level: str = "info",
    payload: dict[str, Any] | None = None,
) -> None:
    emit_event(
        event,
        job.job_id,
        "video-parameter-worker",
        "creative",
        level,
        message,
        payload or {},
    )


async def run(job: CrawlJob) -> None:
    database = SystemDatabase(SystemDatabaseSettings.from_environment())
    repository = PostgresCreativeRepository(database)
    try:
        await _run_with_repository(job, repository)
    except Exception as exc:
        project_id = int((job.extra or {}).get("project_id") or 0)
        if project_id > 0:
            try:
                repository.fail_video_parameter_recommendation(project_id, str(exc))
            except Exception:
                pass
        raise
    finally:
        database.dispose()


async def _run_with_repository(job: CrawlJob, repository: Any) -> None:
    extra = job.extra or {}
    project_id = int(extra.get("project_id") or 0)
    if project_id < 1:
        raise ValueError("视频参数分析任务缺少 project_id")
    project = repository.get_project(project_id)
    if not project:
        raise ValueError("创作项目不存在")
    source = build_video_parameter_source(project)
    expected = str(extra.get("source_fingerprint") or "")
    if expected and source["fingerprint"] != expected:
        raise ValueError("剧本已变化，请重新发起视频参数分析")

    repository.start_video_parameter_recommendation(project_id)
    emit(
        job,
        "job_started",
        "开始分析全剧视频生成参数",
        payload={
            "stage": "video_parameters",
            "completed": 0,
            "total": 3,
            "episode_count": len(source["episode_numbers"]),
        },
    )
    analysis_error = ""
    deadline_seconds = video_parameter_analysis_deadline_seconds(config)
    try:
        emit(
            job,
            "progress",
            "正在读取剧本构图、场景规模与节奏信号",
            payload={"stage": "video_parameters", "completed": 1, "total": 3},
        )
        provider = create_llm_provider_from_config(config, prefix="VIDEO_PARAMETER_LLM")
        recommendation = await asyncio.wait_for(
            analyze_video_parameters(provider, source),
            timeout=deadline_seconds,
        )
    except TimeoutError:
        analysis_error = (
            f"AI 分析超过 {deadline_seconds} 秒，已自动切换为规则建议"
        )
        recommendation = fallback_recommendation(source, analysis_error)
        emit(
            job,
            "progress",
            "AI 分析等待超时，正在生成规则建议",
            "warning",
            {"stage": "video_parameters", "completed": 2, "total": 3},
        )
    except Exception as exc:
        analysis_error = str(exc)
        recommendation = fallback_recommendation(source, analysis_error)
        emit(
            job,
            "progress",
            "模型分析不可用，正在生成明确标注的规则建议",
            "warning",
            {"stage": "video_parameters", "completed": 2, "total": 3},
        )

    completed = repository.complete_video_parameter_recommendation(
        project_id,
        source_fingerprint=source["fingerprint"],
        source_episode_numbers=source["episode_numbers"],
        source_complete=source["complete"],
        recommendation=recommendation,
    )
    message = "视频参数建议已生成"
    if analysis_error:
        message = "AI 分析失败，已生成规则默认建议"
    emit(
        job,
        "job_finished",
        message,
        "warning" if analysis_error else "info",
        {
            "stage": "video_parameters",
            "completed": 3,
            "total": 3,
            "project_id": project_id,
            "analysis_mode": completed.get("analysis_mode"),
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recommend script video parameters.")
    parser.add_argument("--job-file", required=True)
    return parser.parse_args()


def main() -> int:
    job = CrawlJob.load(parse_args().job_file)
    project_id = int((job.extra or {}).get("project_id") or 0)
    try:
        asyncio.run(run(job))
    except Exception as exc:
        emit(job, "job_failed", str(exc), "error", {"project_id": project_id})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
