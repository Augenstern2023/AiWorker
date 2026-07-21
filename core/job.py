# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import config


def _split_csv(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


@dataclass
class CrawlJob:
    job_id: str
    platform: str
    crawler_type: str
    keywords: list[str] = field(default_factory=list)
    login_type: str = "qrcode"
    save_data_option: str = "json"
    start_page: int = 1
    max_notes_count: int = 200
    max_concurrency: int = 1
    get_comments: bool = True
    get_sub_comments: bool = False
    headless: bool = False
    auto_close: bool = True
    cdp_debug_port: int | None = None
    user_data_dir: str | None = None
    project_keyword_map: dict[str, list[str]] = field(default_factory=dict)
    project_name: str = ""
    specified_ids: list[str] = field(default_factory=list)
    creator_ids: list[str] = field(default_factory=list)
    cookies: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, job_id: str | None = None) -> "CrawlJob":
        return cls(
            job_id=job_id or f"job-{int(time.time())}",
            platform=config.PLATFORM,
            crawler_type=config.CRAWLER_TYPE,
            keywords=_split_csv(config.KEYWORDS),
            login_type=config.LOGIN_TYPE,
            save_data_option=config.SAVE_DATA_OPTION,
            start_page=config.START_PAGE,
            max_notes_count=config.CRAWLER_MAX_NOTES_COUNT,
            max_concurrency=config.MAX_CONCURRENCY_NUM,
            get_comments=config.ENABLE_GET_COMMENTS,
            get_sub_comments=config.ENABLE_GET_SUB_COMMENTS,
            headless=config.HEADLESS,
            auto_close=config.AUTO_CLOSE_BROWSER,
            cdp_debug_port=config.CDP_DEBUG_PORT,
            user_data_dir=config.USER_DATA_DIR,
            project_keyword_map=dict(config.PROJECT_KEYWORD_MAP or {}),
            project_name=config.PROJECT_NAME,
            cookies=config.COOKIES,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CrawlJob":
        values = dict(data)
        values["keywords"] = _split_csv(values.get("keywords", []))
        values["specified_ids"] = _split_csv(values.get("specified_ids", []))
        values["creator_ids"] = _split_csv(values.get("creator_ids", []))
        values["project_keyword_map"] = {
            str(keyword): _split_csv(projects)
            for keyword, projects in dict(values.get("project_keyword_map") or {}).items()
        }
        return cls(**values)

    @classmethod
    def load(cls, path: str | Path) -> "CrawlJob":
        with Path(path).open("r", encoding="utf-8-sig") as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    def apply_to_config(self) -> None:
        config.PLATFORM = self.platform
        config.LOGIN_TYPE = self.login_type
        config.CRAWLER_TYPE = self.crawler_type
        config.KEYWORDS = ",".join(self.keywords)
        config.SAVE_DATA_OPTION = self.save_data_option
        config.START_PAGE = self.start_page
        config.CRAWLER_MAX_NOTES_COUNT = self.max_notes_count
        config.MAX_CONCURRENCY_NUM = self.max_concurrency
        config.ENABLE_GET_COMMENTS = self.get_comments
        config.ENABLE_GET_SUB_COMMENTS = self.get_sub_comments
        config.HEADLESS = self.headless
        config.CDP_HEADLESS = self.headless
        config.AUTO_CLOSE_BROWSER = self.auto_close
        config.PROJECT_KEYWORD_MAP = dict(self.project_keyword_map)
        config.PROJECT_NAME = self.project_name
        config.COOKIES = self.cookies
        if self.cdp_debug_port is not None:
            config.CDP_DEBUG_PORT = self.cdp_debug_port
        if self.user_data_dir:
            config.USER_DATA_DIR = self.user_data_dir

        if self.specified_ids:
            if self.platform == "xhs":
                config.XHS_SPECIFIED_NOTE_URL_LIST = self.specified_ids
            elif self.platform == "bili":
                config.BILI_SPECIFIED_ID_LIST = self.specified_ids
            elif self.platform == "dy":
                config.DY_SPECIFIED_ID_LIST = self.specified_ids
            elif self.platform == "wb":
                config.WEIBO_SPECIFIED_ID_LIST = self.specified_ids
            elif self.platform == "ks":
                config.KS_SPECIFIED_ID_LIST = self.specified_ids

        if self.creator_ids:
            if self.platform == "xhs":
                config.XHS_CREATOR_ID_LIST = self.creator_ids
            elif self.platform == "bili":
                config.BILI_CREATOR_ID_LIST = self.creator_ids
            elif self.platform == "dy":
                config.DY_CREATOR_ID_LIST = self.creator_ids
            elif self.platform == "wb":
                config.WEIBO_CREATOR_ID_LIST = self.creator_ids
            elif self.platform == "ks":
                config.KS_CREATOR_ID_LIST = self.creator_ids

        for name, value in self.extra.items():
            if name.isupper() and hasattr(config, name):
                setattr(config, name, value)
