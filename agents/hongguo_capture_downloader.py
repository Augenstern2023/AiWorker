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
import math
import re
import shutil
import struct
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from agents.hongguo_capture import CapturedVideoVariant, HongguoCaptureCatalog
from agents.spade_cenc import decrypt_mp4_cenc, derive_content_key


class CaptureDownloadError(RuntimeError):
    pass


class CaptureTimeoutError(CaptureDownloadError):
    pass


class CapturePaidError(CaptureDownloadError):
    pass


@dataclass
class CaptureDeadline:
    expires_at: float


class CapturedMediaDownloader:
    def __init__(
        self,
        catalog: HongguoCaptureCatalog,
        *,
        wait_timeout_seconds: int = 600,
        poll_interval_seconds: float = 2,
        max_attempts_per_url: int = 3,
        retry_delay_seconds: float = 5,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        client_factory: Callable[[], httpx.Client] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        enable_ffprobe: bool = True,
    ) -> None:
        self.catalog = catalog
        self.wait_timeout_seconds = max(1, int(wait_timeout_seconds))
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self.max_attempts_per_url = max(1, int(max_attempts_per_url))
        self.retry_delay_seconds = max(0, float(retry_delay_seconds))
        self.max_bytes = int(max_bytes)
        self.client_factory = client_factory or (
            lambda: httpx.Client(
                timeout=httpx.Timeout(60.0, read=120.0), follow_redirects=True
            )
        )
        self.clock = clock
        self.sleeper = sleeper
        self.enable_ffprobe = enable_ffprobe

    def wait_for_variant(
        self,
        series_id: str,
        episode: int,
        *,
        on_wait: Callable[[int], dict[str, Any] | None] | None = None,
        wait_if_paused: Callable[[], None] | None = None,
        excluded: set[tuple[str, str, str, int, str, str]] | None = None,
        deadline: float | CaptureDeadline | None = None,
    ) -> CapturedVideoVariant | dict[str, Any]:
        deadline_state = self._deadline_state(deadline)
        while True:
            self._wait_while_paused(wait_if_paused, deadline_state)
            variant = self.catalog.best_variant(series_id, episode, excluded)
            if variant is not None:
                return variant
            captured_episode = self.catalog.episode(series_id, episode)
            if captured_episode and captured_episode.is_paid:
                raise CapturePaidError(
                    captured_episode.paid_reason or "抓包数据明确标记该集需要付费"
                )
            remaining = deadline_state.expires_at - self.clock()
            if remaining <= 0:
                raise CaptureTimeoutError(
                    f"等待第 {episode} 集新抓包超时，请在红果 App 播放该集后重试"
                )
            if on_wait:
                external_result = on_wait(max(1, math.ceil(remaining)))
                if external_result is not None:
                    return external_result
            self.sleeper(min(self.poll_interval_seconds, remaining))

    def download_episode(
        self,
        series_id: str,
        episode: int,
        target: str | Path,
        *,
        on_wait: Callable[[int], dict[str, Any] | None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_variant_failed: Callable[[str], None] | None = None,
        wait_if_paused: Callable[[], None] | None = None,
        deadline: float | CaptureDeadline | None = None,
        initial_excluded: set[tuple[str, str, str, int, str, str]] | None = None,
    ) -> dict[str, Any]:
        deadline_state = self._deadline_state(deadline)
        excluded = set(initial_excluded or ())
        last_error = ""
        while True:
            try:
                variant = self.wait_for_variant(
                    series_id,
                    episode,
                    on_wait=on_wait,
                    wait_if_paused=wait_if_paused,
                    excluded=excluded,
                    deadline=deadline_state,
                )
                if isinstance(variant, dict):
                    return variant
            except CaptureTimeoutError as exc:
                suffix = f"；最后一次下载失败：{last_error}" if last_error else ""
                raise CaptureTimeoutError(f"{exc}{suffix}") from exc
            try:
                return self.download_variant(
                    variant,
                    target,
                    on_progress=on_progress,
                    wait_if_paused=wait_if_paused,
                    deadline=deadline_state,
                )
            except CaptureTimeoutError:
                raise
            except CaptureDownloadError as exc:
                excluded.add(variant.identity)
                last_error = str(exc)
                if on_variant_failed:
                    on_variant_failed(last_error)

    def adopt_local_artifact(
        self, source: str | Path, target: str | Path
    ) -> dict[str, Any]:
        source_path = Path(source)
        target_path = Path(target)
        if not source_path.is_file():
            raise CaptureDownloadError("抓包服务未生成目标剧集文件")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = target_path.with_suffix(target_path.suffix + ".part")
        shutil.copyfile(source_path, temporary)
        self._validate_mp4(temporary)
        temporary.replace(target_path)
        return {
            "bytes": target_path.stat().st_size,
            "definition": "",
            "codec": "",
            "encrypted": False,
            "source_file": source_path.name,
        }

    def download_variant(
        self,
        variant: CapturedVideoVariant,
        target: str | Path,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        wait_if_paused: Callable[[], None] | None = None,
        deadline: float | CaptureDeadline | None = None,
    ) -> dict[str, Any]:
        deadline_state = self._deadline_state(deadline)
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        urls: list[str] = []
        for value in (variant.main_url, variant.backup_url, variant.fallback_url):
            if value.startswith(("http://", "https://")) and value not in urls:
                urls.append(value)
        if not urls:
            raise CaptureDownloadError("抓包数据没有有效下载地址")
        last_error = ""
        for url in urls:
            for attempt in range(self.max_attempts_per_url):
                self._ensure_before_deadline(deadline_state, variant.episode)
                self._wait_while_paused(wait_if_paused, deadline_state)
                try:
                    self._download_url(
                        url,
                        target_path,
                        on_progress=on_progress,
                        wait_if_paused=wait_if_paused,
                        deadline=deadline_state,
                    )
                    self._ensure_before_deadline(deadline_state, variant.episode)
                    self._finalize_payload(variant, target_path)
                    for diagnostic in target_path.parent.glob(
                        f"{target_path.name}.*.part"
                    ):
                        diagnostic.unlink(missing_ok=True)
                    return {
                        "bytes": target_path.stat().st_size,
                        "definition": variant.definition,
                        "codec": variant.codec,
                        "encrypted": variant.encrypted,
                        "source_file": Path(variant.source_file).name,
                    }
                except CaptureTimeoutError:
                    raise
                except (CaptureDownloadError, httpx.HTTPError, OSError, ValueError) as exc:
                    last_error = self._redacted_error(exc)
                    if attempt + 1 < self.max_attempts_per_url and self.retry_delay_seconds:
                        delay = self.retry_delay_seconds
                        if deadline_state is not None:
                            delay = min(
                                delay,
                                max(0, deadline_state.expires_at - self.clock()),
                            )
                        if delay <= 0:
                            self._ensure_before_deadline(deadline_state, variant.episode)
                        self.sleeper(delay)
        detail = last_error or "未知错误"
        raise CaptureDownloadError(f"所有抓包下载地址均失败：{detail}")

    @staticmethod
    def _redacted_error(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"下载服务返回 HTTP {exc.response.status_code}"
        if isinstance(exc, httpx.RequestError):
            return f"视频传输失败（{type(exc).__name__}）"
        return re.sub(
            r"https?://[^\s\"'<>]+",
            "[已脱敏地址]",
            str(exc),
            flags=re.IGNORECASE,
        )

    def _download_url(
        self,
        url: str,
        target: Path,
        *,
        on_progress: Callable[[int, int], None] | None,
        wait_if_paused: Callable[[], None] | None,
        deadline: CaptureDeadline,
    ) -> None:
        part = target.with_suffix(target.suffix + ".part")
        meta = part.with_suffix(part.suffix + ".json")
        url_token = hashlib.sha256(url.encode("utf-8")).hexdigest()
        previous_token = ""
        try:
            previous_token = str(json.loads(meta.read_text(encoding="utf-8")).get("url_hash") or "")
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        if previous_token != url_token:
            if part.exists() and previous_token:
                diagnostic = target.with_name(
                    f"{target.name}.{previous_token[:12]}.part"
                )
                part.replace(diagnostic)
            else:
                part.unlink(missing_ok=True)
        meta.write_text(json.dumps({"url_hash": url_token}), encoding="utf-8")
        resumed = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={resumed}-"} if resumed else {}
        with self.client_factory() as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                content_length = int(response.headers.get("content-length") or 0)
                content_range = response.headers.get("content-range") or ""
                if resumed and response.status_code == 206:
                    if not content_range.startswith(f"bytes {resumed}-"):
                        raise CaptureDownloadError("下载服务返回无效续传范围")
                    total = int(content_range.rsplit("/", 1)[-1]) if "/" in content_range else 0
                    written, mode = resumed, "ab"
                else:
                    total, written, mode = content_length, 0, "wb"
                if total and total > self.max_bytes:
                    raise CaptureDownloadError("视频超过 2 GiB 大小限制")
                if on_progress:
                    on_progress(written, total)
                with part.open(mode) as output:
                    for chunk in response.iter_bytes():
                        self._ensure_before_deadline(deadline)
                        self._wait_while_paused(wait_if_paused, deadline)
                        written += len(chunk)
                        if written > self.max_bytes:
                            raise CaptureDownloadError("视频超过 2 GiB 大小限制")
                        output.write(chunk)
                        if on_progress:
                            on_progress(written, total)
                if total and written != total:
                    raise CaptureDownloadError(
                        f"视频传输不完整：预期 {total} 字节，实际 {written} 字节"
                    )

    def _finalize_payload(self, variant: CapturedVideoVariant, target: Path) -> None:
        part = target.with_suffix(target.suffix + ".part")
        if not part.exists():
            raise CaptureDownloadError("下载未生成临时文件")
        encrypted = self._file_contains_any_marker(part)
        if encrypted:
            if not variant.spade_a:
                raise CaptureDownloadError("捕获到 CENC 视频但缺少本地解密材料")
            try:
                payload = decrypt_mp4_cenc(
                    part.read_bytes(), derive_content_key(variant.spade_a)
                )
            except (IndexError, ValueError, struct.error) as exc:
                raise CaptureDownloadError("CENC 解密失败：解密材料或容器结构无效") from exc
            clear_temp = target.with_suffix(target.suffix + ".clear.tmp")
            clear_temp.write_bytes(payload)
            try:
                self._validate_mp4(clear_temp)
                clear_temp.replace(target)
            finally:
                clear_temp.unlink(missing_ok=True)
        else:
            self._validate_mp4(part)
            part.replace(target)
        part.with_suffix(part.suffix + ".json").unlink(missing_ok=True)

    @staticmethod
    def _file_contains_any_marker(path: Path) -> bool:
        marker_types = {b"tenc", b"encv", b"enca"}
        container_types = {
            b"moov",
            b"trak",
            b"mdia",
            b"minf",
            b"stbl",
            b"stsd",
            b"sinf",
            b"schi",
            b"edts",
            b"dinf",
            b"mvex",
            b"moof",
            b"traf",
            b"mfra",
            b"udta",
            b"meta",
            b"ilst",
        }

        with path.open("rb") as file:
            file_size = path.stat().st_size

            def walk_boxes(start: int, end: int, depth: int = 0) -> bool:
                if depth > 16:
                    return False
                offset = start
                while offset + 8 <= end:
                    file.seek(offset)
                    header = file.read(8)
                    if len(header) != 8:
                        return False
                    box_size = struct.unpack(">I", header[:4])[0]
                    box_type = header[4:8]
                    header_size = 8
                    if box_size == 1:
                        extended = file.read(8)
                        if len(extended) != 8:
                            return False
                        box_size = struct.unpack(">Q", extended)[0]
                        header_size = 16
                    elif box_size == 0:
                        box_size = end - offset
                    if box_size < header_size or offset + box_size > end:
                        return False
                    if box_type in marker_types:
                        return True
                    if box_type in container_types:
                        child_start = offset + header_size
                        if box_type == b"stsd":
                            child_start += 8
                        elif box_type == b"meta":
                            child_start += 4
                        if child_start < offset + box_size and walk_boxes(
                            child_start, offset + box_size, depth + 1
                        ):
                            return True
                    offset += box_size
                return False

            return walk_boxes(0, file_size)

    def _ensure_before_deadline(
        self, deadline: CaptureDeadline | None, episode: int | None = None
    ) -> None:
        if deadline is not None and self.clock() >= deadline.expires_at:
            detail = f"第 {episode} 集" if episode else "目标剧集"
            raise CaptureTimeoutError(
                f"{detail}抓包下载超时，请在红果 App 重新播放后重试"
            )

    def _deadline_state(
        self, deadline: float | CaptureDeadline | None
    ) -> CaptureDeadline:
        if isinstance(deadline, CaptureDeadline):
            return deadline
        return CaptureDeadline(
            float(deadline)
            if deadline is not None
            else self.clock() + self.wait_timeout_seconds
        )

    def _wait_while_paused(
        self,
        wait_if_paused: Callable[[], None] | None,
        deadline: CaptureDeadline,
    ) -> None:
        if wait_if_paused is None:
            return
        started = self.clock()
        wait_if_paused()
        deadline.expires_at += max(0, self.clock() - started)

    def _validate_mp4(self, path: Path) -> None:
        if path.stat().st_size <= 1024:
            raise CaptureDownloadError("下载结果过短，不是有效 MP4")
        with path.open("rb") as file:
            header = file.read(12)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise CaptureDownloadError("下载结果不是有效 MP4")
        if self._file_contains_any_marker(path):
            raise CaptureDownloadError("输出仍包含 CENC 加密标记")
        ffprobe = shutil.which("ffprobe") if self.enable_ffprobe else None
        if not ffprobe:
            return
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "stream=index", "-of", "json", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CaptureDownloadError("ffprobe 验证视频失败或超时") from exc
        if result.returncode != 0:
            raise CaptureDownloadError(f"ffprobe 无法解析下载视频：{result.stderr.strip()}")
        try:
            streams = json.loads(result.stdout).get("streams") or []
        except (AttributeError, json.JSONDecodeError, TypeError):
            streams = []
        if not streams:
            raise CaptureDownloadError("ffprobe 未识别到音视频流")
