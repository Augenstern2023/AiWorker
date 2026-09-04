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

from config import bilibili_config
from agents.hongguo_capture_downloader import CapturePaidError, CapturedMediaDownloader
from agents.drama_providers import ProviderFailure
from agents.hongguo_drama import (
    HongguoDramaClient,
    HongguoDramaError,
    load_settings,
    safe_filename,
)
from agents.hongguo_video_capture_client import (
    HongguoVideoCaptureClient,
    HongguoVideoCaptureServiceError,
    completed_capture_artifact,
    safe_capture_service_error,
)
from agents.hongguo_third_party_downloader import download_third_party_episode
from analysis.asr import ASRError, OpenAICompatibleASRProvider, SenseVoiceASRProvider, convert_audio_to_wav, default_sensevoice_script_path
from core.events import emit_event
from core.job import CrawlJob


def create_asr_provider():
    provider = getattr(bilibili_config, "BILI_ASR_PROVIDER", "sensevoice")
    if provider == "openai":
        return OpenAICompatibleASRProvider(
            base_url=getattr(bilibili_config, "ASR_BASE_URL", ""),
            api_key=getattr(bilibili_config, "ASR_API_KEY", ""),
            model=getattr(bilibili_config, "ASR_MODEL", "whisper-1"),
        )
    if provider == "sensevoice":
        return SenseVoiceASRProvider(
            python_path=getattr(bilibili_config, "SENSEVOICE_PYTHON", ""),
            model_dir=getattr(bilibili_config, "SENSEVOICE_MODEL_DIR", ""),
            vad_model_dir=getattr(bilibili_config, "SENSEVOICE_VAD_MODEL_DIR", "fsmn-vad"),
            ffmpeg_dir=getattr(bilibili_config, "SENSEVOICE_FFMPEG_DIR", ""),
            device=getattr(bilibili_config, "SENSEVOICE_DEVICE", "cuda:0"),
            script_path=getattr(bilibili_config, "SENSEVOICE_SCRIPT_PATH", default_sensevoice_script_path()),
        )
    raise ASRError(f"Unsupported ASR provider: {provider}")


def emit(job: CrawlJob, event: str, message: str, level: str = "info", payload: dict | None = None) -> None:
    emit_event(event, job.job_id, "hongguo-drama-worker", "hg", level, message, payload or {})


def artifact(job: CrawlJob, path: Path, artifact_type: str) -> None:
    emit(job, "artifact", path.name, payload={"path": str(path), "artifact_type": artifact_type})


def render_script(title: str, episode: int, source: str, transcript: str) -> str:
    return "\n".join(
        [
            f"# {title} 第 {episode} 集",
            "",
            f"- 来源：{source}",
            "- 说明：以下内容为字幕/语音转写稿，不是官方剧本。",
            "",
            "## 转写内容",
            "",
            transcript or "_未识别到文本_",
            "",
        ]
    )


def _finish_transcription(
    job: CrawlJob,
    *,
    client: HongguoDramaClient,
    title: str,
    episode_number: int,
    episode: dict,
    target_dir: Path,
    video_path: Path,
) -> None:
    subtitle_text = client.fetch_subtitle_text(
        str(episode.get("subtitle_url") or "")
    )
    transcript_source = "subtitle" if subtitle_text else "asr"
    transcript_payload: dict = {
        "source": transcript_source,
        "text": subtitle_text,
        "segments": [],
    }
    audio_path = target_dir / "audio.wav"
    emit(job, "log", "正在提取音频")
    asyncio.run(
        convert_audio_to_wav(
            video_path,
            audio_path,
            getattr(
                bilibili_config,
                "BILI_FFMPEG_PATH",
                "imageio-ffmpeg",
            ),
        )
    )
    artifact(job, audio_path, "audio")
    if not subtitle_text:
        emit(job, "log", "未找到可用字幕，正在转写音频")
        transcript_payload = asyncio.run(
            create_asr_provider().transcribe(str(audio_path))
        )
        subtitle_text = str(transcript_payload.get("text") or "").strip()
    else:
        emit(job, "log", "已使用接口提供的字幕")
    if not subtitle_text:
        raise HongguoDramaError("字幕/语音转写未返回文本")
    transcript_path = target_dir / "transcript.json"
    transcript_path.write_text(
        json.dumps(transcript_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    artifact(job, transcript_path, "json")
    script_path = target_dir / "script.md"
    script_path.write_text(
        render_script(title, episode_number, transcript_source, subtitle_text),
        encoding="utf-8",
    )
    artifact(job, script_path, "md")
    emit(
        job,
        "job_finished",
        "剧目分析完成",
        payload={"script_path": str(script_path)},
    )


def _run_third_party_analysis(
    job: CrawlJob,
    *,
    client: HongguoDramaClient,
    title: str,
    series_ref: str,
    provider_id: str,
    episode_number: int,
    target_dir: Path,
) -> None:
    episodes = client.episodes(series_ref, provider_id)
    episode = next(
        (
            item
            for item in episodes
            if int(item.get("episode") or 0) == episode_number
        ),
        None,
    )
    if episode is None:
        raise HongguoDramaError(f"所选来源没有第 {episode_number} 集")
    paid_reason = str(episode.get("paid_reason") or "")
    episode_ref = str(
        episode.get("episode_ref") or episode.get("video_id") or ""
    ).strip()
    if episode.get("is_paid") or paid_reason:
        source_path = target_dir / "source.json"
        source_path.write_text(
            json.dumps(
                {
                    "title": title,
                    "provider_id": provider_id,
                    "series_ref": series_ref,
                    "episode": episode_number,
                    "episode_ref": episode_ref,
                    "status": "skipped_paid",
                    "reason": paid_reason or "接口明确标记该集需要付费",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        artifact(job, source_path, "json")
        emit(
            job,
            "job_finished",
            "目标集需要付费，已说明并跳过",
            payload={"status": "skipped_paid", "source_path": str(source_path)},
        )
        return
    if not episode_ref:
        raise HongguoDramaError("目标集缺少剧集标识")
    video_path = target_dir / "video.mp4"
    provider = client.provider(provider_id)
    try:
        result = download_third_party_episode(
            provider, series_ref, episode_ref, video_path
        )
    except ProviderFailure as exc:
        if exc.code == "paid_content":
            raise HongguoDramaError(exc.safe_message) from exc
        raise HongguoDramaError(exc.safe_message) from exc
    artifact(job, video_path, "video")
    emit(job, "log", f"视频下载完成，{result.bytes / 1024 / 1024:.1f} MB")
    source_path = target_dir / "source.json"
    source_path.write_text(
        json.dumps(
            {
                "title": title,
                "source_platform": provider_id,
                "provider_id": provider_id,
                "series_ref": series_ref,
                "episode": {
                    "episode": episode_number,
                    "episode_ref": episode_ref,
                    "title": episode.get("title"),
                },
                "status": "succeeded",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    artifact(job, source_path, "json")
    _finish_transcription(
        job,
        client=client,
        title=title,
        episode_number=episode_number,
        episode=episode,
        target_dir=target_dir,
        video_path=video_path,
    )


def run(job: CrawlJob) -> None:
    extra = job.extra or {}
    title = str(extra.get("title") or "未命名剧目").strip()
    book_id = str(extra.get("book_id") or "").strip()
    episode_number = int(extra.get("episode") or 0)
    provider_id = str(
        extra.get("provider_id") or extra.get("source_platform") or "hongguo_capture"
    ).strip().lower() or "hongguo_capture"
    if not book_id or episode_number < 1:
        raise HongguoDramaError("剧目分析任务缺少 book_id 或有效集数")

    target_dir = Path("data") / "hg" / "jobs" / job.job_id / "dramas" / safe_filename(title) / f"episode_{episode_number:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    emit(job, "log", f"开始分析《{title}》第 {episode_number} 集")

    settings = load_settings()
    client = HongguoDramaClient(settings)
    if provider_id != "hongguo_capture":
        _run_third_party_analysis(
            job,
            client=client,
            title=title,
            series_ref=book_id,
            provider_id=provider_id,
            episode_number=episode_number,
            target_dir=target_dir,
        )
        return
    client.refresh_interfaces()
    episodes = client.episodes(book_id)
    series_title = getattr(getattr(client, "catalog", None), "series_title", None)
    capture_title = series_title(book_id) if callable(series_title) else title
    capture_title = capture_title or title
    episode = next(
        (item for item in episodes if int(item.get("episode") or 0) == episode_number),
        None,
    )
    if episode is None:
        raise HongguoDramaError(f"抓包剧集列表中没有第 {episode_number} 集")
    if not episode.get("video_id"):
        raise HongguoDramaError("目标集缺少 video_id")

    paid_reason = str(episode.get("paid_reason") or "")
    if episode.get("is_paid") or paid_reason:
        source_path = target_dir / "source.json"
        source_path.write_text(json.dumps({
            "title": title,
            "series_id": book_id,
            "episode": episode_number,
            "vid": episode.get("video_id"),
            "status": "skipped_paid",
            "reason": paid_reason or "接口明确标记该集需要付费",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact(job, source_path, "json")
        emit(job, "log", f"第 {episode_number} 集需要付费，已跳过：{paid_reason or '接口明确标记该集需要付费'}", "warning")
        emit(job, "job_finished", "目标集需要付费，已说明并跳过", payload={"status": "skipped_paid", "source_path": str(source_path)})
        return

    emit(job, "log", "正在等待目标剧集抓包")
    video_path = target_dir / "video.mp4"
    capture_service = HongguoVideoCaptureClient(
        str(settings.get("capture_service_url") or "http://127.0.0.1:8776")
    )
    initial_excluded = client.catalog.variant_identities(book_id, episode_number)
    emit(job, "progress", f"正在请求视频抓包服务播放第 {episode_number} 集", payload={"stage": "requesting_capture"})
    remote_capture = capture_service.start_capture(
        capture_title, book_id, episode_number
    )
    capture_job_id = str(remote_capture.get("job_id") or "")
    emit(job, "log", f"第 {episode_number} 集视频抓包任务已提交")
    downloader = CapturedMediaDownloader(
        client.catalog,
        wait_timeout_seconds=int(settings["wait_timeout_seconds"]),
    )

    def report_wait(remaining: int) -> dict | None:
        remote_job = capture_service.get_job(capture_job_id)
        capture_status = str(remote_job.get("status") or "")
        if capture_status == "failed":
            raise HongguoVideoCaptureServiceError(
                safe_capture_service_error(
                    remote_job.get("error"), job_failure=True
                )
            )
        service_file = completed_capture_artifact(
            remote_job, episode_number, settings["capture_root"]
        )
        if service_file is not None:
            emit(job, "log", "抓包服务已完成下载，正在接管校验后的文件")
            return downloader.adopt_local_artifact(service_file, video_path)
        emit(
            job,
            "progress",
            f"抓包服务已发起，等待第 {episode_number} 集新抓包（剩余 {remaining} 秒）",
            payload={
                "stage": "waiting_capture",
                "waiting_seconds": remaining,
                "capture_job_status": capture_status,
            },
        )
        return None

    try:
        capture = downloader.download_episode(
            book_id,
            episode_number,
            video_path,
            on_wait=report_wait,
            on_variant_failed=lambda reason: emit(
                job,
                "log",
                f"当前抓包下载失败，继续等待新抓包：{reason}",
                "warning",
            ),
            initial_excluded=initial_excluded,
        )
    except CapturePaidError as exc:
        source_path = target_dir / "source.json"
        source_path.write_text(json.dumps({
            "title": title,
            "series_id": book_id,
            "episode": episode_number,
            "vid": episode.get("video_id"),
            "status": "skipped_paid",
            "reason": str(exc),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact(job, source_path, "json")
        emit(job, "job_finished", "目标集需要付费，已说明并跳过", payload={"status": "skipped_paid", "source_path": str(source_path)})
        return
    size = int(capture["bytes"])
    artifact(job, video_path, "video")
    emit(job, "log", f"视频下载完成，{size / 1024 / 1024:.1f} MB")

    source_payload = {
        "title": title,
        "series_id": book_id,
        "source_platform": "hongguo_capture",
        "episode": {
            "episode": episode_number,
            "vid": episode.get("video_id"),
            "title": episode.get("title"),
            "capture_status": episode.get("capture_status"),
        },
        "capture": {
            key: capture[key]
            for key in ("definition", "codec", "encrypted", "source_file")
            if key in capture
        },
    }
    source_path = target_dir / "source.json"
    source_path.write_text(json.dumps(source_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact(job, source_path, "json")

    _finish_transcription(
        job,
        client=client,
        title=title,
        episode_number=episode_number,
        episode=episode,
        target_dir=target_dir,
        video_path=video_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and transcribe a Hongguo drama episode.")
    parser.add_argument("--job-file", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    job = CrawlJob.load(args.job_file)
    try:
        run(job)
    except Exception as exc:
        emit(job, "job_failed", str(exc), "error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
