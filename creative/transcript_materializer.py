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
from typing import Any, Callable

from agents.hongguo_drama_worker import create_asr_provider
from analysis.asr import convert_audio_to_wav
from config import bilibili_config


def render_script(title: str, episode_number: int, transcript_source: str, text: str) -> str:
    return "\n".join([
        f"# {title} 第 {episode_number} 集",
        "",
        f"- 来源：{transcript_source}",
        "- 说明：以下内容为字幕/语音转写稿，不是官方剧本。",
        "",
        "## 转写内容",
        "",
        text,
        "",
    ])


async def materialize_video_transcripts(
    assets: list[dict[str, Any]],
    title: str,
    *,
    provider: Any | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    asr_provider = provider
    total = len(assets)
    for index, asset in enumerate(assets, start=1):
        episode_number = int(asset.get("episode_number") or index)
        video_path = Path(str(asset.get("video_path") or ""))
        episode_dir = video_path.parent

        def progress(message: str, completed: int = index - 1) -> None:
            if on_progress:
                on_progress({
                    "stage": "transcript_materialization",
                    "message": message,
                    "completed": completed,
                    "current": index,
                    "total": total,
                    "episode_number": episode_number,
                })

        try:
            if not video_path.is_file():
                raise ValueError("下载视频文件不存在")
            audio_path = episode_dir / "audio.wav"
            progress(f"第 {episode_number} 集：正在提取音频")
            if not audio_path.exists():
                await convert_audio_to_wav(
                    video_path,
                    audio_path,
                    getattr(bilibili_config, "BILI_FFMPEG_PATH", "imageio-ffmpeg"),
                )
            if asr_provider is None:
                asr_provider = create_asr_provider()
            progress(f"第 {episode_number} 集：正在生成语音转写")
            transcript = await asr_provider.transcribe(str(audio_path))
            transcript = transcript if isinstance(transcript, dict) else {}
            text = str(transcript.get("text") or "").strip()
            if not text:
                raise ValueError("语音转写未返回有效文本")
            transcript["source"] = str(transcript.get("source") or "asr")
            transcript_path = episode_dir / "transcript.json"
            transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
            script_path = episode_dir / "script.md"
            script_path.write_text(
                render_script(title, episode_number, transcript["source"], text),
                encoding="utf-8",
            )
            created.append({
                "episode_number": episode_number,
                "audio_path": str(audio_path),
                "transcript_path": str(transcript_path),
                "script_path": str(script_path),
            })
            progress(f"第 {episode_number} 集：剧本转写已生成", completed=index)
        except Exception as exc:
            failed.append({"episode_number": episode_number, "error": str(exc)})
            progress(f"第 {episode_number} 集：剧本转写失败", completed=index)
    return {"created": created, "failed": failed}
