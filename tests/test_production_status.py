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
import json
import sqlite3

import pytest

from creative.production_status import sync_production_status, get_production_status


def fixture_project(tmp_path):
    root = tmp_path / 'production'
    root.mkdir()
    package = {'sourceSystem': 'AiWorker', 'project': {'id': 7}, 'fingerprint': 'a' * 64}
    (root / '01-source').mkdir()
    (root / '01-source/aiworker-production-package.json').write_text(json.dumps(package), encoding='utf-8')
    project = {'projectId': 'demo', 'status': 'running', 'pipeline': {'currentStage': 'structure'},
               'manifests': {'episodes': [{'episodeId': 'EP01'}]},
               'outputs': {'manifest': '07-output/episodes.json'}}
    (root / 'project.json').write_text(json.dumps(project), encoding='utf-8')
    (root / 'PROGRESS.md').write_text('正在制作', encoding='utf-8')
    return root, tmp_path / 'status.db'


def test_sync_is_persistent_and_idempotent(tmp_path):
    root, db = fixture_project(tmp_path)
    first = sync_production_status(db, root, 7, 'a' * 64)
    second = sync_production_status(db, root, 7, 'a' * 64)
    assert first == second
    assert get_production_status(db, 7, 'a' * 64)['completedEpisodes'] == 0
    with sqlite3.connect(db) as connection:
        assert connection.execute('select count(*) from production_status').fetchone()[0] == 1


def test_missing_video_never_counts_as_complete(tmp_path):
    root, db = fixture_project(tmp_path)
    (root / '07-output').mkdir()
    (root / '07-output/episodes.json').write_text(json.dumps({'projectId': 'demo', 'episodes': [{'episodeId': 'EP01', 'file': '07-output/missing.mp4'}]}), encoding='utf-8')
    result = sync_production_status(db, root, 7, 'a' * 64)
    assert result['completedEpisodes'] == 0
    assert result['state'] == 'incomplete'
    assert result['issues']


@pytest.mark.parametrize('project_id,fingerprint', [(8, 'a' * 64), (7, 'b' * 64)])
def test_wrong_source_rejected(tmp_path, project_id, fingerprint):
    root, db = fixture_project(tmp_path)
    with pytest.raises(ValueError):
        sync_production_status(db, root, project_id, fingerprint)
    assert not db.exists()


def test_output_traversal_rejected(tmp_path):
    root, db = fixture_project(tmp_path)
    (root / '07-output').mkdir()
    (root / '07-output/episodes.json').write_text(json.dumps({'projectId': 'demo', 'episodes': [{'episodeId': 'EP01', 'file': '../secret.mp4'}]}), encoding='utf-8')
    with pytest.raises(ValueError, match='outside'):
        sync_production_status(db, root, 7, 'a' * 64)


def test_manifest_project_mismatch_rejected(tmp_path):
    root, db = fixture_project(tmp_path)
    (root / '07-output').mkdir()
    (root / '07-output/episodes.json').write_text(json.dumps({'projectId': 'other', 'episodes': []}), encoding='utf-8')
    with pytest.raises(ValueError):
        sync_production_status(db, root, 7, 'a' * 64)
