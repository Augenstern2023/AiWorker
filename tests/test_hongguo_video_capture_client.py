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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from agents.hongguo_video_capture_client import (
    HongguoVideoCaptureClient,
    HongguoVideoCaptureServiceError,
    completed_capture_artifact,
)


def test_completed_capture_artifact_returns_only_requested_episode_under_root(
    tmp_path: Path,
) -> None:
    capture_root = tmp_path / "raw" / "video_urls"
    output_dir = tmp_path / "downloads" / "测试短剧"
    output_dir.mkdir(parents=True)
    episode_file = output_dir / "E001.mp4"
    episode_file.write_bytes(b"video")

    result = completed_capture_artifact(
        {
            "status": "completed",
            "downloaded": True,
            "decrypted": True,
            "output_path": str(output_dir),
        },
        1,
        capture_root,
    )

    assert result == episode_file.resolve()
    assert (
        completed_capture_artifact(
            {
                "status": "completed",
                "downloaded": True,
                "decrypted": True,
                "output_path": str(tmp_path.parent),
            },
            1,
            capture_root,
        )
        is None
    )


def test_video_capture_client_uses_8776_contract() -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, payload))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/videos/capture":
            return httpx.Response(
                202,
                json={
                    "job_id": "capture-1",
                    "title": "测试短剧",
                    "series_id": "series-1",
                    "episode": 2,
                    "status": "queued",
                },
            )
        return httpx.Response(
            200,
            json={
                "job_id": "capture-1",
                "title": "测试短剧",
                "series_id": "series-1",
                "episode": 2,
                "status": "running:opening",
            },
        )

    client = HongguoVideoCaptureClient(
        "http://127.0.0.1:8776",
        transport=httpx.MockTransport(handler),
    )

    assert client.health()["status"] == "ok"
    assert client.start_capture("测试短剧", "series-1", 2)["job_id"] == "capture-1"
    assert client.get_job("capture-1")["status"] == "running:opening"
    assert requests == [
        ("GET", "/health", None),
        (
            "POST",
            "/v1/videos/capture",
            {"title": "测试短剧", "series_id": "series-1", "episode": 2},
        ),
        ("GET", "/v1/videos/jobs/capture-1", None),
    ]


def test_video_capture_client_reports_service_failure_without_leaking_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            request=request,
            json={"error": "configured emulator is not connected to ADB"},
        )

    client = HongguoVideoCaptureClient(
        "http://127.0.0.1:8776",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HongguoVideoCaptureServiceError, match="模拟器"):
        client.start_capture("测试短剧", "series-1", 1)


def test_video_capture_client_redacts_unknown_service_error_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "job_id": "capture-1",
                "status": "failed",
                "error": (
                    "download https://private.example/video?token=abc failed; "
                    "Cookie=session-secret; device=emulator-5554; "
                    r"path=E:\hg\private\capture.json"
                ),
            },
        )

    client = HongguoVideoCaptureClient(
        "http://127.0.0.1:8776",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HongguoVideoCaptureServiceError) as captured:
        client.get_job("capture-1")

    message = str(captured.value)
    assert message == "视频抓包任务执行失败"
    assert "private.example" not in message
    assert "session-secret" not in message
    assert "emulator-5554" not in message
    assert "E:\\hg" not in message


def test_video_capture_client_ignores_process_proxy_for_loopback_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        client = HongguoVideoCaptureClient(
            f"http://127.0.0.1:{server.server_address[1]}"
        )

        assert client.health() == {"status": "ok"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
