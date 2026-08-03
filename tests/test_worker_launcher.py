# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_worker_launcher.py
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

"""Tests for worker command generation."""

import sys

from runtime.worker_launcher import build_worker_command, build_worker_env


def test_build_worker_command():
    assert build_worker_command(".runtime/jobs/job.json") == [
        sys.executable,
        "main.py",
        "--job-file",
        ".runtime/jobs/job.json",
    ]


def test_build_hongguo_worker_command():
    assert build_worker_command(".runtime/jobs/hg.json", platform="hg") == [
        sys.executable,
        "-m",
        "agents.hongguo_csv_worker",
        "--job-file",
        ".runtime/jobs/hg.json",
    ]


def test_build_douyin_rank_worker_command():
    assert build_worker_command(".runtime/jobs/dy-rank.json", platform="dy", crawler_type="rank") == [
        sys.executable,
        "-m",
        "agents.douyin_rank_worker",
        "--job-file",
        ".runtime/jobs/dy-rank.json",
    ]


def test_build_creative_phase4_worker_command():
    assert build_worker_command(
        ".runtime/jobs/creative.json",
        platform="creative",
        crawler_type="phase4_generate",
    ) == [
        sys.executable,
        "-m",
        "creative.phase4_worker",
        "--job-file",
        ".runtime/jobs/creative.json",
    ]


def test_build_creative_breakdown_worker_command():
    assert build_worker_command(
        ".runtime/jobs/breakdown.json",
        platform="creative",
        crawler_type="breakdown_generate",
    ) == [
        sys.executable,
        "-m",
        "creative.breakdown_worker",
        "--job-file",
        ".runtime/jobs/breakdown.json",
    ]


def test_build_creative_screenplay_review_worker_command():
    assert build_worker_command(
        ".runtime/jobs/script-review.json",
        platform="creative",
        crawler_type="screenplay_review",
    ) == [
        sys.executable,
        "-m",
        "screenplay_review.worker",
        "--job-file",
        ".runtime/jobs/script-review.json",
    ]


def test_build_creative_screenplay_optimization_worker_command():
    assert build_worker_command(
        ".runtime/jobs/script-optimization.json",
        platform="creative",
        crawler_type="screenplay_optimization",
    ) == [
        sys.executable,
        "-m",
        "screenplay_optimization.worker",
        "--job-file",
        ".runtime/jobs/script-optimization.json",
    ]


def test_build_data_file_operation_worker_command():
    assert build_worker_command(
        ".runtime/jobs/data-operation.json",
        platform="data",
        crawler_type="file_operation",
    ) == [
        sys.executable,
        "-m",
        "agents.data_file_operation_worker",
        "--job-file",
        ".runtime/jobs/data-operation.json",
    ]


def test_build_worker_env_sets_utf8():
    assert build_worker_env()["PYTHONIOENCODING"] == "utf-8"
