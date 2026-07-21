# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tools/fetch_douyin_series_rank_md.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# Statement: This code is only for learning and research. Please comply with
# target platform terms, robots.txt, and reasonable request rates.

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from playwright.async_api import BrowserContext, Locator, Page, Response, TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from tools.cdp_browser import CDPBrowserManager


SERIES_URL = "https://www.douyin.com/series"
DEFAULT_COOKIE_FILE = PROJECT_ROOT / "browser_data" / "douyin_series_cookies.json"
RANK_CATEGORIES = ["短剧", "漫剧"]
TABS = ["热榜", "榜单"]
DETAIL_KEYWORDS = (
    "series",
    "album",
    "lvideo",
    "aweme",
    "comment",
    "episode",
    "rank",
    "favorite",
    "collect",
)


@dataclass
class CommentInfo:
    user: str = ""
    content: str = ""
    like_count: str = ""
    publish_time: str = ""


@dataclass
class DramaInfo:
    rank: int = 0
    title: str = ""
    rank_category: str = "短剧"
    tab: str = ""
    heat_text: str = ""
    card_episode_text: str = ""
    card_category_text: str = ""
    cover_url: str = ""
    detail_url: str = ""
    series_id: str = ""
    album_id: str = ""
    item_id: str = ""
    description: str = ""
    like_count: str = ""
    favorite_count: str = ""
    episode_count: str = ""
    comment_count: str = ""
    producer: str = ""
    category: str = ""
    online_time: str = ""
    comments: list[CommentInfo] = field(default_factory=list)
    detail_error: str = ""
    debug_notes: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Douyin short-drama hot/rank data via Playwright CDP and export Markdown."
    )
    parser.add_argument("--limit", type=int, default=50, help="Items per tab to collect")
    parser.add_argument("--output-dir", default="rank", help="Markdown output directory")
    parser.add_argument("--comment-limit", type=int, default=10, help="Comments per drama")
    parser.add_argument("--detail-timeout", type=float, default=25.0, help="Detail page timeout seconds")
    parser.add_argument(
        "--login-wait-seconds",
        type=float,
        default=0.0,
        help="Pause on Douyin page before crawling, useful for manual login",
    )
    parser.add_argument(
        "--cookie-file",
        default=str(DEFAULT_COOKIE_FILE),
        help="Cookie JSON file to load before crawling and save after crawling",
    )
    parser.add_argument("--keep-browser", action="store_true", help="Leave launched browser open")
    parser.add_argument("--debug", action="store_true", help="Print detail-page debug diagnostics")
    return parser.parse_args()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def md_escape(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
        .replace("\r", "<br>")
    )


def safe_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        encoded = message.encode(sys.stdout.encoding or "utf-8", errors="replace")
        print(encoded.decode(sys.stdout.encoding or "utf-8", errors="replace"))


def resolve_cookie_path(cookie_file: str) -> Path:
    path = Path(cookie_file).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


async def load_saved_cookies(context: BrowserContext, cookie_path: Path) -> int:
    if not cookie_path.exists():
        safe_print(f"[cookie] no saved cookie file: {cookie_path}")
        return 0
    try:
        raw_cookies = json.loads(cookie_path.read_text(encoding="utf-8"))
    except Exception as exc:
        safe_print(f"[cookie] failed to read {cookie_path}: {exc}")
        return 0
    if not isinstance(raw_cookies, list):
        safe_print(f"[cookie] ignored invalid cookie file: {cookie_path}")
        return 0

    cookies: list[dict[str, Any]] = []
    for cookie in raw_cookies:
        if not isinstance(cookie, dict):
            continue
        if not cookie.get("name") or cookie.get("value") is None:
            continue
        if not cookie.get("domain") and not cookie.get("url"):
            cookie["url"] = SERIES_URL
        cookies.append(cookie)
    if not cookies:
        safe_print(f"[cookie] no usable cookies in {cookie_path}")
        return 0

    try:
        await context.add_cookies(cookies)
    except Exception as exc:
        safe_print(f"[cookie] failed to load cookies from {cookie_path}: {exc}")
        return 0
    safe_print(f"[cookie] loaded {len(cookies)} cookies from {cookie_path}")
    return len(cookies)


async def save_current_cookies(context: BrowserContext, cookie_path: Path) -> int:
    try:
        cookies = await context.cookies()
    except Exception as exc:
        safe_print(f"[cookie] failed to read browser cookies: {exc}")
        return 0
    try:
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        safe_print(f"[cookie] failed to save cookies to {cookie_path}: {exc}")
        return 0
    safe_print(f"[cookie] saved {len(cookies)} cookies to {cookie_path}")
    return len(cookies)


async def wait_for_manual_login(page: Page, seconds: float, cookie_path: Path, context: BrowserContext) -> None:
    if seconds <= 0:
        return
    await page.goto(SERIES_URL, wait_until="domcontentloaded", timeout=120_000)
    safe_print(
        f"[login] waiting {seconds:g}s on Douyin page. "
        "Please finish login in the opened browser if needed."
    )
    await page.wait_for_timeout(int(seconds * 1000))
    await save_current_cookies(context, cookie_path)


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(md_escape(value) for value in row) + " |")
    return lines


def number_text(value: Any) -> int:
    text = clean_text(value).replace(",", "")
    if not text:
        return 0
    multiplier = 1
    if text.lower().endswith("w"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0
    return int(float(match.group(0)) * multiplier)


HEAT_FIELD_KEYS = (
    "heat_text",
    "heatText",
    "hot_text",
    "hotText",
    "hot_desc",
    "hotDesc",
    "hot_value",
    "hotValue",
    "hot",
    "heat",
    "rank_value",
    "rankValue",
    "rank_score",
    "rankScore",
    "score",
    "play_amount",
    "playAmount",
    "play_count",
    "playCount",
    "view_count",
    "viewCount",
    "vv",
    "播放量",
    "热度",
)


def looks_like_heat_text(value: Any) -> str:
    text = clean_text(value)
    if not text or isinstance(value, (dict, list)):
        return ""
    compact = text.replace(",", "")
    if not re.search(r"\d", compact):
        return ""
    if re.fullmatch(r"[0-9.]+(?:万|亿|w|W)?", compact):
        return compact
    match = re.search(r"([0-9][0-9.,]*(?:\.\d+)?(?:万|亿|w|W)?)\s*(?:热度|播放|次播放|人在看)?", compact)
    return match.group(1) if match and re.search(r"热|播放|view|hot", compact, re.I) else ""


def find_first_heat(value: Any) -> str:
    wanted = {key.lower() for key in HEAT_FIELD_KEYS}

    def walk(node: Any) -> str:
        if isinstance(node, dict):
            priority_items: list[tuple[Any, Any]] = []
            other_items: list[tuple[Any, Any]] = []
            for key, child in node.items():
                lowered = str(key).lower()
                if lowered in wanted:
                    text = looks_like_heat_text(child)
                    if text:
                        return text
                    priority_items.append((key, child))
                else:
                    other_items.append((key, child))
            for _, child in priority_items + other_items:
                found = walk(child)
                if found:
                    return found
        elif isinstance(node, list):
            for child in node:
                found = walk(child)
                if found:
                    return found
        return ""

    return walk(value)


def group_key(category: str, tab: str) -> str:
    return f"{clean_text(category) or '短剧'}::{clean_text(tab)}"


def section_title(category: str, tab: str) -> str:
    return f"{clean_text(category) or '短剧'}{clean_text(tab)}"


def compare_key(category: str, tab: str, title: str) -> str:
    return f"{clean_text(category) or '短剧'}::{clean_text(tab)}::{clean_text(title)}"


def parse_douyin_rank_markdown(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    records: dict[str, dict[str, Any]] = {}
    for block in text.split("\n### "):
        lines = block.splitlines()
        if not lines:
            continue
        match = re.match(r"(?P<rank>\d+)\.\s+(?P<title>.+)", lines[0])
        if not match:
            continue

        def row_value(label: str) -> str:
            row_match = re.search(rf"\| {re.escape(label)} \| (.*?) \|", block)
            return clean_text(row_match.group(1)) if row_match else ""

        title = row_value("剧名") or clean_text(match.group("title"))
        category = row_value("分类") or "短剧"
        tab = row_value("榜单页签")
        if not title or not tab:
            continue
        key = compare_key(category, tab, title)
        records[key] = {
            "rank": number_text(row_value("排名") or match.group("rank")),
            "title": title,
            "category": category,
            "tab": tab,
            "heat": number_text(row_value("热度/播放量")),
            "like": number_text(row_value("点赞")),
            "favorite": number_text(row_value("收藏")),
            "comment": number_text(row_value("评论数")),
            "item_id": row_value("Item ID"),
            "series_id": row_value("Series ID"),
        }
    return records


def current_douyin_records(grouped_records: dict[str, list[DramaInfo]]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for _group, items in grouped_records.items():
        for item in items:
            category = item.rank_category or "短剧"
            key = compare_key(category, item.tab, item.title)
            records[key] = {
                "rank": item.rank,
                "title": item.title,
                "category": category,
                "tab": item.tab,
                "heat": number_text(item.heat_text),
                "like": number_text(item.like_count),
                "favorite": number_text(item.favorite_count),
                "comment": number_text(item.comment_count),
                "item_id": item.item_id,
                "series_id": item.series_id,
            }
    return records


def comment_to_dict(comment: CommentInfo) -> dict[str, Any]:
    return {
        "user": comment.user,
        "content": comment.content,
        "like_count": comment.like_count,
        "publish_time": comment.publish_time,
    }


def drama_to_dict(item: DramaInfo, captured_at: str) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "title": item.title,
        "rank_category": item.rank_category,
        "tab": item.tab,
        "heat_text": item.heat_text,
        "card_episode_text": item.card_episode_text,
        "card_category_text": item.card_category_text,
        "cover_url": item.cover_url,
        "detail_url": item.detail_url,
        "series_id": item.series_id,
        "album_id": item.album_id,
        "item_id": item.item_id,
        "description": item.description,
        "like_count": item.like_count,
        "favorite_count": item.favorite_count,
        "episode_count": item.episode_count,
        "comment_count": item.comment_count,
        "producer": item.producer,
        "category": item.category,
        "online_time": item.online_time,
        "comments": [comment_to_dict(comment) for comment in item.comments],
        "detail_error": item.detail_error,
        "debug_notes": item.debug_notes,
        "captured_at": captured_at,
    }


def render_json_payload(grouped_records: dict[str, list[DramaInfo]], captured_at: str, source_markdown: Path | None = None) -> dict[str, Any]:
    groups = []
    records = []
    for category in RANK_CATEGORIES:
        for tab in TABS:
            key = group_key(category, tab)
            items = grouped_records.get(key, [])
            group_records = [drama_to_dict(item, captured_at) for item in items]
            groups.append({"category": category, "tab": tab, "records": group_records})
            records.extend(group_records)
    return {
        "platform": "dy",
        "source": "douyin_series_rank",
        "captured_at": captured_at,
        "source_markdown": str(source_markdown) if source_markdown else "",
        "records": records,
        "groups": groups,
    }


def find_previous_douyin_file(output_dir: Path, current_date: str) -> Path | None:
    search_dirs = [output_dir, Path("rank")]
    jobs_root = Path("data") / "dy" / "jobs"
    if jobs_root.exists():
        search_dirs.extend(path for path in jobs_root.glob("*/rank") if path.is_dir())

    candidates_by_path: dict[Path, Path] = {}
    for directory in search_dirs:
        if not directory.exists():
            continue
        for path in directory.glob("douyin_series_rank_*.md"):
            candidates_by_path[path.resolve()] = path

    candidates = sorted(candidates_by_path.values(), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        if current_date not in path.name:
            return path
    return None


def render_douyin_comparison(
    grouped_records: dict[str, list[DramaInfo]],
    previous_path: Path | None,
) -> list[str]:
    lines = ["## 与上一期榜单对比", ""]
    if previous_path is None:
        lines.extend(["_未找到可用于对比的上一期抖音榜单文件。_", ""])
        return lines

    previous = parse_douyin_rank_markdown(previous_path)
    current = current_douyin_records(grouped_records)
    if not previous:
        lines.extend([f"_无法从上一期文件解析榜单：{md_escape(previous_path.name)}_", ""])
        return lines

    current_keys = set(current)
    previous_keys = set(previous)
    common_keys = current_keys & previous_keys
    new_keys = sorted(
        current_keys - previous_keys,
        key=lambda key: (current[key]["category"], current[key]["tab"], current[key]["rank"]),
    )
    dropped_keys = sorted(
        previous_keys - current_keys,
        key=lambda key: (previous[key]["category"], previous[key]["tab"], previous[key]["rank"]),
    )

    movers: list[dict[str, Any]] = []
    for key in common_keys:
        old = previous[key]
        new = current[key]
        movers.append(
            {
                "title": new["title"],
                "category": new["category"],
                "tab": new["tab"],
                "old_rank": old["rank"],
                "new_rank": new["rank"],
                "rank_delta": old["rank"] - new["rank"],
                "heat_delta": new["heat"] - old["heat"],
                "like_delta": new["like"] - old["like"],
                "favorite_delta": new["favorite"] - old["favorite"],
                "comment_delta": new["comment"] - old["comment"],
            }
        )

    up = sorted([item for item in movers if item["rank_delta"] > 0], key=lambda item: (-item["rank_delta"], item["new_rank"]))
    down = sorted([item for item in movers if item["rank_delta"] < 0], key=lambda item: (item["rank_delta"], item["new_rank"]))
    heat_up = sorted(movers, key=lambda item: item["heat_delta"], reverse=True)
    like_up = sorted(movers, key=lambda item: item["like_delta"], reverse=True)

    lines.extend(
        [
            f"- 对比文件：{previous_path.name}",
            f"- 今日榜单数：{len(current)}",
            f"- 上一期榜单数：{len(previous)}",
            f"- 共同在榜：{len(common_keys)}",
            f"- 新上榜：{len(new_keys)}",
            f"- 下榜：{len(dropped_keys)}",
            "",
        ]
    )

    lines.extend(["### 新上榜", ""])
    if new_keys:
        lines.extend(
            markdown_table(
                ["分类", "榜单页签", "排名", "剧名", "热度/播放量", "点赞", "收藏", "评论数"],
                [
                    [
                        current[key]["category"],
                        current[key]["tab"],
                        current[key]["rank"],
                        current[key]["title"],
                        current[key]["heat"],
                        current[key]["like"],
                        current[key]["favorite"],
                        current[key]["comment"],
                    ]
                    for key in new_keys[:20]
                ],
            )
        )
    else:
        lines.append("_无_")
    lines.append("")

    lines.extend(["### 下榜", ""])
    if dropped_keys:
        lines.extend(
            markdown_table(
                ["分类", "榜单页签", "上一期排名", "剧名", "上一期热度/播放量", "上一期点赞", "上一期收藏", "上一期评论数"],
                [
                    [
                        previous[key]["category"],
                        previous[key]["tab"],
                        previous[key]["rank"],
                        previous[key]["title"],
                        previous[key]["heat"],
                        previous[key]["like"],
                        previous[key]["favorite"],
                        previous[key]["comment"],
                    ]
                    for key in dropped_keys[:20]
                ],
            )
        )
    else:
        lines.append("_无_")
    lines.append("")

    lines.extend(["### 排名上升 Top 20", ""])
    if up:
        lines.extend(
            markdown_table(
                ["分类", "榜单页签", "剧名", "上一期排名", "今日排名", "上升名次", "热度变化", "点赞变化", "评论变化"],
                [
                    [
                        item["category"],
                        item["tab"],
                        item["title"],
                        item["old_rank"],
                        item["new_rank"],
                        item["rank_delta"],
                        item["heat_delta"],
                        item["like_delta"],
                        item["comment_delta"],
                    ]
                    for item in up[:20]
                ],
            )
        )
    else:
        lines.append("_无_")
    lines.append("")

    lines.extend(["### 排名下降 Top 20", ""])
    if down:
        lines.extend(
            markdown_table(
                ["分类", "榜单页签", "剧名", "上一期排名", "今日排名", "下降名次", "热度变化", "点赞变化", "评论变化"],
                [
                    [
                        item["category"],
                        item["tab"],
                        item["title"],
                        item["old_rank"],
                        item["new_rank"],
                        abs(item["rank_delta"]),
                        item["heat_delta"],
                        item["like_delta"],
                        item["comment_delta"],
                    ]
                    for item in down[:20]
                ],
            )
        )
    else:
        lines.append("_无_")
    lines.append("")

    lines.extend(["### 热度增长 Top 20", ""])
    if heat_up:
        lines.extend(
            markdown_table(
                ["分类", "榜单页签", "剧名", "热度变化", "上一期排名", "今日排名"],
                [
                    [item["category"], item["tab"], item["title"], item["heat_delta"], item["old_rank"], item["new_rank"]]
                    for item in heat_up[:20]
                ],
            )
        )
    else:
        lines.append("_无_")
    lines.append("")

    lines.extend(["### 点赞增长 Top 20", ""])
    if like_up:
        lines.extend(
            markdown_table(
                ["分类", "榜单页签", "剧名", "点赞变化", "收藏变化", "评论变化", "上一期排名", "今日排名"],
                [
                    [
                        item["category"],
                        item["tab"],
                        item["title"],
                        item["like_delta"],
                        item["favorite_delta"],
                        item["comment_delta"],
                        item["old_rank"],
                        item["new_rank"],
                    ]
                    for item in like_up[:20]
                ],
            )
        )
    else:
        lines.append("_无_")
    lines.append("")
    return lines


def first_text(*values: Any) -> str:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def format_timestamp_text(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if not re.fullmatch(r"\d{10}|\d{13}", text):
        return text
    timestamp = int(text)
    if len(text) == 13:
        timestamp = timestamp // 1000
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return text


def useful_text(value: Any, max_len: int = 120) -> str:
    text = clean_text(value)
    if not text or len(text) > max_len:
        return ""
    lowered = text.lower()
    if lowered in {"and", "or", "rule", "true", "false", "null", "none", "undefined"}:
        return ""
    if re.fullmatch(r"\d{8,22}", text):
        return ""
    if text.startswith(("http://", "https://", "aweme://", "sslocal://", "snssdk")):
        return ""
    return text


def useful_description(value: Any) -> str:
    text = useful_text(value, max_len=500)
    if not text:
        return ""
    blocked_patterns = (
        r"榜单.*排序",
        r"榜单值",
        r"加入粉丝团",
        r"新增粉丝",
        r"送礼",
    )
    if any(re.search(pattern, text) for pattern in blocked_patterns):
        return ""
    return text


def useful_label(value: Any) -> str:
    text = useful_text(value, max_len=60)
    if not text or re.fullmatch(r"\d+(?:\.\d+)?", text):
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
        return ""
    return text


def find_first_by_keys(value: Any, keys: Iterable[str]) -> str:
    wanted = {key.lower() for key in keys}
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in wanted:
                text = useful_text(child)
                if text and not isinstance(child, (dict, list)):
                    return text
            found = find_first_by_keys(child, wanted)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_first_by_keys(child, wanted)
            if found:
                return found
    return ""


def find_first_stat(value: Any, keys: Iterable[str]) -> str:
    wanted = {key.lower() for key in keys}

    def walk(node: Any, in_stats: bool = False) -> str:
        if isinstance(node, dict):
            for key, child in node.items():
                lowered = str(key).lower()
                child_in_stats = in_stats or lowered in {"stats", "stat", "statistics", "statistics_info", "aweme_statistics"}
                if lowered in wanted and not isinstance(child, (dict, list)):
                    text = clean_text(child)
                    if text and re.fullmatch(r"[0-9.,万亿wWkK]+", text):
                        return text
                found = walk(child, child_in_stats)
                if found:
                    return found
        elif isinstance(node, list):
            for child in node:
                found = walk(child, in_stats)
                if found:
                    return found
        return ""

    return walk(value)


def find_first_label_by_keys(value: Any, keys: Iterable[str]) -> str:
    wanted = {key.lower() for key in keys}
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in wanted and not isinstance(child, (dict, list)):
                text = useful_label(child)
                if text:
                    return text
            found = find_first_label_by_keys(child, wanted)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_first_label_by_keys(child, wanted)
            if found:
                return found
    return ""


def collect_dicts_with_keys(value: Any, keys: set[str], limit: int = 20) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if len(result) >= limit:
            return
        if isinstance(node, dict):
            lower_keys = {str(key).lower() for key in node.keys()}
            if lower_keys & keys:
                result.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result


def iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child)


def find_first_url(value: Any) -> str:
    if isinstance(value, str):
        text = clean_text(value)
        if text.startswith(("http://", "https://")):
            return text
        if text.startswith(("aweme://", "snssdk1128://", "sslocal://")):
            return text
        if text.startswith("/") and not text.startswith("//"):
            return urljoin(SERIES_URL, text)
        if text.startswith("//"):
            return "https:" + text
    if isinstance(value, dict):
        for key in (
            "url",
            "href",
            "link",
            "schema",
            "jump_url",
            "jumpUrl",
            "open_url",
            "openUrl",
            "detail_url",
            "detailUrl",
            "play_url",
            "playUrl",
            "share_url",
            "shareUrl",
        ):
            found = find_first_url(value.get(key))
            if found:
                return found
        for child in value.values():
            found = find_first_url(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_first_url(child)
            if found:
                return found
    return ""


def extract_id_from_url(value: str) -> tuple[str, str, str]:
    text = unquote(clean_text(value))
    if not text:
        return "", "", ""
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    series_id = first_text(
        *(query.get(key, [""])[0] for key in ("series_id", "seriesId", "series", "playlet_id", "playletId"))
    )
    album_id = first_text(*(query.get(key, [""])[0] for key in ("album_id", "albumId", "album")))
    item_id = first_text(*(query.get(key, [""])[0] for key in ("aweme_id", "item_id", "itemId", "id")))
    path_numbers = re.findall(r"(?<!\d)(\d{12,22})(?!\d)", parsed.path)
    if not series_id and any(part in parsed.path for part in ("/series", "/playlet")):
        series_id = path_numbers[-1] if path_numbers else ""
    if not item_id and any(part in parsed.path for part in ("/video", "/note")):
        item_id = path_numbers[-1] if path_numbers else ""
    return series_id, album_id, item_id


def iter_video_ids(value: Any) -> Iterable[str]:
    key_hints = (
        "aweme_id",
        "awemeId",
        "item_id",
        "itemId",
        "watched_item",
        "watchedItem",
        "group_id",
        "groupId",
    )
    if isinstance(value, dict):
        for key, child in value.items():
            text = clean_text(child)
            if str(key) in key_hints and re.fullmatch(r"\d{12,22}", text) and text != "0":
                yield text
            if isinstance(child, str):
                _, _, item_id = extract_id_from_url(child)
                if item_id:
                    yield item_id
            yield from iter_video_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_video_ids(child)
    elif isinstance(value, str):
        _, _, item_id = extract_id_from_url(value)
        if item_id:
            yield item_id


def first_video_id_from_captures(captures: list[dict[str, Any] | list[Any]], series_id: str = "") -> str:
    seen: set[str] = set()
    for data in captures:
        for item_id in iter_video_ids(data):
            if item_id in seen or item_id == series_id:
                continue
            seen.add(item_id)
            return item_id
    return ""


def normalize_detail_url(url: str, series_id: str = "", item_id: str = "") -> str:
    text = clean_text(url)
    if text.startswith(("aweme://", "snssdk1128://", "sslocal://")):
        embedded = re.search(r"(https?%3A%2F%2F[^&]+|https?://[^&]+)", text)
        if embedded:
            return unquote(embedded.group(1))
        series_from_url, _, item_from_url = extract_id_from_url(text)
        series_id = series_id or series_from_url
        item_id = item_id or item_from_url
        text = ""
    if text.startswith("//"):
        text = "https:" + text
    if text.startswith("/"):
        text = urljoin(SERIES_URL, text)
    if text and text.startswith(("http://", "https://")):
        return text
    if series_id:
        return f"https://www.douyin.com/series/{series_id}"
    if item_id:
        return f"https://www.douyin.com/video/{item_id}"
    return ""


def first_image_from_json(value: Any) -> str:
    if isinstance(value, str):
        text = clean_text(value)
        if text.startswith(("http://", "https://", "//")) and re.search(r"\.(?:jpg|jpeg|png|webp)|image|tos-", text):
            return "https:" + text if text.startswith("//") else text
        return ""
    if isinstance(value, dict):
        for key in ("url_list", "urlList", "urls"):
            child = value.get(key)
            if isinstance(child, list):
                found = first_image_from_json(child)
                if found:
                    return found
        for key in ("cover", "cover_url", "coverUrl", "poster", "poster_url", "image", "uri"):
            found = first_image_from_json(value.get(key))
            if found:
                return found
        for child in value.values():
            found = first_image_from_json(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = first_image_from_json(child)
            if found:
                return found
    return ""


def text_has_any(text: str, words: Iterable[str]) -> bool:
    return any(word in text for word in words)


def normalize_title_key(title: str) -> str:
    text = clean_text(title)
    text = re.sub(r"^\d+(?:\.\d+)?\s*[万亿]\s+", "", text)
    text = re.sub(
        r"\s+(?:其他|爱情|剧情|逆袭|反转|亲情|悬疑|喜剧|古装|友情|犯罪|惊悚|青春|科幻|仙侠|玄幻|奇幻)[·\s]*\d+\s*集$",
        "",
        text,
    )
    return text


def is_probably_dirty_title(title: str) -> bool:
    text = clean_text(title)
    if not text:
        return True
    if re.search(r"\d+(?:\.\d+)?\s*[万亿].*集", text):
        return True
    if text_has_any(text, ("推荐", "精选", "搜索", "关注", "下载", "ICP")):
        return True
    return False


async def click_text(page: Page, text: str, timeout: int = 12_000) -> bool:
    candidates = [
        page.get_by_text(text, exact=True),
        page.locator(f"text={text}"),
    ]
    for locator in candidates:
        try:
            await locator.first.click(timeout=timeout)
            await page.wait_for_timeout(1_500)
            return True
        except Exception:
            continue
    xpath_text = text.replace("'", "\\'")
    fallback = page.locator(
        f"xpath=//*[contains(normalize-space(.), '{xpath_text}') "
        "and not(self::script) and not(self::style)]"
    )
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    while asyncio.get_running_loop().time() < deadline:
        count = min(await fallback.count(), 80)
        for index in range(count):
            locator = fallback.nth(index)
            try:
                visible = await locator.is_visible(timeout=300)
                text_content = clean_text(await locator.inner_text(timeout=300))
                box = await locator.bounding_box(timeout=300)
            except Exception:
                continue
            if not visible or not box:
                continue
            if text not in text_content or len(text_content) > 30:
                continue
            if box.get("width", 0) < 8 or box.get("height", 0) < 8:
                continue
            for target in (
                locator,
                locator.locator("xpath=ancestor-or-self::a[1]"),
                locator.locator("xpath=ancestor-or-self::button[1]"),
                locator.locator("xpath=ancestor::*[self::div or self::li][1]"),
            ):
                try:
                    await target.click(timeout=800, force=True)
                    await page.wait_for_timeout(1_500)
                    return True
                except Exception:
                    continue
        await page.wait_for_timeout(500)
    return False


async def click_tab_text(page: Page, text: str, timeout: int = 15_000) -> bool:
    try:
        clicked = await page.evaluate(
            """label => {
                const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                const nodes = Array.from(document.querySelectorAll('body *')).filter(
                    node => clean(node.innerText || node.textContent) === label
                );
                const candidates = nodes
                    .map(node => ({node, rect: node.getBoundingClientRect()}))
                    .filter(({rect}) =>
                        rect.x > 150 && rect.y > 150 && rect.y < 330 &&
                        rect.width > 8 && rect.width <= 140 &&
                        rect.height > 8 && rect.height <= 70
                    )
                    .sort((a, b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x);
                const hit = candidates[0];
                if (!hit) return false;
                const x = hit.rect.x + hit.rect.width / 2;
                const y = hit.rect.y + hit.rect.height / 2;
                for (const type of ['mouseover', 'mousedown', 'mouseup', 'click']) {
                    hit.node.dispatchEvent(new MouseEvent(type, {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                        clientX: x,
                        clientY: y,
                    }));
                }
                return true;
            }""",
            text,
        )
        if clicked:
            await page.wait_for_timeout(2_000)
            return True
    except Exception:
        pass

    escaped = text.replace("'", "\\'")
    selectors = [
        f"xpath=//*[normalize-space()='{escaped}']",
        f"xpath=//*[contains(normalize-space(.), '{escaped}') and string-length(normalize-space(.)) <= 20]",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        deadline = asyncio.get_running_loop().time() + timeout / 1000
        while asyncio.get_running_loop().time() < deadline:
            count = min(await locator.count(), 30)
            for index in range(count):
                item = locator.nth(index)
                try:
                    if not await item.is_visible(timeout=300):
                        continue
                    box = await item.bounding_box(timeout=300)
                except Exception:
                    continue
                if not box or box.get("width", 0) < 12 or box.get("height", 0) < 12:
                    continue
                for target in (
                    item,
                    item.locator("xpath=ancestor-or-self::*[@role='tab'][1]"),
                    item.locator("xpath=ancestor::*[self::button or self::a or self::div or self::span][1]"),
                    item.locator("xpath=ancestor::*[self::button or self::a or self::div or self::span][2]"),
                ):
                    try:
                        await target.scroll_into_view_if_needed(timeout=800)
                        await target.click(timeout=1_000, force=True)
                        await page.wait_for_timeout(1_800)
                        return True
                    except Exception:
                        continue
            await page.wait_for_timeout(500)
    return await click_text(page, text, timeout=timeout)


async def response_json(response: Response) -> Optional[dict[str, Any] | list[Any]]:
    resource_type = response.request.resource_type
    if resource_type not in {"xhr", "fetch", "document"}:
        return None
    url = response.url.lower()
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type and not any(keyword in url for keyword in DETAIL_KEYWORDS):
        return None
    try:
        text = (await response.text()).strip()
    except Exception:
        return None
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def capture_response(response: Response, captures: list[dict[str, Any] | list[Any]]) -> None:
    data = await response_json(response)
    if data is not None:
        captures.append(data)


def schedule_capture_response(response: Response, captures: list[dict[str, Any] | list[Any]]) -> None:
    task = asyncio.create_task(capture_response(response, captures))

    def consume_exception(done: asyncio.Task[None]) -> None:
        try:
            done.exception()
        except Exception:
            pass

    task.add_done_callback(consume_exception)


async def visible_text(locator: Locator) -> str:
    try:
        return clean_text(await locator.inner_text(timeout=1500))
    except Exception:
        return ""


async def attr(locator: Locator, name: str) -> str:
    try:
        return clean_text(await locator.get_attribute(name, timeout=1500))
    except Exception:
        return ""


async def first_image_url(card: Locator) -> str:
    images = card.locator("img")
    count = min(await images.count(), 4)
    for index in range(count):
        image = images.nth(index)
        for name in ("src", "data-src"):
            value = await attr(image, name)
            if value and not value.startswith("data:"):
                return value
    try:
        background = await card.evaluate(
            """node => {
                const nodes = [node, ...node.querySelectorAll('*')];
                for (const item of nodes) {
                    const bg = getComputedStyle(item).backgroundImage;
                    if (bg && bg !== 'none') return bg;
                }
                return '';
            }"""
        )
    except Exception:
        return ""
    match = re.search(r'url\(["\']?(.*?)["\']?\)', background or "")
    return match.group(1) if match else ""


async def best_link(card: Locator) -> str:
    links = card.locator("a[href]")
    count = min(await links.count(), 6)
    for index in range(count):
        href = await attr(links.nth(index), "href")
        if href and not href.startswith("javascript:"):
            return urljoin(SERIES_URL, href)
    return ""


def split_card_text(text: str) -> tuple[str, str, str, str]:
    parts = [part.strip() for part in re.split(r"[\r\n]+| {2,}", text) if part.strip()]
    title = ""
    heat = ""
    episode = ""
    category = ""
    for part in parts:
        if not title and not text_has_any(part, ("热", "播放", "万", "亿", "集", "其他")):
            title = part
        if not heat and text_has_any(part, ("万", "亿", "热", "播放")):
            heat = part
        if not episode and "集" in part:
            episode = part
        if not category and text_has_any(part, ("其他", "爱情", "剧情", "逆袭", "反转", "亲情", "悬疑", "喜剧", "古装")):
            category = part
    if not title and parts:
        title = parts[0]
    return title, heat, episode, category


def heat_from_visible_text(text: str) -> str:
    value = clean_text(text)
    if not value:
        return ""
    patterns = (
        r"(?:热度|播放量|播放|观看|在看)\s*[:：]?\s*([0-9][0-9.,]*(?:\.\d+)?(?:万|亿|w|W)?)",
        r"([0-9][0-9.,]*(?:\.\d+)?(?:万|亿|w|W)?)\s*(?:热度|播放|次播放|人在看)",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    return ""


async def candidate_cards(page: Page) -> list[Locator]:
    selectors = [
        "a[href*='/series']:has(img)",
        "a[href*='album']:has(img)",
        "div:has(> img)",
        "div:has(img)",
    ]
    seen: set[str] = set()
    cards: list[Locator] = []
    for selector in selectors:
        locators = page.locator(selector)
        count = min(await locators.count(), 320)
        for index in range(count):
            item = await normalize_card_locator(page, locators.nth(index), f"{len(cards)}-{index}")
            text = await visible_text(item)
            if len(text) < 2 or len(text) > 220:
                continue
            if not text_has_any(text, ("集", "万", "亿", "热", "播放")):
                continue
            if text_has_any(text, ("ICP", "用户服务协议", "隐私政策", "下载抖音", "反馈", "举报")):
                continue
            box = None
            try:
                box = await item.bounding_box(timeout=1000)
            except Exception:
                pass
            if not box or box.get("width", 0) < 80 or box.get("height", 0) < 80:
                continue
            if box.get("width", 0) > 420 or box.get("height", 0) > 620:
                continue
            key = clean_text(text)[:120]
            if key in seen:
                continue
            seen.add(key)
            cards.append(item)
    return cards


async def normalize_card_locator(page: Page, locator: Locator, marker: str) -> Locator:
    try:
        handle = await locator.element_handle(timeout=500)
        if not handle:
            return locator
        card_id = await handle.evaluate(
            """(node, marker) => {
                const badText = text => /ICP|用户服务协议|隐私政策|下载抖音|举报/.test(text || '');
                let current = node;
                let best = node;
                for (let i = 0; current && i < 6; i += 1, current = current.parentElement) {
                    const rect = current.getBoundingClientRect();
                    const text = (current.innerText || '').trim();
                    if (
                        rect.width >= 100 && rect.width <= 420 &&
                        rect.height >= 140 && rect.height <= 620 &&
                        text.length >= 2 && text.length <= 220 &&
                        /集|万|亿|热|播放/.test(text) &&
                        !badText(text)
                    ) {
                        best = current;
                    }
                }
                const id = `aiworker-card-${marker}`;
                best.setAttribute('data-aiworker-card-id', id);
                return id;
            }""",
            marker,
        )
        if card_id:
            return page.locator(f'[data-aiworker-card-id="{card_id}"]').first
    except Exception:
        return locator
    return locator


async def extract_card_info(card: Locator, rank: int, category: str, tab: str) -> DramaInfo:
    text = await visible_text(card)
    title, heat, episode, category = split_card_text(text)
    return DramaInfo(
        rank=rank,
        title=title,
        rank_category=category,
        tab=tab,
        heat_text=heat,
        card_episode_text=episode,
        card_category_text=category,
        cover_url=await first_image_url(card),
        detail_url=await best_link(card),
    )


async def collect_rank_cards(page: Page, category: str, tab: str, limit: int) -> list[DramaInfo]:
    text_records = await collect_rank_cards_from_text(page, category, tab, limit)
    await enrich_records_from_dom(page, text_records)
    if text_records:
        return text_records[:limit]

    records: list[DramaInfo] = []
    for item in text_records:
        item.rank = len(records) + 1
        records.append(item)
    seen: set[str] = set()
    for item in records:
        seen.add(normalize_title_key(item.title) or item.detail_url or item.cover_url)
    stagnant = 0
    while len(records) < limit and stagnant < 5:
        before = len(records)
        cards = await candidate_cards(page)
        for card in cards:
            if len(records) >= limit:
                break
            item = await extract_card_info(card, len(records) + 1, category, tab)
            title_key = normalize_title_key(item.title)
            key = title_key or item.detail_url or item.cover_url
            if is_probably_dirty_title(item.title) or not key or key in seen:
                continue
            item.title = title_key
            seen.add(key)
            records.append(item)
        if len(records) == before:
            stagnant += 1
        else:
            stagnant = 0
        if len(records) < limit:
            await page.mouse.wheel(0, 1100)
            await page.wait_for_timeout(1_500)
    if not records:
        records = await collect_rank_cards_from_text(page, category, tab, limit)
    return records[:limit]


async def enrich_records_from_dom(page: Page, records: list[DramaInfo]) -> None:
    for record in records:
        await enrich_record_from_dom(page, record)


async def enrich_record_from_dom(page: Page, record: DramaInfo) -> None:
    if not record.title:
        return
    try:
        data = await page.evaluate(
            """title => {
                const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                const nodes = Array.from(document.querySelectorAll('a, div, span, p'));
                const matched = nodes.find(node => {
                    const text = clean(node.innerText || node.textContent || '');
                    return text && text.includes(title) && text.length <= 260;
                });
                if (!matched) return {};

                let card = matched;
                let best = matched;
                for (let i = 0; card && i < 8; i += 1, card = card.parentElement) {
                    const text = clean(card.innerText || '');
                    const rect = card.getBoundingClientRect();
                    if (
                        text.includes(title) &&
                        /集|万|亿|播放|热/.test(text) &&
                        rect.width >= 80 && rect.width <= 520 &&
                        rect.height >= 100 && rect.height <= 720
                    ) {
                        best = card;
                    }
                }
                card = best || matched;
                const marker = `aiworker-detail-${Math.random().toString(36).slice(2)}`;
                card.setAttribute('data-aiworker-detail-card', marker);

                const linkNode = matched.closest('a[href]') || card.querySelector('a[href]');
                const imageNode = card.querySelector('img');
                let image = imageNode ? (imageNode.currentSrc || imageNode.src || imageNode.getAttribute('data-src') || '') : '';
                if (!image) {
                    const all = [card, ...card.querySelectorAll('*')];
                    for (const item of all) {
                        const bg = getComputedStyle(item).backgroundImage;
                        const match = bg && bg.match(/url\\(["']?(.*?)["']?\\)/);
                        if (match) {
                            image = match[1];
                            break;
                        }
                    }
                }
                return {
                    href: linkNode ? linkNode.href : '',
                    image,
                    text: clean(card.innerText || matched.innerText || ''),
                    marker,
                };
            }""",
            record.title,
        )
    except Exception:
        return
    if not isinstance(data, dict):
        return
    href = clean_text(data.get("href"))
    if href and "jingxuan" not in href:
        record.detail_url = href
    if not record.detail_url:
        marker = clean_text(data.get("marker"))
        if marker:
            record.detail_url = f"dom://{marker}"
    record.cover_url = record.cover_url or clean_text(data.get("image"))
    card_text = clean_text(data.get("text"))
    if card_text:
        title, heat, episode, category = split_card_text(card_text)
        record.heat_text = record.heat_text or heat or heat_from_visible_text(card_text)
        record.card_episode_text = record.card_episode_text or episode
        record.card_category_text = record.card_category_text or category


async def collect_rank_cards_from_text(page: Page, category: str, tab: str, limit: int) -> list[DramaInfo]:
    body_text = await visible_text(page.locator("body"))
    text = re.sub(r"\s+", " ", body_text)
    matches = list(
        re.finditer(
            r"(?P<heat>\d+(?:\.\d+)?\s*[万亿])\s+"
            r"(?P<title>.{2,45}?)\s+"
            r"(?P<meta>(?:其他|爱情|剧情|逆袭|反转|亲情|悬疑|喜剧|古装|友情|犯罪|惊悚|青春|科幻|仙侠|玄幻|奇幻).{0,12}?集)",
            text,
        )
    )
    records: list[DramaInfo] = []
    seen: set[str] = set()
    for match in matches:
        title = normalize_title_key(match.group("title"))
        if is_probably_dirty_title(title) or not title or title in seen:
            continue
        seen.add(title)
        records.append(
            DramaInfo(
                rank=len(records) + 1,
                title=title,
                rank_category=category,
                tab=tab,
                heat_text=clean_text(match.group("heat")),
                card_episode_text=clean_text(match.group("meta")),
                card_category_text=clean_text(match.group("meta").split("·")[0]),
            )
        )
        if len(records) >= limit:
            break
    return records


def merge_detail_from_json(info: DramaInfo, captures: list[dict[str, Any] | list[Any]]) -> None:
    for data in captures:
        if not info.description:
            info.description = useful_description(
                find_first_by_keys(
                    data,
                    ("desc", "description", "intro", "introduction", "series_intro", "album_desc"),
                )
            )
        info.like_count = info.like_count or find_first_stat(
            data,
            ("like_count", "digg_count", "diggCount", "digg", "likes", "zan_count"),
        )
        info.favorite_count = info.favorite_count or find_first_stat(
            data,
            (
                "favorite_count",
                "collect_count",
                "collectCount",
                "collection_count",
                "favorited_count",
                "favorite",
                "收藏",
            ),
        )
        info.comment_count = info.comment_count or find_first_stat(
            data,
            ("comment_count", "commentCount", "comments_count", "reply_count"),
        )
        info.heat_text = info.heat_text or find_first_heat(data)
        info.episode_count = info.episode_count or find_first_by_keys(
            data,
            ("episode_count", "episode_cnt", "total_episode", "total", "集数"),
        )
        info.producer = info.producer or find_first_by_keys(
            data,
            ("author", "producer", "publisher", "provider", "出品方"),
        )
        info.category = info.category or find_first_label_by_keys(data, ("category", "tag", "type", "genre"))
        info.online_time = info.online_time or format_timestamp_text(
            find_first_by_keys(
            data,
            ("online_time", "publish_time", "release_time", "create_time", "update_time"),
            )
        )


def merge_record_from_json_object(info: DramaInfo, item: dict[str, Any]) -> None:
    info.description = info.description or find_first_by_keys(
        item,
        ("desc", "description", "intro", "introduction", "series_intro", "album_desc", "简介"),
    )
    if info.description:
        info.description = useful_description(info.description)
    info.like_count = info.like_count or find_first_stat(
        item,
        ("like_count", "digg_count", "diggCount", "digg", "likes", "zan_count"),
    )
    info.favorite_count = info.favorite_count or find_first_stat(
        item,
        (
            "favorite_count",
            "collect_count",
            "collectCount",
            "collection_count",
            "favorited_count",
            "favorite",
            "收藏",
        ),
    )
    info.comment_count = info.comment_count or find_first_stat(
        item,
        ("comment_count", "commentCount", "comments_count", "reply_count", "评论数"),
    )
    info.heat_text = info.heat_text or find_first_heat(item)
    info.episode_count = info.episode_count or find_first_by_keys(
        item,
        ("episode_count", "episode_cnt", "total_episode", "total_episodes", "total", "集数"),
    )
    info.producer = info.producer or find_first_by_keys(
        item,
        ("author", "author_name", "producer", "publisher", "provider", "studio", "出品方", "剧场"),
    )
    info.category = info.category or find_first_label_by_keys(item, ("category", "tag", "type", "genre", "题材", "类型"))
    info.online_time = info.online_time or format_timestamp_text(
        find_first_by_keys(
        item,
        ("online_time", "publish_time", "release_time", "create_time", "update_time", "上线时间", "更新时间"),
        )
    )
    info.series_id = info.series_id or find_first_by_keys(
        item,
        ("series_id", "seriesId", "series", "playlet_id", "playletId"),
    )
    info.album_id = info.album_id or find_first_by_keys(item, ("album_id", "albumId"))
    info.item_id = info.item_id or find_first_by_keys(item, ("aweme_id", "item_id", "itemId", "id"))
    url = find_first_url(item)
    series_from_url, album_from_url, item_from_url = extract_id_from_url(url)
    info.series_id = info.series_id or series_from_url
    info.album_id = info.album_id or album_from_url
    info.item_id = info.item_id or item_from_url
    normalized = normalize_detail_url(url, info.series_id, info.item_id)
    if normalized and not normalized.startswith("dom://"):
        info.detail_url = normalized
    info.cover_url = info.cover_url or first_image_from_json(item)


def first_nested_dict(item: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            return value
    return item


def normalize_series_item(raw_item: dict[str, Any], category: str, tab: str, rank: int) -> DramaInfo:
    item = first_nested_dict(raw_item, ("series", "series_info", "seriesInfo", "album"))
    title = first_text(
        item.get("series_name"),
        item.get("name"),
        item.get("title"),
        item.get("album_name"),
        raw_item.get("series_name"),
        raw_item.get("title"),
    )
    info = DramaInfo(
        rank=rank,
        title=normalize_title_key(title),
        rank_category=category,
        tab=tab,
        heat_text=first_text(
            item.get("hot_value"),
            item.get("hot"),
            item.get("heat"),
            item.get("play_amount"),
            item.get("play_count"),
            item.get("view_count"),
            item.get("rank_value"),
            raw_item.get("hot_value"),
            raw_item.get("hot"),
            raw_item.get("heat"),
            raw_item.get("heat_text"),
            raw_item.get("hot_text"),
            raw_item.get("hot_desc"),
            raw_item.get("play_count"),
            raw_item.get("view_count"),
            raw_item.get("rank_value"),
        ),
        card_episode_text=first_text(
            item.get("sub_title"),
            item.get("episode_count_text"),
            item.get("episode_text"),
            raw_item.get("sub_title"),
        ),
        card_category_text=first_text(
            item.get("category_name"),
            item.get("tag_name"),
            item.get("genre"),
            raw_item.get("category_name"),
        ),
    )
    merge_record_from_json_object(info, raw_item)
    merge_record_from_json_object(info, item)
    info.item_id = info.item_id or first_text(
        raw_item.get("aweme_id"),
        raw_item.get("item_id"),
        raw_item.get("watched_item"),
        item.get("aweme_id"),
        item.get("item_id"),
        item.get("watched_item"),
    )
    if info.item_id == "0":
        info.item_id = ""
    if not info.heat_text:
        info.heat_text = find_first_heat(raw_item)
    if not info.card_episode_text and info.episode_count:
        info.card_episode_text = f"{info.episode_count}集"
    if not info.card_category_text:
        info.card_category_text = info.category
    if info.item_id:
        info.detail_url = f"https://www.douyin.com/video/{info.item_id}"
    elif info.series_id and not info.detail_url:
        info.detail_url = f"https://www.iesdouyin.com/share/playlet/detail/{info.series_id}/?schema_type=43&object_id={info.series_id}&from_ssr=1"
    return info


def collect_records_from_tab_json(
    captures: list[dict[str, Any] | list[Any]],
    category: str,
    tab: str,
    limit: int,
) -> list[DramaInfo]:
    records: list[DramaInfo] = []
    seen: set[str] = set()
    for data in captures:
        candidate_lists: list[list[Any]] = []
        if isinstance(data, dict):
            if tab == "热榜" and isinstance(data.get("series_infos"), list):
                candidate_lists.append(data["series_infos"])
            if tab == "榜单" and isinstance(data.get("card_list"), list):
                candidate_lists.append(data["card_list"])
        for items in candidate_lists:
            for raw_item in items:
                if not isinstance(raw_item, dict):
                    continue
                record = normalize_series_item(raw_item, category, tab, len(records) + 1)
                key = record.series_id or normalize_title_key(record.title)
                if not key or key in seen or is_probably_dirty_title(record.title):
                    continue
                seen.add(key)
                records.append(record)
                if len(records) >= limit:
                    return records
    return records


def merge_records_from_json(records: list[DramaInfo], captures: list[dict[str, Any] | list[Any]]) -> None:
    if not records or not captures:
        return
    unmatched = [record for record in records if record.title]
    for data in captures:
        for item in iter_dicts(data):
            item_text = json.dumps(item, ensure_ascii=False)
            if len(item_text) > 20_000:
                continue
            for record in unmatched:
                if record.title and record.title in item_text:
                    before = (record.detail_url, record.series_id, record.item_id)
                    merge_record_from_json_object(record, item)
                    after = (record.detail_url, record.series_id, record.item_id)
                    if after != before:
                        break


def comments_from_json(captures: list[dict[str, Any] | list[Any]], limit: int) -> list[CommentInfo]:
    comments: list[CommentInfo] = []
    seen: set[str] = set()
    for data in captures:
        candidates = collect_dicts_with_keys(
            data,
            {"text", "content", "comment", "cid", "digg_count", "like_count", "user"},
            limit=80,
        )
        for item in candidates:
            user_obj = item.get("user") if isinstance(item.get("user"), dict) else {}
            has_comment_shape = any(item.get(key) for key in ("cid", "comment_id", "reply_id"))
            has_user_shape = bool(user_obj.get("uid") or user_obj.get("nickname") or item.get("user_id"))
            if not (has_comment_shape or has_user_shape):
                continue
            content = first_text(
                item.get("text"),
                item.get("content"),
                item.get("comment"),
                item.get("comment_text"),
            )
            if not content or len(content) > 500 or is_non_comment_text(content) or content in seen:
                continue
            seen.add(content)
            comments.append(
                CommentInfo(
                    user=first_text(item.get("nickname"), user_obj.get("nickname"), user_obj.get("unique_id")),
                    content=content,
                    like_count=first_text(item.get("digg_count"), item.get("like_count")),
                    publish_time=format_timestamp_text(first_text(item.get("create_time"), item.get("publish_time"))),
                )
            )
            if len(comments) >= limit:
                return comments
    return comments


def is_non_comment_text(text: str) -> bool:
    value = clean_text(text)
    if not value:
        return True
    blocked_patterns = (
        r"^作者声明[:：]",
        r"虚构演绎",
        r"仅供娱乐",
        r"拍摄于\s*\d{4}-\d{2}-\d{2}",
        r"^继续观看$",
        r"^已购/收藏$",
        r"^去看更多",
    )
    return any(re.search(pattern, value) for pattern in blocked_patterns)


async def labeled_text(page: Page, label: str) -> str:
    try:
        locator = page.get_by_text(re.compile(label)).first
        text = await visible_text(locator)
    except Exception:
        text = ""
    if not text:
        return ""
    text = re.sub(label, "", text, count=1)
    return clean_text(text.strip(":： "))


async def merge_detail_from_dom(page: Page, info: DramaInfo, comment_limit: int) -> None:
    body_text = await visible_text(page.locator("body"))
    info.producer = info.producer or await labeled_text(page, "出品方|作者|剧场|版权方")
    info.category = info.category or await labeled_text(page, "题材|类型|分类")
    info.episode_count = info.episode_count or await labeled_text(page, "集数|共")

    if not info.like_count:
        match = re.search(r"(点赞|赞)\s*[:：]?\s*([0-9.,万亿wW]+)", body_text)
        info.like_count = match.group(2) if match else ""
    if not info.favorite_count:
        match = re.search(r"(收藏)\s*[:：]?\s*([0-9.,万亿wW]+)", body_text)
        info.favorite_count = match.group(2) if match else ""
    if not info.comment_count:
        match = re.search(r"(评论)\s*[:：]?\s*([0-9.,万亿wW]+)", body_text)
        info.comment_count = match.group(2) if match else ""
    if not info.heat_text:
        info.heat_text = heat_from_visible_text(body_text)

    if not info.comments:
        info.comments = await comments_from_dom(page, comment_limit)


async def comments_from_dom(page: Page, limit: int) -> list[CommentInfo]:
    try:
        rows = await page.evaluate(
            """limit => {
                const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                const infoNodes = Array.from(document.querySelectorAll('[class*="comment-item-info-wrap"]'));
                const roots = [];
                for (const info of infoNodes) {
                    let root = info.parentElement;
                    for (let i = 0; root && i < 4; i += 1, root = root.parentElement) {
                        const text = clean(root.innerText || root.textContent || '');
                        if (
                            root.querySelector('[class*="comment-item-info-wrap"]') &&
                            root.querySelector('[class*="comment-item-stats-container"]') &&
                            /回复|分享/.test(text)
                        ) {
                            roots.push(root);
                            break;
                        }
                    }
                }
                const result = [];
                const seen = new Set();
                for (const node of roots) {
                    const text = clean(node.innerText || node.textContent || '');
                    if (!text || text.length < 4 || text.length > 900) continue;
                    if (!/回复|分享|小时前|分钟前|天前|周前|月前|刚刚/.test(text)) continue;

                    const info = node.querySelector('[class*="comment-item-info-wrap"]');
                    const stats = node.querySelector('[class*="comment-item-stats-container"]');
                    const children = Array.from(node.children);
                    const user = clean(info ? info.innerText : '').replace(/\\s*\\.\\.\\.$/, '');
                    let content = '';
                    for (const child of children) {
                        if (child === info || child === stats) continue;
                        const childText = clean(child.innerText || child.textContent || '');
                        if (
                            childText &&
                            !/回复|分享|小时前|分钟前|天前|周前|月前|刚刚/.test(childText) &&
                            childText !== user
                        ) {
                            content = childText;
                            break;
                        }
                    }
                    if (!content && info && info.nextElementSibling) {
                        content = clean(info.nextElementSibling.innerText || info.nextElementSibling.textContent || '');
                    }
                    if (!content) continue;
                    const publishMatch = text.match(/(?:刚刚|\\d+\\s*(?:分钟|小时|天|周|月)前(?:·[^\\s]+)?)/);
                    const publishTime = publishMatch ? publishMatch[0] : '';
                    const likeMatch = text.match(/(?:\\s|^)(\\d+(?:\\.\\d+)?[万亿]?)(?=\\s*分享\\s*回复|\\s*回复)/);
                    const likeCount = likeMatch ? likeMatch[1] : '';
                    const key = `${user}|${content}`;
                    if (!content || seen.has(key)) continue;
                    seen.add(key);
                    result.push({user, content, like_count: likeCount, publish_time: publishTime});
                    if (result.length >= limit) break;
                }
                return result;
            }""",
            limit,
        )
    except Exception:
        return []

    comments: list[CommentInfo] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        content = clean_text(row.get("content"))
        if not content or is_non_comment_text(content):
            continue
        comments.append(
            CommentInfo(
                user=clean_text(row.get("user")),
                content=content,
                like_count=clean_text(row.get("like_count")),
                publish_time=clean_text(row.get("publish_time")),
            )
        )
        if len(comments) >= limit:
            break
    return comments


async def comment_dom_count(page: Page) -> int:
    try:
        return int(
            await page.evaluate(
                """() => document.querySelectorAll('[class*="comment-item-info-wrap"]').length"""
            )
        )
    except Exception:
        return 0


def debug_item(item: DramaInfo, enabled: bool, message: str) -> None:
    if not enabled:
        return
    item.debug_notes.append(message)
    safe_print(f"[debug][{item.tab}][{item.rank}][{item.title}] {message}")


async def open_detail_page(
    context: BrowserContext,
    list_page: Page,
    item: DramaInfo,
    timeout_seconds: float,
    captures: list[dict[str, Any] | list[Any]],
) -> Page:
    nav_timeout_ms = int(max(timeout_seconds, 8.0) * 1000)
    if item.item_id:
        item.detail_url = f"https://www.douyin.com/video/{item.item_id}"
    if item.detail_url and not item.detail_url.startswith("dom://"):
        page = await context.new_page()
        page.on("response", lambda response: schedule_capture_response(response, captures))
        await page.goto(item.detail_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
        return page

    marker = item.detail_url.removeprefix("dom://") if item.detail_url.startswith("dom://") else ""
    candidates: list[Locator] = []
    if marker:
        candidates.append(list_page.locator(f'[data-aiworker-detail-card="{marker}"]').first)
    if item.title:
        candidates.extend(
            [
                list_page.get_by_text(item.title, exact=True).first,
                list_page.locator(f"text={item.title}").first,
            ]
        )

    for locator in candidates:
        try:
            before_url = list_page.url
            async with context.expect_page(timeout=4_000) as page_info:
                await locator.click(timeout=3_000, force=True)
            page = await page_info.value
            await page.wait_for_load_state("domcontentloaded", timeout=30_000)
            item.detail_url = page.url
            return page
        except PlaywrightTimeoutError:
            try:
                await list_page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                pass
            if list_page.url != before_url:
                item.detail_url = list_page.url
                page = await context.new_page()
                page.on("response", lambda response: schedule_capture_response(response, captures))
                await page.goto(item.detail_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
                try:
                    await list_page.go_back(wait_until="domcontentloaded", timeout=20_000)
                except Exception:
                    await list_page.goto(SERIES_URL, wait_until="domcontentloaded", timeout=60_000)
                    await click_text(list_page, item.rank_category or "短剧", timeout=10_000)
                    await click_text(list_page, item.tab, timeout=10_000)
                return page
        except Exception:
            continue

    fallback_url = normalize_detail_url("", item.series_id, item.item_id)
    if fallback_url:
        page = await context.new_page()
        page.on("response", lambda response: schedule_capture_response(response, captures))
        await page.goto(fallback_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
        item.detail_url = fallback_url
        return page
    raise RuntimeError("未发现详情链接，且无法通过榜单卡片进入详情页")


async def enrich_detail(
    context: BrowserContext,
    list_page: Page,
    item: DramaInfo,
    comment_limit: int,
    timeout_seconds: float,
    debug: bool = False,
) -> DramaInfo:
    return await _enrich_detail(context, list_page, item, comment_limit, timeout_seconds, debug)


async def _enrich_detail(
    context: BrowserContext,
    list_page: Page,
    item: DramaInfo,
    comment_limit: int,
    timeout_seconds: float,
    debug: bool = False,
) -> DramaInfo:
    captures: list[dict[str, Any] | list[Any]] = []
    detail_page: Optional[Page] = None
    try:
        detail_page = await asyncio.wait_for(
            open_detail_page(context, list_page, item, timeout_seconds, captures),
            timeout=max(timeout_seconds + 4.0, 12.0),
        )
        debug_item(item, debug, f"opened url={detail_page.url} item_id={item.item_id or '-'} series_id={item.series_id or '-'}")
        await detail_page.wait_for_timeout(2_000)
        merge_detail_from_json(item, captures)
        await merge_detail_from_dom(detail_page, item, comment_limit)
        debug_item(
            item,
            debug,
            f"after initial merge captures={len(captures)} comments={len(item.comments)} dom_comments={await comment_dom_count(detail_page)}",
        )
        if not item.detail_url:
            item.detail_url = detail_page.url
        if not item.item_id:
            candidate_item_id = first_video_id_from_captures(captures, item.series_id)
            if candidate_item_id:
                debug_item(item, debug, f"found candidate video id from playlet responses: {candidate_item_id}")
                item.item_id = candidate_item_id
                item.detail_url = f"https://www.douyin.com/video/{candidate_item_id}"
                await detail_page.close()
                detail_page = await context.new_page()
                detail_page.on("response", lambda response: schedule_capture_response(response, captures))
                await detail_page.goto(item.detail_url, wait_until="domcontentloaded", timeout=int(max(timeout_seconds, 8.0) * 1000))
                await detail_page.wait_for_timeout(2_000)
                merge_detail_from_json(item, captures)
                await merge_detail_from_dom(detail_page, item, comment_limit)
                debug_item(
                    item,
                    debug,
                    f"after video fallback comments={len(item.comments)} dom_comments={await comment_dom_count(detail_page)} url={detail_page.url}",
                )
        try:
            if item.item_id:
                await detail_page.mouse.wheel(0, 900)
                await detail_page.wait_for_timeout(2_000)
                await detail_page.wait_for_selector('[class*="comment-item-info-wrap"]', timeout=5_000)
            else:
                await detail_page.mouse.wheel(0, 700)
                await detail_page.wait_for_timeout(1_000)
                await click_text(detail_page, "评论", timeout=2_000)
                await detail_page.wait_for_timeout(2_000)
                await detail_page.mouse.wheel(0, 900)
                await detail_page.wait_for_timeout(1_500)
        except Exception:
            pass
        merge_detail_from_json(item, captures)
        if not item.comments:
            item.comments = comments_from_json(captures, comment_limit)
        await merge_detail_from_dom(detail_page, item, comment_limit)
        debug_item(
            item,
            debug,
            f"final comments={len(item.comments)} captures={len(captures)} dom_comments={await comment_dom_count(detail_page)} item_id={item.item_id or '-'}",
        )
        if item.detail_error and (item.description or item.like_count or item.favorite_count):
            item.detail_error = ""
    except asyncio.TimeoutError:
        item.detail_error = f"detail open timeout after {timeout_seconds:g}s"
    except Exception as exc:
        item.detail_error = str(exc)
    finally:
        if detail_page:
            try:
                await detail_page.close()
            except Exception:
                pass
    return item


async def prepare_series_page(page: Page, category: str) -> None:
    await page.goto(SERIES_URL, wait_until="domcontentloaded", timeout=120_000)
    await page.wait_for_timeout(3_000)
    if not await click_text(page, category, timeout=15_000):
        body_text = await visible_text(page.locator("body"))
        if not ("热榜" in body_text and "榜单" in body_text and "集" in body_text):
            raise RuntimeError(f"无法点击左侧{category}入口")


async def collect_tab(
    context: BrowserContext,
    page: Page,
    category: str,
    tab: str,
    limit: int,
    comment_limit: int,
    detail_timeout: float,
    debug: bool = False,
) -> list[DramaInfo]:
    list_captures: list[dict[str, Any] | list[Any]] = []
    handler = lambda response: schedule_capture_response(response, list_captures)
    page.on("response", handler)
    try:
        clicked_tab = await click_tab_text(page, tab, timeout=15_000)
        if not clicked_tab:
            body_text = await visible_text(page.locator("body"))
            if not ("热榜" in body_text and "榜单" in body_text and "集" in body_text):
                raise RuntimeError(f"无法点击页签: {tab}")
        await page.wait_for_timeout(2_000)
        await page.mouse.wheel(0, -5000)
        await page.wait_for_timeout(1_000)
        for _ in range(10):
            records = collect_records_from_tab_json(list_captures, category, tab, limit)
            if records:
                break
            await page.wait_for_timeout(1_000)
        else:
            records = []
        if not records:
            records = await collect_rank_cards(page, category, tab, limit)
        if not records:
            for _ in range(3):
                await page.mouse.wheel(0, 900)
                await page.wait_for_timeout(1_500)
                records = collect_records_from_tab_json(list_captures, category, tab, limit)
                if not records:
                    records = await collect_rank_cards(page, category, tab, limit)
                if records:
                    break
        merge_records_from_json(records, list_captures)
        safe_print(f"[{category}/{tab}] collected {len(records)} cards")
        for index, item in enumerate(records, start=1):
            safe_print(f"[{category}/{tab}] detail {index}/{len(records)} {item.title}")
            await enrich_detail(context, page, item, comment_limit, detail_timeout, debug)
            await asyncio.sleep(0.8)
        return records
    finally:
        page.remove_listener("response", handler)


def render_record(record: DramaInfo, captured_at: str, comment_limit: int) -> list[str]:
    lines = [f"### {record.rank}. {md_escape(record.title)}", ""]
    lines.extend(
        markdown_table(
            ["字段", "值"],
            [
                ["排名", record.rank],
                ["剧名", record.title],
                ["分类", record.rank_category or "短剧"],
                ["榜单页签", record.tab],
                ["详情链接", record.detail_url],
                ["Series ID", record.series_id],
                ["Album ID", record.album_id],
                ["Item ID", record.item_id],
                ["封面", record.cover_url],
                ["热度/播放量", record.heat_text],
                ["卡片集数", record.card_episode_text],
                ["详情集数", record.episode_count],
                ["题材/类型", first_text(record.category, record.card_category_text)],
                ["出品方/作者/剧场", record.producer],
                ["上线/更新时间", record.online_time],
                ["简介", record.description],
                ["点赞", record.like_count],
                ["收藏", record.favorite_count],
                ["评论数", record.comment_count],
                ["采集时间", captured_at],
            ],
        )
    )
    lines.append("")
    lines.append(f"**点赞/页面可见 Top{comment_limit} 评论**")
    lines.append("")
    if record.comments:
        lines.extend(
            markdown_table(
                ["序号", "用户", "评论内容", "点赞数", "发布时间"],
                [
                    [index, comment.user, comment.content, comment.like_count, comment.publish_time]
                    for index, comment in enumerate(record.comments[:comment_limit], start=1)
                ],
            )
        )
    else:
        lines.append("_暂无评论数据_")
    lines.append("")
    if record.detail_error:
        lines.extend(["**详情采集失败原因**", "", f"> {md_escape(record.detail_error)}", ""])
    if record.debug_notes:
        lines.extend(["**Debug Notes**", ""])
        for note in record.debug_notes:
            lines.append(f"- {md_escape(note)}")
        lines.append("")
    return lines


def render_markdown(
    grouped_records: dict[str, list[DramaInfo]],
    captured_at: str,
    limit: int,
    comment_limit: int,
    previous_path: Path | None = None,
) -> str:
    lines = [
        "# 抖音短剧/漫剧榜单",
        "",
        f"- 采集时间：{captured_at}",
        f"- 页面地址：{SERIES_URL}",
        f"- 分类：{'、'.join(RANK_CATEGORIES)}",
        "- 页签：热榜、榜单",
        f"- 每页签采集上限：{limit}",
        f"- 评论采集上限：Top{comment_limit}",
        "- 数据来源：Playwright/CDP 页面渲染 + 详情页响应监听",
        "- 说明：评论优先取点赞最高或接口返回顺序；无法确认排序时为页面可见 Top10。",
        "",
    ]
    lines.extend(render_douyin_comparison(grouped_records, previous_path))
    for category in RANK_CATEGORIES:
        for tab in TABS:
            records = grouped_records.get(group_key(category, tab), [])
            lines.extend([f"## {section_title(category, tab)}", "", f"共 {len(records)} 部", ""])
            if not records:
                lines.extend(["_暂无数据_", ""])
                continue
            for record in records:
                lines.extend(render_record(record, captured_at, comment_limit))
    return "\n".join(lines).rstrip() + "\n"


async def run(args: argparse.Namespace) -> Path:
    config.PLATFORM = "dy"
    if args.keep_browser:
        config.AUTO_CLOSE_BROWSER = False
    cdp_manager = CDPBrowserManager()
    grouped_records: dict[str, list[DramaInfo]] = {}
    cookie_path = resolve_cookie_path(args.cookie_file)
    context: Optional[BrowserContext] = None
    async with async_playwright() as playwright:
        try:
            context = await cdp_manager.launch_and_connect(
                playwright=playwright,
                headless=config.CDP_HEADLESS,
            )
            await cdp_manager.add_stealth_script()
            await load_saved_cookies(context, cookie_path)
            page = await context.new_page()
            await wait_for_manual_login(page, args.login_wait_seconds, cookie_path, context)
            for category in RANK_CATEGORIES:
                try:
                    await prepare_series_page(page, category)
                except Exception as exc:
                    safe_print(f"[{category}] skipped: {exc}")
                    for tab in TABS:
                        grouped_records[group_key(category, tab)] = []
                    continue
                for tab in TABS:
                    grouped_records[group_key(category, tab)] = await collect_tab(
                        context,
                        page,
                        category,
                        tab,
                        args.limit,
                        args.comment_limit,
                        args.detail_timeout,
                        args.debug,
                    )
            await page.close()
        finally:
            if context:
                await save_current_cookies(context, cookie_path)
            await cdp_manager.cleanup()

    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    current_date = datetime.now().strftime("%Y%m%d")
    previous_path = find_previous_douyin_file(output_dir, current_date)
    output_path = output_dir / f"douyin_series_rank_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    output_path.write_text(
        render_markdown(grouped_records, captured_at, args.limit, args.comment_limit, previous_path),
        encoding="utf-8",
    )
    json_path = output_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(render_json_payload(grouped_records, captured_at, output_path), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def main() -> int:
    args = parse_args()
    if args.limit <= 0:
        print("--limit must be greater than 0", file=sys.stderr)
        return 2
    if args.comment_limit <= 0:
        print("--comment-limit must be greater than 0", file=sys.stderr)
        return 2
    if args.detail_timeout <= 0:
        print("--detail-timeout must be greater than 0", file=sys.stderr)
        return 2
    if args.login_wait_seconds < 0:
        print("--login-wait-seconds must be greater than or equal to 0", file=sys.stderr)
        return 2
    output_path = asyncio.run(run(args))
    safe_print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
