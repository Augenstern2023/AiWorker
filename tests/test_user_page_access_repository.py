# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.engine import make_url

from identity.repository import IdentityRepository
from identity.dependencies import current_principal
from identity.schema import Principal
from audit.service import AuditService
from system_database import SystemDatabase, SystemDatabaseSettings
from system_database.schema import users
from web.navigation_repository import NavigationRepository
from web.navigation_api import build_navigation_router


@pytest.fixture
def page_access_repository(monkeypatch: pytest.MonkeyPatch) -> tuple[NavigationRepository, IdentityRepository]:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")

    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identity_repository = IdentityRepository(database)
    identity_repository.seed_defaults()
    return NavigationRepository(database), identity_repository


def _create_user(repository: IdentityRepository, label: str) -> dict[str, object]:
    suffix = uuid.uuid4().hex
    return repository.create_user(
        f"page-access-{label}-{suffix}",
        f"Page Access {label}",
        "hash",
    )


def test_replace_page_access_replaces_the_route_whitelist(
    page_access_repository: tuple[NavigationRepository, IdentityRepository],
) -> None:
    repository, identity_repository = page_access_repository
    first_user = _create_user(identity_repository, "first")
    second_user = _create_user(identity_repository, "second")

    assert repository.replace_page_access("/admin/users", {first_user["id"], second_user["id"]}) == {
        first_user["id"],
        second_user["id"],
    }
    assert repository.list_page_access_user_ids("/admin/users") == {
        first_user["id"],
        second_user["id"],
    }

    assert repository.replace_page_access("/admin/users", set()) == set()
    assert repository.list_page_access_user_ids("/admin/users") == set()


def test_role_defaults_and_account_overrides_are_queryable(
    page_access_repository: tuple[NavigationRepository, IdentityRepository],
) -> None:
    repository, identity_repository = page_access_repository
    user = _create_user(identity_repository, "override")
    role = identity_repository.get_role_by_code("selection_operator")
    assert role is not None
    expected_routes = {
        "/selection/dashboard",
        "/selection/ranks",
        "/selection/hits",
        "/selection/topics",
        "/selection/dramas",
    }
    identity_repository.replace_role_page_access(role["id"], expected_routes)

    assert repository.list_role_page_access_routes(("selection_operator",)) == expected_routes

    assert repository.replace_page_access_overrides(
        "/selection/ranks", {user["id"]: False}
    ) == {user["id"]: False}
    assert repository.get_page_access_override(user["id"], "/selection/ranks") is False

    assert identity_repository.replace_role_page_access(
        role["id"], {"/selection/topics"}
    ) == {"/selection/topics"}
    assert repository.list_role_page_access_routes(("selection_operator",)) == {
        "/selection/topics"
    }


@pytest.mark.parametrize("route", ["/unknown", "/planning/projects/{project_id}/brief"])
def test_replace_page_access_rejects_unknown_or_dynamic_routes(
    page_access_repository: tuple[NavigationRepository, IdentityRepository], route: str
) -> None:
    repository, identity_repository = page_access_repository
    user = _create_user(identity_repository, "route")

    with pytest.raises(ValueError):
        repository.replace_page_access(route, {user["id"]})


def test_replace_page_access_rejects_disabled_user_without_changing_existing_access(
    page_access_repository: tuple[NavigationRepository, IdentityRepository],
) -> None:
    repository, identity_repository = page_access_repository
    active_user = _create_user(identity_repository, "active")
    disabled_user = _create_user(identity_repository, "disabled")
    identity_repository.update_user(disabled_user["id"], status="disabled")
    repository.replace_page_access("/admin/users", {active_user["id"]})

    with pytest.raises(ValueError):
        repository.replace_page_access("/admin/users", {disabled_user["id"]})

    assert repository.list_page_access_user_ids("/admin/users") == {active_user["id"]}


def test_replace_page_access_rejects_deleted_user(
    page_access_repository: tuple[NavigationRepository, IdentityRepository],
) -> None:
    repository, identity_repository = page_access_repository
    deleted_user = _create_user(identity_repository, "deleted")
    with identity_repository.database.transaction() as connection:
        connection.execute(
            update(users).where(users.c.id == deleted_user["id"]).values(is_deleted=1)
        )

    with pytest.raises(ValueError):
        repository.replace_page_access("/admin/users", {deleted_user["id"]})


def test_replace_page_access_rejects_system_administrator_account(
    page_access_repository: tuple[NavigationRepository, IdentityRepository],
) -> None:
    repository, identity_repository = page_access_repository
    administrator = _create_user(identity_repository, "administrator")
    system_admin = identity_repository.get_role_by_code("system_admin")
    assert system_admin is not None
    identity_repository.replace_user_roles(administrator["id"], [system_admin["id"]])

    with pytest.raises(ValueError):
        repository.replace_page_access("/admin/users", {administrator["id"]})


def test_replace_page_access_rejects_unknown_user_without_changing_existing_access(
    page_access_repository: tuple[NavigationRepository, IdentityRepository],
) -> None:
    repository, identity_repository = page_access_repository
    active_user = _create_user(identity_repository, "known")
    repository.replace_page_access("/admin/users", {active_user["id"]})

    with pytest.raises(ValueError):
        repository.replace_page_access("/admin/users", {str(uuid.uuid4())})

    assert repository.list_page_access_user_ids("/admin/users") == {active_user["id"]}


def test_navigation_account_api_persists_allow_deny_and_inherit_overrides(
    page_access_repository: tuple[NavigationRepository, IdentityRepository],
) -> None:
    navigation_repository, identity_repository = page_access_repository
    navigation_repository.seed_catalog()
    user = _create_user(identity_repository, "navigation-api")
    administrator = _create_user(identity_repository, "navigation-admin")
    system_admin = identity_repository.get_role_by_code("system_admin")
    assert system_admin is not None
    identity_repository.replace_user_roles(administrator["id"], [system_admin["id"]])
    app = FastAPI()
    app.state.navigation_repository = navigation_repository
    app.state.identity_repository = identity_repository
    app.state.audit_service = AuditService(identity_repository)
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=administrator["id"],
        username=administrator["username"],
        display_name=administrator["display_name"],
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="system-admin-session",
    )
    app.include_router(build_navigation_router())
    client = TestClient(app)
    item_id = "navigation:selection:ranks"

    initial = client.get(f"/api/admin/navigation/{item_id}/accounts")
    assert initial.status_code == 200
    account = next(item for item in initial.json()["accounts"] if item["id"] == user["id"])
    assert account["access_mode"] == "inherit"

    denied = client.put(
        f"/api/admin/navigation/{item_id}/accounts",
        json={"overrides": [{"user_id": user["id"], "access_mode": "deny"}]},
    )
    assert denied.status_code == 200
    assert navigation_repository.get_page_access_override(user["id"], "/selection/ranks") is False

    inherited = client.put(
        f"/api/admin/navigation/{item_id}/accounts",
        json={"overrides": [{"user_id": user["id"], "access_mode": "inherit"}]},
    )
    assert inherited.status_code == 200
    assert navigation_repository.get_page_access_override(user["id"], "/selection/ranks") is None
