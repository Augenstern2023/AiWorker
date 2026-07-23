# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx

from agents.drama_download_policy import paid_content_reason


DEFAULT_CONFIG_URL = "https://mizhi.kissm.top/pc/con.php"


class HongguoDramaError(RuntimeError):
    pass


class HongguoPaidContentError(HongguoDramaError):
    pass


def text_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_drama_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", text_value(value)).casefold()
    return "".join(char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def safe_filename(value: Any, max_len: int = 80) -> str:
    text = re.sub(r"\s+", "_", text_value(value))
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip("._ ")
    return (text or "untitled")[:max_len]


def settings_path(runtime_dir: str | Path = ".runtime") -> Path:
    return Path(runtime_dir) / "hongguo_drama_settings.json"


def load_settings(runtime_dir: str | Path = ".runtime") -> dict[str, str]:
    settings = {"config_url": DEFAULT_CONFIG_URL, "activation_code": ""}
    path = settings_path(runtime_dir)
    if not path.exists():
        return settings
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    if isinstance(payload, dict):
        settings["config_url"] = text_value(payload.get("config_url")) or settings["config_url"]
        settings["activation_code"] = text_value(payload.get("activation_code"))
    return settings


def save_settings(
    runtime_dir: str | Path = ".runtime",
    config_url: str | None = None,
    activation_code: str | None = None,
) -> dict[str, str]:
    settings = load_settings(runtime_dir)
    if config_url is not None:
        settings["config_url"] = text_value(config_url) or DEFAULT_CONFIG_URL
    if activation_code is not None:
        settings["activation_code"] = text_value(activation_code)
    path = settings_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings


def public_settings(settings: dict[str, str]) -> dict[str, Any]:
    return {
        "config_url": settings.get("config_url") or DEFAULT_CONFIG_URL,
        "has_activation_code": bool(settings.get("activation_code")),
    }


def _data_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("list", "items", "records"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _find_platform_config(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HongguoDramaError("配置中心返回格式无效")
    containers = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        platforms = container.get("platforms")
        if isinstance(platforms, dict):
            candidate = platforms.get("hg")
            if isinstance(candidate, dict):
                return candidate
        if isinstance(platforms, list):
            for candidate in platforms:
                if not isinstance(candidate, dict):
                    continue
                if text_value(
                    candidate.get("platform") or candidate.get("key") or candidate.get("name") or candidate.get("value")
                ).lower() == "hg":
                    return candidate
    raise HongguoDramaError("配置中心未提供红果 hg 接口配置")


class HongguoDramaClient:
    def __init__(self, settings: dict[str, str], timeout: float = 20.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self.interfaces: dict[str, str] = {}

    def _params(self, **params: Any) -> dict[str, Any]:
        result = {key: value for key, value in params.items() if value not in (None, "")}
        if self.settings.get("activation_code"):
            result["code"] = self.settings["activation_code"]
        return result

    def _request_url(self, url: str, **params: Any) -> str:
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        for key, value in self._params(**params).items():
            query.setdefault(key, value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))

    def _json(self, url: str, **params: Any) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(self._request_url(url, **params))
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                error_payload = exc.response.json()
                if isinstance(error_payload, dict):
                    detail = text_value(error_payload.get("message") or error_payload.get("msg"))
            except ValueError:
                pass
            suffix = f"（{detail}）" if detail else ""
            raise HongguoDramaError(f"请求接口失败: HTTP {exc.response.status_code}{suffix}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise HongguoDramaError("请求接口失败，请检查网络或接口设置") from exc
        if not isinstance(payload, dict):
            raise HongguoDramaError("接口未返回 JSON 对象")
        return payload

    def refresh_interfaces(self) -> dict[str, str]:
        payload = self._json(self.settings.get("config_url") or DEFAULT_CONFIG_URL)
        platform = _find_platform_config(payload)
        interfaces = {
            "search": text_value(platform.get("searchUrl")),
            "book": text_value(platform.get("bookUrl")),
            "play": text_value(platform.get("playUrl")),
        }
        missing = [name for name, value in interfaces.items() if not value]
        if missing:
            raise HongguoDramaError(f"红果接口配置缺少: {', '.join(missing)}")
        self.interfaces = interfaces
        return interfaces

    def _interface(self, name: str) -> str:
        if not self.interfaces:
            self.refresh_interfaces()
        return self.interfaces[name]

    def search(self, title: str, page: int = 1) -> list[dict[str, Any]]:
        query = text_value(title)
        if not query:
            raise HongguoDramaError("请输入剧名")
        payload = self._json(self._interface("search"), name=query, page=max(1, int(page)), platform="hg")
        candidates = []
        for item in _data_list(payload):
            book_id = text_value(item.get("book_id") or item.get("bookId") or item.get("id"))
            if not book_id:
                continue
            candidates.append(
                {
                    "book_id": book_id,
                    "title": text_value(item.get("name") or item.get("title") or item.get("book_name")),
                    "cover": text_value(item.get("cover") or item.get("cover_url") or item.get("image")),
                    "summary": text_value(item.get("desc") or item.get("description") or item.get("intro")),
                    "episode_count": item.get("episode_count") or item.get("total") or item.get("total_episode") or "",
                    "raw": item,
                }
            )
        return candidates

    def search_exact(self, title: str) -> list[dict[str, Any]]:
        """Return only exact normalized title matches for episode downloads."""
        query = normalize_drama_title(title)
        if not query:
            raise HongguoDramaError("请输入剧名")
        candidates: list[dict[str, Any]] = []
        page_signatures: set[tuple[str, ...]] = set()
        for page in range(1, 6):
            page_candidates = self.search(title, page=page)
            if not page_candidates:
                break
            signature = tuple(str(item.get("book_id") or "") for item in page_candidates)
            if signature in page_signatures:
                break
            page_signatures.add(signature)
            candidates.extend(page_candidates)
        matches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            if normalize_drama_title(candidate.get("title")) != query:
                continue
            book_id = str(candidate.get("book_id") or "")
            if book_id and book_id not in seen:
                matches.append(candidate)
                seen.add(book_id)
        return matches

    def episodes(self, book_id: str) -> list[dict[str, Any]]:
        value = text_value(book_id)
        if not value:
            raise HongguoDramaError("缺少 book_id")
        payload = self._json(self._interface("book"), book_id=value)
        episodes = []
        for index, item in enumerate(_data_list(payload), start=1):
            video_id = text_value(item.get("video_id") or item.get("videoId") or item.get("id"))
            if not video_id:
                continue
            paid_reason = paid_content_reason(item)
            episodes.append(
                {
                    "episode": index,
                    "video_id": video_id,
                    "title": text_value(item.get("title") or item.get("name") or item.get("episode_name")) or f"第 {index} 集",
                    "subtitle_url": text_value(item.get("subtitle_url") or item.get("caption_url") or item.get("srt_url") or item.get("vtt_url")),
                    "is_paid": bool(paid_reason),
                    "paid_reason": paid_reason,
                    "raw": item,
                }
            )
        if not episodes:
            raise HongguoDramaError("未找到可播放剧集")
        return episodes

    def play_url(self, video_id: str) -> str:
        payload = self._json(self._interface("play"), video_id=text_value(video_id))
        paid_reason = paid_content_reason(payload)
        if paid_reason:
            raise HongguoPaidContentError(paid_reason)
        if str(payload.get("code", "")) not in {"200", "0"}:
            raise HongguoDramaError(text_value(payload.get("msg") or payload.get("message")) or "播放接口返回失败")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        url = text_value(data.get("video_url") or data.get("url"))
        if not url.startswith(("http://", "https://")):
            raise HongguoDramaError("播放接口未返回有效视频地址")
        return url

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
            with httpx.Client(timeout=httpx.Timeout(60.0, read=120.0), follow_redirects=True) as client:
                with client.stream("GET", video_url, headers=headers) as response:
                    response.raise_for_status()
                    content_length = int(response.headers.get("content-length") or 0)
                    content_range = response.headers.get("content-range") or ""
                    if resumed_bytes and response.status_code == 206:
                        if not content_range.startswith(f"bytes {resumed_bytes}-"):
                            raise HongguoDramaError("下载服务返回了无效的续传范围")
                        total_bytes = int(content_range.rsplit("/", 1)[-1]) if "/" in content_range else 0
                        written = resumed_bytes
                        mode = "ab"
                    else:
                        # Some sources ignore Range requests. Restarting from a clean
                        # partial file avoids producing a corrupted video.
                        if resumed_bytes:
                            temp_path.unlink(missing_ok=True)
                        total_bytes = content_length
                        written = 0
                        mode = "wb"
                    if total_bytes and total_bytes > max_bytes:
                        raise HongguoDramaError("视频超过大小限制")
                    if on_progress:
                        on_progress(written, total_bytes)
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
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(subtitle_url)
                response.raise_for_status()
                content = response.text
        except httpx.HTTPError:
            return ""
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            body = payload.get("body") or payload.get("segments") or payload.get("data") or []
            if isinstance(body, list):
                lines = [text_value(item.get("content") or item.get("text")) for item in body if isinstance(item, dict)]
                return "\n".join(line for line in lines if line)
        lines = []
        for line in content.splitlines():
            value = line.strip()
            if not value or value.upper() == "WEBVTT" or "-->" in value or value.isdigit():
                continue
            lines.append(value)
        return "\n".join(lines)


def media_extension(video_url: str) -> str:
    suffix = Path(urlparse(video_url).path).suffix.lower()
    return suffix if suffix in {".mp4", ".mov", ".mkv", ".webm"} else ".mp4"
