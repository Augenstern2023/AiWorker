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
from screenplay_review.service import build_project_source, source_fingerprint

from .service import content_fingerprint, optimize_episode, report_findings


def create_provider() -> Any:
    return create_llm_provider_from_config(config)


def emit(job: CrawlJob, event: str, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    emit_event(event, job.job_id, "screenplay-optimization-worker", "creative", level, message, payload or {})


def _latest(project: dict[str, Any], stage: str, episode_number: int) -> dict[str, Any] | None:
    candidates = [
        item for item in project.get("episodes") or []
        if item.get("stage") == stage and int(item.get("episode_number") or 0) == episode_number
    ]
    return max(candidates, key=lambda item: (int(item.get("version") or 0), int(item.get("id") or 0)), default=None)


async def run(job: CrawlJob) -> None:
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
    generated_drafts: list[dict[str, Any]] = []
    total = len(optimization["target_episode_numbers"])
    try:
        provider = create_provider()
        for index, episode_number in enumerate(optimization["target_episode_numbers"], start=1):
            episode_context = context_by_episode.get(int(episode_number))
            if not episode_context:
                raise ValueError(f"第 {episode_number} 集场次剧本不存在")
            script = episode_context.get("script") or {}
            beats = episode_context.get("beats") or {}
            if not script:
                raise ValueError(f"第 {episode_number} 集场次剧本不存在")
            if optimization["mode"] == "structure" and not beats:
                raise ValueError(f"第 {episode_number} 集 Beats 不存在")
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
            )
            if optimization["mode"] == "structure":
                generated_drafts.append(
                    {
                        "episode_number": int(episode_number), "kind": "beats",
                        "source_episode_id": beats["id"],
                        "source_fingerprint": content_fingerprint(beats["content"]),
                        "content": result["beats"],
                    }
                )
                generated_drafts.append(
                    {
                        "episode_number": int(episode_number), "kind": "script",
                        "source_episode_id": None,
                        "source_fingerprint": content_fingerprint(result["beats"]),
                        "content": result["script"],
                    }
                )
            else:
                generated_drafts.append(
                    {
                        "episode_number": int(episode_number), "kind": "script",
                        "source_episode_id": script["id"],
                        "source_fingerprint": content_fingerprint(script["content"]),
                        "content": result["script"],
                    }
                )
            emit(job, "progress", f"已生成 {index}/{total} 集优化草稿", payload={"completed": index, "total": total})
        completed = repository.complete_script_optimization(optimization_id, generated_drafts)
    except Exception as exc:
        repository.fail_script_optimization(optimization_id, str(exc))
        raise
    target = Path("data") / "script_optimizations" / job.job_id / "drafts.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(completed, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(job, "artifact", "剧本优化草稿", payload={"path": str(target), "artifact_type": "json"})
    emit(job, "job_finished", "剧本优化草稿生成完成", payload={"optimization_id": optimization_id})


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate screenplay optimization drafts.")
    parser.add_argument("--job-file", required=True)
    job = CrawlJob.load(parser.parse_args().job_file)
    try:
        asyncio.run(run(job))
    except Exception as exc:
        emit(job, "job_failed", str(exc), "error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
