# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio
import json
import pytest
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
    assert "模型响应未通过结构校验" in message
    assert "顶层对象/2键" in message
    assert "relationships 必须是数组" not in message
    assert payload["debug_path"]

    dumped = json.loads(Path(payload["debug_path"]).read_text(encoding="utf-8"))
    assert dumped["stage"] == "characters"
    assert dumped["attempt"] == 1
    assert dumped["elapsed_seconds"] == 12.5
    assert dumped["response"]["relations"] == {"林穗-周谨": {}}
    assert dumped["error"] == "relationships 必须是数组"


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
    assert "模型响应未通过结构校验" in events[1][0]
    assert "LLM_BASE_URL is required." not in events[1][0]
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
@pytest.mark.parametrize("failure", [None, RuntimeError("boom"), asyncio.CancelledError()])
@pytest.mark.asyncio
async def test_worker_installs_attempt_reporter_and_restores_it_even_on_failure(monkeypatch, tmp_path, failure):
    from core.job import CrawlJob
    from creative import phase4_worker, llm_observability

    events = []
    previous = set_attempt_reporter(lambda info: None)
    outer = active_attempt_reporter()
    monkeypatch.setattr(llm_observability, "LLM_DEBUG_BASE_DIR", tmp_path)
    monkeypatch.setattr(phase4_worker, "emit", lambda job, kind, message, level, payload: events.append(payload))

    async def run_once(job):
        assert active_attempt_reporter() is not outer
        active_attempt_reporter()({
            "attempt": 1, "max_attempts": 2, "ok": False, "payload": {"relationships": 42},
            "error": "invalid shape",
        })
        if failure is not None:
            raise failure

    monkeypatch.setattr(phase4_worker, "_run_once", run_once)
    try:
        job = CrawlJob(job_id="restore-test", platform="creative", crawler_type="characters", extra={"action": "characters"})
        if failure is None:
            await phase4_worker.run(job)
        else:
            with pytest.raises(type(failure)):
                await phase4_worker.run(job)
        assert active_attempt_reporter() is outer
        assert events[0]["stage"] == "characters"
        assert json.loads(Path(events[0]["debug_path"]).read_text(encoding="utf-8"))["response"] == {"relationships": 42}
    finally:
        set_attempt_reporter(previous)


def test_repeated_batch_attempts_keep_every_response_and_truncated_fragment(tmp_path):
    events = []
    reporter = build_attempt_reporter(
        lambda message, level, payload: events.append(payload),
        job_id="batch-job", stage="outlines", base_dir=tmp_path,
    )
    for fragment in ["  first\n", " second\n"]:
        reporter({
            "attempt": 1, "max_attempts": 2, "ok": False,
            "payload": {"part": fragment}, "truncated_text": fragment,
        })
    assert len({event["debug_path"] for event in events}) == 2
    assert len({event["truncated_path"] for event in events}) == 2
    assert [Path(event["truncated_path"]).read_text(encoding="utf-8") for event in events] == ["  first\n", " second\n"]
@pytest.mark.asyncio
@pytest.mark.parametrize("truncated", [False, True])
async def test_attempt_logs_and_events_exclude_supplier_echo_but_files_keep_it(tmp_path, caplog, truncated):
    from analysis.llm import LLMError, LLMOutputTruncatedError
    from creative.imitation_service import complete_with_one_repair

    secret = "PRIVATE_SYSTEM_PROMPT_DO_NOT_DISCLOSE"
    raw = "  " + secret + "\n"
    events = []
    reporter = build_attempt_reporter(
        lambda message, level, payload: events.append((message, payload)),
        job_id="safe-diagnostic", stage="characters", base_dir=tmp_path,
    )
    class Provider:
        last_request_summary = {"protocol": "responses", "model": "model", "system_prompt": secret}
        async def complete_json(self, messages):
            if truncated:
                raise LLMOutputTruncatedError(secret, finish_reason="length", returned_chars=len(raw), partial_text=raw)
            return {secret: {"echo": secret}}
    def validate(payload):
        raise ValueError(secret)

    with pytest.raises(LLMError) as caught:
        await complete_with_one_repair(Provider(), [], validate, max_attempts=1, on_attempt=reporter)
    assert secret not in caplog.text
    assert secret not in repr(events)
    event = events[0][1]
    if truncated:
        assert caught.value.partial_text == raw
        assert caught.value.finish_reason == "length"
        assert caught.value.returned_chars == len(raw)
        assert Path(event["truncated_path"]).read_text(encoding="utf-8") == raw
    else:
        dumped = json.loads(Path(event["debug_path"]).read_text(encoding="utf-8"))
        assert dumped["response"] == {secret: {"echo": secret}}
        assert dumped["error"] == secret


@pytest.mark.asyncio
async def test_failed_attempt_reporters_do_not_log_callback_exception_text(caplog):
    from creative.imitation_service import complete_with_one_repair
    secret = "PRIVATE_CALLBACK_CONTENT"
    class Provider:
        async def complete_json(self, messages):
            return {"ok": True}
    def broken(info):
        raise ValueError(secret)
    result, _ = await complete_with_one_repair(Provider(), [], lambda value: value, on_attempt=broken)
    assert result == {"ok": True}
    assert secret not in caplog.text

    reporter = build_attempt_reporter(
        lambda *args: broken(None), job_id="broken-notify", stage="characters",
    )
    reporter({"attempt": 2, "max_attempts": 2, "ok": True})
    assert secret not in caplog.text
