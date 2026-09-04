# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_job_model.py
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

"""Tests for CrawlJob."""

from core.job import CrawlJob


def test_job_roundtrip(tmp_path):
    job = CrawlJob(
        job_id="job-1",
        platform="dy",
        crawler_type="search",
        keywords=["a", "b"],
        extra={"SEARCH_SORT_TYPE": 2},
    )
    path = tmp_path / "job.json"
    job.save(path)

    loaded = CrawlJob.load(path)

    assert loaded.job_id == "job-1"
    assert loaded.platform == "dy"
    assert loaded.keywords == ["a", "b"]
    assert loaded.extra["SEARCH_SORT_TYPE"] == 2


def test_job_apply_to_config(monkeypatch):
    import config

    job = CrawlJob(
        job_id="job-2",
        platform="dy",
        crawler_type="detail",
        keywords=["keyword"],
        save_data_option="json",
        specified_ids=["123"],
        extra={"SEARCH_SORT_TYPE": 1},
    )

    job.apply_to_config()

    assert config.PLATFORM == "dy"
    assert config.CRAWLER_TYPE == "detail"
    assert config.KEYWORDS == "keyword"
    assert config.DY_SPECIFIED_ID_LIST == ["123"]
    assert config.SEARCH_SORT_TYPE == 1
