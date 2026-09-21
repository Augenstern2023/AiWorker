# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

import hashlib
import zipfile
from io import BytesIO

import pytest

from creative.production_archive import build_production_archive
from creative.production_export import export_production_package
from tests.test_production_export import lock_sequential_chain


@pytest.fixture
def archive_project(postgres_creative) -> int:
    project = postgres_creative.create_original_project(
        seed_text="一名记者能听见旧物的记忆",
        name="测试剧",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="production-archive-original",
    )
    postgres_creative.mark_project_bootstrap_status(project["id"], "ready")
    lock_sequential_chain(postgres_creative, int(project["id"]), episodes=2)
    return int(project["id"])


@pytest.fixture
def imitation_project(postgres_creative) -> int:
    topics = postgres_creative.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "production-archive-rank",
                "source_path": "production-archive-rank.json",
                "captured_at": "2026-09-14",
                "title": "原剧",
                "description": "原剧简介",
                "category": "都市",
                "rank_type": "热播榜",
                "score": 90,
                "confidence": "high",
                "score_details": {},
                "recommendation": "建议仿写",
                "evidence": [],
            }
        ]
    )
    topic = next(item for item in topics if item["title"] == "原剧")
    postgres_creative.update_topic(topic["id"], "adopted")
    breakdown = postgres_creative.create_breakdown(
        topic["id"],
        {"core_conflict": "原剧主角逆境翻盘"},
        {"core_conflict": [{"episode_number": 1}]},
        ["仅参考结构，不复刻表达"],
    )
    postgres_creative.lock_breakdown(breakdown["id"])
    project = postgres_creative.create_project(
        topic["id"],
        breakdown["id"],
        "测试剧",
        2,
        created_by="owner-1",
        creation_request_id="production-archive-imitation",
    )
    postgres_creative.mark_project_bootstrap_status(project["id"], "ready")
    lock_sequential_chain(postgres_creative, int(project["id"]), episodes=2)
    return int(project["id"])


def test_archive_is_deterministic_complete_and_writer_readable(
    postgres_creative, archive_project,
):
    package = export_production_package(postgres_creative, archive_project)
    assert isinstance(package["project"]["project_uid"], str)

    first = build_production_archive(package)
    second = build_production_archive(package)

    assert first == second
    with zipfile.ZipFile(BytesIO(first)) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert not any(name.lower().endswith('.json') for name in names)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert all(not name.startswith(('/', '\\')) and '..' not in name for name in names)
        assert archive.read('02_Brief/创作Brief.md').decode('utf-8').startswith(
            '# 测试剧 · 创作 Brief'
        )
        assert '发现线索' in archive.read(
            '04_分集大纲/第001集_分集大纲.md'
        ).decode('utf-8')
        assert '04_分集大纲/第002集_分集大纲.md' in names
        assert '第 1 集节拍' in archive.read(
            '05_Beats/第001集_Beats.md'
        ).decode('utf-8')
        first_script = archive.read(
            '06_剧本/第001集_剧本.txt'
        ).decode('utf-8')
        second_script = archive.read(
            '06_剧本/第002集_剧本.txt'
        ).decode('utf-8')
        assert first_script.startswith(
            '==================== AI 视频规格建议 ===================='
        )
        assert second_script.startswith(
            '==================== AI 视频规格建议 ===================='
        )
        assert 'MiniMax H3' in first_script
        assert 'Seedance 2.5' in second_script
        assert '第 1 集:第 1 集' in first_script
        assert '小林展开信笺。' in first_script
        assert '01_原剧拆解/原剧拆解.md' not in names

        checksum_lines = archive.read(
            '99_数据清单/SHA256SUMS.txt'
        ).decode('utf-8').splitlines()
        checksums = {
            path: digest
            for digest, path in (line.split('  ', 1) for line in checksum_lines)
        }
        for name in names:
            if name == '99_数据清单/SHA256SUMS.txt':
                continue
            assert checksums[name] == hashlib.sha256(archive.read(name)).hexdigest()


def test_imitation_archive_includes_locked_source_breakdown(
    postgres_creative, imitation_project,
):
    package = export_production_package(postgres_creative, imitation_project)

    with zipfile.ZipFile(BytesIO(build_production_archive(package))) as archive:
        breakdown = archive.read('01_原剧拆解/原剧拆解.md').decode('utf-8')

    assert '原剧主角逆境翻盘' in breakdown
    assert '仅参考结构，不复刻表达' in breakdown
