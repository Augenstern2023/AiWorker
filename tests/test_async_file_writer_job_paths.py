# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_async_file_writer_job_paths.py
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

"""Tests for job-aware file paths."""

from tools.async_file_writer import AsyncFileWriter
from var import job_id_var


def test_file_path_without_job_id():
    token = job_id_var.set("")
    try:
        writer = AsyncFileWriter(platform="dy", crawler_type="search")
        path = writer._get_file_path("json", "contents")
    finally:
        job_id_var.reset(token)

    assert path.startswith("data/dy/json/")
    assert "jobs" not in path


def test_file_path_with_job_id():
    token = job_id_var.set("job-1")
    try:
        writer = AsyncFileWriter(platform="dy", crawler_type="search")
        path = writer._get_file_path("json", "contents")
    finally:
        job_id_var.reset(token)

    assert path == "data/dy/jobs/job-1/json/search_contents.json"
