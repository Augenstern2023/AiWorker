# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import csv
from io import BytesIO, StringIO
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from analysis.today_rank_service import TodayRankService
from core.job import CrawlJob
from creative.api import creative_api_principal, register_creative_routes
from creative.lifecycle_service import content_fingerprint, current_beats_content
from creative.repository import CreativeRepository
from creative.topic_service import TopicService
from identity.dependencies import current_principal
from identity.schema import Principal
from runtime.job_state import JobState


class LifecycleJobManager:
    def __init__(self) -> None:
        self.states: list[JobState] = []

    def create_job(self, data: dict) -> JobState:
        payload = dict(data)
        payload.setdefault("job_id", f"creative-{len(self.states) + 1}")
        state = JobState(job=CrawlJob.from_dict(payload), status="running")
        self.states.append(state)
        return state

    def list_jobs(self) -> list[dict]:
        return [state.to_dict() for state in self.states]


def build_client(tmp_path: Path) -> tuple[TestClient, CreativeRepository, LifecycleJobManager]:
    runtime_dir = tmp_path / ".runtime"
    manager = LifecycleJobManager()
    app = FastAPI()
    principal = Principal(
        user_id="lifecycle-admin",
        username="lifecycle-admin",
        display_name="Lifecycle Admin",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="lifecycle-session",
    )
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    with patch.object(
        TodayRankService,
        "from_environment",
        return_value=TodayRankService.from_files(root=tmp_path),
    ):
        register_creative_routes(
            app,
            root=tmp_path,
            runtime_dir=runtime_dir,
            job_manager=manager,
        )
    return TestClient(app), CreativeRepository(runtime_dir / "creative" / "creative.db"), manager


def seed_topic(
    repository: CreativeRepository,
    title: str = "生命周期短剧",
    platform: str = "hg",
) -> dict:
    return repository.upsert_topics([{
        "platform": platform,
        "job_id": f"rank-{title}",
        "source_path": f"data/{platform}/{title}.json",
        "captured_at": "2026-07-21",
        "title": title,
        "description": "生命周期测试",
        "category": "都市",
        "rank_type": "热播榜",
        "rank_source": "热榜" if platform == "dy" else "全部热播榜",
        "score": 90,
        "confidence": "high",
        "score_details": {},
        "recommendation": "推荐",
        "evidence": [],
    }])[0]


def write_script(root: Path, title: str, episode: int) -> None:
    episode_dir = root / "data" / "hg" / "jobs" / "download-1" / "dramas" / title / f"episode_{episode:02d}"
    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / "source.json").write_text(
        f'{{"title":"{title}","episode":{{"episode":{episode}}}}}',
        encoding="utf-8",
    )
    (episode_dir / "script.md").write_text(f"## 转写内容\n\n第 {episode} 集正文", encoding="utf-8")


def write_downloaded_video(root: Path, title: str, episode: int) -> None:
    episode_dir = root / "data" / "hg" / "jobs" / "download-video" / "dramas" / title / f"episode_{episode:02d}"
    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / "source.json").write_text(
        f'{{"title":"{title}","episode":{{"episode":{episode}}}}}',
        encoding="utf-8",
    )
    (episode_dir / "video.mp4").write_bytes(b"video")


def topic_payload(client: TestClient) -> dict:
    response = client.get("/api/creative/topics?limit=500")
    assert response.status_code == 200
    return response.json()["items"][0]


def test_topic_lifecycle_follows_the_complete_creation_flow(tmp_path: Path):
    client, repository, manager = build_client(tmp_path)
    topic = seed_topic(repository)

    assert topic_payload(client)["lifecycle_status"] == "pending_review"
    assert client.get(f"/api/creative/topics/{topic['id']}").json()["lifecycle_status"] == "pending_review"
    adopted = client.patch(f"/api/creative/topics/{topic['id']}", json={"status": "adopted"})
    assert adopted.status_code == 200

    manager.create_job({
        "platform": "hg",
        "crawler_type": "drama_download",
        "extra": {"title": topic["title"], "book_id": "book-1"},
    })
    assert topic_payload(client)["lifecycle_status"] == "pending_download"

    write_script(tmp_path, topic["title"], 1)
    write_script(tmp_path, topic["title"], 2)
    downloaded = topic_payload(client)
    assert downloaded["lifecycle_status"] == "downloaded"
    assert downloaded["lifecycle_label"] == "已下载 2 集"
    assert downloaded["downloaded_episode_count"] == 2
    assert downloaded["transcript_episode_count"] == 2

    breakdown = repository.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    assert topic_payload(client)["lifecycle_status"] == "pending_breakdown"
    repository.lock_breakdown(breakdown["id"])
    assert topic_payload(client)["lifecycle_status"] == "breakdown_completed"

    project = repository.create_project(topic["id"], breakdown["id"], "生命周期项目", 1)
    current = topic_payload(client)
    assert current["lifecycle_status"] == "pending_imitation"
    assert current["lifecycle_project_id"] == project["id"]

    blueprint = repository.create_blueprints(project["id"], [{
        "content": {"title": "新方向"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    assert topic_payload(client)["lifecycle_status"] == "imitation_generated"

    repository.lock_brief(blueprint["id"])
    assert topic_payload(client)["lifecycle_status"] == "pending_character_relationships"
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    assert topic_payload(client)["lifecycle_status"] == "character_relationships_generated"

    repository.lock_character_batch(project["id"], characters["batch_id"])
    assert topic_payload(client)["lifecycle_status"] == "pending_episode_outlines"
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "单集弧线")
    assert topic_payload(client)["lifecycle_status"] == "episode_outlines_generated"

    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    assert topic_payload(client)["lifecycle_status"] == "pending_beats"
    beats = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1, "scene": "大厅"}]})
    assert topic_payload(client)["lifecycle_status"] == "beats_generated"
    current_beats = current_beats_content(repository.get_project(project["id"]))
    repository.record_stage_export(
        project["id"], "beats", "json", content_fingerprint(current_beats)
    )
    assert topic_payload(client)["lifecycle_status"] == "beats_exported"

    repository.update_episode(beats["id"], {"beats": [{"order": 1, "scene": "天台"}]})
    assert topic_payload(client)["lifecycle_status"] == "beats_generated"


def test_scene_script_download_api_returns_delivery_txt(tmp_path: Path):
    client, repository, _ = build_client(tmp_path)
    topic = seed_topic(repository, "场次剧本接口剧")
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(topic["id"], breakdown["id"], "场次剧本项目", 1)
    brief = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向"}, "risk_score": 1, "risk_level": "low",
    }])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1}]})
    repository.lock_episode_beats(beats["id"])
    script = repository.create_episode_script(project["id"], 1, {
        "episode_title": "开局", "scenes": [{
            "sequence_no": 1, "scene_no": 1, "time_of_day": "日",
            "interior_exterior": "内", "location": "客厅", "props": [],
            "characters": ["主角"], "blocks": [{"type": "action", "text": "主角走进客厅。"}],
        }],
    })
    download = client.get(
        f"/api/creative/projects/{project['id']}/generated-content/download",
        params={"stage": "script", "resource_id": str(script["id"]), "format": "txt"},
    )

    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/plain")
    assert "第1集场次剧本v1.txt" in unquote(download.headers["content-disposition"])
    assert "△ 主角走进客厅。" in download.text


def test_merged_scene_script_download_returns_current_episodes_in_one_txt(tmp_path: Path):
    client, repository, _ = build_client(tmp_path)
    topic = seed_topic(repository, "合并剧本接口剧")
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(topic["id"], breakdown["id"], "合并剧本项目", 3)
    brief = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向"}, "risk_score": 1, "risk_level": "low",
    }])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"], [{"episode": 1}, {"episode": 2}, {"episode": 3}], "弧线"
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    for episode_number, title in ((1, "开局"), (3, "转折")):
        beats = repository.create_episode_beats(
            project["id"], episode_number, {"beats": [{"order": 1}]}
        )
        repository.lock_episode_beats(beats["id"])
        repository.create_episode_script(project["id"], episode_number, {
            "episode_title": title,
            "scenes": [{
                "sequence_no": 1, "scene_no": 1, "time_of_day": "日",
                "interior_exterior": "内", "location": "客厅", "props": [],
                "characters": ["主角"],
                "blocks": [{"type": "action", "text": f"第{episode_number}集内容。"}],
            }],
        })

    download = client.get(f"/api/creative/projects/{project['id']}/scripts/download")

    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/plain")
    assert "filename*=UTF-8''" in download.headers["content-disposition"]
    assert "合并剧本接口剧第1集-第3集场次剧本v1.txt" in unquote(download.headers["content-disposition"])
    assert "========== 第 1 集 ==========" in download.text
    assert "========== 第 3 集 ==========" in download.text
    assert download.text.index("第 1 集:开局") < download.text.index("第 3 集:转折")

    repository.create_episode_script(project["id"], 1, {
        "episode_title": "开局重写", "scenes": [{
            "sequence_no": 1, "scene_no": 1, "time_of_day": "日",
            "interior_exterior": "内", "location": "客厅", "props": [],
            "characters": ["主角"],
            "blocks": [{"type": "action", "text": "第1集新版本内容。"}],
        }],
    })

    updated_download = client.get(f"/api/creative/projects/{project['id']}/scripts/download")

    assert "合并剧本接口剧第1集-第3集场次剧本v1-v2.txt" in unquote(
        updated_download.headers["content-disposition"]
    )
    assert "第 1 集:开局重写" in updated_download.text

def test_generating_today_hits_returns_only_unadopted_recommendations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client, repository, _ = build_client(tmp_path)
    adopted = seed_topic(repository, "已采纳剧目")
    repository.update_topic(adopted["id"], "adopted")

    monkeypatch.setattr(TopicService, "generate", lambda *_args, **_kwargs: [{
        "platform": "hg",
        "job_id": "rank-new",
        "source_path": "data/hg/new.json",
        "captured_at": "2026-07-24",
        "title": "待采纳剧目",
        "description": "新的爆款推荐",
        "category": "都市",
        "rank_type": "热播榜",
        "rank_source": "全部热播榜",
        "score": 95,
        "confidence": "high",
        "score_details": {},
        "recommendation": "推荐",
        "evidence": [],
    }])

    response = client.post(
        "/api/creative/topics/generate",
        json={"platform": "hg", "rank_source": "全部", "limit": 10},
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["待采纳剧目"]
    assert response.json()["items"][0]["status"] == "pending_review"


def test_topic_service_filters_hongguo_source_and_joins_duplicate_board_names(
    tmp_path: Path,
):
    service = TopicService(
        root=str(tmp_path),
        rank_service=TodayRankService.from_files(root=tmp_path),
    )
    response = {
        "date": "2026-08-18",
        "selected_snapshot": {
            "platform": "hg",
            "job_id": "hg-rank",
            "source_path": "exports/20260818/top10_rankings.csv",
        },
        "previous_snapshot": None,
        "current_entries": [],
        "sections": [
            {
                "key": "真人剧",
                "rankings": [
                    {
                        "section": "真人剧",
                        "label": "热播榜",
                        "current_entries": [
                            {"title": "真人多榜剧", "rank": 2, "subject": "都市"}
                        ],
                    },
                    {
                        "section": "真人剧",
                        "label": "推荐榜",
                        "current_entries": [
                            {"title": "真人多榜剧", "rank": 1, "subject": "都市"}
                        ],
                    },
                ],
            },
            {
                "key": "漫剧",
                "rankings": [
                    {
                        "section": "漫剧",
                        "label": "热播榜",
                        "current_entries": [
                            {"title": "漫剧候选", "rank": 1, "subject": "玄幻"}
                        ],
                    }
                ],
            },
        ],
    }
    service.rank_service.get_today_rank = lambda **_kwargs: response

    topics = service.generate("hg", rank_source="真人剧", limit=10)

    assert [item["title"] for item in topics] == ["真人多榜剧"]
    assert topics[0]["rank_source"] == "真人剧热播榜|真人剧推荐榜"


def test_topic_service_filters_douyin_source_and_keeps_cross_board_provenance(
    tmp_path: Path,
):
    service = TopicService(
        root=str(tmp_path),
        rank_service=TodayRankService.from_files(root=tmp_path),
    )
    response = {
        "date": "2026-08-18",
        "selected_snapshot": {
            "platform": "dy",
            "job_id": "dy-rank",
            "source_path": "data/dy/jobs/rank.json",
        },
        "previous_snapshot": None,
        "current_entries": [],
        "sections": [
            {
                "key": "榜单",
                "rankings": [
                    {
                        "section": "榜单",
                        "label": "榜单",
                        "current_entries": [
                            {"title": "跨榜候选", "rank": 1, "subject": "都市"}
                        ],
                    }
                ],
            },
            {
                "key": "热榜",
                "rankings": [
                    {
                        "section": "热榜",
                        "label": "热榜",
                        "current_entries": [
                            {"title": "跨榜候选", "rank": 3, "subject": "都市"},
                            {"title": "热榜独有", "rank": 1, "subject": "甜宠"},
                        ],
                    }
                ],
            },
        ],
    }
    service.rank_service.get_today_rank = lambda **_kwargs: response

    topics = service.generate("dy", rank_source="热榜", limit=10)

    assert {item["title"] for item in topics} == {"跨榜候选", "热榜独有"}
    cross_board = next(item for item in topics if item["title"] == "跨榜候选")
    assert cross_board["rank_source"] == "榜单|热榜"


def test_repository_filters_topics_by_rank_source_token_prefix(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    common = {
        "platform": "hg",
        "job_id": "hg-rank",
        "source_path": "exports/20260818/top10_rankings.csv",
        "captured_at": "2026-08-18",
        "description": "来源筛选测试",
        "category": "都市",
        "rank_type": "热播榜",
        "score": 90,
        "confidence": "high",
        "score_details": {},
        "recommendation": "推荐",
        "evidence": [],
    }
    repository.upsert_topics([
        {
            **common,
            "title": "真人多榜剧",
            "rank_source": "真人剧热播榜|真人剧推荐榜",
        },
        {**common, "title": "漫剧候选", "rank_source": "漫剧热播榜"},
        {**common, "title": "AI剧候选", "rank_source": "AI剧热播榜"},
    ])

    assert [
        item["title"] for item in repository.list_topics(rank_source="真人剧")
    ] == ["真人多榜剧"]
    assert [
        item["title"] for item in repository.list_topics(rank_source="漫剧")
    ] == ["漫剧候选"]


def test_generate_topics_api_uses_and_returns_selected_rank_source(tmp_path: Path):
    client, _, _ = build_client(tmp_path)
    rank_path = (
        tmp_path
        / "data/dy/jobs/rank-source/rank/douyin_series_rank_20260818_100000.json"
    )
    rank_path.parent.mkdir(parents=True)
    rank_path.write_text(
        json.dumps(
            {
                "captured_at": "2026-08-18 10:00:00",
                "records": [
                    {
                        "series_id": "ranking-only",
                        "title": "榜单候选",
                        "rank": 1,
                        "rank_category": "短剧",
                        "tab": "榜单",
                        "heat_text": "100",
                    },
                    {
                        "series_id": "heat-only",
                        "title": "热榜候选",
                        "rank": 1,
                        "rank_category": "短剧",
                        "tab": "热榜",
                        "heat_text": "100",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/api/creative/topics/generate",
        json={"platform": "dy", "rank_source": "热榜", "limit": 10},
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["热榜候选"]
    assert response.json()["items"][0]["rank_source"] == "热榜"


def test_generate_topics_api_uses_default_source_without_mixing_same_day_topics(
    tmp_path: Path, monkeypatch
):
    client, repository, _ = build_client(tmp_path)
    seed_topic_data = {
        "platform": "dy",
        "job_id": "existing-heat",
        "source_path": "data/dy/same-day.json",
        "captured_at": "2026-08-18",
        "title": "已有热榜候选",
        "description": "不应混入默认榜单",
        "category": "都市",
        "rank_type": "热榜",
        "rank_source": "热榜",
        "score": 99,
        "confidence": "high",
        "score_details": {},
        "recommendation": "推荐",
        "evidence": [],
    }
    repository.upsert_topics([seed_topic_data])
    monkeypatch.setattr(TopicService, "generate", lambda *_args, **_kwargs: [{
        **seed_topic_data,
        "job_id": "default-ranking",
        "title": "默认榜单候选",
        "rank_type": "榜单",
        "rank_source": "榜单",
        "score": 90,
    }])

    response = client.post(
        "/api/creative/topics/generate", json={"platform": "dy", "limit": 10}
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["默认榜单候选"]


def test_generate_topics_api_does_not_fall_back_to_historical_pending_topics(
    tmp_path: Path, monkeypatch
):
    client, repository, _ = build_client(tmp_path)
    repository.upsert_topics([{
        "platform": "dy",
        "job_id": "historical-heat",
        "source_path": "data/dy/historical.json",
        "captured_at": "2026-08-17",
        "title": "昨日热榜候选",
        "description": "昨日数据",
        "category": "都市",
        "rank_type": "热榜",
        "rank_source": "热榜",
        "score": 90,
        "confidence": "high",
        "score_details": {},
        "recommendation": "推荐",
        "evidence": [],
    }])
    monkeypatch.setattr(TopicService, "generate", lambda *_args, **_kwargs: [])

    response = client.post(
        "/api/creative/topics/generate",
        json={"platform": "dy", "rank_source": "热榜", "limit": 10},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_generate_topics_api_rejects_invalid_rank_source(tmp_path: Path):
    client, _, _ = build_client(tmp_path)

    response = client.post(
        "/api/creative/topics/generate",
        json={"platform": "dy", "rank_source": "推荐榜", "limit": 10},
    )

    assert response.status_code == 400


def test_list_topics_api_rejects_invalid_rank_source(tmp_path: Path):
    client, _, _ = build_client(tmp_path)

    response = client.get(
        "/api/creative/topics",
        params={"platform": "dy", "rank_source": "推荐榜"},
    )

    assert response.status_code == 400


def test_repository_backfills_legacy_hongguo_rank_source(tmp_path: Path):
    db_path = tmp_path / "legacy-creative.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("""
            CREATE TABLE creative_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL, job_id TEXT NOT NULL, source_path TEXT NOT NULL,
                captured_at TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                category TEXT, rank_type TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending_review', score REAL NOT NULL,
                confidence TEXT NOT NULL, score_details_json TEXT NOT NULL,
                recommendation TEXT NOT NULL, evidence_json TEXT NOT NULL,
                decision_note TEXT NOT NULL DEFAULT '', deleted INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(platform, job_id, source_path, title)
            )
        """)
        connection.execute("""
            INSERT INTO creative_topics (
                platform, job_id, source_path, captured_at, title, description,
                category, rank_type, score, confidence, score_details_json,
                recommendation, evidence_json, created_at, updated_at
            ) VALUES (
                'hg', 'legacy', 'legacy.csv', '2026-08-18', '旧版红果候选', '',
                '都市', '热播榜', 90, 'high', '{}', '推荐', '[]', 1, 1
            )
        """)

    repository = CreativeRepository(db_path)

    topics = repository.list_topics(platform="hg", rank_source="全部")
    assert [item["title"] for item in topics] == ["旧版红果候选"]
    assert topics[0]["rank_source"] == "全部热播榜"


def test_adopting_rank_entry_creates_an_adopted_topic_and_rejects_duplicates(
    tmp_path: Path,
):
    client, repository, _ = build_client(tmp_path)
    rank_path = tmp_path / "data/dy/jobs/rank-adopt/rank/douyin_series_rank_20260728_100000.json"
    rank_path.parent.mkdir(parents=True)
    rank_path.write_text(
        json.dumps(
            {
                "captured_at": "2026-07-28 10:00:00",
                "records": [
                    {
                        "series_id": "rank-drama-1",
                        "title": "榜单收录短剧",
                        "rank": 1,
                        "rank_category": "真人剧",
                        "tab": "榜单",
                        "description": "来自今日榜单的剧目",
                        "heat_text": "12345",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = {"platform": "dy", "date": "2026-07-28", "record_id": "rank-drama-1"}
    created = client.post("/api/creative/topics/adopt-from-rank", json=payload)

    assert created.status_code == 201
    topic = created.json()["topic"]
    assert topic["title"] == "榜单收录短剧"
    assert topic["status"] == "adopted"
    assert topic["rank_type"] == "榜单"
    assert topic["evidence"][0]["source_type"] == "snapshot"
    assert repository.get_latest_selection_stage_snapshot(topic["id"], "topic_decision")["outcome"] == "adopted"

    duplicate = client.post("/api/creative/topics/adopt-from-rank", json=payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "剧目已收录"
    assert len(repository.list_topics(limit=None)) == 1


def test_generating_today_hits_excludes_a_title_already_in_the_topic_library(
    tmp_path: Path, monkeypatch
):
    client, repository, _ = build_client(tmp_path)
    adopted = seed_topic(repository, "已入库剧目")
    repository.update_topic(adopted["id"], "adopted")
    monkeypatch.setattr(TopicService, "generate", lambda *_args, **_kwargs: [
        {
            "platform": "hg", "job_id": "rank-today", "source_path": "data/hg/today.json",
            "captured_at": "2026-07-24", "title": "已入库剧目", "description": "重复候选",
            "category": "都市", "rank_type": "热播榜", "rank_source": "全部热播榜", "score": 99, "confidence": "high",
            "score_details": {}, "recommendation": "重复推荐", "evidence": [],
        },
        {
            "platform": "hg", "job_id": "rank-today", "source_path": "data/hg/today.json",
            "captured_at": "2026-07-24", "title": "全新候选", "description": "新的候选",
            "category": "都市", "rank_type": "热播榜", "rank_source": "全部热播榜", "score": 90, "confidence": "high",
            "score_details": {}, "recommendation": "新推荐", "evidence": [],
        },
    ])

    response = client.post(
        "/api/creative/topics/generate",
        json={"platform": "hg", "rank_source": "全部", "limit": 10},
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["全新候选"]


def test_generating_today_hits_matches_snapshot_day_when_capture_time_is_present(
    tmp_path: Path, monkeypatch
):
    client, repository, _ = build_client(tmp_path)
    repository.upsert_topics([{
        "platform": "dy", "job_id": "rank-today", "source_path": "data/dy/today.json",
        "captured_at": "2026-07-29", "title": "当日候选", "description": "初始快照",
        "category": "都市", "rank_type": "热榜", "score": 90, "confidence": "high",
        "score_details": {}, "recommendation": "推荐", "evidence": [],
    }])
    monkeypatch.setattr(TopicService, "generate", lambda *_args, **_kwargs: [{
        "platform": "dy", "job_id": "rank-today", "source_path": "data/dy/today.json",
        "captured_at": "2026-07-29 16:42:51", "title": "当日候选", "description": "更新快照",
        "category": "都市", "rank_type": "热榜", "score": 95, "confidence": "high",
        "score_details": {}, "recommendation": "推荐", "evidence": [],
    }])

    response = client.post(
        "/api/creative/topics/generate",
        json={"platform": "dy", "rank_source": "热榜", "limit": 10},
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["当日候选"]


def test_generating_top_ten_is_not_reduced_by_higher_scored_adopted_topics(
    tmp_path: Path, monkeypatch
):
    client, repository, _ = build_client(tmp_path)
    adopted = [
        {
            "platform": "dy",
            "job_id": f"historical-{index}",
            "source_path": f"data/dy/historical-{index}.json",
            "captured_at": "2026-07-26",
            "title": f"历史已采纳-{index}",
            "description": "历史记录",
            "category": "都市",
            "rank_type": "热榜",
            "score": 100 - index,
            "confidence": "high",
            "score_details": {},
            "recommendation": "历史推荐",
            "evidence": [],
        }
        for index in range(5)
    ]
    for topic in repository.upsert_topics(adopted):
        repository.update_topic(topic["id"], "adopted")
    generated = [
        {
            "platform": "dy",
            "job_id": "today-rank",
            "source_path": "data/dy/today-rank.json",
            "captured_at": "2026-07-27",
            "title": f"今日候选-{index}",
            "description": "今日热榜",
            "category": "都市",
            "rank_type": "热榜",
            "score": 90 - index,
            "confidence": "high",
            "score_details": {},
            "recommendation": "今日推荐",
            "evidence": [],
        }
        for index in range(10)
    ]
    monkeypatch.setattr(TopicService, "generate", lambda *_args, **_kwargs: generated)

    response = client.post(
        "/api/creative/topics/generate",
        json={"platform": "dy", "rank_source": "热榜", "limit": 10},
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == [
        f"今日候选-{index}" for index in range(10)
    ]


def test_topic_library_can_load_every_adopted_topic_without_a_page_limit(tmp_path: Path):
    client, repository, _ = build_client(tmp_path)
    topics = [{
        "platform": "hg",
        "job_id": f"rank-{index}",
        "source_path": f"data/hg/{index}.json",
        "captured_at": "2026-07-24",
        "title": f"已采纳剧目-{index}",
        "description": "选题库数量测试",
        "category": "都市",
        "rank_type": "热播榜",
        "score": 100 - index / 10_000,
        "confidence": "high",
        "score_details": {},
        "recommendation": "推荐",
        "evidence": [],
    } for index in range(501)]
    repository.upsert_topics(topics)
    saved = repository.list_topics(limit=None)
    for topic in saved:
        repository.update_topic(topic["id"], "adopted")

    response = client.get("/api/creative/topics?status=adopted&all=true")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 501


def test_topic_library_exports_filtered_topics_as_csv_and_excel(tmp_path: Path):
    client, repository, _ = build_client(tmp_path)
    exported_topic = seed_topic(repository, "导出目标短剧", platform="hg")
    other_topic = seed_topic(repository, "不应导出的短剧", platform="dy")
    repository.update_topic(exported_topic["id"], "adopted")
    repository.update_topic(other_topic["id"], "adopted")

    csv_response = client.get(
        "/api/creative/topics/export",
        params={"format": "csv", "status": "adopted", "platform": "hg"},
    )

    assert csv_response.status_code == 200
    assert "attachment" in csv_response.headers["content-disposition"]
    assert csv_response.headers["content-type"].startswith("text/csv")
    csv_rows = list(csv.reader(StringIO(csv_response.content.decode("utf-8-sig"))))
    assert csv_rows[0] == ["选题", "简介", "来源", "榜单", "题材", "推荐分", "状态", "拆解版本"]
    assert "操作" not in csv_rows[0]
    assert [row[0] for row in csv_rows[1:]] == ["导出目标短剧"]

    excel_response = client.get(
        "/api/creative/topics/export",
        params={"format": "excel", "status": "adopted", "platform": "hg"},
    )

    assert excel_response.status_code == 200
    assert "attachment" in excel_response.headers["content-disposition"]
    assert excel_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    worksheet = load_workbook(BytesIO(excel_response.content)).active
    assert [cell.value for cell in worksheet[1]] == csv_rows[0]
    assert "操作" not in [cell.value for cell in worksheet[1]]
    assert [cell.value for cell in worksheet[2]][0] == "导出目标短剧"


def test_downloaded_videos_advance_an_adopted_topic_to_pending_breakdown(tmp_path: Path):
    client, repository, manager = build_client(tmp_path)
    topic = seed_topic(repository, "视频下载完成短剧")
    repository.update_topic(topic["id"], "adopted")
    manager.create_job({
        "platform": "hg",
        "crawler_type": "drama_download",
        "extra": {"title": topic["title"], "book_id": "book-video"},
    })
    write_downloaded_video(tmp_path, topic["title"], 1)
    write_downloaded_video(tmp_path, topic["title"], 2)

    downloaded = topic_payload(client)

    assert downloaded["lifecycle_status"] == "pending_breakdown"
    assert downloaded["lifecycle_label"] == "待拆解"
    assert downloaded["downloaded_episode_count"] == 2


def test_latest_breakdown_draft_takes_precedence_over_older_locked_version(tmp_path: Path):
    client, repository, _ = build_client(tmp_path)
    topic = seed_topic(repository)
    repository.update_topic(topic["id"], "adopted")
    locked = repository.create_breakdown(topic["id"], {"title": "第一版"}, {}, [])
    repository.lock_breakdown(locked["id"])
    repository.create_breakdown(topic["id"], {"title": "第二版"}, {}, [])

    assert topic_payload(client)["lifecycle_status"] == "pending_breakdown"


def test_lifecycle_uses_furthest_non_archived_project_and_can_filter(tmp_path: Path):
    client, repository, _ = build_client(tmp_path)
    topic = seed_topic(repository)
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    slow = repository.create_project(topic["id"], breakdown["id"], "慢项目", 1)
    fast = repository.create_project(topic["id"], breakdown["id"], "快项目", 1)
    repository.create_blueprints(fast["id"], [{
        "content": {"title": "已生成方向"}, "risk_score": 10, "risk_level": "low",
    }])

    listed = topic_payload(client)
    assert listed["lifecycle_status"] == "imitation_generated"
    assert listed["lifecycle_project_id"] == fast["id"]
    assert client.get("/api/creative/topics?lifecycle_status=imitation_generated").json()["items"]
    assert client.get("/api/creative/topics?lifecycle_status=pending_imitation").json()["items"] == []

    repository.update_project(fast["id"], {"status": "archived"})
    listed = topic_payload(client)
    assert listed["lifecycle_status"] == "pending_imitation"
    assert listed["lifecycle_project_id"] == slow["id"]


def test_decision_status_overrides_generated_lifecycle(tmp_path: Path):
    client, repository, _ = build_client(tmp_path)
    topic = seed_topic(repository)
    repository.update_topic(topic["id"], "rejected")
    assert topic_payload(client)["lifecycle_status"] == "rejected"

    repository.update_topic(topic["id"], "archived")
    assert topic_payload(client)["lifecycle_status"] == "archived"


def test_latest_manual_decision_is_shared_by_same_title_snapshots(tmp_path: Path):
    client, repository, _ = build_client(tmp_path)
    first = seed_topic(repository, "同名决策短剧")
    repository.update_topic(first["id"], "adopted")
    repository.upsert_topics([{
        "platform": "dy", "job_id": "new-rank", "source_path": "data/dy/new.json",
        "captured_at": "2026-07-22", "title": first["title"], "description": "新快照",
        "category": "都市", "rank_type": "热播榜", "score": 99, "confidence": "high",
        "score_details": {}, "recommendation": "推荐", "evidence": [],
    }])

    displayed = topic_payload(client)
    assert displayed["lifecycle_status"] == "adopted"
    requested = client.get(f"/api/creative/topics/{first['id']}").json()
    assert requested["id"] == first["id"]
    assert requested["lifecycle_status"] == "adopted"
    repository.update_topic(displayed["id"], "archived")

    assert topic_payload(client)["lifecycle_status"] == "archived"
