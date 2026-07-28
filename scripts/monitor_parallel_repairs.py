# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Atomic status updates and live JSONL monitoring for parallel repair tasks."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _read_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tasks": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def update_status(path: Path, task: str, state: str, message: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_status(path)
    payload.setdefault("tasks", {})[task] = {
        "state": state,
        "message": message,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return payload


def watch(path: Path, interval: float) -> None:
    previous = ""
    while True:
        current = json.dumps(_read_status(path), ensure_ascii=False, sort_keys=True)
        if current != previous:
            print(current, flush=True)
            previous = current
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--task")
    parser.add_argument("--set", dest="state")
    parser.add_argument("--message", default="")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    arguments = parser.parse_args()
    if arguments.watch:
        watch(arguments.status_file, arguments.interval)
        return
    if not arguments.task or not arguments.state:
        parser.error("--task and --set are required unless --watch is used")
    print(
        json.dumps(
            update_status(arguments.status_file, arguments.task, arguments.state, arguments.message),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
