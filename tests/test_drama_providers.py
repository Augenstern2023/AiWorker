# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from agents.drama_providers import (
    DownloadResult,
    DramaCandidate,
    DramaProviderRegistry,
    PlayableMedia,
    ProviderFailure,
)
from agents.drama_providers.third_party import (
    DEFAULT_CONFIG_URL,
    DOWNLOAD_SEARCH_SOURCES,
    ThirdPartyDramaProvider,
    ThirdPartyProviderConfig,
    build_third_party_registry,
)
from agents import hongguo_third_party_downloader
from agents.hongguo_third_party_downloader import (
    download_third_party_episode,
    validate_downloaded_mp4,
)


class FakeProvider:
    def __init__(
        self,
        provider_id: str,
        *,
        title: str = "目标短剧",
        delay: float = 0,
        failure: ProviderFailure | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.display_name = f"来源 {provider_id}"
        self.title = title
        self.delay = delay
        self.failure = failure

    def search_exact(self, title: str) -> list[DramaCandidate]:
        if self.delay:
            time.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        return [
            DramaCandidate(
                provider_id=self.provider_id,
                series_ref=f"{self.provider_id}-book-1",
                title=self.title,
                episode_count=12,
            )
        ]


def test_candidate_public_dict_keeps_provider_bound_compatibility_aliases():
    candidate = DramaCandidate(
        provider_id="hm",
        series_ref="hm-book-1",
        title="目标短剧",
        cover="https://image.example.test/cover.jpg",
        summary="剧情简介",
        episode_count=12,
    )

    assert candidate.public_dict() == {
        "provider_id": "hm",
        "source_platform": "hm",
        "series_ref": "hm-book-1",
        "book_id": "hm-book-1",
        "title": "目标短剧",
        "cover": "https://image.example.test/cover.jpg",
        "summary": "剧情简介",
        "episode_count": 12,
    }


def test_registry_keeps_same_title_from_different_providers_separate():
    registry = DramaProviderRegistry([FakeProvider("hg"), FakeProvider("hm")])

    report = registry.search_exact_all("目标短剧")

    assert [item.provider_id for item in report.candidates] == ["hg", "hm"]
    assert [item.series_ref for item in report.candidates] == [
        "hg-book-1",
        "hm-book-1",
    ]
    assert [item.provider_id for item in report.providers] == ["hg", "hm"]
    assert all(item.status == "available" for item in report.providers)


def test_registry_reports_one_provider_error_without_hiding_other_results():
    registry = DramaProviderRegistry(
        [
            FakeProvider(
                "hg",
                failure=ProviderFailure(
                    "provider_unavailable", "来源暂时不可用", retryable=True
                ),
            ),
            FakeProvider("hm"),
        ]
    )

    report = registry.search_exact_all("目标短剧")

    assert [item.provider_id for item in report.candidates] == ["hm"]
    assert report.providers[0].provider_id == "hg"
    assert report.providers[0].status == "error"
    assert report.providers[0].error_code == "provider_unavailable"
    assert report.providers[0].error == "来源暂时不可用"
    assert report.providers[1].status == "available"


def test_registry_preserves_registration_order_when_parallel_search_finishes_out_of_order():
    registry = DramaProviderRegistry(
        [FakeProvider("hg", delay=0.03), FakeProvider("hm")]
    )

    report = registry.search_exact_all("目标短剧")

    assert [item.provider_id for item in report.candidates] == ["hg", "hm"]
    assert [item.provider_id for item in report.providers] == ["hg", "hm"]


def test_registry_rejects_duplicate_provider_ids():
    with pytest.raises(ValueError, match="重复的 Provider ID: hg"):
        DramaProviderRegistry([FakeProvider("hg"), FakeProvider("hg")])


def test_registry_rejects_blank_provider_id():
    with pytest.raises(ValueError, match="Provider ID 不能为空"):
        DramaProviderRegistry([FakeProvider("")])


def _provider(
    provider_id: str = "hm",
    *,
    play_requires_book_id: bool = False,
    play_level: str = "",
) -> ThirdPartyDramaProvider:
    return ThirdPartyDramaProvider(
        ThirdPartyProviderConfig(
            provider_id=provider_id,
            display_name=f"来源 {provider_id}",
            search_url=f"https://api.example.test/{provider_id}/search",
            book_url=f"https://api.example.test/{provider_id}/book",
            play_url=f"https://api.example.test/{provider_id}/play",
            play_requires_book_id=play_requires_book_id,
            play_level=play_level,
        ),
        activation_code="code-1",
    )


def test_third_party_default_clients_ignore_broken_environment_proxy(
    tmp_path: Path, monkeypatch
):
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path.startswith("/config"):
                base_url = f"http://127.0.0.1:{self.server.server_port}"
                payload = {
                    "platforms": [
                        {
                            "value": source,
                            "searchUrl": f"{base_url}/search",
                            "bookUrl": f"{base_url}/book",
                            "playUrl": f"{base_url}/play",
                        }
                        for source in DOWNLOAD_SEARCH_SOURCES
                    ]
                }
                body = json.dumps(payload).encode()
                content_type = "application/json"
            elif self.path.startswith("/search"):
                body = b'{"data": []}'
                content_type = "application/json"
            elif self.path.startswith("/book"):
                body = b'{"data": [{"video_id": "video-1"}]}'
                content_type = "application/json"
            elif self.path.startswith("/play"):
                base_url = f"http://127.0.0.1:{self.server.server_port}"
                body = json.dumps(
                    {"code": 200, "data": {"video_url": f"{base_url}/media"}}
                ).encode()
                content_type = "application/json"
            else:
                body = b"direct-media"
                content_type = "video/mp4"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        registry = build_third_party_registry({"config_url": f"{base_url}/config"})
        report = registry.search_all("接口连通测试")
        provider = registry.get("hg1")
        episode = provider.episodes("book-1")[0]
        media = provider.resolve_playable(episode.series_ref, episode.episode_ref)
        result = provider.download_episode(media, tmp_path / "episode.mp4")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert len(report.providers) == len(DOWNLOAD_SEARCH_SOURCES)
    assert all(item.status == "available" for item in report.providers)
    assert result.path.read_bytes() == b"direct-media"
    assert sum(path.startswith("/search") for path in requests) == len(
        DOWNLOAD_SEARCH_SOURCES
    )


@pytest.mark.parametrize(
    ("path", "expected_message"),
    [
        ("/http-status", "来源接口返回 HTTP 503"),
        ("/invalid-json?activation=secret-code", "来源接口返回的 JSON 无效"),
    ],
)
def test_third_party_request_errors_are_safe(path, expected_message):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/http-status"):
                body = b"https://secret.example.test/?code=secret-code"
                self.send_response(503)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = b"not-json https://secret.example.test/?code=secret-code"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ProviderFailure) as captured:
            build_third_party_registry(
                {"config_url": f"http://127.0.0.1:{server.server_port}{path}"}
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert captured.value.safe_message == expected_message
    assert "secret-code" not in captured.value.safe_message
    assert "secret.example" not in captured.value.safe_message


@pytest.mark.parametrize(
    ("failure", "expected_message"),
    [
        (httpx.ConnectError("https://secret.example/?code=secret"), "来源接口连接失败"),
        (httpx.ReadTimeout("https://secret.example/?code=secret"), "来源接口请求超时"),
    ],
)
def test_third_party_connection_errors_are_classified_without_leaking_request(
    failure, expected_message, monkeypatch
):
    client_options: list[dict] = []

    class FailedClient:
        def __init__(self, **kwargs):
            client_options.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            raise failure

    monkeypatch.setattr(
        "agents.drama_providers.third_party.httpx.Client", FailedClient
    )

    with pytest.raises(ProviderFailure) as captured:
        build_third_party_registry(
            {"config_url": "https://secret.example/?code=secret"}
        )

    assert captured.value.safe_message == expected_message
    assert client_options[0]["trust_env"] is False
    assert "secret" not in captured.value.safe_message


def test_third_party_registry_loads_all_configured_sources_once():
    calls: list[tuple[str, dict]] = []

    def fake_json(url: str, params: dict) -> dict:
        calls.append((url, params))
        return {
            "platforms": [
                {
                    "value": source,
                    "searchUrl": f"https://api.example.test/{source}/search",
                    "bookUrl": f"https://api.example.test/{source}/book",
                    "playUrl": f"https://api.example.test/{source}/play",
                }
                for source in DOWNLOAD_SEARCH_SOURCES
            ]
        }

    registry = build_third_party_registry(
        {
            "config_url": "https://config.example.test/providers",
            "activation_code": "secret-code",
        },
        request_json=fake_json,
    )

    assert [item.provider_id for item in registry.providers] == list(
        DOWNLOAD_SEARCH_SOURCES
    )
    assert calls == [("https://config.example.test/providers", {})]


def test_default_dynamic_entry_resolves_current_config_center_and_sources():
    calls: list[tuple[str, dict]] = []
    expected_sources = (
        "hg1",
        "hg2",
        "hm",
        "huolong",
        "xingyadongli",
    )

    def fake_json(url: str, params: dict) -> dict:
        calls.append((url, params))
        if url == "https://mizhi.kissm.top/pc/go.php":
            return {"code": 0, "con": "http://current.example.test/con.php"}
        assert url == "http://current.example.test/con.php"
        return {
            "platforms": [
                {
                    "value": source,
                    "displayName": f"当前来源 {source}",
                    "searchUrl": f"http://current.example.test/{source}/search",
                    "bookUrl": f"http://current.example.test/{source}/book",
                    "playUrl": f"http://current.example.test/{source}/play",
                    "playUrlOptions": [
                        {
                            "url": f"http://current.example.test/{source}/play-1080",
                            "clarity": "1080p",
                            "paramKey": "level",
                        }
                    ],
                }
                for source in expected_sources
            ]
        }

    registry = build_third_party_registry({}, request_json=fake_json)

    assert DEFAULT_CONFIG_URL == "https://mizhi.kissm.top/pc/go.php"
    assert DOWNLOAD_SEARCH_SOURCES == expected_sources
    assert calls == [
        ("https://mizhi.kissm.top/pc/go.php", {}),
        ("http://current.example.test/con.php", {}),
    ]
    assert [provider.provider_id for provider in registry.providers] == list(
        expected_sources
    )
    assert [provider.display_name for provider in registry.providers] == [
        f"当前来源 {source}" for source in expected_sources
    ]
    assert registry.get("hg1").config.play_url.endswith("/hg1/play-1080")
    assert registry.get("hg1").config.play_level == "1080p"


def test_incomplete_provider_config_is_reported_without_hiding_valid_source():
    def fake_json(url: str, params: dict) -> dict:
        if url == "https://config.example.test/providers":
            return {
                "platforms": {
                    "hg1": {
                        "searchUrl": "https://api.example.test/hg1/search",
                        "bookUrl": "https://api.example.test/hg1/book",
                        "playUrl": "https://api.example.test/hg1/play",
                    },
                    "hm": {
                        "searchUrl": "https://api.example.test/hm/search",
                        "bookUrl": "https://api.example.test/hm/book",
                    },
                }
            }
        assert url == "https://api.example.test/hg1/search"
        return {"data": [{"book_id": "hg1-1", "title": "目标短剧"}]}

    registry = build_third_party_registry(
        {"config_url": "https://config.example.test/providers"},
        request_json=fake_json,
    )

    report = registry.search_exact_all("目标短剧")

    assert [item.provider_id for item in report.providers] == list(
        DOWNLOAD_SEARCH_SOURCES
    )
    assert [item.provider_id for item in report.candidates] == ["hg1"]
    hm_status = next(item for item in report.providers if item.provider_id == "hm")
    assert hm_status.status == "error"
    assert hm_status.error_code == "configuration_error"


def test_third_party_request_keeps_configured_platform_without_overriding_it():
    provider = _provider("hg")

    request_url = provider.request_url(
        "https://api.example.test/api.php?platform=wpf11&action=search_hg",
        name="目标短剧",
        page=1,
        platform="hg",
    )

    pairs = parse_qsl(urlsplit(request_url).query, keep_blank_values=True)
    assert pairs.count(("platform", "wpf11")) == 1
    assert ("platform", "hg") not in pairs
    assert ("code", "code-1") in pairs
    assert ("action", "search_hg") in pairs


def test_third_party_search_returns_provider_bound_candidate(monkeypatch):
    provider = _provider("hm")
    monkeypatch.setattr(
        provider,
        "_json",
        lambda url, **params: {
            "data": [
                {
                    "book_id": "hm-book-1",
                    "name": "目标短剧",
                    "cover": "https://image.example.test/cover.jpg",
                    "desc": "剧情简介",
                    "total": 18,
                }
            ]
        },
    )

    candidate = provider.search("目标短剧")[0]

    assert candidate.provider_id == "hm"
    assert candidate.series_ref == "hm-book-1"
    assert candidate.title == "目标短剧"
    assert candidate.episode_count == 18


def test_third_party_exact_search_supports_title_containment_for_four_chars(
    monkeypatch,
):
    provider = _provider("hg1")
    monkeypatch.setattr(
        provider,
        "_json",
        lambda url, **params: {
            "data": [
                {"book_id": "contained", "title": "目标短剧续篇"},
                {"book_id": "exact", "title": "目标 短剧"},
                {"book_id": "too-short", "title": "目标短"},
                {"book_id": "unrelated", "title": "另一部短剧"},
            ]
        },
    )

    matches = provider.search_exact("目标短剧")

    assert [item.series_ref for item in matches] == ["contained", "exact"]


def test_third_party_title_containment_requires_four_normalized_chars(monkeypatch):
    provider = _provider("hg1")
    monkeypatch.setattr(
        provider,
        "_json",
        lambda url, **params: {
            "data": [
                {"book_id": "reverse-contained", "title": "目标短剧"},
                {"book_id": "short-contained", "title": "目标剧续篇"},
            ]
        },
    )

    assert [
        item.series_ref for item in provider.search_exact("目标短剧完整版")
    ] == ["reverse-contained"]
    assert provider.search_exact("目标剧") == []


def test_third_party_provider_keeps_episode_identity_and_required_book_id(
    monkeypatch,
):
    provider = _provider("hm", play_requires_book_id=True)
    calls: list[tuple[str, dict]] = []

    def fake_json(url: str, **params) -> dict:
        calls.append((url, params))
        if url.endswith("/book"):
            return {
                "data": [
                    {
                        "video_id": "hm-video-1",
                        "title": "第一集",
                        "subtitle_url": "https://subtitle.example.test/1.vtt",
                    }
                ]
            }
        return {
            "code": 200,
            "data": {"video_url": "https://video.example.test/1.mp4"},
        }

    monkeypatch.setattr(provider, "_json", fake_json)

    episode = provider.episodes("hm-book-1")[0]
    media = provider.resolve_playable(episode.series_ref, episode.episode_ref)

    assert episode.provider_id == "hm"
    assert episode.series_ref == "hm-book-1"
    assert episode.episode_ref == "hm-video-1"
    assert media.provider_id == "hm"
    assert calls == [
        (
            "https://api.example.test/hm/book",
            {"book_id": "hm-book-1"},
        ),
        (
            "https://api.example.test/hm/play",
            {"video_id": "hm-video-1", "book_id": "hm-book-1"},
        ),
    ]


def test_third_party_provider_forwards_configured_play_level(monkeypatch):
    provider = _provider("hg1", play_level="1080p")
    calls: list[dict] = []

    def fake_json(url: str, **params) -> dict:
        calls.append(params)
        return {
            "code": 200,
            "data": {"video_url": "https://video.example.test/1.mp4"},
        }

    monkeypatch.setattr(provider, "_json", fake_json)

    provider.resolve_playable("book-1", "video-1")

    assert calls == [{"video_id": "video-1", "level": "1080p"}]


def test_third_party_provider_marks_paid_episode_and_rejects_paid_play(
    monkeypatch,
):
    provider = _provider("hg")

    def fake_json(url: str, **params) -> dict:
        if url.endswith("/book"):
            return {
                "data": [
                    {
                        "video_id": "paid-video",
                        "title": "第一集",
                        "is_paid_content": True,
                    }
                ]
            }
        return {"code": 403, "msg": "该剧集需要付费解锁"}

    monkeypatch.setattr(provider, "_json", fake_json)

    episode = provider.episodes("book-1")[0]
    assert episode.is_paid is True
    with pytest.raises(ProviderFailure) as captured:
        provider.resolve_playable("book-1", "paid-video")
    assert captured.value.code == "paid_content"


def test_third_party_download_resumes_partial_file(tmp_path: Path):
    requests: list[dict] = []
    progress: list[tuple[int, int]] = []

    class FakeResponse:
        headers = {"content-length": "3", "content-range": "bytes 3-5/6"}
        status_code = 206

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"def"

    class FakeHttpClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, headers=None):
            requests.append({"method": method, "url": url, "headers": headers or {}})
            return FakeResponse()

    provider = ThirdPartyDramaProvider(
        _provider("hg").config,
        client_factory=lambda: FakeHttpClient(),
    )
    target = tmp_path / "video.mp4"
    target.with_suffix(".mp4.part").write_bytes(b"abc")

    result = provider.download_episode(
        PlayableMedia(
            provider_id="hg",
            series_ref="book-1",
            episode_ref="video-1",
            url="https://video.example.test/1.mp4",
        ),
        target,
        on_progress=lambda current, total: progress.append((current, total)),
    )

    assert result.bytes == 6
    assert target.read_bytes() == b"abcdef"
    assert not target.with_suffix(".mp4.part").exists()
    assert requests[0]["headers"]["Range"] == "bytes=3-"
    assert progress[-1] == (6, 6)


def test_validate_downloaded_mp4_skips_transcode_for_h264_aac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "video.mp4"
    target.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 2048)
    commands: list[list[str]] = []

    monkeypatch.setattr(
        hongguo_third_party_downloader.shutil,
        "which",
        lambda command: command,
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {"index": 0, "codec_type": "video", "codec_name": "h264"},
                        {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(hongguo_third_party_downloader.subprocess, "run", fake_run)

    validate_downloaded_mp4(target)

    assert len(commands) == 1
    assert commands[0][0] == "ffprobe"


def test_validate_downloaded_mp4_transcodes_incompatible_codecs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "video.mp4"
    original = b"\x00\x00\x00\x18ftypmp42" + b"x" * 2048
    target.write_bytes(original)
    commands: list[list[str]] = []

    monkeypatch.setattr(
        hongguo_third_party_downloader.shutil,
        "which",
        lambda command: command,
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "index": 0,
                                "codec_type": "video",
                                "codec_name": "hevc",
                            },
                            {
                                "index": 1,
                                "codec_type": "audio",
                                "codec_name": "mp3",
                            },
                        ]
                    }
                ),
                stderr="",
            )
        Path(command[-1]).write_bytes(b"browser-compatible")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hongguo_third_party_downloader.subprocess, "run", fake_run)

    validate_downloaded_mp4(target)

    assert target.read_bytes() == b"browser-compatible"
    assert len(commands) == 2
    ffmpeg_command = commands[1]
    assert ffmpeg_command[0] == "ffmpeg"
    assert ffmpeg_command[ffmpeg_command.index("-c:v") + 1] == "libx264"
    assert ffmpeg_command[ffmpeg_command.index("-pix_fmt") + 1] == "yuv420p"
    assert ffmpeg_command[ffmpeg_command.index("-c:a") + 1] == "aac"
    assert ffmpeg_command[ffmpeg_command.index("-movflags") + 1] == "+faststart"
    assert not target.with_name("video.browser.mp4").exists()


def test_third_party_download_retries_invalid_mp4_and_keeps_final_part(
    tmp_path: Path,
):
    attempts: list[str] = []

    class InvalidMediaProvider:
        provider_id = "hm"

        def resolve_playable(self, series_ref, episode_ref):
            attempts.append("resolve")
            return PlayableMedia(
                provider_id="hm",
                series_ref=series_ref,
                episode_ref=episode_ref,
                url="https://video.example.test/invalid.mp4",
            )

        def download_episode(self, media, target, **kwargs):
            attempts.append("download")
            path = Path(target)
            path.write_bytes(b"not-an-mp4" + b"x" * 2048)
            return DownloadResult("hm", path, path.stat().st_size, {})

    target = tmp_path / "video.mp4"

    with pytest.raises(ProviderFailure) as captured:
        download_third_party_episode(
            InvalidMediaProvider(), "hm-series", "hm-episode", target
        )

    assert captured.value.code == "transfer_failed"
    assert attempts == ["resolve", "download", "resolve", "download"]
    assert not target.exists()
    assert target.with_suffix(".mp4.part").exists()
