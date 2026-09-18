# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.job import CrawlJob
from analysis.today_rank_service import TodayRankService
from creative.api import (
    build_beats_markdown,
    build_creative_router,
    creative_api_principal,
    register_creative_routes,
)
from creative.free_mode_service import FreeCreativeModeService
from creative.imitation_service import assess_similarity_risk
from creative.outline_fields import outline_field_source_fingerprint
from creative.repository import CreativeRepository
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from runtime.job_state import JobState
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow import SYSTEM_ACTOR
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService


def seed_adopted_topic(repository: CreativeRepository) -> tuple[dict, dict]:
    topic = repository.upsert_topics([
        {
            "platform": "hg",
            "job_id": "rank-1",
            "source_path": "data/hg/rank.json",
            "captured_at": "2026-07-17T10:00:00",
            "title": "测试短剧",
            "description": "普通人逆袭",
            "category": "都市",
            "rank_type": "热播榜",
            "score": 88,
            "confidence": "high",
            "score_details": {},
            "recommendation": "值得拆解",
            "evidence": [{"source_type": "rank", "excerpt": "热播榜第 1"}],
        }
    ])[0]
    topic = repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(
        topic["id"],
        {
            "protagonist": "林小满",
            "core_conflict": "林小满被家族排斥后依靠特殊能力翻身",
            "hooks": ["当众退婚", "隐藏身份曝光"],
            "target_audience": "偏好都市逆袭题材的女性观众",
        },
        {"core_conflict": [{"source_type": "asr", "excerpt": "当众退婚"}]},
        [],
    )
    breakdown = repository.lock_breakdown(breakdown["id"])
    return topic, breakdown


def seed_topic_without_breakdown(repository: CreativeRepository, title: str = "首次拆解短剧") -> dict:
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": f"rank-{title}", "source_path": f"data/hg/{title}.json",
        "captured_at": "2026-07-20", "title": title, "description": "首次生成",
        "category": "都市", "rank_type": "热播榜", "score": 80, "confidence": "high",
        "score_details": {}, "recommendation": "值得拆解", "evidence": [],
    }])[0]
    return repository.update_topic(topic["id"], "adopted")


def test_project_requires_adopted_topic_and_locked_breakdown(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)

    first = repository.create_project(topic["id"], breakdown["id"], "方向 A", 20)
    second = repository.create_project(topic["id"], breakdown["id"], "方向 B", 30)

    assert first["id"] != second["id"]
    assert first["status"] == "created"
    assert first["target_episode_count"] == 20
    assert len(repository.list_projects(topic_id=topic["id"])) == 2


def test_project_inherits_target_audience_from_locked_breakdown_when_omitted(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)

    project = repository.create_project(topic["id"], breakdown["id"], "继承受众", 20)

    assert project["target_audience"] == "偏好都市逆袭题材的女性观众"


def test_project_keeps_explicit_target_audience_over_breakdown_value(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)

    project = repository.create_project(
        topic["id"], breakdown["id"], "指定受众", 20,
        target_audience="18-24 岁男性观众",
    )

    assert project["target_audience"] == "18-24 岁男性观众"


def test_deleting_project_removes_its_creative_records_and_script_review_data(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "待删除项目", 1)
    blueprint = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(blueprint["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [{"relation": "同盟"}])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1, "summary": "开局"}], "单集弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1}]})
    repository.lock_episode_beats(beats["id"])
    repository.create_episode_script(project["id"], 1, {"episode_title": "第 1 集", "scenes": []})
    review = repository.create_script_review(
        source_type="project", mode="single", project_id=project["id"], episode_numbers=[1],
        source_fingerprint="fingerprint", source_payload={}, created_by="tester",
    )
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint="fingerprint", selected_findings=[], target_episode_numbers=[1], created_by="tester",
    )
    repository.complete_script_optimization(optimization["id"], [{
        "project_id": project["id"], "episode_number": 1, "kind": "script",
        "source_episode_id": beats["id"], "source_fingerprint": "fingerprint", "content": {},
    }])
    with repository._connect() as connection:
        connection.execute(
            "CREATE TABLE creative_deliveries (id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, path TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO creative_deliveries (id, project_id, path) VALUES ('delivery-1', ?, 'exports/project.md')",
            (project["id"],),
        )

    removed = repository.delete_project(project["id"])

    assert removed["id"] == project["id"]
    assert removed["delivery_paths"] == ["exports/project.md"]
    assert repository.get_project(project["id"]) is None
    with repository._connect() as connection:
        for table in (
            "creative_blueprints", "creative_characters", "creative_relationships",
            "creative_episodes", "creative_stage_exports", "script_reviews",
            "script_optimizations", "script_optimization_drafts", "creative_deliveries",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project["id"],)).fetchone()[0] == 0


def test_project_delete_api_removes_its_workflow_members_and_tasks(tmp_path: Path):
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    runtime_dir = tmp_path / ".runtime"
    identity = IdentityRepository(SystemDatabase(SystemDatabaseSettings(postgres_url)))
    identity.seed_defaults()
    admin_role = identity.get_role_by_code("system_admin")
    assert admin_role is not None
    suffix = uuid.uuid4().hex
    admin = identity.create_user(f"delete-project-{suffix}", "Delete Project Admin", "hash")
    identity.replace_user_roles(admin["id"], [admin_role["id"]])
    permissions = PermissionService(identity, SessionService(identity, secure_cookie=False))
    principal = permissions.principal_for_user(admin["id"], f"delete-project-{suffix}")
    assert principal is not None
    workflow = WorkflowRepository(identity)
    repository = CreativeRepository(runtime_dir / "creative" / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(
        topic["id"], breakdown["id"], "API 删除项目", 1,
        created_by=principal.user_id,
    )
    workflow_project_id = repository.workflow_project_id(project["id"])
    task = workflow.ensure_project_task(
        workflow_project_id, topic["id"], task_type="brief",
        role_code="creative_planner", actor=principal,
    )
    workflow.add_project_member(
        workflow_project_id, principal.user_id, "project_owner", principal,
    )
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_creative_router(
        root=tmp_path, runtime_dir=runtime_dir, repository=repository,
        workflow_repository=workflow,
    ))
    client = TestClient(app)

    response = client.delete(f"/api/creative/projects/{project['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == project["id"]
    assert repository.get_project(project["id"]) is None
    with workflow.identity_repository.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM workflow_tasks WHERE project_id = %s",
            (workflow_project_id,),
        ).scalar_one() == 0
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM project_members WHERE project_id = %s",
            (workflow_project_id,),
        ).scalar_one() == 0
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM workflow_instances WHERE id = %s", (task["workflow_id"],)
        ).scalar_one() == 0


def test_repository_backfills_empty_project_audience_from_breakdown(tmp_path: Path):
    db_path = tmp_path / "creative.db"
    repository = CreativeRepository(db_path)
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(
        topic["id"], breakdown["id"], "历史项目", 20,
        target_audience="临时受众",
    )
    with repository._connect() as conn:
        conn.execute(
            "UPDATE creative_projects SET target_audience = '' WHERE id = ?",
            (project["id"],),
        )

    reopened = CreativeRepository(db_path)

    assert reopened.get_project(project["id"])["target_audience"] == "偏好都市逆袭题材的女性观众"


def test_project_accepts_one_and_one_hundred_episode_boundaries(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)

    one = repository.create_project(topic["id"], breakdown["id"], "单集", 1)
    hundred = repository.create_project(topic["id"], breakdown["id"], "百集", 100)

    assert one["target_episode_count"] == 1
    assert hundred["target_episode_count"] == 100


def test_project_brief_settings_are_editable_until_brief_is_locked(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "旧名称", 20)

    updated = repository.update_project(project["id"], {
        "name": "新名称", "genre": "家庭", "target_platform": "dy",
        "target_audience": "25-40 岁女性", "target_episode_count": 30,
        "episode_duration_seconds": 150,
    })

    assert updated["name"] == "新名称"
    assert updated["target_episode_count"] == 30
    blueprint = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    repository.lock_brief(blueprint["id"])
    with pytest.raises(ValueError, match="锁定 Brief"):
        repository.update_project(project["id"], {"target_episode_count": 40})


def test_successor_brief_draft_can_update_and_freeze_new_project_settings(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "版本参数", 20)
    repository.update_project(project["id"], {
        "genre": "都市", "target_platform": "hg", "target_audience": "初版受众",
    })
    first = repository.create_blueprints(project["id"], [{
        "content": {"title": "20 集初版", "audience": "方向自身受众"},
        "risk_score": 10, "risk_level": "low",
    }])[0]
    repository.lock_brief(first["id"])
    second = repository.create_blueprint_version(first["id"])

    with pytest.raises(ValueError, match="锁定 Brief"):
        repository.update_project(project["id"], {"target_episode_count": 25})

    updated = repository.update_project(
        project["id"],
        {
            "name": "版本参数新版",
            "genre": "悬疑",
            "target_platform": "dy",
            "target_audience": "新版受众",
            "target_episode_count": 30,
            "episode_duration_seconds": 150,
        },
        draft_brief_id=second["id"],
        expected_revision=repository.get_project(project["id"])["revision"],
    )
    locked_second = repository.lock_brief(
        second["id"], expected_revision=updated["revision"]
    )

    assert updated["name"] == "版本参数新版"
    assert updated["genre"] == "悬疑"
    assert updated["target_platform"] == "dy"
    assert updated["target_audience"] == "新版受众"
    assert updated["target_episode_count"] == 30
    assert updated["episode_duration_seconds"] == 150
    first_snapshot = repository.get_blueprint(first["id"])["content"]
    assert first_snapshot["project_name"] == "版本参数"
    assert first_snapshot["genre"] == "都市"
    assert first_snapshot["target_platform"] == "hg"
    assert first_snapshot["target_audience"] == "初版受众"
    assert first_snapshot["target_episode_count"] == 20
    assert first_snapshot["episode_duration_seconds"] == 120
    assert locked_second["content"]["project_name"] == "版本参数新版"
    assert locked_second["content"]["genre"] == "悬疑"
    assert locked_second["content"]["target_platform"] == "dy"
    assert locked_second["content"]["target_audience"] == "新版受众"
    assert locked_second["content"]["target_episode_count"] == 30
    assert locked_second["content"]["episode_duration_seconds"] == 150
    current = repository.get_project(project["id"])
    assert current["locked_brief_id"] == second["id"]
    assert current["target_episode_count"] == 30
    assert current["episode_duration_seconds"] == 150

    restored_first = repository.select_locked_brief(
        project["id"], first["id"], expected_revision=current["revision"]
    )
    assert restored_first["name"] == "版本参数"
    assert restored_first["genre"] == "都市"
    assert restored_first["target_platform"] == "hg"
    assert restored_first["target_audience"] == "初版受众"
    assert restored_first["target_episode_count"] == 20
    assert restored_first["episode_duration_seconds"] == 120
    restored_second = repository.select_locked_brief(
        project["id"], second["id"], expected_revision=restored_first["revision"]
    )
    assert restored_second["name"] == "版本参数新版"
    assert restored_second["genre"] == "悬疑"
    assert restored_second["target_platform"] == "dy"
    assert restored_second["target_audience"] == "新版受众"
    assert restored_second["target_episode_count"] == 30
    assert restored_second["episode_duration_seconds"] == 150


def test_project_rejects_unadopted_topic_or_unlocked_breakdown(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    repository.update_topic(topic["id"], "pending_review")

    with pytest.raises(ValueError, match="采纳"):
        repository.create_project(topic["id"], breakdown["id"], "不允许", 20)

    repository.update_topic(topic["id"], "adopted")
    draft = repository.create_breakdown(topic["id"], {}, {}, [])
    with pytest.raises(ValueError, match="锁定"):
        repository.create_project(topic["id"], draft["id"], "不允许", 20)


def test_project_accepts_locked_breakdown_from_same_title_on_another_platform(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topics = repository.upsert_topics([
        {
            "platform": platform, "job_id": f"job-{platform}", "source_path": f"{platform}.json",
            "captured_at": "2026-07-17", "title": "跨平台同名剧", "description": "",
            "category": "都市", "rank_type": "热播榜", "score": 90 if platform == "dy" else 80,
            "confidence": "high", "score_details": {}, "recommendation": "推荐", "evidence": [],
        }
        for platform in ("dy", "hg")
    ])
    dy_topic = repository.list_topics(platform="dy")[0]
    hg_topic = repository.list_topics(platform="hg")[0]
    repository.update_topic(dy_topic["id"], "adopted")
    breakdown = repository.create_breakdown(hg_topic["id"], {"core_conflict": "冲突"}, {}, [])
    repository.lock_breakdown(breakdown["id"])

    project = repository.create_project(dy_topic["id"], breakdown["id"], "跨平台项目", 20)

    assert project["topic_id"] == dy_topic["id"]
    assert project["breakdown_id"] == breakdown["id"]


def test_high_risk_blueprint_requires_difference_note_before_lock(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "新项目", 20)
    blueprint = repository.create_blueprints(project["id"], [{
        "content": {"title": "替代方向", "core_conflict": "新的冲突"},
        "risk_score": 82,
        "risk_level": "high",
    }], job_id="creative-job")[0]

    with pytest.raises(ValueError, match="差异化"):
        repository.lock_brief(blueprint["id"], "")

    locked = repository.lock_brief(blueprint["id"], "更换主角身份、时代背景和冲突解法")
    assert locked["status"] == "brief_locked"
    assert locked["difference_note"]
    assert locked["content"]["project_name"] == "新项目"
    assert locked["content"]["target_episode_count"] == 20
    assert locked["content"]["episode_duration_seconds"] == 120
    assert repository.get_project(project["id"])["status"] == "brief_locked"

    with pytest.raises(ValueError, match="锁定"):
        repository.update_blueprint(blueprint["id"], {"title": "不能修改"})


def test_character_outline_and_beats_follow_locked_upstream_versions(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "新项目", 2)
    blueprint = repository.create_blueprints(project["id"], [{
        "content": {"title": "新方向"}, "risk_score": 20, "risk_level": "low",
    }])[0]

    with pytest.raises(ValueError, match="Brief"):
        repository.create_character_batch(project["id"], [], [])

    repository.lock_brief(blueprint["id"])
    character_batch = repository.create_character_batch(
        project["id"],
        [{"name": "周晴", "identity": "社区调解员"}],
        [{"from": "周晴", "to": "顾川", "relation": "对手"}],
        job_id="character-job",
    )

    with pytest.raises(ValueError, match="人物关系"):
        repository.create_outline_batch(project["id"], [{"episode": 1}], "arc")

    repository.lock_character_batch(project["id"], character_batch["batch_id"])
    outline_batch = repository.create_outline_batch(
        project["id"],
        [
            {"episode": 1, "core_event": "双方第一次交锋"},
            {"episode": 2, "core_event": "误会反转"},
        ],
        "相识、对抗、合作",
        job_id="outline-job",
    )
    repository.lock_outline_batch(project["id"], outline_batch["batch_id"])
    beats = repository.create_episode_beats(
        project["id"],
        1,
        {"beats": [{"order": 1, "scene": "社区大厅", "goal": "说服居民"}]},
        job_id="beats-job",
    )
    locked_beats = repository.lock_episode_beats(beats["id"])

    detail = repository.get_project(project["id"])
    assert locked_beats["status"] == "locked"
    assert detail["locked_brief_id"] == blueprint["id"]
    assert detail["character_batch_id"] == character_batch["batch_id"]
    assert detail["outline_batch_id"] == outline_batch["batch_id"]
    assert detail["episodes"][0]["upstream_refs"]["outline_batch_id"] == outline_batch["batch_id"]


def test_character_update_survives_project_reload(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "保存测试", 2)
    blueprint = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    repository.lock_brief(blueprint["id"])
    batch = repository.create_character_batch(project["id"], [{"name": "旧名字", "identity": "旧身份"}], [])
    character = batch["characters"][0]

    response = client.patch(
        f"/api/creative/characters/{character['id']}",
        json={"content": {**character["content"], "name": "新名字", "identity": "已保存身份"}},
    )
    reloaded = client.get(f"/api/creative/projects/{project['id']}")

    assert response.status_code == 200
    assert reloaded.status_code == 200
    saved = next(item for item in reloaded.json()["characters"] if item["id"] == character["id"])
    assert saved["content"]["name"] == "新名字"
    assert saved["content"]["identity"] == "已保存身份"


def test_outline_requires_exact_episode_range(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "边界测试", 2)
    blueprint = repository.create_blueprints(project["id"], [{
        "content": {"title": "新方向"}, "risk_score": 20, "risk_level": "low",
    }])[0]
    repository.lock_brief(blueprint["id"])
    batch = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], batch["batch_id"])

    with pytest.raises(ValueError, match="1-2"):
        repository.create_outline_batch(project["id"], [{"episode": 2}, {"episode": 3}], "arc")


def test_new_beats_version_points_to_parent_version(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "版本测试", 1)
    blueprint = repository.create_blueprints(project["id"], [{
        "content": {"title": "新方向"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    repository.lock_brief(blueprint["id"])
    people = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], people["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "arc")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    first = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1}]})
    repository.lock_episode_beats(first["id"])

    second = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1}]})

    assert second["version"] == 2
    assert second["parent_version_id"] == first["id"]


def test_locking_new_brief_keeps_history_but_invalidates_current_downstream(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "分支测试", 1)
    first = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向一"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    repository.lock_brief(first["id"])
    people = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], people["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "arc")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])

    second = repository.create_blueprint_version(first["id"])
    repository.update_blueprint(second["id"], {"title": "方向二"})
    repository.lock_brief(
        second["id"],
        expected_revision=repository.get_project(project["id"])["revision"],
    )

    detail = repository.get_project(project["id"])
    assert detail["locked_brief_id"] == second["id"]
    assert detail["character_batch_id"] is None
    assert detail["outline_batch_id"] is None
    assert any(item["batch_id"] == people["batch_id"] for item in detail["characters"])
    assert any(item["batch_id"] == outlines["batch_id"] for item in detail["episodes"])


def test_selecting_another_locked_brief_repoints_character_generation_context(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "方向切换", 1)
    first = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向一"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    repository.lock_brief(first["id"])
    people = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], people["batch_id"])
    second = repository.create_blueprint_version(first["id"])
    repository.update_blueprint(second["id"], {"title": "方向二"})
    repository.lock_brief(
        second["id"],
        expected_revision=repository.get_project(project["id"])["revision"],
    )

    selected = repository.select_locked_brief(project["id"], first["id"])

    assert selected["locked_brief_id"] == first["id"]
    assert selected["character_batch_id"] is None
    assert selected["outline_batch_id"] is None


def test_selecting_locked_brief_restores_its_frozen_episode_count(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "方向集数切换", 96)
    first = repository.create_blueprints(project["id"], [{
        "content": {"title": "96 集方向"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    repository.lock_brief(first["id"])
    second = repository.create_blueprint_version(first["id"])
    repository.lock_brief(
        second["id"],
        expected_revision=repository.get_project(project["id"])["revision"],
    )
    with repository._connect() as connection:
        content = dict(repository.get_blueprint(second["id"])["content"])
        content["target_episode_count"] = 30
        connection.execute(
            "UPDATE creative_blueprints SET content_json = ? WHERE id = ?",
            (json.dumps(content, ensure_ascii=False), second["id"]),
        )
        connection.execute(
            "UPDATE creative_projects SET target_episode_count = 96 WHERE id = ?",
            (project["id"],),
        )

    repository.select_locked_brief(project["id"], first["id"])
    selected = repository.select_locked_brief(project["id"], second["id"])

    assert selected["locked_brief_id"] == second["id"]
    assert selected["target_episode_count"] == 30


def test_selecting_another_locked_character_batch_repoints_outline_context(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "人物版本切换", 1)
    brief = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    repository.lock_brief(brief["id"])
    first = repository.create_character_batch(project["id"], [{"name": "主角一"}], [])
    repository.lock_character_batch(project["id"], first["batch_id"])
    second = repository.create_character_batch(project["id"], [{"name": "主角二"}], [])
    repository.lock_character_batch(project["id"], second["batch_id"])

    selected = repository.select_locked_character_batch(project["id"], first["batch_id"])

    assert selected["character_batch_id"] == first["batch_id"]
    assert selected["outline_batch_id"] is None


def test_locked_brief_version_api_creates_a_successor_brief_task(tmp_path: Path) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    runtime_dir = tmp_path / ".runtime"
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identity = IdentityRepository(database)
    identity.seed_defaults()
    admin_role = identity.get_role_by_code("system_admin")
    assert admin_role is not None
    suffix = uuid.uuid4().hex
    admin = identity.create_user(f"brief-version-{suffix}", "Brief Version Admin", "hash")
    identity.replace_user_roles(admin["id"], [admin_role["id"]])
    principal = PermissionService(identity, SessionService(identity)).principal_for_user(
        admin["id"], f"brief-version-{suffix}"
    )
    assert principal is not None
    workflow = WorkflowRepository(identity)
    repository = CreativeRepository(runtime_dir / "creative" / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "版本项目", 1)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "初版"}}])[0]
    task = workflow.create_task(
        f"project:{project['id']}", "brief", "creative_planner",
        project_id=str(project["id"]), topic_id=topic["id"], actor=SYSTEM_ACTOR,
    )
    task = workflow.claim_task(task["id"], principal)
    task = workflow.start_task(task["id"], principal)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE creative_blueprints SET workflow_task_id = ? WHERE id = ?",
            (task["id"], brief["id"]),
        )
    transitions = TransitionService(
        workflow, creative_adapter=CreativeWorkflowAdapter(repository, workflow)
    )
    transitions.complete_creative_without_review(task["id"], "brief", str(brief["id"]), principal)

    app = FastAPI()
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_creative_router(
        root=tmp_path, runtime_dir=runtime_dir, repository=repository,
        workflow_repository=workflow
    ))
    client = TestClient(app)

    ensured = client.post(
        f"/api/creative/projects/{project['id']}/workflow-tasks/brief/ensure"
    )

    assert ensured.status_code == 200
    assert ensured.json()["status"] == "assigned"
    successor_task = workflow.start_task(ensured.json()["id"], principal)
    response = client.post(
        f"/api/creative/blueprints/{brief['id']}/versions",
        json={
            "workflow_task_id": successor_task["id"],
            "expected_project_revision": repository.get_project(project["id"])["revision"],
        },
    )

    assert response.status_code == 200
    successor = response.json()
    assert successor["parent_version_id"] == brief["id"]
    assert successor["status"] == "draft"
    assert successor["workflow_task_id"] == successor_task["id"]
    assert workflow.get_task(successor_task["id"])["predecessor_task_id"] == task["id"]
    database.dispose()


def test_similarity_risk_uses_more_conservative_score():
    result = assess_similarity_risk(
        {"core_conflict": "林小满当众退婚后隐藏身份曝光"},
        {"core_conflict": "林小满当众退婚后隐藏身份曝光", "risk_score": 30},
    )

    assert result["risk_score"] >= 70
    assert result["risk_level"] == "high"
    assert result["overlap_score"] >= result["model_score"]


class RecordingJobManager:
    def __init__(self):
        self.created: list[dict] = []

    def create_job(self, data: dict):
        payload = dict(data)
        payload.setdefault("job_id", f"creative-{len(self.created) + 1}")
        self.created.append(payload)
        return JobState(job=CrawlJob.from_dict(payload), status="running")


class InlineSequentialProvider:
    provider_name = "test"

    def __init__(self, responses: list[dict | Exception]):
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    async def complete_json(self, messages):
        self.calls.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def build_phase4_client(
    tmp_path: Path,
    *,
    llm_provider_factory=None,
    repository: CreativeRepository | None = None,
) -> tuple[TestClient, CreativeRepository, RecordingJobManager]:
    runtime_dir = tmp_path / ".runtime"
    manager = RecordingJobManager()
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    identity = IdentityRepository(
        SystemDatabase(SystemDatabaseSettings(postgres_url))
    )
    identity.seed_defaults()
    admin_role = identity.get_role_by_code("system_admin")
    assert admin_role is not None
    suffix = uuid.uuid4().hex
    username = f"phase4-admin-{suffix}"
    admin = identity.create_user(username, "Phase 4 Admin", "hash")
    identity.replace_user_roles(admin["id"], [admin_role["id"]])
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    workflow = WorkflowRepository(identity)
    workflow.delete_project_data("1")
    if repository is None:
        # Free-mode stages need the repository transaction boundary, which only
        # the PostgreSQL repository provides; callers that exercise them inject
        # the isolated postgres_creative fixture instead of a SQLite store.
        repository = CreativeRepository(runtime_dir / "creative" / "creative.db")
    app = FastAPI()
    app.state.identity_permission_service = permissions
    register_creative_routes(
        app,
        root=tmp_path,
        runtime_dir=runtime_dir,
        job_manager=manager,
        repository=repository,
        workflow_repository=workflow,
        llm_provider_factory=llm_provider_factory,
        # The rank service is file backed so building the router never opens the
        # ambient system database.
        rank_service=TodayRankService.from_files(root=tmp_path),
    )
    session_app = FastAPI()

    @session_app.get("/session")
    async def create_session(request: Request):
        return sessions.create_session(admin["id"], request)

    raw_token, _ = TestClient(session_app).get("/session").json()
    client = TestClient(app)
    client.cookies.set(sessions.cookie_name, raw_token)
    client.workflow = workflow
    client.principal = permissions.principal_for_user(admin["id"], username)
    assert client.principal is not None
    return client, repository, manager


def assign_phase4_task(
    client: TestClient,
    task_type: str,
    *,
    topic_id: int | None = None,
    project_id: int | None = None,
) -> dict:
    role_code = (
        "selection_operator" if task_type == "breakdown" else "creative_planner"
    )
    if topic_id is not None and project_id is None:
        topic_scope = client.app.state.workflow_identity_resolver.topic_scope(
            topic_id
        )
        task = client.workflow.create_task(
            f"topic:{topic_scope}:candidate:"
            f"{len(client.workflow.list_tasks(client.principal))}",
            task_type,
            role_code,
            topic_id=topic_id,
            actor=SYSTEM_ACTOR,
        )
    else:
        resolved_project_id = (
            client.app.state.workflow_identity_resolver.project_id(project_id)
            if project_id is not None
            else None
        )
        task = client.workflow.create_task(
            f"phase4-{task_type}-{topic_id}-{project_id}-{len(client.workflow.list_tasks(client.principal))}",
            task_type,
            role_code,
            topic_id=topic_id,
            project_id=resolved_project_id,
            actor=SYSTEM_ACTOR,
        )
    return client.workflow.claim_task(task["id"], client.principal)


def outline_series_overview() -> dict[str, str]:
    return {
        "core_event": "主角追查真相并守住团队",
        "character_progress": "主角从逃避责任成长为主动担当",
        "conflict": "内部背叛与外部围堵同步升级",
        "reversal": "最可信任的盟友才是幕后推手",
        "highlight": "主角在直播现场完成证据反击",
        "cliffhanger": "终局订单指向尚未揭开的旧案",
    }


def seed_complete_outline_project(tmp_path: Path, *, locked: bool):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "字段重写", 2)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "旧剧院迷局"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "林舟"}], [{"relation": "盟友"}]
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    overview = outline_series_overview()
    episodes = [
        {
            "episode": number,
            "core_event": f"事件 {number}",
            "conflict": f"冲突 {number}",
            "character_progress": f"人物推进 {number}",
            "highlight": f"亮点 {number}",
            "reversal": f"反转 {number}",
            "cliffhanger": f"悬念 {number}",
            "series_overview": overview,
        }
        for number in (1, 2)
    ]
    batch = repository.create_outline_batch(project["id"], episodes, "调查、受阻、反击")
    if locked:
        repository.lock_outline_batch(project["id"], batch["batch_id"])
        batch = repository.get_outline_batch(project["id"], batch["batch_id"])
    return repository, repository.get_project(project["id"]), batch


# These tests catch replacing the submitted whole episode, accepting a stale source,
# or resolving rewrite authority from another outline batch.
def test_repository_updates_only_one_outline_field_with_matching_fingerprint(tmp_path):
    repository, project, batch = seed_complete_outline_project(tmp_path, locked=False)
    episode = batch["episodes"][0]
    before = dict(episode["content"])
    fingerprint = outline_field_source_fingerprint(episode, "core_event")

    saved = repository.apply_outline_field_rewrite(
        episode["id"], "core_event", "重写后的核心事件", fingerprint
    )

    assert saved["content"]["core_event"] == "重写后的核心事件"
    assert saved["content"]["conflict"] == before["conflict"]
    assert saved["content"]["series_overview"] == before["series_overview"]


def test_repository_rejects_outline_field_rewrite_with_stale_fingerprint(tmp_path):
    repository, _, batch = seed_complete_outline_project(tmp_path, locked=False)
    episode = batch["episodes"][0]
    fingerprint = outline_field_source_fingerprint(episode, "core_event")
    repository.apply_outline_field_rewrite(
        episode["id"], "core_event", "第一次重写", fingerprint
    )

    with pytest.raises(ValueError, match="该字段已被更新，请重新生成"):
        repository.apply_outline_field_rewrite(
            episode["id"], "core_event", "过期重写", fingerprint
        )


def test_repository_rejects_in_place_outline_field_rewrite_for_locked_episode(tmp_path):
    repository, _, batch = seed_complete_outline_project(tmp_path, locked=True)
    episode = batch["episodes"][0]

    with pytest.raises(ValueError, match="已完成的版本不能原地修改"):
        repository.apply_outline_field_rewrite(
            episode["id"],
            "core_event",
            "不能写入锁定版本",
            outline_field_source_fingerprint(episode, "core_event"),
        )


def test_repository_rejects_outline_field_rewrite_for_non_outline_episode(tmp_path):
    repository, project, batch = seed_complete_outline_project(tmp_path, locked=True)
    beats = repository.create_episode_beats(
        project["id"], 1, {"beats": [{"order": 1}]}
    )

    with pytest.raises(ValueError, match="分集大纲不存在"):
        repository.apply_outline_field_rewrite(
            beats["id"],
            "core_event",
            "不能写入 Beats",
            outline_field_source_fingerprint(batch["episodes"][0], "core_event"),
        )


def test_repository_rejects_outline_field_rewrite_for_noncurrent_compatible_draft(tmp_path):
    repository, project, first = seed_complete_outline_project(tmp_path, locked=False)
    second = repository.create_outline_batch(
        project["id"],
        [
            {**episode["content"], "core_event": f"第二版事件 {episode['episode_number']}"}
            for episode in first["episodes"]
        ],
        "第二版调查、受阻、反击",
    )
    assert second["batch_id"] != first["batch_id"]
    stale = first["episodes"][0]

    with pytest.raises(ValueError, match="只能编辑当前大纲批次"):
        repository.apply_outline_field_rewrite(
            stale["id"],
            "core_event",
            "不能写入旧草稿",
            outline_field_source_fingerprint(stale, "core_event"),
        )


def test_repository_rewrite_context_uses_adjacent_episodes_from_same_batch(tmp_path):
    repository, project, first = seed_complete_outline_project(tmp_path, locked=False)
    second = repository.create_outline_batch(
        project["id"],
        [
            {**episode["content"], "core_event": f"第二版事件 {episode['episode_number']}"}
            for episode in first["episodes"]
        ],
        "第二版调查、受阻、反击",
    )
    target = second["episodes"][1]

    context = repository.get_outline_field_rewrite_context(target["id"])

    assert context["project"]["id"] == project["id"]
    assert context["brief"]["id"] == context["project"]["locked_brief_id"]
    assert context["episode"]["id"] == target["id"]
    assert context["previous_episode"]["batch_id"] == target["batch_id"]
    assert context["previous_episode"]["content"]["core_event"] == "第二版事件 1"
    assert context["next_episode"] is None


def seed_draft_outline_for_api(
    client: TestClient,
    repository: CreativeRepository,
) -> tuple[dict, dict, dict]:
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "概览项目", 2)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "概览方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    task = assign_phase4_task(client, "outline", project_id=project["id"])
    batch = repository.create_outline_batch(
        project["id"],
        [
            {
                "episode": number,
                "core_event": core_event,
                "conflict": f"冲突 {number}",
                "character_progress": f"人物推进 {number}",
                "highlight": f"亮点 {number}",
                "reversal": f"反转 {number}",
                "cliffhanger": f"悬念 {number}",
                "series_overview": outline_series_overview(),
            }
            for number, core_event in ((1, "发现假账"), (2, "公开证据"))
        ],
        "调查、受阻、反击",
        workflow_task_id=task["id"],
    )
    return project, batch, task


# This test catches a generation endpoint that writes before the user accepts a candidate.
def test_outline_field_rewrite_api_generates_candidate_without_writing(tmp_path: Path):
    provider = InlineSequentialProvider([{"value": "候选核心事件"}])
    client, repository, _ = build_phase4_client(
        tmp_path, llm_provider_factory=lambda: provider
    )
    project, batch, _ = seed_draft_outline_for_api(client, repository)
    episode = batch["episodes"][0]

    response = client.post(
        f"/api/creative/episodes/{episode['id']}/fields/core_event/rewrite",
        json={"instruction": "强化危机"},
    )

    assert response.status_code == 200
    assert response.json()["candidate_value"] == "候选核心事件"
    assert response.json()["project_id"] == project["id"]
    assert repository.get_episode(episode["id"])["content"]["core_event"] == "发现假账"
    assert len(provider.calls) == 1


# This test catches applying an entire browser episode payload instead of one field.
def test_outline_field_rewrite_api_applies_only_selected_draft_field(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    project, batch, _ = seed_draft_outline_for_api(client, repository)
    episode = batch["episodes"][0]
    before = dict(episode["content"])

    response = client.post(
        f"/api/creative/episodes/{episode['id']}/fields/core_event/apply-rewrite",
        json={
            "candidate_value": "采用后的核心事件",
            "source_fingerprint": outline_field_source_fingerprint(episode, "core_event"),
            "expected_project_revision": repository.get_project(project["id"])["revision"],
            "operation_id": "draft-field-rewrite",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["created_new_version"] is False
    assert payload["episode"]["content"]["core_event"] == "采用后的核心事件"
    assert payload["episode"]["content"]["conflict"] == before["conflict"]
    assert payload["episode"]["content"]["series_overview"] == before["series_overview"]


# This test catches mutating a locked batch or dropping sibling episodes/metadata.
def test_outline_field_rewrite_api_derives_complete_draft_from_locked_batch(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    project, batch, _ = seed_draft_outline_for_api(client, repository)
    repository.lock_outline_batch(project["id"], batch["batch_id"])
    locked = repository.get_outline_batch(project["id"], batch["batch_id"])
    source = locked["episodes"][0]
    original = [dict(item["content"]) for item in locked["episodes"]]
    current_project = repository.get_project(project["id"])

    response = client.post(
        f"/api/creative/episodes/{source['id']}/fields/core_event/apply-rewrite",
        json={
            "candidate_value": "锁定版派生的新核心事件",
            "source_fingerprint": outline_field_source_fingerprint(source, "core_event"),
            "expected_project_revision": current_project["revision"],
            "operation_id": "locked-field-rewrite",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["created_new_version"] is True
    assert payload["batch_id"] != locked["batch_id"]
    derived = repository.get_outline_batch(project["id"], payload["batch_id"])
    assert len(derived["episodes"]) == len(locked["episodes"]) == 2
    assert derived["episodes"][0]["content"]["core_event"] == "锁定版派生的新核心事件"
    assert derived["episodes"][0]["content"]["conflict"] == original[0]["conflict"]
    assert derived["episodes"][1]["content"] == original[1]
    assert repository.get_outline_batch(project["id"], locked["batch_id"])["episodes"][0]["content"] == original[0]


def seed_free_outline_for_api(
    client: TestClient,
    repository: CreativeRepository,
    *,
    locked: bool,
) -> tuple[dict, dict, dict]:
    project = repository.create_original_project(
        seed_text="一名记者调查被篡改的旧案",
        name="自由大纲字段重写",
        target_episode_count=2,
        created_by=client.principal.user_id,
        creation_request_id=f"free-outline-rewrite-{uuid.uuid4().hex}",
        workflow_mode="free",
    )
    repository.mark_project_bootstrap_status(project["id"], "ready")
    service = FreeCreativeModeService(repository)
    created = service.create_outline(
        project["id"],
        [
            {
                "episode": number,
                "core_event": core_event,
                "conflict": f"冲突 {number}",
                "character_progress": f"人物推进 {number}",
                "highlight": f"亮点 {number}",
                "reversal": f"反转 {number}",
                "cliffhanger": f"悬念 {number}",
                "series_overview": outline_series_overview(),
            }
            for number, core_event in ((1, "记者找到旧案卷宗"), (2, "证人公开关键录音"))
        ],
        series_arc="调查、受阻、公开真相",
        series_overview=outline_series_overview(),
        expected_head_revision=0,
        actor_user_id=client.principal.user_id,
    )
    if locked:
        service.lock_resource(
            project_id=project["id"],
            stage="outline",
            resource_ref=created["resource"]["batch_id"],
            expected_head_revision=created["head"]["revision"],
            actor_user_id=client.principal.user_id,
        )
    head = service.get_head(project["id"], "outline")
    return repository.get_project(project["id"]), repository.get_outline_batch(
        project["id"], created["resource"]["batch_id"]
    ), head


def test_free_outline_field_rewrite_generates_and_applies_to_current_draft(
    tmp_path: Path,
    postgres_creative,
):
    provider = InlineSequentialProvider([{"value": "记者在密室中找到被调包的原始卷宗"}])
    client, repository, _ = build_phase4_client(
        tmp_path,
        llm_provider_factory=lambda: provider,
        repository=postgres_creative,
    )
    project, batch, head = seed_free_outline_for_api(
        client, repository, locked=False
    )
    source = batch["episodes"][0]
    original_conflict = source["content"]["conflict"]

    generated = client.post(
        f"/api/creative/episodes/{source['id']}/fields/core_event/rewrite",
        json={"instruction": "强化发现证据的危险感"},
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["candidate_value"] == "记者在密室中找到被调包的原始卷宗"
    assert repository.get_episode(source["id"])["content"]["core_event"] == "记者找到旧案卷宗"

    applied = client.post(
        f"/api/creative/episodes/{source['id']}/fields/core_event/apply-rewrite",
        json={
            "candidate_value": generated.json()["candidate_value"],
            "source_fingerprint": generated.json()["source_fingerprint"],
            "expected_project_revision": project["revision"],
            "expected_head_revision": head["revision"],
            "operation_id": "free-draft-field-rewrite",
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["created_new_version"] is False
    saved = repository.get_episode(source["id"])
    assert saved["content"]["core_event"] == "记者在密室中找到被调包的原始卷宗"
    assert saved["content"]["conflict"] == original_conflict


def test_free_locked_outline_field_rewrite_derives_new_head_without_mutating_source(
    tmp_path: Path,
    postgres_creative,
):
    client, repository, _ = build_phase4_client(
        tmp_path, repository=postgres_creative
    )
    project, locked, head = seed_free_outline_for_api(
        client, repository, locked=True
    )
    source = locked["episodes"][0]
    original = [dict(item["content"]) for item in locked["episodes"]]

    response = client.post(
        f"/api/creative/episodes/{source['id']}/fields/core_event/apply-rewrite",
        json={
            "candidate_value": "锁定版派生出的新核心事件",
            "source_fingerprint": outline_field_source_fingerprint(source, "core_event"),
            "expected_project_revision": project["revision"],
            "expected_head_revision": head["revision"],
            "operation_id": "free-locked-field-rewrite",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["created_new_version"] is True
    assert payload["batch_id"] != locked["batch_id"]
    derived = repository.get_outline_batch(project["id"], payload["batch_id"])
    assert len(derived["episodes"]) == len(locked["episodes"]) == 2
    assert derived["episodes"][0]["content"]["core_event"] == "锁定版派生出的新核心事件"
    assert derived["episodes"][1]["content"] == original[1]
    assert repository.get_outline_batch(
        project["id"], locked["batch_id"]
    )["episodes"][0]["content"] == original[0]
    assert FreeCreativeModeService(repository).get_head(
        project["id"], "outline"
    )["resource_ref"] == payload["batch_id"]


def test_outline_field_rewrite_api_rejects_whitespace_candidate_without_deriving_locked_version(
    tmp_path: Path,
):
    client, repository, _ = build_phase4_client(tmp_path)
    project, batch, _ = seed_draft_outline_for_api(client, repository)
    repository.lock_outline_batch(project["id"], batch["batch_id"])
    locked = repository.get_outline_batch(project["id"], batch["batch_id"])
    source = locked["episodes"][0]
    before = repository.get_project(project["id"])

    response = client.post(
        f"/api/creative/episodes/{source['id']}/fields/core_event/apply-rewrite",
        json={
            "candidate_value": " \n\t ",
            "source_fingerprint": outline_field_source_fingerprint(source, "core_event"),
            "expected_project_revision": before["revision"],
            "operation_id": "locked-whitespace-field-rewrite",
        },
    )

    assert response.status_code == 422
    after = repository.get_project(project["id"])
    assert after["revision"] == before["revision"]
    assert after["outline_batch_id"] == locked["batch_id"]
    assert len(after["episodes"]) == len(locked["episodes"]) == 2


@pytest.mark.parametrize(
    ("provider_factory", "expected_status"),
    [
        (None, 503),
        (lambda: InlineSequentialProvider([RuntimeError("secret request body")]), 502),
        (lambda: InlineSequentialProvider([{"value": ""}]), 502),
    ],
)
def test_outline_field_rewrite_api_reports_provider_errors_without_writing(
    tmp_path: Path, provider_factory, expected_status: int
):
    client, repository, _ = build_phase4_client(
        tmp_path, llm_provider_factory=provider_factory
    )
    _, batch, _ = seed_draft_outline_for_api(client, repository)
    episode = batch["episodes"][0]
    before = repository.get_episode(episode["id"])["content"]

    response = client.post(
        f"/api/creative/episodes/{episode['id']}/fields/core_event/rewrite",
        json={"instruction": "不要泄漏这一段"},
    )

    assert response.status_code == expected_status
    assert "secret request body" not in response.text
    assert "不要泄漏这一段" not in response.text
    assert repository.get_episode(episode["id"])["content"] == before


def test_outline_field_rewrite_api_rejects_invalid_field_and_stale_fingerprint(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    project, batch, _ = seed_draft_outline_for_api(client, repository)
    episode = batch["episodes"][0]

    invalid = client.post(
        f"/api/creative/episodes/{episode['id']}/fields/title/apply-rewrite",
        json={
            "candidate_value": "不能改标题",
            "source_fingerprint": "0" * 64,
            "expected_project_revision": repository.get_project(project["id"])["revision"],
        },
    )
    stale = client.post(
        f"/api/creative/episodes/{episode['id']}/fields/core_event/apply-rewrite",
        json={
            "candidate_value": "过期候选",
            "source_fingerprint": "0" * 64,
            "expected_project_revision": repository.get_project(project["id"])["revision"],
        },
    )

    assert invalid.status_code == 400
    assert stale.status_code == 409
    assert repository.get_episode(episode["id"])["content"]["core_event"] == "发现假账"


def test_outline_field_rewrite_api_rejects_locked_source_after_new_draft_exists(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    project, batch, task = seed_draft_outline_for_api(client, repository)
    repository.lock_outline_batch(project["id"], batch["batch_id"])
    locked = repository.get_outline_batch(project["id"], batch["batch_id"])
    source = locked["episodes"][0]
    repository.create_outline_batch(
        project["id"],
        [dict(item["content"]) for item in locked["episodes"]],
        "新草稿弧线",
        workflow_task_id=task["id"],
        replaces_resource_id=f"{project['id']}:{locked['batch_id']}",
        expected_project_revision=repository.get_project(project["id"])["revision"],
    )

    response = client.post(
        f"/api/creative/episodes/{source['id']}/fields/core_event/apply-rewrite",
        json={
            "candidate_value": "不应产生分支",
            "source_fingerprint": outline_field_source_fingerprint(source, "core_event"),
            "expected_project_revision": repository.get_project(project["id"])["revision"],
        },
    )

    assert response.status_code == 409
    assert len(repository.get_project(project["id"])["episodes"]) == 4


def test_outline_overview_api_saves_and_starts_legacy_generation(tmp_path: Path):
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    runtime_dir = tmp_path / ".runtime"
    identity = IdentityRepository(SystemDatabase(SystemDatabaseSettings(postgres_url)))
    identity.seed_defaults()
    admin_role = identity.get_role_by_code("system_admin")
    assert admin_role is not None
    suffix = uuid.uuid4().hex
    admin = identity.create_user(f"outline-overview-{suffix}", "Outline Admin", "hash")
    identity.replace_user_roles(admin["id"], [admin_role["id"]])
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    principal = permissions.principal_for_user(admin["id"], f"outline-overview-{suffix}")
    assert principal is not None
    workflow = WorkflowRepository(identity)
    manager = RecordingJobManager()
    repository = CreativeRepository(runtime_dir / "creative" / "creative.db")
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_creative_router(
        root=tmp_path,
        runtime_dir=runtime_dir,
        job_manager=manager,
        repository=repository,
        workflow_repository=workflow,
    ))
    client = TestClient(app)
    session_app = FastAPI()

    @session_app.get("/session")
    async def create_outline_overview_session(request: Request):
        return sessions.create_session(admin["id"], request)

    raw_token, _ = TestClient(session_app).get("/session").json()
    client.cookies.set(sessions.cookie_name, raw_token)
    client.workflow = workflow
    client.principal = principal
    app.state.workflow_identity_resolver = CreativeWorkflowAdapter(
        repository, workflow
    ).workflow_identity
    project, batch, task = seed_draft_outline_for_api(client, repository)
    latest = repository.create_outline_batch(
        project["id"],
        [{"episode": 1, "core_event": "第二版发现"}, {"episode": 2, "core_event": "第二版反击"}],
        "第二版调查、受阻、反击",
        workflow_task_id=task["id"],
    )

    stale = client.post(
        f"/api/creative/projects/{project['id']}/outlines/{batch['batch_id']}/overview/generate",
        json={"workflow_task_id": task["id"]},
    )
    assert stale.status_code == 409
    assert manager.created == []

    generated = client.post(
        f"/api/creative/projects/{project['id']}/outlines/{latest['batch_id']}/overview/generate",
        json={"workflow_task_id": task["id"]},
    )
    assert generated.status_code == 200, generated.text
    assert manager.created[-1]["extra"]["action"] == "outline_overview"
    assert manager.created[-1]["extra"]["batch_id"] == latest["batch_id"]
    saved = client.patch(
        f"/api/creative/projects/{project['id']}/outlines/{latest['batch_id']}/overview",
        json={"series_overview": outline_series_overview()},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["series_overview"] == outline_series_overview()
    duplicate = client.post(
        f"/api/creative/projects/{project['id']}/outlines/{latest['batch_id']}/overview/generate",
        json={"workflow_task_id": task["id"]},
    )
    assert duplicate.status_code == 409

    locked = repository.create_outline_batch(
        project["id"],
        [{"episode": 1, "core_event": "锁定版发现"}, {"episode": 2, "core_event": "锁定版反击"}],
        "锁定版调查、受阻、反击",
        workflow_task_id=task["id"],
    )
    repository.lock_outline_batch(project["id"], locked["batch_id"])
    locked_generation = client.post(
        f"/api/creative/projects/{project['id']}/outlines/{locked['batch_id']}/overview/generate",
        json={},
    )
    assert locked_generation.status_code == 200, locked_generation.text
    assert manager.created[-1]["extra"]["action"] == "outline_overview"
    assert manager.created[-1]["extra"]["batch_id"] == locked["batch_id"]
    assert manager.created[-1]["extra"].get("workflow_task_id") is None
    locked_manual_save = client.patch(
        f"/api/creative/projects/{project['id']}/outlines/{locked['batch_id']}/overview",
        json={"series_overview": outline_series_overview()},
    )
    assert locked_manual_save.status_code == 409


def write_episode_script(root: Path, title: str, episode_number: int, text: str) -> Path:
    episode = root / "data" / "hg" / "jobs" / "job-1" / "dramas" / title / f"episode_{episode_number:02d}"
    episode.mkdir(parents=True, exist_ok=True)
    (episode / "source.json").write_text(
        '{"title":"' + title + '","episode":{"episode":' + str(episode_number) + "}}",
        encoding="utf-8",
    )
    script = episode / "script.md"
    script.write_text("## 转写内容\n\n" + text, encoding="utf-8")
    return script


def write_episode_video(root: Path, title: str, episode_number: int) -> Path:
    episode = root / "data" / "hg" / "jobs" / "job-video" / "dramas" / title / f"episode_{episode_number:02d}"
    episode.mkdir(parents=True, exist_ok=True)
    (episode / "source.json").write_text(
        '{"title":"' + title + '","episode":{"episode":' + str(episode_number) + "}}",
        encoding="utf-8",
    )
    video = episode / "video.mp4"
    video.write_bytes(b"video")
    return video


def test_phase4_api_creates_multiple_projects_and_lists_them(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    topic, breakdown = seed_adopted_topic(repository)
    first_task = assign_phase4_task(client, "brief", topic_id=topic["id"])
    second_task = assign_phase4_task(client, "brief", topic_id=topic["id"])

    first = client.post("/api/creative/projects", json={
        "topic_id": topic["id"], "breakdown_id": breakdown["id"],
        "name": "都市方向", "target_episode_count": 20,
        "workflow_task_id": first_task["id"],
    })
    second = client.post("/api/creative/projects", json={
        "topic_id": topic["id"], "breakdown_id": breakdown["id"],
        "name": "年代方向", "target_episode_count": 30,
        "workflow_task_id": second_task["id"],
    })

    assert first.status_code == 200
    assert second.status_code == 200
    updated = client.patch(f"/api/creative/projects/{first.json()['id']}", json={"target_audience": "女性用户"})
    assert updated.status_code == 200
    assert updated.json()["target_audience"] == "女性用户"
    payload = client.get(f"/api/creative/projects?topic_id={topic['id']}").json()
    assert {item["name"] for item in payload["items"]} == {"年代方向", "都市方向"}


def test_project_creation_retries_binding_without_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client, repository, _ = build_phase4_client(tmp_path)
    topic, breakdown = seed_adopted_topic(repository)
    task = assign_phase4_task(client, "brief", topic_id=topic["id"])
    original_bind = WorkflowRepository.bind_workflow_project

    def fail_bind(*args, **kwargs):
        raise OSError("system database unavailable")

    monkeypatch.setattr(WorkflowRepository, "bind_workflow_project", fail_bind)
    payload = {
        "topic_id": topic["id"],
        "breakdown_id": breakdown["id"],
        "name": "Retry-safe project",
        "target_episode_count": 20,
        "workflow_task_id": task["id"],
    }
    failed = client.post("/api/creative/projects", json=payload)
    assert failed.status_code == 503
    orphan = repository.get_project_by_workflow_task_id(task["id"])
    assert orphan is not None
    assert client.workflow.get_task(task["id"])["project_id"] is None

    monkeypatch.setattr(
        WorkflowRepository, "bind_workflow_project", original_bind
    )
    reconciled_list = client.get("/api/creative/projects")
    assert any(
        item["id"] == orphan["id"]
        for item in reconciled_list.json()["items"]
    )
    assert client.workflow.get_task(task["id"])["project_id"] is None
    retried = client.post("/api/creative/projects", json=payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["id"] == orphan["id"]
    assert client.workflow.get_task(task["id"])["project_id"] == (
        repository.workflow_project_id(orphan["id"])
    )
    assert len([
        project
        for project in repository.list_projects(topic_id=topic["id"])
        if project["workflow_task_id"] == task["id"]
    ]) == 1
    listed = client.get("/api/creative/projects")
    assert any(item["id"] == orphan["id"] for item in listed.json()["items"])


def test_project_retry_prefers_system_bound_project_over_creative_mismatch(
    tmp_path: Path,
):
    client, repository, _ = build_phase4_client(tmp_path)
    topic, breakdown = seed_adopted_topic(repository)
    task = assign_phase4_task(client, "brief", topic_id=topic["id"])
    payload = {
        "topic_id": topic["id"],
        "breakdown_id": breakdown["id"],
        "name": "Bound project",
        "target_episode_count": 20,
        "workflow_task_id": task["id"],
    }
    first = client.post("/api/creative/projects", json=payload)
    assert first.status_code == 200, first.text
    bound = first.json()

    with repository._connect() as connection:
        connection.execute(
            "UPDATE creative_projects SET workflow_task_id = NULL, "
            "creation_workflow_task_id = NULL WHERE id = ?",
            (bound["id"],),
        )
    mismatch = repository.create_project(
        topic["id"],
        breakdown["id"],
        "Mismatched project",
        20,
        workflow_task_id=task["id"],
        created_by=client.principal.user_id,
    )
    assert mismatch["id"] != bound["id"]

    retried = client.post("/api/creative/projects", json=payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["id"] == bound["id"]
    with repository._connect() as connection:
        rows = connection.execute(
            "SELECT id, workflow_task_id FROM creative_projects ORDER BY id"
        ).fetchall()
    assert [row["id"] for row in rows if row["workflow_task_id"] == task["id"]] == [
        bound["id"]
    ]


def test_project_duplicate_migration_keeps_newest_nondeleted_row(tmp_path: Path):
    path = tmp_path / "creative.db"
    repository = CreativeRepository(path)
    topic, breakdown = seed_adopted_topic(repository)
    first = repository.create_project(
        topic["id"],
        breakdown["id"],
        "Older orphan",
        20,
        workflow_task_id="duplicate-task",
    )
    with repository._connect() as connection:
        connection.execute("DROP INDEX uq_creative_projects_workflow_task")
        connection.execute(
            "DROP INDEX uq_creative_projects_creation_workflow_task"
        )
        cursor = connection.execute(
            """
            INSERT INTO creative_projects (
                topic_id, breakdown_id, name, genre, target_platform,
                target_audience, target_episode_count, episode_duration_seconds,
                status, workflow_task_id, creation_workflow_task_id,
                created_by, created_at, updated_at
            )
            SELECT topic_id, breakdown_id, 'Newer bound', genre, target_platform,
                   target_audience, target_episode_count, episode_duration_seconds,
                   status, workflow_task_id, creation_workflow_task_id,
                   created_by, created_at + 1, updated_at + 1
            FROM creative_projects WHERE id = ?
            """,
            (first["id"],),
        )
        newer_id = int(cursor.lastrowid)

    CreativeRepository(path)
    with repository._connect() as connection:
        rows = connection.execute(
            "SELECT id, workflow_task_id, creation_workflow_task_id "
            "FROM creative_projects ORDER BY id"
        ).fetchall()
    assert rows[0]["workflow_task_id"] is None
    assert rows[0]["creation_workflow_task_id"] is None
    assert rows[1]["id"] == newer_id
    assert rows[1]["workflow_task_id"] == "duplicate-task"
    assert rows[1]["creation_workflow_task_id"] == "duplicate-task"


def test_project_list_reports_legacy_orphan_mismatch_without_rewriting_links(
    tmp_path: Path,
):
    client, repository, _ = build_phase4_client(tmp_path)
    topic, breakdown = seed_adopted_topic(repository)
    brief_task = assign_phase4_task(client, "brief", topic_id=topic["id"])
    orphan = repository.create_project(
        topic["id"],
        breakdown["id"],
        "Older orphan",
        20,
        workflow_task_id=brief_task["id"],
        created_by=client.principal.user_id,
    )
    target = repository.create_project(
        topic["id"],
        breakdown["id"],
        "Bound target",
        20,
        created_by=client.principal.user_id,
    )
    client.workflow.bind_workflow_project(
        brief_task["id"], str(target["id"]), client.principal
    )
    characters_task = client.workflow.create_task(
        "legacy-current-characters",
        "characters",
        "creative_planner",
        topic_id=topic["id"],
        project_id=str(target["id"]),
        actor=SYSTEM_ACTOR,
    )
    client.workflow.claim_task(characters_task["id"], client.principal)
    repository.link_project_workflow_task(target["id"], characters_task["id"])

    listed = client.get("/api/creative/projects")

    assert listed.status_code == 200, listed.text
    reloaded_target = repository.get_project(target["id"])
    reloaded_orphan = repository.get_project(orphan["id"])
    assert reloaded_target is not None
    assert reloaded_orphan is not None
    assert reloaded_target["workflow_task_id"] == characters_task["id"]
    assert reloaded_target["creation_workflow_task_id"] is None
    assert reloaded_orphan["workflow_task_id"] == brief_task["id"]
    assert reloaded_orphan["creation_workflow_task_id"] == brief_task["id"]
    items = {item["id"]: item for item in listed.json()["items"]}
    assert items[target["id"]]["workflow_link_status"] == "unbound"
    assert items[orphan["id"]]["workflow_link_status"] == "mismatched"


def test_breakdown_api_returns_personalized_field_writing_guide(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    _, breakdown = seed_adopted_topic(repository)

    response = client.get(f"/api/creative/breakdowns/{breakdown['id']}")

    assert response.status_code == 200
    guide = response.json()["field_guide"]
    assert len(guide) == 15
    assert next(item for item in guide if item["key"] == "hook_points")["evidence_rule"]
    assert next(item for item in guide if item["key"] == "core_conflict")["example"] == breakdown["content"]["core_conflict"]


def test_repository_latest_breakdown_includes_newest_draft(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, locked = seed_adopted_topic(repository)
    draft = repository.create_breakdown(
        topic["id"], {"core_conflict": "最新草稿冲突"}, {}, [], parent_version_id=locked["id"],
    )

    latest = repository.get_latest_breakdown(topic["id"])

    assert latest["id"] == draft["id"]
    assert latest["status"] == "draft"


def test_topic_list_exposes_latest_breakdown_metadata(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic, locked = seed_adopted_topic(repository)
    draft = repository.create_breakdown(
        topic["id"], {"core_conflict": "最新草稿冲突"}, {}, [], parent_version_id=locked["id"],
    )

    listed = repository.list_topics(limit=10)[0]

    assert listed["locked_breakdown_id"] == locked["id"]
    assert listed["latest_breakdown_id"] == draft["id"]
    assert listed["latest_breakdown_version"] == draft["version"]
    assert listed["latest_breakdown_status"] == "draft"


def test_merged_topic_uses_breakdown_owner_for_repeat_actions(tmp_path: Path):
    repository = CreativeRepository(tmp_path / "creative.db")
    base = {
        "platform": "dy", "captured_at": "2026-07-20", "title": "跨快照短剧",
        "description": "", "category": "都市", "rank_type": "热播榜",
        "confidence": "high", "score_details": {}, "recommendation": "推荐", "evidence": [],
    }
    older = repository.upsert_topics([{**base, "job_id": "old", "source_path": "old.json", "score": 70}])[0]
    breakdown = repository.create_breakdown(older["id"], {"core_conflict": "旧快照拆解"}, {}, [])
    repository.upsert_topics([{**base, "job_id": "new", "source_path": "new.json", "score": 90}])

    listed = repository.list_topics(platform="dy", limit=10)[0]

    assert listed["score"] == 90
    assert listed["latest_breakdown_id"] == breakdown["id"]
    assert listed["id"] == older["id"]


def test_breakdown_api_reuses_latest_version_without_creating_job(tmp_path: Path):
    client, repository, manager = build_phase4_client(tmp_path)
    topic, locked = seed_adopted_topic(repository)
    draft = repository.create_breakdown(
        topic["id"], {"core_conflict": "最新草稿冲突"}, {}, [], parent_version_id=locked["id"],
    )
    assign_phase4_task(client, "breakdown", topic_id=topic["id"])

    response = client.post("/api/creative/breakdowns", json={"topic_id": topic["id"]})

    assert response.status_code == 200
    assert response.json()["status"] == "existing"
    assert response.json()["breakdown"]["id"] == draft["id"]
    assert response.json()["breakdown"]["field_guide"]
    assert manager.created == []


def test_breakdown_api_requires_at_least_one_transcript(tmp_path: Path):
    client, repository, manager = build_phase4_client(tmp_path)
    topic = seed_topic_without_breakdown(repository)
    assign_phase4_task(client, "breakdown", topic_id=topic["id"])

    response = client.post("/api/creative/breakdowns", json={"topic_id": topic["id"]})

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "transcript_required",
        "message": "请先下载剧目脚本再拆解",
        "topic_id": topic["id"],
        "title": topic["title"],
    }
    assert manager.created == []
    with repository._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM creative_breakdowns WHERE topic_id = ?", (topic["id"],)).fetchone()[0]
    assert count == 0


def test_breakdown_api_enqueues_job_when_transcript_exists(tmp_path: Path):
    client, repository, manager = build_phase4_client(tmp_path)
    topic = seed_topic_without_breakdown(repository)
    assign_phase4_task(client, "breakdown", topic_id=topic["id"])
    write_episode_script(tmp_path, topic["title"], 1, "林小满在宴会上公开拒绝家族安排。")

    response = client.post("/api/creative/breakdowns", json={"topic_id": topic["id"]})

    assert response.status_code == 200
    assert response.json()["job"]["status"] == "running"
    assert response.json()["coverage"] == [1]
    assert manager.created[0]["crawler_type"] == "breakdown_generate"
    assert manager.created[0]["extra"]["topic_id"] == topic["id"]
    assert manager.created[0]["extra"]["workspace_root"] == str(tmp_path.resolve())


def test_breakdown_api_enqueues_without_workflow_task(tmp_path: Path):
    client, repository, manager = build_phase4_client(tmp_path)
    topic = seed_topic_without_breakdown(repository, "免任务拆解")
    write_episode_script(tmp_path, topic["title"], 1, "第一集剧情正文")

    response = client.post("/api/creative/breakdowns", json={"topic_id": topic["id"]})

    assert response.status_code == 200, response.text
    assert response.json()["job"]["workflow_task_id"] is None
    assert manager.created[0]["extra"]["workflow_task_id"] is None


def test_topic_transcript_preview_returns_all_downloaded_episode_scripts(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    topic = seed_topic_without_breakdown(repository, "转写预览短剧")
    write_episode_script(tmp_path, topic["title"], 2, "第二集：主角发现新的线索。")
    write_episode_script(tmp_path, topic["title"], 1, "第一集：主角进入宴会厅。")

    response = client.get(f"/api/creative/topics/{topic['id']}/transcripts")

    assert response.status_code == 200
    assert response.json()["title"] == topic["title"]
    assert response.json()["episode_count"] == 2
    assert [item["episode_number"] for item in response.json()["episodes"]] == [1, 2]
    assert response.json()["episodes"][0]["text"] == "第一集：主角进入宴会厅。"
    assert response.json()["episodes"][0]["character_count"] == len("第一集：主角进入宴会厅。")


def test_topic_transcript_preview_reports_when_no_transcript_exists(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    topic = seed_topic_without_breakdown(repository, "没有转写的短剧")

    response = client.get(f"/api/creative/topics/{topic['id']}/transcripts")

    assert response.status_code == 404
    assert response.json()["detail"] == "该剧目暂无可预览的转写"


def test_breakdown_download_returns_the_current_version_as_a_local_markdown_file(
    tmp_path: Path,
):
    client, repository, _ = build_phase4_client(tmp_path)
    _, breakdown = seed_adopted_topic(repository)

    response = client.get(f"/api/creative/breakdowns/{breakdown['id']}/download")

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        f'attachment; filename="breakdown-{breakdown["id"]}.md"'
    )
    assert response.headers["content-type"].startswith("text/markdown")
    assert "核心冲突" in response.text


def test_breakdown_api_enqueues_transcription_when_only_video_exists(tmp_path: Path):
    client, repository, manager = build_phase4_client(tmp_path)
    topic = seed_topic_without_breakdown(repository)
    assign_phase4_task(client, "breakdown", topic_id=topic["id"])
    write_episode_video(tmp_path, topic["title"], 1)

    response = client.post("/api/creative/breakdowns", json={"topic_id": topic["id"]})

    assert response.status_code == 200
    assert response.json()["requires_transcription"] is True
    assert response.json()["coverage"] == [1]
    assert manager.created[0]["crawler_type"] == "breakdown_generate"


def test_breakdown_api_force_regenerate_enqueues_job_for_existing_version(tmp_path: Path):
    client, repository, manager = build_phase4_client(tmp_path)
    topic, _ = seed_adopted_topic(repository)
    assign_phase4_task(client, "breakdown", topic_id=topic["id"])
    write_episode_script(tmp_path, topic["title"], 1, "林小满在宴会上公开拒绝家族安排。")

    response = client.post("/api/creative/breakdowns", json={
        "topic_id": topic["id"], "force_regenerate": True,
    })

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert manager.created[0]["crawler_type"] == "breakdown_generate"


def test_breakdown_api_lists_locked_versions_for_project_source_selection(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    topic, first = seed_adopted_topic(repository)
    second = repository.create_breakdown(topic["id"], {"core_conflict": "新版冲突"}, {}, [], parent_version_id=first["id"])
    second = repository.lock_breakdown(second["id"])

    response = client.get(f"/api/creative/topics/{topic['id']}/breakdowns?status=locked")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [second["id"], first["id"]]
    assert [item["version"] for item in response.json()["items"]] == [2, 1]


def test_phase4_generation_api_enqueues_standard_creative_job(tmp_path: Path):
    client, repository, manager = build_phase4_client(tmp_path)
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "测试项目", 20)

    missing_task = client.post(
        f"/api/creative/projects/{project['id']}/blueprints/generate",
        json={"count": 4},
    )

    assert missing_task.status_code == 409
    assert manager.created == []

    task = client.workflow.create_task(
        "phase4-generation",
        "brief",
        "creative_planner",
        topic_id=topic["id"],
        project_id=repository.workflow_project_id(project["id"]),
        actor=SYSTEM_ACTOR,
    )
    client.workflow.claim_task(task["id"], client.principal)

    response = client.post(
        f"/api/creative/projects/{project['id']}/blueprints/generate",
        json={"count": 4},
    )

    assert response.status_code == 200
    assert response.json()["job"]["status"] == "running"
    assert manager.created[0]["platform"] == "creative"
    assert manager.created[0]["crawler_type"] == "phase4_generate"
    assert manager.created[0]["extra"]["action"] == "blueprints"
    assert manager.created[0]["extra"]["count"] == 4
    assert manager.created[0]["extra"]["workflow_task_id"] == task["id"]


def test_phase4_legacy_export_requires_approved_delivery_submission(tmp_path: Path):
    client, repository, _ = build_phase4_client(tmp_path)
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "导出项目", 20)
    blueprint = repository.create_blueprints(project["id"], [{
        "content": {"title": "新方向", "core_conflict": "新的冲突"},
        "risk_score": 20,
        "risk_level": "low",
    }])[0]
    repository.lock_brief(blueprint["id"])

    markdown = client.get(f"/api/creative/projects/{project['id']}/export?stage=brief&format=md")
    json_response = client.get(f"/api/creative/projects/{project['id']}/export?stage=brief&format=json")

    assert markdown.status_code == 409
    assert json_response.status_code == 409
    assert "approved submission_id is required" in markdown.json()["detail"]


def test_generated_content_preview_uses_latest_version_and_supports_local_download(
    tmp_path: Path,
):
    client, repository, _ = build_phase4_client(tmp_path)
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "预览项目", 1)
    first = repository.create_blueprints(project["id"], [{
        "content": {"title": "初版 Brief"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    latest = repository.create_blueprints(project["id"], [{
        "content": {"title": "新版 Brief"}, "risk_score": 8, "risk_level": "low",
    }])[0]

    response = client.get(
        f"/api/creative/projects/{project['id']}/generated-content",
        params={"stage": "brief"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["selected_resource_id"] == str(latest["id"])
    assert [item["resource_id"] for item in payload["versions"]] == [
        str(latest["id"]), str(first["id"])
    ]
    assert payload["snapshot"]["content"]["title"] == "新版 Brief"

    historic = client.get(
        f"/api/creative/projects/{project['id']}/generated-content",
        params={"stage": "brief", "resource_id": str(first["id"])},
    )
    assert historic.status_code == 200
    assert historic.json()["snapshot"]["content"]["title"] == "初版 Brief"

    download = client.get(
        f"/api/creative/projects/{project['id']}/generated-content/download",
        params={"stage": "brief", "resource_id": str(latest["id"]), "format": "md"},
    )
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "新版 Brief" in download.text


def test_beats_markdown_export_uses_chinese_fields_and_includes_field_guide(tmp_path: Path):
    _, repository, _ = build_phase4_client(tmp_path)
    topic, breakdown = seed_adopted_topic(repository)
    project = repository.create_project(topic["id"], breakdown["id"], "节拍导出", 1)
    blueprint = repository.create_blueprints(project["id"], [{
        "content": {"title": "方向"}, "risk_score": 10, "risk_level": "low",
    }])[0]
    repository.lock_brief(blueprint["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "单集弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(project["id"], 1, {"beats": [{
        "order": 1,
        "scene": "早餐店",
        "characters": ["主角"],
        "goal": "拿到账本",
        "obstacle": "家人阻挠",
        "emotion_change": "隐忍到坚定",
        "turn": "旧账本出现",
        "hook": "陌生通知到达",
    }]})

    markdown = build_beats_markdown(
        repository.get_project(project["id"]), [beats]
    )

    assert "## 字段说明" in markdown
    assert "**节拍序号**" in markdown
    assert "**场景**" in markdown
    assert "**出场人物**" in markdown
    assert "**行动目标**" in markdown
    assert "**情绪变化**" in markdown
    assert "**结尾钩子**" in markdown
    assert "用于说明该节拍发生的具体地点和情境" in markdown
    assert "**order**" not in markdown
    assert "**episode number**" not in markdown
