# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class RankSnapshot:
    platform: str
    job_id: str
    source_path: Path
    captured_at: datetime
    records: list[dict[str, Any]]
    validation: dict[str, Any]

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "job_id": self.job_id,
            "source_path": _relative_path(self.source_path, root),
            "captured_at": self.captured_at.strftime("%Y-%m-%d %H:%M:%S"),
            "date": self.captured_at.date().isoformat(),
            "record_count": len(self.records),
        }


class RankSnapshotSource(Protocol):
    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        raise NotImplementedError

    def revision(self, platform: str) -> str:
        raise NotImplementedError


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
