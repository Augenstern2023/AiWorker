# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from agents import weekly_report_worker
from core.job import CrawlJob


def test_weekly_report_worker_accepts_an_explicit_central_service(monkeypatch):
    events = []

    class CentralWeeklyReportService:
        def generate_all_available(self, with_llm: bool):
            assert with_llm is True
            return []

    monkeypatch.setattr(
        weekly_report_worker,
        "emit_event",
        lambda event, **kwargs: events.append((event, kwargs)),
    )
    job = CrawlJob(job_id="weekly-central", platform="dy", crawler_type="rank")

    result = weekly_report_worker.run_job(
        job,
        service=CentralWeeklyReportService(),
    )

    assert result == 0
    assert events[-1][1]["message"] == "周度流行趋势报告任务结束"
