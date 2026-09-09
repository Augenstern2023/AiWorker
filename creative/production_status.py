# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/creative\production_status.py
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
"""Read-only downstream inspection, persisted separately from creative decisions."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3


def _inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError('Path outside production project')
    return target


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected JSON object: {path.name}')
    return value


def sync_production_status(db_path, project_root, project_id: int, fingerprint: str) -> dict:
    """Pull a bound project's status; no remote URLs, credentials or media calls."""
    root = Path(project_root).resolve(strict=True)
    package = _read(_inside(root, '01-source/aiworker-production-package.json'))
    if (package.get('sourceSystem') != 'AiWorker'
            or package.get('project', {}).get('id') != project_id
            or package.get('fingerprint') != fingerprint):
        raise ValueError('Production package identity/fingerprint mismatch')
    project = _read(_inside(root, 'project.json'))
    expected = [item['episodeId'] for item in project['manifests']['episodes']]
    if not expected or len(expected) != len(set(expected)):
        raise ValueError('Invalid episode index')
    output = _inside(root, project['outputs']['manifest'])
    issues, episodes = [], []
    if output.exists():
        manifest = _read(output)
        if manifest.get('projectId') != project['projectId']:
            raise ValueError('Output manifest project mismatch')
        seen = set()
        for item in manifest['episodes']:
            episode_id = item['episodeId']
            if episode_id in seen or episode_id not in expected:
                raise ValueError('Duplicate or unknown output episode')
            seen.add(episode_id)
            file = _inside(root, item['file'])
            exists = file.is_file() and file.stat().st_size > 0
            episodes.append({'episodeId': episode_id, 'file': item['file'], 'exists': exists})
            if not exists:
                issues.append(f'{episode_id}: output missing or empty')
    completed = sum(item['exists'] for item in episodes)
    declared = project.get('status', 'ready')
    if declared == 'completed' and completed != len(expected):
        issues.append('Project claims completion without all episode files')
    progress_path = _inside(root, 'PROGRESS.md')
    progress = progress_path.read_text(encoding='utf-8-sig') if progress_path.is_file() else ''
    result = {
        'schemaVersion': '1.0', 'sourceProjectId': project_id,
        'fingerprint': fingerprint, 'productionProjectId': project['projectId'],
        'projectRoot': str(root), 'declaredStatus': declared,
        'state': 'incomplete' if issues else ('completed' if completed == len(expected) else declared if declared != 'completed' else 'incomplete'),
        'currentStage': project.get('pipeline', {}).get('currentStage'),
        'stages': project.get('pipeline', {}).get('stages', {}),
        'totalEpisodes': len(expected), 'completedEpisodes': completed,
        'episodes': episodes, 'issues': issues, 'progressMarkdown': progress,
        'verification': 'nonempty-files-only; media quality not verified',
    }
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS production_status (project_id INTEGER NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id, fingerprint))')
        connection.execute('INSERT INTO production_status VALUES (?, ?, ?) ON CONFLICT(project_id,fingerprint) DO UPDATE SET payload=excluded.payload', (project_id, fingerprint, json.dumps(result, ensure_ascii=False, sort_keys=True)))
    return get_production_status(database, project_id, fingerprint)


def get_production_status(db_path, project_id: int, fingerprint: str) -> dict | None:
    if not Path(db_path).is_file():
        return None
    with sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        row = connection.execute('SELECT payload FROM production_status WHERE project_id=? AND fingerprint=?', (project_id, fingerprint)).fetchone()
    return json.loads(row[0]) if row else None
