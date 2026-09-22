# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

import re
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


def test_creative_modules_are_route_scoped():
    core = "common/creative.js"
    reviews = "selection/drama_reviews.js"
    episodes = "writing/creative_episodes.js"
    prompt = "common/prompt_templates.js"
    generation = "common/creative_generation.js"
    routes = {
        "selection/topics.html": [core, reviews],
        "selection/hits.html": [core, reviews],
        "selection/dramas.html": [core, reviews],
        "planning/projects.html": [core, prompt, generation],
        "planning/brief.html": [core, prompt, generation],
        "planning/characters.html": [core, prompt, generation],
        "writing/projects.html": [core],
        "writing/outlines.html": [core, episodes, prompt, generation],
        "writing/beats.html": [core, episodes, prompt, generation],
        "writing/scripts.html": [core, episodes, prompt, generation],
        "common/workspace/prompt_template_center.html": [core, prompt],
        "common/workspace/prompt_template_effects.html": [core, prompt],
    }
    modules = {core, reviews, episodes, prompt, generation}
    for template, expected in routes.items():
        scripts = re.findall(r'<script src="/static/js/([^?]+)\?', source(f"templates/{template}"))
        assert [script for script in scripts if script in modules] == expected, template
        assert scripts[:len(expected)] == expected, template


def test_common_creative_bundle_stays_within_source_budget():
    assert (ROOT / "static/js/common/creative.js").stat().st_size <= 160_000


def test_tasks_template_only_loads_its_page_script():
    tasks = source("templates/common/my_tasks.html")
    assert "/static/js/common/my_tasks.js" in tasks
    assert "/static/js/common/tasks.js" not in tasks
    assert "/static/js/common/creative.js" not in tasks
