# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.presence_api import build_presence_router
from identity.presence_service import PresenceService
from identity.repository import IdentityRepository
from identity.schema import Principal
from system_database.schema import user_presence, users


def _principal(*, admin: bool) -> Principal:
    return Principal(
        user_id="admin" if admin else "writer",
        username="admin" if admin else "writer",
        display_name="管理员" if admin else "编剧",
        role_codes=("system_admin",) if admin else ("screenwriter",),
        permissions=frozenset({"*"}) if admin else frozenset(),
        data_scope="all" if admin else "task",
        session_id="session-admin" if admin else "session-writer",
    )


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _SnapshotRepository:
    def __init__(self, user_rows, presence_rows):
        self.results = [_Rows(user_rows), _Rows(presence_rows)]

    @contextmanager
    def connect(self):
        repository = self

        class Connection:
            @staticmethod
            def execute(_statement):
                return repository.results.pop(0)

        yield Connection()


def test_snapshot_aggregates_tabs_and_devices_without_exposing_offline_page():
    now = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
    repository = _SnapshotRepository(
        [
            {"id": "active-user", "last_online_at": now},
            {"id": "offline-user", "last_online_at": now - timedelta(hours=1)},
        ],
        [
            {
                "user_id": "active-user",
                "session_id": "device-1",
                "page_label": "内容创作",
                "is_visible": True,
                "last_activity_at": now - timedelta(seconds=10),
                "last_heartbeat_at": now - timedelta(seconds=5),
            },
            {
                "user_id": "active-user",
                "session_id": "device-2",
                "page_label": "系统管理",
                "is_visible": False,
                "last_activity_at": now - timedelta(seconds=1),
                "last_heartbeat_at": now - timedelta(seconds=1),
            },
        ],
    )

    snapshot = PresenceService(repository).snapshot(now=now)

    assert snapshot["users"]["active-user"] == {
        "status": "active",
        "page": "内容创作",
        "device_count": 2,
        "last_online_at": now.isoformat(),
    }
    assert snapshot["users"]["offline-user"] == {
        "status": "offline",
        "page": None,
        "device_count": 0,
        "last_online_at": (now - timedelta(hours=1)).isoformat(),
    }


def test_snapshot_marks_connected_hidden_tabs_idle():
    now = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
    repository = _SnapshotRepository(
        [{"id": "writer", "last_online_at": now}],
        [
            {
                "user_id": "writer",
                "session_id": "device-1",
                "page_label": "内容策划",
                "is_visible": False,
                "last_activity_at": now,
                "last_heartbeat_at": now,
            }
        ],
    )

    presence = PresenceService(repository).snapshot(now=now)["users"]["writer"]

    assert presence["status"] == "idle"
    assert presence["page"] == "内容策划"


def test_page_labels_are_module_level_only():
    assert PresenceService.page_label_for_path(
        "/writing/projects/project-secret/episodes/3/scripts?draft=12"
    ) == "内容创作"
    assert PresenceService.page_label_for_path("/admin/users") == "系统管理"
    assert PresenceService.page_label_for_path("/profile") == "个人中心"


def test_presence_schema_and_general_user_payload_keep_monitoring_separate():
    assert "last_online_at" in users.c
    assert set(user_presence.primary_key.columns.keys()) == {"session_id", "tab_id"}

    repository = IdentityRepository.__new__(IdentityRepository)
    repository.user_role_codes = lambda _user_id, connection=None: []
    repository.list_user_departments = lambda _user_id, connection=None: []
    payload = repository._user_dict(
        {
            "id": "writer",
            "username": "writer",
            "normalized_username": "writer",
            "display_name": "编剧",
            "password_hash": "not-for-output",
            "last_online_at": datetime.now(timezone.utc),
            "is_deleted": 0,
            "updated_at": datetime.now(timezone.utc),
        }
    )

    assert "last_online_at" not in payload


class _FakeSessionService:
    cookie_name = "aiworker_session"


class _FakePermissionService:
    def principal_for_session(self, raw_token):
        if raw_token == "admin-token":
            return _principal(admin=True)
        if raw_token == "writer-token":
            return _principal(admin=False)
        return None


class _FakePresenceService:
    repository = object()

    def __init__(self):
        self.heartbeat = None

    def record_heartbeat(self, **values):
        self.heartbeat = values

    @staticmethod
    def snapshot():
        return {"refreshed_at": "2026-09-10T08:00:00+00:00", "users": {}}


def _presence_client(service, token):
    app = FastAPI()
    app.include_router(
        build_presence_router(
            service,
            _FakeSessionService(),
            permission_service=_FakePermissionService(),
        )
    )
    client = TestClient(app)
    client.cookies.set("aiworker_session", token)
    return client


def test_heartbeat_accepts_authenticated_users_and_sanitized_page_path():
    service = _FakePresenceService()
    client = _presence_client(service, "writer-token")

    response = client.post(
        "/api/presence/heartbeat",
        json={
            "tab_id": "tab-1",
            "path": "/writing/projects/project-secret",
            "is_visible": True,
            "idle_seconds": 12.5,
        },
    )

    assert response.status_code == 204
    assert service.heartbeat == {
        "user_id": "writer",
        "session_id": "session-writer",
        "tab_id": "tab-1",
        "path": "/writing/projects/project-secret",
        "is_visible": True,
        "idle_seconds": 12.5,
    }


def test_presence_snapshot_is_system_admin_only():
    service = _FakePresenceService()

    assert _presence_client(service, "admin-token").get(
        "/api/admin/presence"
    ).status_code == 200
    denied = _presence_client(service, "writer-token").get(
        "/api/admin/presence"
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "没有查看在线情况的权限"}
