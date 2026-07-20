# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
from pathlib import Path

from creative.transcript_assets import find_transcript_bundle


def _write_source(episode_dir: Path, title: str, episode: int) -> None:
    episode_dir.mkdir(parents=True)
    (episode_dir / "source.json").write_text(
        json.dumps({"title": title, "episode": {"episode": episode}}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_finds_script_by_source_metadata_instead_of_directory_name(tmp_path: Path):
    episode = tmp_path / "data" / "hg" / "jobs" / "job-1" / "dramas" / "renamed" / "episode_01"
    _write_source(episode, "目标剧", 1)
    (episode / "script.md").write_text("# 标题\n\n## 转写内容\n\n第一集真实剧情", encoding="utf-8")

    bundle = find_transcript_bundle(tmp_path, "目标剧", "hg")

    assert bundle["title"] == "目标剧"
    assert bundle["episodes"][0]["episode_number"] == 1
    assert bundle["episodes"][0]["transcript_source"] == "script"
    assert bundle["episodes"][0]["text"] == "第一集真实剧情"
    assert bundle["episodes"][0]["script_path"].endswith("script.md")


def test_reads_transcript_json_when_script_is_missing(tmp_path: Path):
    episode = tmp_path / "data" / "hg" / "jobs" / "job-1" / "dramas" / "目标剧" / "episode_02"
    _write_source(episode, "目标剧", 2)
    (episode / "transcript.json").write_text(
        json.dumps({"source": "asr", "text": "第二集转写"}, ensure_ascii=False),
        encoding="utf-8",
    )

    bundle = find_transcript_bundle(tmp_path, "目标剧", "hg")

    assert bundle["episodes"][0]["episode_number"] == 2
    assert bundle["episodes"][0]["text"] == "第二集转写"
    assert bundle["episodes"][0]["transcript_source"] == "asr"


def test_finds_douyin_fallback_script_from_manifest(tmp_path: Path):
    drama = tmp_path / "data" / "hg" / "jobs" / "job-1" / "dramas" / "fallback"
    item = drama / "douyin_fallback" / "item_001"
    item.mkdir(parents=True)
    script = item / "script.md"
    script.write_text("## 转写内容\n\n穿书后认错反派", encoding="utf-8")
    (drama / "douyin_fallback" / "manifest.json").write_text(
        json.dumps({
            "title": "穿书短剧",
            "items": [{"index": 1, "status": "succeeded", "script_path": str(script)}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    bundle = find_transcript_bundle(tmp_path, "《穿书短剧》", "hg")

    assert bundle["episodes"][0]["episode_number"] == 1
    assert bundle["episodes"][0]["text"] == "穿书后认错反派"


def test_video_without_transcript_is_not_valid(tmp_path: Path):
    episode = tmp_path / "data" / "hg" / "jobs" / "job-1" / "dramas" / "目标剧" / "episode_01"
    _write_source(episode, "目标剧", 1)
    (episode / "video.mp4").write_bytes(b"video")

    bundle = find_transcript_bundle(tmp_path, "目标剧", "hg")

    assert bundle["episodes"] == []
    assert bundle["searched_paths"]


def test_latest_non_empty_asset_wins_per_episode(tmp_path: Path):
    for job, text in (("job-1", "旧转写"), ("job-2", "新转写")):
        episode = tmp_path / "data" / "hg" / "jobs" / job / "dramas" / "目标剧" / "episode_01"
        _write_source(episode, "目标剧", 1)
        (episode / "script.md").write_text(f"## 转写内容\n\n{text}", encoding="utf-8")
    old = tmp_path / "data" / "hg" / "jobs" / "job-1" / "dramas" / "目标剧" / "episode_01" / "script.md"
    new = tmp_path / "data" / "hg" / "jobs" / "job-2" / "dramas" / "目标剧" / "episode_01" / "script.md"
    old.touch()
    new.touch()

    bundle = find_transcript_bundle(tmp_path, "目标剧", "hg")

    assert len(bundle["episodes"]) == 1
    assert bundle["episodes"][0]["text"] == "新转写"
