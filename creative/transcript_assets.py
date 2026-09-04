# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


def normalize_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in text if char.isalnum())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _script_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    marker = re.search(r"^##\s*转写(?:内容|全文)?\s*$", text, flags=re.MULTILINE)
    return text[marker.end():].strip() if marker else text


def _transcript_text(path: Path) -> tuple[str, str]:
    payload = _read_json(path)
    return str(payload.get("text") or "").strip(), str(payload.get("source") or "transcript").strip()


def _episode_number(payload: dict[str, Any], fallback: int = 0) -> int:
    episode = payload.get("episode")
    if isinstance(episode, dict):
        episode = episode.get("episode") or episode.get("episode_number")
    try:
        return int(episode or payload.get("episode_number") or fallback)
    except (TypeError, ValueError):
        return fallback


def _asset_from_episode_dir(episode_dir: Path, episode_number: int) -> dict[str, Any] | None:
    script_path = episode_dir / "script.md"
    transcript_path = episode_dir / "transcript.json"
    text = _script_text(script_path) if script_path.exists() else ""
    source = "script"
    if not text and transcript_path.exists():
        text, source = _transcript_text(transcript_path)
    if not text:
        for candidate in sorted(episode_dir.glob("*transcript*.txt"), reverse=True):
            try:
                text = candidate.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                transcript_path = candidate
                source = "text"
                break
    if not text:
        return None
    existing_paths = [path for path in (script_path, transcript_path) if path.exists()]
    modified_at = max((path.stat().st_mtime for path in existing_paths), default=0.0)
    return {
        "episode_number": max(1, int(episode_number or 1)),
        "text": text,
        "transcript_source": source,
        "script_path": str(script_path) if script_path.exists() else "",
        "transcript_path": str(transcript_path) if transcript_path.exists() else "",
        "modified_at": modified_at,
    }


def _resolve_manifest_path(root: Path, manifest_path: Path, value: Any) -> Path:
    candidate = Path(str(value or ""))
    if candidate.is_absolute():
        return candidate
    workspace_candidate = root / candidate
    return workspace_candidate if workspace_candidate.exists() else manifest_path.parent / candidate


def index_transcript_bundles(root: str | Path) -> dict[str, dict[str, Any]]:
    workspace = Path(root).resolve()
    data_root = workspace / "data"
    searched_paths: list[str] = []
    assets_by_title: dict[str, list[dict[str, Any]]] = {}
    display_titles: dict[str, str] = {}
    if not data_root.exists():
        return {}

    def ensure_title(title: Any) -> str:
        title_key = normalize_title(title)
        if title_key:
            display_titles.setdefault(title_key, str(title or ""))
            assets_by_title.setdefault(title_key, [])
        return title_key

    def add_asset(title: Any, asset: dict[str, Any]) -> None:
        title_key = ensure_title(title)
        if not title_key:
            return
        assets_by_title[title_key].append(asset)

    for source_path in data_root.glob("*/jobs/**/source.json"):
        searched_paths.append(str(source_path))
        payload = _read_json(source_path)
        ensure_title(payload.get("title"))
        asset = _asset_from_episode_dir(source_path.parent, _episode_number(payload, 1))
        if asset:
            asset["metadata_path"] = str(source_path)
            add_asset(payload.get("title"), asset)

    for manifest_path in data_root.glob("*/jobs/**/manifest.json"):
        searched_paths.append(str(manifest_path))
        payload = _read_json(manifest_path)
        title = payload.get("title")
        if not ensure_title(title):
            continue
        for index, item in enumerate(payload.get("items") or [], start=1):
            if not isinstance(item, dict) or item.get("status") not in {None, "succeeded"}:
                continue
            script_value = item.get("script_path")
            if not script_value:
                continue
            script_path = _resolve_manifest_path(workspace, manifest_path, script_value)
            text = _script_text(script_path) if script_path.exists() else ""
            if not text:
                continue
            add_asset(title, {
                "episode_number": _episode_number(item, int(item.get("index") or index)),
                "text": text,
                "transcript_source": "script",
                "script_path": str(script_path),
                "transcript_path": str(script_path.with_name("transcript.json")) if script_path.with_name("transcript.json").exists() else "",
                "metadata_path": str(manifest_path),
                "modified_at": script_path.stat().st_mtime,
            })

    bundles: dict[str, dict[str, Any]] = {}
    for title_key, assets in assets_by_title.items():
        latest_by_episode: dict[int, dict[str, Any]] = {}
        for asset in assets:
            episode_number = int(asset["episode_number"])
            current = latest_by_episode.get(episode_number)
            asset_order = (
                float(asset.get("modified_at") or 0),
                str(asset.get("script_path") or asset.get("transcript_path") or ""),
            )
            current_order = (
                float(current.get("modified_at") or 0),
                str(current.get("script_path") or current.get("transcript_path") or ""),
            ) if current else (-1.0, "")
            if current is None or asset_order >= current_order:
                latest_by_episode[episode_number] = asset
        bundles[title_key] = {
            "title": display_titles[title_key],
            "episodes": [latest_by_episode[number] for number in sorted(latest_by_episode)],
            "searched_paths": sorted(set(searched_paths)),
        }
    return bundles


def find_transcript_bundle(root: str | Path, title: str, platform: str = "") -> dict[str, Any]:
    title_key = normalize_title(title)
    bundle = index_transcript_bundles(root).get(title_key)
    if not bundle:
        return {"title": str(title or ""), "platform": platform, "episodes": [], "searched_paths": []}
    return {**bundle, "title": str(title or bundle["title"]), "platform": str(platform or "")}


def render_transcript_bundle_text(title: str, episodes: list[dict[str, Any]]) -> str:
    lines = [f"# {str(title or '').strip()} 字幕转写", ""]
    for item in sorted(episodes, key=lambda episode: int(episode.get("episode_number") or 0)):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        lines.extend([
            f"## 第 {int(item.get('episode_number') or 0)} 集",
            "",
            text,
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def transcript_bundle_filename(title: str, episodes: list[dict[str, Any]]) -> str:
    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(title or "")).strip(" .") or "剧目"
    numbers = sorted({int(item.get("episode_number") or 0) for item in episodes if int(item.get("episode_number") or 0) > 0})
    episode_range = f"第{numbers[0]}集" if len(numbers) == 1 else f"第{numbers[0]}集-第{numbers[-1]}集"
    return f"{safe_title}_转写_{episode_range}.txt"


def index_downloaded_video_assets(root: str | Path) -> dict[str, list[dict[str, Any]]]:
    workspace = Path(root).resolve()
    data_root = workspace / "data"
    assets_by_title: dict[str, dict[int, dict[str, Any]]] = {}
    if not data_root.exists():
        return {}
    for source_path in data_root.glob("*/jobs/**/source.json"):
        payload = _read_json(source_path)
        title_key = normalize_title(payload.get("title"))
        if not title_key:
            continue
        if _asset_from_episode_dir(source_path.parent, _episode_number(payload, 1)):
            continue
        videos = [
            path for path in source_path.parent.glob("video.*")
            if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".flv"}
        ]
        if not videos:
            continue
        video_path = max(videos, key=lambda path: (path.stat().st_mtime, str(path)))
        episode_number = max(1, _episode_number(payload, 1))
        asset = {
            "episode_number": episode_number,
            "video_path": str(video_path),
            "source_path": str(source_path),
            "modified_at": video_path.stat().st_mtime,
        }
        latest_by_episode = assets_by_title.setdefault(title_key, {})
        current = latest_by_episode.get(episode_number)
        if not current or (asset["modified_at"], asset["video_path"]) >= (
            current["modified_at"], current["video_path"],
        ):
            latest_by_episode[episode_number] = asset
    return {
        title_key: [items[number] for number in sorted(items)]
        for title_key, items in assets_by_title.items()
    }


def find_downloaded_video_assets(root: str | Path, title: str) -> list[dict[str, Any]]:
    return index_downloaded_video_assets(root).get(normalize_title(title), [])
