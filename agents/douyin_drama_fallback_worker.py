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
import time
from pathlib import Path
from typing import Any

from agents.douyin_drama_fallback import DouyinDramaFallbackClient, DouyinFallbackError, DouyinPaidContentError, parse_episode_metadata
from agents.hongguo_drama import HongguoDramaClient, HongguoDramaError, media_extension, safe_filename
from agents.hongguo_drama_worker import create_asr_provider
from analysis.asr import convert_audio_to_wav
from config import bilibili_config
from core.events import emit_event
from core.job import CrawlJob


def emit(job: CrawlJob, event: str, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    emit_event(event, job.job_id, "douyin-fallback-drama-worker", "hg", level, message, payload or {})


def artifact(job: CrawlJob, path: Path, artifact_type: str) -> None:
    emit(job, "artifact", path.name, payload={"path": str(path), "artifact_type": artifact_type})


def _subtitle_url(detail: dict[str, Any]) -> str:
    for key in ("subtitle_url", "caption_url", "srt_url", "vtt_url"):
        value = str(detail.get(key) or "").strip()
        if value:
            return value
    video = detail.get("video") if isinstance(detail.get("video"), dict) else {}
    for key in ("subtitle_url", "caption_url", "srt_url", "vtt_url"):
        value = str(video.get(key) or "").strip()
        if value:
            return value
    return ""


def _pause_path(job: CrawlJob) -> Path:
    return Path(str((job.extra or {}).get("pause_path") or Path(".runtime") / "jobs" / f"{job.job_id}.pause"))


def _wait_if_paused(job: CrawlJob, pause_path: Path) -> None:
    announced = False
    while pause_path.exists():
        if not announced:
            emit(job, "log", "下载已暂停，点击继续后恢复", "warning")
            announced = True
        time.sleep(0.5)


def _selected_aweme_ids(extra: dict[str, Any]) -> list[str]:
    values = list(extra.get("aweme_ids") or [])
    if extra.get("aweme_id"):
        values.append(extra["aweme_id"])
    result: list[str] = []
    for value in values:
        aweme_id = str(value or "").strip()
        if aweme_id and aweme_id not in result:
            result.append(aweme_id)
    return result


def _stage_progress(
    job: CrawlJob,
    index: int,
    total: int,
    fraction: float,
    message: str,
    counts: dict[str, int],
    downloaded_bytes: int = 0,
    total_bytes: int = 0,
) -> None:
    percent = round(((index - 1) + max(0.0, min(1.0, fraction))) * 100 / total, 1)
    emit(job, "progress", message, payload={
        "completed": index - 1,
        "current": index,
        "total": total,
        "percent": percent,
        "downloaded_bytes": downloaded_bytes,
        "total_bytes": total_bytes,
        **counts,
    })


def _render_douyin_script(video_title: str, episode_label: str, transcript_source: str, transcript: str) -> str:
    coverage = episode_label or "未识别，当前产物对应一个抖音视频"
    return "\n".join([
        f"# {video_title}",
        "",
        "- 来源：抖音降级",
        f"- 覆盖集数：{coverage}",
        f"- 转写来源：{transcript_source}",
        "- 说明：以下内容为字幕/语音转写稿，不是官方剧本。",
        "",
        "## 转写内容",
        "",
        transcript or "_未识别到文本_",
        "",
    ])


def run(job: CrawlJob) -> None:
    extra = job.extra or {}
    title = str(extra.get("title") or "未命名剧目").strip()
    aweme_ids = _selected_aweme_ids(extra)
    fallback_reason = str(extra.get("fallback_reason") or "红果接口不可用或未找到匹配剧目").strip()
    if not aweme_ids:
        raise DouyinFallbackError("抖音降级任务缺少 aweme_id")

    target_root = Path("data") / "hg" / "jobs" / job.job_id / "dramas" / safe_filename(title) / "douyin_fallback"
    target_root.mkdir(parents=True, exist_ok=True)
    pause_path = _pause_path(job)
    downloader = HongguoDramaClient({})
    manifest: dict[str, Any] = {
        "source_platform": "dy",
        "source": "douyin_fallback",
        "title": title,
        "fallback_reason": fallback_reason,
        "items": [],
    }
    counts = {"downloaded": 0, "skipped_paid": 0, "failed": 0}
    total = len(aweme_ids)
    emit(job, "log", f"红果检索已降级至抖音，开始处理 {total} 个已选视频", "warning")
    emit(job, "progress", f"任务已创建，准备处理 {total} 个视频", payload={"completed": 0, "current": 0, "total": total, "percent": 0, **counts})

    for index, aweme_id in enumerate(aweme_ids, start=1):
        _wait_if_paused(job, pause_path)
        item_dir = target_root / f"item_{index:03d}_{safe_filename(aweme_id, 40)}"
        item_dir.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "index": index,
            "aweme_id": aweme_id,
            "source_url": f"https://www.douyin.com/video/{aweme_id}",
            "status": "running",
        }
        emit(job, "log", f"正在处理抖音视频 {index}/{total}：{aweme_id}")
        try:
            _stage_progress(job, index, total, 0.05, f"视频 {index}/{total}：正在读取登录态", counts)
            detail, video_url = asyncio.run(DouyinDramaFallbackClient(background=True).get_video(aweme_id))
            video_title = str(detail.get("desc") or title).strip() or title
            episode_metadata = parse_episode_metadata(video_title)
            record.update({"title": video_title, **episode_metadata})
            _stage_progress(job, index, total, 0.2, f"视频 {index}/{total}：正在获取视频信息", counts)
            video_path = item_dir / f"video{media_extension(video_url)}"
            _stage_progress(job, index, total, 0.3, f"视频 {index}/{total}：正在下载视频，较大文件需要等待", counts)
            last_progress_at = 0.0

            def report_download_progress(downloaded_bytes: int, total_bytes: int) -> None:
                nonlocal last_progress_at
                now = time.monotonic()
                if downloaded_bytes < total_bytes and now - last_progress_at < 0.5:
                    return
                last_progress_at = now
                fraction = 0.3 + 0.35 * (downloaded_bytes / total_bytes) if total_bytes else 0.3
                _stage_progress(
                    job,
                    index,
                    total,
                    fraction,
                    f"视频 {index}/{total}：正在下载视频",
                    counts,
                    downloaded_bytes,
                    total_bytes,
                )

            try:
                size = downloader.download_video(video_url, video_path, on_progress=report_download_progress, wait_if_paused=lambda: _wait_if_paused(job, pause_path))
            except HongguoDramaError:
                emit(job, "log", f"视频 {index} 地址失效，正在刷新后重试", "warning")
                detail, video_url = asyncio.run(DouyinDramaFallbackClient(background=True).get_video(aweme_id))
                video_path = item_dir / f"video{media_extension(video_url)}"
                size = downloader.download_video(video_url, video_path, on_progress=report_download_progress, wait_if_paused=lambda: _wait_if_paused(job, pause_path))
            artifact(job, video_path, "video")
            _stage_progress(job, index, total, 0.65, f"视频 {index}/{total}：下载完成，正在准备解析", counts)

            source_payload = {
                "source_platform": "dy",
                "source": "douyin_fallback",
                "fallback_reason": fallback_reason,
                "title": title,
                "video_title": video_title,
                "aweme_id": aweme_id,
                "source_url": record["source_url"],
                "episode_metadata": episode_metadata,
                "detail": detail,
            }
            source_path = item_dir / "source.json"
            source_path.write_text(json.dumps(source_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            artifact(job, source_path, "json")

            _wait_if_paused(job, pause_path)
            subtitle_text = downloader.fetch_subtitle_text(_subtitle_url(detail))
            transcript_source = "subtitle" if subtitle_text else "asr"
            transcript_payload: dict[str, Any] = {"source": transcript_source, "text": subtitle_text, "segments": []}
            audio_path = item_dir / "audio.wav"
            _stage_progress(job, index, total, 0.72, f"视频 {index}/{total}：正在提取音频", counts)
            asyncio.run(convert_audio_to_wav(video_path, audio_path, getattr(bilibili_config, "BILI_FFMPEG_PATH", "imageio-ffmpeg")))
            artifact(job, audio_path, "audio")
            if not subtitle_text:
                _wait_if_paused(job, pause_path)
                _stage_progress(job, index, total, 0.82, f"视频 {index}/{total}：正在进行语音转写", counts)
                transcript_payload = asyncio.run(create_asr_provider().transcribe(str(audio_path)))
                subtitle_text = str(transcript_payload.get("text") or "").strip()
            if not subtitle_text:
                raise DouyinFallbackError("字幕/语音转写未返回文本")

            transcript_path = item_dir / "transcript.json"
            transcript_path.write_text(json.dumps(transcript_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            artifact(job, transcript_path, "json")
            script_path = item_dir / "script.md"
            script_path.write_text(_render_douyin_script(video_title, str(episode_metadata["episode_label"]), transcript_source, subtitle_text), encoding="utf-8")
            artifact(job, script_path, "md")
            record.update({"status": "succeeded", "bytes": size, "script_path": str(script_path)})
            counts["downloaded"] += 1
            emit(job, "log", f"视频 {index}/{total} 下载与解析完成（{size / 1024 / 1024:.1f} MB）")
        except DouyinPaidContentError as exc:
            record.update({"status": "skipped_paid", "reason": str(exc)})
            counts["skipped_paid"] += 1
            emit(job, "log", f"视频 {index}/{total} 需要付费，已跳过：{exc}", "warning")
        except Exception as exc:
            record.update({"status": "failed", "reason": str(exc)})
            counts["failed"] += 1
            emit(job, "log", f"视频 {index}/{total} 处理失败，继续下一条：{exc}", "error")
        manifest["items"].append(record)
        manifest_path = target_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(job, "progress", f"已处理 {index}/{total} 个视频", payload={
            "completed": index,
            "total": total,
            "percent": round(index * 100 / total, 1),
            **counts,
        })

    pause_path.unlink(missing_ok=True)
    manifest_path = target_root / "manifest.json"
    artifact(job, manifest_path, "json")
    if counts["downloaded"] == 0 and counts["failed"]:
        raise DouyinFallbackError(f"所选视频均未下载成功，失败 {counts['failed']} 条，付费跳过 {counts['skipped_paid']} 条")
    if counts["downloaded"] == 0:
        message = f"所选 {total} 个视频均需付费，已全部跳过"
    else:
        message = f"抖音批量任务完成：下载 {counts['downloaded']}，付费跳过 {counts['skipped_paid']}，失败 {counts['failed']}"
    emit(job, "job_finished", message, payload={"manifest_path": str(manifest_path), "source_platform": "dy", **counts, "completed": total, "total": total, "percent": 100})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and transcribe a confirmed Douyin fallback video.")
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
