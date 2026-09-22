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


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_existing_review_page_exposes_semif_as_an_explicit_gray_release() -> None:
    template = _source("templates/review/scripts.html")
    script = _source("static/js/review/scripts.js")

    assert 'id="script-review-engine"' in template
    assert '<option value="legacy">' in template
    assert '<option value="semif">' in template
    assert 'id="script-review-model-profile"' in template
    assert "X-Script-Review-Engine" in script
    assert "X-SemIf-Model-Profile" in script


def test_semif_report_rendering_keeps_quality_evidence_and_reruns_in_one_page() -> None:
    script = _source("static/js/review/scripts.js")

    assert "function renderSemifReport(review)" in script
    assert "report.quality_status === 'passed'" in script
    assert "report.hard_gate?.passed" in script
    assert "item.evidence || []" in script
    assert "function safeLocalUrl(value)" in script
    assert "raw.startsWith('/') && !raw.startsWith('//')" in script
    assert "failed_dimensions_only" in script
    assert "affected_episodes" in script
    assert "仅复审失败维度" in script
    assert "const pendingRerunKeys = new Map()" in script
    assert "pendingRerunKeys.delete(identity)" in script
