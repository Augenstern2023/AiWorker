# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from creative.free_mode_service import FreeCreativeModeService


def _over_limit_content() -> dict:
    return {"beats": [{"order": number} for number in range(1, 5)]}


class _NoConnectRepository:
    @contextmanager
    def _connect(self):
        raise AssertionError("validation should happen before opening a connection")
        yield


class _RowConnection:
    def __init__(self, content: dict):
        self.content = content
        self.update_calls = 0

    def execute(self, statement, parameters=()):
        sql = str(statement)
        if sql == "BEGIN IMMEDIATE":
            return self
        if "SELECT * FROM creative_episodes" in sql:
            return _OneRow({"id": 1, "stage": "beats", "status": "draft"})
        if "content_json FROM creative_episodes" in sql:
            return _OneRow({"content_json": json.dumps(self.content)})
        self.update_calls += int(sql.lstrip().upper().startswith(("UPDATE", "INSERT")))
        return self

    def fetchone(self):
        return None

    @property
    def rowcount(self):
        return 0


class _LockRepository:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def _connect(self):
        yield self.connection


class _OneRow:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def test_create_episode_resource_rejects_over_limit_before_db_access():
    service = FreeCreativeModeService(_NoConnectRepository())

    with pytest.raises(ValueError, match="1-3"):
        service.create_episode_resource(
            1,
            "beats",
            1,
            _over_limit_content(),
            expected_head_revision=0,
            actor_user_id="owner-1",
        )


def test_update_resource_rejects_over_limit_before_db_access():
    service = FreeCreativeModeService(_NoConnectRepository())

    with pytest.raises(ValueError, match="1-3"):
        service.update_resource(
            project_id=1,
            stage="beats",
            resource_ref="1",
            content=_over_limit_content(),
        )


def test_update_item_rejects_over_limit_before_update():
    connection = _RowConnection({"beats": [{"order": 1}]})
    repository = _LockRepository(connection)
    service = FreeCreativeModeService(repository)
    service._raw_project = lambda _conn, _project_id: {"workflow_mode": "free"}

    with pytest.raises(ValueError, match="1-3"):
        service.update_item(
            project_id=1,
            item_type="episode",
            item_id=1,
            content=_over_limit_content(),
        )
    assert connection.update_calls == 0


def test_lock_resource_rejects_over_limit_before_changing_status():
    connection = _RowConnection(_over_limit_content())
    repository = _LockRepository(connection)
    service = FreeCreativeModeService(repository)
    service._raw_project = lambda _conn, _project_id: {"workflow_mode": "free"}
    service._assert_head_resource = lambda *_args: 1

    with pytest.raises(ValueError, match="1-3"):
        service.lock_resource(
            project_id=1,
            stage="beats",
            resource_ref="1",
            episode_number=1,
            expected_head_revision=0,
            actor_user_id="owner-1",
        )
    assert connection.update_calls == 0
