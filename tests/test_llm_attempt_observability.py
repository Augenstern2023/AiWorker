# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
from pathlib import Path

from creative.llm_observability import (
    active_attempt_reporter,
    build_attempt_reporter,
    format_request_summary,
    set_attempt_reporter,
    summarize_payload_shape,
)


def test_summarize_payload_shape_lists_top_level_keys_with_their_shapes():
    summary = summarize_payload_shape(
        {"characters": [1, 2], "relations": {"林穗-周谨": {}}, "note": None}
    )

    assert "characters(数组/2项)" in summary
    assert "relations(对象/1键)" in summary
    assert "note(null)" in summary


def test_summarize_payload_shape_reports_non_object_payloads():
    assert "数组/3项" in summarize_payload_shape([1, 2, 3])
    assert "str" in summarize_payload_shape('{"characters": []}')


def test_attempt_reporter_emits_shape_summary_and_dumps_raw_response(tmp_path):
    events = []
    reporter = build_attempt_reporter(
        lambda message, level, payload: events.append((message, level, payload)),
        job_id="job-1",
        stage="characters",
        base_dir=tmp_path,
    )

    reporter(
        {
            "attempt": 1,
            "max_attempts": 2,
            "ok": False,
            "error": "relationships 必须是数组",
            "elapsed_seconds": 12.5,
            "payload": {"characters": [{"name": "林穗"}], "relations": {"林穗-周谨": {}}},
        }
    )

    assert len(events) == 1
    message, level, payload = events[0]
    assert level == "warning"
    assert "第 1/2 次模型响应未通过结构校验" in message
    assert "relationships 必须是数组" not in message
    assert "顶层对象/2键" in message
    assert "林穗" not in message
    assert payload["debug_path"]

    dumped = json.loads(Path(payload["debug_path"]).read_text(encoding="utf-8"))
    assert dumped["stage"] == "characters"
    assert dumped["error"] == "relationships 必须是数组"
    assert dumped["attempt"] == 1
    assert dumped["elapsed_seconds"] == 12.5
    assert dumped["response"]["relations"] == {"林穗-周谨": {}}


def test_attempt_reporter_dumps_each_failed_attempt_into_its_own_file(tmp_path):
    events = []
    reporter = build_attempt_reporter(
        lambda message, level, payload: events.append(payload),
        job_id="job-2",
        stage="characters",
        base_dir=tmp_path,
    )

    reporter({"attempt": 1, "max_attempts": 2, "ok": False, "error": "坏", "elapsed_seconds": 1.0, "payload": {"characters": []}})
    reporter({"attempt": 2, "max_attempts": 2, "ok": False, "error": "仍然坏", "elapsed_seconds": 2.0, "payload": {"characters": []}})

    dumped_paths = sorted(
        path.name for path in (tmp_path / "job-2" / "llm_debug").glob("*.json")
    )
    assert dumped_paths == ["characters-attempt1.json", "characters-attempt2.json"]
    assert events[1]["debug_path"].endswith("characters-attempt2.json")


def test_attempt_reporter_keeps_the_first_success_out_of_the_job_log(tmp_path):
    events = []
    reporter = build_attempt_reporter(
        lambda message, level, payload: events.append((message, level, payload)),
        job_id="job-3",
        stage="characters",
        base_dir=tmp_path,
    )

    reporter({"attempt": 1, "max_attempts": 2, "ok": True, "elapsed_seconds": 3.0, "payload": {}})

    assert events == []


def test_attempt_reporter_reports_repair_success_and_missing_payload(tmp_path):
    events = []
    reporter = build_attempt_reporter(
        lambda message, level, payload: events.append((message, level, payload)),
        job_id="job-4",
        stage="characters",
        base_dir=tmp_path,
    )

    reporter({"attempt": 2, "max_attempts": 2, "ok": True, "elapsed_seconds": 4.0, "payload": {"characters": []}})
    reporter({"attempt": 2, "max_attempts": 2, "ok": False, "error": "LLM_BASE_URL is required.", "elapsed_seconds": 0.5, "payload": None})

    assert "第 2/2 次模型响应通过结构校验" in events[0][0]
    assert events[0][1] == "info"
    assert "LLM_BASE_URL is required." not in events[1][0]
    assert "模型响应未通过结构校验" in events[1][0]
    assert events[1][1] == "warning"
    assert events[1][2]["debug_path"] is None


def test_format_request_summary_shows_the_effective_generation_limits():
    summary = format_request_summary(
        {
            "model": "claude-sonnet-5",
            "protocol": "chat_completions",
            "max_tokens": 32768,
            "reasoning_effort": "high",
            "response_format": "json_schema",
            "input_chars": 15000,
        }
    )

    assert "model=claude-sonnet-5" in summary
    assert "max_tokens=32768" in summary
    assert "reasoning_effort=high" in summary
    assert "response_format=json_schema" in summary
    assert "输入=15000 字符" in summary
    assert format_request_summary(None) == ""


def test_attempt_reporter_includes_the_request_parameters(tmp_path):
    events = []
    reporter = build_attempt_reporter(
        lambda message, level, payload: events.append((message, level, payload)),
        job_id="job-5",
        stage="characters",
        base_dir=tmp_path,
    )

    reporter(
        {
            "attempt": 1,
            "max_attempts": 2,
            "ok": False,
            "error": "relationships 必须是数组",
            "elapsed_seconds": 320.7,
            "payload": {"characters": [], "relations": {}},
            "request": {
                "model": "claude-sonnet-5",
                "protocol": "chat_completions",
                "max_tokens": 32768,
                "reasoning_effort": "high",
                "response_format": "json_object",
                "input_chars": 15000,
            },
        }
    )

    message, _level, payload = events[0]
    assert "max_tokens=32768" in message
    assert "reasoning_effort=high" in message
    assert payload["request"]["max_tokens"] == 32768
    dumped = json.loads(Path(payload["debug_path"]).read_text(encoding="utf-8"))
    assert dumped["request"]["reasoning_effort"] == "high"


def test_attempt_reporter_dumps_the_truncated_output_fragment(tmp_path):
    events = []
    reporter = build_attempt_reporter(
        lambda message, level, payload: events.append((message, level, payload)),
        job_id="job-6",
        stage="characters",
        base_dir=tmp_path,
    )

    reporter(
        {
            "attempt": 1,
            "max_attempts": 2,
            "ok": False,
            "error": "模型输出被输出上限截断（finish_reason=length，已返回 120 字符）。",
            "elapsed_seconds": 88.0,
            "payload": None,
            "truncated_text": '{"characters": [{"name": "林穗"',
        }
    )

    message, _level, payload = events[0]
    path = Path(payload["truncated_path"])
    assert path.name == "characters-attempt1-truncated.txt"
    assert path.read_text(encoding="utf-8") == '{"characters": [{"name": "林穗"'
    assert "截断片段已保存" in message
    assert payload["debug_path"] is None


def test_set_attempt_reporter_returns_the_previous_reporter():
    def first(_info):
        return None

    previous = set_attempt_reporter(first)
    try:
        assert active_attempt_reporter() is first
        assert set_attempt_reporter(None) is first
        assert active_attempt_reporter() is None
    finally:
        set_attempt_reporter(previous)
