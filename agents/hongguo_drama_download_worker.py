# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from agents.hongguo_capture_downloader import (
    CaptureDeadline,
    CaptureDownloadError,
    CapturePaidError,
    CaptureTimeoutError,
    CapturedMediaDownloader,
)
from agents.hongguo_drama import HongguoDramaClient, HongguoDramaError, load_settings, safe_filename
from agents.drama_providers import ProviderFailure
from agents.hongguo_third_party_downloader import download_third_party_episode
from agents.hongguo_video_capture_client import (
    HongguoVideoCaptureClient,
    HongguoVideoCaptureServiceError,
    completed_capture_artifact,
    safe_capture_service_error,
)
from core.events import emit_event
from core.job import CrawlJob


def emit(
    job: CrawlJob,
    event: str,
    message: str,
    level: str = "info",
    payload: dict[str, Any] | None = None,
) -> None:
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
    **extra: Any,
) -> dict[str, Any]:
    return {
        "completed": completed,
        "current": current,
        "total": total,
        "percent": round(((completed + max(0.0, min(1.0, fraction))) * 100 / total), 1),
        "downloaded_bytes": downloaded_bytes,
        "total_bytes": total_bytes,
        **counts,
        **extra,
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_capture_metadata(capture: dict[str, Any]) -> dict[str, Any]:
    return {
        key: capture[key]
        for key in ("definition", "codec", "encrypted", "source_file")
        if key in capture
    }


def _run_third_party_download(
    job: CrawlJob,
    *,
    title: str,
    series_ref: str,
    provider_id: str,
    requested: list[int],
    all_episodes: bool,
    target_root: Path,
    pause_path: Path,
    client: HongguoDramaClient,
) -> None:
    episodes = client.episodes(series_ref, provider_id)
    selected = (
        episodes
        if all_episodes
        else [item for item in episodes if item.get("episode") in requested]
    )
    if not selected:
        raise HongguoDramaError("没有选择有效集数")
    provider = client.provider(provider_id)
    total = len(selected)
    counts = {"downloaded": 0, "skipped_paid": 0, "failed": 0}
    manifest_path = target_root / "manifest.json"
    manifest: dict[str, Any] = {
        "source_platform": provider_id,
        "provider_id": provider_id,
        "title": title,
        "series_ref": series_ref,
        "book_id": series_ref,
        "items": [],
    }
    emit(job, "log", f"开始通过 {provider_id} 下载《{title}》，共 {total} 集")
    emit(
        job,
        "progress",
        f"任务已创建，准备下载 {total} 集",
        payload=_progress_payload(0, 0, total, 0, counts),
    )
    for completed, episode in enumerate(selected, start=1):
        _wait_if_paused(job, pause_path)
        number = int(episode.get("episode") or completed)
        previous = completed - 1
        episode_ref = str(
            episode.get("episode_ref") or episode.get("video_id") or ""
        ).strip()
        record: dict[str, Any] = {
            "episode": number,
            "episode_ref": episode_ref,
            "title": str(episode.get("title") or ""),
            "provider_id": provider_id,
            "status": "running",
        }
        paid_reason = str(episode.get("paid_reason") or "")
        if episode.get("is_paid") or paid_reason:
            record.update(
                {
                    "status": "skipped_paid",
                    "reason": paid_reason or "接口明确标记该集需要付费",
                }
            )
            manifest["items"].append(record)
            counts["skipped_paid"] += 1
            _write_manifest(manifest_path, manifest)
            continue
        if not episode_ref:
            record.update({"status": "failed", "reason": "目标集缺少剧集标识"})
            manifest["items"].append(record)
            counts["failed"] += 1
            _write_manifest(manifest_path, manifest)
            continue
        target_dir = target_root / f"episode_{number:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        video_path = target_dir / "video.mp4"
        last_progress_at = 0.0

        def report_download(downloaded_bytes: int, total_bytes: int) -> None:
            nonlocal last_progress_at
            now = time.monotonic()
            if downloaded_bytes < total_bytes and now - last_progress_at < 0.5:
                return
            last_progress_at = now
            fraction = (
                0.12 + 0.78 * (downloaded_bytes / total_bytes)
                if total_bytes
                else 0.12
            )
            emit(
                job,
                "progress",
                f"第 {number} 集：正在通过 {provider_id} 下载",
                payload=_progress_payload(
                    previous,
                    completed,
                    total,
                    fraction,
                    counts,
                    downloaded_bytes,
                    total_bytes,
                    stage="downloading",
                    provider_id=provider_id,
                ),
            )

        try:
            result = download_third_party_episode(
                provider,
                series_ref,
                episode_ref,
                video_path,
                on_progress=report_download,
                wait_if_paused=lambda: _wait_if_paused(job, pause_path),
            )
        except ProviderFailure as exc:
            if exc.code == "paid_content":
                record.update({"status": "skipped_paid", "reason": exc.safe_message})
                counts["skipped_paid"] += 1
            else:
                record.update(
                    {
                        "status": "failed",
                        "error_code": exc.code,
                        "reason": exc.safe_message,
                    }
                )
                counts["failed"] += 1
            manifest["items"].append(record)
            _write_manifest(manifest_path, manifest)
            continue
        source_payload = {
            "title": title,
            "source_platform": provider_id,
            "provider_id": provider_id,
            "series_ref": series_ref,
            "episode": {
                "episode": number,
                "episode_ref": episode_ref,
                "title": record["title"],
            },
            "status": "succeeded",
        }
        source_path = target_dir / "source.json"
        source_path.write_text(
            json.dumps(source_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifact(job, video_path, "video")
        artifact(job, source_path, "json")
        record.update(
            {"status": "succeeded", "bytes": result.bytes, "path": str(video_path)}
        )
        manifest["items"].append(record)
        counts["downloaded"] += 1
        _write_manifest(manifest_path, manifest)
        emit(
            job,
            "progress",
            f"第 {number} 集下载完成（{result.bytes / 1024 / 1024:.1f} MB）",
            payload=_progress_payload(
                completed,
                completed,
                total,
                0,
                counts,
                result.bytes,
                result.bytes,
                provider_id=provider_id,
            ),
        )
    pause_path.unlink(missing_ok=True)
    _write_manifest(manifest_path, manifest)
    artifact(job, manifest_path, "json")
    if counts["failed"]:
        raise HongguoDramaError(
            f"《{title}》第三方下载部分失败：成功 {counts['downloaded']} 集，失败 {counts['failed']} 集，付费跳过 {counts['skipped_paid']} 集"
        )
    emit(
        job,
        "job_finished",
        f"《{title}》下载完成：成功 {counts['downloaded']} 集，付费跳过 {counts['skipped_paid']} 集",
        payload={"completed": total, "total": total, "percent": 100, **counts},
    )


def run(job: CrawlJob) -> None:
    extra = job.extra or {}
    title = str(extra.get("title") or "未命名剧目").strip()
    series_id = str(extra.get("book_id") or "").strip()
    provider_id = str(
        extra.get("provider_id") or extra.get("source_platform") or "hongguo_capture"
    ).strip().lower() or "hongguo_capture"
    requested = [int(value) for value in (extra.get("episodes") or []) if str(value).isdigit()]
    if not title or not series_id:
        raise HongguoDramaError("下载任务缺少剧名或 series_id")

    target_root = Path("data") / "hg" / "jobs" / job.job_id / "dramas" / safe_filename(title)
    target_root.mkdir(parents=True, exist_ok=True)
    pause_path = _pause_path(job)
    settings = load_settings()
    client = HongguoDramaClient(settings)
    if provider_id != "hongguo_capture":
        _run_third_party_download(
            job,
            title=title,
            series_ref=series_id,
            provider_id=provider_id,
            requested=requested,
            all_episodes=bool(extra.get("all_episodes")),
            target_root=target_root,
            pause_path=pause_path,
            client=client,
        )
        return
    client.refresh_interfaces()
    episodes = client.episodes(series_id)
    series_title = getattr(getattr(client, "catalog", None), "series_title", None)
    capture_title = series_title(series_id) if callable(series_title) else title
    capture_title = capture_title or title
    selected = episodes if extra.get("all_episodes") else [item for item in episodes if item.get("episode") in requested]
    if not selected:
        raise HongguoDramaError("没有选择有效集数")

    downloader = CapturedMediaDownloader(
        client.catalog,
        wait_timeout_seconds=int(settings["wait_timeout_seconds"]),
    )
    capture_service = HongguoVideoCaptureClient(
        str(settings.get("capture_service_url") or "http://127.0.0.1:8776")
    )
    total = len(selected)
    manifest_path = target_root / "manifest.json"
    manifest: dict[str, Any] = {
        "source_platform": "hongguo_capture",
        "title": title,
        "series_id": series_id,
        "items": [],
    }
    counts = {"downloaded": 0, "skipped_paid": 0, "failed": 0}
    job_deadline = CaptureDeadline(
        time.monotonic() + int(settings["wait_timeout_seconds"])
    )
    emit(job, "log", f"开始抓包下载《{title}》，共 {total} 集")
    emit(job, "progress", f"任务已创建，准备下载 {total} 集", payload=_progress_payload(0, 0, total, 0, counts))

    for completed, episode in enumerate(selected, start=1):
        pause_started = time.monotonic()
        _wait_if_paused(job, pause_path)
        job_deadline.expires_at += max(0, time.monotonic() - pause_started)
        number = int(episode.get("episode") or completed)
        previous = completed - 1
        record: dict[str, Any] = {
            "episode": number,
            "vid": str(episode.get("video_id") or ""),
            "title": str(episode.get("title") or ""),
            "status": "waiting_capture",
        }
        paid_reason = str(episode.get("paid_reason") or "")
        if episode.get("is_paid") or paid_reason:
            record.update({"status": "skipped_paid", "reason": paid_reason or "抓包数据明确标记该集需要付费"})
            manifest["items"].append(record)
            counts["skipped_paid"] += 1
            _write_manifest(manifest_path, manifest)
            emit(job, "log", f"第 {number} 集需要付费，已跳过：{record['reason']}", "warning")
            emit(job, "progress", f"第 {number} 集已跳过", payload=_progress_payload(completed, completed, total, 0, counts))
            continue

        target_dir = target_root / f"episode_{number:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        video_path = target_dir / "video.mp4"
        last_progress_at = 0.0
        capture_job_id = ""

        def report_wait(remaining: int) -> dict[str, Any] | None:
            capture_status = ""
            if capture_job_id:
                remote_job = capture_service.get_job(capture_job_id)
                capture_status = str(remote_job.get("status") or "")
                if capture_status == "failed":
                    raise HongguoVideoCaptureServiceError(
                        safe_capture_service_error(
                            remote_job.get("error"), job_failure=True
                        )
                    )
                service_file = completed_capture_artifact(
                    remote_job, number, settings["capture_root"]
                )
                if service_file is not None:
                    emit(
                        job,
                        "log",
                        f"第 {number} 集抓包服务已完成下载，正在接管校验后的文件",
                    )
                    return downloader.adopt_local_artifact(
                        service_file, video_path
                    )
            emit(
                job,
                "progress",
                f"第 {number} 集：抓包服务已发起，等待新抓包（剩余 {remaining} 秒）",
                payload=_progress_payload(previous, completed, total, 0.05, counts, stage="waiting_capture", waiting_seconds=remaining, capture_job_status=capture_status),
            )
            return None

        def report_download(downloaded_bytes: int, total_bytes: int) -> None:
            nonlocal last_progress_at
            now = time.monotonic()
            if downloaded_bytes < total_bytes and now - last_progress_at < 0.5:
                return
            last_progress_at = now
            fraction = 0.12 + 0.78 * (downloaded_bytes / total_bytes) if total_bytes else 0.12
            emit(
                job,
                "progress",
                f"第 {number} 集：正在下载抓包视频",
                payload=_progress_payload(previous, completed, total, fraction, counts, downloaded_bytes, total_bytes, stage="downloading"),
            )

        try:
            initial_excluded = client.catalog.variant_identities(series_id, number)
            emit(
                job,
                "progress",
                f"第 {number} 集：正在请求视频抓包服务",
                payload=_progress_payload(
                    previous,
                    completed,
                    total,
                    0.02,
                    counts,
                    stage="requesting_capture",
                ),
            )
            remote_job = capture_service.start_capture(
                capture_title, series_id, number
            )
            capture_job_id = str(remote_job.get("job_id") or "")
            emit(job, "log", f"第 {number} 集视频抓包任务已提交")
            capture = downloader.download_episode(
                series_id,
                number,
                video_path,
                on_wait=report_wait,
                on_progress=report_download,
                on_variant_failed=lambda reason: emit(
                    job,
                    "log",
                    f"第 {number} 集当前抓包下载失败，继续等待新抓包：{reason}",
                    "warning",
                ),
                wait_if_paused=lambda: _wait_if_paused(job, pause_path),
                deadline=job_deadline,
                initial_excluded=initial_excluded,
            )
        except CapturePaidError as exc:
            record.update({"status": "skipped_paid", "reason": str(exc)})
            manifest["items"].append(record)
            counts["skipped_paid"] += 1
            _write_manifest(manifest_path, manifest)
            emit(job, "log", f"第 {number} 集需要付费，已跳过：{exc}", "warning")
            emit(job, "progress", f"第 {number} 集已跳过", payload=_progress_payload(completed, completed, total, 0, counts))
            continue
        except (
            CaptureTimeoutError,
            CaptureDownloadError,
            HongguoVideoCaptureServiceError,
        ) as exc:
            status = (
                "capture_timeout"
                if isinstance(exc, CaptureTimeoutError)
                else "capture_request_failed"
                if isinstance(exc, HongguoVideoCaptureServiceError)
                else "download_failed"
            )
            record.update({"status": status, "reason": str(exc)})
            manifest["items"].append(record)
            counts["failed"] += 1
            _write_manifest(manifest_path, manifest)
            emit(job, "log", f"第 {number} 集下载失败：{exc}", "error")
            emit(job, "progress", f"第 {number} 集下载失败", payload=_progress_payload(completed, completed, total, 0, counts))
            continue

        source_payload = {
            "title": title,
            "series_id": series_id,
            "source_platform": "hongguo_capture",
            "episode": {
                "episode": number,
                "vid": record["vid"],
                "title": record["title"],
                "capture_status": episode.get("capture_status") or "waiting_capture",
            },
            "capture": _safe_capture_metadata(capture),
        }
        source_path = target_dir / "source.json"
        source_path.write_text(json.dumps(source_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact(job, video_path, "video")
        artifact(job, source_path, "json")
        record.update(
            {
                "status": "succeeded",
                "capture": _safe_capture_metadata(capture),
            }
        )
        manifest["items"].append(record)
        counts["downloaded"] += 1
        _write_manifest(manifest_path, manifest)
        emit(job, "progress", f"第 {number} 集下载完成（{capture['bytes'] / 1024 / 1024:.1f} MB）", payload=_progress_payload(completed, completed, total, 0, counts, capture["bytes"], capture["bytes"]))

    pause_path.unlink(missing_ok=True)
    _write_manifest(manifest_path, manifest)
    artifact(job, manifest_path, "json")
    if counts["failed"]:
        failed_items = [
            item
            for item in manifest["items"]
            if item.get("status")
            in {"capture_timeout", "capture_request_failed", "download_failed"}
        ]
        failure_details = "；".join(
            f"第 {item['episode']} 集：{item.get('reason') or item['status']}"
            for item in failed_items[:3]
        )
        if len(failed_items) > 3:
            failure_details += f"；另有 {len(failed_items) - 3} 集失败，详见 manifest.json"
        raise HongguoDramaError(
            f"《{title}》抓包下载部分失败：成功 {counts['downloaded']} 集，失败 {counts['failed']} 集，付费跳过 {counts['skipped_paid']} 集；{failure_details}"
        )
    message = (
        f"《{title}》所选集数均需付费，已全部跳过"
        if counts["downloaded"] == 0
        else f"《{title}》下载完成：成功 {counts['downloaded']} 集，付费跳过 {counts['skipped_paid']} 集"
    )
    emit(job, "job_finished", message, payload={"completed": total, "total": total, "percent": 100, **counts})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download selected Hongguo drama episodes from captured traffic.")
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
