# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
import pytest


ROOT = Path(__file__).resolve().parents[1]


def render_workspace_template(
    template_name: str, permissions: set[str] | None = None
) -> str:
    environment = Environment(loader=FileSystemLoader(ROOT / "templates"))
    principal = SimpleNamespace(
        is_system_admin=False,
        permissions=frozenset(permissions or {"project.create"}),
    )
    return environment.get_template(template_name).render(
        principal=principal,
        asset_version="test",
        navigation_items=[],
        request=SimpleNamespace(url=SimpleNamespace(path="/projects")),
    )


@pytest.mark.parametrize(
    "template_name", ["planning/projects.html", "writing/projects.html"]
)
def test_both_role_pages_render_original_project_entry(template_name: str) -> None:
    html = render_workspace_template(template_name)
    assert 'data-action="create-original-project"' in html
    assert 'id="original-seed-text"' in html
    assert "一句话灵感或题材" in html


def test_planning_keeps_topic_creation_but_writing_does_not_force_it() -> None:
    planning = render_workspace_template("planning/projects.html")
    writing = render_workspace_template("writing/projects.html")
    assert 'data-section="topic-project-creation"' in planning
    assert 'data-section="topic-project-creation"' not in writing
    assert 'data-action="create-original-project"' in writing


def test_original_entry_is_hidden_without_project_create_permission() -> None:
    html = render_workspace_template(
        "writing/projects.html", permissions={"project.read"}
    )
    assert 'data-action="create-original-project"' not in html


def test_shared_project_table_exposes_mode_source_and_stage_columns() -> None:
    html = render_workspace_template("writing/projects.html")
    for label in ("项目", "创作模式", "创作来源", "目标集数", "当前阶段", "更新时间/操作"):
        assert label in html


def test_original_brief_uses_mode_templates_and_original_fields() -> None:
    source = (ROOT / "templates/common/workspace/project_blueprints.html").read_text(
        encoding="utf-8"
    )
    assert "原创方向与 Brief" in source
    assert "原创性说明" in source
    assert 'x-if="creativeWorkspace.project?.creation_mode === \'original\'"' in source
    assert 'x-if="creativeWorkspace.project?.creation_mode !== \'original\'"' in source


def test_original_brief_does_not_mount_imitation_only_fields() -> None:
    source = (ROOT / "templates/common/workspace/project_blueprints.html").read_text(
        encoding="utf-8"
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(source)
        assert page.get_by_text("仿制原因", exact=True).count() == 0
        assert page.get_by_text("借鉴元素", exact=True).count() == 0
        browser.close()


def test_original_javascript_posts_lightweight_payload_and_preserves_candidates() -> None:
    source = (ROOT / "static/js/common/creative.js").read_text(encoding="utf-8")
    assert "createOriginalProject" in source
    assert "/api/creative/projects/original" in source
    assert "preserve_blueprint_ids" in source
    assert "create_seed_version" not in source
    assert "/seeds/versions" in source


def test_original_mobile_layout_has_no_fixed_comparison_width() -> None:
    source = (ROOT / "static/css/workspaces.css").read_text(encoding="utf-8")
    assert ".original-direction-grid" in source
    assert "max-width: 767.98px" in source
    assert "overflow-wrap: anywhere" in source
