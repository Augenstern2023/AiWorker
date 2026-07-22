# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from creative import transcript_materializer


class Provider:
    async def transcribe(self, audio_path: str) -> dict:
        assert Path(audio_path).exists()
        return {"source": "asr", "text": "顾先生在宴会上公开身份。", "segments": []}


def test_materializes_downloaded_video_into_transcript_and_script(tmp_path: Path, monkeypatch):
    episode = tmp_path / "episode_01"
    episode.mkdir()
    video = episode / "video.mp4"
    video.write_bytes(b"video")
    source = episode / "source.json"
    source.write_text(
        json.dumps({"title": "目标剧", "episode": {"episode": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )

    async def fake_convert(video_path, audio_path, ffmpeg_path):
        Path(audio_path).write_bytes(b"audio")

    monkeypatch.setattr(transcript_materializer, "convert_audio_to_wav", fake_convert)
    progress = []

    result = asyncio.run(transcript_materializer.materialize_video_transcripts(
        [{"episode_number": 1, "video_path": str(video), "source_path": str(source)}],
        "目标剧",
        provider=Provider(),
        on_progress=lambda payload: progress.append(payload),
    ))

    transcript = json.loads((episode / "transcript.json").read_text(encoding="utf-8"))
    script = (episode / "script.md").read_text(encoding="utf-8")
    assert result["created"][0]["episode_number"] == 1
    assert result["failed"] == []
    assert transcript["text"] == "顾先生在宴会上公开身份。"
    assert "## 转写内容" in script
    assert "顾先生在宴会上公开身份。" in script
    assert progress[-1]["completed"] == 1
