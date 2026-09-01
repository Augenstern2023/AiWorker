# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import base64
import hashlib
import json
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import httpx

import WebGUI
from agents import hongguo_drama_download_worker, hongguo_drama_worker
from agents import hongguo_drama
from agents.drama_providers import ProviderFailure
from agents.hongguo_capture import HongguoCaptureCatalog
from agents.hongguo_capture_downloader import (
    CaptureDownloadError,
    CapturePaidError,
    CaptureTimeoutError,
    CapturedMediaDownloader,
)
from agents.hongguo_drama import (
    DEFAULT_CAPTURE_ROOT,
    DEFAULT_CAPTURE_SERVICE_URL,
    load_settings,
    public_settings,
    save_settings,
)
from agents.spade_cenc import _cenc_ctr, decrypt_mp4_cenc, derive_content_key
from core.job import CrawlJob


def _write_capture(root: Path, name: str, payload: object) -> Path:
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _detail_payload() -> dict:
    return {
        "data": {
            "series": {
                "series_id": "series-1",
                "series_title": "好一个乖乖女",
                "video_list": [
                    {"vid": "vid-1", "vid_index": 1, "title": "第一集"},
                    {
                        "vid": "vid-2",
                        "vid_index": 2,
                        "title": "第二集",
                        "is_paid": True,
                    },
                ],
            }
        }
    }


def _model_payload(*, encrypted: bool = False, expires: int | None = None) -> dict:
    expiry = expires if expires is not None else int(time.time()) + 3600
    variants = [
        {
            "main_url": "https://video.example.test/high.mp4",
            "backup_url": "https://video.example.test/high-backup.mp4",
            "video_meta": {
                "codec_type": "h264",
                "definition": "1080p",
                "vheight": 1080,
                "size": 2000,
            },
            "encrypt_info": (
                {
                    "encrypt": True,
                    "spade_a": "synthetic-spade",
                    "kid": "private-kid",
                    "encryption_method": "cenc-aes-ctr",
                }
                if encrypted
                else {"encrypt": False}
            ),
        },
        {
            "main_url": "https://video.example.test/low.mp4",
            "video_meta": {
                "codec_type": "h265",
                "definition": "720p",
                "vheight": 720,
                "size": 1000,
            },
            "encrypt_info": {"encrypt": False},
        },
    ]
    return {
        "data": {
            "vid-1": {
                "video_model": json.dumps(
                    {
                        "video_id": "video-1",
                        "url_expire": expiry,
                        "fallback_api": {
                            "fallback_api": "https://video.example.test/fallback.mp4"
                        },
                        "video_list": variants,
                    }
                )
            }
        }
    }


def _single_model_payload() -> dict:
    model = _model_payload()["data"]["vid-1"]["video_model"]
    return {"data": {"expire_time": 3600, "video_model": model}}


def test_catalog_indexes_only_explicit_series_and_reports_episode_status(tmp_path: Path):
    """A loose title field must not become a series search result."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    _write_capture(tmp_path, "model.json", _model_payload())
    _write_capture(
        tmp_path,
        "noise.json",
        {"series_id": "noise", "title": "剧情简介而不是剧名"},
    )

    catalog = HongguoCaptureCatalog(tmp_path)

    assert [item.series_id for item in catalog.search_exact("好一个 乖乖女")] == [
        "series-1"
    ]
    assert catalog.search_exact("剧情简介而不是剧名") == []
    episodes = catalog.episodes("series-1")
    assert [(item.episode, item.capture_status) for item in episodes] == [
        (1, "ready_clear"),
        (2, "paid"),
    ]
    assert catalog.best_variant("series-1", 1).main_url.endswith("high.mp4")


def test_catalog_links_single_video_model_capture_to_requested_vid(tmp_path: Path):
    """A single-video response must use the request vid recorded in its filename."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    _write_capture(
        tmp_path,
        "20260820_novel_player_video_model_v1__capture_vid_vid-1.json",
        _single_model_payload(),
    )

    catalog = HongguoCaptureCatalog(tmp_path)

    assert catalog.episode("series-1", 1).capture_status == "ready_clear"
    assert catalog.best_variant("series-1", 1).main_url.endswith("high.mp4")


def test_catalog_discovers_series_from_legacy_capture_directories(tmp_path: Path):
    """Rank/detail captures outside video_urls must still resolve a selectable series."""

    capture_root = tmp_path / "raw" / "video_urls"
    (capture_root / "raw").mkdir(parents=True)
    legacy_dir = tmp_path / "raw" / "mitm_responses_20260723"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "detail.json").write_text(
        json.dumps(_detail_payload(), ensure_ascii=False), encoding="utf-8"
    )

    catalog = HongguoCaptureCatalog(capture_root)

    matches = catalog.search_exact("好一个乖乖女")
    assert [item.series_id for item in matches] == ["series-1"]
    assert [item.episode for item in catalog.episodes("series-1")] == [1, 2]
    assert catalog.episodes("series-1")[0].capture_status == "waiting_capture"


def test_catalog_skips_rebuilding_when_capture_files_are_unchanged(tmp_path: Path):
    """Polling an unchanged capture directory must reuse the built index."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    catalog = HongguoCaptureCatalog(tmp_path)

    catalog.search_exact("好一个乖乖女")
    original_index = catalog._series
    catalog.search_exact("好一个乖乖女")

    assert catalog._series is original_index


def test_catalog_uses_confirmable_compatible_title_when_exact_is_absent(
    tmp_path: Path,
):
    """A selection-library suffix must not prevent resolving the captured series id."""

    _write_capture(tmp_path, "detail.json", _detail_payload())

    matches = HongguoCaptureCatalog(tmp_path).search_compatible(
        "好一个乖乖女第二部"
    )

    assert [item.series_id for item in matches] == ["series-1"]
    assert matches[0].title == "好一个乖乖女"


def test_catalog_retries_a_partially_written_json_file(tmp_path: Path):
    """A JSON decode failure must not poison the incremental file cache."""

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True)
    path = raw_dir / "detail.json"
    path.write_text('{"data":', encoding="utf-8")
    catalog = HongguoCaptureCatalog(tmp_path)

    assert catalog.search_exact("好一个乖乖女") == []

    path.write_text(json.dumps(_detail_payload(), ensure_ascii=False), encoding="utf-8")
    assert [item.series_id for item in catalog.search_exact("好一个乖乖女")] == [
        "series-1"
    ]


def test_catalog_prefers_clear_stream_over_higher_encrypted_stream(tmp_path: Path):
    """Clear streams avoid an unnecessary DRM transform when both are usable."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    payload = _model_payload(encrypted=True)
    _write_capture(tmp_path, "model.json", payload)

    variant = HongguoCaptureCatalog(tmp_path).best_variant("series-1", 1)

    assert variant.main_url.endswith("low.mp4")
    assert variant.encrypted is False


def test_catalog_marks_expired_playback_and_never_returns_it(tmp_path: Path):
    """Expired signed URLs must make the worker wait for a fresh capture."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    _write_capture(tmp_path, "model.json", _model_payload(expires=int(time.time()) - 1))
    catalog = HongguoCaptureCatalog(tmp_path)

    assert catalog.episodes("series-1")[0].capture_status == "expired"
    assert catalog.best_variant("series-1", 1) is None


def test_catalog_deduplicates_normalized_titles_and_supports_concurrent_reads(
    tmp_path: Path,
):
    first = _detail_payload()
    second = _detail_payload()
    second["data"]["series"]["series_id"] = "series-duplicate"
    second["data"]["series"]["series_title"] = "好一个 乖乖女"
    _write_capture(tmp_path, "first.json", first)
    _write_capture(tmp_path, "second.json", second)
    catalog = HongguoCaptureCatalog(tmp_path)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda _: catalog.suggest("乖乖女"), range(8))
        )

    assert all(len(rows) == 1 for rows in results)


def test_catalog_rejects_encrypted_variant_with_invalid_spade(tmp_path: Path):
    _write_capture(tmp_path, "detail.json", _detail_payload())
    payload = _model_payload(encrypted=True)
    payload["data"]["vid-1"]["video_model"] = json.dumps(
        {
            **json.loads(payload["data"]["vid-1"]["video_model"]),
            "video_list": [
                json.loads(payload["data"]["vid-1"]["video_model"])["video_list"][0]
            ],
        }
    )
    _write_capture(tmp_path, "model.json", payload)
    catalog = HongguoCaptureCatalog(tmp_path)

    assert catalog.episodes("series-1")[0].capture_status == "waiting_capture"
    assert catalog.best_variant("series-1", 1) is None


def test_settings_preserve_legacy_remote_credentials_without_exposing_secret(
    tmp_path: Path,
):
    """Legacy third-party settings remain usable while the secret stays private."""

    settings_file = tmp_path / "hongguo_drama_settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "config_url": "https://legacy.example.test/config",
                "activation_code": "secret",
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings == {
        "config_url": "https://legacy.example.test/config",
        "activation_code": "secret",
        "capture_root": DEFAULT_CAPTURE_ROOT,
        "capture_service_url": DEFAULT_CAPTURE_SERVICE_URL,
        "wait_timeout_seconds": 600,
    }
    assert public_settings(settings) == {
        "config_url": "https://legacy.example.test/config",
        "has_activation_code": True,
        "capture_root": DEFAULT_CAPTURE_ROOT,
        "capture_service_url": DEFAULT_CAPTURE_SERVICE_URL,
        "wait_timeout_seconds": 600,
    }


def test_settings_migrate_retired_default_config_entry_without_rewriting_file(
    tmp_path: Path,
):
    settings_file = tmp_path / "hongguo_drama_settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "config_url": "https://mizhi.kissm.top/pc/con.php",
                "activation_code": "secret",
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings["config_url"] == "https://mizhi.kissm.top/pc/go.php"
    assert settings["activation_code"] == "secret"
    assert json.loads(settings_file.read_text(encoding="utf-8"))["config_url"] == (
        "https://mizhi.kissm.top/pc/con.php"
    )


def test_capture_settings_require_absolute_root_and_bounded_timeout(tmp_path: Path):
    """Invalid admin configuration must be rejected before a worker starts."""

    with pytest.raises(ValueError, match="绝对路径"):
        save_settings(tmp_path, capture_root="relative/path", wait_timeout_seconds=600)
    with pytest.raises(ValueError, match="绝对路径"):
        save_settings(tmp_path, capture_root="", wait_timeout_seconds=600)
    with pytest.raises(ValueError, match="60 到 3600"):
        save_settings(tmp_path, capture_root=str(tmp_path), wait_timeout_seconds=30)
    with pytest.raises(ValueError, match="HTTP 地址"):
        save_settings(
            tmp_path,
            capture_root=str(tmp_path),
            capture_service_url="127.0.0.1:8776",
            wait_timeout_seconds=600,
        )

    settings = save_settings(
        tmp_path,
        capture_root=str(tmp_path),
        capture_service_url="http://127.0.0.1:8776/",
        wait_timeout_seconds=900,
    )
    assert settings == {
        "config_url": "https://mizhi.kissm.top/pc/go.php",
        "activation_code": "",
        "capture_root": str(tmp_path.resolve()),
        "capture_service_url": "http://127.0.0.1:8776",
        "wait_timeout_seconds": 900,
    }


def _synthetic_spade_for_key(key_hex: str) -> str:
    desired = bytes([0]) + key_hex.encode("ascii") + bytes(46)
    source = bytearray()
    previous_even = 246
    previous_odd = 85
    for index, transformed in enumerate(desired):
        previous = previous_odd if index & 1 else previous_even
        original = previous ^ ((transformed + 21 + index.bit_count()) & 255)
        source.append(original)
        if index & 1:
            previous_odd = original
        else:
            previous_even = original
    raw0 = 47 ^ source[0] ^ source[1]
    return base64.b64encode(bytes([raw0]) + bytes(source)).decode("ascii")


def test_spade_key_derivation_and_ctr_use_known_literals():
    """Changing the SPADE transform or CENC counter layout must break this test."""

    expected_key = "00112233445566778899aabbccddeeff"
    assert derive_content_key(_synthetic_spade_for_key(expected_key)).hex() == expected_key
    assert _cenc_ctr(bytes(range(16)), bytes(8), bytes(16)).hex() == (
        "c6a13b37878f5b826f4f8162a1c8d879"
    )


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body) + 8) + kind + body


def _minimal_cenc_mp4(key: bytes) -> tuple[bytes, bytes, int]:
    clear_sample = bytes(range(64))
    iv = b"fixture1"
    encrypted_sample = _cenc_ctr(key, iv, clear_sample)
    ftyp = _box(b"ftyp", b"isom" + bytes(4))

    def build_moov(aux_offset: int, sample_offset: int) -> bytes:
        stsz = _box(
            b"stsz",
            bytes(4) + struct.pack(">II", 0, 1) + struct.pack(">I", len(clear_sample)),
        )
        stco = _box(b"stco", bytes(4) + struct.pack(">II", 1, sample_offset))
        stsc = _box(
            b"stsc",
            bytes(4) + struct.pack(">I", 1) + struct.pack(">III", 1, 1, 1),
        )
        saiz = _box(b"saiz", bytes(4) + bytes([8]) + struct.pack(">I", 1))
        saio = _box(b"saio", bytes(4) + struct.pack(">II", 1, aux_offset))
        protection = _box(
            b"sinf", _box(b"frma", b"avc1") + _box(b"tenc", bytes(8))
        )
        stbl = _box(b"stbl", b"encv" + protection + stsz + stco + stsc + saiz + saio)
        return _box(b"moov", _box(b"trak", stbl))

    provisional = build_moov(0, 0)
    mdat_start = len(ftyp) + len(provisional)
    aux_offset = mdat_start + 8
    sample_offset = aux_offset + len(iv)
    moov = build_moov(aux_offset, sample_offset)
    encrypted = ftyp + moov + _box(b"mdat", iv + encrypted_sample)
    return encrypted, clear_sample, sample_offset


def test_spade_cenc_decrypts_minimal_mp4_and_rewrites_encryption_boxes():
    """The MP4 path must decrypt samples and remove all encryption markers."""

    key = bytes.fromhex("00112233445566778899aabbccddeeff")
    encrypted, clear_sample, sample_offset = _minimal_cenc_mp4(key)

    output = decrypt_mp4_cenc(encrypted, key)

    assert output[sample_offset : sample_offset + len(clear_sample)] == clear_sample
    assert b"avc1" in output
    assert b"hvc1" not in output
    assert b"encv" not in output
    assert b"tenc" not in output
    assert b"sinf" not in output


@pytest.mark.asyncio
async def test_capture_settings_test_reports_local_catalog_stats(
    tmp_path: Path, monkeypatch
):
    """The admin test endpoint must validate the unsaved capture directory."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path / "workspace")

    class FakeCaptureClient:
        def __init__(self, base_url):
            assert base_url == "http://127.0.0.1:8776"

        def health(self):
            return {"status": "ok"}

    monkeypatch.setattr(WebGUI, "HongguoVideoCaptureClient", FakeCaptureClient)
    monkeypatch.setattr(
        WebGUI.HongguoDramaClient,
        "refresh_third_party_providers",
        lambda self: {
            "provider_count": 1,
            "available_count": 1,
            "providers": [
                {
                    "provider_id": "hg",
                    "display_name": "红果源",
                    "status": "available",
                    "elapsed_ms": 1,
                }
            ],
        },
    )

    response = await WebGUI.test_hongguo_drama_interface(
        WebGUI.HongguoDramaSettingsRequest(
            capture_root=str(tmp_path),
            capture_service_url="http://127.0.0.1:8776",
            wait_timeout_seconds=900,
        )
    )

    assert response["status"] == "success"
    assert response["capture_root"] == str(tmp_path.resolve())
    assert response["series_count"] == 1
    assert response["raw_file_count"] == 1
    assert response["capture_service_status"] == "ok"


@pytest.mark.asyncio
async def test_capture_settings_test_rejects_non_ok_service_health(
    tmp_path: Path, monkeypatch
):
    """A reachable service that is not ready must not pass the admin check."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path / "workspace")

    class StartingCaptureClient:
        def __init__(self, _base_url):
            pass

        def health(self):
            return {"status": "starting"}

    monkeypatch.setattr(WebGUI, "HongguoVideoCaptureClient", StartingCaptureClient)
    monkeypatch.setattr(
        WebGUI.HongguoDramaClient,
        "refresh_third_party_providers",
        lambda self: {
            "provider_count": 1,
            "available_count": 1,
            "providers": [
                {
                    "provider_id": "hg",
                    "display_name": "红果源",
                    "status": "available",
                    "elapsed_ms": 1,
                }
            ],
        },
    )

    response = await WebGUI.test_hongguo_drama_interface(
        WebGUI.HongguoDramaSettingsRequest(
            capture_root=str(tmp_path),
            capture_service_url="http://127.0.0.1:8776",
            wait_timeout_seconds=600,
        )
    )

    assert response["status"] == "partial"
    service_check = next(
        item for item in response["checks"] if item["id"] == "capture_service"
    )
    assert service_check["status"] == "error"
    assert "未就绪" in service_check["message"]


@pytest.mark.asyncio
async def test_download_search_keeps_capture_matches_when_third_party_is_unavailable(
    tmp_path: Path, monkeypatch
):
    """Removing a captured match must return an empty result, not a Douyin fallback."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    monkeypatch.setattr(
        hongguo_drama,
        "build_third_party_registry",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ProviderFailure("provider_unavailable", "测试来源不可用")
        ),
    )
    monkeypatch.setattr(
        WebGUI,
        "load_hongguo_drama_settings",
        lambda _runtime: {
            "capture_root": str(tmp_path),
            "wait_timeout_seconds": 600,
        },
    )
    monkeypatch.setattr(
        WebGUI.DouyinDramaFallbackClient,
        "search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("红果抓包检索不得调用抖音")
        ),
    )
    WebGUI._drama_download_search_cache.clear()

    found = await WebGUI.search_hongguo_drama_for_download(
        WebGUI.HongguoDramaSearchRequest(title="好一个乖乖女")
    )
    missing = await WebGUI.search_hongguo_drama_for_download(
        WebGUI.HongguoDramaSearchRequest(title="从未抓包的剧")
    )

    assert found["source"] == "hongguo_providers"
    assert found["candidates"] == []
    assert found["capture_candidates"][0]["book_id"] == "series-1"
    assert missing["source"] == "hongguo_providers"
    assert missing["candidates"] == []
    assert missing["capture_candidates"] == []


@pytest.mark.asyncio
async def test_episode_api_exposes_capture_status_without_sensitive_urls(
    tmp_path: Path, monkeypatch
):
    """The browser needs capture state but must never receive signed media data."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    _write_capture(tmp_path, "model.json", _model_payload())
    monkeypatch.setattr(
        WebGUI,
        "load_hongguo_drama_settings",
        lambda _runtime: {
            "capture_root": str(tmp_path),
            "wait_timeout_seconds": 600,
        },
    )
    monkeypatch.setattr(WebGUI, "downloaded_hongguo_episode_statuses", lambda _title: {})

    response = await WebGUI.get_hongguo_drama_episodes(
        WebGUI.HongguoDramaEpisodesRequest(
            book_id="series-1", source_platform="hongguo_capture", title="好一个乖乖女"
        )
    )

    assert response["episodes"][0]["capture_status"] == "ready_clear"
    assert "url" not in json.dumps(response, ensure_ascii=False).lower()
    assert "spade" not in json.dumps(response, ensure_ascii=False).lower()


def test_wait_for_variant_observes_a_new_capture_file(tmp_path: Path):
    """Removing the poll refresh would leave a valid newly captured episode waiting."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    catalog = HongguoCaptureCatalog(tmp_path)
    current = [100.0]
    waits: list[int] = []

    def clock() -> float:
        return current[0]

    def sleeper(seconds: float) -> None:
        _write_capture(tmp_path, "model.json", _model_payload())
        current[0] += seconds

    downloader = CapturedMediaDownloader(
        catalog,
        wait_timeout_seconds=10,
        poll_interval_seconds=2,
        clock=clock,
        sleeper=sleeper,
        enable_ffprobe=False,
    )

    variant = downloader.wait_for_variant(
        "series-1", 1, on_wait=lambda remaining: waits.append(remaining)
    )

    assert variant.vid == "vid-1"
    assert waits == [10]


def test_wait_for_variant_excludes_all_variants_present_before_capture_request(
    tmp_path: Path,
):
    """A download request must wait for the POST-triggered capture, not reuse old URLs."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    _write_capture(tmp_path, "old-model.json", _model_payload())
    catalog = HongguoCaptureCatalog(tmp_path)
    baseline = catalog.variant_identities("series-1", 1)
    current = [100.0]

    def sleeper(seconds: float) -> None:
        fresh = _model_payload()
        model = json.loads(fresh["data"]["vid-1"]["video_model"])
        model["video_list"][0]["main_url"] = "https://fresh.example.test/new.mp4"
        fresh["data"]["vid-1"]["video_model"] = json.dumps(model)
        _write_capture(tmp_path, "new-model.json", fresh)
        current[0] += seconds

    downloader = CapturedMediaDownloader(
        catalog,
        wait_timeout_seconds=10,
        poll_interval_seconds=2,
        clock=lambda: current[0],
        sleeper=sleeper,
        enable_ffprobe=False,
    )

    variant = downloader.wait_for_variant(
        "series-1", 1, excluded=baseline
    )

    assert variant.main_url == "https://fresh.example.test/new.mp4"
    assert variant.source_file.endswith("new-model.json")


def test_wait_for_variant_reports_paid_capture_and_times_out(tmp_path: Path):
    detail = _detail_payload()
    detail["data"]["series"]["video_list"][0].pop("is_paid", None)
    capture_path = _write_capture(tmp_path, "detail.json", detail)
    current = [10.0]

    def clock() -> float:
        return current[0]

    def mark_paid(seconds: float) -> None:
        detail["data"]["series"]["video_list"][0]["is_paid"] = True
        capture_path.write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
        current[0] += seconds

    downloader = CapturedMediaDownloader(
        HongguoCaptureCatalog(tmp_path),
        wait_timeout_seconds=4,
        poll_interval_seconds=2,
        clock=clock,
        sleeper=mark_paid,
        enable_ffprobe=False,
    )
    with pytest.raises(CapturePaidError):
        downloader.wait_for_variant("series-1", 1)

    detail["data"]["series"]["video_list"][0].pop("is_paid")
    capture_path.write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
    timeout_downloader = CapturedMediaDownloader(
        HongguoCaptureCatalog(tmp_path),
        wait_timeout_seconds=2,
        poll_interval_seconds=1,
        clock=clock,
        sleeper=lambda seconds: current.__setitem__(0, current[0] + seconds),
        enable_ffprobe=False,
    )
    with pytest.raises(CaptureTimeoutError, match="等待第 1 集新抓包超时"):
        timeout_downloader.wait_for_variant("series-1", 1)


def _clear_mp4() -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + bytes(1200)


def test_downloader_adopts_valid_capture_service_artifact(tmp_path: Path):
    source = tmp_path / "service" / "E001.mp4"
    source.parent.mkdir()
    source.write_bytes(_clear_mp4())
    target = tmp_path / "job" / "video.mp4"
    downloader = CapturedMediaDownloader(
        HongguoCaptureCatalog(tmp_path), enable_ffprobe=False
    )

    result = downloader.adopt_local_artifact(source, target)

    assert target.read_bytes() == _clear_mp4()
    assert result == {
        "bytes": len(_clear_mp4()),
        "definition": "",
        "codec": "",
        "encrypted": False,
        "source_file": "E001.mp4",
    }


def test_downloader_accepts_capture_service_result_while_waiting(tmp_path: Path):
    class EmptyCatalog:
        def best_variant(self, *_args, **_kwargs):
            return None

        def episode(self, *_args, **_kwargs):
            return None

    expected = {
        "bytes": 1204,
        "definition": "",
        "codec": "",
        "encrypted": False,
        "source_file": "E001.mp4",
    }
    downloader = CapturedMediaDownloader(
        EmptyCatalog(),
        wait_timeout_seconds=5,
        sleeper=lambda _seconds: pytest.fail("service artifact should stop polling"),
        enable_ffprobe=False,
    )

    result = downloader.download_episode(
        "series-1", 1, tmp_path / "unused.mp4", on_wait=lambda _remaining: expected
    )

    assert result == expected


def test_cenc_detection_ignores_marker_bytes_inside_media_samples(tmp_path: Path):
    """Coincidental marker text in mdat payload must not classify a clear MP4 as CENC."""

    payload = bytes(600) + b"tenc/encv/enca" + bytes(600)
    clear = _box(b"ftyp", b"isom" + bytes(4)) + _box(b"mdat", payload)
    encrypted = _box(b"ftyp", b"isom" + bytes(4)) + _box(
        b"moov", _box(b"tenc", bytes(8))
    )
    clear_path = tmp_path / "clear.mp4"
    encrypted_path = tmp_path / "encrypted.mp4"
    clear_path.write_bytes(clear)
    encrypted_path.write_bytes(encrypted)

    assert CapturedMediaDownloader._file_contains_any_marker(clear_path) is False
    assert CapturedMediaDownloader._file_contains_any_marker(encrypted_path) is True


def test_download_retries_main_then_uses_backup_without_leaking_urls(tmp_path: Path):
    """A broken primary CDN must not prevent the ordered backup attempt."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    _write_capture(tmp_path, "model.json", _model_payload())
    variant = HongguoCaptureCatalog(tmp_path).best_variant("series-1", 1)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url).endswith("high.mp4"):
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            content=_clear_mp4(),
            headers={"content-length": str(len(_clear_mp4()))},
            request=request,
        )

    downloader = CapturedMediaDownloader(
        HongguoCaptureCatalog(tmp_path),
        max_attempts_per_url=3,
        retry_delay_seconds=0,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        enable_ffprobe=False,
    )
    target = tmp_path / "video.mp4"

    result = downloader.download_variant(variant, target)

    assert calls[:3] == ["https://video.example.test/high.mp4"] * 3
    assert calls[3] == "https://video.example.test/high-backup.mp4"
    assert target.read_bytes() == _clear_mp4()
    assert result == {
        "bytes": len(_clear_mp4()),
        "definition": "1080p",
        "codec": "h264",
        "encrypted": False,
        "source_file": "model.json",
    }
    assert "video.example" not in json.dumps(result)


def test_capture_download_resumes_and_enforces_size_limit(tmp_path: Path):
    _write_capture(tmp_path, "detail.json", _detail_payload())
    _write_capture(tmp_path, "model.json", _model_payload())
    catalog = HongguoCaptureCatalog(tmp_path)
    variant = catalog.best_variant("series-1", 1)
    payload = _clear_mp4()
    split = 400
    target = tmp_path / "resume.mp4"
    part = target.with_suffix(".mp4.part")
    meta = target.with_suffix(".mp4.part.json")
    part.write_bytes(payload[:split])
    meta.write_text(
        json.dumps(
            {
                "url_hash": hashlib.sha256(
                    variant.main_url.encode("utf-8")
                ).hexdigest()
            }
        ),
        encoding="utf-8",
    )

    def resume_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == f"bytes={split}-"
        return httpx.Response(
            206,
            content=payload[split:],
            headers={
                "content-length": str(len(payload) - split),
                "content-range": f"bytes {split}-{len(payload) - 1}/{len(payload)}",
            },
            request=request,
        )

    downloader = CapturedMediaDownloader(
        catalog,
        max_attempts_per_url=1,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(resume_handler)
        ),
        enable_ffprobe=False,
    )
    downloader.download_variant(variant, target)
    assert target.read_bytes() == payload

    def oversized_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"",
            headers={"content-length": "2048"},
            request=request,
        )

    limited = CapturedMediaDownloader(
        catalog,
        max_bytes=1024,
        max_attempts_per_url=1,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(oversized_handler)
        ),
        enable_ffprobe=False,
    )
    with pytest.raises(CaptureDownloadError, match="大小限制"):
        limited.download_variant(variant, tmp_path / "oversized.mp4")


def test_download_failure_preserves_partial_file_for_diagnosis(tmp_path: Path):
    """Deleting the last failed payload would remove the required audit evidence."""

    _write_capture(tmp_path, "detail.json", _detail_payload())
    _write_capture(tmp_path, "model.json", _model_payload())
    variant = HongguoCaptureCatalog(tmp_path).best_variant("series-1", 1)
    invalid = b"not-an-mp4" * 200

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=invalid, request=request)

    downloader = CapturedMediaDownloader(
        HongguoCaptureCatalog(tmp_path),
        max_attempts_per_url=1,
        retry_delay_seconds=0,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        enable_ffprobe=False,
    )
    target = tmp_path / "video.mp4"

    with pytest.raises(CaptureDownloadError, match="有效 MP4"):
        downloader.download_variant(variant, target)

    assert target.with_suffix(".mp4.part").read_bytes() == invalid
    assert not target.exists()


def test_download_errors_never_include_captured_urls(tmp_path: Path):
    _write_capture(tmp_path, "detail.json", _detail_payload())
    _write_capture(tmp_path, "model.json", _model_payload())
    variant = HongguoCaptureCatalog(tmp_path).best_variant("series-1", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    downloader = CapturedMediaDownloader(
        HongguoCaptureCatalog(tmp_path),
        max_attempts_per_url=1,
        retry_delay_seconds=0,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        enable_ffprobe=False,
    )

    with pytest.raises(CaptureDownloadError) as captured:
        downloader.download_variant(variant, tmp_path / "private.mp4")

    assert "https://" not in str(captured.value).lower()
    assert "video.example" not in str(captured.value)


def test_download_worker_writes_redacted_capture_artifacts(tmp_path: Path, monkeypatch):
    """A successful capture job must not persist signed URLs or SPADE material."""

    capture_root = tmp_path / "captures"
    _write_capture(capture_root, "detail.json", _detail_payload())
    _write_capture(capture_root, "old-model.json", _model_payload())
    events: list[tuple[str, str, dict]] = []
    actions: list[tuple[object, ...]] = []

    class FakeCaptureClient:
        def __init__(self, base_url):
            assert base_url == "http://127.0.0.1:8776"

        def start_capture(self, title, series_id, episode):
            actions.append(("capture", title, series_id, episode))
            return {"job_id": "remote-capture-1", "status": "queued"}

        def get_job(self, job_id):
            return {"job_id": job_id, "status": "running:opening"}

    class FakeDownloader:
        def __init__(self, catalog, **kwargs):
            assert catalog.capture_root == capture_root
            assert kwargs["wait_timeout_seconds"] == 600

        def download_episode(self, series_id, episode, target, **kwargs):
            assert (series_id, episode) == ("series-1", 1)
            assert kwargs["initial_excluded"]
            actions.append(("download", series_id, episode))
            Path(target).write_bytes(_clear_mp4())
            return {
                "bytes": len(_clear_mp4()),
                "definition": "1080p",
                "codec": "h264",
                "encrypted": True,
                "source_file": "model.json",
            }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        hongguo_drama_download_worker,
        "load_settings",
        lambda: {
            "capture_root": str(capture_root),
            "capture_service_url": "http://127.0.0.1:8776",
            "wait_timeout_seconds": 600,
        },
    )
    monkeypatch.setattr(
        hongguo_drama_download_worker,
        "HongguoVideoCaptureClient",
        FakeCaptureClient,
    )
    monkeypatch.setattr(
        hongguo_drama_download_worker, "CapturedMediaDownloader", FakeDownloader
    )
    monkeypatch.setattr(
        hongguo_drama_download_worker,
        "emit",
        lambda job, event, message, level="info", payload=None: events.append(
            (event, message, payload or {})
        ),
    )
    job = CrawlJob(
        job_id="capture-job",
        platform="hg",
        crawler_type="drama_download",
        extra={
            "title": "好一个乖乖女第二部",
            "book_id": "series-1",
            "episodes": [1],
            "all_episodes": False,
            "source_platform": "hongguo_capture",
        },
    )

    hongguo_drama_download_worker.run(job)

    assert actions == [
        ("capture", "好一个乖乖女", "series-1", 1),
        ("download", "series-1", 1),
    ]

    root = (
        tmp_path
        / "data"
        / "hg"
        / "jobs"
        / "capture-job"
        / "dramas"
        / "好一个乖乖女第二部"
    )
    source = json.loads((root / "episode_01" / "source.json").read_text("utf-8"))
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    serialized = json.dumps({"source": source, "manifest": manifest})
    assert manifest["items"][0]["status"] == "succeeded"
    assert source["capture"]["source_file"] == "model.json"
    assert "http" not in serialized
    assert "spade" not in serialized.lower()
    assert events[-1][0] == "job_finished"


def test_analysis_worker_uses_capture_download_before_transcription(tmp_path: Path, monkeypatch):
    """The analysis endpoint must not retain the removed play-url dependency."""

    capture_root = tmp_path / "captures"
    _write_capture(capture_root, "detail.json", _detail_payload())
    actions: list[tuple[object, ...]] = []

    class FakeCaptureClient:
        def __init__(self, base_url):
            assert base_url == "http://127.0.0.1:8776"

        def start_capture(self, title, series_id, episode):
            actions.append(("capture", title, series_id, episode))
            return {"job_id": "analysis-remote-capture", "status": "queued"}

        def get_job(self, job_id):
            return {"job_id": job_id, "status": "running:opening"}

    class FakeDownloader:
        def __init__(self, catalog, **kwargs):
            assert catalog.capture_root == capture_root

        def download_episode(self, series_id, episode, target, **kwargs):
            actions.append(("download", series_id, episode))
            Path(target).write_bytes(_clear_mp4())
            return {
                "bytes": len(_clear_mp4()),
                "definition": "720p",
                "codec": "h264",
                "encrypted": False,
                "source_file": "model.json",
            }

    class FakeAsr:
        async def transcribe(self, _path):
            return {"text": "这是抓包视频的转写", "segments": []}

    async def fake_convert(_video, audio, _ffmpeg):
        Path(audio).write_bytes(b"wav")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        hongguo_drama_worker,
        "load_settings",
        lambda: {
            "capture_root": str(capture_root),
            "capture_service_url": "http://127.0.0.1:8776",
            "wait_timeout_seconds": 600,
        },
    )
    monkeypatch.setattr(
        hongguo_drama_worker,
        "HongguoVideoCaptureClient",
        FakeCaptureClient,
    )
    monkeypatch.setattr(hongguo_drama_worker, "CapturedMediaDownloader", FakeDownloader)
    monkeypatch.setattr(hongguo_drama_worker, "convert_audio_to_wav", fake_convert)
    monkeypatch.setattr(hongguo_drama_worker, "create_asr_provider", lambda: FakeAsr())
    monkeypatch.setattr(hongguo_drama_worker, "emit", lambda *args, **kwargs: None)
    job = CrawlJob(
        job_id="analysis-capture",
        platform="hg",
        crawler_type="drama_analysis",
        extra={
            "title": "好一个乖乖女",
            "book_id": "series-1",
            "episode": 1,
            "source_platform": "hongguo_capture",
        },
    )

    hongguo_drama_worker.run(job)

    assert actions == [
        ("capture", "好一个乖乖女", "series-1", 1),
        ("download", "series-1", 1),
    ]

    root = (
        tmp_path
        / "data"
        / "hg"
        / "jobs"
        / "analysis-capture"
        / "dramas"
        / "好一个乖乖女"
        / "episode_01"
    )
    assert "这是抓包视频的转写" in (root / "script.md").read_text("utf-8")
    serialized = (root / "source.json").read_text("utf-8")
    assert "http" not in serialized
    assert "spade" not in serialized.lower()
