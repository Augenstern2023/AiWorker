# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_douyin_analysis_agent.py
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

"""Tests for Douyin rank analysis agent."""

import json
from pathlib import Path

from agents.douyin_analysis_agent import AnalyzeOptions, analyze_rank_file, select_records


def test_select_records_keeps_top_n_per_category():
    records = [
        {"title": "短1", "rank_category": "短剧", "tab": "热榜", "heat_text": "10万"},
        {"title": "短2", "rank_category": "短剧", "tab": "热榜", "heat_text": "9万"},
        {"title": "漫1", "rank_category": "漫剧", "tab": "热榜", "heat_text": "8万"},
        {"title": "漫2", "rank_category": "漫剧", "tab": "热榜", "heat_text": "7万"},
    ]

    selected = select_records(records, AnalyzeOptions(top_n=1, per_category=True, with_llm=False))

    assert [item["title"] for item in selected] == ["短1", "漫1"]


def test_analyze_rank_file_generates_llm_reports(tmp_path, monkeypatch):
    rank_path = tmp_path / "douyin_rank.json"
    rank_path.write_text(
        json.dumps(
            {
                "platform": "dy",
                "records": [
                    {
                        "rank": 1,
                        "title": "测试短剧",
                        "rank_category": "短剧",
                        "tab": "热榜",
                        "heat_text": "10万",
                        "like_count": "1万",
                        "favorite_count": "500",
                        "comment_count": "100",
                        "description": "女主被误会后逆袭翻身。",
                        "comments": [{"content": "开头很抓人"}, {"content": "反转爽"}],
                    },
                    {
                        "rank": 1,
                        "title": "测试漫剧",
                        "rank_category": "漫剧",
                        "tab": "榜单",
                        "heat_text": "9万",
                        "description": "男主重生复仇。",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def fake_llm(payload, overview=False):
        if overview:
            return {"题材分布": ["逆袭", "复仇"], "投放关键词池": ["短剧", "爽剧"]}, ""
        return {
            "剧情类型": "逆袭爽剧",
            "核心卖点": ["误会开局", "反转打脸"],
            "开头钩子": ["女主被误会"],
            "情绪点": ["委屈", "爽感"],
            "爆款结构": ["压制", "翻身"],
            "适合投放关键词": ["逆袭", "爽剧"],
            "评论反馈洞察": ["评论认可反转"],
            "素材切片建议": ["截取误会到反转"],
            "风险提示": [],
            "置信度与依据": "基于简介和评论",
        }, ""

    monkeypatch.setattr("agents.douyin_analysis_agent.call_llm_json", fake_llm)

    manifest = analyze_rank_file(
        rank_path,
        output_dir=tmp_path / "analysis",
        options=AnalyzeOptions(top_n=1, per_category=True, with_llm=True),
    )

    assert len(manifest["items"]) == 2
    assert Path(manifest["overview"]).exists()
    overview = Path(manifest["overview"]).read_text(encoding="utf-8")
    assert "## 热剧概览" in overview
    assert "共性核心卖点" in overview
    assert "{" not in overview
    first = manifest["items"][0]["analysis"]
    assert first["llm_analysis"]["剧情类型"] == "逆袭爽剧"
