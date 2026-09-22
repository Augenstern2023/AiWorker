# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Manage the optional local-review sidecar without exposing engine details."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .semif_client import SemIfClient, SemIfError


EXPECTED_GGUF_SHA256 = (
    "13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983"
)


class LocalReviewRuntimeManager:
    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[1]
        )
        self.launcher_path = self.project_root / "scripts/start_semif_pilot.py"
        self.sidecar_executable = Path(
            os.getenv(
                "SCREENPLAY_REVIEW_SEMIF_EXECUTABLE",
                "D:/AiWorker/SemIf/.venv/Scripts/aiworker-semif.exe",
            )
        )
        self.model_path = Path(
            os.getenv(
                "SEMIF_GGUF_PATH",
                "D:/AiWorker/SemIf/models/Qwen_Qwen3.5-4B-Q4_K_M.gguf",
            )
        )
        self.sidecar_url = os.getenv(
            "SCREENPLAY_REVIEW_SEMIF_URL", "http://127.0.0.1:8765"
        ).rstrip("/")
        self._launcher_process: subprocess.Popen[bytes] | None = None
        self._start_lock = asyncio.Lock()
        self._stderr_path = self.project_root / "logs/local-review-runtime.stderr.log"

    @property
    def installed(self) -> bool:
        return all(
            path.is_file()
            for path in (self.launcher_path, self.sidecar_executable, self.model_path)
        )

    async def status(self) -> dict[str, Any]:
        health = await self._health()
        if health is not None:
            return {
                "available": True,
                "installed": True,
                "state": "ready",
                "message": "本地初审已就绪",
            }
        process = self._launcher_process
        if process is not None and process.poll() is None:
            return {
                "available": False,
                "installed": True,
                "state": "starting",
                "message": "正在准备本地初审，首次启动可能需要几分钟",
            }
        if not self.installed:
            return {
                "available": False,
                "installed": False,
                "state": "unavailable",
                "message": "本机尚未安装本地初审组件",
            }
        if process is not None and process.returncode not in (None, 0):
            return {
                "available": False,
                "installed": True,
                "state": "error",
                "message": "本地初审启动失败，请重试或改用完整审核",
            }
        return {
            "available": False,
            "installed": True,
            "state": "stopped",
            "message": "首次使用时将自动准备本地初审",
        }

    async def start(self) -> dict[str, Any]:
        async with self._start_lock:
            current = await self.status()
            if current["available"] or current["state"] == "starting":
                return current
            if not current["installed"]:
                return current
            self._stderr_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path = self.project_root / "logs/local-review-runtime.stdout.log"
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            with stdout_path.open("ab") as stdout, self._stderr_path.open("ab") as stderr:
                self._launcher_process = subprocess.Popen(
                    [sys.executable, str(self.launcher_path), "--check-only"],
                    cwd=self.project_root,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=creation_flags,
                )
            return {
                "available": False,
                "installed": True,
                "state": "starting",
                "message": "正在准备本地初审，首次启动可能需要几分钟",
            }

    async def _health(self) -> dict[str, Any] | None:
        timeout = float(
            os.getenv("SCREENPLAY_REVIEW_SEMIF_HEALTH_TIMEOUT_SECONDS", "2")
        )
        try:
            async with SemIfClient(
                self.sidecar_url, timeout_seconds=timeout, max_retries=0
            ) as client:
                health = await client.health()
        except (SemIfError, ValueError):
            return None
        payload = health.model_dump(mode="json")
        gguf = (payload.get("model") or {}).get("gguf") or {}
        if (
            payload.get("backend") != "llamacpp"
            or gguf.get("sha256") != EXPECTED_GGUF_SHA256
        ):
            return None
        return payload
