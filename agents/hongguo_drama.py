# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlparse

import httpx

from agents.hongguo_capture import HongguoCaptureCatalog, normalize_title
from agents.drama_providers import ProviderFailure
from agents.drama_providers.third_party import (
    DEFAULT_CONFIG_URL,
    LEGACY_CONFIG_URL,
    build_third_party_registry,
)


DEFAULT_CAPTURE_ROOT = r"E:\hg\raw\video_urls"
DEFAULT_CAPTURE_SERVICE_URL = "http://127.0.0.1:8776"
DEFAULT_WAIT_TIMEOUT_SECONDS = 600
_catalogs: dict[str, HongguoCaptureCatalog] = {}
_catalogs_lock = RLock()


class HongguoDramaError(RuntimeError):
    pass


class HongguoPaidContentError(HongguoDramaError):
    pass


def text_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_drama_title(value: Any) -> str:
    return normalize_title(value)


def safe_filename(value: Any, max_len: int = 80) -> str:
    text = re.sub(r"\s+", "_", text_value(value))
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip("._ ")
    return (text or "untitled")[:max_len]


def settings_path(runtime_dir: str | Path = ".runtime") -> Path:
    return Path(runtime_dir) / "hongguo_drama_settings.json"


def _validated_settings(
    capture_root: str,
    wait_timeout_seconds: int,
    capture_service_url: str = DEFAULT_CAPTURE_SERVICE_URL,
    config_url: str = DEFAULT_CONFIG_URL,
    activation_code: str = "",
) -> dict[str, Any]:
    root = Path(capture_root).expanduser()
    if not root.is_absolute():
        raise ValueError("抓包根目录必须是绝对路径")
    timeout = int(wait_timeout_seconds)
    if not 60 <= timeout <= 3600:
        raise ValueError("等待时长必须为 60 到 3600 秒")
    service_url = text_value(capture_service_url).rstrip("/")
    parsed_service_url = urlparse(service_url)
    if parsed_service_url.scheme not in {"http", "https"} or not parsed_service_url.netloc:
        raise ValueError("视频抓包服务地址必须是有效的 HTTP 地址")
    remote_config_url = text_value(config_url) or DEFAULT_CONFIG_URL
    if remote_config_url.rstrip("/") == LEGACY_CONFIG_URL.rstrip("/"):
        remote_config_url = DEFAULT_CONFIG_URL
    parsed_config_url = urlparse(remote_config_url)
    if parsed_config_url.scheme not in {"http", "https"} or not parsed_config_url.netloc:
        raise ValueError("第三方配置中心地址必须是有效的 HTTP 地址")
    return {
        "config_url": remote_config_url,
        "activation_code": text_value(activation_code),
        "capture_root": str(root.resolve()),
        "capture_service_url": service_url,
        "wait_timeout_seconds": timeout,
    }


def load_settings(runtime_dir: str | Path = ".runtime") -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "config_url": DEFAULT_CONFIG_URL,
        "activation_code": "",
        "capture_root": DEFAULT_CAPTURE_ROOT,
        "capture_service_url": DEFAULT_CAPTURE_SERVICE_URL,
        "wait_timeout_seconds": DEFAULT_WAIT_TIMEOUT_SECONDS,
    }
    path = settings_path(runtime_dir)
    if not path.exists():
        return defaults
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(payload, dict):
        return defaults
    try:
        return _validated_settings(
            text_value(payload.get("capture_root")) or DEFAULT_CAPTURE_ROOT,
            int(payload.get("wait_timeout_seconds") or DEFAULT_WAIT_TIMEOUT_SECONDS),
            text_value(payload.get("capture_service_url"))
            or DEFAULT_CAPTURE_SERVICE_URL,
            text_value(payload.get("config_url")) or DEFAULT_CONFIG_URL,
            text_value(payload.get("activation_code")),
        )
    except (TypeError, ValueError):
        return defaults


def save_settings(
    runtime_dir: str | Path = ".runtime",
    capture_root: str | None = None,
    capture_service_url: str | None = None,
    wait_timeout_seconds: int | None = None,
    config_url: str | None = None,
    activation_code: str | None = None,
) -> dict[str, Any]:
    current = load_settings(runtime_dir)
    settings = _validated_settings(
        str(current["capture_root"])
        if capture_root is None
        else text_value(capture_root),
        int(current["wait_timeout_seconds"])
        if wait_timeout_seconds is None
        else int(wait_timeout_seconds),
        str(current.get("capture_service_url") or DEFAULT_CAPTURE_SERVICE_URL)
        if capture_service_url is None
        else text_value(capture_service_url),
        str(current.get("config_url") or DEFAULT_CONFIG_URL)
        if config_url is None
        else text_value(config_url),
        str(current.get("activation_code") or "")
        if activation_code is None
        else text_value(activation_code),
    )
    path = settings_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings


def public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_url": text_value(settings.get("config_url")) or DEFAULT_CONFIG_URL,
        "has_activation_code": bool(text_value(settings.get("activation_code"))),
        "capture_root": text_value(settings.get("capture_root")) or DEFAULT_CAPTURE_ROOT,
        "capture_service_url": text_value(settings.get("capture_service_url"))
        or DEFAULT_CAPTURE_SERVICE_URL,
        "wait_timeout_seconds": int(
            settings.get("wait_timeout_seconds") or DEFAULT_WAIT_TIMEOUT_SECONDS
        ),
    }


class HongguoDramaClient:
    """Compatibility facade routing capture and third-party providers."""

    def __init__(self, settings: dict[str, Any], timeout: float = 20.0) -> None:
        self.settings = _validated_settings(
            text_value(settings.get("capture_root"))
            if "capture_root" in settings
            else DEFAULT_CAPTURE_ROOT,
            int(settings.get("wait_timeout_seconds") or DEFAULT_WAIT_TIMEOUT_SECONDS),
            text_value(settings.get("capture_service_url"))
            or DEFAULT_CAPTURE_SERVICE_URL,
            text_value(settings.get("config_url")) or DEFAULT_CONFIG_URL,
            text_value(settings.get("activation_code")),
        )
        self.timeout = timeout
        self.default_source_platform = "hongguo_capture"
        self._third_party_registry = None
        catalog_key = self.settings["capture_root"]
        with _catalogs_lock:
            catalog = _catalogs.get(catalog_key)
            if catalog is None:
                catalog = HongguoCaptureCatalog(catalog_key)
                _catalogs[catalog_key] = catalog
            self.catalog = catalog

    def refresh_interfaces(self) -> dict[str, Any]:
        try:
            self.catalog.validate_root()
            return self.catalog.stats()
        except (OSError, ValueError) as exc:
            raise HongguoDramaError(str(exc)) from exc

    def _registry(self):
        if self._third_party_registry is None:
            try:
                self._third_party_registry = build_third_party_registry(
                    self.settings, timeout=self.timeout
                )
            except ProviderFailure as exc:
                raise HongguoDramaError(exc.safe_message) from exc
        return self._third_party_registry

    def refresh_third_party_providers(self) -> dict[str, Any]:
        self._third_party_registry = None
        registry = self._registry()
        report = registry.search_all("接口连通测试")
        providers = [
            {
                "provider_id": status.provider_id,
                "display_name": status.display_name,
                "status": status.status,
                "elapsed_ms": status.elapsed_ms,
                "error_code": status.error_code,
                "error": status.error,
            }
            for status in report.providers
        ]
        return {
            "provider_count": len(providers),
            "available_count": sum(
                provider["status"] == "available" for provider in providers
            ),
            "providers": providers,
        }

    def provider(self, source_platform: str):
        source = text_value(source_platform).lower()
        if not source or source == "hongguo_capture":
            raise HongguoDramaError("抓包来源不提供第三方播放接口")
        try:
            return self._registry().get(source)
        except ProviderFailure as exc:
            raise HongguoDramaError(exc.safe_message) from exc

    def search(
        self, title: str, page: int = 1, source_platform: str = "hongguo_capture"
    ) -> list[dict[str, Any]]:
        query = text_value(title)
        if not query:
            raise HongguoDramaError("请输入剧名")
        source = text_value(source_platform).lower() or self.default_source_platform
        if source != "hongguo_capture":
            try:
                return [
                    item.public_dict()
                    for item in self.provider(source).search(query, page=page)
                ]
            except ProviderFailure as exc:
                raise HongguoDramaError(exc.safe_message) from exc
        try:
            return [item.public_dict() for item in self.catalog.search_exact(query)]
        except (OSError, ValueError) as exc:
            raise HongguoDramaError(str(exc)) from exc

    def search_exact(self, title: str) -> list[dict[str, Any]]:
        return self.search(title)

    def search_for_download(self, title: str) -> list[dict[str, Any]]:
        query = text_value(title)
        if not query:
            raise HongguoDramaError("请输入剧名")
        try:
            return [
                item.public_dict()
                for item in self.catalog.search_compatible(query, 8)
            ]
        except (OSError, ValueError) as exc:
            raise HongguoDramaError(str(exc)) from exc

    def search_exact_all_sources(self, title: str) -> list[dict[str, Any]]:
        try:
            report = self._registry().search_exact_all(title)
        except ProviderFailure as exc:
            raise HongguoDramaError(exc.safe_message) from exc
        return [item.public_dict() for item in report.candidates]

    def search_provider_groups(self, title: str) -> dict[str, Any]:
        result: dict[str, Any]
        try:
            report = self._registry().search_exact_all(title)
            result = report.public_dict()
            result["third_party_error"] = ""
        except (HongguoDramaError, ProviderFailure) as exc:
            result = {
                "candidates": [],
                "providers": [],
                "third_party_error": str(exc),
            }
        try:
            result["capture_candidates"] = [
                item.public_dict()
                for item in self.catalog.search_compatible(title, 8)
            ]
            result["capture_error"] = ""
        except (OSError, ValueError) as exc:
            result["capture_candidates"] = []
            result["capture_error"] = str(exc)
        return result

    def suggest(self, title: str, limit: int = 8) -> list[dict[str, Any]]:
        try:
            return [item.public_dict() for item in self.catalog.suggest(title, limit)]
        except (OSError, ValueError) as exc:
            raise HongguoDramaError(str(exc)) from exc

    def suggest_all(self, title: str, limit: int = 8) -> list[dict[str, Any]]:
        query = text_value(title)
        if not query:
            return []
        capped_limit = max(1, min(8, int(limit)))
        candidates: list[Any] = []
        third_party_error: Exception | None = None
        try:
            candidates.extend(self._registry().search_all(query).candidates)
        except (HongguoDramaError, ProviderFailure) as exc:
            third_party_error = exc
        try:
            candidates.extend(self.catalog.suggest(query, capped_limit))
        except (OSError, ValueError) as exc:
            if not candidates:
                message = str(third_party_error or exc)
                raise HongguoDramaError(message) from exc

        suggestions: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for candidate in candidates:
            title_key = normalize_title(getattr(candidate, "title", ""))
            if not title_key or title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            suggestions.append(candidate.public_dict())
            if len(suggestions) >= capped_limit:
                break
        return suggestions

    def episodes(
        self, book_id: str, source_platform: str = "hongguo_capture"
    ) -> list[dict[str, Any]]:
        series_id = text_value(book_id)
        if not series_id:
            raise HongguoDramaError("缺少 series_id")
        source = text_value(source_platform).lower() or self.default_source_platform
        if source != "hongguo_capture":
            try:
                return [
                    item.public_dict()
                    for item in self.provider(source).episodes(series_id)
                ]
            except ProviderFailure as exc:
                raise HongguoDramaError(exc.safe_message) from exc
        try:
            rows = self.catalog.episodes(series_id)
        except (OSError, ValueError) as exc:
            raise HongguoDramaError(str(exc)) from exc
        if not rows:
            raise HongguoDramaError("抓包目录中未找到该剧目的剧集信息")
        return [row.public_dict() for row in rows]

    def play_url(
        self,
        video_id: str,
        book_id: str = "",
        source_platform: str = "",
    ) -> str:
        source = text_value(source_platform).lower() or self.default_source_platform
        if source == "hongguo_capture":
            raise HongguoDramaError("播放地址必须从外部抓包数据获取")
        try:
            return self.provider(source).resolve_playable(
                text_value(book_id), text_value(video_id)
            ).url
        except ProviderFailure as exc:
            if exc.code == "paid_content":
                raise HongguoPaidContentError(exc.safe_message) from exc
            raise HongguoDramaError(exc.safe_message) from exc

    def download_video(
        self,
        video_url: str,
        target: str | Path,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        on_progress: Callable[[int, int], None] | None = None,
        wait_if_paused: Callable[[], None] | None = None,
    ) -> int:
        target_path = Path(target)
        temp_path = target_path.with_suffix(target_path.suffix + ".part")
        resumed_bytes = temp_path.stat().st_size if temp_path.exists() else 0
        headers = {"Range": f"bytes={resumed_bytes}-"} if resumed_bytes else {}
        try:
            with httpx.Client(
                timeout=httpx.Timeout(60.0, read=120.0), follow_redirects=True
            ) as client:
                with client.stream("GET", video_url, headers=headers) as response:
                    response.raise_for_status()
                    content_length = int(response.headers.get("content-length") or 0)
                    content_range = response.headers.get("content-range") or ""
                    if resumed_bytes and response.status_code == 206:
                        if not content_range.startswith(f"bytes {resumed_bytes}-"):
                            raise HongguoDramaError("下载服务返回了无效的续传范围")
                        total_bytes = int(content_range.rsplit("/", 1)[-1]) if "/" in content_range else 0
                        written, mode = resumed_bytes, "ab"
                    else:
                        if resumed_bytes:
                            temp_path.unlink(missing_ok=True)
                        total_bytes, written, mode = content_length, 0, "wb"
                    if total_bytes and total_bytes > max_bytes:
                        raise HongguoDramaError("视频超过大小限制")
                    if on_progress:
                        on_progress(written, total_bytes)
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with temp_path.open(mode) as output:
                        for chunk in response.iter_bytes():
                            if wait_if_paused:
                                wait_if_paused()
                            written += len(chunk)
                            if written > max_bytes:
                                raise HongguoDramaError("视频超过大小限制")
                            output.write(chunk)
                            if on_progress:
                                on_progress(written, total_bytes)
        except (OSError, httpx.HTTPError, HongguoDramaError) as exc:
            if isinstance(exc, HongguoDramaError):
                raise
            raise HongguoDramaError(f"下载视频失败: {exc}") from exc
        temp_path.replace(target_path)
        return written

    def fetch_subtitle_text(self, subtitle_url: str) -> str:
        if not subtitle_url.startswith(("http://", "https://")):
            return ""
        try:
            response = httpx.get(subtitle_url, timeout=self.timeout, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError:
            return ""
        content = response.text
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            body = payload.get("body") or payload.get("segments") or payload.get("data") or []
            if isinstance(body, list):
                lines = [
                    text_value(item.get("content") or item.get("text"))
                    for item in body
                    if isinstance(item, dict)
                ]
                return "\n".join(line for line in lines if line)
        return "\n".join(
            line.strip()
            for line in content.splitlines()
            if line.strip()
            and line.strip().upper() != "WEBVTT"
            and "-->" not in line
            and not line.strip().isdigit()
        )


def media_extension(video_url: str) -> str:
    suffix = Path(urlparse(video_url).path).suffix.lower()
    return suffix if suffix in {".mp4", ".mov", ".mkv", ".webm"} else ".mp4"
