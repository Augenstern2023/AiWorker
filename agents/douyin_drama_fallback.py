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
import os
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


def local_playwright_chromium_executable() -> Path | None:
    """Return a locally installed Playwright Chromium binary when available."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    browser_root = Path(local_app_data) / "ms-playwright"
    candidates = list(browser_root.glob("chromium-*/chrome-win/chrome.exe"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


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


def _title_key(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(value or "").casefold())


def _mix_matches_title(mix_info: Any, title: str) -> bool:
    if not isinstance(mix_info, dict):
        return False
    mix_key = _title_key(mix_info.get("mix_name") or "")
    title_key = _title_key(title)
    return bool(mix_key and title_key and (mix_key == title_key or title_key in mix_key))


def _public_video(info: dict[str, Any], title: str, *, source_kind: str = "search", episode: int | None = None) -> dict[str, Any]:
    stats = info.get("statistics") or {}
    author = info.get("author") or {}
    paid_reason = paid_content_reason(info)
    candidate_title = str(info.get("desc") or "").strip() or title
    metadata = parse_episode_metadata(candidate_title)
    mix_info = info.get("mix_info") if isinstance(info.get("mix_info"), dict) else {}
    if episode is None:
        current_episode = (mix_info.get("statis") or {}).get("current_episode")
        try:
            episode = int(current_episode) if current_episode else None
        except (TypeError, ValueError):
            episode = None
    if episode and not metadata["episode_start"]:
        metadata = {
            "episode_start": episode,
            "episode_end": episode,
            "episode_count": 1,
            "episode_label": f"第 {episode} 集",
        }
    return {
        "source": "douyin_fallback",
        "source_kind": source_kind,
        "aweme_id": str(info.get("aweme_id") or ""),
        "title": candidate_title,
        "author": str(author.get("nickname") or ""),
        "sec_uid": str(author.get("sec_uid") or ""),
        "cover": _extract_content_cover_url(info),
        "duration": int((info.get("video") or {}).get("duration") or info.get("duration") or 0),
        "like_count": stats.get("digg_count") or 0,
        "comment_count": stats.get("comment_count") or 0,
        "url": f"https://www.douyin.com/video/{info.get('aweme_id') or ''}",
        "is_paid": bool(paid_reason),
        "paid_reason": paid_reason,
        "is_compilation": bool((metadata.get("episode_count") or 0) > 1),
        **metadata,
    }


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
            candidate = _public_video(info, title)
            mix_info = info.get("mix_info") if isinstance(info.get("mix_info"), dict) else {}
            series_info = info.get("series_info") if isinstance(info.get("series_info"), dict) else {}
            collection_info = mix_info or series_info
            collection_name = str(
                collection_info.get("mix_name")
                or collection_info.get("series_name")
                or ""
            ).strip()
            collection_id = str(
                collection_info.get("mix_id")
                or collection_info.get("series_id")
                or ""
            ).strip()
            is_collection = bool(collection_id or collection_name)
            collection_matches_title = _mix_matches_title(mix_info, title) or (
                bool(series_info)
                and _title_key(title) in _title_key(series_info.get("series_name") or "")
            )
            statis = (
                mix_info.get("statis")
                if isinstance(mix_info.get("statis"), dict)
                else series_info.get("stats")
                if isinstance(series_info.get("stats"), dict)
                else {}
            )
            try:
                collection_episode_count = int(statis.get("updated_to_episode") or 0)
            except (TypeError, ValueError):
                collection_episode_count = 0
            candidate.update({
                "is_primary": len(candidates) == 0,
                "is_collection": is_collection,
                "collection_matches_title": collection_matches_title,
                "collection_id": collection_id,
                "collection_name": collection_name,
                "collection_episode_count": collection_episode_count,
            })
            candidates.append(candidate)
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
                crawler.browser_context = await crawler.launch_browser(
                    playwright.chromium,
                    None,
                    None,
                    headless=True,
                    executable_path=local_playwright_chromium_executable(),
                )
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

    async def resolve_series(self, title: str, aweme_id: str) -> dict[str, Any]:
        crawler, playwright = await self._client()
        try:
            selected = await crawler.dy_client.get_video_by_id(aweme_id)
            if not selected:
                raise DouyinFallbackError("抖音未返回已确认视频的详情")
            mix_info = selected.get("mix_info") if isinstance(selected.get("mix_info"), dict) else {}
            if not mix_info:
                search_response = await crawler.dy_client.search_info_by_keyword(
                    keyword=title,
                    search_channel=SearchChannelType.VIDEO,
                    sort_type=SearchSortType.GENERAL,
                    publish_time=PublishTimeType.UNLIMITED,
                )
                for raw in search_response.get("data") or []:
                    if not isinstance(raw, dict):
                        continue
                    infos: list[dict[str, Any]] = []
                    if isinstance(raw.get("aweme_info"), dict):
                        infos.append(raw["aweme_info"])
                    mix_items = (raw.get("aweme_mix_info") or {}).get("mix_items")
                    if isinstance(mix_items, list):
                        infos.extend(item for item in mix_items if isinstance(item, dict))
                    matched = next(
                        (item for item in infos if str(item.get("aweme_id") or "") == aweme_id),
                        None,
                    )
                    if matched and isinstance(matched.get("mix_info"), dict):
                        mix_info = matched["mix_info"]
                        break
            episodes: list[dict[str, Any]] = []
            related: list[dict[str, Any]] = []
            warnings: list[str] = []
            seen: set[str] = set()
            collection_name = str(mix_info.get("mix_name") or "").strip()
            mix_id = str(mix_info.get("mix_id") or "").strip()

            if mix_id and _mix_matches_title(mix_info, title):
                cursor = ""
                while len(episodes) < 100:
                    page = await crawler.dy_client.get_mix_aweme_list(mix_id, cursor=cursor, count=20)
                    episode_map = page.get("item_id_to_episode") if isinstance(page.get("item_id_to_episode"), dict) else {}
                    for info in page.get("aweme_list") or []:
                        if not isinstance(info, dict):
                            continue
                        item_id = str(info.get("aweme_id") or "")
                        if not item_id or item_id in seen:
                            continue
                        seen.add(item_id)
                        try:
                            episode = int(episode_map.get(item_id)) if episode_map.get(item_id) is not None else None
                        except (TypeError, ValueError):
                            episode = None
                        item = _public_video(info, title, source_kind="collection", episode=episode)
                        if item["episode_start"]:
                            episodes.append(item)
                        else:
                            related.append(item)
                    if not page.get("has_more"):
                        break
                    next_cursor = str(page.get("cursor") or "")
                    if not next_cursor or next_cursor == cursor:
                        break
                    cursor = next_cursor
                if len(episodes) >= 100:
                    warnings.append("合集超过 100 条，本次仅展示前 100 条")
            else:
                item = _public_video(selected, title, source_kind="search")
                (episodes if item["episode_start"] else related).append(item)
                warnings.append("该视频详情未返回与剧名匹配的合集，暂时只能使用已确认视频")

            episodes.sort(key=lambda item: (int(item.get("episode_start") or 10**9), int(item.get("episode_end") or 10**9)))
            numbered = sorted({int(item["episode_start"]) for item in episodes if item.get("episode_start") and item.get("episode_start") == item.get("episode_end")})
            missing: list[int] = []
            range_label = ""
            if numbered:
                missing = [number for number in range(numbered[0], numbered[-1] + 1) if number not in numbered]
                prefix = f"合集{collection_name}" if collection_name else "已识别剧集"
                range_label = f"{prefix} · 已识别第 {numbered[0]}-{numbered[-1]} 集"
            elif episodes:
                range_label = f"已识别 {len(episodes)} 个视频"
            return {
                "episodes": episodes[:100],
                "related_videos": related,
                "missing_episodes": missing,
                "range_label": range_label,
                "warnings": warnings,
                "collection_id": mix_id if episodes and collection_name else "",
                "collection_name": collection_name if episodes else "",
            }
        except DouyinFallbackError:
            raise
        except Exception as exc:
            raise DouyinFallbackError(f"抖音合集解析失败: {exc}") from exc
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
