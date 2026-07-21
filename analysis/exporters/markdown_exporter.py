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

import pathlib
from typing import Iterable, Union

from analysis.schema import BilibiliAnalysisResult


def _yaml_scalar(value: object) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yaml_list(values: Iterable[str]) -> str:
    return "[" + ", ".join(_yaml_scalar(value) for value in values) + "]"


def render_markdown(result: BilibiliAnalysisResult) -> str:
    stats = result.stats or {}
    key_points = result.key_points or ["待生成"]
    analysis = result.analysis or {}
    risk_flags = analysis.get("risk_flags") or []

    return "\n".join([
        "---",
        "source: bilibili",
        f"aid: {result.aid}",
        f"bvid: {_yaml_scalar(result.bvid)}",
        f"url: {_yaml_scalar(result.url)}",
        f"author: {_yaml_scalar(result.author)}",
        f"published_at: {_yaml_scalar(result.published_at)}",
        f"transcript_source: {_yaml_scalar(result.transcript_source)}",
        f"tags: {_yaml_list(result.tags)}",
        "---",
        "",
        f"# {result.title}",
        "",
        "## 摘要",
        "",
        result.summary or "待生成",
        "",
        "## 关键点",
        "",
        *[f"- {point}" for point in key_points],
        "",
        "## 风险提示",
        "",
        *([f"- {flag}" for flag in risk_flags] if risk_flags else ["- 无明显风险提示"]),
        "",
        "## 基本信息",
        "",
        f"- UP主: {result.author}",
        f"- 播放: {stats.get('view', 0)}",
        f"- 点赞: {stats.get('like', 0)}",
        f"- 投币: {stats.get('coin', 0)}",
        f"- 收藏: {stats.get('favorite', 0)}",
        f"- 评论: {stats.get('reply', 0)}",
        "",
        "## 转写全文",
        "",
        result.transcript,
        "",
    ])


def export_markdown(result: BilibiliAnalysisResult, output_dir: Union[str, pathlib.Path]) -> pathlib.Path:
    output_path = pathlib.Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / f"{result.aid}.md"
    file_path.write_text(render_markdown(result), encoding="utf-8")
    return file_path
