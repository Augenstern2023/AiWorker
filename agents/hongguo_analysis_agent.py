# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis.llm import LLMError, create_llm_provider_from_config


@dataclass
class AnalyzeOptions:
    top_n: int = 10
    episode_limit: int = 1
    fetch_details: bool = False
    fetch_play_urls: bool = False
    download_media: bool = False
    with_asr: bool = False
    with_llm: bool = False
    keep_video: bool = True
    media_limit_mb: int = 300
    code: str = ""
    timeout: float = 20.0
    retries: int = 2
    backoff: float = 1.0


def text_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def md_escape(value: Any) -> str:
    return text_value(value).replace("|", "\\|").replace("\n", "<br>")


def safe_filename(value: Any, max_len: int = 80) -> str:
    text = re.sub(r"\s+", "_", text_value(value))
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip("._ ")
    return (text or "untitled")[:max_len]


def render_inline(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(text_value(item) for item in value if text_value(item))
    if isinstance(value, dict):
        return "；".join(f"{text_value(key)}：{render_inline(item)}" for key, item in value.items() if render_inline(item))
    return text_value(value)


def render_markdown_value(value: Any, level: int = 3) -> list[str]:
    if isinstance(value, list):
        return [f"- {md_escape(render_inline(item))}" for item in value] + [""]
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            lines.extend([f"{'#' * min(level, 6)} {md_escape(key)}", ""])
            lines.extend(render_markdown_value(item, level=level + 1))
        return lines
    return [md_escape(value) or "_暂无_", ""]


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(md_escape(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(md_escape(render_inline(value)) for value in row) + " |")
    return lines


async def call_llm_json(payload: dict[str, Any], overview: bool = False) -> tuple[dict[str, Any], str]:
    try:
        provider = create_llm_provider_from_config()
        prompt = json.dumps(payload, ensure_ascii=False)
        result = await provider.generate_json(prompt)
        return result if isinstance(result, dict) else {}, ""
    except (LLMError, Exception) as exc:
        return {}, str(exc)


def load_rank_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Hongguo rank JSON must be an object")
    if not isinstance(payload.get("records"), list):
        raise ValueError("Hongguo rank JSON has no records list")
    return payload


def infer_job_id(rank_path: Path, payload: dict[str, Any]) -> str:
    return text_value(payload.get("job_id")) or "manual"


def default_output_dir(rank_path: Path, payload: dict[str, Any]) -> Path:
    return Path("data") / "hg" / "jobs" / infer_job_id(rank_path, payload) / "analysis"


def fetch_play_url(record: dict[str, Any], episode: dict[str, Any], options: AnalyzeOptions) -> tuple[str, str]:
    return "", "红果第三方接口已移除，无法获取播放地址"


def _rank(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("rank") if isinstance(record.get("rank"), dict) else record


def _details(record: dict[str, Any]) -> list[dict[str, Any]]:
    details = record.get("details")
    return [item for item in details if isinstance(item, dict)] if isinstance(details, list) else []


def _title(record: dict[str, Any]) -> str:
    rank = _rank(record)
    return text_value(rank.get("play_name") or record.get("剧名") or record.get("title")) or "未命名"


def _analysis_from_record(record: dict[str, Any], index: int, options: AnalyzeOptions) -> dict[str, Any]:
    rank = _rank(record)
    details = _details(record)[: max(1, options.episode_limit)]
    tags = text_value(record.get("标签") or rank.get("category") or record.get("榜单类型"))
    plot_tags = [part for part in re.split(r"[·,，/|、\s]+", tags) if part][:5]
    media_candidates = [
        text_value(item.get("play_url") or item.get("video_url") or item.get("url"))
        for item in details
        if text_value(item.get("play_url") or item.get("video_url") or item.get("url"))
    ]
    subtitle_candidates = [
        text_value(item.get("subtitle_url") or item.get("caption_url") or item.get("srt_url") or item.get("vtt_url"))
        for item in details
        if text_value(item.get("subtitle_url") or item.get("caption_url") or item.get("srt_url") or item.get("vtt_url"))
    ]
    return {
        "rank": rank.get("rowNum") or record.get("排名") or index,
        "title": _title(record),
        "book_id": text_value(rank.get("book_id")),
        "play_type": text_value(rank.get("play_type")),
        "library_source": text_value(rank.get("library_source") or "csv"),
        "category": tags,
        "episode_count": text_value(rank.get("episode_count")),
        "summary": text_value(rank.get("desc") or record.get("简介") or record.get("description")),
        "plot_tags": plot_tags,
        "selling_points": [text_value(rank.get("desc") or record.get("简介"))] if text_value(rank.get("desc") or record.get("简介")) else [],
        "episodes_analyzed": details,
        "media_candidates": media_candidates,
        "subtitle_candidates": subtitle_candidates,
        "downloaded_media": [],
        "transcript_source": "rank_text",
        "transcript": text_value(rank.get("desc") or record.get("简介") or record.get("热评")),
        "llm_analysis": {},
        "llm_error": "",
    }


def render_drama_markdown(analysis: dict[str, Any], fetch_error: str = "") -> str:
    lines = [f"# {md_escape(analysis.get('title'))}", ""]
    lines.extend(
        markdown_table(
            ["字段", "值"],
            [
                ["排名", analysis.get("rank")],
                ["类型", analysis.get("category")],
                ["来源", analysis.get("library_source")],
                ["题材", " / ".join(analysis.get("plot_tags") or [])],
            ],
        )
    )
    lines.extend(["", "## 简介", "", md_escape(analysis.get("summary")) or "_暂无_", ""])
    if fetch_error:
        lines.extend(["## 补拉提示", "", md_escape(fetch_error), ""])
    return "\n".join(lines)


def render_overview(results: list[dict[str, Any]], rank_path: Path, overview_llm: dict[str, Any] | None = None) -> str:
    rows = [
        [
            item["analysis"].get("rank"),
            item["analysis"].get("title"),
            item["analysis"].get("category"),
            " / ".join(item["analysis"].get("plot_tags") or []),
        ]
        for item in results
    ]
    lines = ["# 红果榜单剧目分析总览", "", f"- 榜单文件：{rank_path}", ""]
    lines.extend(markdown_table(["排名", "剧名", "类型", "标签"], rows))
    if overview_llm:
        lines.extend(["", "## LLM 总结", ""])
        lines.extend(render_markdown_value(overview_llm, level=3))
    return "\n".join(lines)


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

    results: list[dict[str, Any]] = []
    for index, record in enumerate(payload.get("records", [])[: max(1, options.top_n)], start=1):
        if not isinstance(record, dict):
            continue
        analysis = _analysis_from_record(record, index, options)
        if options.fetch_details:
            analysis["fetch_error"] = "红果第三方接口已移除，跳过补拉"
        if options.with_llm:
            analysis["llm_analysis"], analysis["llm_error"] = await call_llm_json({"analysis": analysis})
        base_name = f"{index:02d}_{safe_filename(analysis.get('title'))}"
        json_path = target_dir / f"{base_name}.json"
        md_path = target_dir / f"{base_name}.md"
        result_payload = {"rank_file": str(rank_path), "analysis": analysis, "fetch_error": analysis.get("fetch_error", "")}
        json_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(render_drama_markdown(analysis, analysis.get("fetch_error", "")), encoding="utf-8")
        results.append({"json": str(json_path), "markdown": str(md_path), "analysis": analysis})

    overview_llm: dict[str, Any] = {}
    overview_llm_error = ""
    if options.with_llm and results:
        overview_llm, overview_llm_error = await call_llm_json({"items": [item["analysis"] for item in results]}, overview=True)

    overview_path = target_dir / "hongguo_analysis_overview.md"
    overview_json_path = target_dir / "hongguo_llm_overview.json"
    manifest_path = target_dir / "hongguo_analysis_manifest.json"
    overview_path.write_text(render_overview(results, rank_path, overview_llm), encoding="utf-8")
    overview_json_path.write_text(json.dumps({"analysis": overview_llm, "error": overview_llm_error}, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "rank_file": str(rank_path),
        "output_dir": str(target_dir),
        "overview": str(overview_path),
        "overview_json": str(overview_json_path),
        "overview_llm_error": overview_llm_error,
        "items": results,
        "manifest": str(manifest_path),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def analyze_rank_file(
    rank_json_path: str | Path,
    output_dir: str | Path | None = None,
    options: AnalyzeOptions | None = None,
) -> dict[str, Any]:
    return asyncio.run(analyze_rank_file_async(rank_json_path, output_dir=output_dir, options=options))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Hongguo CSV-imported rank dramas.")
    parser.add_argument("rank_json")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--with-llm", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    analyze_rank_file(args.rank_json, output_dir=args.output_dir or None, options=AnalyzeOptions(top_n=args.top_n, with_llm=args.with_llm))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
