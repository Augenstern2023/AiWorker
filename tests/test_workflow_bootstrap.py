# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audit.service import AuditService
from creative.repository import CreativeRepository
from creative.workflow_bootstrap import WorkflowBootstrapService
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.permission_service import PermissionService
from identity.dependencies import current_principal
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from workflow.repository import WorkflowRepository
from workflow.api import build_workflow_router
from workflow.transition_service import TransitionService


def _principal(identity: IdentityRepository, username: str, role_code: str):
    role = identity.get_role_by_code(role_code)
    assert role is not None
    user = identity.create_user(username, username, "hash")
    identity.replace_user_roles(user["id"], [role["id"]])
    principal = PermissionService(identity, SessionService(identity)).principal_for_user(
        user["id"], f"session:{username}"
    )
    assert principal is not None
    return principal


def _successor_count(identity: IdentityRepository, task_id: str) -> int:
    with identity.connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM workflow_tasks "
            "WHERE predecessor_task_id = ? AND task_type = 'source_package'",
            (task_id,),
        ).fetchone()
    return int(row["count"])


def test_topic_decision_approval_adopts_after_pending_bootstrap(tmp_path: Path):
    identity = IdentityRepository(tmp_path / "system.db")
    identity.seed_defaults()
    creative = CreativeRepository(tmp_path / "creative.db")
    workflow = WorkflowRepository(identity)
    service = WorkflowBootstrapService(creative, workflow)
    owner = _principal(identity, "owner", "project_owner")
    selector = _principal(identity, "selector", "selection_operator")
    topic = creative.upsert_topics(
        [{
            "platform": "dy",
            "job_id": "bootstrap-job",
            "source_path": "bootstrap.json",
            "captured_at": "2026-07-22",
            "title": "Bootstrap",
            "score": 90,
            "confidence": "high",
            "score_details": {},
            "recommendation": "adopt",
            "evidence": [],
        }]
    )[0]

    with pytest.raises(PermissionError):
        service.ensure_topic_workflow(topic["id"], selector)
    assert creative.get_topic(topic["id"])["status"] == "pending_review"

    first = service.ensure_topic_workflow(topic["id"], owner)
    repeated = service.ensure_topic_workflow(topic["id"], owner)

    assert creative.get_topic(topic["id"])["status"] == "pending_review"
    assert first["task"] == repeated["task"]
    assert first["task"]["task_type"] == "topic_decision"
    assert first["task"]["role_code"] == "selection_operator"
    assert first["task"]["status"] == "pool"
    workflow.claim_task(first["task"]["id"], selector)
    workflow.start_task(first["task"]["id"], selector)
    transitions = TransitionService(
        workflow, creative_adapter=CreativeWorkflowAdapter(creative, workflow)
    )
    draft = transitions.create_submission(
        first["task"]["id"],
        "topic_decision",
        str(topic["id"]),
        {"decision": "adopt"},
        selector,
    )
    submitted = transitions.submit(draft["id"], selector)
    returned = transitions.return_submission(
        submitted["id"], owner, "needs stronger evidence"
    )
    assert creative.get_topic(topic["id"])["status"] == "pending_review"
    workflow.start_task(first["task"]["id"], selector)
    resubmitted = transitions.submit(returned["draft"]["id"], selector)
    transitions.approve(resubmitted["id"], owner)

    assert creative.get_topic(topic["id"])["status"] == "adopted"
    with pytest.raises(PermissionError):
        service.ensure_topic_workflow(topic["id"], selector)


def test_topic_adoption_mirror_failure_audits_failure_and_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    identity = IdentityRepository(tmp_path / "system.db")
    identity.seed_defaults()
    creative = CreativeRepository(tmp_path / "creative.db")
    workflow = WorkflowRepository(identity)
    owner = _principal(identity, "mirror-owner", "project_owner")
    selector = _principal(identity, "mirror-selector", "selection_operator")
    topic = creative.upsert_topics([{
        "platform": "dy", "job_id": "mirror-topic", "source_path": "mirror.json",
        "captured_at": "2026-07-23", "title": "Mirror topic", "score": 91,
        "confidence": "high", "score_details": {}, "recommendation": "adopt",
        "evidence": [],
    }])[0]
    task = WorkflowBootstrapService(creative, workflow).ensure_topic_workflow(
        topic["id"], owner
    )["task"]
    workflow.claim_task(task["id"], selector)
    workflow.start_task(task["id"], selector)
    transitions = TransitionService(
        workflow, creative_adapter=CreativeWorkflowAdapter(creative, workflow)
    )
    draft = transitions.create_submission(
        task["id"], "topic_decision", str(topic["id"]), {"decision": "adopt"}, selector
    )
    submitted = transitions.submit(draft["id"], selector)
    audit = AuditService(identity)
    app = FastAPI()
    app.state.identity_permission_service = PermissionService(
        identity, SessionService(identity)
    )
    app.state.audit_service = audit
    app.dependency_overrides[current_principal] = lambda: owner
    app.include_router(build_workflow_router(workflow, transitions))
    client = TestClient(app)
    original_update = creative.update_topic

    def fail_update(*args, **kwargs):
        raise OSError("creative database unavailable")

    monkeypatch.setattr(creative, "update_topic", fail_update)
    failed = client.post(f"/api/workflow/submissions/{submitted['id']}/approve")
    assert failed.status_code == 503
    assert creative.get_topic(topic["id"])["status"] == "pending_review"
    assert _successor_count(identity, task["id"]) == 0
    assert audit.query(action="workflow.approve", result="failure")
    assert audit.query(action="workflow.approve", result="success") == []

    monkeypatch.setattr(creative, "update_topic", original_update)
    retried = client.post(f"/api/workflow/submissions/{submitted['id']}/approve")
    assert retried.status_code == 200
    assert creative.get_topic(topic["id"])["status"] == "adopted"
    assert _successor_count(identity, task["id"]) == 1
    assert audit.query(action="workflow.approve", result="success")


def test_topic_successor_failure_reconciles_on_task_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    identity = IdentityRepository(tmp_path / "system.db")
    identity.seed_defaults()
    creative = CreativeRepository(tmp_path / "creative.db")
    workflow = WorkflowRepository(identity)
    owner = _principal(identity, "successor-owner", "project_owner")
    selector = _principal(identity, "successor-selector", "selection_operator")
    topic = creative.upsert_topics([{
        "platform": "dy", "job_id": "successor-topic", "source_path": "successor.json",
        "captured_at": "2026-07-23", "title": "Successor topic", "score": 92,
        "confidence": "high", "score_details": {}, "recommendation": "adopt",
        "evidence": [],
    }])[0]
    task = WorkflowBootstrapService(creative, workflow).ensure_topic_workflow(
        topic["id"], owner
    )["task"]
    workflow.claim_task(task["id"], selector)
    workflow.start_task(task["id"], selector)
    transitions = TransitionService(
        workflow, creative_adapter=CreativeWorkflowAdapter(creative, workflow)
    )
    draft = transitions.create_submission(
        task["id"], "topic_decision", str(topic["id"]), {"decision": "adopt"}, selector
    )
    submitted = transitions.submit(draft["id"], selector)
    app = FastAPI()
    app.state.identity_permission_service = PermissionService(
        identity, SessionService(identity)
    )
    app.dependency_overrides[current_principal] = lambda: owner
    app.include_router(build_workflow_router(workflow, transitions))
    client = TestClient(app)
    original_create_next_task = workflow.create_next_task

    def fail_create_next_task(*args, **kwargs):
        raise RuntimeError("system database temporarily unavailable")

    monkeypatch.setattr(workflow, "create_next_task", fail_create_next_task)
    failed = client.post(f"/api/workflow/submissions/{submitted['id']}/approve")
    assert failed.status_code == 503
    assert creative.get_topic(topic["id"])["status"] == "adopted"
    assert _successor_count(identity, task["id"]) == 0

    monkeypatch.setattr(workflow, "create_next_task", original_create_next_task)
    app.dependency_overrides[current_principal] = lambda: selector
    reconciled = client.get(f"/api/workflow/tasks/{task['id']}")
    assert reconciled.status_code == 200
    assert _successor_count(identity, task["id"]) == 1
