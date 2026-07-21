# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

from analysis.exporters.markdown_exporter import export_markdown, render_markdown
from analysis.schema import BilibiliAnalysisResult


def _result() -> BilibiliAnalysisResult:
    return BilibiliAnalysisResult(
        aid=123,
        bvid="BV123",
        title="测试标题",
        url="https://www.bilibili.com/video/BV123",
        author="测试UP",
        published_at="2026-06-25 14:18:08",
        stats={"view": 10, "like": 2, "coin": 1, "favorite": 3, "reply": 4},
        transcript_source="subtitle",
        transcript="第一句\n第二句",
        tags=["B站", "AI"],
        analysis={"risk_flags": ["涉及隐私画面，需要谨慎处理"]},
    )


def test_render_markdown_contains_front_matter_and_transcript():
    markdown = render_markdown(_result())

    assert markdown.startswith("---\nsource: bilibili\n")
    assert 'bvid: "BV123"' in markdown
    assert 'author: "测试UP"' in markdown
    assert 'transcript_source: "subtitle"' in markdown
    assert 'tags: ["B站", "AI"]' in markdown
    assert "# 测试标题" in markdown
    assert "## 转写全文" in markdown
    assert "## 风险提示" in markdown
    assert "- 涉及隐私画面，需要谨慎处理" in markdown
    assert "第一句\n第二句" in markdown


def test_export_markdown_writes_aid_named_file(tmp_path):
    file_path = export_markdown(_result(), tmp_path)

    assert file_path.name == "123.md"
    assert file_path.read_text(encoding="utf-8").startswith("---")
