# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Start the D-drive SemIf sidecar and an opt-in AIWorker pilot GUI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEMIF_ROOT = Path("D:/AiWorker/SemIf")
SIDECAR_EXECUTABLE = SEMIF_ROOT / ".venv/Scripts/aiworker-semif.exe"
MODEL_PATH = SEMIF_ROOT / "models/Qwen_Qwen3.5-4B-Q4_K_M.gguf"
MAIN_PYTHON = PROJECT_ROOT / ".venv/Scripts/python.exe"
SIDECAR_URL = "http://127.0.0.1:8765"
EXPECTED_COMMIT = "1f2dea3e25379f9dfc98cb83c324f00ab5deda37"
EXPECTED_MODEL_SHA256 = (
    "13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", choices=("web", "desktop"), default="web")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--startup-timeout", type=int, default=360)
    args = parser.parse_args()
    if not 30 <= args.startup_timeout <= 900:
        parser.error("--startup-timeout must be between 30 and 900 seconds")
    return args


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_health() -> dict[str, Any] | None:
    try:
        with urlopen(f"{SIDECAR_URL}/health", timeout=5) as response:
            health = json.load(response)
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None
    model = health.get("model") or {}
    gguf = model.get("gguf") or {}
    if (
        health.get("status") != "ok"
        or health.get("backend") != "llamacpp"
        or health.get("upstream_commit") != EXPECTED_COMMIT
        or gguf.get("sha256") != EXPECTED_MODEL_SHA256
    ):
        return None
    return health


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(1)
        return client.connect_ex(("127.0.0.1", port)) == 0


def sidecar_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(SEMIF_ROOT / ".venv"),
            "UV_CACHE_DIR": "D:/AiWorker/uv-cache",
            "HF_HOME": "D:/AiWorker/huggingface-cache",
            "HF_HUB_CACHE": "D:/AiWorker/huggingface-cache/hub",
            "SEMIF_GGUF_PATH": str(MODEL_PATH),
            "SEMIF_GGUF_SHA256": EXPECTED_MODEL_SHA256,
            "SEMIF_LLAMA_THREADS": "6",
        }
    )
    return environment


def ensure_sidecar(environment: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    health = verified_health()
    if health:
        return health
    if port_is_open(8765):
        raise RuntimeError(
            "Port 8765 is occupied, but it is not the expected SemIf sidecar."
        )

    log_directory = PROJECT_ROOT / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    stdout_path = log_directory / "semif-sidecar.stdout.log"
    stderr_path = log_directory / "semif-sidecar.stderr.log"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    print(
        "Starting SemIf from D drive. Cold startup can take several minutes...",
        flush=True,
    )
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            [str(SIDECAR_EXECUTABLE)],
            cwd=PROJECT_ROOT / "deploy/semif",
            env=environment,
            stdout=stdout,
            stderr=stderr,
            creationflags=creation_flags,
        )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(2)
        health = verified_health()
        if health:
            return health
        exit_code = process.poll()
        if exit_code is not None:
            tail = ""
            if stderr_path.exists():
                tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise RuntimeError(
                f"SemIf exited during startup with code {exit_code}.\n{tail}"
            )
    raise TimeoutError(
        f"SemIf did not become healthy within {timeout_seconds} seconds. "
        f"See {stderr_path}"
    )


def review_environment(base: dict[str, str]) -> dict[str, str]:
    environment = base.copy()
    environment.update(
        {
            "SCREENPLAY_REVIEW_ENGINE": "semif",
            "SCREENPLAY_REVIEW_SEMIF_URL": SIDECAR_URL,
            "SCREENPLAY_REVIEW_SEMIF_FALLBACK": "legacy",
            "SCREENPLAY_REVIEW_SEMIF_TIMEOUT_SECONDS": "900",
            "SCREENPLAY_REVIEW_SEMIF_MAX_RETRIES": "0",
        }
    )
    return environment


def main() -> int:
    args = parse_args()
    for required in (SIDECAR_EXECUTABLE, MODEL_PATH, MAIN_PYTHON):
        if not required.is_file():
            raise FileNotFoundError(f"Required file not found: {required}")
    actual_sha256 = file_sha256(MODEL_PATH)
    if actual_sha256 != EXPECTED_MODEL_SHA256:
        raise ValueError(f"SemIf model SHA-256 mismatch: {actual_sha256}")

    base_environment = sidecar_environment()
    health = ensure_sidecar(base_environment, args.startup_timeout)
    model = health["model"]
    print(
        json.dumps(
            {
                "status": health["status"],
                "url": SIDECAR_URL,
                "upstream_commit": health["upstream_commit"],
                "backend": health["backend"],
                "model_sha256": model["gguf"]["sha256"],
                "threads": model["threads"],
                "n_gpu_layers": model["n_gpu_layers"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if args.check_only:
        return 0

    if args.interface == "web":
        if port_is_open(8899):
            raise RuntimeError(
                "WebGUI is already running on port 8899. Stop it before starting "
                "the SemIf pilot instance."
            )
        entry_point = PROJECT_ROOT / "WebGUI.py"
    else:
        entry_point = PROJECT_ROOT / "GUI.py"

    print(
        f"Starting AIWorker {args.interface} interface with SemIf enabled "
        "and Legacy fallback...",
        flush=True,
    )
    return subprocess.call(
        [str(MAIN_PYTHON), str(entry_point)],
        cwd=PROJECT_ROOT,
        env=review_environment(base_environment),
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SemIf pilot launcher failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
