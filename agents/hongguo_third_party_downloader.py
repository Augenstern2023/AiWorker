# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from agents.drama_providers import (
    DownloadResult,
    DramaProvider,
    PlayableMedia,
    ProviderFailure,
)
from agents.spade_cenc import mp4_contains_cenc


_NON_RETRYABLE_FAILURES = {
    "paid_content",
    "decryption_key_invalid",
    "decryption_tool_missing",
}


def _partial_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".part")


def _resume_state_path(path: Path) -> Path:
    partial = _partial_path(path)
    return partial.with_suffix(partial.suffix + ".json")


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _media_resume_hash(media: PlayableMedia) -> str:
    identity = f"{media.url}\0{media.decrypt_key}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _prepare_resumable_download(path: Path, media: PlayableMedia) -> Path:
    """Only resume bytes that belong to the exact URL and private key pair."""

    partial = _partial_path(path)
    state_path = _resume_state_path(path)
    expected_hash = _media_resume_hash(media)
    saved_hash = ""
    try:
        if state_path.exists():
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and not payload.get("diagnostic_only"):
                saved_hash = str(payload.get("media_hash") or "")
    except (OSError, json.JSONDecodeError, TypeError):
        saved_hash = ""
    if partial.exists() and saved_hash != expected_hash:
        try:
            partial.unlink()
        except OSError as exc:
            raise ProviderFailure(
                "transfer_failed", "无法清理不匹配的断点下载文件"
            ) from exc
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"media_hash": expected_hash}), encoding="utf-8"
        )
    except OSError as exc:
        raise ProviderFailure("transfer_failed", "无法准备安全断点续传") from exc
    return state_path


def _preserve_diagnostic(source: Path, target: Path) -> None:
    """Keep failed bytes for inspection while marking them as non-resumable."""

    state_path = target.with_suffix(target.suffix + ".json")
    try:
        target.unlink(missing_ok=True)
        source.replace(target)
        state_path.write_text(
            json.dumps({"diagnostic_only": True}), encoding="utf-8"
        )
    except OSError:
        _best_effort_unlink(source)
        _best_effort_unlink(target)
        _best_effort_unlink(state_path)


def _validate_mp4_header(path: Path, *, failure_code: str = "transfer_failed") -> None:
    try:
        size = path.stat().st_size
        with path.open("rb") as file:
            header = file.read(12)
    except OSError as exc:
        raise ProviderFailure(failure_code, "无法读取下载结果") from exc
    if size <= 1024:
        raise ProviderFailure(failure_code, "下载结果过短，不是有效 MP4")
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise ProviderFailure(failure_code, "下载结果不是有效 MP4")


def decrypt_downloaded_cenc(
    encrypted_path: Path, output_path: Path, decrypt_key: str
) -> None:
    """Decrypt a local CENC MP4 with FFmpeg without exposing its content key."""

    key = str(decrypt_key or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{32}", key) is None:
        raise ProviderFailure(
            "decryption_key_invalid", "播放接口返回的 CENC 解密密钥格式无效"
        )
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ProviderFailure("decryption_tool_missing", "CENC 解密需要 FFmpeg")

    encrypted = Path(encrypted_path)
    output = Path(output_path)
    decrypted = output.with_name(f"{output.stem}.decrypting{output.suffix}")
    _best_effort_unlink(decrypted)
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-decryption_key",
                key,
                "-i",
                str(encrypted),
                "-map",
                "0:v?",
                "-map",
                "0:a?",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(decrypted),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1800,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _best_effort_unlink(decrypted)
        raise ProviderFailure(
            "decryption_failed", "FFmpeg 解密 CENC 视频失败或超时"
        ) from None
    if result.returncode != 0 or not decrypted.exists():
        _best_effort_unlink(decrypted)
        raise ProviderFailure("decryption_failed", "FFmpeg 无法解密 CENC 视频")
    try:
        _validate_mp4_header(decrypted, failure_code="decryption_failed")
        if mp4_contains_cenc(decrypted):
            raise ProviderFailure("decryption_failed", "解密输出仍包含 CENC 加密标记")
        decrypted.replace(output)
    except ProviderFailure:
        _best_effort_unlink(decrypted)
        raise
    except OSError as exc:
        _best_effort_unlink(decrypted)
        raise ProviderFailure("decryption_failed", "无法保存解密后的视频") from exc


def validate_downloaded_mp4(path: Path) -> None:
    """Reject invalid MP4 files and normalize codecs for browser playback."""

    media_path = Path(path)
    _validate_mp4_header(media_path)
    if mp4_contains_cenc(media_path):
        raise ProviderFailure(
            "decryption_key_missing", "下载结果为 CENC 加密视频，但播放地址缺少解密密钥"
        )

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
    partial_path = _partial_path(target_path)
    partial_state_path = _resume_state_path(target_path)
    encrypted_path = target_path.with_suffix(".enc")
    encrypted_partial_path = _partial_path(encrypted_path)
    encrypted_state_path = _resume_state_path(encrypted_path)
    for attempt in range(2):
        try:
            media = provider.resolve_playable(series_ref, episode_ref)
            download_target = encrypted_path if media.decrypt_key else target_path
            if media.decrypt_key:
                _best_effort_unlink(encrypted_path)
            resume_state_path = _prepare_resumable_download(download_target, media)
            result = provider.download_episode(
                media,
                download_target,
                on_progress=on_progress,
                wait_if_paused=wait_if_paused,
            )
            _best_effort_unlink(resume_state_path)
            try:
                if media.decrypt_key:
                    decrypt_downloaded_cenc(result.path, target_path, media.decrypt_key)
                validate_downloaded_mp4(target_path)
            except ProviderFailure as exc:
                failed_download = Path(result.path)
                if failed_download != target_path:
                    _best_effort_unlink(target_path)
                if failed_download.exists():
                    _preserve_diagnostic(failed_download, partial_path)
                if attempt == 0 and exc.code not in _NON_RETRYABLE_FAILURES:
                    _best_effort_unlink(partial_path)
                    _best_effort_unlink(partial_state_path)
                raise
            for temporary in (
                encrypted_path,
                encrypted_partial_path,
                encrypted_state_path,
                partial_path,
                partial_state_path,
            ):
                _best_effort_unlink(temporary)
            return DownloadResult(
                provider_id=result.provider_id,
                path=target_path,
                bytes=target_path.stat().st_size,
                metadata=result.metadata,
            )
        except ProviderFailure as exc:
            if exc.code in _NON_RETRYABLE_FAILURES:
                raise
            last_error = exc
    assert last_error is not None
    if not partial_path.exists():
        for failed_path in (encrypted_path, encrypted_partial_path):
            if failed_path.exists():
                _preserve_diagnostic(failed_path, partial_path)
                break
    _best_effort_unlink(encrypted_path)
    _best_effort_unlink(encrypted_partial_path)
    _best_effort_unlink(encrypted_state_path)
    raise last_error
