# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.storage_agent import StorageAgent
from rag.schema import Artifact

SUPPORTED_SUFFIXES = {".json", ".md", ".markdown", ".txt"}
SKIPPED_DIRS = {"media", "__pycache__"}


def load_job_artifacts(
    job_id: str,
    platform: str,
    root: str | Path = ".",
    storage: StorageAgent | None = None,
) -> list[Artifact]:
    root_path = Path(root)
    job_dir = root_path / "data" / platform / "jobs" / job_id
    candidates = _artifact_paths_from_storage(root_path, job_id, storage)
    if job_dir.exists():
        candidates.extend(_scan_job_dir(job_dir))
    unique_paths = sorted({path.resolve() for path in candidates if path.exists() and path.suffix.lower() in SUPPORTED_SUFFIXES})
    return [_load_artifact(path, root_path, job_id, platform) for path in unique_paths]


def _artifact_paths_from_storage(root: Path, job_id: str, storage: StorageAgent | None) -> list[Path]:
    if storage is None:
        db_path = root / ".runtime" / "agent_state.db"
        if not db_path.exists():
            return []
        storage = StorageAgent(db_path)
    paths = []
    try:
        rows = storage.list_artifacts(job_id)
    except Exception:
        return []
    for row in rows:
        path = Path(str(row.get("path", "")))
        paths.append(path if path.is_absolute() else root / path)
    return paths


def _scan_job_dir(job_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in job_dir.rglob("*"):
        if any(part in SKIPPED_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            paths.append(path)
    return paths


def _load_artifact(path: Path, root: Path, job_id: str, platform: str) -> Artifact:
    suffix = path.suffix.lower()
    source_type = _infer_source_type(path)
    rel_path = _relative_path(path, root)
    if suffix == ".json":
        text = path.read_text(encoding="utf-8")
        data: Any = json.loads(text)
        return Artifact(
            job_id=job_id,
            platform=platform,
            path=rel_path,
            source_type=source_type,
            content_type="json",
            data=data,
            text=text,
            metadata={"suffix": suffix},
        )
    text = path.read_text(encoding="utf-8", errors="ignore")
    content_type = "markdown" if suffix in {".md", ".markdown"} else "text"
    return Artifact(
        job_id=job_id,
        platform=platform,
        path=rel_path,
        source_type=source_type,
        content_type=content_type,
        data=None,
        text=text,
        metadata={"suffix": suffix},
    )


def _infer_source_type(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if "rank" in parts or "rank" in name or "hot_rank" in name or "series_rank" in name:
        return "rank"
    if "transcripts" in parts or "transcript" in name:
        return "asr"
    if "analysis" in parts or "analysis" in name:
        return "analysis"
    if "comment" in name:
        return "comment"
    if "library" in parts:
        return "library"
    return "artifact"


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()

