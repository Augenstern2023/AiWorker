# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

import hashlib
import importlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creative.production_export import export_production_package
from creative.repository import CreativeRepository
from identity.permission_service import PermissionService
from identity.schema import Principal
from tests.test_production_export import db


@pytest.fixture
def environment(db, tmp_path):
    module = importlib.import_module('creative.production_exports')
    api = importlib.import_module('creative.production_exports_api')
    repository = CreativeRepository(db)
    audit = Mock()
    audit.record.return_value = 'audit-id'
    admin = Principal('admin', 'admin', '管理员', ('system_admin',), frozenset(), 'all', 'admin')
    owner = Principal('owner', 'owner', '负责人', (), frozenset(), 'managed_projects', 'owner')
    writer = Principal('writer', 'writer', '编剧', ('screenwriter',), frozenset({'page.review.projects'}), 'project', 'writer')
    sessions = SimpleNamespace(cookie_name='session', verify_csrf=lambda token, csrf: token and csrf == 'valid')
    permissions = PermissionService.__new__(PermissionService)
    permissions.session_service = sessions
    permissions.principal_for_session = lambda token: {'admin': admin, 'owner': owner, 'writer': writer}.get(token)
    permissions.can_access_managed_project = Mock(side_effect=lambda principal, project_id: principal.user_id == 'owner' and project_id == '1')
    service = module.ProductionExportService(root=tmp_path, repository=repository, audit_service=audit)
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(api.build_production_export_router(service))
    client = TestClient(app)
    client.cookies.set('session', 'owner')
    client.headers['X-CSRF-Token'] = 'valid'
    return SimpleNamespace(service=service, client=client, repository=repository, audit=audit,
                           permissions=permissions, admin=admin, owner=owner, root=tmp_path)


def create(environment, episode_numbers=None):
    fingerprint = export_production_package(
        environment.repository.db_path, 1, episode_numbers
    )['fingerprint']
    payload = {'expected_fingerprint': fingerprint}
    if episode_numbers is not None:
        payload['episode_numbers'] = episode_numbers
    return environment.client.post(
        '/api/creative/projects/1/production-exports', json=payload
    )


def test_create_download_and_reuse_are_immutable_and_audited(environment, db):
    before = export_production_package(db, 1)
    result = create(environment)
    assert result.status_code == 201, result.text
    record = result.json()['export']
    downloaded = environment.client.get(record['download_url'])
    assert downloaded.status_code == 200
    assert json.loads(downloaded.content) == before
    assert downloaded.headers['cache-control'] == 'no-store'
    assert 'filename*=UTF-8' in downloaded.headers['content-disposition']
    assert hashlib.sha256(downloaded.content).hexdigest() == record['file_sha256']
    with sqlite3.connect(db) as connection:
        connection.execute('UPDATE creative_projects SET updated_at=999')
    repeated = create(environment)
    assert repeated.status_code == 200
    assert repeated.json()['export']['id'] == record['id']
    assert environment.client.get(record['download_url']).content == downloaded.content
    assert export_production_package(db, 1)['fingerprint'] == before['fingerprint']
    actions = [call.kwargs['action'] for call in environment.audit.record.call_args_list]
    assert actions == ['production_export.create', 'production_export.download_started', 'production_export.reuse', 'production_export.download_started']
    assert '寻找真相' not in str(environment.audit.record.call_args_list)
    assert 'path' not in record


def test_preview_create_and_history_preserve_selected_episodes(environment):
    preview = environment.client.get(
        '/api/creative/projects/1/production-export/preview',
        params=[('episode_numbers', 2)],
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload['ready'] is True
    assert payload['selection'] == {
        'export_scope': 'partial',
        'episode_numbers': [2],
        'project_total_episodes': 2,
        'exported_episode_count': 1,
    }

    created = environment.client.post(
        '/api/creative/projects/1/production-exports',
        json={
            'expected_fingerprint': payload['fingerprint'],
            'episode_numbers': [2],
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()['export']
    assert record['episode_numbers'] == [2]
    assert record['episode_count'] == 1
    assert record['project_total_episodes'] == 2
    assert record['export_scope'] == 'partial'
    assert record['workflow_mode'] == 'sequential'

    downloaded = environment.client.get(record['download_url'])
    package = downloaded.json()
    assert package['provenance']['episode_numbers'] == [2]
    assert package['provenance']['export_scope'] == 'partial'
    for stage in ('outlines', 'beats', 'scripts'):
        assert [item['episode_number'] for item in package[stage]] == [2]
    history = environment.client.get(
        '/api/creative/projects/1/production-exports'
    ).json()['items'][0]
    assert history['episode_numbers'] == [2]
    assert '%E7%AC%AC2%E9%9B%86' in downloaded.headers['content-disposition']


@pytest.mark.parametrize('episode_numbers', [[], [0], [3], ['1'], [True]])
def test_invalid_episode_selection_is_rejected(environment, episode_numbers):
    preview = environment.client.get(
        '/api/creative/projects/1/production-export/preview',
        params=[('episode_numbers', number) for number in episode_numbers],
    )
    if episode_numbers and all(type(number) is int for number in episode_numbers):
        assert preview.status_code == 422

    created = environment.client.post(
        '/api/creative/projects/1/production-exports',
        json={'expected_fingerprint': 'a' * 64, 'episode_numbers': episode_numbers},
    )
    assert created.status_code == 422


def test_selection_change_after_preview_requires_new_confirmation(environment):
    preview = environment.client.get(
        '/api/creative/projects/1/production-export/preview',
        params=[('episode_numbers', 2)],
    ).json()
    response = environment.client.post(
        '/api/creative/projects/1/production-exports',
        json={
            'expected_fingerprint': preview['fingerprint'],
            'episode_numbers': [1],
        },
    )
    assert response.status_code == 409


@pytest.mark.parametrize('token,status', [(None, 401), ('writer', 403), ('admin', 200), ('owner', 200)])
def test_preview_access(environment, token, status):
    environment.client.cookies.clear()
    if token:
        environment.client.cookies.set('session', token)
    response = environment.client.get('/api/creative/projects/1/production-export/preview')
    assert response.status_code == status


def test_csrf_and_version_conflict(environment, db):
    environment.client.headers.pop('X-CSRF-Token')
    assert create(environment).status_code == 403
    environment.client.headers['X-CSRF-Token'] = 'valid'
    preview = environment.client.get('/api/creative/projects/1/production-export/preview').json()
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE creative_projects SET name='新剧名'")
    response = environment.client.post('/api/creative/projects/1/production-exports', json={'expected_fingerprint': preview['fingerprint']})
    assert response.status_code == 409
    assert environment.client.get('/api/creative/projects/1/production-exports').json()['total'] == 0


def test_audit_failure_is_recoverable_and_blocks_download(environment):
    environment.audit.record.side_effect = RuntimeError('private audit connection')
    response = create(environment)
    assert response.status_code == 503
    assert 'private' not in response.text
    listing = environment.client.get('/api/creative/projects/1/production-exports').json()
    record = listing['items'][0]
    assert record['audit_status'] == 'pending'
    assert environment.client.get(record['download_url']).status_code == 503
    environment.audit.record.side_effect = None
    retried = create(environment)
    assert retried.status_code == 200
    assert retried.json()['export']['id'] == record['id']
    assert environment.client.get(record['download_url']).status_code == 200


@pytest.mark.parametrize('mode', ['missing', 'tampered', 'outside'])
def test_unavailable_files_cannot_be_downloaded_or_silently_rebuilt(environment, mode):
    record = create(environment).json()['export']
    with environment.repository._connect() as connection:
        path = connection.execute('SELECT path FROM creative_production_exports WHERE id=?', (record['id'],)).fetchone()[0]
    from pathlib import Path
    if mode == 'missing':
        Path(path).unlink()
    elif mode == 'tampered':
        Path(path).write_bytes(b'changed')
    else:
        outside = environment.root / 'outside.json'
        outside.write_bytes(Path(path).read_bytes())
        with environment.repository._connect() as connection:
            connection.execute('UPDATE creative_production_exports SET path=? WHERE id=?', (str(outside), record['id']))
    assert environment.client.get(record['download_url']).status_code == 410
    assert create(environment).status_code == 410


def test_download_and_history_reauthorize(environment):
    record = create(environment).json()['export']
    environment.client.cookies.set('session', 'writer')
    assert environment.client.get(record['download_url']).status_code == 403
    assert environment.client.get('/api/creative/projects/1/production-exports').status_code == 403
    assert create(environment).status_code == 403


def test_concurrent_creation_reuses_one_file(environment):
    fingerprint = export_production_package(environment.repository.db_path, 1)['fingerprint']
    def run():
        return environment.service.create(1, fingerprint, environment.owner)[0]['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = list(pool.map(lambda _: run(), range(4)))
    assert len(set(identifiers)) == 1
    assert len(list((environment.root / '.runtime/production-exports').iterdir())) == 1


def test_history_versions_pagination_and_deleted_project(environment, db):
    first = create(environment).json()['export']
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE creative_projects SET name='新版剧名'")
    second = create(environment).json()['export']
    listing = environment.client.get('/api/creative/projects/1/production-exports?page=1&page_size=1').json()
    assert listing['total'] == 2
    assert listing['items'][0]['id'] == second['id']
    assert environment.client.get(first['download_url']).status_code == 200
    with sqlite3.connect(db) as connection:
        connection.execute('UPDATE creative_projects SET deleted=1')
    assert environment.client.get(first['download_url']).status_code == 404


def test_existing_export_table_is_upgraded_for_episode_scope(tmp_path):
    repository = CreativeRepository(tmp_path / 'existing.sqlite')
    with repository._connect() as connection:
        connection.execute(
            '''
            CREATE TABLE creative_production_exports (
                id TEXT PRIMARY KEY, project_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL, contract_version TEXT NOT NULL,
                created_by TEXT NOT NULL, created_by_name TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, episode_count INTEGER NOT NULL,
                path TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                file_sha256 TEXT NOT NULL, audit_recorded_at REAL,
                UNIQUE(project_id, fingerprint, contract_version)
            )
            '''
        )
    module = importlib.import_module('creative.production_exports')
    module.ProductionExportService(
        root=tmp_path,
        repository=repository,
        audit_service=Mock(),
    )
    with repository._connect() as connection:
        columns = {
            row['name']
            for row in connection.execute(
                'PRAGMA table_info(creative_production_exports)'
            ).fetchall()
        }
    assert {
        'episode_numbers_json', 'export_scope',
        'project_total_episodes', 'workflow_mode',
    } <= columns
