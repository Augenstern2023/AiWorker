# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Private frozen prompts remain private at historical and public boundaries."""

import json
import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from creative.prompt_registry import builtin_template
from creative.free_mode_service import FreeCreativeModeService
from creative.prompt_store import PromptStore
from creative.project_prompt_service import ProjectPromptService
from creative.repository import CreativeRepository
from creative.schema import CreativeItemUpdateRequest
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.dependencies import current_principal
from identity.schema import Principal
from screenplay_optimization.api import build_script_optimization_router
from tests.test_prompt_library_api import (
    _endpoint, _matrix_principal, _principal, _request, _router, permission_context,
)
from tests.test_script_optimizations import _project_with_review
from tests.test_script_review_api import MembershipPermissionService


SECRET = "ALICE_PRIVATE_INSTRUCTION_SENTINEL"
pytestmark = pytest.mark.usefixtures("isolated_creative_rank_source")


def _private_snapshot(repository, project_id, owner="alice"):
    store = PromptStore(repository.db_path)
    store.enable_project(project_id, actor_user_id=owner)
    template = store.create_template(
        title="私人模板", description="", content=builtin_template("generic-vertical-short"),
        owner_scope="user", owner_user_id=owner,
    )
    return store.resolve_snapshot(
        project_id=project_id, stage="optimization", mode="single",
        run_template_key=template["template_key"],
        run_override={"custom_requirements": SECRET}, actor_user_id=owner,
    )


@pytest.mark.parametrize("operation", ["detail", "list", "retry"])
@pytest.mark.parametrize("history", ["current", "legacy"])
def test_private_optimization_history_is_owner_only(tmp_path, operation, history):
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    snapshot = _private_snapshot(repository, project["id"])
    if history == "legacy":
        snapshot.pop("owner_scope")
        snapshot.pop("owner_user_id")
    task = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"], selected_findings=[],
        target_episode_numbers=[1], created_by="alice", prompt_snapshot=snapshot,
    )
    repository.set_script_optimization_job(task["id"], "original")
    repository.fail_script_optimization(task["id"], "retryable")
    actor = [_principal("bob", permissions={"script.optimize.read", "script.optimize.generate"})]
    app = FastAPI()
    app.state.identity_permission_service = MembershipPermissionService(
        {repository.workflow_project_id(project["id"])}
    )
    app.dependency_overrides[current_principal] = lambda: actor[0]
    app.include_router(build_script_optimization_router(
        repository, SimpleNamespace(retry=lambda _: SimpleNamespace(job_id="resumed")),
    ))
    client = TestClient(app)
    path = (f"/api/script-reviews/{review['id']}/optimizations" if operation == "list"
            else f"/api/script-optimizations/{task['id']}" + ("/retry" if operation == "retry" else ""))
    invoke = client.post if operation == "retry" else client.get
    denied = invoke(path)
    assert SECRET not in denied.text
    assert denied.status_code == (200 if operation == "list" else 403)
    if operation == "list":
        assert denied.json()["items"] == []
    assert repository.get_script_optimization(task["id"])["status"] == "failed"
    actor[0] = replace(actor[0], user_id="alice")
    owned = invoke(path)
    assert owned.status_code == 200, owned.text
    assert SECRET in owned.text


def test_optimization_creation_checks_source_review_private_prompt(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    source = dict(review["source_payload"], prompt_snapshot=_private_snapshot(repository, project["id"]))
    with repository._connect() as conn:
        conn.execute("UPDATE script_reviews SET source_payload_json = ? WHERE id = ?",
                     (json.dumps(source), review["id"]))
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: _principal("bob", admin=True)
    app.include_router(build_script_optimization_router(repository, object()))
    response = TestClient(app).post(
        f"/api/script-reviews/{review['id']}/optimizations",
        json={"mode": "direct", "findings": [{"ref": "core:0", "episode_numbers": [1]}]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["project", "resource", "download", "update-resource"])
@pytest.mark.parametrize("actor_id", ["member", "manager", "admin"])
async def test_public_project_resources_project_private_snapshots(permission_context, tmp_path, boundary, actor_id):
    repository, project, _, app = permission_context
    snapshot = _private_snapshot(repository, project["id"], owner="manager")
    resource = FreeCreativeModeService(repository).create_brief(
        project["id"], {"title": "公开方向"}, expected_head_revision=0,
        actor_user_id="manager", generation_meta={"prompt_snapshot": snapshot},
    )["resource"]
    router, workflow, application = _router(repository, tmp_path)
    workflow.list_project_members.return_value = [
        {"user_id": user, "status": "active"} for user in ("member", "manager")
    ]
    application.workflow_adapter.snapshot.side_effect = CreativeWorkflowAdapter(repository, workflow).snapshot
    args = dict(project_id=project["id"], request=_request(app, "GET"), principal=_matrix_principal(actor_id))
    path = "/api/creative/projects/{project_id}"
    if boundary != "project":
        path += "/generated-content"
        args.update(stage="brief", resource_id=str(resource["id"]))
    if boundary == "download":
        path += "/download"
        args["format"] = "json"
    if boundary == "update-resource":
        path = "/api/creative/projects/{project_id}/free/items/{item_type}/{item_id}"
        args = dict(project_id=project["id"], item_type="brief", item_id=resource["id"],
                    principal=_matrix_principal(actor_id),
                    payload=CreativeItemUpdateRequest(content={"title": "公开更新"}))
    result = await _endpoint(router, path, "PATCH" if boundary == "update-resource" else "GET")(**args)
    payload = json.loads(result.body) if boundary == "download" else result
    serialized = json.dumps(payload)
    assert (SECRET in serialized) == (actor_id == "manager")
    assert snapshot["content_hash"] in serialized
    assert repository.get_project(project["id"])["blueprints"][0]["generation_meta"]["prompt_snapshot"] == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["list_jobs", "get_job"])
@pytest.mark.parametrize("actor_id", ["alice", "bob", "admin"])
async def test_job_public_projections_keep_private_prompt_identity_only(tmp_path, boundary, actor_id):
    repository = CreativeRepository(tmp_path / "creative.db")
    project, _, _ = _project_with_review(repository)
    snapshot = _private_snapshot(repository, project["id"])
    job = {"job_id": "private-job", "created_by_user_id": "alice", "project_id": project["id"],
           "job": {"extra": {"prompt_snapshot": snapshot}}}
    # Execute the actual WebGUI functions without importing its process-wide DB/server startup.
    names = {"_workflow_project_key", "_can_read_job", "_assert_can_read_job", "_public_job", "list_jobs", "get_job"}
    tree = ast.parse((Path(__file__).parents[1] / "WebGUI.py").read_text(encoding="utf-8-sig"))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in selected:
        node.decorator_list = []
    scope = dict(Any=Any, Principal=Principal, Request=Request, Depends=Depends,
                 current_principal=current_principal, HTTPException=HTTPException,
                 ProjectPromptService=ProjectPromptService, creative_repository=repository,
                 _operation_permission_granted=lambda *_: True,
                 workflow_repository=SimpleNamespace(list_project_members=lambda _: [
                     {"user_id": user, "status": "active"} for user in ("alice", "bob")]),
                 job_manager=SimpleNamespace(list_jobs=lambda: [job], get_job=lambda _: job))
    exec(compile(ast.Module(body=selected, type_ignores=[]), "WebGUI.py", "exec"), scope)
    kwargs = dict(principal=_principal(actor_id, admin=actor_id == "admin"), request=None)
    if boundary == "get_job":
        kwargs["job_id"] = "private-job"
    result = await scope[boundary](**kwargs)
    serialized = json.dumps(result)
    assert (SECRET in serialized) == (actor_id == "alice")
    assert snapshot["content_hash"] in serialized
    assert job["job"]["extra"]["prompt_snapshot"] == snapshot


@pytest.mark.parametrize("actor_id", ["manager", "member"])
def test_creative_projection_keeps_fastapi_dependency_and_response_contracts(permission_context, actor_id):
    repository, project, router, _ = permission_context
    snapshot = _private_snapshot(repository, project["id"], owner="manager")
    resource = FreeCreativeModeService(repository).create_brief(
        project["id"], {"title": "公开方向"}, expected_head_revision=0,
        actor_user_id="manager", generation_meta={"prompt_snapshot": snapshot},
    )["resource"]
    app = FastAPI()
    # Permission matrices are covered separately; this runs FastAPI's actual
    # signature/dependency resolution and JSON rendering around the real route.
    for dependency in router.dependencies:
        app.dependency_overrides[dependency.dependency] = lambda: None
    app.dependency_overrides[current_principal] = lambda: _matrix_principal(actor_id)
    app.include_router(router)
    response = TestClient(app).patch(
        f"/api/creative/projects/{project['id']}/free/items/brief/{resource['id']}",
        json={"content": {"title": "HTTP 更新"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"] == {"title": "HTTP 更新"}
    assert (SECRET in response.text) == (actor_id == "manager")
