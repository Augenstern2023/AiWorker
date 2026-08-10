# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

from identity.permission_catalog import NAVIGATION_CATALOG
from web.page_access_catalog import PAGE_ACCESS_CATALOG
from web.page_router import PAGE_TEMPLATES
from web.page_routes import PAGE_ROUTES


ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_center_has_no_runtime_configuration() -> None:
    assert not any(item["route"] == "/admin/schedules" for item in NAVIGATION_CATALOG)
    assert "/admin/schedules" not in PAGE_TEMPLATES
    assert "/admin/schedules" not in {route for route, _, _ in PAGE_ROUTES}
    assert "/admin/schedules" not in {
        entry["page_route"] for entry in PAGE_ACCESS_CATALOG
    }
    assert "apscheduler" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "scheduler" not in (ROOT / "WebGUI.py").read_text(encoding="utf-8")
    assert "scheduling_" not in (ROOT / "system_database" / "schema.py").read_text(
        encoding="utf-8"
    )
    assert "scheduling_" not in (
        ROOT / "system_database" / "sqlite_import.py"
    ).read_text(encoding="utf-8")
    assert "schedule: '调度中心'" not in (
        ROOT / "static" / "js" / "admin" / "audit.js"
    ).read_text(encoding="utf-8")
    assert not (ROOT / "scheduler" / "local_service.py").exists()
    assert not (ROOT / "templates" / "admin" / "schedules.html").exists()
    assert not (ROOT / "static" / "js" / "admin" / "schedules.js").exists()
