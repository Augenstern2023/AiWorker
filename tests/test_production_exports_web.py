# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""The production export service against the migrated PostgreSQL creative store.

Every bookkeeping path runs for real: the service is built on a
``PostgresCreativeRepository`` bound to the Alembic-migrated creative schema, and
the assertions read ``creative_production_exports`` back with SQL. The schema
itself is never created by this suite or by the service -- it comes from the
migration, exactly as it does in production.

:func:`snapshot` stands in for ``creative.production_export._inspect_snapshot``
so these tests stay focused on the service's own bookkeeping (record, find,
list, audit, file, HTTP) without also having to build a fully locked resource
chain. It is not a stand-in for a still-unported reader: the real reader is
PostgreSQL-bound and is exercised end to end by ``tests/test_production_export.py``
and ``tests/test_production_preview.py``, including
``test_export_service_preview_uses_the_real_snapshot_reader``, which drives this
same service through the real reader.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text, update
from sqlalchemy import inspect as sqlalchemy_inspect

import creative.production_exports as production_exports
from creative.database_schema import creative_production_exports, creative_projects
from creative.production_exports_api import build_production_export_router
from identity.permission_service import PermissionService
from identity.schema import Principal


class _Preview:
    """The inspection surface ``ProductionExportService.preview`` consumes."""

    def __init__(self, project: dict, version_manifest: dict | None) -> None:
        self.project = project
        self._manifest = version_manifest

    def preview(self, package: dict | None) -> dict:
        return {
            "project": {
                key: self.project[key]
                for key in ("id", "name", "target_episode_count", "workflow_mode")
            },
            "ready": package is not None,
            "fingerprint": package["fingerprint"] if package is not None else None,
            "stages": [],
            "issues": [],
            "version_selection": {
                "strategy": "custom" if self._manifest is not None else "current",
                "manifest": package["version_manifest"] if package is not None else {},
                "candidates": {},
                "warnings": [],
            },
        }


def _fingerprint(project: dict, numbers: list[int], version_manifest: dict | None) -> str:
    payload = {
        "name": project["name"],
        "revision": int(project.get("revision") or 0),
        "target_episode_count": int(project["target_episode_count"]),
        "episode_numbers": numbers,
        "version_manifest": version_manifest,
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _package(project: dict, numbers: list[int], version_manifest: dict | None) -> dict:
    total = int(project["target_episode_count"])
    manifest = (
        dict(version_manifest)
        if version_manifest is not None
        else {"brief": {"resource_ref": str(project["id"]), "version": 1}}
    )
    package = {
        "schemaVersion": "1.0",
        "sourceSystem": "AiWorker",
        "project": {
            key: project[key]
            for key in (
                "id",
                "name",
                "target_episode_count",
                "workflow_mode",
                "revision",
            )
        },
        "provenance": {
            "project_id": int(project["id"]),
            "project_revision": int(project.get("revision") or 0),
            "episode_numbers": list(numbers),
            "export_scope": (
                "full" if numbers == list(range(1, total + 1)) else "partial"
            ),
            "project_total_episodes": total,
            "exported_episode_count": len(numbers),
            "workflow_mode": str(project.get("workflow_mode") or "sequential"),
            "locked_brief_id": 1,
            "character_batch_id": "cast",
            "outline_batch_id": "outline",
        },
        "brief": {"id": 1, "version": 1, "content": {"premise": "寻找真相"}},
        "characters": [{"id": 1, "batch_id": "cast", "version": 1, "content": {"name": "小林"}}],
        "relationships": [],
        "outlines": [
            {"id": number, "stage": "outline", "episode_number": number, "version": 1}
            for number in numbers
        ],
        "beats": [
            {"id": number, "stage": "beats", "episode_number": number, "version": 1}
            for number in numbers
        ],
        "scripts": [
            {"id": number, "stage": "script", "episode_number": number, "version": 1}
            for number in numbers
        ],
        "version_manifest": manifest,
    }
    package["fingerprint"] = _fingerprint(project, list(numbers), version_manifest)
    return package


def snapshot(
    repository,
    project_id,
    episode_numbers=None,
    version_manifest=None,
    *,
    connection=None,
):
    """Stand-in for the ported ``creative.production_export._inspect_snapshot``."""

    assert connection is not None, "the snapshot must be read on a borrowed connection"
    assert connection.execute(text("SELECT 1")).scalar_one() == 1
    project = repository.get_project(int(project_id))
    if project is None:
        raise KeyError(project_id)
    total = int(project["target_episode_count"])
    if episode_numbers is None:
        numbers = list(range(1, total + 1))
    else:
        if isinstance(episode_numbers, (str, bytes)):
            raise ValueError("episode_numbers must contain positive integers")
        numbers = list(episode_numbers)
        if not numbers or any(
            type(number) is not int or not 1 <= number <= total for number in numbers
        ):
            raise ValueError("episode_numbers must contain integers in the project range")
        numbers = sorted(set(numbers))
    return _Preview(project, version_manifest), _package(project, numbers, version_manifest)


@pytest.fixture
def project_id(postgres_creative) -> int:
    """A real creative project so the export foreign key is honest."""

    project = postgres_creative.create_original_project(
        seed_text="一名记者能听见旧物的记忆",
        name="测试剧",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="production-export-web",
    )
    return int(project["id"])


@pytest.fixture
def environment(postgres_creative, monkeypatch, tmp_path, project_id):
    monkeypatch.setattr(production_exports, "_inspect_snapshot", snapshot)
    audit = Mock()
    audit.record.return_value = "audit-id"
    admin = Principal("admin", "admin", "管理员", ("system_admin",), frozenset(), "all", "admin")
    owner = Principal("owner", "owner", "负责人", (), frozenset(), "managed_projects", "owner")
    writer = Principal("writer", "writer", "编剧", ("screenwriter",), frozenset({"page.review.projects"}), "project", "writer")
    sessions = SimpleNamespace(
        cookie_name="session",
        verify_csrf=lambda token, csrf: token and csrf == "valid",
    )
    permissions = PermissionService.__new__(PermissionService)
    permissions.session_service = sessions
    permissions.principal_for_session = lambda token: {
        "admin": admin,
        "owner": owner,
        "writer": writer,
    }.get(token)
    permissions.can_access_managed_project = Mock(
        side_effect=lambda principal, project: (
            principal.user_id == "owner"
            and project == postgres_creative.workflow_project_id(project_id)
        )
    )
    service = production_exports.ProductionExportService(
        root=tmp_path, repository=postgres_creative, audit_service=audit
    )
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(build_production_export_router(service))
    client = TestClient(app)
    client.cookies.set("session", "owner")
    client.headers["X-CSRF-Token"] = "valid"
    return SimpleNamespace(
        service=service,
        client=client,
        repository=postgres_creative,
        audit=audit,
        permissions=permissions,
        admin=admin,
        owner=owner,
        writer=writer,
        root=tmp_path,
        project_id=project_id,
    )


def url(environment, suffix: str = "") -> str:
    return f"/api/creative/projects/{environment.project_id}{suffix}"


def package_for(environment, episode_numbers=None, version_manifest=None) -> dict:
    with environment.repository.connection_scope() as connection:
        _, package = snapshot(
            environment.repository,
            environment.project_id,
            episode_numbers,
            version_manifest,
            connection=connection,
        )
    return package


def create(environment, episode_numbers=None, version_manifest=None):
    """Post the confirmed fingerprint for an already-resolved snapshot."""

    payload: dict = {
        "expected_fingerprint": package_for(
            environment, episode_numbers, version_manifest
        )["fingerprint"]
    }
    if episode_numbers is not None:
        payload["episode_numbers"] = episode_numbers
    if version_manifest is not None:
        payload["version_strategy"] = "custom"
        payload["version_manifest"] = version_manifest
    return environment.client.post(url(environment, "/production-exports"), json=payload)


def test_create_download_and_reuse_are_immutable_and_audited(environment):
    before = package_for(environment)
    result = create(environment)
    assert result.status_code == 201, result.text
    record = result.json()["export"]
    assert result.json()["created"] is True
    downloaded = environment.client.get(record["download_url"])
    assert downloaded.status_code == 200
    with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
        assert "00_导出说明.txt" in archive.namelist()
        assert before["fingerprint"] in archive.read("00_导出说明.txt").decode("utf-8")
    assert downloaded.headers["cache-control"] == "no-store"
    assert "filename*=UTF-8" in downloaded.headers["content-disposition"]
    assert hashlib.sha256(downloaded.content).hexdigest() == record["file_sha256"]
    assert record["audit_status"] == "recorded"
    assert "path" not in record

    with environment.repository.connection_scope() as connection:
        stored = (
            connection.execute(
                select(creative_production_exports).where(
                    creative_production_exports.c.id == record["id"]
                )
            )
            .mappings()
            .one()
        )
    assert stored["project_id"] == environment.project_id
    assert stored["contract_version"] == "2.4"
    assert stored["episode_numbers_json"] == [1, 2]
    assert stored["version_manifest_json"] == before["version_manifest"]
    assert stored["export_scope"] == "full"
    assert stored["project_total_episodes"] == 2
    assert stored["workflow_mode"] == "sequential"
    assert stored["version_strategy"] == "current"
    assert stored["audit_recorded_at"] is not None
    assert stored["size_bytes"] == len(downloaded.content)

    # An unrelated project write must not rebuild the registered artifact.
    with environment.repository.transaction_scope() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == environment.project_id)
            .values(updated_at=func.now())
        )
    repeated = create(environment)
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["export"]["id"] == record["id"]
    assert environment.client.get(record["download_url"]).content == downloaded.content
    assert len(list((environment.root / ".runtime/production-exports").iterdir())) == 1
    actions = [call.kwargs["action"] for call in environment.audit.record.call_args_list]
    assert actions == [
        "production_export.create",
        "production_export.download_started",
        "production_export.reuse",
        "production_export.download_started",
    ]
    assert "寻找真相" not in str(environment.audit.record.call_args_list)


def test_preview_create_and_history_preserve_selected_episodes(environment):
    preview = environment.client.get(
        url(environment, "/production-export/preview"),
        params=[("episode_numbers", 2)],
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["ready"] is True
    assert payload["selection"] == {
        "export_scope": "partial",
        "episode_numbers": [2],
        "project_total_episodes": 2,
        "exported_episode_count": 1,
    }

    created = environment.client.post(
        url(environment, "/production-exports"),
        json={
            "expected_fingerprint": payload["fingerprint"],
            "episode_numbers": [2],
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()["export"]
    assert record["episode_numbers"] == [2]
    assert record["episode_count"] == 1
    assert record["project_total_episodes"] == 2
    assert record["export_scope"] == "partial"
    assert record["workflow_mode"] == "sequential"

    downloaded = environment.client.get(record["download_url"])
    with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
        script_names = [
            name for name in archive.namelist() if name.startswith("06_剧本/")
        ]
        assert script_names == ["06_剧本/第002集_剧本.txt"]
    history = environment.client.get(url(environment, "/production-exports")).json()["items"][0]
    assert history["episode_numbers"] == [2]
    assert "%E7%AC%AC2%E9%9B%86" in downloaded.headers["content-disposition"]


def test_custom_version_manifest_is_prechecked_exported_and_kept_in_history(environment):
    current = environment.client.get(url(environment, "/production-export/preview")).json()
    manifest = current["version_selection"]["manifest"]
    preview = environment.client.post(
        url(environment, "/production-export/preview"),
        json={"version_strategy": "custom", "version_manifest": manifest},
    )
    assert preview.status_code == 200, preview.text
    checked = preview.json()
    assert checked["ready"] is True
    assert checked["version_selection"]["strategy"] == "custom"
    assert checked["version_selection"]["manifest"] == manifest

    created = environment.client.post(
        url(environment, "/production-exports"),
        json={
            "expected_fingerprint": checked["fingerprint"],
            "version_strategy": "custom",
            "version_manifest": manifest,
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()["export"]
    assert record["version_strategy"] == "custom"
    assert record["version_manifest"] == manifest
    downloaded = environment.client.get(record["download_url"])
    assert downloaded.status_code == 200
    with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
        assert "00_导出说明.txt" in archive.namelist()
    history = environment.client.get(url(environment, "/production-exports")).json()["items"][0]
    assert history["version_manifest"] == manifest
    with environment.repository.connection_scope() as connection:
        stored = (
            connection.execute(
                select(creative_production_exports.c.version_manifest_json).where(
                    creative_production_exports.c.id == record["id"]
                )
            )
            .scalars()
            .one()
        )
    assert stored == manifest


@pytest.mark.parametrize("episode_numbers", [[], [0], [3], ["1"], [True]])
def test_invalid_episode_selection_is_rejected(environment, episode_numbers):
    preview = environment.client.get(
        url(environment, "/production-export/preview"),
        params=[("episode_numbers", number) for number in episode_numbers],
    )
    if episode_numbers and all(type(number) is int for number in episode_numbers):
        assert preview.status_code == 422

    created = environment.client.post(
        url(environment, "/production-exports"),
        json={"expected_fingerprint": "a" * 64, "episode_numbers": episode_numbers},
    )
    assert created.status_code == 422


def test_selection_change_after_preview_requires_new_confirmation(environment):
    preview = environment.client.get(
        url(environment, "/production-export/preview"),
        params=[("episode_numbers", 2)],
    ).json()
    response = environment.client.post(
        url(environment, "/production-exports"),
        json={"expected_fingerprint": preview["fingerprint"], "episode_numbers": [1]},
    )
    assert response.status_code == 409
    assert environment.client.get(url(environment, "/production-exports")).json()["total"] == 0


@pytest.mark.parametrize("token,status", [(None, 401), ("writer", 403), ("admin", 200), ("owner", 200)])
def test_preview_access(environment, token, status):
    environment.client.cookies.clear()
    if token:
        environment.client.cookies.set("session", token)
    response = environment.client.get(url(environment, "/production-export/preview"))
    assert response.status_code == status


def test_csrf_and_version_conflict(environment):
    environment.client.headers.pop("X-CSRF-Token")
    assert create(environment).status_code == 403
    environment.client.headers["X-CSRF-Token"] = "valid"
    preview = environment.client.get(url(environment, "/production-export/preview")).json()
    with environment.repository.transaction_scope() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == environment.project_id)
            .values(name="新剧名")
        )
    response = environment.client.post(
        url(environment, "/production-exports"),
        json={"expected_fingerprint": preview["fingerprint"]},
    )
    assert response.status_code == 409
    assert environment.client.get(url(environment, "/production-exports")).json()["total"] == 0


def test_audit_failure_is_recoverable_and_blocks_download(environment):
    environment.audit.record.side_effect = RuntimeError("private audit connection")
    response = create(environment)
    assert response.status_code == 503
    assert "private" not in response.text
    listing = environment.client.get(url(environment, "/production-exports")).json()
    record = listing["items"][0]
    assert record["audit_status"] == "pending"
    assert environment.client.get(record["download_url"]).status_code == 503
    environment.audit.record.side_effect = None
    retried = create(environment)
    assert retried.status_code == 200
    assert retried.json()["created"] is False
    assert retried.json()["export"]["id"] == record["id"]
    assert environment.client.get(record["download_url"]).status_code == 200


@pytest.mark.parametrize("mode", ["missing", "tampered", "outside"])
def test_unavailable_files_cannot_be_downloaded_or_silently_rebuilt(environment, mode):
    record = create(environment).json()["export"]
    with environment.repository.connection_scope() as connection:
        path = connection.execute(
            select(creative_production_exports.c.path).where(
                creative_production_exports.c.id == record["id"]
            )
        ).scalar_one()
    from pathlib import Path

    if mode == "missing":
        Path(path).unlink()
    elif mode == "tampered":
        Path(path).write_bytes(b"changed")
    else:
        outside = environment.root / "outside.json"
        outside.write_bytes(Path(path).read_bytes())
        with environment.repository.transaction_scope() as connection:
            connection.execute(
                update(creative_production_exports)
                .where(creative_production_exports.c.id == record["id"])
                .values(path=str(outside))
            )
    assert environment.client.get(record["download_url"]).status_code == 410
    assert create(environment).status_code == 410


def test_download_and_history_reauthorize(environment):
    record = create(environment).json()["export"]
    environment.client.cookies.set("session", "writer")
    assert environment.client.get(record["download_url"]).status_code == 403
    assert environment.client.get(url(environment, "/production-exports")).status_code == 403
    assert create(environment).status_code == 403


def test_concurrent_creation_reuses_one_file(environment):
    fingerprint = package_for(environment)["fingerprint"]

    def run():
        return environment.service.create(
            environment.project_id, fingerprint, environment.owner
        )[0]["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = list(pool.map(lambda _: run(), range(4)))
    assert len(set(identifiers)) == 1
    assert len(list((environment.root / ".runtime/production-exports").iterdir())) == 1
    with environment.repository.connection_scope() as connection:
        rows = (
            connection.execute(
                select(creative_production_exports.c.id).where(
                    creative_production_exports.c.project_id == environment.project_id
                )
            )
            .scalars()
            .all()
        )
    assert rows == [identifiers[0]]


def test_history_versions_pagination_and_deleted_project(environment):
    first = create(environment).json()["export"]
    with environment.repository.transaction_scope() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == environment.project_id)
            .values(name="新版剧名")
        )
    second = create(environment).json()["export"]
    assert second["id"] != first["id"]
    listing = environment.client.get(
        url(environment, "/production-exports") + "?page=1&page_size=1"
    ).json()
    assert listing["total"] == 2
    assert listing["page_size"] == 1
    assert listing["items"][0]["id"] == second["id"]
    assert environment.client.get(first["download_url"]).status_code == 200
    with environment.repository.transaction_scope() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == environment.project_id)
            .values(deleted=True)
        )
    assert environment.client.get(first["download_url"]).status_code == 404


def test_service_owns_no_schema_ddl(postgres_creative, tmp_path):
    """The export table is the migration's; construction writes no DDL."""

    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = postgres_creative.database.engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        production_exports.ProductionExportService(
            root=tmp_path, repository=postgres_creative, audit_service=Mock()
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements == []
    columns = {
        column["name"]
        for column in sqlalchemy_inspect(engine).get_columns(
            "creative_production_exports"
        )
    }
    assert set(creative_production_exports.c.keys()) <= columns
