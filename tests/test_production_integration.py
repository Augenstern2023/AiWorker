# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_production_integration.py
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
"""Cross-repository test: PostgreSQL export, downstream adapter, status pull.

Synthetic locked data through the real creative repository; never a production
approval. The downstream adapter is only exercised when the sibling
``auto_short_play`` checkout and Node are present, so the status half of the
handoff is skipped on a machine without them.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from sqlalchemy import update

from creative.database_schema import creative_episodes
from creative.production_export import export_production_package
from creative.production_status import sync_production_status
from tests.test_production_export import sequential_project  # noqa: F401 (fixture)


def _script_content(number: int) -> dict:
    return {
        'episode_title': f'线索{number}',
        'scenes': [{
            'sequence_no': 1, 'scene_no': 1, 'time_of_day': '夜',
            'interior_exterior': '内', 'location': '书房',
            'characters': ['小林'], 'props': ['信笺'],
            'blocks': [{'type': 'action', 'text': '小林展开信笺。'},
                       {'type': 'dialogue', 'speaker': '小林', 'text': '我要寻找真相。'}],
        }],
    }


def test_postgres_export_to_node_project_to_status(
    postgres_creative, sequential_project, tmp_path,  # noqa: F811
):
    automation = Path(__file__).resolve().parents[2] / 'auto_short_play/automation'
    adapter = automation / 'framework/scripts/source/aiworker.mjs'
    node = shutil.which('node')
    if not node or not adapter.exists():
        pytest.skip('requires sibling auto_short_play and Node')
    with postgres_creative.transaction_scope() as connection:
        for number in (1, 2):
            connection.execute(
                update(creative_episodes)
                .where(
                    creative_episodes.c.stage == 'script',
                    creative_episodes.c.episode_number == number,
                )
                .values(content_json=_script_content(number))
            )
    package = export_production_package(postgres_creative, sequential_project)
    package_file = tmp_path / 'package.json'
    package_file.write_text(json.dumps(package, ensure_ascii=False), encoding='utf-8')
    approval = {'metadataConfirmed': True, 'sourceReview': {
        'status': 'passed', 'reviewer': 'codex', 'reviewedAt': 'test-fixture',
        'sourceCompared': True, 'issuesResolved': 0, 'summary': 'Synthetic integration fixture only',
        'checks': [{'code': code, 'result': 'not_applicable' if code == 'novel_adaptation_traceability' else 'passed',
                    'evidence': 'Synthetic fixture comparison only, not production approval.'}
                   for code in ['entry_route', 'canonical_structure', 'source_coverage', 'speaker_attribution', 'scene_boundaries', 'novel_adaptation_traceability']]}}
    approval_file = tmp_path / 'approval.json'
    approval_file.write_text(json.dumps(approval), encoding='utf-8')
    project_root = tmp_path / 'downstream'
    result = subprocess.run([node, str(adapter), '--package', str(package_file), '--init', str(project_root), '--approval', str(approval_file)], cwd=automation, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    sidecar = json.loads((project_root / '01-source/aiworker-production-package.json').read_text(encoding='utf-8'))
    assert sidecar == package
    status = sync_production_status(
        postgres_creative, project_root, sequential_project, package['fingerprint'],
    )
    assert status['totalEpisodes'] == 2
    assert status['completedEpisodes'] == 0
    assert status['currentStage'] == 'structure'
    assert status['state'] != 'completed'


def test_status_pull_works_without_the_node_adapter(
    postgres_creative, sequential_project, tmp_path,  # noqa: F811
):
    """The status half of the handoff is exercised even when Node is missing."""

    project_root = tmp_path / 'downstream'
    (project_root / '01-source').mkdir(parents=True)
    (project_root / '01-source/aiworker-production-package.json').write_text(
        json.dumps(
            {'sourceSystem': 'AiWorker', 'project': {'id': sequential_project},
             'fingerprint': 'a' * 64},
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    (project_root / 'project.json').write_text(
        json.dumps({
            'projectId': 'demo', 'status': 'running',
            'pipeline': {'currentStage': 'structure'},
            'manifests': {'episodes': [{'episodeId': 'EP01'}, {'episodeId': 'EP02'}]},
            'outputs': {'manifest': '07-output/episodes.json'},
        }),
        encoding='utf-8',
    )
    status = sync_production_status(
        postgres_creative, project_root, sequential_project, 'a' * 64,
    )
    assert status['totalEpisodes'] == 2
    assert status['completedEpisodes'] == 0
    assert status['currentStage'] == 'structure'
    assert status['state'] != 'completed'
