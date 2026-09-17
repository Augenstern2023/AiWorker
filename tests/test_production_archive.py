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

from creative.production_archive import build_production_archive
from creative.production_export import export_production_package
from tests.test_production_export import db, imitation_db


def test_archive_is_deterministic_complete_and_writer_readable(db):
    package = export_production_package(db, 1)

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
        assert '线索出现' in archive.read(
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
        assert '△ 开门' in first_script
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


def test_imitation_archive_includes_locked_source_breakdown(imitation_db):
    package = export_production_package(imitation_db, 1)

    with zipfile.ZipFile(BytesIO(build_production_archive(package))) as archive:
        breakdown = archive.read('01_原剧拆解/原剧拆解.md').decode('utf-8')

    assert '原剧主角逆境翻盘' in breakdown
    assert '仅参考结构，不复刻表达' in breakdown
