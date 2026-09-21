# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from creative.prompt_registry import (
    builtin_template,
    compose_prompt_snapshot,
    content_hash,
)
from creative.project_prompt_service import ProjectPromptService
from creative.prompt_store import PromptStore
from creative.prompt_templates import (
    beats_messages,
    blueprint_messages,
    character_messages,
    outline_messages,
    script_messages,
)
from creative.repository import CreativeRepository


@pytest.fixture(autouse=True)
def _use_postgres_creative(monkeypatch, postgres_creative):
    """Route historical constructor calls onto the isolated PostgreSQL store."""

    monkeypatch.setitem(
        globals(), "CreativeRepository", lambda *_args, **_kwargs: postgres_creative
    )


def test_prompt_immediate_transactions_serialize_and_release(postgres_creative):
    store = PromptStore(postgres_creative)
    with store._connect() as first:
        first.execute("BEGIN IMMEDIATE")
        with pytest.raises(OperationalError, match="lock timeout|锁超时"):
            with store._connect() as second:
                second.execute("SET LOCAL lock_timeout = '100ms'")
                second.execute("BEGIN IMMEDIATE")
    with store._connect() as released:
        released.execute("SET LOCAL lock_timeout = '100ms'")
        released.execute("BEGIN IMMEDIATE")


def _project(repository: CreativeRepository, *, name: str = "模板测试项目") -> int:
    now = time.time()
    with repository._connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO creative_projects (
                name, target_episode_count, episode_duration_seconds,
                created_by, creation_mode, workflow_mode, created_at, updated_at
            ) VALUES (?, 12, 120, 'owner-1', 'original', 'sequential', ?, ?)
            """,
            (name, now, now),
        )
    return int(cursor.lastrowid)


@pytest.fixture
def prompt_store(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    return repository, PromptStore(repository)


def test_catalog_seeds_four_selectable_system_templates(prompt_store):
    _, store = prompt_store

    catalog = store.list_catalog(actor_user_id="owner-1")

    assert {item["template_key"] for item in catalog} == {
        "generic-vertical-short",
        "urban-identity-romcom",
        "barrage-revenge",
        "oriental-rule-mystery",
    }
    assert all(item["revision"] == 1 for item in catalog)
    assert all(len(item["content"]["dimensions"]) == 7 for item in catalog)


def test_catalog_hides_legacy_alias_of_canonical_system_template(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository, name="旧模板别名项目")
    content = builtin_template("generic-vertical-short")
    legacy_id = "legacy-system-template-alias"
    legacy_key = "legacy:source01:generic-vertical-short"
    now = time.time()
    with repository._connect() as conn:
        conn.execute(
            """
            INSERT INTO creative_prompt_templates (
                id, template_key, title, description, owner_scope, visibility,
                status, governance_status, approved_revision, catalog_visible,
                created_at, updated_at
            ) VALUES (?, ?, '通用竖屏短剧', '', 'system', 'system',
                      'active', 'approved', 1, TRUE, ?, ?)
            """,
            (legacy_id, legacy_key, now, now),
        )
        conn.execute(
            """
            INSERT INTO creative_prompt_template_revisions (
                template_id, revision, content_json, content_hash,
                created_by, created_at
            ) VALUES (?, 1, ?, ?, 'legacy-import', ?)
            """,
            (
                legacy_id,
                json.dumps(content, ensure_ascii=False),
                content_hash(content),
                now,
            ),
        )
    store.set_binding(
        project_id=project_id,
        stage="script",
        episode_number=0,
        template_id=legacy_id,
        template_revision=1,
        override={"custom_requirements": "保留旧项目绑定"},
        actor_user_id="owner-1",
    )

    refreshed_store = PromptStore(repository)
    catalog = refreshed_store.list_catalog(
        project_id=project_id, actor_user_id="owner-1"
    )

    assert [
        item["template_key"]
        for item in catalog
        if item["title"] == "通用竖屏短剧"
    ] == ["generic-vertical-short"]
    assert refreshed_store.get_template(legacy_id)["catalog_visible"] is False
    assert refreshed_store.binding(project_id, "script", 0)["template_id"] == legacy_id


def test_personal_template_delete_preserves_history_and_blocks_active_bindings(
    prompt_store,
):
    repository, store = prompt_store
    project_id = _project(repository)
    personal = store.create_template(
        title="待删除个人模板",
        description="验证安全删除",
        content=builtin_template("generic-vertical-short"),
        owner_scope="user",
        owner_user_id="owner-1",
    )

    with pytest.raises(PermissionError, match="只能删除自己的个人模板"):
        store.personal_template_deletion_impact(
            personal["id"], actor_user_id="other-user"
        )

    store.set_binding(
        project_id=project_id,
        stage="script",
        episode_number=0,
        template_id=personal["id"],
        template_revision=1,
        override={},
        actor_user_id="owner-1",
    )
    impact = store.personal_template_deletion_impact(
        personal["id"], actor_user_id="owner-1"
    )
    assert impact["can_delete"] is False
    assert impact["active_binding_count"] == 1
    with pytest.raises(ValueError, match="请先改绑"):
        store.delete_personal_template(
            personal["id"], actor_user_id="owner-1", expected_revision=1
        )

    system = store.get_template_by_key("generic-vertical-short")
    store.set_binding(
        project_id=project_id,
        stage="script",
        episode_number=0,
        template_id=system["id"],
        template_revision=1,
        override={},
        actor_user_id="owner-1",
    )
    deleted = store.delete_personal_template(
        personal["id"], actor_user_id="owner-1", expected_revision=1
    )

    assert deleted["deleted"] is True
    assert deleted["template"]["status"] == "archived"
    assert store.get_revision(personal["id"], 1)["content_hash"] == personal[
        "content_hash"
    ]
    assert personal["template_key"] not in {
        item["template_key"]
        for item in store.list_catalog(
            project_id=project_id, actor_user_id="owner-1"
        )
    }


def test_personal_template_rebind_is_project_scoped_and_deletes_after_last_reference(
    prompt_store,
):
    repository, store = prompt_store
    first_project_id = _project(repository, name="第一个改绑项目")
    second_project_id = _project(repository, name="第二个改绑项目")
    personal = store.create_template(
        title="跨项目待改绑模板",
        description="",
        content=builtin_template("generic-vertical-short"),
        owner_scope="user",
        owner_user_id="owner-1",
    )
    replacement = store.get_template_by_key("generic-vertical-short")
    for project_id in (first_project_id, second_project_id):
        store.set_binding(
            project_id=project_id,
            stage="script",
            episode_number=0,
            template_id=personal["id"],
            template_revision=1,
            override={"custom_requirements": f"保留项目 {project_id} 要求"},
            actor_user_id="owner-1",
        )

    impact = store.personal_template_deletion_impact(
        personal["id"],
        actor_user_id="owner-1",
        project_id=first_project_id,
    )
    assert impact["current_project_binding_count"] == 1
    assert impact["other_project_references"][0]["project_id"] == second_project_id
    assert replacement["id"] in {
        candidate["id"] for candidate in impact["replacement_candidates"]
    }

    first_result = store.rebind_and_delete_personal_template(
        project_id=first_project_id,
        template_id=personal["id"],
        actor_user_id="owner-1",
        expected_revision=1,
        replacement_template_id=replacement["id"],
        replacement_template_revision=1,
    )
    first_binding = store.binding(first_project_id, "script", 0)
    assert first_result["deleted"] is False
    assert first_result["remaining_binding_count"] == 1
    assert first_binding["template_id"] == replacement["id"]
    assert first_binding["override"]["custom_requirements"].startswith("保留项目")
    assert store.get_template(personal["id"])["status"] == "active"

    second_result = store.rebind_and_delete_personal_template(
        project_id=second_project_id,
        template_id=personal["id"],
        actor_user_id="owner-1",
        expected_revision=1,
        replacement_template_id=replacement["id"],
        replacement_template_revision=1,
    )
    assert second_result["deleted"] is True
    assert second_result["remaining_binding_count"] == 0
    assert store.get_template(personal["id"])["status"] == "archived"


def test_prompt_store_uses_alembic_managed_governance_schema(prompt_store):
    repository, store = prompt_store
    schema = inspect(repository.database.engine)
    columns = {
        item["name"]
        for item in schema.get_columns("creative_prompt_templates")
    }

    assert {
        "governance_status",
        "governance_revision",
        "approved_revision",
        "governance_note",
        "governance_updated_by",
        "governance_updated_at",
    } <= columns
    assert schema.has_table("creative_prompt_governance_events")
    assert store.get_template_by_key("generic-vertical-short")[
        "approved_revision"
    ] == 1


def test_prompt_store_rejects_non_postgresql_backend():
    with pytest.raises(TypeError, match="PostgreSQL creative repository"):
        PromptStore(object())


def test_old_project_is_legacy_until_owner_enables_prompt_library(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)

    assert store.project_settings(project_id) == {
        "project_id": project_id,
        "enabled": False,
    }
    assert store.resolve_snapshot(
        project_id=project_id,
        stage="beats",
        mode="single",
        episode_number=1,
    ) is None

    one_run = store.resolve_snapshot(
        project_id=project_id,
        stage="beats",
        mode="single",
        episode_number=1,
        run_template_key="urban-identity-romcom",
        actor_user_id="owner-1",
    )
    assert one_run["template_key"] == "urban-identity-romcom"
    assert store.project_settings(project_id)["enabled"] is False

    store.enable_project(project_id, actor_user_id="owner-1")
    snapshot = store.resolve_snapshot(
        project_id=project_id,
        stage="beats",
        mode="single",
        episode_number=1,
    )

    assert snapshot is not None
    assert snapshot["template_key"] == "generic-vertical-short"
    assert snapshot["stage"] == "beats"
    assert snapshot["mode"] == "single"
    assert len(snapshot["content_hash"]) == 64


def test_first_persistent_binding_implicitly_enables_legacy_project(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    service = ProjectPromptService(repository)

    result = service.bind(
        project_id=project_id,
        stage="script",
        episode_number=0,
        template_key="urban-identity-romcom",
        template_revision=1,
        override={"custom_requirements": "后续新稿减少内心独白。"},
        actor_user_id="owner-1",
    )

    default_template = store.get_template(result["settings"]["default_template_id"])
    assert result["settings"]["enabled"] is True
    assert default_template["template_key"] == "classic-compatible"
    assert result["effective"]["template_key"] == "urban-identity-romcom"
    assert "后续新稿减少内心独白" in result["effective"]["effective_instruction"]


def test_episode_binding_wins_over_stage_binding_and_run_selection_wins_last(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    comedy = store.get_template_by_key("urban-identity-romcom")
    revenge = store.get_template_by_key("barrage-revenge")
    assert comedy is not None and revenge is not None
    store.set_binding(
        project_id=project_id,
        stage="script",
        episode_number=0,
        template_id=comedy["id"],
        template_revision=1,
        override={"custom_requirements": "减少内心独白。"},
        actor_user_id="owner-1",
    )
    store.set_binding(
        project_id=project_id,
        stage="script",
        episode_number=3,
        template_id=revenge["id"],
        template_revision=1,
        override={"custom_requirements": "本集只使用一条弹幕。"},
        actor_user_id="owner-1",
    )

    episode_snapshot = store.resolve_snapshot(
        project_id=project_id,
        stage="script",
        mode="single",
        episode_number=3,
    )
    stage_snapshot = store.resolve_snapshot(
        project_id=project_id,
        stage="script",
        mode="single",
        episode_number=4,
    )
    run_snapshot = store.resolve_snapshot(
        project_id=project_id,
        stage="script",
        mode="single",
        episode_number=3,
        run_template_key="oriental-rule-mystery",
        run_override={"custom_requirements": "本次突出道具伏笔。"},
    )

    assert episode_snapshot["template_key"] == "barrage-revenge"
    assert "本集只使用一条弹幕" in episode_snapshot["effective_instruction"]
    assert stage_snapshot["template_key"] == "urban-identity-romcom"
    assert run_snapshot["template_key"] == "oriental-rule-mystery"
    assert "本次突出道具伏笔" in run_snapshot["effective_instruction"]


def test_custom_template_revisions_are_immutable_and_system_template_is_read_only(prompt_store):
    _, store = prompt_store
    content = builtin_template("generic-vertical-short")
    custom = store.create_template(
        title="我的克制短剧",
        description="个人模板",
        content=content,
        owner_scope="user",
        owner_user_id="owner-1",
    )
    changed = builtin_template("generic-vertical-short")
    changed["shared_dna"] = "所有冲突都通过人物的困难选择推进。"

    revision = store.save_revision(
        custom["id"],
        changed,
        expected_revision=1,
        actor_user_id="owner-1",
    )

    assert revision["revision"] == 2
    assert store.get_revision(custom["id"], 1)["content"]["shared_dna"] != revision["content"]["shared_dna"]
    system = store.get_template_by_key("generic-vertical-short")
    assert system is not None
    with pytest.raises(ValueError, match="系统模板只读"):
        store.save_revision(
            system["id"],
            changed,
            expected_revision=1,
            actor_user_id="owner-1",
        )


def test_project_template_revision_waits_for_approval_without_breaking_pinned_use(
    prompt_store,
):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.create_template(
        title="受控项目模板",
        description="治理测试",
        content=builtin_template("generic-vertical-short"),
        owner_scope="project",
        owner_user_id="owner-1",
        project_id=project_id,
    )
    store.set_binding(
        project_id=project_id,
        stage="beats",
        episode_number=0,
        template_id=template["id"],
        template_revision=1,
        override={},
        actor_user_id="owner-1",
    )
    changed = builtin_template("generic-vertical-short")
    changed["shared_dna"] += " 新版本必须经过项目审核。"

    revision = store.save_revision(
        template["id"], changed,
        expected_revision=1, actor_user_id="owner-1",
    )
    pending = store.get_template(template["id"])
    pinned = store.resolve_snapshot(
        project_id=project_id, stage="beats", mode="single"
    )

    assert revision["revision"] == 2
    assert pending["governance_status"] == "pending_review"
    assert pending["approved_revision"] == 1
    assert pinned["revision"] == 1
    assert template["template_key"] not in {
        item["template_key"]
        for item in store.list_catalog(
            project_id=project_id, actor_user_id="owner-1"
        )
    }
    with pytest.raises(ValueError, match="尚未通过审核"):
        store.resolve_snapshot(
            project_id=project_id,
            stage="beats",
            mode="single",
            run_template_key=template["template_key"],
            run_template_revision=2,
            actor_user_id="owner-1",
        )

    approved = store.govern_project_template(
        project_id=project_id,
        template_id=template["id"],
        action="approve",
        note="审核通过，结构与契约均符合要求",
        expected_governance_revision=1,
        operation_id="approve-project-template-v2",
        actor_user_id="owner-1",
    )
    retried = store.govern_project_template(
        project_id=project_id,
        template_id=template["id"],
        action="approve",
        note="审核通过，结构与契约均符合要求",
        expected_governance_revision=1,
        operation_id="approve-project-template-v2",
        actor_user_id="owner-1",
    )

    assert approved["template"]["governance_status"] == "approved"
    assert approved["template"]["approved_revision"] == 2
    assert approved["event"]["id"] == retried["event"]["id"]
    assert store.resolve_snapshot(
        project_id=project_id,
        stage="beats",
        mode="single",
        run_template_key=template["template_key"],
        run_template_revision=2,
        actor_user_id="owner-1",
    )["revision"] == 2


def test_deprecated_template_keeps_existing_binding_and_archive_requires_rebinding(
    prompt_store,
):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.create_template(
        title="待弃用项目模板",
        description="",
        content=builtin_template("generic-vertical-short"),
        owner_scope="project",
        owner_user_id="owner-1",
        project_id=project_id,
    )
    store.set_binding(
        project_id=project_id, stage="script", episode_number=0,
        template_id=template["id"], template_revision=1,
        override={}, actor_user_id="owner-1",
    )

    deprecated = store.govern_project_template(
        project_id=project_id,
        template_id=template["id"],
        action="deprecate",
        note="新项目不再使用，但保留已有剧本绑定",
        expected_governance_revision=0,
        operation_id="deprecate-project-template",
        actor_user_id="owner-1",
    )

    assert deprecated["template"]["governance_status"] == "deprecated"
    assert deprecated["impact"]["active_binding_count"] == 1
    assert store.resolve_snapshot(
        project_id=project_id, stage="script", mode="single"
    )["template_key"] == template["template_key"]
    with pytest.raises(ValueError, match="停止使用"):
        store.resolve_snapshot(
            project_id=project_id,
            stage="script",
            mode="single",
            run_template_key=template["template_key"],
            actor_user_id="owner-1",
        )
    with pytest.raises(ValueError, match="请先改绑"):
        store.govern_project_template(
            project_id=project_id,
            template_id=template["id"],
            action="archive",
            note="尝试归档仍在使用的模板",
            expected_governance_revision=1,
            operation_id="blocked-project-template-archive",
            actor_user_id="owner-1",
        )

    generic = store.get_template_by_key("generic-vertical-short")
    store.set_binding(
        project_id=project_id, stage="script", episode_number=0,
        template_id=generic["id"], template_revision=1,
        override={}, actor_user_id="owner-1",
    )
    archived = store.govern_project_template(
        project_id=project_id,
        template_id=template["id"],
        action="archive",
        note="绑定已经迁移，正式归档",
        expected_governance_revision=1,
        operation_id="archive-project-template",
        actor_user_id="owner-1",
    )
    restored = store.govern_project_template(
        project_id=project_id,
        template_id=template["id"],
        action="restore",
        note="恢复后重新送审",
        expected_governance_revision=2,
        operation_id="restore-project-template",
        actor_user_id="owner-1",
    )

    assert archived["template"]["governance_status"] == "archived"
    assert restored["template"]["governance_status"] == "pending_review"
    assert len(store.list_governance_events(project_id, template["id"])) == 3


def test_template_impact_counts_bindings_ab_and_generated_resource_references(
    prompt_store,
):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.create_template(
        title="影响分析模板",
        description="",
        content=builtin_template("generic-vertical-short"),
        owner_scope="project",
        owner_user_id="owner-1",
        project_id=project_id,
    )
    store.set_binding(
        project_id=project_id, stage="brief", episode_number=0,
        template_id=template["id"], template_revision=1,
        override={}, actor_user_id="owner-1",
    )
    snapshot = store.resolve_snapshot(
        project_id=project_id,
        stage="brief",
        mode="single",
        run_template_key=template["template_key"],
        actor_user_id="owner-1",
    )
    store.create_ab_experiment(
        operation_id="impact-ab-experiment",
        project_id=project_id,
        stage="brief",
        episode_number=0,
        model_record_id="model-1",
        source_fingerprint="source-hash",
        source_snapshot={},
        preflight_hash="preflight-hash",
        estimate={},
        budget_amount=1,
        price_currency="CNY",
        prompt_snapshots={"A": snapshot, "B": snapshot},
        actor_user_id="owner-1",
    )
    repository.create_blueprints(
        project_id,
        [{"content": {"title": "候选方向"}}],
        generation_meta={"prompt_template_id": template["id"]},
    )

    impact = store.template_impact(project_id, template["id"])

    assert impact["active_binding_count"] == 1
    assert len(impact["historical_references"]["ab_arms"]) == 2
    assert impact["historical_references"]["resource_reference_count"] == 1
    assert impact["can_archive"] is False


def test_prompt_snapshot_hash_changes_with_single_run_override():
    template = builtin_template("generic-vertical-short")
    first = compose_prompt_snapshot(
        template_id="system:generic-vertical-short",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="beats",
        mode="single",
    )
    second = compose_prompt_snapshot(
        template_id="system:generic-vertical-short",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="beats",
        mode="single",
        override={"custom_requirements": "本次减少到三个节拍。"},
    )

    assert first["content_hash"] != second["content_hash"]
    assert "本次减少到三个节拍" in second["effective_instruction"]


def test_server_resolved_style_is_appended_without_replacing_output_contract():
    template = builtin_template("urban-identity-romcom")
    snapshot = compose_prompt_snapshot(
        template_id="system:urban-identity-romcom",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="beats",
        mode="single",
    )

    styled = beats_messages({"project": {"name": "测试"}}, 1, snapshot)
    legacy = beats_messages({"project": {"name": "测试"}}, 1)

    assert "严格使用 {\"beats\": [...]} 格式" in styled[0]["content"]
    assert "当前项目创作风格要求" in styled[0]["content"]
    assert "身份信息差" in styled[0]["content"]
    assert "当前项目创作风格要求" not in legacy[0]["content"]


def test_classic_compatibility_template_does_not_duplicate_legacy_rules():
    template = builtin_template("classic-compatible")
    snapshot = compose_prompt_snapshot(
        template_id="system:classic-compatible",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="beats",
        mode="single",
    )
    customized = compose_prompt_snapshot(
        template_id="system:classic-compatible",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="beats",
        mode="single",
        override={"custom_requirements": "本次突出道具伏笔。"},
    )

    assert snapshot["effective_instruction"] == ""
    assert beats_messages({"project": {"name": "测试"}}, 1, snapshot) == beats_messages(
        {"project": {"name": "测试"}}, 1
    )
    assert customized["effective_instruction"] == "1. 本次突出道具伏笔。"


def test_brief_template_styles_direction_without_replacing_blueprint_contract():
    template = builtin_template("barrage-revenge")
    snapshot = compose_prompt_snapshot(
        template_id="system:barrage-revenge",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="brief",
        mode="single",
    )

    messages = blueprint_messages({"project": {"name": "测试"}}, 3, snapshot)

    assert "返回 blueprints 数组" in messages[0]["content"]
    assert "当前项目创作风格要求" in messages[0]["content"]
    assert "女主的主动取证能力" in messages[0]["content"]


def test_brief_binding_is_project_scoped_and_has_no_episode_override(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.get_template_by_key("oriental-rule-mystery")

    binding = store.set_binding(
        project_id=project_id,
        stage="brief",
        episode_number=0,
        template_id=template["id"],
        template_revision=1,
        override={"custom_requirements": "控制场景成本。"},
        actor_user_id="owner-1",
    )
    snapshot = store.resolve_snapshot(
        project_id=project_id,
        stage="brief",
        mode="single",
        actor_user_id="owner-1",
    )

    assert binding["stage"] == "brief"
    assert snapshot["template_key"] == "oriental-rule-mystery"
    assert "控制场景成本" in snapshot["effective_instruction"]
    with pytest.raises(ValueError, match="Brief 模板不支持单集覆盖"):
        store.set_binding(
            project_id=project_id,
            stage="brief",
            episode_number=1,
            template_id=template["id"],
            template_revision=1,
            override={},
            actor_user_id="owner-1",
        )


def test_character_template_styles_people_without_replacing_bundle_contract():
    template = builtin_template("urban-identity-romcom")
    snapshot = compose_prompt_snapshot(
        template_id="system:urban-identity-romcom",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="characters",
        mode="single",
    )

    messages = character_messages(
        {"project": {"name": "测试"}, "locked_brief": {"title": "身份谜局"}},
        snapshot,
    )

    assert '{"characters": [...], "relationships": [...]}' in messages[0]["content"]
    assert "characters 与 relationships 都必须是 JSON 数组" in messages[0]["content"]
    assert "dialogue_style" in messages[0]["content"]
    assert "当前项目创作风格要求" in messages[0]["content"]
    assert "身份信息差" in messages[0]["content"]


def test_character_binding_is_project_scoped_and_has_no_episode_override(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.get_template_by_key("barrage-revenge")

    binding = store.set_binding(
        project_id=project_id,
        stage="characters",
        episode_number=0,
        template_id=template["id"],
        template_revision=1,
        override={"custom_requirements": "强化反派失败代价。"},
        actor_user_id="owner-1",
    )
    snapshot = store.resolve_snapshot(
        project_id=project_id,
        stage="characters",
        mode="single",
        actor_user_id="owner-1",
    )

    assert binding["stage"] == "characters"
    assert snapshot["template_key"] == "barrage-revenge"
    assert "强化反派失败代价" in snapshot["effective_instruction"]
    with pytest.raises(ValueError, match="人物模板不支持单集覆盖"):
        store.set_binding(
            project_id=project_id,
            stage="characters",
            episode_number=1,
            template_id=template["id"],
            template_revision=1,
            override={},
            actor_user_id="owner-1",
        )


def test_outline_template_styles_series_without_replacing_episode_contract():
    template = builtin_template("oriental-rule-mystery")
    snapshot = compose_prompt_snapshot(
        template_id="system:oriental-rule-mystery",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="outline",
        mode="series",
    )

    messages = outline_messages(
        {"project": {"name": "测试"}, "history_context": {}},
        1,
        10,
        snapshot,
    )

    assert "返回 episodes 数组" in messages[0]["content"]
    assert "estimated_seconds" in messages[0]["content"]
    assert "当前项目创作风格要求" in messages[0]["content"]
    assert "表任务、身世谜和规则阴谋" in messages[0]["content"]


def test_outline_binding_is_project_scoped_and_has_no_episode_override(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.get_template_by_key("urban-identity-romcom")

    binding = store.set_binding(
        project_id=project_id,
        stage="outline",
        episode_number=0,
        template_id=template["id"],
        template_revision=1,
        override={"custom_requirements": "第十集完成一次身份误判反转。"},
        actor_user_id="owner-1",
    )
    snapshot = store.resolve_snapshot(
        project_id=project_id,
        stage="outline",
        mode="series",
        actor_user_id="owner-1",
    )

    assert binding["stage"] == "outline"
    assert snapshot["template_key"] == "urban-identity-romcom"
    assert "第十集完成一次身份误判反转" in snapshot["effective_instruction"]
    with pytest.raises(ValueError, match="大纲模板不支持单集覆盖"):
        store.set_binding(
            project_id=project_id,
            stage="outline",
            episode_number=1,
            template_id=template["id"],
            template_revision=1,
            override={},
            actor_user_id="owner-1",
        )


def test_startup_adds_new_stage_bindings_without_losing_existing_rows(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.get_template_by_key("urban-identity-romcom")
    store.set_binding(
        project_id=project_id,
        stage="beats",
        episode_number=0,
        template_id=template["id"],
        template_revision=1,
        override={},
        actor_user_id="owner-1",
    )
    migrated = PromptStore(repository)

    assert migrated.binding(project_id, "beats", 0)["template_id"] == template["id"]
    assert migrated.binding(project_id, "brief", 0) is not None
    assert migrated.binding(project_id, "characters", 0) is not None
    assert migrated.binding(project_id, "outline", 0) is not None
    assert migrated.binding(project_id, "review", 0) is not None
    assert migrated.binding(project_id, "optimization", 0) is not None


def test_enabled_project_pins_only_new_character_stage_to_compatible_revision(
    prompt_store,
):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.get_template_by_key("generic-vertical-short")
    store.set_binding(
        project_id=project_id,
        stage="beats",
        episode_number=0,
        template_id=template["id"],
        template_revision=1,
        override={"custom_requirements": "保留既有 Beats 设置。"},
        actor_user_id="owner-1",
    )
    legacy_content = store.get_revision(template["id"], 1)["content"]
    legacy_content["stage_rules"].pop("characters")
    with repository._connect() as conn:
        conn.execute(
            "UPDATE creative_prompt_template_revisions "
            "SET content_json = ?, content_hash = ? "
            "WHERE template_id = ? AND revision = 1",
            (
                json.dumps(legacy_content, ensure_ascii=False),
                content_hash(legacy_content),
                template["id"],
            ),
        )

    migrated = PromptStore(repository)

    settings = migrated.project_settings(project_id)
    assert settings["default_template_revision"] == 1
    assert migrated.binding(project_id, "brief", 0)["template_revision"] == 1
    assert migrated.binding(project_id, "beats", 0)["template_revision"] == 1
    assert migrated.binding(project_id, "characters", 0)["template_revision"] == 2
    assert migrated.binding(project_id, "outline", 0)["template_revision"] == 1


def test_enabled_project_pins_only_new_outline_stage_to_compatible_revision(
    prompt_store,
):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.get_template_by_key("generic-vertical-short")
    store.set_binding(
        project_id=project_id,
        stage="characters",
        episode_number=0,
        template_id=template["id"],
        template_revision=1,
        override={"custom_requirements": "保留既有人物设置。"},
        actor_user_id="owner-1",
    )
    legacy_content = store.get_revision(template["id"], 1)["content"]
    legacy_content["stage_rules"].pop("outline")
    with repository._connect() as conn:
        conn.execute(
            "UPDATE creative_prompt_template_revisions "
            "SET content_json = ?, content_hash = ? "
            "WHERE template_id = ? AND revision = 1",
            (
                json.dumps(legacy_content, ensure_ascii=False),
                content_hash(legacy_content),
                template["id"],
            ),
        )

    migrated = PromptStore(repository)

    settings = migrated.project_settings(project_id)
    assert settings["default_template_revision"] == 1
    assert migrated.binding(project_id, "brief", 0)["template_revision"] == 1
    assert migrated.binding(project_id, "characters", 0)["template_revision"] == 1
    assert migrated.binding(project_id, "outline", 0)["template_revision"] == 2


@pytest.mark.parametrize("governance_status", ["pending_review", "deprecated", "archived"])
def test_startup_new_stage_never_binds_unapproved_or_retired_revision(
    tmp_path, governance_status
):
    repository = CreativeRepository(tmp_path / "legacy-governance.db")
    store = PromptStore(repository)
    project_id = _project(repository)
    template = store.create_template(
        title="旧版项目模板", description="启动补齐治理边界",
        content=builtin_template("generic-vertical-short"),
        owner_scope="project", owner_user_id="owner-1", project_id=project_id,
    )
    store.enable_project(
        project_id, actor_user_id="owner-1", template_key=template["template_key"]
    )
    store.set_binding(
        project_id=project_id, stage="beats", episode_number=0,
        template_id=template["id"], template_revision=1,
        override={"custom_requirements": "保留已绑定版本与覆盖。"}, actor_user_id="owner-1",
    )
    existing_binding = store.binding(project_id, "beats", 0)
    # Model a pre-review-stage database while keeping v2 creation on the real path.
    legacy_content = store.get_revision(template["id"], 1)["content"]
    legacy_content["stage_rules"].pop("review")
    with store._connect() as conn:
        conn.execute(
            "UPDATE creative_prompt_template_revisions SET content_json = ?, "
            "content_hash = ? WHERE template_id = ? AND revision = 1",
            (json.dumps(legacy_content), content_hash(legacy_content), template["id"]),
        )
    changed = builtin_template("generic-vertical-short")
    changed["shared_dna"] = "未经重新选择，不得自动执行这份新内容。"
    store.save_revision(
        template["id"], changed, expected_revision=1, actor_user_id="owner-1"
    )
    if governance_status != "pending_review":
        store.govern_project_template(
            project_id=project_id, template_id=template["id"], action="approve",
            note="审核通过", expected_governance_revision=1,
            operation_id="approve-legacy-v2", actor_user_id="owner-1",
        )
        with store._connect() as conn:
            conn.execute(
                "UPDATE creative_prompt_templates SET governance_status = ? WHERE id = ?",
                (governance_status, template["id"]),
            )

    restarted = PromptStore(repository)
    snapshot = restarted.resolve_snapshot(
        project_id=project_id, stage="review", mode="single", actor_user_id="owner-1"
    )

    assert snapshot["template_key"] == "generic-vertical-short"
    assert changed["shared_dna"] not in snapshot["effective_instruction"]
    assert restarted.binding(project_id, "beats", 0) == existing_binding
    assert restarted.project_settings(project_id)["default_template_revision"] == 1
    again = PromptStore(repository)
    assert again.binding(project_id, "review", 0) == restarted.binding(project_id, "review", 0)


def test_script_snapshot_rejects_cross_stage_content_by_construction():
    template = builtin_template("oriental-rule-mystery")
    snapshot = compose_prompt_snapshot(
        template_id="system:oriental-rule-mystery",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="script",
        mode="single",
    )

    messages = script_messages({"project": {}}, 2, snapshot)

    assert "返回 episode_title 和 scenes" in messages[0]["content"]
    assert "避免现代网感词" in messages[0]["content"]


def test_review_and_optimization_are_non_episode_template_stages(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    template = store.get_template_by_key("generic-vertical-short")

    review = store.set_binding(
        project_id=project_id,
        stage="review",
        episode_number=0,
        template_id=template["id"],
        template_revision=1,
        override={},
        actor_user_id="owner-1",
    )
    optimization = store.set_binding(
        project_id=project_id,
        stage="optimization",
        episode_number=0,
        template_id=template["id"],
        template_revision=1,
        override={},
        actor_user_id="owner-1",
    )

    assert review["episode_number"] == 0
    assert optimization["episode_number"] == 0
    with pytest.raises(ValueError, match="审核模板不支持单集覆盖"):
        store.set_binding(
            project_id=project_id,
            stage="review",
            episode_number=1,
            template_id=template["id"],
            template_revision=1,
            override={},
            actor_user_id="owner-1",
        )


def test_project_deletion_removes_prompt_bindings_and_project_templates(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    project_template = store.create_template(
        title="项目专用模板",
        description="删除项目时应一并删除",
        content=builtin_template("generic-vertical-short"),
        owner_scope="project",
        owner_user_id="owner-1",
        project_id=project_id,
    )
    store.set_binding(
        project_id=project_id,
        stage="beats",
        episode_number=0,
        template_id=project_template["id"],
        template_revision=1,
        override={},
        actor_user_id="owner-1",
    )
    store.govern_project_template(
        project_id=project_id,
        template_id=project_template["id"],
        action="request_review",
        note="删除项目前保留治理事件",
        expected_governance_revision=0,
        operation_id="project-deletion-governance-event",
        actor_user_id="owner-1",
    )

    repository.delete_project(project_id)

    assert repository.get_project(project_id) is None
    assert store.get_template(project_template["id"]) is None
    assert store.binding(project_id, "beats", 0) is None
    assert store.list_governance_events(project_id, project_template["id"]) == []


def test_writer_project_deletion_fingerprint_includes_prompt_state(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    before = repository.writer_project_deletion_fingerprint(project_id)

    store.set_binding(
        project_id=project_id,
        stage="beats",
        episode_number=0,
        template_id=store.get_template_by_key("urban-identity-romcom")["id"],
        template_revision=1,
        override={},
        actor_user_id="owner-1",
    )

    after = repository.writer_project_deletion_fingerprint(project_id)

    assert after != before


def test_project_default_rejects_another_users_private_template(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    personal = store.create_template(
        title="私人默认模板",
        description="不能被其他用户设为项目默认值",
        content=builtin_template("generic-vertical-short"),
        owner_scope="user",
        owner_user_id="owner-1",
    )

    with pytest.raises(PermissionError, match="无权使用该个人模板"):
        store.enable_project(
            project_id,
            actor_user_id="user-2",
            template_key=personal["template_key"],
        )


def test_new_and_existing_project_enablement_use_different_defaults(prompt_store):
    repository, store = prompt_store
    service = ProjectPromptService(repository)
    new_project = _project(repository, name="新项目")
    old_project = _project(repository, name="旧项目")

    service.enable_new_project(new_project, "owner-1")
    service.enable_existing_project(old_project, "owner-1")

    assert store.resolve_snapshot(
        project_id=new_project,
        stage="beats",
        mode="single",
    )["template_key"] == "generic-vertical-short"
    assert store.resolve_snapshot(
        project_id=old_project,
        stage="beats",
        mode="single",
    )["template_key"] == "classic-compatible"


def test_private_personal_template_cannot_be_selected_by_another_user(prompt_store):
    repository, store = prompt_store
    project_id = _project(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    personal = store.create_template(
        title="私人模板",
        description="不跨用户暴露",
        content=builtin_template("generic-vertical-short"),
        owner_scope="user",
        owner_user_id="owner-1",
    )

    with pytest.raises(PermissionError, match="无权使用该个人模板"):
        store.resolve_snapshot(
            project_id=project_id,
            stage="beats",
            mode="single",
            run_template_key=personal["template_key"],
            actor_user_id="user-2",
        )


def test_prompt_override_rejects_unknown_or_oversized_fields():
    template = builtin_template("generic-vertical-short")
    common = {
        "template_id": "system:generic-vertical-short",
        "template_key": template["key"],
        "title": template["title"],
        "revision": 1,
        "content": template,
        "stage": "beats",
        "mode": "single",
    }

    with pytest.raises(ValueError, match="不支持的模板覆盖字段"):
        compose_prompt_snapshot(**common, override={"system_prompt": "越权覆盖"})
    with pytest.raises(ValueError, match="不能超过 4000"):
        compose_prompt_snapshot(
            **common,
            override={"custom_requirements": "x" * 4001},
        )


def test_included_examples_and_parameter_overrides_reach_effective_instruction():
    template = builtin_template("generic-vertical-short")
    template["examples"] = [
        {
            "label": "节拍力度",
            "text": "误会必须引发一次可见的关系变化。",
            "include_in_context": True,
        }
    ]

    snapshot = compose_prompt_snapshot(
        template_id="system:generic-vertical-short",
        template_key=template["key"],
        title=template["title"],
        revision=1,
        content=template,
        stage="beats",
        mode="single",
        override={"parameters": {"max_beats": 4}},
    )

    assert '"max_beats":4' in snapshot["effective_instruction"]
    assert "模板示例（节拍力度）" in snapshot["effective_instruction"]
    assert "误会必须引发一次可见的关系变化" in snapshot["effective_instruction"]


@pytest.mark.parametrize(
    ("stage", "binding_kind"),
    [
        ("script", "default"),
        ("script", "stage"),
        ("script", "episode"),
        ("review", "default"),
        ("review", "stage"),
    ],
)
def test_bound_private_template_still_requires_owner(prompt_store, binding_kind, stage):
    # A binding must not turn a private template into shared project content.
    repository, store = prompt_store
    project_id = _project(repository)
    personal = store.create_template(
        title="私人内容",
        description="",
        content=builtin_template("generic-vertical-short"),
        owner_scope="user",
        owner_user_id="owner-1",
    )
    store.enable_project(
        project_id,
        actor_user_id="owner-1",
        template_key=(
            personal["template_key"]
            if binding_kind == "default"
            else "generic-vertical-short"
        ),
    )
    if binding_kind != "default":
        store.set_binding(
            project_id=project_id,
            stage=stage,
            episode_number=1 if binding_kind == "episode" else 0,
            template_id=personal["id"],
            template_revision=1,
            override={},
            actor_user_id="owner-1",
        )
    assert (
        store.resolve_snapshot(
            project_id=project_id,
            stage=stage,
            mode="single",
            episode_number=1 if stage == "script" else 0,
            actor_user_id="owner-1",
        )["template_id"]
        == personal["id"]
    )
    with pytest.raises(PermissionError, match="个人模板"):
        store.resolve_snapshot(
            project_id=project_id,
            stage=stage,
            mode="single",
            episode_number=1 if stage == "script" else 0,
            actor_user_id="other-member",
        )


def test_repeated_startup_preserves_custom_revisions_and_bindings(tmp_path):
    repository = CreativeRepository(tmp_path / "legacy-startup.db")
    project_id = _project(repository)
    store = PromptStore(repository)
    store.enable_project(project_id, actor_user_id="owner-1")
    for scope, stage, episode in [("user", "script", 1), ("project", "beats", 0)]:
        template = store.create_template(
            title=f"保留 {scope}",
            description="原始数据",
            content=builtin_template("generic-vertical-short"),
            owner_scope=scope,
            owner_user_id="owner-1",
            project_id=project_id if scope == "project" else None,
        )
        store.set_binding(
            project_id=project_id,
            stage=stage,
            episode_number=episode,
            template_id=template["id"],
            template_revision=1,
            override={"custom_requirements": "保留绑定"},
            actor_user_id="owner-1",
        )
        content = builtin_template("urban-identity-romcom")
        store.save_revision(
            template["id"], content, expected_revision=1, actor_user_id="owner-1"
        )
    expected_titles = {"保留 user", "保留 project"}
    before_catalog = {
        item["id"]: item
        for item in store.list_catalog(
            project_id=project_id, actor_user_id="owner-1"
        )
        if item["title"] in expected_titles
    }
    before_bindings = {
        (stage, episode): store.binding(project_id, stage, episode)
        for stage, episode in [("script", 1), ("beats", 0)]
    }
    for _ in range(2):
        store = PromptStore(repository)
        after_catalog = {
            item["id"]: item
            for item in store.list_catalog(
                project_id=project_id, actor_user_id="owner-1"
            )
            if item["title"] in expected_titles
        }
        after_bindings = {
            (stage, episode): store.binding(project_id, stage, episode)
            for stage, episode in [("script", 1), ("beats", 0)]
        }
        assert after_catalog == before_catalog
        assert after_bindings == before_bindings
