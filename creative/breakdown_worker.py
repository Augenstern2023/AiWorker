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
import json
from pathlib import Path
from typing import Any

import config

from analysis.llm import create_llm_provider_from_config
from core.events import emit_event
from core.job import CrawlJob
from creative.breakdown_service import generate_breakdown
from creative.repository import CreativeRepository
from creative.transcript_assets import find_transcript_bundle


HEARTBEAT_SECONDS = 15.0


def create_provider():
    return create_llm_provider_from_config(config)


def emit(
    job: CrawlJob,
    event: str,
    message: str,
    level: str = "info",
    payload: dict[str, Any] | None = None,
) -> None:
    runtime_dir = Path(str((job.extra or {}).get("runtime_dir") or ".runtime"))
    emit_event(
        event,
        job.job_id,
        "creative-breakdown-worker",
        "creative",
        level,
        message,
        payload or {},
        log_dir=runtime_dir / "logs",
    )


async def emit_analysis_heartbeats(job: CrawlJob, coverage: list[int], stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    while not stop.is_set():
        elapsed = max(0, int(loop.time() - started_at))
        emit(
            job,
            "progress",
            f"模型正在分析剧目脚本，已等待 {elapsed} 秒",
            payload={
                "stage": "model_analysis",
                "indeterminate": True,
                "elapsed_seconds": elapsed,
                "coverage": coverage,
            },
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            continue


async def run(job: CrawlJob) -> None:
    extra = job.extra or {}
    topic_id = int(extra.get("topic_id") or 0)
    if topic_id < 1:
        raise ValueError("拆解生成任务缺少有效 topic_id")
    db_path = Path(str(extra.get("db_path") or ".runtime/creative/creative.db"))
    workspace_root = Path(str(extra.get("workspace_root") or ".")).resolve()
    repository = CreativeRepository(db_path)
    topic = repository.get_topic(topic_id)
    if not topic:
        raise ValueError("选题不存在")

    bundle = find_transcript_bundle(workspace_root, topic["title"], topic.get("platform") or "")
    episodes = bundle.get("episodes") or []
    if not episodes:
        raise ValueError("请先下载剧目脚本再拆解")
    coverage = [int(item["episode_number"]) for item in episodes]
    emit(job, "job_started", "开始根据剧目脚本生成拆解", payload={"coverage": coverage})
    emit(
        job,
        "progress",
        f"已加载 {len(coverage)} 集剧目脚本，正在分析",
        payload={"stage": "breakdown", "completed": 0, "total": 1, "coverage": coverage},
    )

    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(emit_analysis_heartbeats(job, coverage, heartbeat_stop))
    try:
        content, sources, risks, meta = await generate_breakdown(create_provider(), topic, bundle)
    finally:
        heartbeat_stop.set()
        await heartbeat_task
    emit(
        job,
        "progress",
        "模型分析完成，正在校验并保存拆解",
        payload={"stage": "persisting", "completed": 9, "total": 10, "coverage": coverage},
    )
    locked = repository.get_latest_locked_breakdown(topic_id)
    breakdown = repository.create_breakdown(
        topic_id,
        content,
        sources,
        risks,
        parent_version_id=locked["id"] if locked else None,
    )

    target_dir = workspace_root / "data" / "creative" / "jobs" / job.job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    report_path = target_dir / "generation.json"
    result = {
        "action": "breakdown",
        "breakdown_id": breakdown["id"],
        "topic_id": topic_id,
        "coverage": coverage,
    }
    report_path.write_text(
        json.dumps(
            {
                "job_id": job.job_id,
                **result,
                "generation_meta": meta,
                "field_sources": sources,
                "risks": risks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    emit(job, "artifact", "剧目拆解生成记录", payload={"path": str(report_path), "artifact_type": "json"})
    emit(
        job,
        "progress",
        "拆解生成完成",
        payload={"stage": "breakdown", "completed": 1, "total": 1, "coverage": coverage},
    )
    emit(job, "job_finished", "剧目拆解生成完成", payload=result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate transcript-backed drama breakdowns.")
    parser.add_argument("--job-file", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    job = CrawlJob.load(args.job_file)
    try:
        asyncio.run(run(job))
    except Exception as exc:
        emit(job, "job_failed", str(exc), "error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
