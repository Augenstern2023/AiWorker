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
from creative.repository import CreativeRepository
from screenplay_review.service import build_project_source, source_fingerprint

from .service import content_fingerprint, optimize_episode, report_findings


def create_provider(*, retry_callback: Any = None) -> Any:
    return create_llm_provider_from_config(
        config,
        prefix="SCREENPLAY_OPTIMIZATION_LLM",
        retry_callback=retry_callback,
    )


def emit(job: CrawlJob, event: str, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    emit_event(event, job.job_id, "screenplay-optimization-worker", "creative", level, message, payload or {})


def _usage_payload(job: CrawlJob, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {**(payload or {}), "llm_usage": get_llm_usage_snapshot(
        *getattr(job, "_llm_usage_providers", ())
    )}


def _latest(project: dict[str, Any], stage: str, episode_number: int) -> dict[str, Any] | None:
    candidates = [
        item for item in project.get("episodes") or []
        if item.get("stage") == stage and int(item.get("episode_number") or 0) == episode_number
    ]
    return max(candidates, key=lambda item: (int(item.get("version") or 0), int(item.get("id") or 0)), default=None)


async def run(job: CrawlJob) -> None:
    job._llm_usage_providers = []
    extra = job.extra or {}
    optimization_id = int(extra.get("optimization_id") or 0)
    if optimization_id < 1:
        raise ValueError("剧本优化任务缺少 optimization_id")
    repository = CreativeRepository(Path(str(extra.get("db_path") or ".runtime/creative/creative.db")))
    optimization = repository.get_script_optimization(optimization_id)
    if not optimization:
        raise ValueError("优化任务不存在")
    review = repository.get_script_review(int(optimization["review_id"]))
    project = repository.get_project(int(optimization["project_id"]))
    if not review or not project:
        raise ValueError("审核记录或创作项目不存在")
    repository.start_script_optimization(optimization_id)
    emit(job, "job_started", "开始生成剧本优化草稿")
    try:
        prompt_snapshot = dict(optimization.get("prompt_snapshot") or {})
        if prompt_snapshot:
            expected_mode = "series" if optimization["mode"] == "structure" else "single"
            if (
                prompt_snapshot.get("stage") != "optimization"
                or prompt_snapshot.get("mode") != expected_mode
            ):
                raise ValueError("优化提示词快照与任务模式不匹配")
        current_source = build_project_source(
            project,
            str(review["mode"]),
            [int(number) for number in review["episode_numbers"]],
        )
        if source_fingerprint(current_source) != optimization["source_fingerprint"]:
            raise ValueError("剧本已更新，请重新审核后再生成优化草稿")
        if not current_source.get("formal"):
            raise ValueError("该审核缺少完整分集上下文，请重新发起正式项目审核")
    except Exception as exc:
        repository.fail_script_optimization(optimization_id, str(exc))
        raise
    available = report_findings(review)
    selected_by_episode: dict[int, list[dict[str, Any]]] = {}
    for selection in optimization["selected_findings"]:
        finding = available.get(str(selection.get("ref") or ""))
        if not finding:
            continue
        for number in selection.get("episode_numbers") or []:
            selected_by_episode.setdefault(int(number), []).append(finding)
    summaries = list((review.get("report") or {}).get("episode_reviews") or [])
    context_by_episode = {
        int(item["episode_number"]): item for item in current_source.get("episodes") or []
    }
    total = len(optimization["target_episode_numbers"])
    current_episode = 0
    current_phase = ""

    def report_retry(retry_number: int, max_retries: int, _reason: str) -> None:
        # Model errors can contain request details; events expose only safe metadata.
        emit(
            job, "progress", "剧本优化模型请求正在重试",
            payload={
                "optimization_id": optimization_id,
                "current_episode": current_episode,
                "optimization_phase": current_phase,
                "request_attempt": int(retry_number) + 1,
                "request_max_attempts": int(max_retries) + 1,
                "request_started_at": time.time(),
            },
        )

    try:
        provider = create_provider(retry_callback=report_retry)
        job._llm_usage_providers.append(provider)
        for index, episode_number in enumerate(optimization["target_episode_numbers"], start=1):
            current_episode = int(episode_number)
            episode_context = context_by_episode.get(int(episode_number))
            if not episode_context:
                raise ValueError(f"第 {episode_number} 集场次剧本不存在")
            script = episode_context.get("script") or {}
            beats = episode_context.get("beats") or {}
            if not script:
                raise ValueError(f"第 {episode_number} 集场次剧本不存在")
            if optimization["mode"] == "structure" and not beats:
                raise ValueError(f"第 {episode_number} 集 Beats 不存在")
            existing = [
                draft for draft in repository.get_script_optimization(optimization_id)["drafts"]
                if int(draft["episode_number"]) == int(episode_number)
            ]
            expected = {"beats", "script"} if optimization["mode"] == "structure" else {"script"}
            if existing:
                if len(existing) != len(expected) or {draft["kind"] for draft in existing} != expected:
                    raise ValueError(f"第 {episode_number} 集已有不完整的优化草稿")
                emit(
                    job, "progress", f"第 {episode_number} 集草稿已保存，跳过重复生成",
                    payload={"completed": index, "total": total, "current_episode": current_episode},
                )
                continue
            emit(
                job, "progress", f"正在生成第 {episode_number} 集优化草稿",
                payload={
                    "completed": index - 1, "total": total,
                    "current_episode": current_episode, "request_attempt": 1,
                    "request_max_attempts": int(getattr(provider, "max_retries", 0)) + 1,
                    "request_started_at": time.time(),
                },
            )

            def phase_started(phase: str) -> None:
                nonlocal current_phase
                current_phase = str(phase)
                label = "Beats" if current_phase == "beats" else "场次剧本"
                emit(
                    job,
                    "progress",
                    f"正在生成第 {episode_number} 集{label}优化草稿",
                    payload={
                        "completed": index - 1,
                        "total": total,
                        "current_episode": current_episode,
                        "optimization_phase": current_phase,
                        "request_attempt": 1,
                        "request_max_attempts": int(getattr(provider, "max_retries", 0)) + 1,
                        "request_started_at": time.time(),
                    },
                )

            result = await optimize_episode(
                provider,
                mode=str(optimization["mode"]),
                episode_number=int(episode_number),
                script_content=script["content"],
                beats_content=beats["content"] if beats else None,
                review_context={
                    "brief": current_source.get("brief"),
                    "characters": current_source.get("characters"),
                    "relationships": current_source.get("relationships"),
                    "episode": episode_context,
                },
                findings=selected_by_episode.get(int(episode_number), []),
                summaries=summaries,
                prompt_snapshot=prompt_snapshot or None,
                on_phase=phase_started,
            )
            if optimization["mode"] == "structure":
                episode_drafts = [
                    {
                        "episode_number": int(episode_number), "kind": "beats",
                        "source_episode_id": beats["id"],
                        "source_fingerprint": content_fingerprint(beats["content"]),
                        "content": result["beats"],
                    },
                    {
                        "episode_number": int(episode_number), "kind": "script",
                        "source_episode_id": None,
                        "source_fingerprint": content_fingerprint(result["beats"]),
                        "content": result["script"],
                    },
                ]
            else:
                episode_drafts = [
                    {
                        "episode_number": int(episode_number), "kind": "script",
                        "source_episode_id": script["id"],
                        "source_fingerprint": content_fingerprint(script["content"]),
                        "content": result["script"],
                    },
                ]
            repository.save_script_optimization_episode(
                optimization_id, int(episode_number), episode_drafts
            )
            emit(
                job, "progress", f"已保存 {index}/{total} 集优化草稿",
                payload={"completed": index, "total": total, "current_episode": current_episode},
            )
        completed = repository.finish_script_optimization(optimization_id)
    except Exception as exc:
        repository.fail_script_optimization(optimization_id, str(exc))
        raise
    target = Path("data") / "script_optimizations" / job.job_id / "drafts.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(completed, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(job, "artifact", "剧本优化草稿", payload={"path": str(target), "artifact_type": "json"})
    emit(
        job,
        "job_finished",
        "剧本优化草稿生成完成",
        payload=_usage_payload(job, {"optimization_id": optimization_id}),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate screenplay optimization drafts.")
    parser.add_argument("--job-file", required=True)
    job = CrawlJob.load(parser.parse_args().job_file)
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
