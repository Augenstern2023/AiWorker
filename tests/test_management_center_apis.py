# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_management_center_apis.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

# Copyright (c) 2025 relakkes@gmail.com

"""HTTP contracts for the system-admin navigation and schedule centers."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.dependencies import current_principal
from identity.schema import Principal
from scheduler.api import build_schedule_router
from scheduler.local_service import ScheduleAlert, ScheduleDefinition, ScheduleRun
from web.navigation_api import build_navigation_router


def _principal(*, admin: bool) -> Principal:
    return Principal(
        user_id="user:admin" if admin else "user:member",
        username="admin" if admin else "member",
        display_name="管理员" if admin else "成员",
        role_codes=("system_admin",) if admin else ("member",),
        permissions=frozenset({"*"}) if admin else frozenset(),
        data_scope="all" if admin else "task",
        session_id="session:test",
    )


class _AuditRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def record(self, **event) -> None:
        self.events.append(event)


class _NavigationRepository:
    def __init__(self) -> None:
        self.items = [{
            "id": "navigation:admin:schedules", "parent_id": "navigation:admin",
            "label": "调度中心", "icon": "bi-clock-history", "route": "/admin/schedules",
            "required_permission": "*", "sort_order": 80, "is_enabled": True,
            "is_system": True, "hide_for_system_admin": False,
        }]

    def list_items(self):
        return [dict(item) for item in self.items]

    def update_presentation(self, item_id: str, **changes):
        for item in self.items:
            if item["id"] == item_id:
                item.update(changes)
                return dict(item)
        raise KeyError(item_id)


class _ScheduleRepository:
    def __init__(self) -> None:
        self.definitions: dict[str, ScheduleDefinition] = {}
        self.runs: dict[str, ScheduleRun] = {}
        self.alerts: list[ScheduleAlert] = []

    def list_definitions(self):
        return list(self.definitions.values())

    def create_definition(self, definition):
        self.definitions[definition.id] = definition

    def get_definition(self, schedule_id):
        return self.definitions[schedule_id]

    def update_definition(self, schedule_id, definition):
        if schedule_id not in self.definitions:
            raise KeyError(schedule_id)
        self.definitions[schedule_id] = definition
        return definition

    def set_definition_enabled(self, schedule_id, enabled):
        self.definitions[schedule_id] = replace(self.definitions[schedule_id], is_enabled=enabled)

    def delete_definition(self, schedule_id):
        if schedule_id not in self.definitions:
            raise KeyError(schedule_id)
        del self.definitions[schedule_id]

    def list_runs(self, schedule_id):
        return [run for run in self.runs.values() if run.schedule_id == schedule_id]

    def get_run(self, run_id):
        return self.runs[run_id]

    def update_run(self, run_id, **changes):
        self.runs[run_id] = replace(self.runs[run_id], **changes)
        return self.runs[run_id]

    def list_alerts(self, schedule_id):
        return [alert for alert in self.alerts if alert.schedule_id == schedule_id]


class _ScheduleService:
    def __init__(self, repository: _ScheduleRepository) -> None:
        self.repository = repository
        self._started = False
        self.scheduler = type("Scheduler", (), {"remove_job": lambda *_: None})()

    @staticmethod
    def _job_payload(definition, run_id):
        if definition.payload.get("crawler_type") is None:
            raise ValueError("scheduled job payload is not allowed for its job type")
        return {"run_id": run_id}

    def validate_definition(self, definition):
        self._job_payload(definition, "validation")

    def _schedule_definition(self, definition):
        del definition

    def trigger(self, schedule_id):
        if schedule_id not in self.repository.definitions:
            raise KeyError(schedule_id)
        run = ScheduleRun(
            id="run:manual", schedule_id=schedule_id, attempt=1, status="running",
            scheduled_at=datetime.now(UTC), started_at=datetime.now(UTC), job_id="job:manual",
        )
        self.repository.runs[run.id] = run
        return run

    def trigger_retry(self, run_id):
        run = self.repository.get_run(run_id)
        if run.status != "queued" or run.retry_of_run_id is None:
            raise ValueError("only queued retry runs can be triggered")
        return self.repository.update_run(run_id, status="running", started_at=datetime.now(UTC))

    def cancel_run(self, run_id):
        run = self.repository.get_run(run_id)
        if run.status not in {"queued", "running"}:
            raise ValueError("only active schedule runs can be cancelled")
        return self.repository.update_run(run_id, status="cancelled", finished_at=datetime.now(UTC))


def _app(principal: Principal) -> tuple[FastAPI, _AuditRecorder, _ScheduleRepository]:
    app = FastAPI()
    audit = _AuditRecorder()
    repository = _ScheduleRepository()
    app.state.audit_service = audit
    app.state.navigation_repository = _NavigationRepository()
    app.state.schedule_repository = repository
    app.state.local_schedule_service = _ScheduleService(repository)
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_navigation_router())
    app.include_router(build_schedule_router())
    return app, audit, repository


def test_navigation_api_allows_only_presentation_updates_and_audits_them():
    app, audit, _ = _app(_principal(admin=True))

    response = TestClient(app).patch(
        "/api/admin/navigation/navigation:admin:schedules",
        json={"label": "本机调度", "icon": "bi-clock", "sort_order": 9,
              "is_enabled": False, "route": "/should-not-change"},
    )

    assert response.status_code == 200
    assert response.json()["route"] == "/admin/schedules"
    assert response.json()["label"] == "本机调度"
    assert audit.events[-1]["action"] == "navigation.update"


def test_management_apis_reject_non_system_administrators():
    app, _, _ = _app(_principal(admin=False))
    client = TestClient(app)

    assert client.get("/api/admin/navigation").status_code == 403
    assert client.get("/api/admin/schedules").status_code == 403


def test_schedule_api_supports_crud_manual_operations_history_alerts_and_audit():
    app, audit, repository = _app(_principal(admin=True))
    client = TestClient(app)
    create = client.post("/api/admin/schedules", json={
        "name": "每日榜单", "job_type": "ranking_update",
        "payload": {"crawler_type": "rank", "platform": "dy"},
        "trigger_type": "cron", "trigger_config": {"hour": "8", "minute": "0"},
    })

    assert create.status_code == 201
    schedule_id = create.json()["id"]
    update = client.patch(f"/api/admin/schedules/{schedule_id}", json={
        "name": "上午榜单", "job_type": "ranking_update",
        "payload": {"crawler_type": "rank", "platform": "dy"},
        "trigger_type": "cron", "trigger_config": {"hour": "9", "minute": "0"},
        "is_enabled": True,
    })
    assert update.status_code == 200
    assert update.json()["name"] == "上午榜单"

    assert client.post(f"/api/admin/schedules/{schedule_id}/enabled?enabled=false").status_code == 200
    assert client.post(f"/api/admin/schedules/{schedule_id}/enabled?enabled=true").status_code == 200
    started = client.post(f"/api/admin/schedules/{schedule_id}/trigger")
    assert started.status_code == 200
    run_id = started.json()["id"]
    assert client.post(f"/api/admin/schedule-runs/{run_id}/cancel").json()["status"] == "cancelled"

    retry = ScheduleRun("run:retry", schedule_id, 2, "queued", datetime.now(UTC), retry_of_run_id="run:failed")
    repository.runs[retry.id] = retry
    repository.alerts.append(ScheduleAlert("alert:1", schedule_id, retry.id, "error", "failed", datetime.now(UTC)))
    assert client.post(f"/api/admin/schedule-runs/{retry.id}/retry").json()["status"] == "running"
    assert client.get(f"/api/admin/schedules/{schedule_id}/runs").status_code == 200
    assert client.get(f"/api/admin/schedules/{schedule_id}/alerts").json()[0]["id"] == "alert:1"
    assert client.delete(f"/api/admin/schedules/{schedule_id}").status_code == 204
    assert {event["action"] for event in audit.events} >= {
        "schedule.create", "schedule.update", "schedule.pause", "schedule.resume",
        "schedule.trigger", "schedule.cancel_run", "schedule.retry", "schedule.delete",
    }


def test_management_pages_render_safe_navigation_fields_and_schedule_history_controls():
    root = Path(__file__).resolve().parents[1]
    navigation_js = (root / "static/js/admin/navigation.js").read_text(encoding="utf-8")
    schedules_js = (root / "static/js/admin/schedules.js").read_text(encoding="utf-8")

    assert "innerHTML" not in navigation_js
    assert "innerHTML" not in schedules_js
    assert "/schedule-runs/" in schedules_js
    assert "/runs" in schedules_js
    assert "/alerts" in schedules_js
    assert "schedule-editor" in (root / "templates/admin/schedules.html").read_text(encoding="utf-8")


def test_management_pages_include_department_editor_audit_pagination_and_job_links():
    root = Path(__file__).resolve().parents[1]
    departments_html = (root / "templates/admin/departments.html").read_text(
        encoding="utf-8"
    )
    departments_js = (root / "static/js/admin/departments.js").read_text(
        encoding="utf-8"
    )
    audit_html = (root / "templates/admin/audit.html").read_text(encoding="utf-8")
    audit_js = (root / "static/js/admin/audit.js").read_text(encoding="utf-8")
    schedules_js = (root / "static/js/admin/schedules.js").read_text(
        encoding="utf-8"
    )

    assert "department-tree" in departments_html
    assert "/api/admin/users/" in departments_js
    assert "/departments/" in departments_js
    assert "audit-target-type" in audit_html
    assert "audit-pagination" in audit_html
    assert "/api/admin/audit/" in audit_js
    assert "job_id" in schedules_js
    assert "/api/jobs/" in schedules_js
