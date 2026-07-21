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
from agents.hongguo_drama import (
    HongguoDramaClient,
    HongguoDramaError,
    HongguoPaidContentError,
    load_settings,
    media_extension,
    safe_filename,
)
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


def run(job: CrawlJob) -> None:
    extra = job.extra or {}
    title = str(extra.get("title") or "未命名剧目").strip()
    book_id = str(extra.get("book_id") or "").strip()
    episode_number = int(extra.get("episode") or 0)
    if not book_id or episode_number < 1:
        raise HongguoDramaError("剧目分析任务缺少 book_id 或有效集数")

    target_dir = Path("data") / "hg" / "jobs" / job.job_id / "dramas" / safe_filename(title) / f"episode_{episode_number:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    emit(job, "log", f"开始分析《{title}》第 {episode_number} 集")

    client = HongguoDramaClient(load_settings())
    client.refresh_interfaces()
    episodes = client.episodes(book_id)
    if episode_number > len(episodes):
        raise HongguoDramaError(f"集数超出范围：共 {len(episodes)} 集")
    episode = episodes[episode_number - 1]
    if not episode.get("video_id"):
        raise HongguoDramaError("目标集缺少 video_id")

    paid_reason = str(episode.get("paid_reason") or "")
    if episode.get("is_paid") or paid_reason:
        source_path = target_dir / "source.json"
        source_path.write_text(json.dumps({
            "title": title,
            "book_id": book_id,
            "episode": episode,
            "status": "skipped_paid",
            "reason": paid_reason or "接口明确标记该集需要付费",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact(job, source_path, "json")
        emit(job, "log", f"第 {episode_number} 集需要付费，已跳过：{paid_reason or '接口明确标记该集需要付费'}", "warning")
        emit(job, "job_finished", "目标集需要付费，已说明并跳过", payload={"status": "skipped_paid", "source_path": str(source_path)})
        return

    emit(job, "log", "正在获取临时播放地址")
    try:
        video_url = client.play_url(episode["video_id"])
    except HongguoPaidContentError as exc:
        source_path = target_dir / "source.json"
        source_path.write_text(json.dumps({
            "title": title,
            "book_id": book_id,
            "episode": episode,
            "status": "skipped_paid",
            "reason": str(exc),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact(job, source_path, "json")
        emit(job, "log", f"第 {episode_number} 集需要付费，已跳过：{exc}", "warning")
        emit(job, "job_finished", "目标集需要付费，已说明并跳过", payload={"status": "skipped_paid", "source_path": str(source_path)})
        return
    video_path = target_dir / f"video{media_extension(video_url)}"
    try:
        size = client.download_video(video_url, video_path)
    except HongguoDramaError:
        emit(job, "log", "首次下载失败，正在刷新播放地址重试", "warning")
        video_url = client.play_url(episode["video_id"])
        size = client.download_video(video_url, video_path)
    artifact(job, video_path, "video")
    emit(job, "log", f"视频下载完成，{size / 1024 / 1024:.1f} MB")

    source_payload = {
        "title": title,
        "book_id": book_id,
        "episode": episode,
        "video_url": video_url,
    }
    source_path = target_dir / "source.json"
    source_path.write_text(json.dumps(source_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact(job, source_path, "json")

    subtitle_text = client.fetch_subtitle_text(str(episode.get("subtitle_url") or ""))
    transcript_source = "subtitle" if subtitle_text else "asr"
    transcript_payload: dict = {"source": transcript_source, "text": subtitle_text, "segments": []}
    audio_path = target_dir / "audio.wav"
    emit(job, "log", "正在提取音频")
    asyncio.run(convert_audio_to_wav(video_path, audio_path, getattr(bilibili_config, "BILI_FFMPEG_PATH", "imageio-ffmpeg")))
    artifact(job, audio_path, "audio")
    if not subtitle_text:
        emit(job, "log", "未找到可用字幕，正在转写音频")
        transcript_payload = asyncio.run(create_asr_provider().transcribe(str(audio_path)))
        subtitle_text = str(transcript_payload.get("text") or "").strip()
    else:
        emit(job, "log", "已使用接口提供的字幕")

    if not subtitle_text:
        raise HongguoDramaError("字幕/语音转写未返回文本")

    transcript_path = target_dir / "transcript.json"
    transcript_path.write_text(json.dumps(transcript_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact(job, transcript_path, "json")
    script_path = target_dir / "script.md"
    script_path.write_text(render_script(title, episode_number, transcript_source, subtitle_text), encoding="utf-8")
    artifact(job, script_path, "md")
    emit(job, "job_finished", "剧目分析完成", payload={"script_path": str(script_path)})


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
