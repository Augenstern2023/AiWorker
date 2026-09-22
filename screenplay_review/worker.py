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
import time
from typing import Any

import config

from analysis.llm import create_llm_provider_from_config, get_llm_usage_snapshot
from core.events import emit_event
from core.job import CrawlJob
from creative.llm_observability import dump_terminal_error, public_failure_summary
from creative.postgres_repository import PostgresCreativeRepository
from system_database import SystemDatabase, SystemDatabaseSettings

from .pipeline import run_semif_review
from .semif_adapter import SemIfAdapter
from .service import review_source


def create_provider(*, profile: str = "unit", retry_callback: Any = None) -> Any:
    prefix = (
        "SCREENPLAY_REVIEW_SUMMARY_LLM"
        if profile == "summary"
        else "SCREENPLAY_REVIEW_UNIT_LLM"
    )
    return create_llm_provider_from_config(
        config, prefix=prefix, retry_callback=retry_callback
    )


def create_semif_adapter() -> SemIfAdapter:
    """Create the sidecar boundary from process configuration."""
    return SemIfAdapter(
        str(config.SEMIF_BASE_URL),
        timeout_seconds=float(config.SEMIF_TIMEOUT_SECONDS),
        max_retries=int(config.SEMIF_MAX_RETRIES),
    )


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
    return {**(payload or {}), "llm_usage": get_llm_usage_snapshot(
        *getattr(job, "_llm_usage_providers", ())
    )}


async def run(job: CrawlJob) -> None:
    job._llm_usage_providers = []
    database = SystemDatabase(SystemDatabaseSettings.from_environment())
    try:
        await _run_with_repository(job, PostgresCreativeRepository(database))
    finally:
        database.dispose()


async def _run_with_repository(job: CrawlJob, repository: Any) -> None:
    job._llm_usage_providers = []
    extra = job.extra or {}
    review_id = int(extra.get("review_id") or 0)
    if review_id < 1:
        raise ValueError("剧本审核任务缺少 review_id")
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

    active_request: dict[str, Any] = {}

    def request_started(request: dict[str, Any]) -> None:
        active_request.clear()
        active_request.update(request)
        labels = {
            "unit": "正在逐集审核",
            "batch_summary": "正在汇总审核批次",
            "final_summary": "正在生成审核总评",
            "semif_decision": "正在执行 SemIf 结构化判断",
        }
        emit(
            job,
            "progress",
            labels.get(str(request.get("kind") or ""), "正在执行剧本审核"),
            payload={
                "stage": "script_review",
                **request,
                "request_attempt": 1,
                "request_started_at": time.time(),
            },
        )

    def report_retry(retry_number: int, max_retries: int, _reason: str) -> None:
        emit(
            job,
            "progress",
            "剧本审核模型请求正在重试",
            payload={
                "stage": "script_review",
                **active_request,
                "request_attempt": int(retry_number) + 1,
                "request_max_attempts": int(max_retries) + 1,
                "request_started_at": time.time(),
            },
        )

    try:
        prior_report = review.get("report") or {}
        prompt_snapshot = dict(review["source_payload"].get("prompt_snapshot") or {})
        if prompt_snapshot:
            expected_mode = "single" if review["mode"] == "single" else "series"
            if (
                prompt_snapshot.get("stage") != "review"
                or prompt_snapshot.get("mode") != expected_mode
            ):
                raise ValueError("审核提示词快照与任务范围不匹配")
        execution = dict(review["source_payload"].get("execution_snapshot") or {})
        engine = str(execution.get("engine") or "legacy")
        if engine == "semif":
            rerun_scope = str(execution.get("rerun_scope") or "resume")
            pipeline_initial_report = prior_report
            parent_review_id = int(execution.get("parent_review_id") or 0)
            if parent_review_id:
                parent_review = repository.get_script_review(parent_review_id)
                if not parent_review:
                    raise ValueError("复审引用的上一版本不存在")
                if (
                    rerun_scope != "full"
                    and str(parent_review.get("source_fingerprint") or "")
                    != str(review.get("source_fingerprint") or "")
                ):
                    raise ValueError("局部复审的来源快照与上一版本不一致")
                pipeline_initial_report = (
                    None if rerun_scope == "full" else parent_review.get("report") or {}
                )
            summary_provider = create_provider(
                profile="summary", retry_callback=report_retry
            )
            job._llm_usage_providers.append(summary_provider)
            semif = create_semif_adapter()
            try:
                report = await run_semif_review(
                    review["source_payload"],
                    semif=semif,
                    writer_provider=summary_provider,
                    model_profile=str(
                        execution.get("model_profile")
                        or config.SEMIF_MODEL_PROFILE
                        or "local-default"
                    ),
                    on_progress=progress,
                    on_checkpoint=checkpoint,
                    on_request=request_started,
                    initial_report=pipeline_initial_report,
                    prompt_snapshot=prompt_snapshot or None,
                    review_version=str(execution.get("review_version") or "review-v001"),
                    rerun_scope=rerun_scope,
                )
            finally:
                close = getattr(semif, "close", None)
                if callable(close):
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
        elif engine == "legacy":
            provider = create_provider(profile="unit", retry_callback=report_retry)
            job._llm_usage_providers.append(provider)
            summary_provider = create_provider(
                profile="summary", retry_callback=report_retry
            )
            job._llm_usage_providers.append(summary_provider)
            report = await review_source(
                provider,
                review["source_payload"],
                summary_provider=summary_provider,
                on_progress=progress,
                on_checkpoint=checkpoint,
                on_request=request_started,
                initial_report=prior_report,
                prompt_snapshot=prompt_snapshot or None,
            )
        else:
            raise ValueError(f"未知剧本审核引擎: {engine}")
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
            {
                "review_id": review_id,
                "ai_verdict": report["ai_verdict"],
                "engine": str(report.get("engine") or "legacy"),
                "quality_status": str(report.get("quality_status") or ""),
            },
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
        error_code, message = public_failure_summary(exc)
        error_debug_path = dump_terminal_error(job.job_id, exc)
        emit(job, "job_failed", message, "error", _usage_payload(job, {
            "error_code": error_code, "error_debug_path": error_debug_path,
        }))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
