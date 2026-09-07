# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from agents.drama_download_policy import paid_content_reason
from agents.drama_providers.models import (
    DownloadResult,
    DramaCandidate,
    DramaEpisode,
    PlayableMedia,
    ProviderFailure,
    ProviderHealth,
)
from agents.drama_providers.registry import DramaProviderRegistry
from agents.hongguo_capture import normalize_title


DEFAULT_CONFIG_URL = "https://mizhi.kissm.top/pc/go.php"
LEGACY_CONFIG_URL = "https://mizhi.kissm.top/pc/con.php"
DOWNLOAD_SEARCH_SOURCES = (
    "hg1",
    "hg2",
    "hm",
    "huolong",
    "xingyadongli",
)
PROVIDER_DISPLAY_NAMES = {
    "hg1": "红短剧",
    "hg2": "红漫剧",
    "hm": "河马短剧",
    "huolong": "火龙漫剧",
    "xingyadongli": "星芽/东梨短剧",
}

JsonRequester = Callable[[str, dict[str, Any]], dict[str, Any]]
HttpClientFactory = Callable[[], Any]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _extract_embedded_query_value(url: str, parameter: str) -> tuple[str, str]:
    """Extract a private URL value without normalizing the remaining signed query."""

    parameter_name = _text(parameter)
    if not parameter_name:
        return url, ""
    parsed = urlsplit(url)
    expected = parameter_name.casefold()
    kept: list[str] = []
    matched = False
    value = ""
    for segment in parsed.query.split("&"):
        raw_name, separator, raw_value = segment.partition("=")
        if unquote(raw_name).casefold() != expected:
            kept.append(segment)
            continue
        matched = True
        if not value and separator:
            value = unquote(raw_value).strip()
    if not matched or not value:
        return url, ""
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "&".join(kept), parsed.fragment)
    ), value


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


def _find_platform_configs(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProviderFailure("configuration_error", "配置中心返回格式无效")
    containers = [payload]
    if isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    for container in containers:
        platforms = container.get("platforms")
        if isinstance(platforms, dict):
            return {
                _text(key).lower(): value
                for key, value in platforms.items()
                if _text(key) and isinstance(value, dict)
            }
        if isinstance(platforms, list):
            result: dict[str, dict[str, Any]] = {}
            for item in platforms:
                if not isinstance(item, dict):
                    continue
                provider_id = _text(
                    item.get("value")
                    or item.get("platform")
                    or item.get("key")
                    or item.get("name")
                ).lower()
                if provider_id:
                    result[provider_id] = item
            if result:
                return result
    raise ProviderFailure("configuration_error", "配置中心未提供剧目接口配置")


def _discovered_config_url(payload: Any, entry_url: str) -> str:
    if not isinstance(payload, dict):
        return ""
    containers = [payload]
    if isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    for container in containers:
        value = _text(
            container.get("con")
            or container.get("config_url")
            or container.get("configUrl")
        )
        if not value:
            continue
        resolved = urljoin(entry_url, value)
        parsed = urlsplit(resolved)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderFailure(
                "configuration_error", "动态入口返回的配置中心地址无效"
            )
        return resolved
    return ""


def _load_platform_configs(
    entry_url: str, requester: JsonRequester
) -> dict[str, dict[str, Any]]:
    payload = requester(entry_url, {})
    try:
        return _find_platform_configs(payload)
    except ProviderFailure as direct_failure:
        config_url = _discovered_config_url(payload, entry_url)
        if not config_url:
            raise direct_failure
    return _find_platform_configs(requester(config_url, {}))


def _default_json_requester(timeout: float) -> JsonRequester:
    def request(url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(
                timeout=timeout, follow_redirects=True, trust_env=False
            ) as client:
                response = client.get(url, params=params or None)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderFailure(
                "provider_unavailable",
                f"来源接口返回 HTTP {exc.response.status_code}",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderFailure(
                "provider_unavailable", "来源接口请求超时", retryable=True
            ) from exc
        except httpx.ConnectError as exc:
            raise ProviderFailure(
                "provider_unavailable", "来源接口连接失败", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure(
                "provider_unavailable", "来源接口请求失败", retryable=True
            ) from exc
        except ValueError as exc:
            raise ProviderFailure(
                "provider_unavailable", "来源接口返回的 JSON 无效"
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderFailure("provider_unavailable", "来源接口未返回 JSON 对象")
        return payload

    return request


@dataclass(frozen=True)
class ThirdPartyProviderConfig:
    provider_id: str
    display_name: str
    search_url: str
    book_url: str
    play_url: str
    play_requires_book_id: bool = False
    play_level: str = ""
    play_level_param: str = "level"
    play_url_tag_param: str = ""


class UnavailableThirdPartyDramaProvider:
    """Registered provider slot whose configuration is missing or incomplete."""

    def __init__(self, provider_id: str, message: str) -> None:
        self.provider_id = provider_id
        self.display_name = PROVIDER_DISPLAY_NAMES[provider_id]
        self.message = message

    def _raise(self) -> NoReturn:
        raise ProviderFailure("configuration_error", self.message)

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id,
            display_name=self.display_name,
            status="error",
            message=self.message,
        )

    def search(self, title: str, page: int = 1) -> list[DramaCandidate]:
        self._raise()

    def search_exact(self, title: str) -> list[DramaCandidate]:
        self._raise()

    def episodes(self, series_ref: str) -> list[DramaEpisode]:
        self._raise()

    def resolve_playable(
        self, series_ref: str, episode_ref: str
    ) -> PlayableMedia:
        self._raise()

    def download_episode(
        self,
        media: PlayableMedia,
        target: Path,
        *,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        on_progress: Callable[[int, int], None] | None = None,
        wait_if_paused: Callable[[], None] | None = None,
    ) -> DownloadResult:
        self._raise()


class ThirdPartyDramaProvider:
    def __init__(
        self,
        config: ThirdPartyProviderConfig,
        *,
        activation_code: str = "",
        timeout: float = 20.0,
        request_json: JsonRequester | None = None,
        client_factory: HttpClientFactory | None = None,
    ) -> None:
        self.config = config
        self.provider_id = config.provider_id
        self.display_name = config.display_name
        self.activation_code = _text(activation_code)
        self.timeout = float(timeout)
        self._request_json = request_json
        self._client_factory = client_factory

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id,
            display_name=self.display_name,
            status="available",
        )

    def _params(self, **params: Any) -> dict[str, Any]:
        result = {
            key: value for key, value in params.items() if value not in (None, "")
        }
        if self.activation_code:
            result["code"] = self.activation_code
        return result

    def request_url(self, url: str, **params: Any) -> str:
        parsed = urlsplit(url)
        query = list(parse_qsl(parsed.query, keep_blank_values=True))
        existing_keys = {key for key, _ in query}
        for key, value in self._params(**params).items():
            if key not in existing_keys:
                query.append((key, str(value)))
                existing_keys.add(key)
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )

    def _json(self, url: str, **params: Any) -> dict[str, Any]:
        clean_params = self._params(**params)
        if self._request_json is not None:
            payload = self._request_json(url, clean_params)
            if not isinstance(payload, dict):
                raise ProviderFailure(
                    "provider_unavailable", "来源接口未返回 JSON 对象"
                )
            return payload
        request_url = self.request_url(url, **params)
        return _default_json_requester(self.timeout)(request_url, {})

    def search(self, title: str, page: int = 1) -> list[DramaCandidate]:
        query = _text(title)
        if not query:
            raise ProviderFailure("series_not_found", "请输入剧名")
        payload = self._json(
            self.config.search_url,
            name=query,
            page=max(1, int(page)),
        )
        candidates: list[DramaCandidate] = []
        seen: set[str] = set()
        for item in _data_list(payload):
            series_ref = _text(
                item.get("book_id") or item.get("bookId") or item.get("id")
            )
            if not series_ref or series_ref in seen:
                continue
            seen.add(series_ref)
            candidates.append(
                DramaCandidate(
                    provider_id=self.provider_id,
                    series_ref=series_ref,
                    title=_text(
                        item.get("name")
                        or item.get("title")
                        or item.get("book_name")
                    ),
                    cover=_text(
                        item.get("cover")
                        or item.get("cover_url")
                        or item.get("image")
                    ),
                    summary=_text(
                        item.get("desc")
                        or item.get("description")
                        or item.get("intro")
                    ),
                    episode_count=item.get("episode_count")
                    or item.get("total")
                    or item.get("total_episode")
                    or "",
                )
            )
        return candidates

    def search_exact(self, title: str) -> list[DramaCandidate]:
        expected = normalize_title(title)
        if not expected:
            raise ProviderFailure("series_not_found", "请输入剧名")
        return [
            candidate
            for candidate in self.search(title, page=1)
            if (
                (candidate_title := normalize_title(candidate.title)) == expected
                or (
                    len(expected) >= 4
                    and len(candidate_title) >= 4
                    and (expected in candidate_title or candidate_title in expected)
                )
            )
        ]

    def episodes(self, series_ref: str) -> list[DramaEpisode]:
        value = _text(series_ref)
        if not value:
            raise ProviderFailure("series_not_found", "缺少剧目标识")
        payload = self._json(self.config.book_url, book_id=value)
        episodes: list[DramaEpisode] = []
        for index, item in enumerate(_data_list(payload), start=1):
            episode_ref = _text(
                item.get("video_id") or item.get("videoId") or item.get("id")
            )
            if not episode_ref:
                continue
            paid_reason = paid_content_reason(item)
            episodes.append(
                DramaEpisode(
                    provider_id=self.provider_id,
                    series_ref=value,
                    episode_ref=episode_ref,
                    episode=index,
                    title=_text(
                        item.get("title")
                        or item.get("name")
                        or item.get("episode_name")
                    )
                    or f"第 {index} 集",
                    subtitle_url=_text(
                        item.get("subtitle_url")
                        or item.get("caption_url")
                        or item.get("srt_url")
                        or item.get("vtt_url")
                    ),
                    is_paid=bool(paid_reason),
                    paid_reason=paid_reason,
                )
            )
        if not episodes:
            raise ProviderFailure("episode_not_found", "未找到可播放剧集")
        return episodes

    def resolve_playable(
        self, series_ref: str, episode_ref: str
    ) -> PlayableMedia:
        params: dict[str, Any] = {"video_id": _text(episode_ref)}
        if self.config.play_requires_book_id:
            params["book_id"] = _text(series_ref)
        if self.config.play_level:
            params[self.config.play_level_param or "level"] = self.config.play_level
        payload = self._json(self.config.play_url, **params)
        paid_reason = paid_content_reason(payload)
        if paid_reason:
            raise ProviderFailure("paid_content", paid_reason)
        if str(payload.get("code", "")) not in {"200", "0"}:
            raise ProviderFailure(
                "provider_unavailable",
                _text(payload.get("msg") or payload.get("message"))
                or "播放接口返回失败",
            )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        url = _text(data.get("video_url") or data.get("url"))
        if not url.startswith(("http://", "https://")):
            raise ProviderFailure("play_url_expired", "播放接口未返回有效视频地址")
        url, decrypt_key = _extract_embedded_query_value(
            url, self.config.play_url_tag_param
        )
        if decrypt_key and re.fullmatch(r"[0-9a-fA-F]{32}", decrypt_key) is None:
            raise ProviderFailure(
                "decryption_key_invalid", "播放接口返回的 CENC 解密密钥格式无效"
            )
        return PlayableMedia(
            provider_id=self.provider_id,
            series_ref=_text(series_ref),
            episode_ref=_text(episode_ref),
            url=url,
            decrypt_key=decrypt_key,
        )

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(
            timeout=httpx.Timeout(60.0, read=120.0),
            follow_redirects=True,
            trust_env=False,
        )

    def download_episode(
        self,
        media: PlayableMedia,
        target: Path,
        *,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        on_progress: Callable[[int, int], None] | None = None,
        wait_if_paused: Callable[[], None] | None = None,
    ) -> DownloadResult:
        if media.provider_id != self.provider_id:
            raise ProviderFailure(
                "configuration_error", "播放信息与所选来源不一致"
            )
        target_path = Path(target)
        temporary = target_path.with_suffix(target_path.suffix + ".part")
        resumed_bytes = temporary.stat().st_size if temporary.exists() else 0
        headers = {"Range": f"bytes={resumed_bytes}-"} if resumed_bytes else {}
        try:
            with self._client() as client:
                with client.stream("GET", media.url, headers=headers) as response:
                    response.raise_for_status()
                    content_length = int(response.headers.get("content-length") or 0)
                    content_range = response.headers.get("content-range") or ""
                    if resumed_bytes and response.status_code == 206:
                        if not content_range.startswith(f"bytes {resumed_bytes}-"):
                            raise ProviderFailure(
                                "transfer_failed", "下载服务返回了无效的续传范围"
                            )
                        total_bytes = (
                            int(content_range.rsplit("/", 1)[-1])
                            if "/" in content_range
                            else 0
                        )
                        written, mode = resumed_bytes, "ab"
                    else:
                        if resumed_bytes:
                            temporary.unlink(missing_ok=True)
                        total_bytes, written, mode = content_length, 0, "wb"
                    if total_bytes and total_bytes > max_bytes:
                        raise ProviderFailure("transfer_failed", "视频超过大小限制")
                    if on_progress:
                        on_progress(written, total_bytes)
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with temporary.open(mode) as output:
                        for chunk in response.iter_bytes():
                            if wait_if_paused:
                                wait_if_paused()
                            written += len(chunk)
                            if written > max_bytes:
                                raise ProviderFailure(
                                    "transfer_failed", "视频超过大小限制"
                                )
                            output.write(chunk)
                            if on_progress:
                                on_progress(written, total_bytes)
        except ProviderFailure:
            raise
        except (OSError, httpx.HTTPError) as exc:
            raise ProviderFailure(
                "transfer_failed", "下载视频失败", retryable=True
            ) from exc
        temporary.replace(target_path)
        return DownloadResult(
            provider_id=self.provider_id,
            path=target_path,
            bytes=written,
            metadata={
                "series_ref": media.series_ref,
                "episode_ref": media.episode_ref,
            },
        )


def build_third_party_registry(
    settings: dict[str, Any],
    *,
    timeout: float = 20.0,
    request_json: JsonRequester | None = None,
) -> DramaProviderRegistry:
    config_url = _text(settings.get("config_url")) or DEFAULT_CONFIG_URL
    requester = request_json or _default_json_requester(timeout)
    platforms = _load_platform_configs(config_url, requester)
    providers: list[Any] = []
    for provider_id in DOWNLOAD_SEARCH_SOURCES:
        raw = platforms.get(provider_id)
        if not raw:
            providers.append(
                UnavailableThirdPartyDramaProvider(
                    provider_id, "配置中心未提供该来源配置"
                )
            )
            continue
        options = raw.get("playUrlOptions")
        play_option: dict[str, Any] = {}
        if isinstance(options, list):
            valid_options = [item for item in options if isinstance(item, dict)]
            play_option = next(
                (
                    item
                    for item in valid_options
                    if _text(item.get("clarity")).casefold() == "1080p"
                ),
                valid_options[0] if valid_options else {},
            )
        config = ThirdPartyProviderConfig(
            provider_id=provider_id,
            display_name=_text(raw.get("displayName"))
            or PROVIDER_DISPLAY_NAMES[provider_id],
            search_url=_text(raw.get("searchUrl")),
            book_url=_text(raw.get("bookUrl")),
            play_url=_text(play_option.get("url") or raw.get("playUrl")),
            play_requires_book_id=bool(raw.get("playRequiresBookId")),
            play_level=_text(play_option.get("clarity")),
            play_level_param=_text(play_option.get("paramKey")) or "level",
            play_url_tag_param=_text(raw.get("playUrlTagParam")),
        )
        missing = [
            name
            for name, value in (
                ("searchUrl", config.search_url),
                ("bookUrl", config.book_url),
                ("playUrl", config.play_url),
            )
            if not value
        ]
        if missing:
            providers.append(
                UnavailableThirdPartyDramaProvider(
                    provider_id, f"来源配置缺少：{', '.join(missing)}"
                )
            )
            continue
        providers.append(
            ThirdPartyDramaProvider(
                config,
                activation_code=_text(settings.get("activation_code")),
                timeout=timeout,
                request_json=request_json,
            )
        )
    return DramaProviderRegistry(providers)
