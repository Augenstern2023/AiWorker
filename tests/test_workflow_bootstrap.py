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

from creative.repository import CreativeRepository
from creative.workflow_bootstrap import WorkflowBootstrapService
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from workflow.repository import WorkflowRepository


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


def test_owner_adoption_bootstraps_one_topic_decision_task(tmp_path: Path):
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
        service.adopt_topic(topic["id"], selector)
    assert creative.get_topic(topic["id"])["status"] == "pending_review"

    first = service.adopt_topic(topic["id"], owner)
    repeated = service.ensure_topic_workflow(topic["id"], owner)

    assert creative.get_topic(topic["id"])["status"] == "adopted"
    assert first["task"] == repeated["task"]
    assert first["task"]["task_type"] == "topic_decision"
    assert first["task"]["role_code"] == "selection_operator"
    assert first["task"]["status"] == "pool"
    with pytest.raises(PermissionError):
        service.ensure_topic_workflow(topic["id"], selector)
