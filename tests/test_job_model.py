# -*- coding: utf-8 -*-
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
