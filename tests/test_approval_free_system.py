# -*- coding: utf-8 -*-
from pathlib import Path

from fastapi import FastAPI

from creative.api import build_creative_router
from creative.repository import CreativeRepository
from identity.repository import IdentityRepository
from workflow.api import build_workflow_router
from workflow.repository import WorkflowRepository


ROOT = Path(__file__).resolve().parents[1]


def test_workflow_router_exposes_no_approval_operations(tmp_path: Path):
    identity = IdentityRepository(tmp_path / ".runtime/system/system.db")
    identity.seed_defaults()
    workflow = WorkflowRepository(identity)
    app = FastAPI()
    app.include_router(build_workflow_router(workflow))

    paths = {route.path for route in app.routes}
    assert "/api/workflow/approval-flow" not in paths
    assert not any(path.endswith("/approve") for path in paths)
    assert not any(path.endswith("/return") for path in paths)


def test_operational_frontend_contains_no_approval_actions_or_copy():
    roots = (ROOT / "templates", ROOT / "static/js")
    forbidden = (
        "审批", "审核", "待审", "已批准", "已退回", "提交审批",
        "/approve", "/return", "can_approve", "can_return", "waiting_review",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".html", ".js"} or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert offenders == []


def test_default_permission_catalog_has_no_approval_permissions(tmp_path: Path):
    identity = IdentityRepository(tmp_path / "system.db")
    identity.seed_defaults()
    with identity.connect() as connection:
        codes = {row[0] for row in connection.execute("SELECT code FROM permissions")}
    assert "version.approve" not in codes
    assert "version.return" not in codes


def test_creative_lock_routes_are_available_without_approval_router(tmp_path: Path):
    runtime = tmp_path / ".runtime"
    CreativeRepository(runtime / "creative/creative.db")
    app = FastAPI()
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))
    paths = {route.path for route in app.routes}
    assert "/api/creative/breakdowns/{breakdown_id}/lock" in paths
    assert "/api/creative/blueprints/{blueprint_id}/lock-brief" in paths
    assert "/api/creative/projects/{project_id}/characters/{batch_id}/lock" in paths
    assert "/api/creative/projects/{project_id}/outlines/{batch_id}/lock" in paths
    assert "/api/creative/episodes/{episode_id}/lock" in paths
