# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tools/explore_douyin_rank_cdp.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# Statement: This code is only for learning and research. Please comply with
# target platform terms, robots.txt, and reasonable request rates.

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urlparse

from playwright.async_api import BrowserContext, Page, Response, async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from tools.cdp_browser import CDPBrowserManager


DEFAULT_TARGETS = [
    "https://www.douyin.com/search/%E7%9F%AD%E5%89%A7%E7%83%AD%E6%92%AD%E6%A6%9C",
    "https://www.douyin.com/search/%E6%BC%AB%E5%89%A7%E7%83%AD%E6%92%AD%E6%A6%9C",
]

DEFAULT_KEYWORDS = [
    "\u77ed\u5267",
    "\u6f2b\u5267",
    "\u70ed\u64ad",
    "\u699c",
    "\u699c\u5355",
    "\u6392\u540d",
    "rank",
    "ranking",
    "billboard",
    "hot",
    "drama",
    "series",
    "aweme",
    "album",
]

INTERESTING_URL_PARTS = [
    "/aweme/",
    "/webcast/",
    "/douyin/",
    "/api/",
    "rank",
    "ranking",
    "billboard",
    "hot",
    "drama",
    "series",
    "aweme",
    "album",
    "list",
]

NOISE_URL_PARTS = [
    "ab/params",
    "abtest_config",
    "athena",
    "external/notification",
    "get_user_risklevel",
    "get_user_settings",
    "im/get_online_feedback",
    "login.douyin.com",
    "page/turn/offline",
    "passport",
    "service/2/",
    "social/count",
    "solution/resource/list",
    "webcast/setting",
]

STRUCTURE_KEYS = {
    "aweme_id",
    "aweme_list",
    "aweme_info",
    "mix_items",
    "rank",
    "rank_list",
    "item_list",
    "card_list",
    "data",
    "list",
    "items",
    "series",
    "album",
    "title",
    "desc",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Explore Douyin short-drama/comic-drama rank pages with Playwright CDP."
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        help="Target rank page URL. Repeat this option for multiple pages.",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        dest="keywords",
        help="Keyword used to score response URL/body. Repeat to add more.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/douyin/rank_probe",
        help="Directory for captured response JSON and summary files.",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=12.0,
        help="Seconds to wait after page load and scrolling.",
    )
    parser.add_argument(
        "--scrolls",
        type=int,
        default=4,
        help="Number of scroll steps to trigger lazy-loaded rank data.",
    )
    parser.add_argument(
        "--manual-wait-seconds",
        type=float,
        default=0.0,
        help="Extra seconds for manual interaction in the opened browser.",
    )
    parser.add_argument(
        "--include-all-json",
        action="store_true",
        help="Save every JSON response, even if it does not match rank heuristics.",
    )
    parser.add_argument(
        "--keep-browser",
        action="store_true",
        help="Leave the launched browser process open after the probe finishes.",
    )
    return parser.parse_args()


def normalize_targets(urls: Optional[List[str]]) -> List[str]:
    if urls:
        return urls
    return DEFAULT_TARGETS


def safe_filename(value: str, suffix: str = ".json") -> str:
    parsed = urlparse(value)
    base = f"{parsed.netloc}{parsed.path}".strip("/") or "response"
    base = re.sub(r"[^0-9A-Za-z._-]+", "_", base)[:90]
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{base}_{digest}{suffix}"


def compact_sample(value: Any, limit: int = 1200) -> Any:
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return value
    return text[:limit] + "...<truncated>"


def iter_json_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from iter_json_keys(child)
    elif isinstance(value, list):
        for child in value[:30]:
            yield from iter_json_keys(child)


def score_response(url: str, body: Any, keywords: List[str]) -> Dict[str, Any]:
    url_lower = url.lower()
    body_text = json.dumps(body, ensure_ascii=False).lower()
    keys = set(iter_json_keys(body))

    url_hits = [part for part in INTERESTING_URL_PARTS if part.lower() in url_lower]
    noise_hits = [part for part in NOISE_URL_PARTS if part.lower() in url_lower]
    keyword_hits = [keyword for keyword in keywords if keyword.lower() in body_text or keyword.lower() in url_lower]
    structure_hits = sorted(keys & STRUCTURE_KEYS)

    score = len(url_hits) * 2 + len(keyword_hits) * 3 + len(structure_hits) - len(noise_hits) * 8
    if {"aweme_id", "title"} <= keys or {"aweme_list"} <= keys:
        score += 8
    if {"rank", "title"} <= keys or {"rank_list"} <= keys:
        score += 8
    if "hot/search/list" in url_lower or "billboard" in url_lower:
        score += 8

    return {
        "score": score,
        "url_hits": url_hits,
        "noise_hits": noise_hits,
        "keyword_hits": keyword_hits,
        "structure_hits": structure_hits,
        "top_level_keys": list(body.keys())[:30] if isinstance(body, dict) else [],
    }


async def maybe_save_response(
    response: Response,
    output_dir: Path,
    keywords: List[str],
    captures: List[Dict[str, Any]],
    include_all_json: bool,
) -> None:
    url = response.url
    resource_type = response.request.resource_type
    if resource_type not in {"xhr", "fetch", "document"}:
        return

    headers = response.headers
    content_type = headers.get("content-type", "")
    if "json" not in content_type and not any(part in url.lower() for part in INTERESTING_URL_PARTS):
        return

    try:
        text = await response.text()
    except Exception as exc:
        captures.append({"url": url, "error": f"read failed: {exc}", "status": response.status})
        return

    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return

    try:
        body = json.loads(stripped)
    except json.JSONDecodeError:
        return

    score = score_response(url, body, keywords)
    if not include_all_json and score["score"] < 6:
        return

    filename = safe_filename(url)
    path = output_dir / filename
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

    captures.append(
        {
            "url": url,
            "status": response.status,
            "resource_type": resource_type,
            "content_type": content_type,
            "file": str(path),
            "body_chars": len(stripped),
            **score,
            "sample": compact_sample(body),
        }
    )


async def open_and_probe(
    context: BrowserContext,
    target_url: str,
    output_dir: Path,
    keywords: List[str],
    wait_seconds: float,
    scrolls: int,
    manual_wait_seconds: float,
    include_all_json: bool,
) -> List[Dict[str, Any]]:
    captures: List[Dict[str, Any]] = []
    page: Page = await context.new_page()

    page.on(
        "response",
        lambda response: asyncio.create_task(
            maybe_save_response(response, output_dir, keywords, captures, include_all_json)
        ),
    )

    print(f"[probe] opening: {target_url}")
    await page.goto(target_url, wait_until="domcontentloaded", timeout=120_000)
    await page.wait_for_timeout(2_000)

    for index in range(max(scrolls, 0)):
        await page.mouse.wheel(0, 900)
        await page.wait_for_timeout(1_500)
        print(f"[probe] scroll {index + 1}/{scrolls}")

    if manual_wait_seconds > 0:
        print(f"[probe] manual wait {manual_wait_seconds:.1f}s; interact in the browser if needed")
        await page.wait_for_timeout(int(manual_wait_seconds * 1000))

    await page.wait_for_timeout(int(wait_seconds * 1000))

    title = ""
    try:
        title = await page.title()
    except Exception:
        pass

    await page.close()
    captures.sort(key=lambda item: item.get("score", 0), reverse=True)
    return [
        {
            "target_url": target_url,
            "page_title": title,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "responses": captures,
        }
    ]


async def main() -> None:
    args = parse_args()
    config.PLATFORM = "dy"
    if args.keep_browser:
        config.AUTO_CLOSE_BROWSER = False

    output_dir = Path(args.output_dir) / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    keywords = list(dict.fromkeys(DEFAULT_KEYWORDS + (args.keywords or [])))
    targets = normalize_targets(args.urls)

    all_results: List[Dict[str, Any]] = []
    cdp_manager = CDPBrowserManager()

    async with async_playwright() as playwright:
        try:
            context = await cdp_manager.launch_and_connect(
                playwright=playwright,
                headless=config.CDP_HEADLESS,
            )
            await cdp_manager.add_stealth_script()

            for target in targets:
                if not target.startswith(("http://", "https://")):
                    target = f"https://www.douyin.com/search/{quote(target)}"
                result = await open_and_probe(
                    context=context,
                    target_url=target,
                    output_dir=output_dir,
                    keywords=keywords,
                    wait_seconds=args.wait_seconds,
                    scrolls=args.scrolls,
                    manual_wait_seconds=args.manual_wait_seconds,
                    include_all_json=args.include_all_json,
                )
                all_results.extend(result)
                await asyncio.sleep(3)
        finally:
            await cdp_manager.cleanup()

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[probe] summary: {summary_path}")
    for result in all_results:
        responses = result["responses"]
        print(f"[probe] {result['target_url']} -> {len(responses)} candidate responses")
        for item in responses[:8]:
            print(f"  score={item.get('score')} status={item.get('status')} file={item.get('file')}")
            print(f"  url={item.get('url')}")


if __name__ == "__main__":
    started = time.time()
    asyncio.run(main())
    print(f"[probe] done in {time.time() - started:.1f}s")
