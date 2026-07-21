# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.hongguo_analysis_agent import (
    call_llm_json,
    markdown_table,
    md_escape,
    render_inline,
    render_markdown_value,
    safe_filename,
)
from tools.fetch_douyin_series_rank_md import clean_text


REPORT_KEYS = (
    "剧情类型",
    "核心卖点",
    "开头钩子",
    "情绪点",
    "爆款结构",
    "适合投放关键词",
    "评论反馈洞察",
    "素材切片建议",
    "风险提示",
    "置信度与依据",
)


@dataclass
class AnalyzeOptions:
    top_n: int = 10
    per_category: bool = True
    with_llm: bool = True
    download_media: bool = False
    with_asr: bool = False


def text_value(value: Any) -> str:
    return clean_text(value)


def load_rank_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Douyin rank JSON must be an object")
    if not isinstance(payload.get("records"), list):
        raise ValueError("Douyin rank JSON has no records list")
    return payload


def infer_job_id(rank_path: Path, payload: dict[str, Any]) -> str:
    parts = rank_path.parts
    if "jobs" in parts:
        index = parts.index("jobs")
        if index + 1 < len(parts):
            return parts[index + 1]
    return text_value(payload.get("job_id")) or "manual"


def default_output_dir(rank_path: Path, payload: dict[str, Any]) -> Path:
    return Path("data") / "dy" / "jobs" / infer_job_id(rank_path, payload) / "analysis"


def normalize_category(value: Any) -> str:
    text = text_value(value)
    if "漫" in text or "婕" in text:
        return "漫剧"
    if "短" in text or "鐭" in text:
        return "短剧"
    return text or "未知"


def normalize_tab(value: Any) -> str:
    text = text_value(value)
    if "热" in text or "鐑" in text:
        return "热榜"
    if "榜" in text or "姒" in text:
        return "榜单"
    return text


def number_from_text(value: Any) -> int:
    text = text_value(value).replace(",", "")
    if not text:
        return 0
    multiplier = 1
    if text.lower().endswith("w") or text.endswith("万") or text.endswith("涓?"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿") or text.endswith("浜?"):
        multiplier = 100_000_000
        text = text[:-1]
    import re

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return int(float(match.group(0)) * multiplier) if match else 0


def record_score(record: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        number_from_text(record.get("heat_text")),
        number_from_text(record.get("like_count")),
        number_from_text(record.get("favorite_count")),
        number_from_text(record.get("comment_count")),
    )


def select_records(records: list[dict[str, Any]], options: AnalyzeOptions) -> list[dict[str, Any]]:
    clean_records = [item for item in records if isinstance(item, dict) and text_value(item.get("title"))]
    if not options.per_category:
        return sorted(clean_records, key=record_score, reverse=True)[: max(1, options.top_n)]

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for category in ("短剧", "漫剧"):
        category_records = [
            item
            for item in clean_records
            if normalize_category(item.get("rank_category") or item.get("category")) == category
        ]
        for item in sorted(category_records, key=record_score, reverse=True)[: max(1, options.top_n)]:
            key = (category, text_value(item.get("title")))
            if key not in seen:
                selected.append(item)
                seen.add(key)
    return selected


def comment_texts(record: dict[str, Any], limit: int = 10) -> list[str]:
    comments = record.get("comments") if isinstance(record.get("comments"), list) else []
    texts = []
    for item in comments[:limit]:
        if not isinstance(item, dict):
            continue
        content = text_value(item.get("content"))
        if content:
            texts.append(content)
    return texts


def build_analysis_seed(record: dict[str, Any]) -> str:
    pieces = [
        text_value(record.get("title")),
        normalize_category(record.get("rank_category") or record.get("category")),
        normalize_tab(record.get("tab")),
        text_value(record.get("category") or record.get("card_category_text")),
        text_value(record.get("description")),
        "评论：" + " / ".join(comment_texts(record)),
    ]
    return "\n".join(piece for piece in pieces if piece and piece != "评论：")


def heuristic_tags(seed: str) -> list[str]:
    candidates = {
        "逆袭": ("逆袭", "翻身", "打脸", "落魄", "草根"),
        "甜宠": ("甜", "宠", "心动", "霸总"),
        "虐恋": ("离婚", "悔", "虐", "背叛", "误会"),
        "家庭": ("父", "母", "女儿", "儿子", "妻", "家"),
        "悬疑": ("真相", "秘密", "案件", "谜"),
        "职场": ("老板", "总裁", "公司", "上司", "职场"),
        "复仇": ("复仇", "报仇", "重生", "归来"),
    }
    return [label for label, words in candidates.items() if any(word in seed for word in words)][:8]


def heuristic_analysis(record: dict[str, Any]) -> dict[str, Any]:
    seed = build_analysis_seed(record)
    return {
        "title": text_value(record.get("title")),
        "rank": record.get("rank"),
        "category": normalize_category(record.get("rank_category") or record.get("category")),
        "tab": normalize_tab(record.get("tab")),
        "detail_url": text_value(record.get("detail_url")),
        "series_id": text_value(record.get("series_id")),
        "item_id": text_value(record.get("item_id")),
        "description": text_value(record.get("description")),
        "stats": {
            "heat": text_value(record.get("heat_text")),
            "like": text_value(record.get("like_count")),
            "favorite": text_value(record.get("favorite_count")),
            "comment": text_value(record.get("comment_count")),
        },
        "producer": text_value(record.get("producer")),
        "episode_count": text_value(record.get("episode_count") or record.get("card_episode_text")),
        "comments": comment_texts(record),
        "plot_tags": heuristic_tags(seed),
        "selling_points": [
            item
            for item in (
                text_value(record.get("description")),
                "高互动评论可作为投放素材的情绪反馈来源" if comment_texts(record) else "",
            )
            if item
        ],
        "analysis_seed": seed,
    }


def normalize_llm_report(payload: dict[str, Any]) -> dict[str, Any]:
    report = {}
    for key in REPORT_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            report[key] = [text_value(item) for item in value if text_value(item)]
        else:
            report[key] = text_value(value)
    return report


def build_llm_payload(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "douyin_series_rank",
        "title": analysis.get("title"),
        "category": analysis.get("category"),
        "tab": analysis.get("tab"),
        "description": analysis.get("description"),
        "stats": analysis.get("stats"),
        "producer": analysis.get("producer"),
        "episode_count": analysis.get("episode_count"),
        "comments": analysis.get("comments"),
        "analysis_seed": analysis.get("analysis_seed"),
    }


async def apply_llm_analysis(analysis: dict[str, Any]) -> None:
    result, error = await call_llm_json(build_llm_payload(analysis))
    if error:
        analysis["llm_error"] = error
        return
    analysis["llm_analysis"] = normalize_llm_report(result)
    analysis["llm_raw"] = result
    analysis.pop("llm_error", None)


def analysis_core_selling_points(analysis: dict[str, Any]) -> str:
    llm = analysis.get("llm_analysis") if isinstance(analysis.get("llm_analysis"), dict) else {}
    value = llm.get("核心卖点") or analysis.get("selling_points") or []
    text = render_inline(value)
    return text[:180] + "..." if len(text) > 180 else text


def render_drama_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        f"# {analysis.get('title')}",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
        f"| 排名 | {md_escape(analysis.get('rank'))} |",
        f"| 类型 | {md_escape(analysis.get('category'))} |",
        f"| 榜单页签 | {md_escape(analysis.get('tab'))} |",
        f"| 剧集数 | {md_escape(analysis.get('episode_count'))} |",
        f"| 详情链接 | {md_escape(analysis.get('detail_url'))} |",
        "",
        "## 剧情摘要",
        "",
        md_escape(analysis.get("description")) or "_暂无摘要_",
        "",
        "## LLM 投放分析",
        "",
    ]
    llm = analysis.get("llm_analysis") if isinstance(analysis.get("llm_analysis"), dict) else {}
    if llm:
        for key in REPORT_KEYS:
            lines.extend([f"### {key}", ""])
            lines.extend(render_markdown_value(llm.get(key), level=4))
    elif analysis.get("llm_error"):
        lines.append(f"> LLM 分析失败：{md_escape(analysis.get('llm_error'))}")
    else:
        lines.append("_未启用 LLM 分析_")

    lines.extend(["", "## Top 评论", ""])
    comments = analysis.get("comments") or []
    if comments:
        lines.extend(f"- {md_escape(comment)}" for comment in comments[:10])
    else:
        lines.append("_暂无评论数据_")
    return "\n".join(lines).rstrip() + "\n"


def render_overview(results: list[dict[str, Any]], rank_path: Path, overview_llm: dict[str, Any] | None = None) -> str:
    lines = [
        "# 抖音榜单剧目分析总览",
        "",
        f"- 来源文件：{rank_path}",
        f"- 分析剧目数：{len(results)}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if overview_llm:
        lines.extend(["## LLM 总览洞察", ""])
        for key, value in overview_llm.items():
            if key == "provider":
                continue
            lines.extend([f"### {md_escape(key)}", ""])
            lines.extend(render_markdown_value(value, level=4))

    lines.extend(["## 热剧概览", ""])
    lines.extend(markdown_table(["排名", "剧名", "类型", "榜单页签", "标签", "共性核心卖点"], [
        [
            item["analysis"].get("rank"),
            item["analysis"].get("title"),
            item["analysis"].get("category"),
            item["analysis"].get("tab"),
            "、".join(item["analysis"].get("plot_tags") or []),
            analysis_core_selling_points(item["analysis"]),
        ]
        for item in results
    ]))
    return "\n".join(lines).rstrip() + "\n"


def _overview_group_key(item: dict[str, Any]) -> tuple[str, str]:
    analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
    return text_value(analysis.get("category")) or "未知类型", text_value(analysis.get("tab")) or "未知页签"


def _overview_grouped_results(results: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in results:
        grouped.setdefault(_overview_group_key(item), []).append(item)
    return [(category, tab, items) for (category, tab), items in grouped.items()]


def _overview_table_rows(items: list[dict[str, Any]], start_index: int) -> list[list[Any]]:
    rows = []
    for offset, item in enumerate(items):
        analysis = item["analysis"]
        rows.append(
            [
                start_index + offset,
                analysis.get("rank"),
                analysis.get("title"),
                analysis.get("category"),
                analysis.get("tab"),
                "、".join(analysis.get("plot_tags") or []),
                analysis_core_selling_points(analysis),
            ]
        )
    return rows


def render_overview(results: list[dict[str, Any]], rank_path: Path, overview_llm: dict[str, Any] | None = None) -> str:
    lines = [
        "# 抖音榜单剧目分析总览",
        "",
        f"- 来源文件：{rank_path}",
        f"- 分析剧目数：{len(results)}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if overview_llm:
        lines.extend(["## LLM 总览洞察", ""])
        for key, value in overview_llm.items():
            if key == "provider":
                continue
            lines.extend([f"### {md_escape(key)}", ""])
            lines.extend(render_markdown_value(value, level=4))

    lines.extend(["## 热剧概览", ""])
    if not results:
        lines.append("_暂无可分析剧目_")
        return "\n".join(lines).rstrip() + "\n"

    next_index = 1
    for category, tab, items in _overview_grouped_results(results):
        lines.extend([f"### {category} / {tab}", ""])
        lines.extend(
            markdown_table(
                ["分析序号", "原始排名", "剧名", "类型", "榜单页签", "标签", "共性核心卖点"],
                _overview_table_rows(items, next_index),
            )
        )
        lines.append("")
        next_index += len(items)
    return "\n".join(lines).rstrip() + "\n"


async def build_overview_llm(results: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    payload = {
        "source": "douyin_rank_overview",
        "items": [
            {
                "title": item["analysis"].get("title"),
                "category": item["analysis"].get("category"),
                "tab": item["analysis"].get("tab"),
                "plot_tags": item["analysis"].get("plot_tags"),
                "stats": item["analysis"].get("stats"),
                "comments": item["analysis"].get("comments"),
                "llm_analysis": item["analysis"].get("llm_analysis"),
            }
            for item in results
        ],
    }
    return await call_llm_json(payload, overview=True)


async def analyze_rank_file_async(
    rank_json_path: str | Path,
    output_dir: str | Path | None = None,
    options: AnalyzeOptions | None = None,
) -> dict[str, Any]:
    options = options or AnalyzeOptions()
    rank_path = Path(rank_json_path)
    payload = load_rank_payload(rank_path)
    target_dir = Path(output_dir) if output_dir else default_output_dir(rank_path, payload)
    target_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for index, record in enumerate(select_records(payload.get("records", []), options), start=1):
        analysis = heuristic_analysis(record)
        if options.with_llm:
            await apply_llm_analysis(analysis)

        base_name = f"{index:02d}_{safe_filename(analysis.get('title'))}"
        json_path = target_dir / f"{base_name}.json"
        md_path = target_dir / f"{base_name}.md"
        result_payload = {"rank_file": str(rank_path), "analysis": analysis}
        json_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(render_drama_markdown(analysis), encoding="utf-8")
        results.append({"json": str(json_path), "markdown": str(md_path), "analysis": analysis})

    overview_llm = {}
    overview_error = ""
    if options.with_llm and results:
        overview_llm, overview_error = await build_overview_llm(results)

    overview_path = target_dir / "douyin_analysis_overview.md"
    overview_path.write_text(render_overview(results, rank_path, overview_llm), encoding="utf-8")
    overview_json_path = target_dir / "douyin_llm_overview.json"
    overview_json_path.write_text(
        json.dumps({"analysis": overview_llm, "error": overview_error}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_path = target_dir / "douyin_analysis_manifest.json"
    manifest = {
        "rank_file": str(rank_path),
        "output_dir": str(target_dir),
        "overview": str(overview_path),
        "overview_json": str(overview_json_path),
        "overview_llm_error": overview_error,
        "items": results,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result.get("value")


def analyze_rank_file(
    rank_json_path: str | Path,
    output_dir: str | Path | None = None,
    options: AnalyzeOptions | None = None,
) -> dict[str, Any]:
    return _run_async(analyze_rank_file_async(rank_json_path, output_dir=output_dir, options=options))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Douyin rank dramas and generate LLM reports.")
    parser.add_argument("rank_json", help="Douyin rank JSON path")
    parser.add_argument("--output-dir", default="", help="Output analysis directory")
    parser.add_argument("--top-n", type=int, default=10, help="Top dramas per category")
    parser.add_argument("--no-per-category", action="store_true", help="Analyze global TopN instead of TopN per category")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM analysis")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = analyze_rank_file(
        args.rank_json,
        output_dir=args.output_dir or None,
        options=AnalyzeOptions(
            top_n=args.top_n,
            per_category=not args.no_per_category,
            with_llm=not args.no_llm,
        ),
    )
    print(manifest["manifest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
