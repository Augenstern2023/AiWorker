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
import json
import time
from pathlib import Path
from typing import Any

from agents.hongguo_drama import HongguoDramaClient, HongguoDramaError, HongguoPaidContentError, load_settings, media_extension, safe_filename
from core.events import emit_event
from core.job import CrawlJob


def emit(job: CrawlJob, event: str, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    emit_event(event, job.job_id, "hongguo-drama-download-worker", "hg", level, message, payload or {})


def artifact(job: CrawlJob, path: Path, artifact_type: str) -> None:
    emit(job, "artifact", path.name, payload={"path": str(path), "artifact_type": artifact_type})


def _pause_path(job: CrawlJob) -> Path:
    return Path(str((job.extra or {}).get("pause_path") or Path(".runtime") / "jobs" / f"{job.job_id}.pause"))


def _wait_if_paused(job: CrawlJob, pause_path: Path) -> None:
    announced = False
    while pause_path.exists():
        if not announced:
            emit(job, "log", "下载已暂停，点击继续后恢复", "warning")
            announced = True
        time.sleep(0.5)


def _progress_payload(
    completed: int,
    current: int,
    total: int,
    fraction: float,
    counts: dict[str, int],
    downloaded_bytes: int = 0,
    total_bytes: int = 0,
) -> dict[str, int | float]:
    return {
        "completed": completed,
        "current": current,
        "total": total,
        "percent": round(((completed + max(0.0, min(1.0, fraction))) * 100 / total), 1),
        "downloaded_bytes": downloaded_bytes,
        "total_bytes": total_bytes,
        **counts,
    }


def _play_url(client: HongguoDramaClient, video_id: str, book_id: str) -> str:
    try:
        return client.play_url(video_id, book_id=book_id)
    except TypeError:
        # Backward-compatible with test doubles and legacy client implementations.
        return client.play_url(video_id)


def run(job: CrawlJob) -> None:
    extra = job.extra or {}
    title = str(extra.get("title") or "未命名剧目").strip()
    book_id = str(extra.get("book_id") or "").strip()
    source_platform = str(extra.get("source_platform") or "hg").strip().lower() or "hg"
    requested = [int(value) for value in (extra.get("episodes") or []) if str(value).isdigit()]
    if not title or not book_id:
        raise HongguoDramaError("下载任务缺少剧名或 book_id")

    target_root = Path("data") / "hg" / "jobs" / job.job_id / "dramas" / safe_filename(title)
    target_root.mkdir(parents=True, exist_ok=True)
    pause_path = _pause_path(job)
    client = HongguoDramaClient(load_settings())
    client.default_source_platform = source_platform
    client.refresh_interfaces()
    episodes = client.episodes(book_id)
    selected = episodes if extra.get("all_episodes") else [item for item in episodes if item.get("episode") in requested]
    if not selected:
        raise HongguoDramaError("没有选择有效集数")

    total = len(selected)
    manifest: dict[str, Any] = {"source_platform": source_platform, "title": title, "book_id": book_id, "items": []}
    counts = {"downloaded": 0, "skipped_paid": 0}
    emit(job, "log", f"开始下载《{title}》，共 {total} 集")
    emit(job, "progress", f"任务已创建，准备下载 {total} 集", payload=_progress_payload(0, 0, total, 0, counts))
    for completed, episode in enumerate(selected, start=1):
        _wait_if_paused(job, pause_path)
        number = int(episode.get("episode") or completed)
        previous = completed - 1
        record: dict[str, Any] = {"episode": number, "video_id": str(episode.get("video_id") or ""), "title": str(episode.get("title") or ""), "status": "running"}
        paid_reason = str(episode.get("paid_reason") or "")
        emit(job, "progress", f"第 {number} 集：正在获取播放地址", payload=_progress_payload(previous, completed, total, 0.05, counts))
        if episode.get("is_paid") or paid_reason:
            record.update({"status": "skipped_paid", "reason": paid_reason or "接口明确标记该集需要付费"})
            manifest["items"].append(record)
            counts["skipped_paid"] += 1
            emit(job, "log", f"第 {number} 集需要付费，已跳过：{record['reason']}", "warning")
            manifest_path = target_root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            emit(job, "progress", f"第 {number} 集已跳过", payload=_progress_payload(completed, completed, total, 0, counts))
            continue
        target_dir = target_root / f"episode_{number:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        video_id = str(episode.get("video_id") or "")
        if not video_id:
            raise HongguoDramaError(f"第 {number} 集缺少 video_id")
        emit(job, "log", f"正在下载第 {number}/{len(episodes)} 集")
        try:
            video_url = _play_url(client, video_id, book_id)
        except HongguoPaidContentError as exc:
            record.update({"status": "skipped_paid", "reason": str(exc)})
            manifest["items"].append(record)
            counts["skipped_paid"] += 1
            emit(job, "log", f"第 {number} 集需要付费，已跳过：{exc}", "warning")
            manifest_path = target_root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            emit(job, "progress", f"第 {number} 集已跳过", payload=_progress_payload(completed, completed, total, 0, counts))
            continue
        video_path = target_dir / f"video{media_extension(video_url)}"
        last_progress_at = 0.0

        def report_download_progress(downloaded_bytes: int, total_bytes: int) -> None:
            nonlocal last_progress_at
            now = time.monotonic()
            if downloaded_bytes < total_bytes and now - last_progress_at < 0.5:
                return
            last_progress_at = now
            fraction = 0.12 + 0.78 * (downloaded_bytes / total_bytes) if total_bytes else 0.12
            emit(
                job,
                "progress",
                f"第 {number} 集：正在下载视频",
                payload=_progress_payload(previous, completed, total, fraction, counts, downloaded_bytes, total_bytes),
            )

        try:
            size = client.download_video(video_url, video_path, on_progress=report_download_progress, wait_if_paused=lambda: _wait_if_paused(job, pause_path))
        except HongguoDramaError:
            emit(job, "log", f"第 {number} 集地址失效，正在刷新后重试", "warning")
            try:
                video_url = _play_url(client, video_id, book_id)
            except HongguoPaidContentError as exc:
                record.update({"status": "skipped_paid", "reason": str(exc)})
                manifest["items"].append(record)
                counts["skipped_paid"] += 1
                emit(job, "log", f"第 {number} 集刷新后确认需要付费，已跳过：{exc}", "warning")
                manifest_path = target_root / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                emit(job, "progress", f"第 {number} 集已跳过", payload=_progress_payload(completed, completed, total, 0, counts))
                continue
            size = client.download_video(video_url, video_path, on_progress=report_download_progress, wait_if_paused=lambda: _wait_if_paused(job, pause_path))
        source_path = target_dir / "source.json"
        source_path.write_text(json.dumps({"title": title, "book_id": book_id, "source_platform": source_platform, "episode": episode}, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact(job, video_path, "video")
        artifact(job, source_path, "json")
        record.update({"status": "succeeded", "bytes": size, "path": str(video_path)})
        manifest["items"].append(record)
        counts["downloaded"] += 1
        manifest_path = target_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(job, "progress", f"第 {number} 集下载完成（{size / 1024 / 1024:.1f} MB）", payload=_progress_payload(completed, completed, total, 0, counts, size, size))

    pause_path.unlink(missing_ok=True)
    manifest_path = target_root / "manifest.json"
    artifact(job, manifest_path, "json")
    if counts["downloaded"] == 0:
        message = f"《{title}》所选集数均需付费，已全部跳过"
    else:
        message = f"《{title}》下载完成：成功 {counts['downloaded']} 集，付费跳过 {counts['skipped_paid']} 集"
    emit(job, "job_finished", message, payload={"completed": total, "total": total, "percent": 100, **counts})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download selected Hongguo drama episodes.")
    parser.add_argument("--job-file", required=True)
    return parser.parse_args()


def main() -> int:
    job = CrawlJob.load(parse_args().job_file)
    try:
        run(job)
    except Exception as exc:
        emit(job, "job_failed", str(exc), "error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
