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

import json
import pathlib
from datetime import datetime
from typing import Dict, Iterable, Optional

import aiofiles

import config
from analysis.asr import (
    ASRError,
    OpenAICompatibleASRProvider,
    SenseVoiceASRProvider,
    convert_audio_to_wav,
    default_sensevoice_script_path,
)
from analysis.exporters.json_exporter import export_json
from analysis.exporters.markdown_exporter import export_markdown
from analysis.llm import LLMError, create_llm_provider_from_config
from analysis.media import get_duration_seconds, get_media_url, pick_best_dash_audio, save_bytes
from analysis.schema import BilibiliAnalysisResult
from analysis.subtitle import (
    get_subtitle_items,
    get_subtitle_language,
    get_subtitle_url,
    parse_bilibili_subtitle,
    select_subtitle,
)
from media_platform.bilibili.client import BilibiliClient
from media_platform.bilibili.help import parse_video_info_from_url
from tools import utils


class BilibiliVideoAnalyzer:
    def __init__(
        self,
        bili_client: BilibiliClient,
        video_store_path: str = "data/bili/videos",
        analysis_output_dir: Optional[str] = None,
    ):
        self.bili_client = bili_client
        self.video_store_path = pathlib.Path(video_store_path)
        self.analysis_output_dir = pathlib.Path(
            analysis_output_dir or getattr(config, "TROVE_MARKDOWN_OUTPUT_DIR", "data/bili/analysis")
        )

    async def analyze(self, video_id_or_url: str, output_formats: Iterable[str]) -> Dict[str, pathlib.Path]:
        return await self.analyze_with_options(video_id_or_url, output_formats, with_asr=False)

    async def analyze_with_options(
        self,
        video_id_or_url: str,
        output_formats: Iterable[str],
        with_asr: bool = False,
        with_llm: bool = False,
    ) -> Dict[str, pathlib.Path]:
        video_id = parse_video_info_from_url(video_id_or_url).video_id
        detail = await self.bili_client.get_video_info(bvid=video_id)
        result = await self._build_result(detail)
        cid = self._get_cid(detail)

        transcript_payload = await self._fetch_transcript(result.aid, cid)
        if not transcript_payload and with_asr:
            transcript_payload = await self._fetch_asr_transcript(result.aid, cid)
        if transcript_payload:
            result.transcript_source = transcript_payload.get("source", "")
            result.transcript = transcript_payload.get("text", "")

        if with_llm:
            await self._apply_llm_analysis(result)

        output_paths: Dict[str, pathlib.Path] = {}
        formats = {item.strip().lower() for item in output_formats}
        if "json" in formats:
            output_paths["json"] = export_json(result, self.analysis_output_dir)
        if "markdown" in formats or "md" in formats:
            output_paths["markdown"] = export_markdown(result, self.analysis_output_dir)
        return output_paths

    async def _build_result(self, detail: Dict) -> BilibiliAnalysisResult:
        view = detail.get("View") or {}
        owner = view.get("owner") or {}
        stat = view.get("stat") or {}
        aid = int(view.get("aid") or 0)
        bvid = view.get("bvid", "")

        return BilibiliAnalysisResult(
            aid=aid,
            bvid=bvid,
            title=view.get("title", ""),
            url=f"https://www.bilibili.com/video/{bvid or 'av' + str(aid)}",
            author=owner.get("name", ""),
            desc=view.get("desc", ""),
            published_at=self._format_pubdate(view.get("pubdate")),
            stats={
                "view": stat.get("view", 0),
                "like": stat.get("like", 0),
                "coin": stat.get("coin", 0),
                "favorite": stat.get("favorite", 0),
                "reply": stat.get("reply", 0),
            },
        )

    def _get_cid(self, detail: Dict) -> int:
        view = detail.get("View") or {}
        return int(view.get("cid") or 0)

    async def _fetch_transcript(self, aid: int, cid: int) -> Optional[Dict]:
        if not getattr(config, "BILI_ENABLE_SUBTITLE", True):
            return None

        try:
            player_info = await self.bili_client.get_player_info(aid=aid, cid=cid)
            subtitle_items = get_subtitle_items(player_info)
            subtitle_item = select_subtitle(
                subtitle_items,
                getattr(config, "BILI_SUBTITLE_LANG_PRIORITY", ["zh-CN", "zh-Hans", "ai-zh", "en-US"]),
            )
            if not subtitle_item:
                utils.logger.warning(f"[BilibiliVideoAnalyzer] No subtitle found for aid={aid}, cid={cid}")
                return None

            subtitle_url = get_subtitle_url(subtitle_item)
            subtitle_json = await self.bili_client.get_subtitle(subtitle_url)
            await self._save_subtitle_files(
                aid=aid,
                subtitle_json=subtitle_json,
                transcript=parse_bilibili_subtitle(
                    subtitle_json,
                    language=get_subtitle_language(subtitle_item),
                ),
            )
            return parse_bilibili_subtitle(subtitle_json, language=get_subtitle_language(subtitle_item))
        except Exception as exc:
            utils.logger.warning(f"[BilibiliVideoAnalyzer] Fetch subtitle failed for aid={aid}, cid={cid}: {exc}")
            return None

    async def _fetch_asr_transcript(self, aid: int, cid: int) -> Optional[Dict]:
        if not getattr(config, "BILI_ENABLE_ASR", False):
            utils.logger.warning("[BilibiliVideoAnalyzer] ASR requested but BILI_ENABLE_ASR is disabled.")
            return None

        try:
            audio_path = self.video_store_path / str(aid) / getattr(config, "BILI_AUDIO_FILE_NAME", "audio.m4s")
            wav_path = audio_path.with_name("audio.wav")
            if not wav_path.exists():
                if not audio_path.exists():
                    audio_path = await self._download_audio(aid, cid)
                    if not audio_path:
                        return None

                    play_url = await self.bili_client.get_video_play_url(aid=aid, cid=cid)
                    duration_seconds = get_duration_seconds(play_url)
                    max_duration = getattr(config, "BILI_ASR_MAX_DURATION_SECONDS", 1800)
                    if duration_seconds and duration_seconds > max_duration:
                        utils.logger.warning(
                            f"[BilibiliVideoAnalyzer] Skip ASR for aid={aid}: duration {duration_seconds}s exceeds {max_duration}s."
                        )
                        return None

                await convert_audio_to_wav(audio_path, wav_path, getattr(config, "BILI_FFMPEG_PATH", "ffmpeg"))
            provider = self._create_asr_provider()
            transcript = await provider.transcribe(str(wav_path))
            await self._save_transcript_file(aid, transcript.get("text", ""))
            return transcript
        except Exception as exc:
            utils.logger.warning(f"[BilibiliVideoAnalyzer] ASR transcript failed for aid={aid}, cid={cid}: {exc}")
            return None

    async def _download_audio(self, aid: int, cid: int) -> Optional[pathlib.Path]:
        if not getattr(config, "BILI_ENABLE_AUDIO_DOWNLOAD", True):
            utils.logger.warning("[BilibiliVideoAnalyzer] Audio download is disabled.")
            return None

        play_url = await self.bili_client.get_video_play_url(aid=aid, cid=cid)
        audio_item = pick_best_dash_audio(play_url)
        if not audio_item:
            utils.logger.warning(f"[BilibiliVideoAnalyzer] No DASH audio found for aid={aid}, cid={cid}")
            return None

        audio_url = get_media_url(audio_item)
        if not audio_url:
            utils.logger.warning(f"[BilibiliVideoAnalyzer] DASH audio URL is empty for aid={aid}, cid={cid}")
            return None

        content = await self.bili_client.get_video_media(audio_url)
        if content is None:
            utils.logger.warning(f"[BilibiliVideoAnalyzer] Download audio failed for aid={aid}, cid={cid}")
            return None

        audio_file_name = getattr(config, "BILI_AUDIO_FILE_NAME", "audio.m4s")
        audio_path = self.video_store_path / str(aid) / audio_file_name
        return await save_bytes(audio_path, content)

    def _create_asr_provider(self):
        provider = getattr(config, "BILI_ASR_PROVIDER", "openai")
        if provider == "openai":
            return OpenAICompatibleASRProvider(
                base_url=getattr(config, "ASR_BASE_URL", ""),
                api_key=getattr(config, "ASR_API_KEY", ""),
                model=getattr(config, "ASR_MODEL", "whisper-1"),
            )
        if provider == "sensevoice":
            return SenseVoiceASRProvider(
                python_path=getattr(config, "SENSEVOICE_PYTHON", ""),
                model_dir=getattr(config, "SENSEVOICE_MODEL_DIR", ""),
                vad_model_dir=getattr(config, "SENSEVOICE_VAD_MODEL_DIR", "fsmn-vad"),
                ffmpeg_dir=getattr(config, "SENSEVOICE_FFMPEG_DIR", ""),
                device=getattr(config, "SENSEVOICE_DEVICE", "cuda:0"),
                script_path=getattr(config, "SENSEVOICE_SCRIPT_PATH", default_sensevoice_script_path()),
            )
        raise ASRError(f"Unsupported ASR provider: {provider}")

    async def _apply_llm_analysis(self, result: BilibiliAnalysisResult) -> None:
        if not getattr(config, "BILI_ENABLE_LLM_ANALYSIS", False):
            utils.logger.warning("[BilibiliVideoAnalyzer] LLM requested but BILI_ENABLE_LLM_ANALYSIS is disabled.")
            return
        if not result.transcript:
            utils.logger.warning("[BilibiliVideoAnalyzer] LLM analysis skipped because transcript is empty.")
            return

        try:
            provider = self._create_llm_provider()
            payload = self._build_llm_payload(result)
            analysis = await provider.analyze_video(payload)
            result.summary = analysis.get("summary", "")
            result.key_points = analysis.get("key_points", [])
            result.tags = analysis.get("tags", [])
            result.analysis = {
                "content_type": analysis.get("content_type", ""),
                "sentiment": analysis.get("sentiment", ""),
                "audience": analysis.get("audience", ""),
                "action_items": analysis.get("action_items", []),
                "risk_flags": analysis.get("risk_flags", []),
                "provider": analysis.get("provider", ""),
                "transcript_truncated": payload.get("transcript_truncated", False),
                "transcript_chars_used": payload.get("transcript_chars_used", len(result.transcript)),
            }
        except Exception as exc:
            utils.logger.warning(f"[BilibiliVideoAnalyzer] LLM analysis failed for aid={result.aid}: {exc}")

    def _create_llm_provider(self):
        return create_llm_provider_from_config(config)

    def _build_llm_payload(self, result: BilibiliAnalysisResult) -> Dict:
        max_chars = getattr(config, "BILI_LLM_MAX_TRANSCRIPT_CHARS", 30000)
        transcript = result.transcript or ""
        truncated = len(transcript) > max_chars
        transcript_used = transcript[:max_chars]
        return {
            "source": "bilibili",
            "aid": result.aid,
            "bvid": result.bvid,
            "url": result.url,
            "title": result.title,
            "author": result.author,
            "desc": result.desc,
            "published_at": result.published_at,
            "stats": result.stats,
            "transcript_source": result.transcript_source,
            "transcript": transcript_used,
            "transcript_truncated": truncated,
            "transcript_chars_used": len(transcript_used),
        }

    async def _save_subtitle_files(self, aid: int, subtitle_json: Dict, transcript: Dict) -> None:
        video_dir = self.video_store_path / str(aid)
        video_dir.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(video_dir / "subtitle.json", "w", encoding="utf-8") as file:
            await file.write(json.dumps(subtitle_json, ensure_ascii=False, indent=2))
        async with aiofiles.open(video_dir / "transcript.txt", "w", encoding="utf-8") as file:
            await file.write(transcript.get("text", ""))

    async def _save_transcript_file(self, aid: int, transcript_text: str) -> None:
        video_dir = self.video_store_path / str(aid)
        video_dir.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(video_dir / "transcript.txt", "w", encoding="utf-8") as file:
            await file.write(transcript_text)

    def _format_pubdate(self, pubdate: object) -> str:
        if not pubdate:
            return ""
        try:
            return datetime.fromtimestamp(int(pubdate)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return str(pubdate)
