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

from pathlib import Path

from analysis.asr import (
    build_ffmpeg_command,
    build_sensevoice_command,
    build_sensevoice_env,
    normalize_openai_asr_response,
    parse_sensevoice_stdout,
    resolve_ffmpeg_path,
)


def test_build_ffmpeg_command_uses_configured_executable():
    command = build_ffmpeg_command("C:/ffmpeg/bin/ffmpeg.exe", Path("audio.m4s"), Path("audio.wav"))

    assert command[0] == "C:/ffmpeg/bin/ffmpeg.exe"
    assert "-i" in command
    assert "audio.m4s" in command
    assert "audio.wav" in command


def test_resolve_ffmpeg_path_keeps_normal_executable_name():
    assert resolve_ffmpeg_path("ffmpeg") == "ffmpeg"


def test_normalize_openai_asr_response_maps_verbose_json():
    result = normalize_openai_asr_response(
        {
            "language": "zh",
            "text": "完整文本",
            "segments": [
                {"start": 0, "end": 1.5, "text": "第一句"},
                {"start": 1.5, "end": 3, "text": "第二句"},
            ],
        }
    )

    assert result["source"] == "asr"
    assert result["provider"] == "openai-compatible"
    assert result["language"] == "zh"
    assert result["text"] == "完整文本"
    assert result["segments"] == [
        {"start": 0.0, "end": 1.5, "text": "第一句"},
        {"start": 1.5, "end": 3.0, "text": "第二句"},
    ]


def test_build_sensevoice_command_includes_runtime_and_model_paths():
    command = build_sensevoice_command(
        python_path="D:/qwen/python/python.exe",
        script_path="tools/sensevoice_transcribe.py",
        audio_path="audio.wav",
        model_dir="D:/models/SenseVoiceSmall",
        device="cuda:0",
        ffmpeg_dir="D:/qwen/python/ffmpeg/bin",
    )

    assert command[:2] == ["D:/qwen/python/python.exe", "tools/sensevoice_transcribe.py"]
    assert "--audio" in command
    assert "audio.wav" in command
    assert "--model-dir" in command
    assert "D:/models/SenseVoiceSmall" in command
    assert "--ffmpeg-dir" in command


def test_build_sensevoice_env_prepends_ffmpeg_path():
    env = build_sensevoice_env({"PATH": "old"}, "D:/ffmpeg/bin")

    assert env["PATH"].startswith("D:/ffmpeg/bin")
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_parse_sensevoice_stdout_uses_last_json_line():
    result = parse_sensevoice_stdout(
        'loading model...\n{"source":"asr","provider":"sensevoice","language":"auto","segments":[],"text":"识别文本"}'
    )

    assert result["source"] == "asr"
    assert result["provider"] == "sensevoice"
    assert result["text"] == "识别文本"
