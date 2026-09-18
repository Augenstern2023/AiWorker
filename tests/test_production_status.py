# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_production_status.py
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
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
"""The downstream status document against the migrated PostgreSQL creative store.

The bound project tree is a real directory on disk; the status document is a
row in the migration-owned ``production_status`` table, written through the
injected repository. Nothing here creates schema.
"""
import json

import pytest
from sqlalchemy import func, select

from creative.database_schema import production_status
from creative.production_status import sync_production_status, get_production_status


def fixture_project(tmp_path, project_id, fingerprint='a' * 64):
    root = tmp_path / 'production'
    root.mkdir()
    package = {'sourceSystem': 'AiWorker', 'project': {'id': project_id},
               'fingerprint': fingerprint}
    (root / '01-source').mkdir()
    (root / '01-source/aiworker-production-package.json').write_text(json.dumps(package), encoding='utf-8')
    project = {'projectId': 'demo', 'status': 'running', 'pipeline': {'currentStage': 'structure'},
               'manifests': {'episodes': [{'episodeId': 'EP01'}]},
               'outputs': {'manifest': '07-output/episodes.json'}}
    (root / 'project.json').write_text(json.dumps(project), encoding='utf-8')
    (root / 'PROGRESS.md').write_text('正在制作', encoding='utf-8')
    return root


def _row_count(repository) -> int:
    with repository.connection_scope() as connection:
        return int(
            connection.execute(select(func.count()).select_from(production_status)).scalar_one()
        )


@pytest.fixture
def project_id(postgres_creative) -> int:
    """A real creative project: ``production_status.project_id`` is a foreign key."""

    project = postgres_creative.create_original_project(
        seed_text="一名记者能听见旧物的记忆",
        name="状态项目",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="production-status-project",
    )
    return int(project["id"])


def test_sync_is_persistent_and_idempotent(postgres_creative, project_id, tmp_path):
    root = fixture_project(tmp_path, project_id)
    first = sync_production_status(postgres_creative, root, project_id, 'a' * 64)
    second = sync_production_status(postgres_creative, root, project_id, 'a' * 64)
    assert first == second
    assert get_production_status(
        postgres_creative, project_id, 'a' * 64,
    )['completedEpisodes'] == 0
    # The upsert refreshes one row; it never appends a second one.
    assert _row_count(postgres_creative) == 1


def test_missing_video_never_counts_as_complete(postgres_creative, project_id, tmp_path):
    root = fixture_project(tmp_path, project_id)
    (root / '07-output').mkdir()
    (root / '07-output/episodes.json').write_text(
        json.dumps({'projectId': 'demo', 'episodes': [
            {'episodeId': 'EP01', 'file': '07-output/missing.mp4'},
        ]}),
        encoding='utf-8',
    )
    result = sync_production_status(postgres_creative, root, project_id, 'a' * 64)
    assert result['completedEpisodes'] == 0
    assert result['state'] == 'incomplete'
    assert result['issues']


def test_sync_rejects_unknown_source_or_fingerprint_without_writing(
    postgres_creative, project_id, tmp_path,
):
    root = fixture_project(tmp_path, project_id)
    with pytest.raises(ValueError):
        sync_production_status(postgres_creative, root, project_id + 1, 'a' * 64)
    with pytest.raises(ValueError):
        sync_production_status(postgres_creative, root, project_id, 'b' * 64)
    assert _row_count(postgres_creative) == 0


def test_output_traversal_rejected(postgres_creative, project_id, tmp_path):
    root = fixture_project(tmp_path, project_id)
    (root / '07-output').mkdir()
    (root / '07-output/episodes.json').write_text(
        json.dumps({'projectId': 'demo', 'episodes': [
            {'episodeId': 'EP01', 'file': '../secret.mp4'},
        ]}),
        encoding='utf-8',
    )
    with pytest.raises(ValueError, match='outside'):
        sync_production_status(postgres_creative, root, project_id, 'a' * 64)


def test_manifest_project_mismatch_rejected(postgres_creative, project_id, tmp_path):
    root = fixture_project(tmp_path, project_id)
    (root / '07-output').mkdir()
    (root / '07-output/episodes.json').write_text(
        json.dumps({'projectId': 'other', 'episodes': []}), encoding='utf-8',
    )
    with pytest.raises(ValueError):
        sync_production_status(postgres_creative, root, project_id, 'a' * 64)


def test_status_payload_is_a_jsonb_document_in_the_migrated_table(
    postgres_creative, project_id, tmp_path,
):
    root = fixture_project(tmp_path, project_id)
    result = sync_production_status(postgres_creative, root, project_id, 'a' * 64)

    with postgres_creative.connection_scope() as connection:
        row = connection.execute(
            select(production_status.c.project_id, production_status.c.fingerprint,
                   production_status.c.payload)
        ).mappings().one()
    # The document round-trips through JSONB as a plain object: no serialized
    # TEXT blob and no runtime DDL of a second shape.
    assert row["project_id"] == project_id
    assert row["fingerprint"] == 'a' * 64
    assert isinstance(row["payload"], dict)
    assert row["payload"] == result
    assert get_production_status(postgres_creative, project_id, 'missing') is None


def test_write_uses_the_borrowed_connection_without_committing(
    postgres_creative, project_id, tmp_path,
):
    """A caller-owned transaction keeps the write, and its rollback discards it."""

    root = fixture_project(tmp_path, project_id)
    with postgres_creative.connection_scope() as connection:
        result = sync_production_status(
            postgres_creative, root, project_id, 'a' * 64, connection=connection,
        )
        assert get_production_status(
            postgres_creative, project_id, 'a' * 64, connection=connection,
        ) == result
        connection.rollback()
    assert get_production_status(postgres_creative, project_id, 'a' * 64) is None
    assert _row_count(postgres_creative) == 0
