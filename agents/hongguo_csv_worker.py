# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.events import emit_event
from core.job import CrawlJob
from agents.rank_zentao_delivery import deliver_rank_update
from analysis.rank_importer import sync_rank_source


DEFAULT_EXPORTS_ROOT = Path(os.environ.get("HONGGUO_EXPORTS_DIR", r"E:\hg\exports"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Hongguo rank snapshots from exported CSV files.")
    parser.add_argument("--job-file", required=True, help="Path to CrawlJob JSON file")
    parser.add_argument("--exports-root", default=str(DEFAULT_EXPORTS_ROOT), help="Hongguo CSV exports root")
    return parser.parse_args()


def discover_rank_csvs(exports_root: str | Path) -> list[Path]:
    root = Path(exports_root)
    if not root.exists():
        return []
    return sorted(root.glob("*/top10_rankings.csv"), key=lambda path: (snapshot_date(path), str(path)))


def snapshot_date(path: Path) -> str:
    for part in reversed(path.parts):
        if re.fullmatch(r"\d{8}", part):
            return part
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d")


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f) if isinstance(row, dict)]


def write_snapshot(job: CrawlJob, csv_path: Path, rows: list[dict[str, Any]]) -> Path:
    date_text = snapshot_date(csv_path)
    captured_at = datetime.strptime(date_text + " 00:00:00", "%Y%m%d %H:%M:%S")
    output_dir = Path("data") / "hg" / "jobs" / job.job_id / "rank"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"hongguo_rank_{date_text}_000000.json"
    rank_types = sorted({str(row.get("榜单类型") or "").strip() for row in rows if str(row.get("榜单类型") or "").strip()})
    output_path.write_text(
        json.dumps(
            {
                "job_id": job.job_id,
                "platform": "hg",
                "captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "csv_import",
                "source_path": str(csv_path),
                "snapshot_date": date_text,
                "rank_type_count": len(rank_types),
                "record_count": len(rows),
                "records": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output_path


def run_job(job: CrawlJob, exports_root: str | Path = DEFAULT_EXPORTS_ROOT) -> int:
    worker_id = "hg-csv-worker"
    csv_paths = discover_rank_csvs(exports_root)
    if not csv_paths:
        emit_event(
            "job_failed",
            job_id=job.job_id,
            worker_id=worker_id,
            platform="hg",
            level="error",
            message=f"未找到红果 CSV 榜单：{exports_root}",
        )
        return 1

    selected_paths = csv_paths[-2:]
    total_records = 0
    artifacts: list[str] = []
    for csv_path in selected_paths:
        rows = read_csv_rows(csv_path)
        total_records += len(rows)
        output_path = write_snapshot(job, csv_path, rows)
        artifacts.append(str(output_path))
        emit_event(
            "artifact",
            job_id=job.job_id,
            worker_id=worker_id,
            platform="hg",
            level="info",
            message=f"导入红果 CSV 榜单：{csv_path}，记录数 {len(rows)}",
            payload={
                "path": str(output_path),
                "artifact_type": "json",
                "size": output_path.stat().st_size,
                "source_path": str(csv_path),
                "records": len(rows),
            },
        )
        try:
            sync_result = sync_rank_source(
                "hg",
                csv_path,
                project_root=Path.cwd(),
                hongguo_exports_root=exports_root,
            )
            emit_event(
                "log",
                job_id=job.job_id,
                worker_id=worker_id,
                platform="hg",
                level="info",
                message="红果榜单已同步到集中数据库",
                payload={
                    "status": sync_result.status,
                    "source_ref": sync_result.source_ref,
                    "record_count": sync_result.record_count,
                },
            )
        except Exception as exc:
            emit_event(
                "job_failed",
                job_id=job.job_id,
                worker_id=worker_id,
                platform="hg",
                level="error",
                message="红果榜单已生成，但集中数据库同步失败",
                payload={
                    "sync_status": "central_sync_failed",
                    "error_type": type(exc).__name__,
                },
            )
            return 1

    latest_path = selected_paths[-1]
    try:
        delivery = deliver_rank_update(
            "hg",
            latest_path,
            report_date=datetime.strptime(snapshot_date(latest_path), "%Y%m%d").date().isoformat(),
        )
        emit_event(
            "log",
            job_id=job.job_id,
            worker_id=worker_id,
            platform="hg",
            level="info",
            message=f"红果榜单禅道推送：{delivery.get('status', 'unknown')}",
            payload={
                "part_key": delivery.get("part_key", "hongguo-rank"),
                "status": delivery.get("status", "unknown"),
                "reason": delivery.get("reason", ""),
            },
        )
    except Exception as exc:
        emit_event(
            "log",
            job_id=job.job_id,
            worker_id=worker_id,
            platform="hg",
            level="warning",
            message=f"红果榜单已更新，但禅道推送失败: {exc}",
            payload={"error_type": type(exc).__name__},
        )

    emit_event(
        "job_finished",
        job_id=job.job_id,
        worker_id=worker_id,
        platform="hg",
        level="info",
        message=f"红果 CSV 榜单导入完成，共 {len(selected_paths)} 个快照、{total_records} 条记录",
        payload={"snapshots": len(selected_paths), "records": total_records, "artifacts": artifacts},
    )
    return 0


def main() -> int:
    args = parse_args()
    job = CrawlJob.load(args.job_file)
    if job.platform != "hg":
        emit_event(
            "job_failed",
            job_id=job.job_id,
            worker_id="hg-csv-worker",
            platform=job.platform,
            level="error",
            message=f"红果 CSV Worker 不支持平台 {job.platform}",
        )
        return 2
    return run_job(job, exports_root=args.exports_root)


if __name__ == "__main__":
    raise SystemExit(main())
