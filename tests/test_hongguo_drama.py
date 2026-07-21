import json
from pathlib import Path

import pytest

import WebGUI
from agents import hongguo_drama
from agents.hongguo_drama import HongguoDramaClient, HongguoDramaError, HongguoPaidContentError, load_settings, public_settings, save_settings
from agents import douyin_drama_fallback_worker, hongguo_drama_download_worker, hongguo_drama_worker
from core.job import CrawlJob
from runtime.worker_launcher import build_worker_command


def test_paid_content_policy_requires_explicit_provider_signal():
    from agents.drama_download_policy import paid_content_reason

    assert paid_content_reason({"desc": "VIP会员短剧完整版"}) == ""
    assert paid_content_reason({"is_paid_content": True}) == "接口标记 is_paid_content"
    assert paid_content_reason({"series_paid_info": {"price": 199}}) == "接口标记 series_paid_info.price=199"


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


def test_douyin_episode_metadata_parses_ranges_and_single_episodes():
    from agents.douyin_drama_fallback import parse_episode_metadata

    assert parse_episode_metadata("短剧 1-68集完整版") == {"episode_start": 1, "episode_end": 68, "episode_count": 68, "episode_label": "1-68 集"}
    assert parse_episode_metadata("短剧 第12集") == {"episode_start": 12, "episode_end": 12, "episode_count": 1, "episode_label": "第 12 集"}
    assert parse_episode_metadata("没有集数标识") == {"episode_start": None, "episode_end": None, "episode_count": None, "episode_label": ""}


def test_hongguo_drama_settings_hide_activation_code(tmp_path):
    save_settings(tmp_path, config_url="https://config.example.test", activation_code="secret-code")

    settings = load_settings(tmp_path)

    assert settings["activation_code"] == "secret-code"
    assert public_settings(settings) == {
        "config_url": "https://config.example.test",
        "has_activation_code": True,
    }


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


def test_hongguo_drama_client_rejects_out_of_range_episode_data(monkeypatch):
    client = HongguoDramaClient({"config_url": "https://config.example.test", "activation_code": ""})
    client.interfaces = {"search": "search", "book": "book", "play": "play"}
    monkeypatch.setattr(client, "_json", lambda url, **params: {"data": [{"title": "没有视频 ID"}]})

    with pytest.raises(HongguoDramaError, match="未找到可播放剧集"):
        client.episodes("book-1")


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

        def episodes(self, book_id):
            assert book_id == "book-1"
            return [
                {"episode": 1, "video_id": "video-1", "title": "第一集"},
                {"episode": 2, "video_id": "video-2", "title": "第二集"},
            ]

    manifest = tmp_path / "data" / "hg" / "jobs" / "job-old" / "dramas" / "测试短剧" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"items": [{"episode": 1, "status": "succeeded", "path": "episode_01/video.mp4"}]}), encoding="utf-8")
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", FakeClient)
    monkeypatch.setattr(WebGUI.job_manager, "list_jobs", lambda: [{
        "job_id": "job-old",
        "crawler_type": "drama_download",
        "job": {"extra": {"book_id": "book-1"}},
        "artifacts": [{"path": str(manifest.relative_to(tmp_path))}],
    }])

    response = await WebGUI.get_hongguo_drama_episodes(WebGUI.HongguoDramaEpisodesRequest(book_id="book-1"))

    assert response["episodes"][0]["download_status"] == {"status": "downloaded", "job_id": "job-old", "path": "episode_01/video.mp4"}
    assert "download_status" not in response["episodes"][1]


@pytest.mark.asyncio
async def test_drama_search_api_hides_raw_provider_record(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, settings):
            pass

        def search(self, title):
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
async def test_drama_search_falls_back_to_douyin_after_hongguo_empty(tmp_path, monkeypatch):
    class EmptyHongguoClient:
        def __init__(self, settings):
            pass

        def search(self, title):
            return []

    class FakeFallbackClient:
        async def search(self, title):
            return [{"source": "douyin_fallback", "aweme_id": "aweme-1", "title": title}]

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", EmptyHongguoClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", FakeFallbackClient)

    response = await WebGUI.search_hongguo_drama(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))

    assert response["source"] == "douyin_fallback"
    assert response["fallback_reason"] == "红果未找到匹配剧目"
    assert response["candidates"][0]["aweme_id"] == "aweme-1"


@pytest.mark.asyncio
async def test_drama_search_falls_back_to_douyin_after_hongguo_error(tmp_path, monkeypatch):
    class BrokenHongguoClient:
        def __init__(self, settings):
            pass

        def search(self, title):
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


@pytest.mark.asyncio
async def test_download_search_calls_douyin_when_no_exact_hongguo_match(tmp_path, monkeypatch):
    class HongguoClient:
        def __init__(self, settings):
            pass

        def search_exact(self, title):
            return []

    class FakeFallbackClient:
        async def search(self, title):
            return [{"source": "douyin_fallback", "aweme_id": "aweme-9", "title": title}]

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "HongguoDramaClient", HongguoClient)
    monkeypatch.setattr(WebGUI, "DouyinDramaFallbackClient", FakeFallbackClient)

    response = await WebGUI.search_hongguo_drama_for_download(WebGUI.HongguoDramaSearchRequest(title="测试短剧"))

    assert response["source"] == "douyin_fallback"
    assert response["fallback_reason"] == "红果接口未返回剧名完全匹配结果"
    assert response["candidates"][0]["aweme_id"] == "aweme-9"


@pytest.mark.asyncio
async def test_download_search_does_not_call_douyin_when_exact_hongguo_match_exists(tmp_path, monkeypatch):
    class HongguoClient:
        def __init__(self, settings):
            pass

        def search_exact(self, title):
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


@pytest.mark.asyncio
async def test_download_search_reports_douyin_fallback_when_douyin_is_unavailable(tmp_path, monkeypatch):
    class EmptyHongguoClient:
        def __init__(self, settings):
            pass

        def search_exact(self, title):
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


@pytest.mark.asyncio
async def test_douyin_fallback_job_accepts_ordered_multiple_aweme_ids(monkeypatch):
    captured = {}

    class FakeState:
        def to_dict(self):
            return {"job_id": "job-batch", "extra": captured["job"].extra}

    def fake_create(job):
        captured["job"] = job
        return FakeState()

    monkeypatch.setattr(WebGUI.job_manager, "generate_job_id", lambda platform: "job-batch")
    monkeypatch.setattr(WebGUI.job_manager, "create_job", fake_create)

    response = await WebGUI.create_douyin_fallback_drama_job(
        WebGUI.DouyinFallbackDramaJobRequest(
            title="测试短剧",
            aweme_ids=["aweme-2", "aweme-1", "aweme-2"],
            fallback_reason="红果无结果",
        )
    )

    assert response["status"] == "success"
    assert captured["job"].extra["aweme_ids"] == ["aweme-2", "aweme-1"]
    assert captured["job"].extra["aweme_id"] == "aweme-2"


@pytest.mark.asyncio
async def test_drama_suggest_returns_hongguo_candidates_without_douyin_fallback(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, settings):
            pass

        def search(self, title):
            return [
                {"book_id": "book-1", "title": title, "raw": {"secret": True}},
                {"book_id": "book-2", "title": "另一个剧"},
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
        {"book_id": "book-2", "title": "另一个剧"},
    ]


@pytest.mark.asyncio
async def test_drama_interface_test_uses_unsaved_form_settings(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, settings):
            self.settings = settings

        def refresh_interfaces(self):
            assert self.settings["config_url"] == "https://temporary.example.test/config"
            assert self.settings["activation_code"] == "temporary-code"
            return {"search": "https://example.test/search", "book": "https://example.test/book", "play": "https://example.test/play"}

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
