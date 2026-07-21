# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WorkerEvent:
    event: str
    job_id: str
    worker_id: str
    platform: str
    level: str
    message: str
    ts: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def emit_event(
    event: str,
    job_id: str = "",
    worker_id: str = "",
    platform: str = "",
    level: str = "info",
    message: str = "",
    payload: dict[str, Any] | None = None,
    log_dir: str | Path = ".runtime/logs",
) -> WorkerEvent:
    worker_event = WorkerEvent(
        event=event,
        job_id=job_id,
        worker_id=worker_id or f"{platform or 'worker'}-worker",
        platform=platform,
        level=level,
        message=message,
        payload=payload or {},
    )
    line = worker_event.to_json()
    print(line, flush=True)
    if job_id:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        with (Path(log_dir) / f"{job_id}.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + os.linesep)
    return worker_event


def parse_event_line(line: str, job_id: str = "", platform: str = "") -> WorkerEvent:
    raw = line.strip()
    if not raw:
        return WorkerEvent(
            event="log",
            job_id=job_id,
            worker_id=f"{platform or 'worker'}-worker",
            platform=platform,
            level="info",
            message="",
        )

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "event" in data:
            return WorkerEvent(
                event=str(data.get("event") or "log"),
                job_id=str(data.get("job_id") or job_id),
                worker_id=str(data.get("worker_id") or f"{platform or 'worker'}-worker"),
                platform=str(data.get("platform") or platform),
                level=str(data.get("level") or "info"),
                message=str(data.get("message") or ""),
                ts=float(data.get("ts") or time.time()),
                payload=dict(data.get("payload") or {}),
            )
    except json.JSONDecodeError:
        pass

    return WorkerEvent(
        event="log",
        job_id=job_id,
        worker_id=f"{platform or 'worker'}-worker",
        platform=platform,
        level="info",
        message=raw,
    )
