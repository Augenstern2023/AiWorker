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
import asyncio
import pathlib
import sys
from typing import Iterable

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv(PROJECT_ROOT / ".env")

import config
from analysis.bili_video_analyzer import BilibiliVideoAnalyzer
from media_platform.bilibili.core import BilibiliCrawler
from media_platform.bilibili.login import BilibiliLogin
from tools import utils


def _parse_output_formats(raw_output: str) -> list[str]:
    formats = [item.strip().lower() for item in raw_output.split(",") if item.strip()]
    supported = {"markdown", "md", "json"}
    unknown = sorted(set(formats) - supported)
    if unknown:
        raise ValueError(f"Unsupported output format: {', '.join(unknown)}")
    return formats or ["markdown", "json"]


async def _create_client(crawler: BilibiliCrawler):
    async with async_playwright() as playwright:
        if config.ENABLE_CDP_MODE:
            crawler.browser_context = await crawler.launch_browser_with_cdp(
                playwright,
                playwright_proxy=None,
                user_agent=crawler.user_agent,
                headless=config.CDP_HEADLESS,
            )
        else:
            crawler.browser_context = await crawler.launch_browser(
                playwright.chromium,
                playwright_proxy=None,
                user_agent=crawler.user_agent,
                headless=config.HEADLESS,
            )
            await crawler.browser_context.add_init_script(path="libs/stealth.min.js")

        crawler.context_page = await crawler.browser_context.new_page()
        await crawler.context_page.goto(crawler.index_url)
        crawler.bili_client = await crawler.create_bilibili_client(httpx_proxy=None)

        if not await crawler.bili_client.pong():
            login_obj = BilibiliLogin(
                login_type=config.LOGIN_TYPE,
                login_phone="",
                browser_context=crawler.browser_context,
                context_page=crawler.context_page,
                cookie_str=config.COOKIES,
            )
            await login_obj.begin()
            await crawler.bili_client.update_cookies(browser_context=crawler.browser_context)

        yield crawler.bili_client


async def analyze_video(video_id: str, output_formats: Iterable[str], with_asr: bool = False, with_llm: bool = False) -> dict:
    config.PLATFORM = "bili"
    if with_asr:
        config.BILI_ENABLE_ASR = True
    if with_llm:
        config.BILI_ENABLE_LLM_ANALYSIS = True
    crawler = BilibiliCrawler()
    try:
        async for client in _create_client(crawler):
            analyzer = BilibiliVideoAnalyzer(client)
            return await analyzer.analyze_with_options(video_id, output_formats, with_asr=with_asr, with_llm=with_llm)
    finally:
        if getattr(config, "AUTO_CLOSE_BROWSER", True):
            await crawler.close()

    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a Bilibili video into transcript, JSON, and Markdown files.")
    parser.add_argument("--id", required=True, help="Bilibili BV id or video URL.")
    parser.add_argument("--output", default="markdown,json", help="Comma-separated output formats: markdown,json.")
    parser.add_argument("--with-asr", action="store_true", help="Use ASR fallback when no subtitle is available.")
    parser.add_argument("--with-llm", action="store_true", help="Generate summary, key points, tags, and risk flags with LLM.")
    args = parser.parse_args()

    output_formats = _parse_output_formats(args.output)
    output_paths = asyncio.run(analyze_video(args.id, output_formats, with_asr=args.with_asr, with_llm=args.with_llm))
    for output_type, output_path in output_paths.items():
        utils.logger.info(f"[analyze_bilibili_video] {output_type}: {output_path}")
        print(f"{output_type}: {output_path}")


if __name__ == "__main__":
    main()
