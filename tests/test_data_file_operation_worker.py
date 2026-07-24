# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests/test_data_file_operation_worker.py
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

import json
import zipfile

from agents.data_file_operation_worker import run
from core.job import CrawlJob


def _operation_job(tmp_path, operation, paths):
    data_root = tmp_path / "data"
    snapshot = tmp_path / "selection.paths"
    snapshot.write_text("".join(f"{json.dumps(path)}\n" for path in paths), encoding="utf-8")
    return CrawlJob(
        job_id="data-operation-test",
        platform="data",
        crawler_type="file_operation",
        extra={
            "operation": operation,
            "snapshot_path": str(snapshot),
            "workspace_root": str(tmp_path),
            "output_path": str(tmp_path / "output.zip"),
        },
    ), data_root


def test_archive_operation_creates_zip_from_snapshot(tmp_path):
    data_root = tmp_path / "data"
    source = data_root / "jobs" / "demo"
    source.mkdir(parents=True)
    (source / "first.json").write_text('{"first": true}', encoding="utf-8")
    (source / "second.md").write_text("# second", encoding="utf-8")
    job, _ = _operation_job(
        tmp_path,
        "archive",
        ["data/jobs/demo/first.json", "data/jobs/demo/second.md"],
    )

    result = run(job)

    assert result["failed"] == 0
    with zipfile.ZipFile(job.extra["output_path"]) as archive:
        assert archive.namelist() == ["jobs/demo/first.json", "jobs/demo/second.md"]


def test_delete_operation_reports_missing_files_without_stopping(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    target = data_root / "remove.json"
    target.write_text("{}", encoding="utf-8")
    job, _ = _operation_job(
        tmp_path,
        "delete",
        ["data/remove.json", "data/missing.json"],
    )

    result = run(job)

    assert not target.exists()
    assert result["succeeded"] == 1
    assert result["failed"] == 1
