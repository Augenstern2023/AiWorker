# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workspace_tokens_define_one_accent_and_semantic_states():
    css = source("static/css/workspaces.css")
    for token in (
        "--surface-canvas", "--surface-panel", "--surface-raised",
        "--color-accent", "--color-success", "--color-warning",
        "--color-danger", "--radius-control", "--radius-panel",
        "--space-1", "--space-6",
    ):
        assert token in css
    assert "prefers-reduced-motion: reduce" in css


def test_heavy_libraries_are_route_scoped():
    base = source("templates/layouts/base.html")
    analytics = source("templates/admin/analytics.html")
    assert "echarts.min.js" not in base
    assert "echarts.min.js" in analytics
    assert "creative.js" not in base
