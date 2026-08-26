# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Background worker for resumable complete-project reviews."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
from typing import Any, Callable

import config

from analysis.llm import create_llm_provider_from_config
from core.events import emit_event
from core.job import CrawlJob
from creative.repository import CreativeRepository

from .service import review_project_source


RetryCallback = Callable[[int, int, str], None]


def create_provider(*, retry_callback: RetryCallback | None = None) -> Any:
    return create_llm_provider_from_config(
        config,
        prefix="PROJECT_REVIEW_LLM",
        retry_callback=retry_callback,
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
        "project-review-worker",
        "creative",
        level,
        message,
        payload or {},
    )


class _ObservedProvider:
    """Emit request metadata while keeping prompt bodies out of worker events."""

    def __init__(self, job: CrawlJob, source: dict[str, Any]) -> None:
        self.job = job
        self.source = source
        self.provider: Any = None
        self.request: dict[str, Any] = {}
        self.active_payload: dict[str, Any] = {}

    def bind(self, provider: Any) -> "_ObservedProvider":
        self.provider = provider
        return self

    def set_request(self, request: dict[str, Any]) -> None:
        self.request = dict(request)

    async def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if self.provider is None:
            raise RuntimeError("完整项目审核 Provider 尚未初始化")
        prompt_payload = _prompt_payload(messages)
        self.active_payload = self._event_payload(messages, prompt_payload)
        emit(
            self.job,
            "progress",
            "正在执行完整项目审核模型请求",
            payload=self.active_payload,
        )
        return await self.provider.complete_json(messages)

    def retry_callback(self, retry_number: int, max_retries: int, _reason: str) -> None:
        if not self.active_payload:
            return
        payload = dict(self.active_payload)
        payload.update(
            {
                "request_attempt": int(retry_number) + 1,
                "request_max_attempts": int(max_retries) + 1,
                "request_started_at": time.time(),
            }
        )
        emit(
            self.job,
            "progress",
            "完整项目审核模型请求正在重试",
            payload=payload,
        )

    def _event_payload(
        self,
        messages: list[dict[str, str]],
        prompt_payload: dict[str, Any],
    ) -> dict[str, Any]:
        stage_kind = str(self.request.get("stage_kind") or "")
        batch_index = _as_int(self.request.get("batch_index"))
        batch = _source_batch(self.source, stage_kind, batch_index)
        max_retries = _as_int(getattr(self.provider, "max_retries", 0))
        refs = _safe_resource_refs(prompt_payload.get("loaded_resource_refs"))
        return {
            "audit_scope": str(self.source.get("audit_scope") or ""),
            "request_kind": str(self.request.get("kind") or ""),
            "stage_kind": stage_kind,
            "current_episode": _as_int(self.request.get("episode_number")),
            "batch_index": batch_index,
            "batch_size": len(batch),
            "batch_start": batch[0] if batch else 0,
            "batch_end": batch[-1] if batch else 0,
            "reasoning_effort": str(
                getattr(self.provider, "reasoning_effort", "") or ""
            ),
            "prompt_chars": sum(
                len(str(message.get("content") or "")) for message in messages
            ),
            "request_started_at": time.time(),
            "request_attempt": 1,
            "request_max_attempts": max_retries + 1,
            "loaded_resource_refs": refs,
        }


async def run(job: CrawlJob) -> None:
    extra = job.extra or {}
    review_id = _as_int(extra.get("review_id"))
    if review_id < 1:
        raise ValueError("完整项目审核任务缺少 review_id")
    repository = CreativeRepository(
        Path(str(extra.get("db_path") or ".runtime/creative/creative.db"))
    )
    review = repository.get_script_review(review_id)
    if not review:
        raise ValueError("审核记录不存在")
    try:
        source = review.get("source_payload")
        if not isinstance(source, dict) or not source.get("audit_scope"):
            raise ValueError("完整项目审核缺少冻结来源快照")

        repository.start_script_review(review_id)
        emit(
            job,
            "job_started",
            "开始完整项目审核",
            payload={
                "review_id": review_id,
                "audit_scope": str(source.get("audit_scope") or ""),
            },
        )
        observed = _ObservedProvider(job, source)

        def checkpoint(report: dict[str, Any]) -> None:
            repository.checkpoint_script_review(review_id, report)
            completed = len(report.get("all_unit_results") or [])
            emit(
                job,
                "progress",
                "完整项目审核检查点已保存",
                payload={
                    "audit_scope": str(source.get("audit_scope") or ""),
                    "stage": "project_review",
                    "completed": completed,
                    "total": _total_units(source),
                    "checkpoint_key": str(report.get("checkpoint_key") or ""),
                },
            )

        provider = create_provider(retry_callback=observed.retry_callback)
        observed.bind(provider)
        report = await review_project_source(
            observed,
            source,
            on_checkpoint=checkpoint,
            on_request=observed.set_request,
            initial_report=review.get("report") or None,
        )
        target_dir = Path("data") / "project_reviews" / job.job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        report_path = target_dir / "report.json"
        report_path.write_text(
            json.dumps(
                {
                    "review_id": review_id,
                    "source_fingerprint": review["source_fingerprint"],
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
            "完整项目审核报告",
            payload={"path": str(report_path), "artifact_type": "json"},
        )
        repository.complete_project_review(review_id, report)
    except Exception as exc:
        repository.fail_script_review(review_id, str(exc))
        raise

    emit(
        job,
        "job_finished",
        "完整项目审核完成",
        payload={
            "review_id": review_id,
            "audit_scope": str(source.get("audit_scope") or ""),
            "ai_verdict": str(report.get("ai_verdict") or ""),
        },
    )


def _prompt_payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    if not messages:
        return {}
    try:
        value = json.loads(str(messages[-1].get("content") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_resource_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {
        "kind",
        "id",
        "revision",
        "version",
        "stage_kind",
        "episode_number",
        "batch_id",
        "content_fingerprint",
    }
    return [
        {key: item.get(key) for key in allowed if key in item}
        for item in value
        if isinstance(item, dict)
    ]


def _source_batch(
    source: dict[str, Any], stage_kind: str, batch_index: int
) -> list[int]:
    stages = source.get("stages")
    stage = stages.get(stage_kind) if isinstance(stages, dict) else None
    batches = stage.get("batches") if isinstance(stage, dict) else None
    if not isinstance(batches, list) or batch_index < 1 or batch_index > len(batches):
        return []
    batch = batches[batch_index - 1]
    return [_as_int(number) for number in batch] if isinstance(batch, list) else []


def _total_units(source: dict[str, Any]) -> int:
    stages = source.get("stages")
    if not isinstance(stages, dict):
        return 0
    return sum(
        len(stage.get("units") or [])
        for stage in stages.values()
        if isinstance(stage, dict)
    )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review a complete creative project.")
    parser.add_argument("--job-file", required=True)
    return parser.parse_args()


def main() -> int:
    job = CrawlJob.load(parse_args().job_file)
    try:
        asyncio.run(run(job))
    except Exception:
        emit(job, "job_failed", "完整项目审核失败", "error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
