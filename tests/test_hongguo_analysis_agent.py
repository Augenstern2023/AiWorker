# -*- coding: utf-8 -*-
"""Tests for Hongguo CSV rank analysis agent."""

import json
from pathlib import Path

from agents.hongguo_analysis_agent import AnalyzeOptions, analyze_rank_file, fetch_play_url


def test_analyze_rank_file_generates_manifest_from_csv_import(tmp_path):
    rank_path = tmp_path / "rank.json"
    rank_path.write_text(
        json.dumps(
            {
                "job_id": "job-hg",
                "source": "csv_import",
                "records": [
                    {
                        "榜单类型": "红果短剧热播榜",
                        "排名": "1",
                        "剧名": "测试短剧",
                        "标签": "逆袭·全80集",
                        "简介": "女主被误会后反击翻身。",
                        "热评": "爽感很足",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest = analyze_rank_file(rank_path, output_dir=tmp_path / "analysis", options=AnalyzeOptions(top_n=1))

    assert manifest["items"]
    assert (tmp_path / "analysis" / "hongguo_analysis_overview.md").exists()
    item = manifest["items"][0]["analysis"]
    assert item["title"] == "测试短剧"
    assert item["plot_tags"] == ["逆袭", "全80集"]
    assert item["transcript_source"] == "rank_text"


def test_fetch_play_url_is_disabled_after_interface_removal():
    url, error = fetch_play_url(
        {"rank": {"book_id": "book-1", "play_type": "0"}},
        {"video_id": "video-1"},
        AnalyzeOptions(code="secret"),
    )

    assert url == ""
    assert "接口已移除" in error


def test_analyze_rank_file_supports_llm_without_media_fetch(tmp_path, monkeypatch):
    rank_path = tmp_path / "rank.json"
    rank_path.write_text(
        json.dumps(
            {
                "job_id": "job-hg",
                "source": "csv_import",
                "records": [{"榜单类型": "红果短剧热播榜", "排名": "1", "剧名": "LLM短剧", "简介": "开局强冲突"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def fake_llm(payload, overview=False):
        if overview:
            return {"summary": "可投"}, ""
        return {"genre": "逆袭爽剧"}, ""

    monkeypatch.setattr("agents.hongguo_analysis_agent.call_llm_json", fake_llm)

    manifest = analyze_rank_file(rank_path, output_dir=tmp_path / "analysis", options=AnalyzeOptions(top_n=1, with_llm=True))

    analysis = manifest["items"][0]["analysis"]
    assert analysis["llm_analysis"]["genre"] == "逆袭爽剧"
    assert Path(manifest["overview"]).read_text(encoding="utf-8").startswith("# 红果榜单剧目分析总览")
    assert Path(manifest["overview_json"]).exists()
