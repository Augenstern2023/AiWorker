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
import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.douyin_drama_fallback import (
    DouyinDramaFallbackClient,
    DouyinFallbackError,
    normalize_search_candidates,
)
from core.events import emit_event
from core.job import CrawlJob
from media_platform.douyin.client import request_keyword_var
from media_platform.douyin.field import PublishTimeType, SearchChannelType, SearchSortType


CSV_COLUMNS = ("评论ID", "用户名", "剧评内容", "评论时间", "点赞数")


def _emit(job: CrawlJob, event: str, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    emit_event(event, job.job_id, "douyin-review-worker", "dy", level, message, payload or {})


def _created_at(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def write_review_csv(path: Path, comments: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for comment in comments:
            user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
            writer.writerow({
                "评论ID": str(comment.get("cid") or comment.get("comment_id") or ""),
                "用户名": str(user.get("nickname") or comment.get("nickname") or ""),
                "剧评内容": str(comment.get("text") or ""),
                "评论时间": _created_at(comment.get("create_time")),
                "点赞数": str(comment.get("digg_count") or 0),
            })
            written += 1
    return written


async def _capture_comments(job: CrawlJob) -> tuple[str, list[dict[str, Any]], int]:
    extra = job.extra or {}
    title = str(extra.get("title") or "").strip()
    aweme_id = str(extra.get("aweme_id") or "").strip()
    all_reviews = bool(extra.get("all_reviews"))
    limit = int(extra.get("limit") or 100)
    if not title:
        raise DouyinFallbackError("抖音剧评任务缺少剧名")
    if not all_reviews and not 1 <= limit <= 10_000:
        raise DouyinFallbackError("剧评数量需为 1 到 10000")

    client = DouyinDramaFallbackClient(background=True)
    crawler, playwright = await client._client()
    keyword_token = request_keyword_var.set(title)
    try:
        if not aweme_id:
            response = await crawler.dy_client.search_info_by_keyword(
                keyword=title,
                search_channel=SearchChannelType.VIDEO,
                sort_type=SearchSortType.GENERAL,
                publish_time=PublishTimeType.UNLIMITED,
            )
            candidates = normalize_search_candidates(response, title)
            selected = next((item for item in candidates if item.get("is_primary")), None)
            aweme_id = str((selected or {}).get("aweme_id") or "").strip()
        if not aweme_id:
            raise DouyinFallbackError("未找到可读取评论的抖音视频")

        comments: list[dict[str, Any]] = []
        cursor = 0
        source_total = 0
        seen_cursors: set[str] = set()
        _emit(job, "progress", "正在拉取抖音评论", payload={"review_count": 0, "source_total": 0})
        while True:
            page = await crawler.dy_client.get_aweme_comments(aweme_id, cursor)
            batch = page.get("comments") if isinstance(page.get("comments"), list) else []
            if not source_total:
                try:
                    source_total = int(page.get("total") or 0)
                except (TypeError, ValueError):
                    source_total = 0
            if not all_reviews and len(comments) + len(batch) > limit:
                batch = batch[: limit - len(comments)]
            comments.extend(item for item in batch if isinstance(item, dict))
            _emit(
                job,
                "progress",
                f"已获取 {len(comments)} 条抖音评论",
                payload={"review_count": len(comments), "source_total": source_total, "aweme_id": aweme_id},
            )
            if (not all_reviews and len(comments) >= limit) or not page.get("has_more"):
                break
            next_cursor = page.get("cursor")
            cursor_key = str(next_cursor)
            if not batch or not cursor_key or cursor_key in seen_cursors:
                break
            seen_cursors.add(cursor_key)
            cursor = next_cursor
            await asyncio.sleep(0.5)
        return aweme_id, comments, source_total
    finally:
        request_keyword_var.reset(keyword_token)
        await client._close(crawler, playwright)


def run(job: CrawlJob) -> None:
    output_path = Path(str((job.extra or {}).get("output_path") or ""))
    if not output_path:
        raise DouyinFallbackError("抖音剧评任务缺少导出路径")
    aweme_id, comments, source_total = asyncio.run(_capture_comments(job))
    review_count = write_review_csv(output_path, comments)
    _emit(job, "artifact", output_path.name, payload={"path": str(output_path), "artifact_type": "csv"})
    _emit(
        job,
        "job_finished",
        f"抖音剧评下载完成，共 {review_count} 条",
        payload={
            "output_path": str(output_path),
            "review_count": review_count,
            "source_total": source_total,
            "aweme_id": aweme_id,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Douyin drama comments.")
    parser.add_argument("--job-file", required=True)
    job = CrawlJob.load(parser.parse_args().job_file)
    try:
        run(job)
    except Exception as exc:
        _emit(job, "job_failed", str(exc), "error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
