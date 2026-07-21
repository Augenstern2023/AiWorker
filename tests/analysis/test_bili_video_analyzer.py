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

import pytest

from analysis.bili_video_analyzer import BilibiliVideoAnalyzer
from analysis.asr import SenseVoiceASRProvider


class FakeBilibiliClient:
    async def get_video_info(self, aid=None, bvid=None):
        return {
            "View": {
                "aid": 123,
                "bvid": bvid or "BV123",
                "cid": 456,
                "title": "测试视频",
                "desc": "简介",
                "pubdate": 1700000000,
                "owner": {"name": "测试UP"},
                "stat": {"view": 1, "like": 2, "coin": 3, "favorite": 4, "reply": 5},
            }
        }

    async def get_player_info(self, aid, cid):
        return {
            "subtitle": {
                "subtitles": [
                    {"lan": "zh-CN", "subtitle_url": "//example.com/subtitle.json"},
                ]
            }
        }

    async def get_subtitle(self, subtitle_url):
        return {"body": [{"from": 0, "to": 1, "content": "字幕文本"}]}


@pytest.mark.asyncio
async def test_analyzer_skips_asr_when_subtitle_exists(tmp_path, monkeypatch):
    analyzer = BilibiliVideoAnalyzer(
        FakeBilibiliClient(),
        video_store_path=str(tmp_path / "videos"),
        analysis_output_dir=str(tmp_path / "analysis"),
    )

    called = False

    async def fake_fetch_asr(aid, cid):
        nonlocal called
        called = True
        return {"source": "asr", "text": "asr文本"}

    monkeypatch.setattr(analyzer, "_fetch_asr_transcript", fake_fetch_asr)

    await analyzer.analyze_with_options("BV123", ["json"], with_asr=True)

    assert called is False
    assert (tmp_path / "videos" / "123" / "transcript.txt").read_text(encoding="utf-8") == "字幕文本"


@pytest.mark.asyncio
async def test_analyzer_attempts_asr_when_subtitle_absent_and_enabled(tmp_path, monkeypatch):
    analyzer = BilibiliVideoAnalyzer(
        FakeBilibiliClient(),
        video_store_path=str(tmp_path / "videos"),
        analysis_output_dir=str(tmp_path / "analysis"),
    )

    async def no_subtitle(aid, cid):
        return None

    async def fake_fetch_asr(aid, cid):
        return {"source": "asr", "text": "asr文本"}

    monkeypatch.setattr(analyzer, "_fetch_transcript", no_subtitle)
    monkeypatch.setattr(analyzer, "_fetch_asr_transcript", fake_fetch_asr)

    await analyzer.analyze_with_options("BV123", ["json"], with_asr=True)

    assert '"transcript_source": "asr"' in (tmp_path / "analysis" / "123.json").read_text(encoding="utf-8")


def test_create_asr_provider_supports_sensevoice(monkeypatch):
    analyzer = BilibiliVideoAnalyzer(FakeBilibiliClient())
    monkeypatch.setattr("config.BILI_ASR_PROVIDER", "sensevoice", raising=False)
    monkeypatch.setattr("config.SENSEVOICE_PYTHON", r"D:\qwen3_tts_rainfall\python\python.exe", raising=False)
    monkeypatch.setattr("config.SENSEVOICE_MODEL_DIR", r"D:\qwen3_tts_rainfall\models\SenseVoiceSmall", raising=False)
    monkeypatch.setattr("config.SENSEVOICE_FFMPEG_DIR", r"D:\qwen3_tts_rainfall\python\ffmpeg\bin", raising=False)
    monkeypatch.setattr("config.SENSEVOICE_DEVICE", "cuda:0", raising=False)
    monkeypatch.setattr("config.SENSEVOICE_SCRIPT_PATH", "tools/sensevoice_transcribe.py", raising=False)

    provider = analyzer._create_asr_provider()

    assert isinstance(provider, SenseVoiceASRProvider)


@pytest.mark.asyncio
async def test_apply_llm_analysis_updates_result(monkeypatch):
    analyzer = BilibiliVideoAnalyzer(FakeBilibiliClient())
    result = await analyzer._build_result(await FakeBilibiliClient().get_video_info(bvid="BV123"))
    result.transcript_source = "asr"
    result.transcript = "这是转写文本"

    class FakeLLMProvider:
        async def analyze_video(self, payload):
            return {
                "summary": "摘要",
                "key_points": ["要点"],
                "tags": ["标签"],
                "content_type": "vlog",
                "sentiment": "中性",
                "audience": "观众",
                "action_items": ["核实"],
                "risk_flags": ["风险"],
                "provider": "minimax",
            }

    monkeypatch.setattr("config.BILI_ENABLE_LLM_ANALYSIS", True, raising=False)
    monkeypatch.setattr(analyzer, "_create_llm_provider", lambda: FakeLLMProvider())

    await analyzer._apply_llm_analysis(result)

    assert result.summary == "摘要"
    assert result.key_points == ["要点"]
    assert result.tags == ["标签"]
    assert result.analysis["risk_flags"] == ["风险"]
    assert result.analysis["provider"] == "minimax"
