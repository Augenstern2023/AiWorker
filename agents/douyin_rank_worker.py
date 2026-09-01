# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agents.douyin_analysis_agent import AnalyzeOptions, analyze_rank_file
from agents.rank_zentao_delivery import deliver_rank_update
from core.events import emit_event
from core.job import CrawlJob
from tools.fetch_douyin_series_rank_md import parse_args as parse_tool_args
from tools.fetch_douyin_series_rank_md import run as run_douyin_rank


def _int_extra(job: CrawlJob, key: str, default: int) -> int:
    try:
        return int(job.extra.get(key, default))
    except (TypeError, ValueError):
        return default


def _float_extra(job: CrawlJob, key: str, default: float) -> float:
    try:
        return float(job.extra.get(key, default))
    except (TypeError, ValueError):
        return default


def _bool_extra(job: CrawlJob, key: str, default: bool = False) -> bool:
    value = job.extra.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _tool_args(job: CrawlJob, output_dir: Path) -> argparse.Namespace:
    original_argv = sys.argv[:]
    sys.argv = [original_argv[0]]
    try:
        args = parse_tool_args()
    finally:
        sys.argv = original_argv
    args.limit = _int_extra(job, "DOUYIN_RANK_LIMIT", 50)
    args.comment_limit = _int_extra(job, "DOUYIN_RANK_COMMENT_LIMIT", 10)
    args.detail_timeout = _float_extra(job, "DOUYIN_RANK_DETAIL_TIMEOUT", 25.0)
    args.login_wait_seconds = _float_extra(job, "DOUYIN_RANK_LOGIN_WAIT_SECONDS", 0.0)
    args.output_dir = str(output_dir)
    args.keep_browser = _bool_extra(job, "DOUYIN_RANK_KEEP_BROWSER", False)
    args.debug = _bool_extra(job, "DOUYIN_RANK_DEBUG", False)
    return args


def _analysis_options(job: CrawlJob) -> AnalyzeOptions:
    return AnalyzeOptions(
        top_n=_int_extra(job, "DOUYIN_ANALYSIS_TOP_N", 10),
        per_category=_bool_extra(job, "DOUYIN_ANALYSIS_PER_CATEGORY", True),
        with_llm=_bool_extra(job, "DOUYIN_ANALYSIS_WITH_LLM", True),
        download_media=_bool_extra(job, "DOUYIN_ANALYSIS_DOWNLOAD_MEDIA", False),
        with_asr=_bool_extra(job, "DOUYIN_ANALYSIS_WITH_ASR", False),
    )


def _emit_analysis_artifact(job: CrawlJob, worker_id: str, path_value: str, artifact_type: str) -> None:
    path = Path(path_value)
    emit_event(
        "artifact",
        job_id=job.job_id,
        worker_id=worker_id,
        platform="dy",
        level="info",
        message=f"生成抖音榜单分析产物: {path}",
        payload={
            "path": str(path),
            "artifact_type": artifact_type,
            "size": path.stat().st_size if path.exists() else 0,
        },
    )


def _run_auto_analysis(job: CrawlJob, worker_id: str, rank_json_path: Path) -> dict[str, object] | None:
    if not _bool_extra(job, "DOUYIN_AUTO_ANALYZE_TOP10", True):
        emit_event(
            "log",
            job_id=job.job_id,
            worker_id=worker_id,
            platform="dy",
            level="info",
            message="抖音榜单 Top10 自动分析已关闭",
        )
        return None

    output_dir = Path("data") / "dy" / "jobs" / job.job_id / "analysis"
    options = _analysis_options(job)
    emit_event(
        "log",
        job_id=job.job_id,
        worker_id=worker_id,
        platform="dy",
        level="info",
        message=f"开始抖音榜单 Top{options.top_n} 自动分析",
        payload={"top_n": options.top_n, "per_category": options.per_category, "with_llm": options.with_llm},
    )
    manifest = analyze_rank_file(rank_json_path, output_dir=output_dir, options=options)
    _emit_analysis_artifact(job, worker_id, manifest["manifest"], "json")
    _emit_analysis_artifact(job, worker_id, manifest["overview"], "markdown")
    _emit_analysis_artifact(job, worker_id, manifest["overview_json"], "json")
    for item in manifest.get("items", []):
        if isinstance(item, dict):
            if item.get("json"):
                _emit_analysis_artifact(job, worker_id, item["json"], "json")
            if item.get("markdown"):
                _emit_analysis_artifact(job, worker_id, item["markdown"], "markdown")
    emit_event(
        "log",
        job_id=job.job_id,
        worker_id=worker_id,
        platform="dy",
        level="info",
        message=f"抖音榜单 Top{options.top_n} 自动分析完成，共 {len(manifest.get('items', []))} 部",
        payload={"manifest": manifest.get("manifest"), "items": len(manifest.get("items", []))},
    )
    return manifest


def run_job(job: CrawlJob) -> int:
    worker_id = "dy-rank-worker"
    output_dir = Path("data") / "dy" / "jobs" / job.job_id / "rank"
    output_dir.mkdir(parents=True, exist_ok=True)
    args = _tool_args(job, output_dir)

    emit_event(
        "job_started",
        job_id=job.job_id,
        worker_id=worker_id,
        platform="dy",
        level="info",
        message="抖音短剧/漫剧榜单抓取任务开始",
        payload={"limit": args.limit, "comment_limit": args.comment_limit},
    )
    try:
        output_path = asyncio.run(run_douyin_rank(args))
    except Exception as exc:
        emit_event(
            "job_failed",
            job_id=job.job_id,
            worker_id=worker_id,
            platform="dy",
            level="error",
            message=f"抖音短剧/漫剧榜单抓取失败: {exc}",
            payload={"error_type": type(exc).__name__},
        )
        return 1

    emit_event(
        "artifact",
        job_id=job.job_id,
        worker_id=worker_id,
        platform="dy",
        level="info",
        message=f"生成抖音榜单文件: {output_path}",
        payload={
            "path": str(output_path),
            "artifact_type": "markdown",
            "size": output_path.stat().st_size if output_path.exists() else 0,
        },
    )
    json_path = output_path.with_suffix(".json")
    if json_path.exists():
        emit_event(
            "artifact",
            job_id=job.job_id,
            worker_id=worker_id,
            platform="dy",
            level="info",
            message=f"生成抖音榜单 JSON 文件: {json_path}",
            payload={
                "path": str(json_path),
                "artifact_type": "json",
                "size": json_path.stat().st_size,
            },
        )
        try:
            _run_auto_analysis(job, worker_id, json_path)
        except Exception as exc:
            emit_event(
                "log",
                job_id=job.job_id,
                worker_id=worker_id,
                platform="dy",
                level="warning",
                message=f"抖音榜单 Top10 自动分析失败，榜单抓取结果已保留: {exc}",
                payload={"error_type": type(exc).__name__},
            )
        try:
            delivery = deliver_rank_update("dy", json_path)
            emit_event(
                "log",
                job_id=job.job_id,
                worker_id=worker_id,
                platform="dy",
                level="info",
                message=f"抖音榜单禅道推送：{delivery.get('status', 'unknown')}",
                payload={
                    "part_key": delivery.get("part_key", "douyin-rank"),
                    "status": delivery.get("status", "unknown"),
                    "reason": delivery.get("reason", ""),
                },
            )
        except Exception as exc:
            emit_event(
                "log",
                job_id=job.job_id,
                worker_id=worker_id,
                platform="dy",
                level="warning",
                message=f"抖音榜单已更新，但禅道推送失败: {exc}",
                payload={"error_type": type(exc).__name__},
            )
    else:
        emit_event(
            "log",
            job_id=job.job_id,
            worker_id=worker_id,
            platform="dy",
            level="warning",
            message=f"未找到抖音榜单 JSON，跳过 Top10 自动分析: {json_path}",
        )
    emit_event(
        "job_finished",
        job_id=job.job_id,
        worker_id=worker_id,
        platform="dy",
        level="info",
        message="抖音短剧/漫剧榜单抓取完成",
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Douyin rank JobManager worker.")
    parser.add_argument("--job-file", required=True, help="Path to CrawlJob JSON file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    job = CrawlJob.load(args.job_file)
    if job.platform != "dy" or job.crawler_type != "rank":
        emit_event(
            "job_failed",
            job_id=job.job_id,
            worker_id="dy-rank-worker",
            platform=job.platform,
            level="error",
            message=f"抖音榜单 Worker 不支持任务: {job.platform}/{job.crawler_type}",
        )
        return 2
    return run_job(job)


if __name__ == "__main__":
    raise SystemExit(main())
