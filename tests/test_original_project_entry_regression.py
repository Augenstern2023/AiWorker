# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "template_name", ("planning/projects.html", "writing/projects.html")
)
def test_project_creator_sees_original_project_entry(template_name: str) -> None:
    """Catch removal of the real creator control from either role project page."""
    environment = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(("html",)),
    )
    html = environment.get_template(template_name).render(
        principal=SimpleNamespace(
            is_system_admin=False,
            permissions=frozenset({"project.create"}),
        ),
        asset_version="test",
        navigation=[],
        page_title="项目",
        workspace_page="projects",
        workspace_runtime=True,
    )

    assert 'data-action="create-original-project"' in html
