# -*- coding: utf-8 -*-
"""Tests for job-aware file paths."""

from tools.async_file_writer import AsyncFileWriter
from var import job_id_var


def test_file_path_without_job_id():
    token = job_id_var.set("")
    try:
        writer = AsyncFileWriter(platform="dy", crawler_type="search")
        path = writer._get_file_path("json", "contents")
    finally:
        job_id_var.reset(token)

    assert path.startswith("data/dy/json/")
    assert "jobs" not in path


def test_file_path_with_job_id():
    token = job_id_var.set("job-1")
    try:
        writer = AsyncFileWriter(platform="dy", crawler_type="search")
        path = writer._get_file_path("json", "contents")
    finally:
        job_id_var.reset(token)

    assert path == "data/dy/jobs/job-1/json/search_contents.json"
