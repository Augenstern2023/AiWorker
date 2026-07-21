# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from core.events import WorkerEvent
from core.job import CrawlJob


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


@dataclass
class JobState:
    job: CrawlJob
    status: str = "pending"
    pid: int | None = None
    return_code: int | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    message: str = ""
    progress: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def job_id(self) -> str:
        return self.job.job_id

    @property
    def platform(self) -> str:
        return self.job.platform

    def add_event(self, event: WorkerEvent) -> None:
        data = event.to_dict()
        self.events.append(data)
        self.events = self.events[-500:]
        self.message = event.message
        if event.event == "progress" and isinstance(event.payload, dict):
            self.progress = dict(event.payload)
        if event.event == "job_started":
            self.status = "running"
            self.started_at = self.started_at or event.ts
        elif event.event == "job_finished":
            self.status = "succeeded"
            self.finished_at = event.ts
        elif event.event == "job_failed":
            self.status = "failed"
            self.finished_at = event.ts
        elif event.event == "job_cancelled":
            self.status = "cancelled"
            self.finished_at = event.ts

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["job"] = self.job.to_dict()
        return data
