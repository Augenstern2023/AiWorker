# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from agents.drama_providers import DownloadResult, DramaProvider, ProviderFailure


def validate_downloaded_mp4(path: Path) -> None:
    """Reject invalid MP4 files and normalize codecs for browser playback."""

    media_path = Path(path)
    try:
        size = media_path.stat().st_size
        with media_path.open("rb") as file:
            header = file.read(12)
    except OSError as exc:
        raise ProviderFailure("transfer_failed", "无法读取下载结果") from exc
    if size <= 1024:
        raise ProviderFailure("transfer_failed", "下载结果过短，不是有效 MP4")
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise ProviderFailure("transfer_failed", "下载结果不是有效 MP4")

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=index,codec_type,codec_name",
                "-of",
                "json",
                str(media_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderFailure("transfer_failed", "ffprobe 验证视频失败或超时") from exc
    if result.returncode != 0:
        raise ProviderFailure("transfer_failed", "ffprobe 无法解析下载视频")
    try:
        streams = json.loads(result.stdout).get("streams") or []
    except (AttributeError, json.JSONDecodeError, TypeError):
        streams = []
    if not streams:
        raise ProviderFailure("transfer_failed", "ffprobe 未识别到音视频流")

    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video_streams:
        raise ProviderFailure("transfer_failed", "ffprobe 未识别到视频流")
    if all(stream.get("codec_name") == "h264" for stream in video_streams) and all(
        stream.get("codec_name") == "aac" for stream in audio_streams
    ):
        return

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ProviderFailure("transfer_failed", "视频编码不兼容且未找到 ffmpeg")
    converted_path = media_path.with_name(f"{media_path.stem}.browser{media_path.suffix}")
    converted_path.unlink(missing_ok=True)
    try:
        converted = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-i",
                str(media_path),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(converted_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1800,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        converted_path.unlink(missing_ok=True)
        raise ProviderFailure("transfer_failed", "ffmpeg 转码视频失败或超时") from exc
    if converted.returncode != 0 or not converted_path.exists():
        converted_path.unlink(missing_ok=True)
        raise ProviderFailure("transfer_failed", "ffmpeg 无法转码为浏览器兼容格式")
    try:
        converted_path.replace(media_path)
    except OSError as exc:
        converted_path.unlink(missing_ok=True)
        raise ProviderFailure("transfer_failed", "无法替换转码后的视频") from exc


def download_third_party_episode(
    provider: DramaProvider,
    series_ref: str,
    episode_ref: str,
    target: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    wait_if_paused: Callable[[], None] | None = None,
) -> DownloadResult:
    """Resolve and download once, then refresh the play URL for one retry."""

    last_error: ProviderFailure | None = None
    target_path = Path(target)
    partial_path = target_path.with_suffix(target_path.suffix + ".part")
    for attempt in range(2):
        try:
            media = provider.resolve_playable(series_ref, episode_ref)
            result = provider.download_episode(
                media,
                target_path,
                on_progress=on_progress,
                wait_if_paused=wait_if_paused,
            )
            try:
                validate_downloaded_mp4(result.path)
            except ProviderFailure:
                if result.path.exists():
                    result.path.replace(partial_path)
                if attempt == 0:
                    partial_path.unlink(missing_ok=True)
                raise
            return DownloadResult(
                provider_id=result.provider_id,
                path=result.path,
                bytes=result.path.stat().st_size,
                metadata=result.metadata,
            )
        except ProviderFailure as exc:
            if exc.code == "paid_content":
                raise
            last_error = exc
    assert last_error is not None
    raise last_error
