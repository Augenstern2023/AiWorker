# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_review_page_uses_writer_facing_method_choices() -> None:
    page = read("templates/review/scripts.html")

    assert 'id="script-review-method-complete"' in page
    assert 'id="script-review-method-local"' in page
    assert "完整审核" in page
    assert "本地初审" in page
    assert "提供问题分析、原文依据和修改建议" in page
    assert "剧本在本机处理" in page
    assert "审核模板" not in page
    assert "本次审核补充要求" not in page
    assert "SemIf" not in page
    assert "Legacy" not in page


def test_review_page_remembers_and_submits_method_per_task() -> None:
    script = read("static/js/review/scripts.js")

    assert "aiworker.scriptReview.method" in script
    assert "/api/script-reviews/capabilities" in script
    assert "/api/script-reviews/local-runtime/start" in script
    assert "review_method: reviewMethod" in script
    assert "'X-Review-Method': reviewMethod" in script
    assert "是否改用完整审核" in script
    assert "reviewMethodValue(review) === 'local'" in script
