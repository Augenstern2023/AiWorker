# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Worker for asynchronous data archive and deletion operations."""

from __future__ import annotations

import argparse
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Iterator

from core.events import emit_event
from core.job import CrawlJob


def emit(
    job: CrawlJob,
    event: str,
    message: str,
    level: str = "info",
    payload: dict[str, Any] | None = None,
) -> None:
    emit_event(event, job.job_id, "data-file-operation-worker", "data", level, message, payload or {})


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _snapshot_paths(snapshot_path: Path) -> Iterator[str]:
    with snapshot_path.open("r", encoding="utf-8") as snapshot:
        for line in snapshot:
            value = line.strip()
            if not value:
                continue
            item = json.loads(value)
            if isinstance(item, str) and item.strip():
                yield item.strip()


def _resolve_selected_file(path_value: str, workspace_root: Path, data_root: Path) -> Path:
    raw_path = Path(path_value)
    candidate = raw_path if raw_path.is_absolute() else workspace_root / raw_path
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, data_root):
        raise ValueError("文件路径不在数据目录内")
    if not resolved.is_file():
        raise FileNotFoundError("文件不存在")
    return resolved


def _progress(job: CrawlJob, completed: int, total: int, succeeded: int, failures: list[dict[str, str]]) -> None:
    emit(
        job,
        "progress",
        f"已处理 {completed}/{total} 个文件",
        payload={
            "completed": completed,
            "total": total,
            "succeeded": succeeded,
            "failed": len(failures),
            "percent": round(completed * 100 / total, 1) if total else 100,
        },
    )


def run(job: CrawlJob) -> dict[str, Any]:
    extra = job.extra or {}
    operation = str(extra.get("operation") or "").strip().lower()
    if operation not in {"archive", "delete"}:
        raise ValueError("未知的数据操作")

    workspace_root = Path(str(extra.get("workspace_root") or ".")).resolve()
    data_root = (workspace_root / "data").resolve()
    snapshot_path = Path(str(extra.get("snapshot_path") or "")).resolve()
    if not snapshot_path.is_file():
        raise FileNotFoundError("任务选择快照不存在")
    paths = _snapshot_paths(snapshot_path)
    total = int(extra.get("total") or 0)
    succeeded = 0
    completed = 0
    failures: list[dict[str, str]] = []
    output_path = Path(str(extra.get("output_path") or "")).resolve()
    archive: zipfile.ZipFile | None = None
    temporary_archive = output_path.with_suffix(output_path.suffix + ".partial")

    if operation == "archive":
        if output_path.suffix.lower() != ".zip":
            raise ValueError("归档任务缺少 ZIP 输出路径")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_archive.unlink(missing_ok=True)
        archive = zipfile.ZipFile(
            temporary_archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        )

    emit(job, "job_started", f"开始{ '打包下载' if operation == 'archive' else '删除' } {total} 个文件")
    _progress(job, completed, total, succeeded, failures)
    try:
        for path_value in paths:
            completed += 1
            try:
                file_path = _resolve_selected_file(path_value, workspace_root, data_root)
                if archive is not None:
                    archive.write(file_path, arcname=file_path.relative_to(data_root).as_posix())
                else:
                    file_path.unlink()
                succeeded += 1
            except (OSError, ValueError) as exc:
                failures.append({"path": path_value, "message": str(exc)})
                emit(job, "log", f"处理失败：{path_value}，{exc}", "warning")
            _progress(job, completed, total, succeeded, failures)
    finally:
        if archive is not None:
            archive.close()

    if operation == "archive":
        if completed == 0:
            temporary_archive.unlink(missing_ok=True)
        else:
            os.replace(temporary_archive, output_path)
            emit(
                job,
                "artifact",
                output_path.name,
                payload={"path": str(output_path), "artifact_type": "zip"},
            )

    result = {
        "operation": operation,
        "completed": completed,
        "total": total,
        "succeeded": succeeded,
        "failed": len(failures),
        "failures": failures,
    }
    report_path = output_path.with_suffix(".json")
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(job, "artifact", report_path.name, payload={"path": str(report_path), "artifact_type": "json"})
    emit(job, "job_finished", f"处理完成：成功 {succeeded}，失败 {len(failures)}", payload=result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a data file archive or delete operation.")
    parser.add_argument("--job-file", required=True)
    return parser.parse_args()


def main() -> int:
    job = CrawlJob.load(parse_args().job_file)
    try:
        run(job)
    except Exception as exc:
        emit(job, "job_failed", str(exc), "error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
