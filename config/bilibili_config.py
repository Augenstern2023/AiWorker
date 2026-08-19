# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/config/bilibili_config.py
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
import os

# bilili 平台配置


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")

# 每天爬取视频/帖子的数量控制
MAX_NOTES_PER_DAY = 1

# 指定B站视频URL列表 (支持完整URL或BV号)
# 示例:
# - 完整URL: "https://www.bilibili.com/video/BV1dwuKzmE26/?spm_id_from=333.1387.homepage.video_card.click"
# - BV号: "BV1d54y1g7db"
BILI_SPECIFIED_ID_LIST = [
    "https://www.bilibili.com/video/BV1dwuKzmE26/?spm_id_from=333.1387.homepage.video_card.click",
    "BV1Sz4y1U77N",
    "BV14Q4y1n7jz",
    # ........................
]

# 指定B站创作者URL列表 (支持完整URL或UID)
# 示例:
# - 完整URL: "https://space.bilibili.com/434377496?spm_id_from=333.1007.0.0"
# - UID: "20813884"
BILI_CREATOR_ID_LIST = [
    "https://space.bilibili.com/434377496?spm_id_from=333.1007.0.0",
    "20813884",
    # ........................
]

# 指定时间范围
START_DAY = "2024-01-01"
END_DAY = "2024-01-01"

# 搜索模式
BILI_SEARCH_MODE = "normal"

# 视频清晰度（qn）配置，常见取值：
# 16=360p, 32=480p, 64=720p, 80=1080p, 112=1080p高码率, 116=1080p60, 120=4K
# 注意：更高清晰度需要账号/视频本身支持
BILI_QN = 80

# B站字幕与分析导出配置
BILI_ENABLE_SUBTITLE = True
BILI_SUBTITLE_LANG_PRIORITY = ["zh-CN", "zh-Hans", "ai-zh", "en-US"]
TROVE_MARKDOWN_OUTPUT_DIR = "data/bili/analysis"

# 后续阶段预留配置，默认关闭
BILI_ENABLE_AUDIO_DOWNLOAD = True
BILI_MEDIA_DOWNLOAD_MODE = "audio"
BILI_AUDIO_FILE_NAME = "audio.m4s"
BILI_ENABLE_ASR = False
BILI_ENABLE_LLM_ANALYSIS = _env_bool("BILI_ENABLE_LLM_ANALYSIS", False)
BILI_ASR_PROVIDER = "sensevoice"
BILI_ASR_MAX_DURATION_SECONDS = 1800
BILI_ASR_OUTPUT_FORMAT = "txt"
BILI_FFMPEG_PATH = "imageio-ffmpeg"
ASR_BASE_URL = os.getenv("ASR_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
ASR_API_KEY = os.getenv("ASR_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("SUB2API_KEY", "")
ASR_MODEL = os.getenv("ASR_MODEL", "whisper-1")
SENSEVOICE_PYTHON = os.getenv("SENSEVOICE_PYTHON", r"D:\qwen3_tts_rainfall\python\python.exe")
SENSEVOICE_MODEL_DIR = os.getenv("SENSEVOICE_MODEL_DIR", r"D:\qwen3_tts_rainfall\models\SenseVoiceSmall")
SENSEVOICE_FFMPEG_DIR = os.getenv("SENSEVOICE_FFMPEG_DIR", r"D:\qwen3_tts_rainfall\python\ffmpeg\bin")
SENSEVOICE_DEVICE = os.getenv("SENSEVOICE_DEVICE", "cuda:0")
SENSEVOICE_SCRIPT_PATH = os.getenv("SENSEVOICE_SCRIPT_PATH", "tools/sensevoice_transcribe.py")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "sub2api")
LLM_ENV_KEY = os.getenv("LLM_ENV_KEY", "SUB2API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "") or os.getenv(LLM_ENV_KEY, "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.6-sol")
LLM_WIRE_API = os.getenv("LLM_WIRE_API", "responses")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "auto")
LLM_ALLOW_INSECURE_HTTP = os.getenv("LLM_ALLOW_INSECURE_HTTP", "")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
LLM_RETRY_BASE_DELAY = float(os.getenv("LLM_RETRY_BASE_DELAY", "1"))
BILI_LLM_MAX_TRANSCRIPT_CHARS = int(os.getenv("BILI_LLM_MAX_TRANSCRIPT_CHARS", "30000"))

# 是否爬取用户信息
CREATOR_MODE = True

# 开始爬取用户信息页码
START_CONTACTS_PAGE = 1

# 单个视频/帖子最大爬取评论数
CRAWLER_MAX_CONTACTS_COUNT_SINGLENOTES = 100

# 单个视频/帖子最大爬取动态数
CRAWLER_MAX_DYNAMICS_COUNT_SINGLENOTES = 50
