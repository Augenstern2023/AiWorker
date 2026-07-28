# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def build_worker_command(job_file: str | Path, platform: str = "", crawler_type: str = "") -> list[str]:
    if platform == "data" and crawler_type == "file_operation":
        return [sys.executable, "-m", "agents.data_file_operation_worker", "--job-file", str(job_file)]
    if platform == "creative" and crawler_type == "breakdown_generate":
        return [sys.executable, "-m", "creative.breakdown_worker", "--job-file", str(job_file)]
    if platform == "creative" and crawler_type == "phase4_generate":
        return [sys.executable, "-m", "creative.phase4_worker", "--job-file", str(job_file)]
    if platform == "hg" and crawler_type == "drama_analysis":
        return [sys.executable, "-m", "agents.hongguo_drama_worker", "--job-file", str(job_file)]
    if platform == "hg" and crawler_type == "drama_download":
        return [sys.executable, "-m", "agents.hongguo_drama_download_worker", "--job-file", str(job_file)]
    if platform == "hg" and crawler_type == "douyin_fallback_analysis":
        return [sys.executable, "-m", "agents.douyin_drama_fallback_worker", "--job-file", str(job_file)]
    if platform == "hg":
        return [sys.executable, "-m", "agents.hongguo_csv_worker", "--job-file", str(job_file)]
    if platform == "dy" and crawler_type == "rank":
        return [sys.executable, "-m", "agents.douyin_rank_worker", "--job-file", str(job_file)]
    if platform == "dy" and crawler_type == "drama_reviews":
        return [sys.executable, "-m", "agents.douyin_review_worker", "--job-file", str(job_file)]
    return [sys.executable, "main.py", "--job-file", str(job_file)]


def build_worker_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def launch_worker(
    job_file: str | Path,
    cwd: str | Path | None = None,
    platform: str = "",
    crawler_type: str = "",
) -> subprocess.Popen:
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        build_worker_command(job_file, platform=platform, crawler_type=crawler_type),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
        env=build_worker_env(),
        cwd=str(cwd) if cwd else None,
        creationflags=creationflags,
    )
