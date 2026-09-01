# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import json
from pathlib import Path

from agents import douyin_rank_worker
from core.job import CrawlJob


def test_douyin_rank_worker_runs_top10_analysis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events = []
    analysis_calls = []

    async def fake_run_douyin_rank(args):
        output_path = Path(args.output_dir) / "douyin_series_rank_20260702_101010.md"
        output_path.write_text("# rank", encoding="utf-8")
        output_path.with_suffix(".json").write_text(
            json.dumps({"platform": "dy", "records": [{"title": "测试剧", "rank_category": "短剧"}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return output_path

    def fake_analyze_rank_file(rank_json_path, output_dir=None, options=None):
        analysis_calls.append((Path(rank_json_path), Path(output_dir), options))
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        manifest_path = target / "douyin_analysis_manifest.json"
        overview_path = target / "douyin_analysis_overview.md"
        overview_json_path = target / "douyin_llm_overview.json"
        item_json = target / "01_测试剧.json"
        item_md = target / "01_测试剧.md"
        for path in (manifest_path, overview_path, overview_json_path, item_json, item_md):
            path.write_text("{}", encoding="utf-8")
        return {
            "manifest": str(manifest_path),
            "overview": str(overview_path),
            "overview_json": str(overview_json_path),
            "items": [{"json": str(item_json), "markdown": str(item_md), "analysis": {"title": "测试剧"}}],
        }

    monkeypatch.setattr(douyin_rank_worker, "run_douyin_rank", fake_run_douyin_rank)
    monkeypatch.setattr(douyin_rank_worker, "analyze_rank_file", fake_analyze_rank_file)
    monkeypatch.setattr(douyin_rank_worker, "emit_event", lambda event, **kwargs: events.append((event, kwargs)))

    job = CrawlJob(job_id="job-dy", platform="dy", crawler_type="rank")

    assert douyin_rank_worker.run_job(job) == 0
    assert len(analysis_calls) == 1
    assert analysis_calls[0][0].name == "douyin_series_rank_20260702_101010.json"
    assert analysis_calls[0][1].resolve() == tmp_path / "data" / "dy" / "jobs" / "job-dy" / "analysis"
    assert analysis_calls[0][2].top_n == 10
    assert analysis_calls[0][2].with_llm is True
    assert any("自动分析完成" in kwargs.get("message", "") for event, kwargs in events if event == "log")


def test_douyin_rank_worker_can_skip_auto_analysis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    analysis_calls = []

    async def fake_run_douyin_rank(args):
        output_path = Path(args.output_dir) / "douyin_series_rank_20260702_101010.md"
        output_path.write_text("# rank", encoding="utf-8")
        output_path.with_suffix(".json").write_text(json.dumps({"platform": "dy", "records": []}), encoding="utf-8")
        return output_path

    monkeypatch.setattr(douyin_rank_worker, "run_douyin_rank", fake_run_douyin_rank)
    monkeypatch.setattr(douyin_rank_worker, "analyze_rank_file", lambda *args, **kwargs: analysis_calls.append(args))
    monkeypatch.setattr(douyin_rank_worker, "emit_event", lambda *args, **kwargs: None)

    job = CrawlJob(
        job_id="job-dy",
        platform="dy",
        crawler_type="rank",
        extra={"DOUYIN_AUTO_ANALYZE_TOP10": False},
    )

    assert douyin_rank_worker.run_job(job) == 0
    assert analysis_calls == []


def test_douyin_rank_worker_pushes_after_rank_artifact_is_ready(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pushes = []

    async def fake_run_douyin_rank(args):
        output_path = Path(args.output_dir) / "douyin_series_rank_20260831_101010.md"
        output_path.write_text("# rank", encoding="utf-8")
        output_path.with_suffix(".json").write_text(
            json.dumps({"captured_at": "2026-08-31 10:10:10", "records": [{"title": "测试剧"}]}),
            encoding="utf-8",
        )
        return output_path

    monkeypatch.setattr(douyin_rank_worker, "run_douyin_rank", fake_run_douyin_rank)
    monkeypatch.setattr(douyin_rank_worker, "analyze_rank_file", lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(douyin_rank_worker, "deliver_rank_update", lambda platform, path: pushes.append((platform, Path(path))) or {"status": "sent"})
    monkeypatch.setattr(douyin_rank_worker, "emit_event", lambda *args, **kwargs: None)

    job = CrawlJob(
        job_id="job-dy",
        platform="dy",
        crawler_type="rank",
        extra={"DOUYIN_AUTO_ANALYZE_TOP10": False},
    )

    assert douyin_rank_worker.run_job(job) == 0
    assert [(platform, path.resolve()) for platform, path in pushes] == [
        ("dy", tmp_path / "data/dy/jobs/job-dy/rank/douyin_series_rank_20260831_101010.json")
    ]
