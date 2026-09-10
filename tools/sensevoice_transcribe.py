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

import argparse
import json
import os
import sys
import traceback
from contextlib import redirect_stdout


def _parse_args():
    parser = argparse.ArgumentParser(description="Transcribe audio with local SenseVoiceSmall.")
    parser.add_argument("--audio", required=True, help="Path to wav/audio file.")
    parser.add_argument("--model-dir", required=True, help="SenseVoiceSmall model directory.")
    parser.add_argument(
        "--vad-model-dir",
        default=os.getenv("SENSEVOICE_VAD_MODEL_DIR", "fsmn-vad"),
        help="Local FSMN VAD model directory.",
    )
    parser.add_argument("--device", default="cuda:0", help="cuda:0 or cpu.")
    parser.add_argument("--ffmpeg-dir", default="", help="Directory containing ffmpeg.exe.")
    parser.add_argument("--language", default="auto", help="SenseVoice language option.")
    return parser.parse_args()


def _emit(payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8", errors="replace") + b"\n"
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main() -> int:
    args = _parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if args.ffmpeg_dir:
        os.environ["PATH"] = args.ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    try:
        # Keep noisy model logs off stdout so the parent process can parse the final JSON line.
        with redirect_stdout(sys.stderr):
            from funasr import AutoModel
            from funasr.utils.postprocess_utils import rich_transcription_postprocess

            model = AutoModel(
                model=args.model_dir,
                trust_remote_code=True,
                vad_model=args.vad_model_dir,
                vad_kwargs={"max_single_segment_time": 30000},
                device=args.device,
                disable_update=True,
            )
            result = model.generate(
                input=args.audio,
                cache={},
                language=args.language,
                use_itn=True,
                batch_size_s=60,
                merge_vad=True,
                merge_length_s=15,
            )

        text = ""
        raw_segments = []
        if result:
            first = result[0]
            text = rich_transcription_postprocess(first.get("text", ""))
            raw_segments = first.get("sentence_info") or first.get("sentences") or []

        segments = []
        for item in raw_segments:
            segment_text = rich_transcription_postprocess(str(item.get("text", ""))).strip()
            if not segment_text:
                continue
            start = item.get("start", 0) or 0
            end = item.get("end", 0) or 0
            segments.append({
                "start": float(start) / 1000 if start > 10000 else float(start),
                "end": float(end) / 1000 if end > 10000 else float(end),
                "text": segment_text,
            })

        _emit({
            "source": "asr",
            "provider": "sensevoice",
            "language": args.language,
            "segments": segments,
            "text": text,
        })
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _emit({
            "source": "asr",
            "provider": "sensevoice",
            "language": args.language,
            "segments": [],
            "text": "",
            "error": str(exc),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
