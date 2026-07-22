# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.job import CrawlJob
from creative.api import register_creative_routes
from creative.repository import CreativeRepository
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
    register_creative_routes(app, root=tmp_path, runtime_dir=runtime_dir, job_manager=manager)
    return TestClient(app), CreativeRepository(runtime_dir / "creative" / "creative.db"), manager


def seed_topic(repository: CreativeRepository, title: str = "生命周期短剧") -> dict:
    return repository.upsert_topics([{
        "platform": "hg",
        "job_id": f"rank-{title}",
        "source_path": f"data/hg/{title}.json",
        "captured_at": "2026-07-21",
        "title": title,
        "description": "生命周期测试",
        "category": "都市",
        "rank_type": "热播榜",
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
    assert adopted.json()["lifecycle_status"] == "adopted"

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

    repository.update_episode(beats["id"], {"beats": [{"order": 1, "scene": "天台"}]})
    assert topic_payload(client)["lifecycle_status"] == "beats_generated"


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
