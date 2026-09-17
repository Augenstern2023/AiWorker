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
import logging
import os


logger = logging.getLogger(__name__)

# bilili 平台配置


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid integer for %s; using default %s", name, default)
        return default


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in allowed:
        return normalized
    logger.warning("Invalid choice for %s; using default %s", name, default)
    return default

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
SENSEVOICE_VAD_MODEL_DIR = os.getenv(
    "SENSEVOICE_VAD_MODEL_DIR",
    r"D:\qwen3_tts_rainfall\models\fsmn-vad",
)
SENSEVOICE_FFMPEG_DIR = os.getenv("SENSEVOICE_FFMPEG_DIR", r"D:\qwen3_tts_rainfall\python\ffmpeg\bin")
SENSEVOICE_DEVICE = os.getenv("SENSEVOICE_DEVICE", "cuda:0")
SENSEVOICE_SCRIPT_PATH = os.getenv("SENSEVOICE_SCRIPT_PATH", "tools/sensevoice_transcribe.py")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "sub2api")
LLM_ENV_KEY = os.getenv("LLM_ENV_KEY", "SUB2API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "") or os.getenv(LLM_ENV_KEY, "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.6-sol")
# 留空时由所选提供商预设决定，避免全局默认覆盖 provider 专属协议。
LLM_WIRE_API = os.getenv("LLM_WIRE_API", "")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "auto")
LLM_MAX_OUTPUT_TOKENS = _env_int("LLM_MAX_OUTPUT_TOKENS", 32768)
LLM_ALLOW_INSECURE_HTTP = os.getenv("LLM_ALLOW_INSECURE_HTTP", "")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
LLM_RETRY_BASE_DELAY = float(os.getenv("LLM_RETRY_BASE_DELAY", "1"))
LLM_RETRY_READ_TIMEOUTS = _env_bool("LLM_RETRY_READ_TIMEOUTS", True)
# 视频参数推荐是受白名单约束的分类任务，不需要继承剧本创作的高推理、
# 大输出和长重试配置。90 秒内未返回时由 worker 自动降级为规则建议。
VIDEO_PARAMETER_LLM_REASONING_EFFORT = _env_choice(
    "VIDEO_PARAMETER_LLM_REASONING_EFFORT",
    "low",
    {"none", "low", "medium"},
)
VIDEO_PARAMETER_LLM_MAX_OUTPUT_TOKENS = _env_int(
    "VIDEO_PARAMETER_LLM_MAX_OUTPUT_TOKENS", 8192
)
VIDEO_PARAMETER_LLM_TIMEOUT_SECONDS = _env_int(
    "VIDEO_PARAMETER_LLM_TIMEOUT_SECONDS", 90
)
VIDEO_PARAMETER_LLM_MAX_RETRIES = _env_int(
    "VIDEO_PARAMETER_LLM_MAX_RETRIES", 1
)
VIDEO_PARAMETER_LLM_RETRY_READ_TIMEOUTS = _env_bool(
    "VIDEO_PARAMETER_LLM_RETRY_READ_TIMEOUTS", False
)
# 整段规格分析的硬时限。即使底层请求或重试未按预期结束，也会及时降级，
# 避免剧本页长期停留在“分析中”。
VIDEO_PARAMETER_ANALYSIS_DEADLINE_SECONDS = _env_int(
    "VIDEO_PARAMETER_ANALYSIS_DEADLINE_SECONDS", 60
)
# 正式剧本审核按规模拆成逐集、批次汇总和最终总评。各请求使用独立短时限，
# 已经等待到读取超时的请求不再重复提交；连接错误和网关错误仍可重试一次。
SCREENPLAY_REVIEW_UNIT_LLM_REASONING_EFFORT = _env_choice(
    "SCREENPLAY_REVIEW_UNIT_LLM_REASONING_EFFORT",
    "high",
    {"low", "medium", "high", "xhigh", "max"},
)
SCREENPLAY_REVIEW_UNIT_LLM_TIMEOUT_SECONDS = _env_int(
    "SCREENPLAY_REVIEW_UNIT_LLM_TIMEOUT_SECONDS", 240
)
SCREENPLAY_REVIEW_UNIT_LLM_MAX_RETRIES = _env_int(
    "SCREENPLAY_REVIEW_UNIT_LLM_MAX_RETRIES", 1
)
SCREENPLAY_REVIEW_UNIT_LLM_RETRY_READ_TIMEOUTS = _env_bool(
    "SCREENPLAY_REVIEW_UNIT_LLM_RETRY_READ_TIMEOUTS", False
)
SCREENPLAY_REVIEW_SUMMARY_LLM_REASONING_EFFORT = _env_choice(
    "SCREENPLAY_REVIEW_SUMMARY_LLM_REASONING_EFFORT",
    "medium",
    {"low", "medium", "high", "xhigh", "max"},
)
SCREENPLAY_REVIEW_SUMMARY_LLM_TIMEOUT_SECONDS = _env_int(
    "SCREENPLAY_REVIEW_SUMMARY_LLM_TIMEOUT_SECONDS", 180
)
SCREENPLAY_REVIEW_SUMMARY_LLM_MAX_RETRIES = _env_int(
    "SCREENPLAY_REVIEW_SUMMARY_LLM_MAX_RETRIES", 1
)
SCREENPLAY_REVIEW_SUMMARY_LLM_RETRY_READ_TIMEOUTS = _env_bool(
    "SCREENPLAY_REVIEW_SUMMARY_LLM_RETRY_READ_TIMEOUTS", False
)
# 完整项目审核同样以有界工作单元运行，不沿用全局的 600 秒长等待配置。
PROJECT_REVIEW_LLM_REASONING_EFFORT = _env_choice(
    "PROJECT_REVIEW_LLM_REASONING_EFFORT",
    "high",
    {"low", "medium", "high", "xhigh", "max"},
)
PROJECT_REVIEW_LLM_TIMEOUT_SECONDS = _env_int(
    "PROJECT_REVIEW_LLM_TIMEOUT_SECONDS", 240
)
PROJECT_REVIEW_LLM_MAX_RETRIES = _env_int(
    "PROJECT_REVIEW_LLM_MAX_RETRIES", 1
)
PROJECT_REVIEW_LLM_RETRY_READ_TIMEOUTS = _env_bool(
    "PROJECT_REVIEW_LLM_RETRY_READ_TIMEOUTS", False
)
# 审核后优化逐集执行；结构优化拆成 Beats 和 Script 两个请求。
SCREENPLAY_OPTIMIZATION_LLM_REASONING_EFFORT = _env_choice(
    "SCREENPLAY_OPTIMIZATION_LLM_REASONING_EFFORT",
    "high",
    {"low", "medium", "high", "xhigh", "max"},
)
SCREENPLAY_OPTIMIZATION_LLM_TIMEOUT_SECONDS = _env_int(
    "SCREENPLAY_OPTIMIZATION_LLM_TIMEOUT_SECONDS", 240
)
SCREENPLAY_OPTIMIZATION_LLM_MAX_RETRIES = _env_int(
    "SCREENPLAY_OPTIMIZATION_LLM_MAX_RETRIES", 1
)
SCREENPLAY_OPTIMIZATION_LLM_RETRY_READ_TIMEOUTS = _env_bool(
    "SCREENPLAY_OPTIMIZATION_LLM_RETRY_READ_TIMEOUTS", False
)
# 编剧大师是同步对话，不复用长任务的 600 秒读取超时与多次自动重试。
# 单轮模型调用最多等待 180 秒，外层请求再以 210 秒作最终兜底。
WRITER_AGENT_LLM_REASONING_EFFORT = os.getenv(
    "WRITER_AGENT_LLM_REASONING_EFFORT", "high"
)
WRITER_AGENT_LLM_TIMEOUT_SECONDS = int(
    os.getenv("WRITER_AGENT_LLM_TIMEOUT_SECONDS", "180")
)
WRITER_AGENT_LLM_MAX_RETRIES = int(
    os.getenv("WRITER_AGENT_LLM_MAX_RETRIES", "0")
)
WRITER_AGENT_MESSAGE_TIMEOUT_SECONDS = float(
    os.getenv("WRITER_AGENT_MESSAGE_TIMEOUT_SECONDS", "210")
)
BILI_LLM_MAX_TRANSCRIPT_CHARS = int(os.getenv("BILI_LLM_MAX_TRANSCRIPT_CHARS", "30000"))

# 是否爬取用户信息
CREATOR_MODE = True

# 开始爬取用户信息页码
START_CONTACTS_PAGE = 1

# 单个视频/帖子最大爬取评论数
CRAWLER_MAX_CONTACTS_COUNT_SINGLENOTES = 100

# 单个视频/帖子最大爬取动态数
CRAWLER_MAX_DYNAMICS_COUNT_SINGLENOTES = 50
