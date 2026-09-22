# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Measure a running SemIf sidecar with a real screenplay text file."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from screenplay_review.semif_client import SemIfClient
from screenplay_review.semif_contract import SemIfScoreRequest
from screenplay_review.semif_rules import criteria
from screenplay_review.semif_state import build_semif_state_windows
from screenplay_review.service import build_text_source


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark SemIf screenplay scoring.")
    parser.add_argument("--text-file", required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    if args.repeat < 1:
        raise ValueError("--repeat must be positive")
    text = args.text_file.read_text(encoding="utf-8")
    source = build_text_source(text, source_filename=args.text_file.name)
    windows = build_semif_state_windows(source)
    measurements = []
    async with SemIfClient(args.url, timeout_seconds=900, max_retries=0) as client:
        health = await client.health()
        for repeat_index in range(args.repeat):
            for window in windows:
                request = SemIfScoreRequest(
                    request_id=f"benchmark-{repeat_index}-{window.id}",
                    mode="shared",
                    state=window.state,
                    criteria=criteria(),
                    state_refs=[dict(item) for item in window.state_refs],
                )
                started = time.perf_counter()
                response = await client.score(request)
                elapsed = time.perf_counter() - started
                measurements.append(
                    {
                        "repeat": repeat_index + 1,
                        "state_id": window.id,
                        "episode_number": window.episode_number,
                        "estimated_state_tokens": window.estimated_tokens,
                        "criteria": len(response.scores),
                        "wall_seconds": elapsed,
                        "sidecar_timing": response.timing,
                        "input_tokens": [score.input_tokens for score in response.scores],
                    }
                )
    timings = [item["wall_seconds"] for item in measurements]
    return {
        "schema_version": "screenplay-semif-benchmark-v1",
        "sidecar": health.model_dump(mode="json"),
        "text_file": str(args.text_file.resolve()),
        "characters": len(text),
        "state_windows": len(windows),
        "repeats": args.repeat,
        "wall_seconds": {
            "total": sum(timings),
            "mean_per_state": statistics.mean(timings),
            "median_per_state": statistics.median(timings),
            "maximum_per_state": max(timings),
        },
        "measurements": measurements,
    }


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(args))
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
