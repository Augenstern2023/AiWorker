# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Pure contracts for Beats validation at script-optimization write paths."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from creative.script_service import validate_beats


@pytest.mark.parametrize("count", [1, 2, 3])
def test_legacy_beats_with_one_to_three_items_are_accepted(count: int) -> None:
    payload = {"beats": [{"order": number} for number in range(1, count + 1)]}

    assert len(validate_beats(payload, allow_legacy=True)["beats"]) == count


def test_legacy_beats_with_four_items_are_rejected_without_truncation() -> None:
    payload = {"beats": [{"order": number} for number in range(1, 5)]}

    with pytest.raises(ValueError, match="1-3"):
        validate_beats(payload, allow_legacy=True)
    assert len(payload["beats"]) == 4


def test_locked_revision_can_preserve_overflow_without_truncation() -> None:
    payload = {"beats": [{"order": number} for number in range(1, 5)]}

    normalized = validate_beats(
        payload,
        allow_legacy=True,
        allow_scene_limit_overflow=True,
    )

    assert len(normalized["beats"]) == 4
    assert len(payload["beats"]) == 4


def test_scene_limit_overflow_flag_is_only_forwarded_by_beats_route() -> None:
    api_source = (Path(__file__).parents[1] / "creative/api.py").read_text(
        encoding="utf-8"
    )
    brief_route = api_source.split("async def create_project_blueprint(", 1)[1].split(
        "async def derive_project_blueprint(", 1
    )[0]
    beats_route = api_source.split("async def create_project_episode_beats(", 1)[1].split(
        "async def lock_episode_beats(", 1
    )[0]

    assert "allow_scene_limit_overflow" not in brief_route
    assert "allow_scene_limit_overflow=payload.allow_scene_limit_overflow" in beats_route


def test_beats_editor_exposes_return_and_standard_revision_actions() -> None:
    root = Path(__file__).parents[1]
    template = (root / "templates/common/workspace/project_beats.html").read_text(
        encoding="utf-8"
    )
    generation = (root / "static/js/common/creative_generation.js").read_text(
        encoding="utf-8"
    )
    creative = (root / "static/js/common/creative.js").read_text(encoding="utf-8")

    assert ':href="beatsBackUrl()"' in template
    assert "beatsBackLabel()" in template
    assert "返回全集 Beats" in creative
    assert "返回分集大纲" in creative
    assert "基于此版本新建" in template
    assert "async createProjectBeatsRevision()" in generation
    assert "/episodes/${episodeNumber}/beats" in generation
    assert "replaces_resource_id: String(item.id)" in generation
    assert "expected_project_revision: Number(project.revision || 0)" in generation
    assert "allow_scene_limit_overflow: sceneCount > 3" in generation


def test_revision_overflow_flag_is_not_forwarded_to_non_beats_repository_writes() -> None:
    source = (Path(__file__).parents[1] / "creative/application_service.py").read_text(
        encoding="utf-8"
    )
    create_revision = source.split("    def create_revision(", 1)[1].split(
        "    def _snapshot_extra(", 1
    )[0]
    characters_call = create_revision.split(
        "self.repository.create_character_batch(", 1
    )[1].split(")", 1)[0]

    assert "allow_scene_limit_overflow" not in characters_call


@pytest.mark.parametrize(
    ("path", "method", "transaction_call"),
    [
        (
            "creative/repositories/reviews.py",
            "save_script_optimization_episode",
            "transaction_scope",
        ),
        (
            "creative/repositories/reviews.py",
            "complete_script_optimization",
            "transaction_scope",
        ),
        (
            "creative/legacy_sqlite_repository.py",
            "complete_script_optimization",
            "_connect",
        ),
    ],
)
def test_optimization_write_paths_validate_beats_before_transaction(
    path: str, method: str, transaction_call: str
) -> None:
    source_path = Path(__file__).parents[1] / path
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    method_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method
    )

    validate_calls = [
        node
        for node in ast.walk(method_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "validate_beats"
    ]
    transaction_calls = [
        node
        for node in ast.walk(method_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == transaction_call
    ]

    assert validate_calls
    assert transaction_calls
    assert all(
        any(
            keyword.arg == "allow_legacy"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in call.keywords
        )
        for call in validate_calls
    )
    assert min(call.lineno for call in validate_calls) < min(
        call.lineno for call in transaction_calls
    )
