# -*- coding: utf-8 -*-
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
