# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import pytest

from creative.prompt_marketplace import PromptMarketplaceService
from creative.prompt_registry import builtin_template
from creative.prompt_store import PromptStore
from creative.repository import CreativeRepository


@pytest.fixture(autouse=True)
def _use_postgres_creative(monkeypatch, postgres_creative):
    monkeypatch.setitem(
        globals(), "CreativeRepository", lambda *_args, **_kwargs: postgres_creative
    )


def _project(
    repository: CreativeRepository, *, name: str, actor: str, request_id: str
) -> dict:
    return repository.create_original_project(
        seed_text="一个用于验证模板市场的短剧项目",
        name=name,
        target_episode_count=3,
        target_platform="抖音",
        genre="短剧",
        target_audience="大众",
        constraints={},
        reference_refs=[],
        created_by=actor,
        creation_request_id=request_id,
        workflow_mode="free",
    )


def _submission(
    service: PromptMarketplaceService,
    *,
    project_id: int,
    template_id: str,
    actor: str = "author-1",
    operation_id: str = "publish-v1",
) -> dict:
    return service.submit(
        project_id=project_id,
        template_id=template_id,
        expected_template_revision=1,
        operation_id=operation_id,
        title="社区节奏模板",
        description="适合竖屏短剧的节奏模板",
        tags=["竖屏", "强钩子", "竖屏"],
        attribution_name="编剧甲",
        source_type="original",
        source_url="",
        rights_statement="本人原创并有权许可其他项目安装使用。",
        publication_confirmed=True,
        rights_confirmed=True,
        actor_user_id=actor,
    )


def test_marketplace_moderates_frozen_personal_template_and_installs_copy(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    source_project = _project(
        repository,
        name="发布项目",
        actor="author-1",
        request_id="market-source",
    )
    target_project = _project(
        repository,
        name="安装项目",
        actor="manager-2",
        request_id="market-target",
    )
    store = PromptStore(repository)
    personal = store.create_template(
        title="我的节奏模板",
        description="发布前仍是私人模板",
        content=builtin_template("generic-vertical-short"),
        owner_scope="user",
        owner_user_id="author-1",
    )
    service = PromptMarketplaceService(repository, store)

    submission = _submission(
        service,
        project_id=source_project["id"],
        template_id=personal["id"],
    )
    assert submission["status"] == "pending"
    assert submission["license_code"] == "INTERNAL"
    assert submission["tags"] == ["竖屏", "强钩子"]
    assert submission["compatibility"]["schema_version"] == "prompt-library-v1"

    with pytest.raises(PermissionError, match="系统管理员"):
        service.moderate(
            submission_id=submission["id"],
            action="approve",
            note="越权批准",
            expected_review_revision=0,
            operation_id="bad-review",
            actor_user_id="author-1",
            is_admin=False,
        )

    approved = service.moderate(
        submission_id=submission["id"],
        action="approve",
        note="来源、许可和内容结构已核验",
        expected_review_revision=0,
        operation_id="approve-v1",
        actor_user_id="admin-1",
        is_admin=True,
    )
    assert approved["status"] == "approved"

    changed = builtin_template("generic-vertical-short")
    changed["shared_dna"] += " 来源模板后续版本的额外规则。"
    store.save_revision(
        personal["id"],
        changed,
        expected_revision=1,
        actor_user_id="author-1",
    )

    catalog = service.marketplace(
        project_id=target_project["id"],
        actor_user_id="manager-2",
        stage="script",
    )
    assert {item["template_key"] for item in catalog["official_items"]} == {
        "generic-vertical-short",
        "urban-identity-romcom",
        "barrage-revenge",
        "oriental-rule-mystery",
    }
    assert all(
        item["owner_scope"] == "system"
        and item["marketplace_kind"] == "official"
        for item in catalog["official_items"]
    )
    assert [item["id"] for item in catalog["items"]] == [submission["id"]]
    assert catalog["items"][0]["install_count"] == 0
    assert catalog["items"][0]["content"] == submission["content"]

    installed = service.install(
        project_id=target_project["id"],
        submission_id=submission["id"],
        operation_id="install-v1-target",
        actor_user_id="manager-2",
    )
    assert installed["bound"] is False
    assert installed["template"]["owner_scope"] == "project"
    assert installed["template"]["content"] == submission["content"]
    assert installed["template"]["content"] != changed
    assert installed["installation"]["source_content_hash"] == submission[
        "template_content_hash"
    ]
    assert installed["installation"]["license_code"] == "INTERNAL"
    store.enable_project(
        target_project["id"],
        actor_user_id="manager-2",
        template_key=installed["template"]["template_key"],
    )
    for stage in (
        "brief",
        "characters",
        "outline",
        "beats",
        "script",
        "review",
        "optimization",
    ):
        for mode in ("single", "series", "rewrite", "repair"):
            snapshot = store.resolve_snapshot(
                project_id=target_project["id"],
                stage=stage,
                mode=mode,
                actor_user_id="manager-2",
            )
            assert snapshot["template_key"] == installed["template"]["template_key"]
            assert snapshot["stage"] == stage
            assert snapshot["mode"] == mode

    withdrawn = service.moderate(
        submission_id=submission["id"],
        action="withdraw",
        note="作者停止后续安装",
        expected_review_revision=1,
        operation_id="withdraw-v1",
        actor_user_id="author-1",
        is_admin=False,
    )
    assert withdrawn["status"] == "withdrawn"
    assert service.marketplace(
        project_id=target_project["id"], actor_user_id="manager-2"
    )["items"] == []
    assert store.get_template(installed["template"]["id"]) is not None


def test_marketplace_requires_rights_and_uses_installed_one_user_rating(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    source_project = _project(
        repository,
        name="评分发布项目",
        actor="author-1",
        request_id="rating-source",
    )
    target_project = _project(
        repository,
        name="评分安装项目",
        actor="manager-2",
        request_id="rating-target",
    )
    other_project = _project(
        repository,
        name="未安装项目",
        actor="viewer-3",
        request_id="rating-other",
    )
    store = PromptStore(repository)
    personal = store.create_template(
        title="评分模板",
        description="",
        content=builtin_template("urban-identity-romcom"),
        owner_scope="user",
        owner_user_id="author-1",
    )
    service = PromptMarketplaceService(repository, store)

    with pytest.raises(ValueError, match="确认模板内容会在公司内部展示"):
        service.submit(
            project_id=source_project["id"],
            template_id=personal["id"],
            expected_template_revision=1,
            operation_id="missing-confirmation",
            title="评分模板",
            description="",
            tags=[],
            attribution_name="编剧甲",
            source_type="original",
            source_url="",
            rights_statement="本人原创并保留完整发布权利。",
            publication_confirmed=False,
            rights_confirmed=True,
            actor_user_id="author-1",
        )

    submission = _submission(
        service,
        project_id=source_project["id"],
        template_id=personal["id"],
        operation_id="rating-publish",
    )
    service.moderate(
        submission_id=submission["id"],
        action="approve",
        note="批准评分测试模板",
        expected_review_revision=0,
        operation_id="rating-approve",
        actor_user_id="admin-1",
        is_admin=True,
    )
    installation = service.install(
        project_id=target_project["id"],
        submission_id=submission["id"],
        operation_id="rating-install",
        actor_user_id="manager-2",
    )

    with pytest.raises(PermissionError, match="自己的模板"):
        service.rate(
            project_id=target_project["id"],
            submission_id=submission["id"],
            score=5,
            review_text="作者自评",
            expected_revision=0,
            actor_user_id="author-1",
        )
    with pytest.raises(PermissionError, match="已安装"):
        service.rate(
            project_id=other_project["id"],
            submission_id=submission["id"],
            score=5,
            review_text="没有安装",
            expected_revision=0,
            actor_user_id="viewer-3",
        )

    rated = service.rate(
        project_id=target_project["id"],
        submission_id=submission["id"],
        score=5,
        review_text="实际安装后节奏约束清晰",
        expected_revision=0,
        actor_user_id="viewer-3",
    )
    assert rated["rating"]["revision"] == 1
    assert rated["summary"]["average"] == 5
    with pytest.raises(ValueError, match="评分版本已变化"):
        service.rate(
            project_id=target_project["id"],
            submission_id=submission["id"],
            score=1,
            review_text="用旧版本覆盖",
            expected_revision=0,
            actor_user_id="viewer-3",
        )
    updated = service.rate(
        project_id=target_project["id"],
        submission_id=submission["id"],
        score=4,
        review_text="长期使用后调整为四分",
        expected_revision=1,
        actor_user_id="viewer-3",
    )
    assert updated["rating"]["revision"] == 2
    assert updated["summary"]["count"] == 1
    assert updated["summary"]["average"] == 4

    repository.delete_project(target_project["id"])
    with service._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM creative_prompt_market_installations"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM creative_prompt_market_ratings"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM creative_prompt_market_submissions WHERE id = ?",
            (submission["id"],),
        ).fetchone()["status"] == "approved"
    assert installation["template"]["id"]


def test_marketplace_rejects_non_original_without_verifiable_source(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    source_project = _project(
        repository,
        name="许可项目",
        actor="author-1",
        request_id="license-source",
    )
    store = PromptStore(repository)
    personal = store.create_template(
        title="授权改编模板",
        description="",
        content=builtin_template("generic-vertical-short"),
        owner_scope="user",
        owner_user_id="author-1",
    )
    service = PromptMarketplaceService(repository, store)

    with pytest.raises(ValueError, match="来源链接"):
        service.submit(
            project_id=source_project["id"],
            template_id=personal["id"],
            expected_template_revision=1,
            operation_id="unverifiable-source",
            title="授权改编模板",
            description="",
            tags=[],
            attribution_name="编剧甲",
            source_type="authorized",
            source_url="",
            rights_statement="已获得原作者明确书面发布授权。",
            publication_confirmed=True,
            rights_confirmed=True,
            actor_user_id="author-1",
        )
