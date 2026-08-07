# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import argparse
from pathlib import Path

from analysis.weekly_report_service import WeeklyReportService
from core.events import emit_event
from core.job import CrawlJob


def run_job(job: CrawlJob) -> int:
    worker_id = "weekly-report-worker"
    emit_event("log", job_id=job.job_id, worker_id=worker_id, platform="analytics", message="开始生成周度流行趋势报告")
    service = WeeklyReportService(root=Path.cwd())
    reports = service.generate_all_latest(with_llm=True)
    for report in reports:
        if report.get("status") in {"ready", "unchanged"}:
            for path, artifact_type in ((report.get("markdown_path"), "markdown"), (report.get("excel_path"), "excel")):
                if path and Path(str(path)).is_file():
                    emit_event("artifact", job_id=job.job_id, worker_id=worker_id, platform="analytics", message=f"生成周报产物: {path}", payload={"path": str(path), "artifact_type": artifact_type})
        else:
            emit_event("log", job_id=job.job_id, worker_id=worker_id, platform="analytics", level="warning", message=f"{report.get('platform', '-') } 周报未生成：数据不足")
    emit_event("log", job_id=job.job_id, worker_id=worker_id, platform="analytics", message="周度流行趋势报告任务结束", payload={"reports": reports})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate weekly trend reports.")
    parser.add_argument("--job-file", required=True)
    args = parser.parse_args()
    return run_job(CrawlJob.load(args.job_file))


if __name__ == "__main__":
    raise SystemExit(main())
