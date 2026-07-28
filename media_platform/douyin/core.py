# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/douyin/core.py
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
import random
from asyncio import Task
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import douyin as douyin_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var, project_var

from .client import DouYinClient
from .exception import DataFetchError
from .field import PublishTimeType, SearchChannelType, SearchSortType
from .help import parse_video_info_from_url, parse_creator_info_from_url
from .login import DouYinLogin


class DouYinCrawler(AbstractCrawler):
    context_page: Page
    dy_client: DouYinClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        self.index_url = "https://www.douyin.com/search/"
        self.cdp_manager = None
        self.ip_proxy_pool = None  # 代理IP池，用于代理自动刷新
        self._load_filter_config()

    def _load_filter_config(self):
        try:
            if os.path.exists("data.json"):
                with open("data.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        data = data[0]
                    if isinstance(data, dict):
                        config.VIDEO_FILTER = data.get("video_filter", False)
                        config.FILTER_HOST = data.get("host", "")
        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] 读取data.json配置失败: {e}")

    async def filter_video_titles(self, collected_items: Dict[str, List[Dict]]):
        """
        Filter video titles using external API
        collected_items: {project_name: [item1, item2, ...]}
        """
        if not config.VIDEO_FILTER or not config.FILTER_HOST:
            return

        utils.logger.info("[DouYinCrawler] 开始执行视频标题筛选流程...")

        # Load filter configs for each project
        project_configs = {}
        try:
            if os.path.exists("data.json"):
                with open("data.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        data = data[0]
                    items = data.get("items", [])
                    for item in items:
                        p_name = item.get("project")
                        if p_name:
                            project_configs[p_name] = {
                                "主题": item.get("project", ""),
                                "有关": item.get("有关视频判定", ""),
                                "无关": item.get("黑名单视频判定", "")
                            }
        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] 读取筛选配置失败: {e}")
            return

        api_url = f"{config.FILTER_HOST.rstrip('/')}/api/filter_video_titles"

        for project_name, items in collected_items.items():
            filter_config = project_configs.get(project_name)
            if not filter_config:
                utils.logger.warning(f"[DouYinCrawler] 未找到项目 {project_name} 的筛选配置，跳过筛选")
                continue

            utils.logger.info(f"[DouYinCrawler] 正在筛选项目 {project_name} 的视频，共 {len(items)} 条")

            items_to_check = []
            indices_to_check = []

            for i, item in enumerate(items):
                if "pass" not in item:
                    items_to_check.append(item)
                    indices_to_check.append(i)

            if not items_to_check:
                continue

            # Batch process
            batch_size = 10
            for i in range(0, len(items_to_check), batch_size):
                batch_items = items_to_check[i:i+batch_size]
                batch_indices = indices_to_check[i:i+batch_size]

                titles = [item.get("title", "") or item.get("desc", "") for item in batch_items]

                payload = {
                    "titles": titles,
                    "filter_config": filter_config
                }

                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(api_url, json=payload, timeout=30)
                        if resp.status_code == 200:
                            res_json = resp.json()
                            if res_json.get("success"):
                                results = res_json.get("results", [])
                                for j, passed in enumerate(results):
                                    if j < len(batch_items):
                                        idx = batch_indices[j]
                                        items[idx]["pass"] = passed
                                        utils.logger.info(f"[DouYinCrawler] 标题筛选: {'通过' if passed else '拒绝'} - {titles[j][:20]}...")
                            else:
                                utils.logger.error(f"[DouYinCrawler] 筛选接口返回失败: {res_json}")
                        else:
                            utils.logger.error(f"[DouYinCrawler] 筛选接口HTTP错误: {resp.status_code}")
                except Exception as e:
                    utils.logger.error(f"[DouYinCrawler] 筛选请求异常: {e}")

            # After updating items in memory, update the file
            await self.update_project_file_status(project_name, items)

    async def update_project_file_status(self, project_name: str, raw_items: List[Dict]):
        """Update the JSON file for a specific project with pass status"""
        from tools.utils import utils
        import aiofiles

        # Create a map of aweme_id -> pass status from raw items
        status_map = {}
        for item in raw_items:
            if "pass" in item:
                status_map[item.get("aweme_id")] = item.get("pass")

        if not status_map:
            return

        date_str = utils.get_current_date()
        c_type = crawler_type_var.get()
        file_path = f"data/douyin/json/{c_type}_contents_{date_str}.json"

        if not os.path.exists(file_path):
            return

        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                data = json.loads(content)

            updated = False
            if isinstance(data, dict):
                project_items = data.get(project_name, [])
                for item in project_items:
                    aid = item.get("aweme_id")
                    if aid in status_map:
                        item["pass"] = status_map[aid]
                        updated = True

            if updated:
                async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, ensure_ascii=False, indent=4))

                utils.logger.info(f"[DouYinCrawler] 已更新项目 {project_name} 的文件数据(pass状态)")

        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] 更新文件失败: {e}")

    def _get_sleep_config(self, default_value: int = 60) -> int:
        """
        读取data.json中的sleep配置
        """
        try:
            if os.path.exists("data.json"):
                with open("data.json", "r", encoding="utf-8") as f:
                    data_config = json.load(f)
                    # 兼容data.json可能为列表的情况
                    if isinstance(data_config, list) and len(data_config) > 0:
                        data_config = data_config[0]

                    if isinstance(data_config, dict):
                        val = data_config.get("sleep")
                        if isinstance(val, int):
                            return val
        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] 读取data.json配置失败: {e}")
        return default_value

    async def start(self) -> None:
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            self.ip_proxy_pool = await create_ip_pool(config.IP_PROXY_POOL_COUNT, enable_validate_ip=True)
            ip_proxy_info: IpInfoModel = await self.ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

        async with async_playwright() as playwright:
            # 根据配置选择启动模式
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[DouYinCrawler] 使用CDP模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    None,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[DouYinCrawler] 使用标准模式启动浏览器")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    playwright_proxy_format,
                    user_agent=None,
                    headless=config.HEADLESS,
                )
                # stealth.min.js is a js script to prevent the website from detecting the crawler.
                await self.browser_context.add_init_script(path="libs/stealth.min.js")

            self.context_page = await self.browser_context.new_page()

            # 如果有cookie，先添加到浏览器上下文
            if config.COOKIES:
                utils.logger.info("[DouYinCrawler] 检测到cookie，正在添加到浏览器上下文...")
                for key, value in utils.convert_str_cookie_to_dict(config.COOKIES).items():
                    await self.browser_context.add_cookies([{
                        'name': key,
                        'value': value,
                        'domain': ".douyin.com",
                        'path': "/"
                    }])
                utils.logger.info("[DouYinCrawler] cookie添加完成")

            await self.context_page.goto(self.index_url)

            self.dy_client = await self.create_douyin_client(httpx_proxy_format)
            if not await self.dy_client.pong(browser_context=self.browser_context):
                login_obj = DouYinLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # you phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.dy_client.update_cookies(browser_context=self.browser_context)
            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                # Search for notes and retrieve their comment information.
                await self.search()
            elif config.CRAWLER_TYPE == "detail":
                # Get the information and comments of the specified post
                await self.get_specified_awemes()
            elif config.CRAWLER_TYPE == "creator":
                # Get the information and comments of the specified creator
                await self.get_creators_and_videos()

            utils.logger.info("[DouYinCrawler.start] Douyin Crawler finished ...")

    async def search(self) -> None:
        utils.logger.info("[DouYinCrawler.search] Begin search douyin keywords")
        dy_limit_count = 10  # douyin limit page fixed value
        if config.CRAWLER_MAX_NOTES_COUNT < dy_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = dy_limit_count
        start_page = config.START_PAGE  # start page number
        for keyword in config.KEYWORDS.split(","):
            # Dictionary to store collected items by project for filtering (scoped to keyword)
            collected_data: Dict[str, List[Dict]] = {}

            source_keyword_var.set(keyword)
            # 设置当前关键词对应的项目列表
            projects = config.PROJECT_KEYWORD_MAP.get(keyword, [])
            project_var.set(projects)

            utils.logger.info(f"[DouYinCrawler.search] Current keyword: {keyword}, Projects: {projects}")
            aweme_list: List[str] = []
            page = 0
            dy_search_id = ""
            while (page - start_page + 1) * dy_limit_count <= config.CRAWLER_MAX_NOTES_COUNT:
                if page < start_page:
                    utils.logger.info(f"[DouYinCrawler.search] Skip {page}")
                    page += 1
                    continue
                try:
                    utils.logger.info(f"[DouYinCrawler.search] search douyin keyword: {keyword}, page: {page}")
                    posts_res = await self.dy_client.search_info_by_keyword(
                        keyword=keyword,
                        offset=page * dy_limit_count - dy_limit_count,
                        search_channel=SearchChannelType(config.SEARCH_CHANNEL),
                        sort_type=SearchSortType(config.SEARCH_SORT_TYPE),
                        publish_time=PublishTimeType(config.PUBLISH_TIME_TYPE),
                        search_id=dy_search_id,
                    )
                    if posts_res.get("data") is None or posts_res.get("data") == []:
                        utils.logger.info(f"[DouYinCrawler.search] search douyin keyword: {keyword}, page: {page} is empty,{posts_res.get('data')}`")
                        sleep_seconds = self._get_sleep_config(default_value=60)
                        utils.logger.info(f"[DouYinCrawler.search] 搜索结果为空，根据配置休息{sleep_seconds}秒...")
                        await asyncio.sleep(sleep_seconds)
                        break
                except DataFetchError:
                    utils.logger.error(f"[DouYinCrawler.search] search douyin keyword: {keyword} failed")
                    break

                page += 1
                if "data" not in posts_res:
                    utils.logger.error(f"[DouYinCrawler.search] search douyin keyword: {keyword} failed，账号也许被风控了。")
                    # break
                    # 使用tkinter弹出确认框
                    import tkinter as tk
                    from tkinter import messagebox
                    root = tk.Tk()
                    root.withdraw() # 隐藏主窗口
                    root.attributes("-topmost", True) # 窗口置顶
                    messagebox.showwarning("账号受限", "账号可能被风控或未登录。\n请在浏览器中切换账号或完成验证，然后点击确定继续。")
                    root.destroy()
                    continue # 继续重试当前页或下一页

                dy_search_id = posts_res.get("extra", {}).get("logid", "")
                for post_item in posts_res.get("data"):
                    try:
                        aweme_info: Dict = (post_item.get("aweme_info") or post_item.get("aweme_mix_info", {}).get("mix_items")[0])
                    except TypeError:
                        continue

                    aweme_list.append(aweme_info.get("aweme_id", ""))

                    # Store for filtering
                    for p in projects:
                        if p not in collected_data: collected_data[p] = []
                        collected_data[p].append(aweme_info)

                    await douyin_store.update_douyin_aweme(aweme_item=aweme_info)

                    if not config.VIDEO_FILTER:
                        await self.get_aweme_media(aweme_item=aweme_info)

                # Sleep after each page navigation
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[DouYinCrawler.search] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after page {page-1}")

            # Filter process
            if config.VIDEO_FILTER:
                await self.filter_video_titles(collected_data)

                passed_ids = set()
                processed_ids = set()

                for p, items in collected_data.items():
                    for item in items:
                        if item.get("pass"):
                            aid = item.get("aweme_id")
                            passed_ids.add(aid)

                            if config.ENABLE_GET_MEIDAS and aid not in processed_ids:
                                await self.get_aweme_media(aweme_item=item)
                                processed_ids.add(aid)

                # Filter aweme_list for comments crawling
                aweme_list = [aid for aid in aweme_list if aid in passed_ids]

            utils.logger.info(f"[DouYinCrawler.search] keyword:{keyword}, aweme_list:{aweme_list}")
            await self.batch_get_note_comments(aweme_list)

            # 读取data.json中的sleep配置，在切换关键词时休息
            try:
                import json
                if os.path.exists("data.json"):
                    with open("data.json", "r", encoding="utf-8") as f:
                        data_config = json.load(f)
                        # 兼容data.json可能为列表的情况
                        if isinstance(data_config, list) and len(data_config) > 0:
                            data_config = data_config[0]

                        if isinstance(data_config, dict):
                            sleep_seconds = data_config.get("sleep", 0)
                            if isinstance(sleep_seconds, int) and sleep_seconds > 0:
                                utils.logger.info(f"[DouYinCrawler.search] 关键词 [{keyword}] 爬取完成，根据配置休息 {sleep_seconds} 秒...")
                                await asyncio.sleep(sleep_seconds)
            except Exception as e:
                utils.logger.error(f"[DouYinCrawler.search] 读取data.json配置失败: {e}")

    async def get_specified_awemes(self):
        """Get the information and comments of the specified post from URLs or IDs"""
        utils.logger.info("[DouYinCrawler.get_specified_awemes] Parsing video URLs...")
        aweme_id_list = []
        for video_url in config.DY_SPECIFIED_ID_LIST:
            try:
                video_info = parse_video_info_from_url(video_url)

                # 处理短链接
                if video_info.url_type == "short":
                    utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Resolving short link: {video_url}")
                    resolved_url = await self.dy_client.resolve_short_url(video_url)
                    if resolved_url:
                        # 从解析后的URL中提取视频ID
                        video_info = parse_video_info_from_url(resolved_url)
                        utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Short link resolved to aweme ID: {video_info.aweme_id}")
                    else:
                        utils.logger.error(f"[DouYinCrawler.get_specified_awemes] Failed to resolve short link: {video_url}")
                        continue

                aweme_id_list.append(video_info.aweme_id)
                utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Parsed aweme ID: {video_info.aweme_id} from {video_url}")
            except ValueError as e:
                utils.logger.error(f"[DouYinCrawler.get_specified_awemes] Failed to parse video URL: {e}")
                continue


        # semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        # task_list = [self.get_aweme_detail(aweme_id=aweme_id, semaphore=semaphore) for aweme_id in aweme_id_list]
        # aweme_details = await asyncio.gather(*task_list)
        # for aweme_detail in aweme_details:
        #     if aweme_detail is not None:
        #         await douyin_store.update_douyin_aweme(aweme_item=aweme_detail)
        #         await self.get_aweme_media(aweme_item=aweme_detail)
        # 直接获取评论，跳过上面获取视频详细信息的步骤
        # 原因：获取视频详细信息的请求经常失败（account blocked），但评论爬取不受影响
        utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Skipping aweme detail fetch, directly getting comments for {len(aweme_id_list)} videos")
        await self.batch_get_note_comments(aweme_id_list)

    async def get_aweme_detail(self, aweme_id: str, semaphore: asyncio.Semaphore) -> Any:
        """Get note detail"""
        async with semaphore:
            try:
                result = await self.dy_client.get_video_by_id(aweme_id)
                # Sleep after fetching aweme detail
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[DouYinCrawler.get_aweme_detail] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching aweme {aweme_id}")
                return result
            except DataFetchError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] Get aweme detail error: {ex}")
                return None
            except KeyError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] have not fund note detail aweme_id:{aweme_id}, err: {ex}")
                return None

    async def batch_get_note_comments(self, aweme_list: List[str]) -> None:
        """
        Batch get note comments
        """
        if not config.ENABLE_GET_COMMENTS:
            utils.logger.info(f"[DouYinCrawler.batch_get_note_comments] Crawling comment mode is not enabled")
            return

        task_list: List[Task] = []
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        for aweme_id in aweme_list:
            task = asyncio.create_task(self.get_comments(aweme_id, semaphore), name=aweme_id)
            task_list.append(task)
        if len(task_list) > 0:
            await asyncio.wait(task_list)

    async def get_comments(self, aweme_id: str, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                # 将关键词列表传递给 get_aweme_all_comments 方法
                # Use fixed crawling interval
                crawl_interval = config.CRAWLER_MAX_SLEEP_SEC
                await self.dy_client.get_aweme_all_comments(
                    aweme_id=aweme_id,
                    crawl_interval=crawl_interval,
                    is_fetch_sub_comments=config.ENABLE_GET_SUB_COMMENTS,
                    callback=douyin_store.batch_update_dy_aweme_comments,
                    max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
                )
                # Sleep after fetching comments
                await asyncio.sleep(crawl_interval)
                utils.logger.info(f"[DouYinCrawler.get_comments] Sleeping for {crawl_interval} seconds after fetching comments for aweme {aweme_id}")
                utils.logger.info(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} comments have all been obtained and filtered ...")
            except DataFetchError as e:
                utils.logger.error(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} get comments failed, error: {e}")

    async def get_creators_and_videos(self) -> None:
        """
        Get the information and videos of the specified creator from URLs or IDs
        """
        utils.logger.info("[DouYinCrawler.get_creators_and_videos] Begin get douyin creators")
        utils.logger.info("[DouYinCrawler.get_creators_and_videos] Parsing creator URLs...")

        for creator_url in config.DY_CREATOR_ID_LIST:
            try:
                creator_info_parsed = parse_creator_info_from_url(creator_url)
                user_id = creator_info_parsed.sec_user_id
                utils.logger.info(f"[DouYinCrawler.get_creators_and_videos] Parsed sec_user_id: {user_id} from {creator_url}")
            except ValueError as e:
                utils.logger.error(f"[DouYinCrawler.get_creators_and_videos] Failed to parse creator URL: {e}")
                continue

            creator_info: Dict = await self.dy_client.get_user_info(user_id)
            if creator_info:
                await douyin_store.save_creator(user_id, creator=creator_info)

            # Get all video information of the creator
            all_video_list = await self.dy_client.get_all_user_aweme_posts(sec_user_id=user_id, callback=self.fetch_creator_video_detail)

            video_ids = [video_item.get("aweme_id") for video_item in all_video_list]
            await self.batch_get_note_comments(video_ids)

    async def fetch_creator_video_detail(self, video_list: List[Dict]):
        """
        Concurrently obtain the specified post list and save the data
        """
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_aweme_detail(post_item.get("aweme_id"), semaphore) for post_item in video_list]

        note_details = await asyncio.gather(*task_list)
        for aweme_item in note_details:
            if aweme_item is not None:
                await douyin_store.update_douyin_aweme(aweme_item=aweme_item)
                await self.get_aweme_media(aweme_item=aweme_item)

    async def create_douyin_client(self, httpx_proxy: Optional[str]) -> DouYinClient:
        """Create douyin client"""
        cookie_str, cookie_dict = utils.convert_cookies(await self.browser_context.cookies())  # type: ignore
        douyin_client = DouYinClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": await self.context_page.evaluate("() => navigator.userAgent"),
                "Cookie": cookie_str,
                "Host": "www.douyin.com",
                "Origin": "https://www.douyin.com/",
                "Referer": "https://www.douyin.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=self.ip_proxy_pool,  # 传递代理池用于自动刷新
        )
        return douyin_client

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
        executable_path: Optional[Path] = None,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        if config.SAVE_LOGIN_STATE:
            user_data_dir = os.path.join(os.getcwd(), "browser_data", config.USER_DATA_DIR % config.PLATFORM)  # type: ignore
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=playwright_proxy,  # type: ignore
                viewport={
                    "width": 1920,
                    "height": 1080
                },
                user_agent=user_agent,
                executable_path=str(executable_path) if executable_path else None,
            )  # type: ignore
            return browser_context
        else:
            browser = await chromium.launch(
                headless=headless,
                proxy=playwright_proxy,
                executable_path=str(executable_path) if executable_path else None,
            )  # type: ignore
            browser_context = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=user_agent)
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        使用CDP模式启动浏览器
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # 添加反检测脚本
            await self.cdp_manager.add_stealth_script()

            # 显示浏览器信息
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[DouYinCrawler] CDP浏览器信息: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] CDP模式启动失败，回退到标准模式: {e}")
            # 回退到标准模式
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def close(self) -> None:
        """Close browser context"""
        # 如果使用CDP模式，需要特殊处理
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[DouYinCrawler.close] Browser context closed ...")

    async def get_aweme_media(self, aweme_item: Dict):
        """
        获取抖音媒体，自动判断媒体类型是短视频还是帖子图片并下载

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            utils.logger.info(f"[DouYinCrawler.get_aweme_media] Crawling image mode is not enabled")
            return
        # 笔记 urls 列表，若为短视频类型则返回为空列表
        note_download_url: List[str] = douyin_store._extract_note_image_list(aweme_item)
        # 视频 url，永远存在，但为短视频类型时的文件其实是音频文件
        video_download_url: str = douyin_store._extract_video_download_url(aweme_item)
        # TODO: 抖音并没采用音视频分离的策略，故音频可从原视频中分离，暂不提取
        if note_download_url:
            await self.get_aweme_images(aweme_item)
        else:
            await self.get_aweme_video(aweme_item)

    async def get_aweme_images(self, aweme_item: Dict):
        """
        get aweme images. please use get_aweme_media

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")
        # 笔记 urls 列表，若为短视频类型则返回为空列表
        note_download_url: List[str] = douyin_store._extract_note_image_list(aweme_item)

        if not note_download_url:
            return
        picNum = 0
        for url in note_download_url:
            if not url:
                continue
            content = await self.dy_client.get_aweme_media(url)
            await asyncio.sleep(random.random())
            if content is None:
                continue
            extension_file_name = f"{picNum:>03d}.jpeg"
            picNum += 1
            await douyin_store.update_dy_aweme_image(aweme_id, content, extension_file_name)

    async def get_aweme_video(self, aweme_item: Dict):
        """
        get aweme videos. please use get_aweme_media

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")

        # 视频 url，永远存在，但为短视频类型时的文件其实是音频文件
        video_download_url: str = douyin_store._extract_video_download_url(aweme_item)

        if not video_download_url:
            return
        content = await self.dy_client.get_aweme_media(video_download_url)
        await asyncio.sleep(random.random())
        if content is None:
            return
        extension_file_name = f"video.mp4"
        await douyin_store.update_dy_aweme_video(aweme_id, content, extension_file_name)
