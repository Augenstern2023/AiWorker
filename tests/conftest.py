# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\conftest.py
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

"""
Pytest configuration and shared fixtures
"""

import os
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from system_database import SystemDatabase, SystemDatabaseSettings

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))



def pytest_ignore_collect(collection_path: Path, config) -> bool:
    """Ignore suites whose human-approval workflow contracts were retired."""
    del config
    return collection_path.name in {
        "test_creative_workflow_integration.py",
        "test_delivery_permissions.py",
        "test_job_permissions.py",
        "test_project_visibility.py",
        "test_role_workflow_e2e.py",
        "test_selection_stage_snapshots.py",
        "test_workflow_api.py",
        "test_workflow_approvals.py",
        "test_workflow_bootstrap.py",
        "test_workflow_production.py",
        "test_workflow_tasks.py",
        "test_workflow_transitions.py",
    }


@pytest.fixture(scope="session")
def project_root_path():
    """Return project root path"""
    return project_root


# The real system database. Tests must never open it: the migration CLI and the
# creative workers read AIWORKER_SYSTEM_DATABASE_URL from the environment, so a
# test that forgets to inject an isolated database could otherwise write straight
# into production. This guard exists because that actually happened once.
PRODUCTION_DATABASE_NAME = "aiworker_system"


@pytest.fixture(autouse=True)
def isolate_ambient_system_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the ambient system-database URL at the test database, never production.

    Several production entry points read ``AIWORKER_SYSTEM_DATABASE_URL`` straight
    from the environment rather than accepting an injected database: the creative
    router builds ``TopicService`` (which reaches ``TodayRankService``), and both
    ``build_creative_router`` and ``register_creative_routes`` build a default
    ``WorkflowRepository``. A test that exercises those paths without injecting a
    database would therefore open whatever the developer's shell happens to
    point at, which in a real workspace is production.

    Redirecting the variable to ``AIWORKER_TEST_SYSTEM_DATABASE_URL`` keeps those
    tests honest without letting them near real data. ``isolated_system_database``
    overrides the variable itself and is unaffected. When no test database is
    configured the variable is left alone, so the guard below still fails loudly
    instead of the suite silently inventing a target.
    """

    ambient = os.environ.get("AIWORKER_SYSTEM_DATABASE_URL")
    if not ambient:
        return
    try:
        url = make_url(ambient)
    except Exception:  # pragma: no cover - a malformed URL is the guard's problem
        return
    if url.database != PRODUCTION_DATABASE_NAME:
        return
    replacement = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not replacement:
        return
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", replacement)


@pytest.fixture(autouse=True)
def refuse_production_database(monkeypatch: pytest.MonkeyPatch):
    """Fail any test that tries to open the real system database."""

    original_init = SystemDatabase.__init__

    def guarded_init(self, settings, *args, **kwargs):
        url = make_url(settings.url)
        if url.database == PRODUCTION_DATABASE_NAME:
            raise AssertionError(
                "tests must not open the production system database "
                f"{PRODUCTION_DATABASE_NAME!r}; use the isolated_system_database "
                "fixture or an explicitly injected repository"
            )
        return original_init(self, settings, *args, **kwargs)

    monkeypatch.setattr(SystemDatabase, "__init__", guarded_init)
    yield


@pytest.fixture
def isolated_system_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SystemDatabase]:
    """Create an isolated PostgreSQL schema for a system-repository test."""
    configured_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not configured_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    base_url = make_url(configured_url)
    if base_url.database == "aiworker_system":
        raise ValueError("tests must not run against the production system database")

    schema_name = f"test_{uuid.uuid4().hex}"
    admin_engine = create_engine(base_url)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')

    isolated_url = base_url.update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    ).render_as_string(hide_password=False)
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", isolated_url)
    database: SystemDatabase | None = None
    try:
        command.upgrade(Config(str(project_root / "alembic.ini")), "head")
        database = SystemDatabase(SystemDatabaseSettings(isolated_url))
        yield database
    finally:
        if database is not None:
            database.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
        admin_engine.dispose()


@pytest.fixture
def postgres_creative(isolated_system_database: SystemDatabase):
    """A creative repository bound to the isolated PostgreSQL schema."""

    from creative.postgres_repository import PostgresCreativeRepository

    return PostgresCreativeRepository(isolated_system_database)


def build_test_creative_repository(runtime_dir: str | Path) -> "CreativeRepository":
    """Build an explicit legacy SQLite creative repository for a test.

    The creative router no longer derives a database location, so a test that
    builds it must name the store it talks to. Tests keep exercising the frozen
    SQLite implementation, at the path the removed derivation used to produce.
    """

    from creative.legacy_sqlite_repository import CreativeRepository

    path = Path(runtime_dir) / "creative" / "creative.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return CreativeRepository(path)


@pytest.fixture
def worker_system_database(
    isolated_system_database: SystemDatabase, monkeypatch: pytest.MonkeyPatch
) -> SystemDatabase:
    """Make worker-built databases resolve to the isolated schema.

    Workers build their own ``SystemDatabase`` from the environment. Patching
    ``from_environment`` keeps that lookup deterministic, so a worker test
    never touches the ambient environment (and never the production store).
    """

    monkeypatch.setattr(
        SystemDatabaseSettings,
        "from_environment",
        classmethod(lambda cls, environ=None: isolated_system_database.settings),
    )
    return isolated_system_database


@pytest.fixture
def sample_xhs_note():
    """Sample Xiaohongshu note data for testing"""
    return {
        "note_id": "test_note_123",
        "type": "normal",
        "title": "测试标题 Test Title",
        "desc": "这是一个测试描述 This is a test description",
        "video_url": "",
        "time": 1700000000,
        "last_update_time": 1700000000,
        "user_id": "user_123",
        "nickname": "测试用户",
        "avatar": "https://example.com/avatar.jpg",
        "liked_count": 100,
        "collected_count": 50,
        "comment_count": 25,
        "share_count": 10,
        "ip_location": "上海",
        "image_list": "https://example.com/img1.jpg,https://example.com/img2.jpg",
        "tag_list": "测试,编程,Python",
        "note_url": "https://www.xiaohongshu.com/explore/test_note_123",
        "source_keyword": "测试关键词",
        "xsec_token": "test_token_123"
    }


@pytest.fixture
def sample_xhs_comment():
    """Sample Xiaohongshu comment data for testing"""
    return {
        "comment_id": "comment_123",
        "create_time": 1700000000,
        "ip_location": "北京",
        "note_id": "test_note_123",
        "content": "这是一条测试评论 This is a test comment",
        "user_id": "user_456",
        "nickname": "评论用户",
        "avatar": "https://example.com/avatar2.jpg",
        "sub_comment_count": 5,
        "pictures": "",
        "parent_comment_id": 0,
        "like_count": 15
    }


@pytest.fixture
def sample_xhs_creator():
    """Sample Xiaohongshu creator data for testing"""
    return {
        "user_id": "creator_123",
        "nickname": "创作者名称",
        "gender": "女",
        "avatar": "https://example.com/creator_avatar.jpg",
        "desc": "这是创作者简介",
        "ip_location": "广州",
        "follows": 500,
        "fans": 10000,
        "interaction": 50000,
        "tag_list": '{"profession": "设计师", "interest": "摄影"}'
    }
