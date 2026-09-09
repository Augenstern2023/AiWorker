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
"""Cross-repository test, synthetic locked SQLite data; never production approval."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest
from tests.test_production_export import db
from creative.production_export import export_production_package
from creative.production_status import sync_production_status


def test_sqlite_export_to_node_project_to_status(db, tmp_path):
    automation = Path(__file__).resolve().parents[2] / 'auto_short_play/automation'
    adapter = automation / 'framework/scripts/source/aiworker.mjs'
    node = shutil.which('node')
    if not node or not adapter.exists():
        pytest.skip('requires sibling auto_short_play and Node')
    with sqlite3.connect(db) as conn:
        for number in (1, 2):
            script = {'episode_title': f'线索{number}', 'scenes': [{
                'sequence_no': 1, 'scene_no': 1, 'time_of_day': '夜',
                'interior_exterior': '内', 'location': '书房',
                'characters': ['小林'], 'props': ['信笺'],
                'blocks': [{'type': 'action', 'text': '小林展开信笺。'},
                           {'type': 'dialogue', 'speaker': '小林', 'text': '我要寻找真相。'}]}]}
            conn.execute("UPDATE creative_episodes SET content_json=? WHERE stage='script' AND episode_number=?", (json.dumps(script), number))
    package = export_production_package(db, 1)
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
    status = sync_production_status(tmp_path / 'status.db', project_root, 1, package['fingerprint'])
    assert status['totalEpisodes'] == 2
    assert status['completedEpisodes'] == 0
    assert status['currentStage'] == 'structure'
    assert status['state'] != 'completed'
