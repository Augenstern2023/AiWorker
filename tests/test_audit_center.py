# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from audit.api import build_audit_router
from audit.service import AuditService
from identity.dependencies import current_principal
from identity.repository import IdentityRepository
from identity.schema import Principal


@pytest.fixture
def repository(isolated_system_database) -> IdentityRepository:
    repository = IdentityRepository(isolated_system_database)
    repository.seed_defaults()
    return repository


def _principal(user: dict[str, str], session_id: str) -> Principal:
    return Principal(
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id=session_id,
    )


def test_audit_query_filters_actor_project_action_result_and_exclusive_time(
    repository: IdentityRepository,
):
    admin = _principal(
        repository.create_user("admin", "Original Admin", "hash"), "admin-session"
    )
    other = _principal(
        repository.create_user("other", "Other Operator", "hash"), "other-session"
    )
    audit_service = AuditService(repository)

    matching_id = audit_service.record(
        actor=admin,
        action="workflow.claim",
        target_type="task",
        target_id="matching-task",
        project_id="project-1",
        before=None,
        after={"status": "assigned"},
        reason=None,
        result="success",
    )
    other_actor_id = audit_service.record(
        actor=other,
        action="workflow.claim",
        target_type="task",
        target_id="other-actor-task",
        project_id="project-1",
        before=None,
        after=None,
        reason=None,
        result="success",
    )
    boundary_id = audit_service.record(
        actor=admin,
        action="workflow.claim",
        target_type="task",
        target_id="boundary-task",
        project_id="project-1",
        before=None,
        after=None,
        reason=None,
        result="success",
    )
    with repository.connect() as connection:
        connection.exec_driver_sql(
            "UPDATE audit_logs SET created_at = %s WHERE id = %s",
            [
                ("2026-07-01 12:00:00", matching_id),
                ("2026-07-01 12:00:00", other_actor_id),
                ("2026-07-02 00:00:00", boundary_id),
            ],
        )
        connection.exec_driver_sql(
            "UPDATE users SET display_name = %s WHERE id = %s",
            ("Renamed Admin", admin.user_id),
        )

    events = audit_service.query(
        actor="Original Admin",
        project_id="project-1",
        action="workflow.claim",
        result="success",
        created_from="2026-07-01 00:00:00",
        created_to="2026-07-02 00:00:00",
    )

    assert [event["id"] for event in events] == [matching_id]
    assert events[0]["actor_display_name"] == "Original Admin"


def test_audit_api_accepts_datetime_local_time_filters():
    admin = Principal(
        user_id="admin",
        username="admin",
        display_name="Audit Admin",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="admin-session",
    )

    class RecordingAuditService:
        repository = SimpleNamespace(identity_repository=object())

        def __init__(self) -> None:
            self.filters: dict[str, object] = {}

        def count(self, **filters: object) -> int:
            self.filters = filters
            return 0

        def query(self, **filters: object) -> list[dict[str, object]]:
            self.filters = filters
            return []

    audit_service = RecordingAuditService()
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: admin
    app.include_router(build_audit_router(audit_service))

    response = TestClient(app).get(
        "/api/admin/audit",
        params={
            "created_from": "2026-07-29 09:52:00",
            "created_to": "2026-07-30 09:52:00",
        },
    )

    assert response.status_code == 200
    assert audit_service.filters["created_from"] == datetime(2026, 7, 29, 9, 52)
    assert audit_service.filters["created_to"] == datetime(2026, 7, 30, 9, 52)


def test_audit_export_uses_filtered_snapshot_data_and_records_export(
    repository: IdentityRepository,
):
    admin = _principal(
        repository.create_user("admin", "Audit Admin", "hash"), "admin-session"
    )
    audit_service = AuditService(repository)
    audit_service.record(
        actor=admin,
        action="workflow.claim",
        target_type="task",
        target_id="task-1",
        project_id="project-1",
        before={"api_key": "secret"},
        after={"status": "assigned"},
        reason=None,
        result="success",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: admin
    app.include_router(build_audit_router(audit_service))
    client = TestClient(app)

    csv_response = client.get(
        "/api/admin/audit/export",
        params={"format": "csv", "actor": "Audit Admin", "project_id": "project-1"},
    )

    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "audit-csv-" in csv_response.headers["content-disposition"]
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    csv_text = csv_response.content.decode("utf-8-sig")
    assert "Audit Admin" in csv_text
    assert "[REDACTED]" in csv_text
    assert "secret" not in csv_text

    workbook_response = client.get(
        "/api/admin/audit/export",
        params={"format": "xlsx", "project_id": "project-1"},
    )

    assert workbook_response.status_code == 200
    workbook = load_workbook(BytesIO(workbook_response.content), read_only=True)
    rows = list(workbook.active.values)
    assert rows[0][1] == "操作人"
    assert rows[1][1] == "Audit Admin"
    assert len(audit_service.query(action="audit.export")) == 2


def test_audit_api_returns_one_event_for_the_details_drawer(
    repository: IdentityRepository,
):
    admin = _principal(
        repository.create_user("admin", "Audit Admin", "hash"), "admin-session"
    )
    audit_service = AuditService(repository)
    event_id = audit_service.record(
        actor=admin,
        action="organization.department.rename",
        target_type="department",
        target_id="department:production",
        project_id=None,
        before={"name": "Production"},
        after={"name": "Content"},
        reason=None,
        result="success",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: admin
    app.include_router(build_audit_router(audit_service))

    response = TestClient(app).get(f"/api/admin/audit/{event_id}")

    assert response.status_code == 200
    assert response.json()["id"] == event_id
    assert response.json()["target_id"] == "department:production"
