# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from media_platform.douyin.core import DouYinCrawler
from media_platform.douyin.field import PublishTimeType, SearchChannelType, SearchSortType
from store.douyin import _extract_content_cover_url, _extract_video_download_url
from agents.drama_download_policy import paid_content_reason


class DouyinFallbackError(RuntimeError):
    pass


class DouyinPaidContentError(DouyinFallbackError):
    pass


def parse_episode_metadata(value: str) -> dict[str, int | str | None]:
    text = str(value or "")
    range_match = re.search(r"(?<!\d)(\d{1,4})\s*[-~～—至到]\s*(\d{1,4})\s*集", text)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if 0 < start <= end:
            return {"episode_start": start, "episode_end": end, "episode_count": end - start + 1, "episode_label": f"{start}-{end} 集"}
    total_match = re.search(r"(?:全|共)\s*(\d{1,4})\s*集", text)
    if total_match:
        total = int(total_match.group(1))
        if total > 0:
            return {"episode_start": 1, "episode_end": total, "episode_count": total, "episode_label": f"全 {total} 集"}
    single_match = re.search(r"第\s*(\d{1,4})\s*集", text)
    if single_match:
        episode = int(single_match.group(1))
        if episode > 0:
            return {"episode_start": episode, "episode_end": episode, "episode_count": 1, "episode_label": f"第 {episode} 集"}
    return {"episode_start": None, "episode_end": None, "episode_count": None, "episode_label": ""}


def normalize_search_candidates(response: dict[str, Any], title: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in response.get("data") or []:
        if not isinstance(item, dict):
            continue
        infos: list[dict[str, Any]] = []
        aweme_info = item.get("aweme_info")
        if isinstance(aweme_info, dict):
            infos.append(aweme_info)
        mix_items = (item.get("aweme_mix_info") or {}).get("mix_items")
        if isinstance(mix_items, list):
            infos.extend(value for value in mix_items if isinstance(value, dict))
        for info in infos:
            aweme_id = str(info.get("aweme_id") or "")
            if not aweme_id or aweme_id in seen:
                continue
            seen.add(aweme_id)
            stats = info.get("statistics") or {}
            paid_reason = paid_content_reason(info)
            candidate_title = str(info.get("desc") or "").strip() or title
            candidates.append({
                "source": "douyin_fallback",
                "aweme_id": aweme_id,
                "title": candidate_title,
                "author": str((info.get("author") or {}).get("nickname") or ""),
                "cover": _extract_content_cover_url(info),
                "duration": int((info.get("video") or {}).get("duration") or 0),
                "like_count": stats.get("digg_count") or 0,
                "comment_count": stats.get("comment_count") or 0,
                "url": f"https://www.douyin.com/video/{aweme_id}",
                "is_primary": len(candidates) == 0,
                "is_paid": bool(paid_reason),
                "paid_reason": paid_reason,
                **parse_episode_metadata(candidate_title),
            })
    return candidates


class DouyinDramaFallbackClient:
    def __init__(self, background: bool = True) -> None:
        self.background = background

    @staticmethod
    def _saved_login_state() -> dict[str, Any]:
        """Read the QR-login state shared with the crawler control page."""
        path = Path("qrcode_login_state.json")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) and payload.get("platform") == "douyin" else {}

    async def _client(self):
        crawler = DouYinCrawler()
        playwright = await async_playwright().start()
        try:
            if self.background:
                crawler.browser_context = await crawler.launch_browser(playwright.chromium, None, None, headless=True)
                await crawler.browser_context.add_init_script(path="libs/stealth.min.js")
            else:
                crawler.browser_context = await crawler.launch_browser_with_cdp(playwright, None, None, headless=False)
            # The QR login flow stores cookies/localStorage in the same workspace.
            # CDP may attach to a different Chrome profile, so hydrate that state
            # before creating the API client instead of treating the user as logged out.
            login_state = self._saved_login_state()
            saved_cookies = login_state.get("cookies") if isinstance(login_state.get("cookies"), list) else []
            if saved_cookies:
                await crawler.browser_context.add_cookies(saved_cookies)
            crawler.context_page = await crawler.browser_context.new_page()
            await crawler.context_page.goto(crawler.index_url)
            saved_storage = login_state.get("local_storage")
            if isinstance(saved_storage, dict) and saved_storage:
                await crawler.context_page.evaluate(
                    """(entries) => {
                        for (const [key, value] of Object.entries(entries)) {
                            window.localStorage.setItem(key, String(value));
                        }
                    }""",
                    saved_storage,
                )
            crawler.dy_client = await crawler.create_douyin_client(None)
            if not await crawler.dy_client.pong(crawler.browser_context):
                raise DouyinFallbackError("抖音登录失效，请先在 CDP 浏览器完成登录")
            return crawler, playwright
        except Exception:
            await playwright.stop()
            raise

    async def _close(self, crawler: DouYinCrawler, playwright) -> None:
        # The CDP context belongs to the user's logged-in browser.  Only close
        # the temporary page and disconnect; closing the context would also
        # close the user's existing tabs/session.
        try:
            if getattr(crawler, "context_page", None):
                await crawler.context_page.close()
        finally:
            if crawler.cdp_manager and crawler.cdp_manager.browser and crawler.cdp_manager.browser.is_connected():
                await crawler.cdp_manager.browser.close()
            elif getattr(crawler, "browser_context", None):
                await crawler.browser_context.close()
        await playwright.stop()

    async def search(self, title: str) -> list[dict[str, Any]]:
        crawler, playwright = await self._client()
        try:
            response = await crawler.dy_client.search_info_by_keyword(
                keyword=title,
                search_channel=SearchChannelType.VIDEO,
                sort_type=SearchSortType.GENERAL,
                publish_time=PublishTimeType.UNLIMITED,
            )
            return normalize_search_candidates(response, title)
        except DouyinFallbackError:
            raise
        except Exception as exc:
            raise DouyinFallbackError(f"抖音检索失败: {exc}") from exc
        finally:
            await self._close(crawler, playwright)

    async def get_video(self, aweme_id: str) -> tuple[dict[str, Any], str]:
        crawler, playwright = await self._client()
        try:
            detail = await crawler.dy_client.get_video_by_id(aweme_id)
            paid_reason = paid_content_reason(detail)
            if paid_reason:
                raise DouyinPaidContentError(paid_reason)
            url = _extract_video_download_url(detail)
            if not url:
                raise DouyinFallbackError("抖音视频没有可下载地址")
            return detail, url
        except DouyinFallbackError:
            raise
        except Exception as exc:
            raise DouyinFallbackError(f"抖音视频详情获取失败: {exc}") from exc
        finally:
            await self._close(crawler, playwright)
