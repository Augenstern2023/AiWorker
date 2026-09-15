# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

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
from creative.repository import CreativeRepository

from .service import review_source


def create_provider() -> Any:
    return create_llm_provider_from_config(config)


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
        "screenplay-review-worker",
        "creative",
        level,
        message,
        payload or {},
    )


def _usage_payload(job: CrawlJob, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return dict(payload or {})


async def run(job: CrawlJob) -> None:
    extra = job.extra or {}
    review_id = int(extra.get("review_id") or 0)
    if review_id < 1:
        raise ValueError("剧本审核任务缺少 review_id")
    repository = CreativeRepository(
        Path(str(extra.get("db_path") or ".runtime/creative/creative.db"))
    )
    review = repository.get_script_review(review_id)
    if not review:
        raise ValueError("审核记录不存在")
    repository.start_script_review(review_id)
    emit(job, "job_started", "开始剧本审核")

    def progress(completed: int, total: int) -> None:
        emit(
            job,
            "progress",
            f"已审核 {completed}/{total} 个剧本范围",
            payload={"stage": "script_review", "completed": completed, "total": total},
        )

    def checkpoint(report: dict[str, Any]) -> None:
        repository.checkpoint_script_review(review_id, report)

    try:
        prior_report = review.get("report") or {}
        completed_episodes = (
            prior_report.get("episode_reviews")
            if prior_report.get("partial") is True
            else None
        )
        prompt_snapshot = dict(review["source_payload"].get("prompt_snapshot") or {})
        if prompt_snapshot:
            expected_mode = "single" if review["mode"] == "single" else "series"
            if (
                prompt_snapshot.get("stage") != "review"
                or prompt_snapshot.get("mode") != expected_mode
            ):
                raise ValueError("审核提示词快照与任务范围不匹配")
        provider = create_provider()
        report = await review_source(
            provider,
            review["source_payload"],
            on_progress=progress,
            on_checkpoint=checkpoint,
            initial_episode_reviews=completed_episodes,
            prompt_snapshot=prompt_snapshot or None,
        )
        completed = repository.complete_script_review(review_id, report)
    except Exception as exc:
        repository.fail_script_review(review_id, str(exc))
        raise

    target_dir = Path("data") / "script_reviews" / job.job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    report_path = target_dir / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "review_id": review_id,
                "source_fingerprint": completed["source_fingerprint"],
                "report": report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    emit(
        job,
        "artifact",
        "剧本审核报告",
        payload={"path": str(report_path), "artifact_type": "json"},
    )
    emit(
        job,
        "job_finished",
        "剧本审核完成，等待人工确认",
        payload=_usage_payload(
            job,
            {"review_id": review_id, "ai_verdict": report["ai_verdict"]},
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review screenplay scripts.")
    parser.add_argument("--job-file", required=True)
    return parser.parse_args()


def main() -> int:
    job = CrawlJob.load(parse_args().job_file)
    try:
        asyncio.run(run(job))
    except Exception as exc:
        emit(job, "job_failed", str(exc), "error", _usage_payload(job))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
