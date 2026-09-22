# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from types import SimpleNamespace

import pytest

from screenplay_review.local_runtime import LocalReviewRuntimeManager


@pytest.mark.asyncio
async def test_status_reports_ready_for_a_healthy_sidecar(tmp_path, monkeypatch) -> None:
    manager = LocalReviewRuntimeManager(tmp_path)

    async def healthy() -> dict:
        return {"status": "ok"}

    monkeypatch.setattr(manager, "_health", healthy)

    assert await manager.status() == {
        "available": True,
        "installed": True,
        "state": "ready",
        "message": "本地初审已就绪",
    }


@pytest.mark.asyncio
async def test_status_reports_unavailable_when_components_are_missing(
    tmp_path, monkeypatch
) -> None:
    manager = LocalReviewRuntimeManager(tmp_path)

    async def unhealthy() -> None:
        return None

    monkeypatch.setattr(manager, "_health", unhealthy)

    status = await manager.status()

    assert status["available"] is False
    assert status["installed"] is False
    assert status["state"] == "unavailable"


@pytest.mark.asyncio
async def test_start_launches_the_existing_checked_sidecar_installer(
    tmp_path, monkeypatch
) -> None:
    manager = LocalReviewRuntimeManager(tmp_path)
    manager.launcher_path.parent.mkdir(parents=True)
    manager.launcher_path.write_text("# launcher", encoding="utf-8")
    manager.sidecar_executable = tmp_path / "aiworker-semif.exe"
    manager.sidecar_executable.write_bytes(b"sidecar")
    manager.model_path = tmp_path / "model.gguf"
    manager.model_path.write_bytes(b"model")

    async def unhealthy() -> None:
        return None

    monkeypatch.setattr(manager, "_health", unhealthy)
    launched: dict[str, object] = {}

    def fake_popen(command, **options):
        launched["command"] = command
        launched["options"] = options
        return SimpleNamespace(poll=lambda: None, returncode=None)

    monkeypatch.setattr("screenplay_review.local_runtime.subprocess.Popen", fake_popen)

    status = await manager.start()

    assert status["state"] == "starting"
    assert launched["command"][-1] == "--check-only"
    assert launched["options"]["cwd"] == tmp_path
