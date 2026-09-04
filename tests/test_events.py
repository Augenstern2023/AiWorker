# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_events.py
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

"""Tests for worker events."""

from core.events import WorkerEvent, parse_event_line


def test_parse_json_event():
    event = WorkerEvent(
        event="job_started",
        job_id="job-1",
        worker_id="dy-worker",
        platform="dy",
        level="info",
        message="started",
    )

    parsed = parse_event_line(event.to_json())

    assert parsed.event == "job_started"
    assert parsed.job_id == "job-1"
    assert parsed.message == "started"


def test_parse_plain_log_fallback():
    parsed = parse_event_line("hello", job_id="job-1", platform="dy")

    assert parsed.event == "log"
    assert parsed.job_id == "job-1"
    assert parsed.platform == "dy"
    assert parsed.message == "hello"
