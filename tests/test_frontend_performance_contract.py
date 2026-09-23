# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Environment(loader=FileSystemLoader(ROOT / "templates"))


class RenderedPage(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.classes: set[str] = set()
        self.handlers: set[str] = set()
        self.script_sources: list[str] = []
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.ids: set[str] = set()
        self.data_attributes: set[tuple[str, str]] = set()
        self.x_attributes: set[tuple[str, str]] = set()
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self.elements.append((tag, attributes))
        self.classes.update((attributes.get("class") or "").split())
        if identifier := attributes.get("id"):
            self.ids.add(identifier)
        self.data_attributes.update(
            (name, value) for name, value in attributes.items()
            if name.startswith("data-")
        )
        self.x_attributes.update(
            (name, value) for name, value in attributes.items()
            if name.startswith("x-") or name.startswith(":") or name.startswith("@")
        )
        self.handlers.update(
            value for name, value in attrs if name.startswith("@") and value
        )
        if tag == "script" and attributes.get("src"):
            self.script_sources.append(attributes["src"])

    def has_element(
        self,
        *,
        tag: str | None = None,
        classes: Iterable[str] = (),
        attributes: dict[str, str] | None = None,
    ) -> bool:
        required_classes = set(classes)
        required_attributes = attributes or {}
        return any(
            (tag is None or element_tag == tag)
            and required_classes <= set(element_attributes.get("class", "").split())
            and all(element_attributes.get(name) == value
                    for name, value in required_attributes.items())
            for element_tag, element_attributes in self.elements
        )


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def rendered_page(template: str) -> RenderedPage:
    """Render a route template so contracts cover included response content."""
    return RenderedPage(TEMPLATES.get_template(template).render(
        asset_version="test",
        can_export_projects=True,
        creative_module="contract",
        page_title="UI contract",
        principal=SimpleNamespace(display_name="Contract", username="contract"),
        project_id="project-1",
        workspace_page="contract",
        workspace_runtime=True,
    ))


def assert_workspace_structure(
    page: RenderedPage,
) -> None:
    for class_name in (
        "workspace-page-header", "workspace-context-bar", "workspace-toolbar",
        "workspace-split", "workspace-list-pane", "workspace-inspector",
    ):
        assert page.has_element(classes={class_name}), class_name


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
        "selection/ranks.html": [],
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
        scripts = [
            script.removeprefix("/static/js/").split("?", maxsplit=1)[0]
            for script in rendered_page(template).script_sources
            if script.startswith("/static/js/")
        ]
        assert [script for script in scripts if script in modules] == expected, template


def test_common_creative_bundle_stays_within_source_budget():
    assert (ROOT / "static/js/common/creative.js").stat().st_size <= 160_000


def test_tasks_template_only_loads_its_page_script():
    tasks = source("templates/common/my_tasks.html")
    assert "/static/js/common/my_tasks.js" in tasks
    assert "/static/js/common/tasks.js" not in tasks
    assert "/static/js/common/creative.js" not in tasks


def test_workspace_layout_primitives_cover_all_workflow_families():
    css = source("static/css/workspaces.css")
    for class_name in (
        ".workspace-page-header", ".workspace-context-bar", ".workspace-toolbar",
        ".workspace-split", ".workspace-list-pane", ".workspace-inspector",
        ".workspace-state", ".workspace-empty", "@media (max-width: 999.98px)",
    ):
        assert class_name in css

    selection_pages = {
        "selection/topics.html": {
            "result": "creative-topic-table",
            "handler": "fetchTopicLibrary()",
            "x_attribute": ("x-model", "topicLibrary.platform"),
            "data_attribute": ("data-bs-toggle", "dropdown"),
        },
        "selection/hits.html": {
            "result": "creative-topic-table",
            "handler": "generateCreativeTopics",
            "x_attribute": ("x-model", "creative.platform"),
        },
        "selection/dramas.html": {
            "result": "list-group",
            "handler": "searchDrama",
            "x_attribute": ("x-model", "dramaAnalysis.title"),
            "identifier": "dramaAnalysisVideo",
        },
        "selection/ranks.html": {
            "result": "today-rank-table",
            "handler": "adoptRankEntry(item)",
            "x_attribute": ("x-model", "todayRankDate"),
            "identifier": "hongguoRankInfoModal",
            "data_attribute": ("data-bs-target", "#hongguoRankInfoModal"),
        },
    }
    for template, contract in selection_pages.items():
        page = rendered_page(template)
        # The shell only advertises these classes; verify the included content
        # renders the actual page regions and its pre-existing result surface.
        assert_workspace_structure(page)
        assert page.has_element(classes={contract["result"]}), template
        assert contract["handler"] in page.handlers, template
        assert contract["x_attribute"] in page.x_attributes, template
        if "identifier" in contract:
            assert contract["identifier"] in page.ids, template
        if "data_attribute" in contract:
            assert contract["data_attribute"] in page.data_attributes, template


def test_creative_stage_pages_render_navigation_editor_inspector_and_actions():
    stage_pages = {
        "planning/brief.html": {
            "openPromptLibrary('brief')", "generateProjectBlueprints",
            "saveProjectBlueprint", "completeProjectBrief",
        },
        "planning/characters.html": {
            "openPromptLibrary('characters')", "generateProjectCharacters",
            "saveProjectCharacter(item)", "completeProjectCharacters",
        },
        "writing/outlines.html": {
            "openPromptLibrary('outline')", "generateProjectOutlines",
            "saveProjectEpisode(item)", "completeProjectOutlines",
        },
        "writing/beats.html": {
            "openPromptLibrary('beats')", "generateProjectBeats",
            "saveProjectEpisode(selectedProjectBeats())", "completeProjectBeats",
        },
        "writing/scripts.html": {
            "openPromptLibrary('script')", "generateProjectScript",
            "saveProjectEpisode(selectedProjectScript())", "completeProjectScript",
        },
    }
    for template, actions in stage_pages.items():
        page = rendered_page(template)
        assert {
            "workspace-page-header", "workspace-context-bar",
            "workspace-module-navigation", "workspace-stage-editor",
            "workspace-inspector",
        } <= page.classes, template
        assert actions <= page.handlers, template

    brief = rendered_page("planning/brief.html")
    assert "brief-production-export" in brief.ids
    assert ("data-project-id", "project-1") in brief.data_attributes
    assert ("x-show", "creativeWorkspace.project") in brief.x_attributes

    beats = rendered_page("writing/beats.html")
    assert "writing-beats-page-header" in beats.ids
    assert "project-beats-workspace" in beats.ids
    assert ("x-model.number", "creativeWorkspace.selectedEpisode") in beats.x_attributes


def test_prompt_surfaces_use_step_context_and_inspect_layout():
    for template in (
        "common/workspace/prompt_template_center.html",
        "common/workspace/prompt_template_effects.html",
    ):
        page = rendered_page(template)
        assert_workspace_structure(page)
        assert {
            "workspace-module-navigation",
            "changePromptWorkspaceStage($event.target.value)",
            "changePromptWorkspaceEpisode($event.target.value)",
        } <= page.classes | page.handlers, template
        prefix = "prompt-center" if "center" in template else "prompt-effects"
        assert f"{prefix}-stage" in page.ids
        assert f"{prefix}-episode" in page.ids
        assert (
            "x-model", "creativeWorkspace.promptLibrary.stage"
        ) in page.x_attributes


def test_tasks_selection_and_prompt_resources_stay_within_their_boundaries():
    forbidden_creative_resources = {
        "/static/js/common/prompt_templates.js",
        "/static/js/common/creative_generation.js",
        "/static/js/writing/creative_episodes.js",
    }
    for template in (
        "selection/topics.html", "selection/hits.html", "selection/dramas.html",
        "selection/ranks.html",
    ):
        resources = {
            script.split("?", maxsplit=1)[0]
            for script in rendered_page(template).script_sources
        }
        assert not resources & forbidden_creative_resources, template

    prompt_resources = {
        script.split("?", maxsplit=1)[0]
        for script in rendered_page(
            "common/workspace/prompt_template_center.html"
        ).script_sources
    }
    assert "/static/js/common/prompt_templates.js" in prompt_resources
    assert "/static/js/selection/drama_reviews.js" not in prompt_resources

    task_resources = {
        script.split("?", maxsplit=1)[0]
        for script in rendered_page("common/my_tasks.html").script_sources
    }
    assert "/static/js/common/my_tasks.js" in task_resources
    assert not task_resources & (forbidden_creative_resources | {
        "/static/js/common/creative.js", "/static/js/selection/drama_reviews.js",
    })
