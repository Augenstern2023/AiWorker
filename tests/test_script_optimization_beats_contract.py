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
