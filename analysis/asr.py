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

import asyncio
import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional

import httpx


class ASRError(RuntimeError):
    pass


class ASRProvider:
    async def transcribe(self, audio_path: str) -> Dict[str, Any]:
        raise NotImplementedError


class OpenAICompatibleASRProvider(ASRProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "whisper-1",
        timeout: int = 300,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def transcribe(self, audio_path: str) -> Dict[str, Any]:
        if not self.base_url:
            raise ASRError("ASR_BASE_URL is required for OpenAI-compatible ASR.")
        if not self.api_key:
            raise ASRError("ASR_API_KEY is required for OpenAI-compatible ASR.")

        endpoint = f"{self.base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = {
            "model": self.model,
            "response_format": "verbose_json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            with open(audio_path, "rb") as audio_file:
                files = {"file": (pathlib.Path(audio_path).name, audio_file, "audio/wav")}
                response = await client.post(endpoint, headers=headers, data=data, files=files)
        response.raise_for_status()
        return normalize_openai_asr_response(response.json(), provider="openai-compatible")


class SenseVoiceASRProvider(ASRProvider):
    def __init__(
        self,
        python_path: str,
        model_dir: str,
        ffmpeg_dir: str = "",
        device: str = "cuda:0",
        script_path: str = "tools/sensevoice_transcribe.py",
        timeout: int = 3600,
    ):
        self.python_path = python_path
        self.model_dir = model_dir
        self.ffmpeg_dir = ffmpeg_dir
        self.device = device
        self.script_path = script_path
        self.timeout = timeout

    async def transcribe(self, audio_path: str) -> Dict[str, Any]:
        command = build_sensevoice_command(
            python_path=self.python_path,
            script_path=self.script_path,
            audio_path=audio_path,
            model_dir=self.model_dir,
            device=self.device,
            ffmpeg_dir=self.ffmpeg_dir,
        )
        env = build_sensevoice_env(os.environ.copy(), self.ffmpeg_dir)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise ASRError(f"SenseVoice python executable not found: {self.python_path}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            raise ASRError("SenseVoice transcription timed out.") from exc

        stdout_text = stdout.decode("utf-8", errors="ignore").strip()
        stderr_text = stderr.decode("utf-8", errors="ignore").strip()
        if process.returncode != 0:
            raise ASRError(f"SenseVoice failed: {stderr_text or stdout_text}")
        return parse_sensevoice_stdout(stdout_text)


def build_sensevoice_command(
    python_path: str,
    script_path: str,
    audio_path: str,
    model_dir: str,
    device: str,
    ffmpeg_dir: str = "",
) -> List[str]:
    command = [
        python_path,
        "-I",
        script_path,
        "--audio",
        audio_path,
        "--model-dir",
        model_dir,
        "--device",
        device,
    ]
    if ffmpeg_dir:
        command.extend(["--ffmpeg-dir", ffmpeg_dir])
    return command


def build_sensevoice_env(env: Dict[str, str], ffmpeg_dir: str = "") -> Dict[str, str]:
    for variable in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "VIRTUAL_ENV",
    ):
        env.pop(variable, None)
    if ffmpeg_dir:
        env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def parse_sensevoice_stdout(stdout_text: str) -> Dict[str, Any]:
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    else:
        raise ASRError("SenseVoice did not output JSON.")

    if payload.get("source") == "asr":
        return payload
    return normalize_sensevoice_response(payload)


def normalize_sensevoice_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = str(payload.get("text", "")).strip()
    segments = payload.get("segments") or []
    if not isinstance(segments, list):
        segments = []
    return {
        "source": "asr",
        "provider": "sensevoice",
        "language": payload.get("language", ""),
        "segments": segments,
        "text": text,
    }


def normalize_openai_asr_response(payload: Dict[str, Any], provider: str = "openai-compatible") -> Dict[str, Any]:
    segments: List[Dict[str, Any]] = []
    for item in payload.get("segments") or []:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        segments.append({
            "start": float(item.get("start", 0) or 0),
            "end": float(item.get("end", 0) or 0),
            "text": text,
        })

    text = str(payload.get("text", "")).strip()
    if not text and segments:
        text = "\n".join(segment["text"] for segment in segments)

    return {
        "source": "asr",
        "provider": provider,
        "language": payload.get("language", ""),
        "segments": segments,
        "text": text,
    }


def build_ffmpeg_command(ffmpeg_path: str, input_path: pathlib.Path, output_path: pathlib.Path) -> List[str]:
    return [
        resolve_ffmpeg_path(ffmpeg_path),
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_path),
    ]


def resolve_ffmpeg_path(ffmpeg_path: str) -> str:
    if ffmpeg_path == "imageio-ffmpeg":
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            raise ASRError("imageio-ffmpeg is not available. Run `uv add imageio-ffmpeg`.") from exc
    return ffmpeg_path


def default_sensevoice_script_path() -> str:
    return str(pathlib.Path(__file__).resolve().parents[1] / "tools" / "sensevoice_transcribe.py")


async def convert_audio_to_wav(input_path: pathlib.Path, output_path: pathlib.Path, ffmpeg_path: str) -> pathlib.Path:
    command = build_ffmpeg_command(ffmpeg_path, input_path, output_path)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ASRError(f"ffmpeg executable not found: {ffmpeg_path}") from exc

    _, stderr = await process.communicate()
    if process.returncode != 0:
        error_text = stderr.decode("utf-8", errors="ignore").strip()
        raise ASRError(f"ffmpeg failed: {error_text}")
    return output_path
