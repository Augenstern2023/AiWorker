# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_hongguo_drama.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import json
import io
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

legacy_remote_contract = pytest.mark.skip(
    reason="旧远程红果接口契约已由本地抓包测试取代"
)

import WebGUI
from agents import hongguo_drama
from agents.drama_providers import (
    DownloadResult,
    DramaCandidate,
    DramaEpisode,
    PlayableMedia,
    ProviderFailure,
    ProviderSearchReport,
    ProviderSearchStatus,
)
from agents.hongguo_drama import HongguoDramaClient, HongguoDramaError, HongguoPaidContentError, load_settings, public_settings, save_settings
from agents import douyin_drama_fallback_worker, hongguo_drama_download_worker, hongguo_drama_worker, hongguo_third_party_downloader
from core.job import CrawlJob
from runtime.worker_launcher import build_worker_command
from workflow import SYSTEM_ACTOR


def test_refresh_third_party_providers_probes_every_source_without_candidates(
    tmp_path, monkeypatch
):
    candidate = DramaCandidate("hg", "book-1", "不应返回的候选")

    class FakeRegistry:
        providers = (object(), object())

        def search_all(self, title):
            assert title == "接口连通测试"
            return ProviderSearchReport(
                candidates=(candidate,),
                providers=(
                    ProviderSearchStatus(
                        "hg", "红果源", "available", 12, candidates=(candidate,)
                    ),
                    ProviderSearchStatus(
                        "hm",
                        "HM 源",
                        "error",
                        18,
                        error_code="provider_unavailable",
                        error="来源接口连接失败",
                    ),
                ),
            )

    monkeypatch.setattr(
        hongguo_drama,
        "build_third_party_registry",
        lambda settings, timeout=20: FakeRegistry(),
    )
    client = HongguoDramaClient(
        {
            "capture_root": str(tmp_path),
            "capture_service_url": "http://127.0.0.1:8776",
            "wait_timeout_seconds": 600,
        }
    )

    result = client.refresh_third_party_providers()

    assert result["provider_count"] == 2
    assert result["available_count"] == 1
    assert result["providers"] == [
        {
            "provider_id": "hg",
            "display_name": "红果源",
            "status": "available",
            "elapsed_ms": 12,
            "error_code": "",
            "error": "",
        },
        {
            "provider_id": "hm",
            "display_name": "HM 源",
            "status": "error",
            "elapsed_ms": 18,
            "error_code": "provider_unavailable",
            "error": "来源接口连接失败",
        },
    ]
    assert "candidates" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_drama_interface_test_reports_three_successful_checks(
    tmp_path, monkeypatch
):
    class FakeClient:
        def __init__(self, settings):
            self.settings = {
                "capture_root": str(tmp_path),
                "capture_service_url": "http://127.0.0.1:8776",
                "wait_timeout_seconds": 600,
                "activation_code": "secret-code",
            }

        def refresh_interfaces(self):
            return {"raw_file_count": 2, "series_count": 1}

        def refresh_third_party_providers(self):
            return {
                "provider_count": 2,
                "available_count": 2,
                "providers": [
                    {
                        "provider_id": "hg",
                        "display_name": "红果源",
                        "status": "available",
                        "elapsed_ms": 8,
                        "error_code": "",
                        "error": "",
                    },
                    {
                        "provider_id": "hm",
                        "display_name": "HM 源",
                        "status": "available",
                        "elapsed_ms": 9,
                        "error_code": "",
                        "error": "",
                    },
                ],
            }

    class HealthyCaptureClient:
        def __init__(self, base_url):
            assert base_url == "http://127.0.0.1:8776"

        def health(self):
            return {"status": "ok"}

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(WebGUI, "HongguoVideoCaptureClient", HealthyCaptureClient)

    response = await WebGUI.test_hongguo_drama_interface(
        WebGUI.HongguoDramaSettingsRequest(
            config_url="https://secret.example.test/?code=secret-code"
        )
    )

    assert response["status"] == "success"
    assert [item["id"] for item in response["checks"]] == [
        "capture_directory",
        "capture_service",
        "third_party",
    ]
    assert all(item["status"] == "success" for item in response["checks"])
    assert response["capture_service_status"] == "ok"
    assert response["third_party"]["available_count"] == 2
    serialized = json.dumps(response, ensure_ascii=False)
    assert "secret-code" not in serialized
    assert "secret.example" not in serialized


@pytest.mark.asyncio
async def test_drama_interface_test_keeps_other_checks_when_capture_service_fails(
    tmp_path, monkeypatch
):
    calls: list[str] = []

    class FakeClient:
        def __init__(self, settings):
            self.settings = {
                "capture_root": str(tmp_path),
                "capture_service_url": "http://127.0.0.1:8776",
                "wait_timeout_seconds": 600,
                "activation_code": "",
            }

        def refresh_interfaces(self):
            calls.append("directory")
            return {"raw_file_count": 1, "series_count": 1}

        def refresh_third_party_providers(self):
            calls.append("third_party")
            return {
                "provider_count": 2,
                "available_count": 1,
                "providers": [
                    {
                        "provider_id": "hg",
                        "display_name": "红果源",
                        "status": "available",
                        "elapsed_ms": 5,
                        "error_code": "",
                        "error": "",
                    },
                    {
                        "provider_id": "hm",
                        "display_name": "HM 源",
                        "status": "error",
                        "elapsed_ms": 7,
                        "error_code": "provider_unavailable",
                        "error": "来源接口连接失败",
                    },
                ],
            }

    class FailedCaptureClient:
        def __init__(self, base_url):
            pass

        def health(self):
            calls.append("service")
            raise RuntimeError("https://secret.example.test/?code=secret-code")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(WebGUI, "HongguoVideoCaptureClient", FailedCaptureClient)

    response = await WebGUI.test_hongguo_drama_interface(
        WebGUI.HongguoDramaSettingsRequest()
    )

    assert response["status"] == "partial"
    assert calls == ["directory", "service", "third_party"]
    assert [item["status"] for item in response["checks"]] == [
        "success",
        "error",
        "partial",
    ]
    assert response["capture_service_status"] == "error"
    assert "HM 源" in response["checks"][2]["message"]
    serialized = json.dumps(response, ensure_ascii=False)
    assert "secret-code" not in serialized
    assert "secret.example" not in serialized


@pytest.mark.asyncio
async def test_drama_interface_test_returns_200_when_all_completed_checks_fail(
    tmp_path, monkeypatch
):
    class FailedClient:
        def __init__(self, settings):
            self.settings = {
                "capture_root": str(tmp_path),
                "capture_service_url": "http://127.0.0.1:8776",
                "wait_timeout_seconds": 600,
                "activation_code": "",
            }

        def refresh_interfaces(self):
            raise HongguoDramaError("D:\\secret\\capture 不存在")

        def refresh_third_party_providers(self):
            raise HongguoDramaError(
                "https://secret.example.test/?code=secret-code"
            )

    class FailedCaptureClient:
        def __init__(self, base_url):
            pass

        def health(self):
            return {"status": "starting"}

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FailedClient)
    monkeypatch.setattr(WebGUI, "HongguoVideoCaptureClient", FailedCaptureClient)

    response = await WebGUI.test_hongguo_drama_interface(
        WebGUI.HongguoDramaSettingsRequest()
    )

    assert isinstance(response, dict)
    assert response["status"] == "error"
    assert all(item["status"] == "error" for item in response["checks"])
    serialized = json.dumps(response, ensure_ascii=False)
    assert "secret-code" not in serialized
    assert "secret.example" not in serialized
    assert "D:\\secret" not in serialized


@pytest.mark.asyncio
async def test_drama_interface_test_returns_400_only_for_invalid_settings(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)

    response = await WebGUI.test_hongguo_drama_interface(
        WebGUI.HongguoDramaSettingsRequest(capture_root="relative/path")
    )

    assert response.status_code == 400
    assert json.loads(response.body)["status"] == "error"


def test_paid_content_policy_requires_explicit_provider_signal():
    from agents.drama_download_policy import paid_content_reason

    assert paid_content_reason({"desc": "VIP会员短剧完整版"}) == ""
    assert paid_content_reason({"is_paid_content": True}) == "接口标记 is_paid_content"
    assert paid_content_reason({"series_paid_info": {"price": 199}}) == "接口标记 series_paid_info.price=199"


def test_hongguo_client_routes_remote_search_and_episodes_to_selected_provider(
    tmp_path, monkeypatch
):
    candidate = DramaCandidate(
        provider_id="hm",
        series_ref="hm-book-1",
        title="目标短剧",
        episode_count=12,
    )
    episode = DramaEpisode(
        provider_id="hm",
        series_ref="hm-book-1",
        episode_ref="hm-video-1",
        episode=1,
        title="第一集",
    )

    class FakeProvider:
        def episodes(self, series_ref):
            assert series_ref == "hm-book-1"
            return [episode]

    class FakeRegistry:
        def search_exact_all(self, title):
            assert title == "目标短剧"
            return ProviderSearchReport(
                candidates=(candidate,),
                providers=(
                    ProviderSearchStatus(
                        provider_id="hm",
                        display_name="HM 源",
                        status="available",
                        elapsed_ms=5,
                        candidates=(candidate,),
                    ),
                ),
            )

        def get(self, provider_id):
            assert provider_id == "hm"
            return FakeProvider()

    monkeypatch.setattr(
        hongguo_drama,
        "build_third_party_registry",
        lambda settings, timeout=20: FakeRegistry(),
    )
    client = HongguoDramaClient(
        {
            "config_url": "https://config.example.test",
            "activation_code": "secret",
            "capture_root": str(tmp_path),
            "capture_service_url": "http://127.0.0.1:8776",
            "wait_timeout_seconds": 600,
        }
    )

    matches = client.search_exact_all_sources("目标短剧")
    episodes = client.episodes("hm-book-1", "hm")

    assert matches == [candidate.public_dict()]
    assert episodes == [episode.public_dict()]
    assert client.search_provider_groups("目标短剧")["providers"][0][
        "provider_id"
    ] == "hm"


def test_hongguo_suggest_queries_providers_and_deduplicates_normalized_titles(
    tmp_path, monkeypatch
):
    third_party = DramaCandidate(
        provider_id="hm", series_ref="hm-1", title="目标 短剧"
    )
    duplicate = DramaCandidate(
        provider_id="hg", series_ref="hg-1", title="目标短剧"
    )
    capture_only = DramaCandidate(
        provider_id="hongguo_capture", series_ref="capture-2", title="目标短剧续篇"
    )

    class FakeRegistry:
        def search_all(self, title):
            assert title == "目标"
            return ProviderSearchReport(
                candidates=(third_party, duplicate), providers=()
            )

    class FakeCatalog:
        def suggest(self, title, limit):
            assert (title, limit) == ("目标", 8)
            return [duplicate, capture_only]

    monkeypatch.setattr(
        hongguo_drama,
        "build_third_party_registry",
        lambda settings, timeout=20: FakeRegistry(),
    )
    client = HongguoDramaClient(
        {
            "capture_root": str(tmp_path),
            "capture_service_url": "http://127.0.0.1:8776",
            "wait_timeout_seconds": 600,
        }
    )
    client.catalog = FakeCatalog()

    suggestions = client.suggest_all("目标", 8)

    assert [(item["provider_id"], item["title"]) for item in suggestions] == [
        ("hm", "目标 短剧"),
        ("hongguo_capture", "目标短剧续篇"),
    ]


@pytest.mark.asyncio
async def test_download_search_api_returns_provider_groups_and_flat_candidates(
    tmp_path, monkeypatch
):
    class FakeClient:
        def __init__(self, settings):
            pass

        def search_provider_groups(self, title):
            assert title == "目标短剧"
            candidate = {
                "provider_id": "hm",
                "source_platform": "hm",
                "series_ref": "hm-book-1",
                "book_id": "hm-book-1",
                "title": title,
                "cover": "",
                "summary": "",
                "episode_count": 12,
            }
            return {
                "candidates": [candidate],
                "providers": [
                    {
                        "provider_id": "hm",
                        "display_name": "HM 源",
                        "status": "available",
                        "elapsed_ms": 8,
                        "candidates": [candidate],
                        "error_code": "",
                        "error": "",
                    },
                    {
                        "provider_id": "hg",
                        "display_name": "红果源",
                        "status": "error",
                        "elapsed_ms": 12,
                        "candidates": [],
                        "error_code": "provider_unavailable",
                        "error": "来源暂时不可用",
                    },
                ],
                "capture_candidates": [],
                "capture_error": "",
            }

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)

    response = await WebGUI.search_hongguo_drama_for_download(
        WebGUI.HongguoDramaSearchRequest(title="目标短剧")
    )

    assert response["status"] == "success"
    assert response["source"] == "hongguo_providers"
    assert response["candidates"][0]["provider_id"] == "hm"
    assert response["providers"][1]["error_code"] == "provider_unavailable"


@pytest.mark.asyncio
async def test_suggest_api_returns_provider_suggestions_without_douyin_fallback(
    tmp_path, monkeypatch
):
    class FakeClient:
        def __init__(self, settings):
            pass

        def suggest_all(self, title, limit):
            assert (title, limit) == ("目标", 8)
            return [
                {
                    "provider_id": "hm",
                    "source_platform": "hm",
                    "series_ref": "hm-1",
                    "book_id": "hm-1",
                    "title": "目标短剧",
                }
            ]

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)

    response = await WebGUI.suggest_hongguo_drama(
        WebGUI.HongguoDramaSearchRequest(title="目标")
    )

    assert response["source"] == "hongguo_providers"
    assert response["candidates"][0]["provider_id"] == "hm"


@pytest.mark.asyncio
async def test_episodes_api_accepts_provider_identity_fields(tmp_path, monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, settings):
            pass

        def episodes(self, series_ref, provider_id):
            calls.append((series_ref, provider_id))
            return [
                {
                    "provider_id": provider_id,
                    "source_platform": provider_id,
                    "series_ref": series_ref,
                    "book_id": series_ref,
                    "episode_ref": "hm-video-1",
                    "video_id": "hm-video-1",
                    "episode": 1,
                    "title": "第一集",
                    "is_paid": False,
                    "paid_reason": "",
                }
            ]

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(
        WebGUI, "downloaded_hongguo_episode_statuses", lambda title: {}
    )

    response = await WebGUI.get_hongguo_drama_episodes(
        WebGUI.HongguoDramaEpisodesRequest(
            book_id="",
            source_platform="",
            series_ref="hm-book-1",
            provider_id="hm",
            title="目标短剧",
        )
    )

    assert response["status"] == "success"
    assert response["episodes"][0]["provider_id"] == "hm"
    assert calls == [("hm-book-1", "hm")]


def test_douyin_search_normalization_expands_mix_items_and_preserves_order():
    from agents.douyin_drama_fallback import normalize_search_candidates

    payload = {
        "data": [
            {"aweme_info": {"aweme_id": "first", "desc": "第一集", "statistics": {}}},
            {
                "aweme_mix_info": {
                    "mix_items": [
                        {"aweme_id": "second", "desc": "第二集", "statistics": {}},
                        {"aweme_id": "first", "desc": "重复", "statistics": {}},
                        {"aweme_id": "paid", "desc": "第三集", "is_paid_content": True, "statistics": {}},
                    ]
                }
            },
        ]
    }

    candidates = normalize_search_candidates(payload, "测试短剧")

    assert [item["aweme_id"] for item in candidates] == ["first", "second", "paid"]
    assert candidates[0]["is_primary"] is True
    assert candidates[1]["is_primary"] is False
    assert candidates[2]["is_paid"] is True


def test_douyin_search_marks_matching_mix_as_collection():
    from agents.douyin_drama_fallback import normalize_search_candidates

    payload = {
        "data": [{
            "aweme_info": {
                "aweme_id": "episode-1",
                "desc": "《第一集》 #龙头 #短剧推荐",
                "author": {"nickname": "明兰剧场", "sec_uid": "author-1"},
                "mix_info": {
                    "mix_id": "mix-1",
                    "mix_name": "《龙头》",
                    "statis": {"current_episode": 1, "updated_to_episode": 71},
                },
                "statistics": {},
            }
        }]
    }

    candidate = normalize_search_candidates(payload, "龙头")[0]

    assert candidate["is_collection"] is True
    assert candidate["collection_matches_title"] is True
    assert candidate["collection_name"] == "《龙头》"
    assert candidate["collection_episode_count"] == 71
    assert candidate["episode_label"] == "第 1 集"
    assert candidate["sec_uid"] == "author-1"


@pytest.mark.asyncio
async def test_douyin_resolve_series_expands_collection_and_reports_gaps(monkeypatch):
    from agents.douyin_drama_fallback import DouyinDramaFallbackClient

    selected = {
        "aweme_id": "episode-1",
        "desc": "《第一集》 #龙头",
        "author": {"nickname": "明兰剧场", "sec_uid": "author-1"},
        "mix_info": {"mix_id": "mix-1", "mix_name": "《龙头》"},
        "statistics": {},
    }
    collection = [
        selected,
        {"aweme_id": "episode-3", "desc": "《第三集》 #龙头", "author": selected["author"], "statistics": {}},
    ]

    class FakeApi:
        async def get_video_by_id(self, aweme_id):
            return selected

        async def search_info_by_keyword(self, **kwargs):
            return {"data": []}

        async def get_mix_aweme_list(self, mix_id, cursor="", count=20):
            return {"aweme_list": collection, "has_more": 0, "cursor": 0, "item_id_to_episode": {"episode-1": 1, "episode-3": 3}}

    class FakeCrawler:
        dy_client = FakeApi()

    client = DouyinDramaFallbackClient()

    async def fake_client():
        return FakeCrawler(), object()

    async def fake_close(crawler, playwright):
        return None

    monkeypatch.setattr(client, "_client", fake_client)
    monkeypatch.setattr(client, "_close", fake_close)

    result = await client.resolve_series("龙头", "episode-1")

    assert [item["aweme_id"] for item in result["episodes"]] == ["episode-1", "episode-3"]
    assert all(item["source_kind"] == "collection" for item in result["episodes"])
    assert result["missing_episodes"] == [2]
    assert result["range_label"] == "合集《龙头》 · 已识别第 1-3 集"


def test_douyin_episode_metadata_parses_ranges_and_single_episodes():
    from agents.douyin_drama_fallback import parse_episode_metadata

    assert parse_episode_metadata("短剧 1-68集完整版") == {"episode_start": 1, "episode_end": 68, "episode_count": 68, "episode_label": "1-68 集"}
    assert parse_episode_metadata("短剧 第12集") == {"episode_start": 12, "episode_end": 12, "episode_count": 1, "episode_label": "第 12 集"}
    assert parse_episode_metadata("没有集数标识") == {"episode_start": None, "episode_end": None, "episode_count": None, "episode_label": ""}


def test_douyin_review_worker_exports_comments_as_csv(tmp_path):
    from agents.douyin_review_worker import write_review_csv

    output = tmp_path / "reviews.csv"
    count = write_review_csv(
        output,
        [
            {
                "cid": "comment-1",
                "user": {"nickname": "评论用户"},
                "text": "这部剧很精彩",
                "create_time": 1_700_000_000,
                "digg_count": 12,
            }
        ],
    )

    assert count == 1
    with output.open(encoding="utf-8-sig", newline="") as file:
        rows = list(__import__("csv").DictReader(file))
    assert rows == [{"评论ID": "comment-1", "用户名": "评论用户", "剧评内容": "这部剧很精彩", "评论时间": "2023-11-15 06:13:20", "点赞数": "12"}]


def test_douyin_review_worker_command_is_specialized():
    from runtime.worker_launcher import build_worker_command

    command = build_worker_command("job.json", platform="dy", crawler_type="drama_reviews")

    assert command[2:4] == ["agents.douyin_review_worker", "--job-file"]


def test_douyin_review_jobs_can_run_for_different_dramas_at_once():
    from runtime.job_manager import JobManager

    first = CrawlJob(job_id="first", platform="dy", crawler_type="drama_reviews", extra={"title": "剧目甲"})
    second = CrawlJob(job_id="second", platform="dy", crawler_type="drama_reviews", extra={"title": "剧目乙"})

    assert JobManager._can_run_concurrently(first, second) is True


def test_douyin_fallback_uses_local_playwright_browser_when_runtime_path_is_overridden(
    tmp_path, monkeypatch
):
    from agents.douyin_drama_fallback import local_playwright_chromium_executable

    executable = (
        tmp_path
        / "ms-playwright"
        / "chromium-1124"
        / "chrome-win"
        / "chrome.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert local_playwright_chromium_executable() == executable


@legacy_remote_contract
def test_hongguo_drama_settings_hide_activation_code(tmp_path):
    save_settings(tmp_path, config_url="https://config.example.test", activation_code="secret-code")

    settings = load_settings(tmp_path)

    assert settings["activation_code"] == "secret-code"
    assert public_settings(settings) == {
        "config_url": "https://config.example.test",
        "has_activation_code": True,
    }


@legacy_remote_contract
@pytest.mark.asyncio
async def test_hongguo_interface_test_validates_authenticated_search(
    tmp_path, monkeypatch
):
    calls = []

    class FakeClient:
        def __init__(self, settings):
            calls.append(("settings", settings))

        def refresh_interfaces(self):
            calls.append(("refresh",))
            return {
                "search": "https://api.example.test/search",
                "book": "https://api.example.test/book",
                "play": "https://api.example.test/play",
            }

        def search(self, title, page=1, source_platform="hg"):
            calls.append(("search", title, page, source_platform))
            return []

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)

    response = await WebGUI.test_hongguo_drama_interface(
        WebGUI.HongguoDramaSettingsRequest(
            config_url="https://config.example.test",
            activation_code="valid-code",
        )
    )

    assert response["status"] == "success"
    assert response["message"] == "配置中心和红果接口鉴权可用"
    assert calls[-1] == ("search", "接口连通测试", 1, "hg")


@legacy_remote_contract
def test_hongguo_drama_client_uses_dynamic_interface_config(monkeypatch):
    client = HongguoDramaClient({"config_url": "https://config.example.test", "activation_code": ""})
    calls = []

    def fake_json(url, **params):
        calls.append((url, params))
        if url == "https://config.example.test":
            return {
                "platforms": [
                    {
                        "value": "hg",
                        "searchUrl": "https://api.example.test/search",
                        "bookUrl": "https://api.example.test/book",
                        "playUrl": "https://api.example.test/play",
                    }
                ]
            }
        if url == "https://api.example.test/search":
            return {"data": [{"book_id": "book-1", "name": "测试短剧", "total": 12}]}
        raise AssertionError(url)

    monkeypatch.setattr(client, "_json", fake_json)

    candidates = client.search("测试短剧")

    assert candidates[0]["book_id"] == "book-1"
    assert candidates[0]["episode_count"] == 12
    assert calls[1] == ("https://api.example.test/search", {"name": "测试短剧", "page": 1, "platform": "hg"})


@legacy_remote_contract
def test_hongguo_download_search_checks_all_remote_sources_before_fallback(
    monkeypatch,
):
    client = HongguoDramaClient(
        {"config_url": "https://config.example.test", "activation_code": ""}
    )
    sources = ("hg", "hgmj", "hgzh", "redguo", "hm", "huolong", "xingyadongli")
    platform_config = [
        {
            "value": source,
            "searchUrl": f"https://api.example.test/{source}/search",
            "bookUrl": f"https://api.example.test/{source}/book",
            "playUrl": f"https://api.example.test/{source}/play",
        }
        for source in sources
    ]
    calls = []

    def fake_json(url, **params):
        calls.append((url, params))
        if url == "https://config.example.test":
            return {"platforms": platform_config}
        source = url.split("/")[-2]
        title = "目标短剧" if source in {"hgzh", "hm"} else "相关短剧"
        return {"code": 200, "data": [{"book_id": f"{source}-1", "title": title}]}

    monkeypatch.setattr(client, "_json", fake_json)

    matches = client.search_exact_all_sources("目标短剧")

    assert [item["book_id"] for item in matches] == ["hgzh-1", "hm-1"]
    assert [item["source_platform"] for item in matches] == ["hgzh", "hm"]
    assert [url for url, _ in calls[1:]] == [
        f"https://api.example.test/{source}/search" for source in sources
    ]
    assert [params["platform"] for _, params in calls[1:]] == list(sources)


@legacy_remote_contract
def test_hongguo_request_keeps_config_platform_and_appends_selected_source():
    client = HongguoDramaClient({"config_url": "", "activation_code": "code-1"})

    request_url = client._request_url(
        "https://api.example.test/api.php?platform=wpf11&action=search_hg",
        name="目标短剧",
        page=1,
        platform="hg",
    )

    pairs = parse_qsl(urlsplit(request_url).query, keep_blank_values=True)
    assert pairs.count(("platform", "wpf11")) == 1
    assert pairs.count(("platform", "hg")) == 1
    assert ("code", "code-1") in pairs


@legacy_remote_contract
def test_hongguo_download_uses_the_selected_source_for_episodes_and_playback(
    monkeypatch,
):
    client = HongguoDramaClient({"config_url": "", "activation_code": ""})
    client.platform_interfaces = {
        "hm": {
            "search": "https://api.example.test/hm/search",
            "book": "https://api.example.test/hm/book",
            "play": "https://api.example.test/hm/play",
            "play_requires_book_id": True,
        }
    }
    calls = []

    def fake_json(url, **params):
        calls.append((url, params))
        if url.endswith("/book"):
            return {"code": 200, "data": [{"video_id": "video-1", "title": "第一集"}]}
        return {"code": 200, "data": {"video_url": "https://video.example.test/1.mp4"}}

    monkeypatch.setattr(client, "_json", fake_json)

    episodes = client.episodes("book-1", source_platform="hm")
    video_url = client.play_url(
        episodes[0]["video_id"], book_id="book-1", source_platform="hm"
    )

    assert video_url == "https://video.example.test/1.mp4"
    assert calls == [
        ("https://api.example.test/hm/book", {"book_id": "book-1"}),
        ("https://api.example.test/hm/play", {"video_id": "video-1", "book_id": "book-1"}),
    ]


def test_hongguo_drama_download_resumes_from_partial_file_and_reports_bytes(tmp_path, monkeypatch):
    requests = []
    progress = []

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
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, headers=None):
            requests.append({"method": method, "url": url, "headers": headers or {}})
            return FakeResponse()

    target = tmp_path / "video.mp4"
    target.with_suffix(".mp4.part").write_bytes(b"abc")
    monkeypatch.setattr(hongguo_drama.httpx, "Client", FakeHttpClient)

    written = HongguoDramaClient({}).download_video(
        "https://video.example.test/drama.mp4",
        target,
        on_progress=lambda current, total: progress.append((current, total)),
    )

    assert written == 6
    assert target.read_bytes() == b"abcdef"
    assert not target.with_suffix(".mp4.part").exists()
    assert requests[0]["headers"]["Range"] == "bytes=3-"
    assert progress[-1] == (6, 6)


@legacy_remote_contract
def test_hongguo_drama_exact_search_filters_related_titles(monkeypatch):
    client = HongguoDramaClient({"config_url": "https://config.example.test", "activation_code": ""})
    def fake_search(title, page=1):
        if page == 1:
            return [{"book_id": "related", "title": "测试短剧后续篇", "raw": {}}]
        if page == 2:
            return [{"book_id": "exact", "title": "测试短剧", "raw": {}}, {"book_id": "same", "title": "测试 短剧", "raw": {}}]
        return []

    monkeypatch.setattr(client, "search", fake_search)

    matches = client.search_exact("测试短剧")

    assert [item["book_id"] for item in matches] == ["exact", "same"]


@legacy_remote_contract
def test_hongguo_drama_client_rejects_out_of_range_episode_data(monkeypatch):
    client = HongguoDramaClient({"config_url": "https://config.example.test", "activation_code": ""})
    client.interfaces = {"search": "search", "book": "book", "play": "play"}
    monkeypatch.setattr(client, "_json", lambda url, **params: {"data": [{"title": "没有视频 ID"}]})

    with pytest.raises(HongguoDramaError, match="未找到可播放剧集"):
        client.episodes("book-1")


@legacy_remote_contract
def test_hongguo_client_marks_paid_episode_and_rejects_paid_play_url(monkeypatch):
    client = HongguoDramaClient({"config_url": "", "activation_code": ""})
    client.interfaces = {"search": "search", "book": "book", "play": "play"}

    def fake_json(url, **params):
        if url == "book":
            return {"data": [{"video_id": "video-paid", "title": "第一集", "is_paid": True}]}
        return {"code": 403, "msg": "该剧集需要付费解锁"}

    monkeypatch.setattr(client, "_json", fake_json)

    episode = client.episodes("book-1")[0]
    assert episode["is_paid"] is True
    with pytest.raises(HongguoPaidContentError, match="付费"):
        client.play_url("video-paid")


@legacy_remote_contract
def test_hongguo_drama_client_keeps_configured_query_parameters():
    client = HongguoDramaClient({"config_url": "", "activation_code": "secret-code"})

    request_url = client._request_url(
        "http://api.example.test/api.php?platform=wpf11&action=search_hg",
        name="测试短剧",
        page=1,
        platform="hg",
    )

    assert "action=search_hg" in request_url
    assert "platform=wpf11" in request_url
    assert "name=%E6%B5%8B%E8%AF%95%E7%9F%AD%E5%89%A7" in request_url
    assert "code=secret-code" in request_url


def test_hongguo_drama_worker_is_selected_for_analysis_jobs():
    command = build_worker_command(".runtime/jobs/drama.json", platform="hg", crawler_type="drama_analysis")

    assert command[-3:] == ["agents.hongguo_drama_worker", "--job-file", ".runtime/jobs/drama.json"]


def test_douyin_fallback_worker_is_selected_for_analysis_jobs():
    command = build_worker_command(".runtime/jobs/drama.json", platform="hg", crawler_type="douyin_fallback_analysis")

    assert command[-3:] == ["agents.douyin_drama_fallback_worker", "--job-file", ".runtime/jobs/drama.json"]


def test_hongguo_download_worker_is_selected_for_download_jobs():
    command = build_worker_command(".runtime/jobs/drama.json", platform="hg", crawler_type="drama_download")

    assert command[-3:] == ["agents.hongguo_drama_download_worker", "--job-file", ".runtime/jobs/drama.json"]


def test_download_worker_uses_only_the_selected_third_party_provider(
    tmp_path, monkeypatch
):
    actions = []

    class FakeProvider:
        provider_id = "hm"

        def resolve_playable(self, series_ref, episode_ref):
            actions.append(("resolve", series_ref, episode_ref))
            return PlayableMedia(
                provider_id="hm",
                series_ref=series_ref,
                episode_ref=episode_ref,
                url="https://video.example.test/1.mp4",
            )

        def download_episode(self, media, target, **kwargs):
            actions.append(("download", media.provider_id, media.episode_ref))
            Path(target).write_bytes(b"third-party-video")
            return DownloadResult(
                provider_id="hm",
                path=Path(target),
                bytes=17,
                metadata={
                    "series_ref": media.series_ref,
                    "episode_ref": media.episode_ref,
                },
            )

    class FakeClient:
        def __init__(self, settings):
            pass

        def refresh_interfaces(self):
            raise AssertionError("第三方任务不应校验抓包目录")

        def episodes(self, series_ref, provider_id):
            actions.append(("episodes", provider_id, series_ref))
            return [
                {
                    "episode": 1,
                    "video_id": "hm-video-1",
                    "episode_ref": "hm-video-1",
                    "title": "第一集",
                    "is_paid": False,
                    "paid_reason": "",
                }
            ]

        def provider(self, provider_id):
            actions.append(("provider", provider_id))
            return FakeProvider()

    class UnexpectedCaptureClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("第三方任务不得调用 8776")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hongguo_drama_download_worker, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(hongguo_drama_download_worker, "load_settings", lambda: {})
    monkeypatch.setattr(
        hongguo_drama_download_worker,
        "HongguoVideoCaptureClient",
        UnexpectedCaptureClient,
    )
    monkeypatch.setattr(hongguo_drama_download_worker, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        hongguo_third_party_downloader,
        "validate_downloaded_mp4",
        lambda _path: None,
    )
    job = CrawlJob(
        job_id="third-party-download",
        platform="hg",
        crawler_type="drama_download",
        extra={
            "title": "目标短剧",
            "book_id": "hm-book-1",
            "series_ref": "hm-book-1",
            "episodes": [1],
            "source_platform": "hm",
            "provider_id": "hm",
        },
    )

    hongguo_drama_download_worker.run(job)

    assert actions == [
        ("episodes", "hm", "hm-book-1"),
        ("provider", "hm"),
        ("resolve", "hm-book-1", "hm-video-1"),
        ("download", "hm", "hm-video-1"),
    ]
    root = (
        tmp_path
        / "data"
        / "hg"
        / "jobs"
        / "third-party-download"
        / "dramas"
        / "目标短剧"
    )
    source = json.loads((root / "episode_01" / "source.json").read_text("utf-8"))
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    assert source["source_platform"] == "hm"
    assert manifest["items"][0]["status"] == "succeeded"
    assert "https://" not in json.dumps({"source": source, "manifest": manifest})


def test_analysis_worker_uses_selected_third_party_provider_before_asr(
    tmp_path, monkeypatch
):
    actions = []

    class FakeProvider:
        provider_id = "hm"

        def resolve_playable(self, series_ref, episode_ref):
            actions.append(("resolve", series_ref, episode_ref))
            return PlayableMedia(
                provider_id="hm",
                series_ref=series_ref,
                episode_ref=episode_ref,
                url="https://video.example.test/1.mp4",
            )

        def download_episode(self, media, target, **kwargs):
            actions.append(("download", media.provider_id, media.episode_ref))
            Path(target).write_bytes(b"third-party-video")
            return DownloadResult("hm", Path(target), 17, {})

    class FakeClient:
        def __init__(self, settings):
            pass

        def refresh_interfaces(self):
            raise AssertionError("第三方任务不应校验抓包目录")

        def episodes(self, series_ref, provider_id):
            actions.append(("episodes", provider_id, series_ref))
            return [
                {
                    "episode": 1,
                    "video_id": "hm-video-1",
                    "episode_ref": "hm-video-1",
                    "title": "第一集",
                    "subtitle_url": "",
                    "is_paid": False,
                    "paid_reason": "",
                }
            ]

        def provider(self, provider_id):
            return FakeProvider()

        def fetch_subtitle_text(self, subtitle_url):
            return ""

    class FakeASR:
        async def transcribe(self, audio_path):
            actions.append(("asr", Path(audio_path).name))
            return {"source": "asr", "text": "第三方视频转写", "segments": []}

    async def fake_convert(video_path, audio_path, ffmpeg_path):
        actions.append(("audio", Path(video_path).name))
        Path(audio_path).write_bytes(b"wav")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hongguo_drama_worker, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(hongguo_drama_worker, "load_settings", lambda: {})
    monkeypatch.setattr(hongguo_drama_worker, "convert_audio_to_wav", fake_convert)
    monkeypatch.setattr(hongguo_drama_worker, "create_asr_provider", lambda: FakeASR())
    monkeypatch.setattr(hongguo_drama_worker, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        hongguo_third_party_downloader,
        "validate_downloaded_mp4",
        lambda _path: None,
    )
    job = CrawlJob(
        job_id="third-party-analysis",
        platform="hg",
        crawler_type="drama_analysis",
        extra={
            "title": "目标短剧",
            "book_id": "hm-book-1",
            "episode": 1,
            "source_platform": "hm",
            "provider_id": "hm",
        },
    )

    hongguo_drama_worker.run(job)

    assert actions == [
        ("episodes", "hm", "hm-book-1"),
        ("resolve", "hm-book-1", "hm-video-1"),
        ("download", "hm", "hm-video-1"),
        ("audio", "video.mp4"),
        ("asr", "audio.wav"),
    ]
    script = (
        tmp_path
        / "data"
        / "hg"
        / "jobs"
        / "third-party-analysis"
        / "dramas"
        / "目标短剧"
        / "episode_01"
        / "script.md"
    ).read_text("utf-8")
    assert "第三方视频转写" in script


@legacy_remote_contract
def test_drama_worker_retries_download_and_writes_media_and_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    attempts = []

    class FakeClient:
        def __init__(self, settings):
            pass

        def refresh_interfaces(self):
            return {}

        def episodes(self, book_id):
            return [{"episode": 1, "video_id": "video-1", "title": "第一集", "subtitle_url": ""}]

        def play_url(self, video_id):
            return "https://video.example.test/episode.mp4"

        def download_video(self, video_url, target, max_bytes=0):
            attempts.append(video_url)
            if len(attempts) == 1:
                raise HongguoDramaError("expired")
            Path(target).write_bytes(b"video")
            return 5

        def fetch_subtitle_text(self, subtitle_url):
            return ""

    class FakeASR:
        async def transcribe(self, audio_path):
            return {"source": "asr", "text": "这是该集转写内容", "segments": []}

    async def fake_convert(video_path, audio_path, ffmpeg_path):
        Path(audio_path).write_bytes(b"wav")
        return Path(audio_path)

    monkeypatch.setattr(hongguo_drama_worker, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(hongguo_drama_worker, "load_settings", lambda: {})
    monkeypatch.setattr(hongguo_drama_worker, "convert_audio_to_wav", fake_convert)
    monkeypatch.setattr(hongguo_drama_worker, "create_asr_provider", lambda: FakeASR())
    job = CrawlJob(
        job_id="job-drama",
        platform="hg",
        crawler_type="drama_analysis",
        extra={"title": "测试短剧", "book_id": "book-1", "episode": 1},
    )

    hongguo_drama_worker.run(job)

    output = tmp_path / "data" / "hg" / "jobs" / "job-drama" / "dramas" / "测试短剧" / "episode_01"
    assert len(attempts) == 2
    assert (output / "video.mp4").read_bytes() == b"video"
    assert (output / "audio.wav").read_bytes() == b"wav"
    assert json.loads((output / "transcript.json").read_text(encoding="utf-8"))["text"] == "这是该集转写内容"
    assert "不是官方剧本" in (output / "script.md").read_text(encoding="utf-8")


def test_hongguo_analysis_worker_explains_and_skips_paid_episode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events = []

    class FakeClient:
        def __init__(self, settings):
            pass

        def refresh_interfaces(self):
            return {}

        def episodes(self, book_id):
            return [{"episode": 1, "video_id": "paid", "title": "第一集", "is_paid": True, "paid_reason": "接口标记 is_paid"}]

    monkeypatch.setattr(hongguo_drama_worker, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(hongguo_drama_worker, "load_settings", lambda: {})
    monkeypatch.setattr(hongguo_drama_worker, "emit", lambda job, event, message, level="info", payload=None: events.append((event, message, payload or {})))
    job = CrawlJob(job_id="job-analysis-paid", platform="hg", crawler_type="drama_analysis", extra={"title": "测试短剧", "book_id": "book-1", "episode": 1})

    hongguo_drama_worker.run(job)

    output = tmp_path / "data" / "hg" / "jobs" / "job-analysis-paid" / "dramas" / "测试短剧" / "episode_01"
    source = json.loads((output / "source.json").read_text(encoding="utf-8"))
    assert source["status"] == "skipped_paid"
    assert not (output / "video.mp4").exists()
    assert events[-1][0] == "job_finished"


def test_douyin_fallback_worker_writes_source_marked_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class FakeFallbackClient:
        def __init__(self, background=False):
            assert background is True

        async def get_video(self, aweme_id):
            assert aweme_id == "aweme-1"
            return {"desc": "抖音视频", "video": {}}, "https://video.example.test/fallback.mp4"

    class FakeDownloadClient:
        def __init__(self, settings):
            pass

        def download_video(self, video_url, target, max_bytes=0, on_progress=None, wait_if_paused=None):
            Path(target).write_bytes(b"video")
            return 5

        def fetch_subtitle_text(self, subtitle_url):
            return ""

    class FakeASR:
        async def transcribe(self, audio_path):
            return {"source": "asr", "text": "抖音转写内容", "segments": []}

    async def fake_convert(video_path, audio_path, ffmpeg_path):
        Path(audio_path).write_bytes(b"wav")
        return Path(audio_path)

    monkeypatch.setattr(douyin_drama_fallback_worker, "DouyinDramaFallbackClient", FakeFallbackClient)
    monkeypatch.setattr(douyin_drama_fallback_worker, "HongguoDramaClient", FakeDownloadClient)
    monkeypatch.setattr(douyin_drama_fallback_worker, "convert_audio_to_wav", fake_convert)
    monkeypatch.setattr(douyin_drama_fallback_worker, "create_asr_provider", lambda: FakeASR())
    job = CrawlJob(
        job_id="job-fallback",
        platform="hg",
        crawler_type="douyin_fallback_analysis",
        extra={"title": "测试短剧", "aweme_id": "aweme-1", "fallback_reason": "红果未找到匹配剧目"},
    )

    douyin_drama_fallback_worker.run(job)

    output = tmp_path / "data" / "hg" / "jobs" / "job-fallback" / "dramas" / "测试短剧" / "douyin_fallback" / "item_001_aweme-1"
    source = json.loads((output / "source.json").read_text(encoding="utf-8"))
    assert source["source_platform"] == "dy"
    assert source["aweme_id"] == "aweme-1"
    assert (output / "video.mp4").read_bytes() == b"video"
    assert (output / "audio.wav").read_bytes() == b"wav"
    assert json.loads((output / "transcript.json").read_text(encoding="utf-8"))["text"] == "抖音转写内容"
    assert "抖音降级" in (output / "script.md").read_text(encoding="utf-8")
    assert (output.parent / "manifest.json").exists()


def test_douyin_fallback_worker_downloads_multiple_items_and_skips_paid(tmp_path, monkeypatch):
    from agents.douyin_drama_fallback import DouyinPaidContentError

    monkeypatch.chdir(tmp_path)

    events = []

    class FakeFallbackClient:
        def __init__(self, background=False):
            assert background is True

        async def get_video(self, aweme_id):
            if aweme_id == "paid-2":
                raise DouyinPaidContentError("该视频需要付费")
            title = "测试短剧 1-3集完整版" if aweme_id == "free-1" else f"视频 {aweme_id}"
            return {"desc": title, "video": {}}, f"https://video.example.test/{aweme_id}.mp4"

    class FakeDownloadClient:
        def __init__(self, settings):
            pass

        def download_video(self, video_url, target, max_bytes=0, on_progress=None, wait_if_paused=None):
            Path(target).write_bytes(video_url.encode())
            if on_progress:
                on_progress(10, 10)
            return 10

        def fetch_subtitle_text(self, subtitle_url):
            return "现成字幕"

    async def fake_convert(video_path, audio_path, ffmpeg_path):
        Path(audio_path).write_bytes(b"wav")
        return Path(audio_path)

    monkeypatch.setattr(douyin_drama_fallback_worker, "DouyinDramaFallbackClient", FakeFallbackClient)
    monkeypatch.setattr(douyin_drama_fallback_worker, "HongguoDramaClient", FakeDownloadClient)
    monkeypatch.setattr(douyin_drama_fallback_worker, "convert_audio_to_wav", fake_convert)
    monkeypatch.setattr(douyin_drama_fallback_worker, "emit", lambda job, event, message, level="info", payload=None: events.append((event, message, payload or {})))
    job = CrawlJob(
        job_id="job-fallback-batch",
        platform="hg",
        crawler_type="douyin_fallback_analysis",
        extra={"title": "测试短剧", "aweme_ids": ["free-1", "paid-2", "free-3"]},
    )

    douyin_drama_fallback_worker.run(job)

    root = tmp_path / "data" / "hg" / "jobs" / "job-fallback-batch" / "dramas" / "测试短剧" / "douyin_fallback"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in manifest["items"]] == ["succeeded", "skipped_paid", "succeeded"]
    assert (root / "item_001_free-1" / "video.mp4").exists()
    assert not (root / "item_002_paid-2" / "video.mp4").exists()
    assert (root / "item_003_free-3" / "script.md").exists()
    assert "覆盖集数：1-3 集" in (root / "item_001_free-1" / "script.md").read_text(encoding="utf-8")
    progress_events = [item for item in events if item[0] == "progress"]
    assert progress_events[0][2]["percent"] == 0
    assert any("正在获取视频信息" in item[1] for item in progress_events)
    assert any(item[2].get("downloaded_bytes") == 10 for item in progress_events)


@legacy_remote_contract
def test_hongguo_download_worker_supports_selected_episodes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events = []

    class FakeClient:
        def __init__(self, settings):
            pass

        def refresh_interfaces(self):
            return {}

        def episodes(self, book_id):
            return [
                {"episode": 1, "video_id": "video-1", "title": "第一集"},
                {"episode": 2, "video_id": "video-2", "title": "第二集"},
            ]

        def play_url(self, video_id):
            return f"https://video.example.test/{video_id}.mp4"

        def download_video(self, video_url, target, max_bytes=0, on_progress=None, wait_if_paused=None):
            Path(target).write_bytes(video_url.encode())
            if on_progress:
                on_progress(5, 5)
            return 5

    monkeypatch.setattr(hongguo_drama_download_worker, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(hongguo_drama_download_worker, "load_settings", lambda: {})
    monkeypatch.setattr(hongguo_drama_download_worker, "emit", lambda job, event, message, level="info", payload=None: events.append((event, message, payload or {})))
    job = CrawlJob(
        job_id="job-download",
        platform="hg",
        crawler_type="drama_download",
        extra={"title": "测试短剧", "book_id": "book-1", "episodes": [2], "all_episodes": False},
    )

    hongguo_drama_download_worker.run(job)

    output = tmp_path / "data" / "hg" / "jobs" / "job-download" / "dramas" / "测试短剧" / "episode_02"
    assert (output / "video.mp4").exists()
    assert not (tmp_path / "data" / "hg" / "jobs" / "job-download" / "dramas" / "测试短剧" / "episode_01").exists()
    progress_events = [item for item in events if item[0] == "progress"]
    assert progress_events[0][2] == {"completed": 0, "current": 0, "total": 1, "percent": 0, "downloaded_bytes": 0, "total_bytes": 0, "downloaded": 0, "skipped_paid": 0}
    assert any("正在下载" in item[1] and 0 < item[2]["percent"] < 100 for item in progress_events)


@legacy_remote_contract
def test_hongguo_download_worker_skips_paid_episode_and_continues(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def __init__(self, settings):
            pass

        def refresh_interfaces(self):
            return {}

        def episodes(self, book_id):
            return [
                {"episode": 1, "video_id": "paid", "title": "第一集", "is_paid": True, "paid_reason": "接口标记 is_paid"},
                {"episode": 2, "video_id": "free", "title": "第二集", "is_paid": False, "paid_reason": ""},
            ]

        def play_url(self, video_id):
            assert video_id == "free"
            return "https://video.example.test/free.mp4"

        def download_video(self, video_url, target, max_bytes=0, on_progress=None, wait_if_paused=None):
            Path(target).write_bytes(b"free")
            return 4

    monkeypatch.setattr(hongguo_drama_download_worker, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(hongguo_drama_download_worker, "load_settings", lambda: {})
    job = CrawlJob(
        job_id="job-download-paid",
        platform="hg",
        crawler_type="drama_download",
        extra={"title": "测试短剧", "book_id": "book-1", "episodes": [], "all_episodes": True},
    )

    hongguo_drama_download_worker.run(job)

    root = tmp_path / "data" / "hg" / "jobs" / "job-download-paid" / "dramas" / "测试短剧"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in manifest["items"]] == ["skipped_paid", "succeeded"]
    assert not (root / "episode_01" / "video.mp4").exists()
    assert (root / "episode_02" / "video.mp4").exists()


@legacy_remote_contract
def test_hongguo_download_worker_skips_when_refreshed_url_becomes_paid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def __init__(self, settings):
            self.play_calls = 0

        def refresh_interfaces(self):
            return {}

        def episodes(self, book_id):
            return [{"episode": 1, "video_id": "video-1", "title": "第一集", "is_paid": False}]

        def play_url(self, video_id):
            self.play_calls += 1
            if self.play_calls == 2:
                raise HongguoPaidContentError("刷新后接口提示需要付费")
            return "https://video.example.test/expired.mp4"

        def download_video(self, video_url, target, max_bytes=0, on_progress=None, wait_if_paused=None):
            raise HongguoDramaError("expired")

    monkeypatch.setattr(hongguo_drama_download_worker, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(hongguo_drama_download_worker, "load_settings", lambda: {})
    job = CrawlJob(job_id="job-refresh-paid", platform="hg", crawler_type="drama_download", extra={"title": "测试短剧", "book_id": "book-1", "episodes": [1]})

    hongguo_drama_download_worker.run(job)

    root = tmp_path / "data" / "hg" / "jobs" / "job-refresh-paid" / "dramas" / "测试短剧"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["status"] == "skipped_paid"


@pytest.mark.asyncio
async def test_episode_api_marks_previously_downloaded_episodes(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, settings):
            pass

        def episodes(self, book_id, source_platform):
            assert book_id == "book-1"
            assert source_platform == "hg"
            return [
                {"episode": 1, "video_id": "video-1", "title": "第一集"},
                {"episode": 2, "video_id": "video-2", "title": "第二集"},
            ]

    episode_dir = tmp_path / "data" / "hg" / "jobs" / "job-old" / "dramas" / "测试短剧" / "episode_01"
    episode_dir.mkdir(parents=True)
    (episode_dir / "video.mp4").write_bytes(b"video")
    (episode_dir / "source.json").write_text(
        json.dumps({"title": "测试短剧", "book_id": "another-book", "source_platform": "dy", "episode": 1}),
        encoding="utf-8",
    )
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(WebGUI.job_manager, "list_jobs", lambda: [{
        "job_id": "job-old",
        "crawler_type": "drama_download",
        "job": {"extra": {"book_id": "another-book", "source_platform": "dy"}},
    }])

    response = await WebGUI.get_hongguo_drama_episodes(
        WebGUI.HongguoDramaEpisodesRequest(
            book_id="book-1", source_platform="hg", title="测试短剧"
        )
    )

    assert response["episodes"][0]["download_status"]["status"] == "downloaded"
    assert response["episodes"][0]["download_status"]["job_id"] == "job-old"
    assert "download_status" not in response["episodes"][1]


def test_downloaded_episode_statuses_use_local_files_across_books_and_sources(tmp_path, monkeypatch):
    other_episode = tmp_path / "data" / "hg" / "jobs" / "job-other" / "dramas" / "同名短剧" / "episode_01"
    match_episode = tmp_path / "data" / "hg" / "jobs" / "job-match" / "dramas" / "同名短剧" / "episode_02"
    for episode_dir, episode, book_id, source in (
        (other_episode, 1, "book-other", "dy"),
        (match_episode, 2, "book-1", "hg"),
    ):
        episode_dir.mkdir(parents=True)
        (episode_dir / "video.mp4").write_bytes(b"video")
        (episode_dir / "source.json").write_text(
            json.dumps({"title": "同名短剧", "book_id": book_id, "source_platform": source, "episode": episode}),
            encoding="utf-8",
        )
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI.job_manager, "list_jobs", lambda: [
        {
            "job_id": "job-other", "crawler_type": "drama_download",
            "job": {"extra": {"book_id": "book-other", "source_platform": "dy"}},
        },
        {
            "job_id": "job-match", "crawler_type": "drama_download",
            "job": {"extra": {"book_id": "book-1", "source_platform": "hg"}},
        },
    ])

    statuses = WebGUI.downloaded_hongguo_episode_statuses("同名短剧")

    assert set(statuses) == {1, 2}
    assert statuses[1]["job_id"] == "job-other"
    assert statuses[2]["job_id"] == "job-match"


def test_downloaded_episode_statuses_do_not_filter_local_files_by_source(
    tmp_path, monkeypatch
):
    hg_manifest = tmp_path / "data" / "hg" / "jobs" / "job-hg" / "manifest.json"
    hm_manifest = tmp_path / "data" / "hg" / "jobs" / "job-hm" / "manifest.json"
    for manifest, source, episode in ((hg_manifest, "hg", 1), (hm_manifest, "hm", 2)):
        manifest.parent.mkdir(parents=True, exist_ok=True)
        video_path = manifest.parent / f"episode_{episode:02d}" / "video.mp4"
        video_path.parent.mkdir()
        video_path.write_bytes(b"video")
        (video_path.parent / "source.json").write_text(
            json.dumps({"title": "同源短剧", "book_id": "shared-book", "source_platform": source, "episode": episode}),
            encoding="utf-8",
        )
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(
        WebGUI.job_manager,
        "list_jobs",
        lambda: [
            {
                "job_id": "job-hg",
                "crawler_type": "drama_download",
                "job": {"extra": {"book_id": "shared-book", "source_platform": "hg"}},
            },
            {
                "job_id": "job-hm",
                "crawler_type": "drama_download",
                "job": {"extra": {"book_id": "shared-book", "source_platform": "hm"}},
            },
        ],
    )

    statuses = WebGUI.downloaded_hongguo_episode_statuses("同源短剧")

    assert statuses[1]["status"] == "downloaded"
    assert statuses[1]["job_id"] == "job-hg"
    assert statuses[2]["status"] == "downloaded"
    assert statuses[2]["job_id"] == "job-hm"
    assert statuses[2]["path"].replace("\\", "/").endswith("job-hm/episode_02")


def test_downloaded_episode_statuses_accept_manifest_backed_local_files(tmp_path, monkeypatch):
    manifest = tmp_path / "data" / "hg" / "jobs" / "job-manifest" / "dramas" / "只留清单短剧" / "manifest.json"
    episode_file = manifest.parent / "episode_03" / "video.mp4"
    episode_file.parent.mkdir(parents=True)
    episode_file.write_bytes(b"video")
    manifest.write_text(
        json.dumps(
            {
                "title": "只留清单短剧",
                "items": [{"episode": 3, "path": "episode_03/video.mp4"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)

    statuses = WebGUI.downloaded_hongguo_episode_statuses("只留清单短剧")

    assert statuses[3]["status"] == "downloaded"
    assert statuses[3]["job_id"] == "job-manifest"


@pytest.mark.asyncio
async def test_download_status_reports_a_local_douyin_full_drama(tmp_path, monkeypatch):
    episode_dir = tmp_path / "data" / "hg" / "jobs" / "job-douyin-full" / "dramas" / "抖音全集短剧" / "douyin_fallback" / "item_001"
    episode_dir.mkdir(parents=True)
    (episode_dir / "video.mp4").write_bytes(b"video")
    (episode_dir / "source.json").write_text(
        json.dumps(
            {
                "title": "抖音全集短剧",
                "source_platform": "dy",
                "source": "douyin_fallback",
                "episode_metadata": {
                    "episode_start": 1,
                    "episode_end": 96,
                    "episode_count": 96,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)

    response = await WebGUI.get_hongguo_drama_download_status(
        WebGUI.HongguoDramaDownloadStatusRequest(title="抖音全集短剧")
    )

    assert response == {
        "douyin_full_downloaded": True,
        "episode_start": 1,
        "episode_end": 96,
        "episode_count": 96,
    }


@legacy_remote_contract
@pytest.mark.asyncio
async def test_drama_search_api_returns_only_exact_matches_from_all_configured_sources(
    tmp_path, monkeypatch
):
    class FakeClient:
        def __init__(self, settings):
            pass

        def search(self, title):
            return [{"book_id": "partial-1", "title": f"{title} 番外"}]

        def search_exact_all_sources(self, title):
            return [
                {
                    "book_id": "exact-1",
                    "title": title,
                    "source_platform": "hm",
                    "raw": {"provider_only": True},
                }
            ]

    class UnexpectedFallbackClient:
        async def search(self, title):
            raise AssertionError("精确匹配存在时不应调用抖音降级")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", UnexpectedFallbackClient)

    response = await WebGUI.search_hongguo_drama(
        WebGUI.HongguoDramaSearchRequest(title="测试短剧")
    )

    assert response == {
        "status": "success",
        "source": "hongguo",
        "candidates": [
            {
                "book_id": "exact-1",
                "title": "测试短剧",
                "source_platform": "hm",
            }
        ],
    }


@legacy_remote_contract
@pytest.mark.asyncio
async def test_drama_search_api_hides_raw_provider_record(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            return [{"book_id": "book-1", "title": title, "raw": {"provider_only": True}}]

    class UnexpectedFallbackClient:
        async def search(self, title):
            raise AssertionError("红果成功时不应调用抖音降级")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", UnexpectedFallbackClient)

    response = await WebGUI.search_hongguo_drama(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))

    assert response["status"] == "success"
    assert response["candidates"] == [{"book_id": "book-1", "title": "测试短剧"}]


@pytest.mark.asyncio
async def test_review_capture_proxy_submits_title_and_limit(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 202

        def json(self):
            return {"job_id": "review-job-1", "title": "测试短剧", "limit": 25, "status": "queued"}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json):
            calls.append((url, json))
            return FakeResponse()

    monkeypatch.setattr(WebGUI.httpx, "AsyncClient", FakeClient)

    response = await WebGUI.start_hongguo_review_capture(
        WebGUI.HongguoReviewCaptureRequest(title="测试短剧", limit=25)
    )

    assert calls == [
        ("http://127.0.0.1:8766/v1/reviews/capture/limited", {"title": "测试短剧", "limit": 25})
    ]
    assert response == {"job": {"job_id": "review-job-1", "title": "测试短剧", "limit": 25, "status": "queued"}, "reviews": []}


@pytest.mark.asyncio
async def test_review_capture_proxy_uses_all_endpoint_when_requested(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 202

        def json(self):
            return {"job_id": "review-job-all", "title": "测试短剧", "limit": None, "status": "queued"}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json):
            calls.append((url, json))
            return FakeResponse()

    monkeypatch.setattr(WebGUI.httpx, "AsyncClient", FakeClient)

    await WebGUI.start_hongguo_review_capture(
        WebGUI.HongguoReviewCaptureRequest(title="测试短剧", all_reviews=True)
    )

    assert calls == [
        ("http://127.0.0.1:8766/v1/reviews/capture/all", {"title": "测试短剧"})
    ]


@pytest.mark.asyncio
async def test_review_capture_status_reads_completed_csv_for_page_rendering(tmp_path, monkeypatch):
    export_root = tmp_path / "exports"
    output = export_root / "20260724" / "review_list_1.csv"
    output.parent.mkdir(parents=True)
    output.write_text(
        "剧名,series_id,评论ID,用户名,剧评内容,评论时间,点赞数\n测试短剧,1,comment-1,用户甲,很好看,2026-07-24 12:00:00,8\n",
        encoding="utf-8-sig",
    )

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "job_id": "review-job-1", "title": "测试短剧", "limit": 25,
                "status": "completed", "review_count": 1, "source_total": 1,
                "output_path": str(output),
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return FakeResponse()

    monkeypatch.setattr(WebGUI, "HONGGUO_REVIEW_CAPTURE_EXPORT_ROOT", export_root.resolve())
    monkeypatch.setattr(WebGUI.httpx, "AsyncClient", FakeClient)

    response = await WebGUI.get_hongguo_review_capture_job("review-job-1")

    assert response["job"]["status"] == "completed"
    assert response["reviews"] == [{
        "comment_id": "comment-1", "author": "用户甲", "content": "很好看",
        "created_at": "2026-07-24 12:00:00", "like_count": "8",
    }]


@pytest.mark.asyncio
async def test_review_capture_download_converts_completed_csv_to_excel(tmp_path, monkeypatch):
    export_root = tmp_path / "exports"
    output = export_root / "20260724" / "review_list_1.csv"
    output.parent.mkdir(parents=True)
    output.write_text(
        "剧名,series_id,评论ID,用户名,剧评内容,评论时间,点赞数\n测试短剧,1,comment-1,用户甲,很好看,2026-07-24 12:00:00,8\n",
        encoding="utf-8-sig",
    )

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"job_id": "review-job-1", "status": "completed", "output_path": str(output)}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return FakeResponse()

    monkeypatch.setattr(WebGUI, "HONGGUO_REVIEW_CAPTURE_EXPORT_ROOT", export_root.resolve())
    monkeypatch.setattr(WebGUI.httpx, "AsyncClient", FakeClient)

    response = await WebGUI.download_hongguo_review_capture("review-job-1", format="xlsx")
    body = b"".join([chunk async for chunk in response.body_iterator])

    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(body))
    sheet = workbook.active
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument")
    assert sheet.title == "剧评"
    assert [cell.value for cell in sheet[1]] == ["剧名", "series_id", "评论ID", "用户名", "剧评内容", "评论时间", "点赞数"]
    assert [cell.value for cell in sheet[2]][:5] == ["测试短剧", "1", "comment-1", "用户甲", "很好看"]


@pytest.mark.asyncio
async def test_review_capture_status_returns_failed_job_without_gateway_error(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "job_id": "review-job-1", "title": "测试短剧", "limit": 25,
                "status": "failed", "error": "title was not found",
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return FakeResponse()

    monkeypatch.setattr(WebGUI.httpx, "AsyncClient", FakeClient)

    response = await WebGUI.get_hongguo_review_capture_job("review-job-1")

    assert response == {
        "job": {
            "job_id": "review-job-1", "title": "测试短剧", "limit": 25,
            "status": "failed", "error": "title was not found",
        },
        "reviews": [],
    }


@legacy_remote_contract
@pytest.mark.asyncio
async def test_drama_search_falls_back_to_douyin_after_hongguo_empty(tmp_path, monkeypatch):
    class EmptyHongguoClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            return []

    class FakeFallbackClient:
        async def search(self, title):
            return [{"source": "douyin_fallback", "aweme_id": "aweme-1", "title": title}]

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", EmptyHongguoClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", FakeFallbackClient)

    response = await WebGUI.search_hongguo_drama(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))

    assert response["source"] == "douyin_fallback"
    assert response["fallback_reason"] == "七个剧目源均未返回剧名完全匹配结果"
    assert response["candidates"][0]["aweme_id"] == "aweme-1"


@legacy_remote_contract
@pytest.mark.asyncio
async def test_drama_search_falls_back_to_douyin_after_hongguo_error(tmp_path, monkeypatch):
    class BrokenHongguoClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            raise HongguoDramaError("请求接口失败: HTTP 401")

    class FakeFallbackClient:
        async def search(self, title):
            return [{"source": "douyin_fallback", "aweme_id": "aweme-2", "title": title}]

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", BrokenHongguoClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", FakeFallbackClient)

    response = await WebGUI.search_hongguo_drama(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))

    assert response["source"] == "douyin_fallback"
    assert "HTTP 401" in response["fallback_reason"]


@legacy_remote_contract
@pytest.mark.asyncio
async def test_download_search_calls_douyin_when_no_exact_hongguo_match(tmp_path, monkeypatch):
    class HongguoClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            return []

    class FakeFallbackClient:
        async def search(self, title):
            return [{"source": "douyin_fallback", "aweme_id": "aweme-9", "title": title}]

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", HongguoClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", FakeFallbackClient)

    response = await WebGUI.search_hongguo_drama_for_download(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))

    assert response["source"] == "douyin_fallback"
    assert response["fallback_reason"] == "七个剧目源均未返回剧名完全匹配结果"
    assert response["candidates"][0]["aweme_id"] == "aweme-9"


@legacy_remote_contract
@pytest.mark.asyncio
async def test_download_search_reuses_a_recent_title_result(tmp_path, monkeypatch):
    calls = []

    class HongguoClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            calls.append(title)
            return [{"book_id": "book-9", "title": title, "source_platform": "redguo"}]

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", HongguoClient)
    monkeypatch.setattr(WebGUI, "_drama_download_search_cache", {})

    first = await WebGUI.search_hongguo_drama_for_download(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))
    second = await WebGUI.search_hongguo_drama_for_download(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))

    assert calls == ["测试短剧"]
    assert second == first


@legacy_remote_contract
@pytest.mark.asyncio
async def test_download_search_waits_for_all_sources_before_douyin_fallback(
    tmp_path, monkeypatch
):
    class HongguoClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            return [{"book_id": "book-9", "title": title, "source_platform": "redguo"}]

    async def legacy_timeout_must_not_be_used(*args, **kwargs):
        raise AssertionError("七个剧目源检索未完成前不能以超时降级到抖音")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", HongguoClient)
    monkeypatch.setattr(WebGUI.asyncio, "wait_for", legacy_timeout_must_not_be_used)

    response = await WebGUI.search_hongguo_drama_for_download(
        WebGUI.HongguoDramaSearchRequest(title="测试短剧")
    )

    assert response["source"] == "hongguo"
    assert response["candidates"] == [
        {"book_id": "book-9", "title": "测试短剧", "source_platform": "redguo"}
    ]


@legacy_remote_contract
@pytest.mark.asyncio
async def test_download_search_does_not_call_douyin_when_exact_hongguo_match_exists(tmp_path, monkeypatch):
    class HongguoClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            return [{"book_id": "book-9", "title": title, "raw": {"private": True}}]

    class UnexpectedFallbackClient:
        async def search(self, title):
            raise AssertionError("红果有精确结果时不应调用抖音降级")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", HongguoClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", UnexpectedFallbackClient)

    response = await WebGUI.search_hongguo_drama_for_download(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))

    assert response["source"] == "hongguo"
    assert response["candidates"] == [{"book_id": "book-9", "title": "测试短剧"}]


@legacy_remote_contract
@pytest.mark.asyncio
async def test_download_search_reports_douyin_fallback_when_douyin_is_unavailable(tmp_path, monkeypatch):
    class EmptyHongguoClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            return []

    class BrokenFallbackClient:
        async def search(self, title):
            raise WebGUI.DouyinFallbackError("CDP 浏览器未登录")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", EmptyHongguoClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", BrokenFallbackClient)

    response = await WebGUI.search_hongguo_drama_for_download(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))

    assert response.status_code == 502
    assert response.body and b'"source":"douyin_fallback"' in response.body


@legacy_remote_contract
@pytest.mark.asyncio
async def test_download_search_returns_json_when_douyin_browser_startup_crashes(tmp_path, monkeypatch):
    class EmptyHongguoClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            return []

    class CrashedFallbackClient:
        async def search(self, title):
            raise RuntimeError("Playwright chromium executable is missing")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", EmptyHongguoClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", CrashedFallbackClient)

    response = await WebGUI.search_hongguo_drama_for_download(
        WebGUI.HongguoDramaSearchRequest(title="测试短剧")
    )

    assert response.status_code == 502
    assert b'"status":"error"' in response.body
    assert b"Playwright chromium executable is missing" in response.body


@pytest.mark.asyncio
async def test_douyin_fallback_job_accepts_ordered_multiple_aweme_ids(monkeypatch):
    captured = {}

    class FakeState:
        job_id = "job-batch"

        def to_dict(self):
            return {"job_id": "job-batch", "extra": captured["job"].extra}

    def fake_create(job, **ownership):
        captured["job"] = job
        captured["ownership"] = ownership
        return FakeState()

    monkeypatch.setattr(WebGUI.job_manager, "generate_job_id", lambda platform: "job-batch")
    monkeypatch.setattr(WebGUI.job_manager, "create_job", fake_create)
    monkeypatch.setattr(WebGUI, "_public_created_job", lambda state: state.to_dict())
    monkeypatch.setattr(
        WebGUI,
        "_resolve_download_topic",
        lambda **kwargs: {"id": 17, "title": kwargs["title"], "status": "adopted"},
    )
    monkeypatch.setattr(WebGUI, "_record_job_audit", lambda *args, **kwargs: None)

    response = await WebGUI.create_douyin_fallback_drama_job(
        WebGUI.DouyinFallbackDramaJobRequest(
            topic_id=17,
            title="测试短剧",
            aweme_ids=["aweme-2", "aweme-1", "aweme-2"],
            fallback_reason="红果无结果",
        ),
        principal=SYSTEM_ACTOR,
    )

    assert response["status"] == "success"
    assert response["topic"]["id"] == 17
    assert captured["job"].extra["aweme_ids"] == ["aweme-2", "aweme-1"]
    assert captured["job"].extra["aweme_id"] == "aweme-2"
    assert captured["ownership"]["workflow_task_id"] is None
    assert captured["ownership"]["project_id"] is None


@legacy_remote_contract
@pytest.mark.asyncio
async def test_drama_suggest_returns_exact_hongguo_candidates_without_douyin_fallback(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, settings):
            pass

        def search_exact_all_sources(self, title):
            return [
                {"book_id": "book-1", "title": title, "raw": {"secret": True}},
            ]

    class UnexpectedFallbackClient:
        async def search(self, title):
            raise AssertionError("联想阶段不应启动抖音降级")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", UnexpectedFallbackClient)

    response = await WebGUI.suggest_hongguo_drama(WebGUI.HongguoDramaSearchRequest(title="测试"))

    assert response["source"] == "hongguo"
    assert response["candidates"] == [
        {"book_id": "book-1", "title": "测试"},
    ]


@legacy_remote_contract
@pytest.mark.asyncio
async def test_drama_interface_test_uses_unsaved_form_settings(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, settings):
            self.settings = settings

        def refresh_interfaces(self):
            assert self.settings["config_url"] == "https://temporary.example.test/config"
            assert self.settings["activation_code"] == "temporary-code"
            return {"search": "https://example.test/search", "book": "https://example.test/book", "play": "https://example.test/play"}

        def search(self, title, page=1, source_platform="hg"):
            assert title == "接口连通测试"
            assert page == 1
            assert source_platform == "hg"
            return []

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)

    response = await WebGUI.test_hongguo_drama_interface(
        WebGUI.HongguoDramaSettingsRequest(
            config_url="https://temporary.example.test/config",
            activation_code="temporary-code",
        )
    )

    assert response["status"] == "success"
    assert response["interfaces"]["play"] == "https://example.test/play"
