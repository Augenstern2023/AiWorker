# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import WebGUI
from web.navigation_service import NavigationService, PAGE_ROUTES
from web.page_router import PAGE_TEMPLATES, _page_response


def test_downloaded_drama_library_groups_episode_videos_without_exposing_paths(
    tmp_path: Path, monkeypatch
):
    data_root = tmp_path / "data"
    drama_root = (
        data_root / "hg" / "jobs" / "job-1" / "dramas" / "测试短剧"
    )
    (drama_root / "episode_02").mkdir(parents=True)
    (drama_root / "episode_01").mkdir(parents=True)
    (drama_root / "episode_02" / "second.mp4").write_bytes(b"episode-2")
    (drama_root / "episode_01" / "first.mp4").write_bytes(b"episode-1")
    (data_root / "uploads").mkdir(parents=True)
    (data_root / "uploads" / "unrelated.mp4").write_bytes(b"not a drama")
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    library = WebGUI.list_downloaded_drama_library()

    assert len(library) == 1
    drama = library[0]
    assert drama["title"] == "测试短剧"
    assert [episode["number"] for episode in drama["episodes"]] == [1, 2]
    assert all(episode["id"] for episode in drama["episodes"])
    assert "path" not in drama
    assert all("path" not in episode for episode in drama["episodes"])


def test_downloaded_drama_library_merges_same_title_across_download_jobs(
    tmp_path: Path, monkeypatch
):
    data_root = tmp_path / "data"
    first_episode = data_root / "hg" / "jobs" / "job-1" / "dramas" / "连续剧" / "episode_01"
    second_episode = data_root / "hg" / "jobs" / "job-2" / "dramas" / "连续剧" / "episode_02"
    first_episode.mkdir(parents=True)
    second_episode.mkdir(parents=True)
    (first_episode / "video.mp4").write_bytes(b"episode-1")
    (second_episode / "video.mp4").write_bytes(b"episode-2")
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    library = WebGUI.list_downloaded_drama_library()

    assert len(library) == 1
    assert library[0]["title"] == "连续剧"
    assert [episode["number"] for episode in library[0]["episodes"]] == [1, 2]


def test_drama_player_episode_token_rejects_files_outside_downloaded_drama_jobs(
    tmp_path: Path, monkeypatch
):
    data_root = tmp_path / "data"
    episode_dir = data_root / "dy" / "jobs" / "job-1" / "dramas" / "测试短剧" / "episode_01"
    episode_dir.mkdir(parents=True)
    video = episode_dir / "episode.mp4"
    video.write_bytes(b"episode")
    unrelated = data_root / "uploads" / "unrelated.mp4"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"not a drama")
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    episode = WebGUI.list_downloaded_drama_library()[0]["episodes"][0]

    assert WebGUI.resolve_downloaded_drama_episode(episode["id"]) == video.resolve()
    with pytest.raises(HTTPException) as exc:
        WebGUI.resolve_downloaded_drama_episode(WebGUI._drama_player_token(unrelated))
    assert exc.value.status_code == 404


def test_drama_player_library_and_stream_endpoints_require_media_preview_permission():
    client = TestClient(WebGUI.app)
    paths = {route.path for route in WebGUI.app.routes}

    assert "/api/drama-player/library" in paths
    assert "/api/drama-player/episodes/{episode_id}/stream" in paths
    assert WebGUI._api_permission("GET", "/api/drama-player/library") == "data.media.preview"
    assert client.get("/api/drama-player/library").status_code == 401
    assert client.get("/api/drama-player/episodes/invalid/stream").status_code == 401


@pytest.mark.asyncio
async def test_drama_player_streams_a_signed_episode_with_http_range(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "data"
    episode_dir = data_root / "hg" / "jobs" / "job-1" / "dramas" / "测试短剧" / "episode_01"
    episode_dir.mkdir(parents=True)
    (episode_dir / "episode.mp4").write_bytes(b"0123456789")
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())
    episode = WebGUI.list_downloaded_drama_library()[0]["episodes"][0]
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"range", b"bytes=2-5")]})
    principal = WebGUI.Principal(
        user_id="owner-1", username="owner", display_name="负责人", role_codes=("project_owner",),
        permissions=frozenset({"data.media.preview"}), data_scope="managed_projects", session_id="session-1",
    )

    response = await WebGUI.stream_downloaded_drama_episode(episode["id"], request, principal)
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert body == b"2345"


def test_drama_player_page_is_a_data_management_destination():
    assert ("/admin/drama-player", "page.data_management", "已下载剧目播放") in PAGE_ROUTES
    assert PAGE_TEMPLATES["/admin/drama-player"] == "admin/drama_player.html"
    navigation = NavigationService().items_for(
        WebGUI.Principal(
            user_id="owner-1",
            username="owner",
            display_name="负责人",
            role_codes=("project_owner",),
            permissions=frozenset({"page.review", "page.data_management", "data.media.preview"}),
            data_scope="managed_projects",
            session_id="session-1",
        )
    )
    project_management = next(item for item in navigation if item["label"] == "项目管理")

    assert {item["route"] for item in project_management["items"]} >= {
        "/admin/data",
        "/admin/drama-player",
    }


def test_drama_player_page_sets_the_workspace_name_used_by_its_initializer():
    principal = WebGUI.Principal(
        user_id="owner-1", username="owner", display_name="负责人", role_codes=("project_owner",),
        permissions=frozenset({"page.review", "page.data_management", "data.media.preview"}),
        data_scope="managed_projects", session_id="session-1",
    )
    request = Request({"type": "http", "method": "GET", "path": "/admin/drama-player", "headers": [], "app": WebGUI.app})

    response = _page_response(
        WebGUI.templates,
        NavigationService(),
        request,
        principal,
        page_title="已下载剧目播放",
        template_name="admin/drama_player.html",
    )

    assert b'data-workspace-page="admin-drama-player"' in response.body


def test_drama_player_client_includes_refresh_resume_and_auto_next_controls():
    script = Path("static/js/admin/drama-player.js").read_text(encoding="utf-8")

    assert "/api/drama-player/library" in script
    assert "localStorage" in script
    assert "handleDramaPlayerEnded" in script
    assert "playDramaPlayerRelative(1)" in script
    assert "playbackRate" in script
    assert "saved.autoNext" in script
    assert "saved.speed" in script


def test_drama_player_uses_template_nodes_for_alpine_repeated_content():
    template = Path("templates/admin/drama_player.html").read_text(encoding="utf-8")

    assert '<template x-for="drama in dramaPlayer.dramas" :key="drama.id">' in template
    assert '<template x-for="episode in selectedDramaPlayerEpisodes()" :key="episode.id">' in template
    assert '<button class="drama-player-drama" type="button" x-for=' not in template
    assert '<button class="btn btn-sm" type="button" x-for=' not in template
