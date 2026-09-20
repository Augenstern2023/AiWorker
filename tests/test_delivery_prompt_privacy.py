# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Delivery downloads project frozen private prompts without rewriting archives."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creative.delivery import DeliveryService, build_delivery_router
from creative.free_mode_service import FreeCreativeModeService
from creative.prompt_registry import builtin_template
from creative.prompt_store import PromptStore
from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.schema import Principal


@pytest.fixture(autouse=True)
def _use_postgres_creative(monkeypatch, postgres_creative):
    monkeypatch.setitem(
        globals(), "CreativeRepository", lambda *_args, **_kwargs: postgres_creative
    )


SECRET = "PRIVATE_DELIVERY_INSTRUCTION_SENTINEL"


def _actor(user_id):
    return Principal(
        user_id=user_id, username=user_id, display_name=user_id,
        role_codes=("system_admin",), permissions=frozenset({"*"}),
        data_scope="all", session_id=f"session-{user_id}",
    )


@pytest.fixture
def delivery_environment(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = repository.create_original_project(
        seed_text="交付隐私测试", name="归档作品", target_episode_count=1,
        created_by="alice", creation_request_id="private-delivery", workflow_mode="free",
    )
    repository.mark_project_bootstrap_status(project["id"], "ready")
    store = PromptStore(repository)
    store.enable_project(project["id"], actor_user_id="alice")
    template = store.create_template(
        title="Alice 私人模板", description="", content=builtin_template("generic-vertical-short"),
        owner_scope="user", owner_user_id="alice",
    )
    snapshot = store.resolve_snapshot(
        project_id=project["id"], stage="brief", mode="single",
        run_template_key=template["template_key"], actor_user_id="alice",
        run_override={"custom_requirements": SECRET},
    )
    brief = FreeCreativeModeService(repository).create_brief(
        project["id"], {"title": "归档方向"}, expected_head_revision=0,
        actor_user_id="alice", generation_meta={"prompt_snapshot": snapshot},
    )["resource"]

    class ExistingAudit:
        # The audit database is external; creation reconciliation still persists
        # its real local timestamp, and subsequent downloads must not change it.
        def query(self, **kwargs):
            return [{"id": "existing-delivery-audit"}]

    service = DeliveryService(
        root=tmp_path, creative_repository=repository,
        workflow_repository=object(), audit_service=ExistingAudit(),
    )
    actor = [_actor("bob")]
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: actor[0]
    app.include_router(build_delivery_router(service))
    return repository, project, brief, snapshot, service, actor, TestClient(app)


def _create_delivery(environment, format, *, legacy=False, actor_id="alice"):
    repository, project, brief, _, service, _, _ = environment
    delivery = service.create(
        project_id=project["id"], stage="brief", resource_id=str(brief["id"]),
        format=format, actor=_actor(actor_id),
    )
    if legacy:
        with repository._connect() as conn:
            conn.execute(
                "UPDATE creative_deliveries SET projection_source_json = '{}' WHERE id = ?",
                (delivery["id"],),
            )
    return delivery


@pytest.mark.parametrize("format", ["json", "md"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("actor_id", ["alice", "bob"])
def test_delivery_download_redacts_private_prompts_and_keeps_archive(
    delivery_environment, format, legacy, actor_id
):
    repository, _, _, snapshot, service, actor, client = delivery_environment
    delivery = _create_delivery(delivery_environment, format, legacy=legacy)
    path = Path(delivery["path"])
    original = path.read_bytes()
    assert SECRET.encode() in original
    with repository._connect() as conn:
        before = dict(conn.execute("SELECT * FROM creative_deliveries WHERE id = ?", (delivery["id"],)).fetchone())
    actor[0] = _actor(actor_id)

    response = client.get(f"/api/admin/deliveries/{delivery['id']}/download")

    assert response.status_code == 200, response.text
    assert (SECRET in response.text) == (actor_id == "alice")
    assert "归档方向" in response.text
    assert snapshot["content_hash"] in response.text
    assert path.read_bytes() == original
    assert hashlib.sha256(original).hexdigest() == delivery["fingerprint"]
    if actor_id == "alice":
        assert response.content == original
    assert SECRET not in json.dumps(delivery)
    assert SECRET not in json.dumps(service.list())
    repeated = _create_delivery(delivery_environment, format, actor_id="bob")
    assert repeated["id"] == delivery["id"]
    assert repeated["fingerprint"] == delivery["fingerprint"]
    with repository._connect() as conn:
        after = dict(conn.execute("SELECT * FROM creative_deliveries WHERE id = ?", (delivery["id"],)).fetchone())
    assert after == before


@pytest.mark.parametrize("format", ["json", "md"])
def test_new_delivery_projection_uses_frozen_source_after_resource_changes(
    delivery_environment, format
):
    repository, _, brief, _, _, actor, client = delivery_environment
    delivery = _create_delivery(delivery_environment, format)
    original = Path(delivery["path"]).read_bytes()
    with repository._connect() as conn:
        conn.execute(
            "UPDATE creative_blueprints SET content_json = ? WHERE id = ?",
            (json.dumps({"title": "后来修改的方向"}), brief["id"]),
        )
    response = client.get(f"/api/admin/deliveries/{delivery['id']}/download")
    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    assert "归档方向" in response.text
    assert "后来修改的方向" not in response.text
    actor[0] = _actor("alice")
    assert client.get(f"/api/admin/deliveries/{delivery['id']}/download").content == original


def test_legacy_markdown_without_reproducible_source_fails_closed(delivery_environment):
    repository, _, brief, _, _, _, client = delivery_environment
    delivery = _create_delivery(delivery_environment, "md", legacy=True)
    original = Path(delivery["path"]).read_bytes()
    with repository._connect() as conn:
        conn.execute(
            "UPDATE creative_blueprints SET content_json = ? WHERE id = ?",
            (json.dumps({"title": "已无法还原旧归档"}), brief["id"]),
        )
    response = client.get(f"/api/admin/deliveries/{delivery['id']}/download")
    assert response.status_code == 409
    assert SECRET not in response.text
    assert Path(delivery["path"]).read_bytes() == original


def test_legacy_json_projection_uses_archive_when_resource_changes(delivery_environment):
    repository, _, brief, _, _, _, client = delivery_environment
    delivery = _create_delivery(delivery_environment, "json", legacy=True)
    with repository._connect() as conn:
        conn.execute(
            "UPDATE creative_blueprints SET generation_meta_json = '{}' WHERE id = ?",
            (brief["id"],),
        )
    response = client.get(f"/api/admin/deliveries/{delivery['id']}/download")
    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    assert "归档方向" in response.text


def test_legacy_markdown_missing_resource_returns_conflict(delivery_environment):
    repository, _, brief, _, _, _, client = delivery_environment
    delivery = _create_delivery(delivery_environment, "md", legacy=True)
    original = Path(delivery["path"]).read_bytes()
    with repository._connect() as conn:
        conn.execute("DELETE FROM creative_blueprints WHERE id = ?", (brief["id"],))
    response = client.get(f"/api/admin/deliveries/{delivery['id']}/download")
    assert response.status_code == 409
    assert SECRET not in response.text
    assert Path(delivery["path"]).read_bytes() == original
