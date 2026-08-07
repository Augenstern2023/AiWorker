# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from dataclasses import replace

import pytest

from scheduler.local_service import LocalScheduleService, ScheduleDefinition, build_default_weekly_report_schedule
from runtime.worker_launcher import build_worker_command


def test_weekly_report_schedule_payload_and_worker_route_are_supported():
    definition = ScheduleDefinition(
        id="weekly-trend-report",
        name="周度流行趋势报告",
        job_type="weekly_report",
        payload={"platform": "analytics", "crawler_type": "weekly_report"},
        trigger_type="cron",
        trigger_config={"day_of_week": "mon", "hour": 10, "minute": 0},
        timezone="Asia/Shanghai",
        is_enabled=True,
    )

    LocalScheduleService(repository=object(), job_manager=object()).validate_definition(definition)
    command = build_worker_command("job.json", platform="analytics", crawler_type="weekly_report")

    assert command[-2:] == ["--job-file", "job.json"]
    assert "agents.weekly_report_worker" in command


def test_default_weekly_report_schedule_runs_on_monday_at_ten_shanghai_time():
    definition = build_default_weekly_report_schedule()

    assert definition.id == "weekly-trend-report"
    assert definition.trigger_type == "cron"
    assert definition.trigger_config == {"day_of_week": "mon", "hour": 10, "minute": 0}
    assert definition.timezone == "Asia/Shanghai"


def test_weekly_report_schedule_rejects_non_analytics_platform():
    definition = replace(build_default_weekly_report_schedule(), payload={"platform": "dy", "crawler_type": "weekly_report"})

    with pytest.raises(ValueError, match="analytics"):
        LocalScheduleService(repository=object(), job_manager=object()).validate_definition(definition)
