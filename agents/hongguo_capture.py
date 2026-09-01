# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from agents.drama_download_policy import paid_content_reason
from agents.spade_cenc import derive_content_key


_CAPTURE_VID_PATTERN = re.compile(r"__capture_vid_([A-Za-z0-9.-]+)$")


def _capture_vid_from_path(path: Path) -> str:
    match = _CAPTURE_VID_PATTERN.search(path.stem)
    return match.group(1) if match else ""


@lru_cache(maxsize=512)
def _has_valid_spade(value: str) -> bool:
    try:
        derive_content_key(value)
    except (TypeError, ValueError):
        return False
    return True


def normalize_title(value: Any) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKC", str(value or "").strip()).casefold()
    return "".join(char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _walk_keyed_dicts(
    value: Any, parent_key: str = ""
) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield dictionaries with the mapping key that directly contains them."""

    if isinstance(value, dict):
        yield parent_key, value
        for key, child in value.items():
            yield from _walk_keyed_dicts(child, str(key))
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keyed_dicts(child)


@dataclass(frozen=True)
class CapturedVideoVariant:
    series_id: str
    series_title: str
    episode: int
    vid: str
    main_url: str
    backup_url: str = ""
    fallback_url: str = ""
    url_expire: int = 0
    definition: str = ""
    codec: str = ""
    height: int = 0
    size: int = 0
    encrypted: bool = False
    spade_a: str = field(default="", repr=False)
    encryption_method: str = ""
    source_file: str = ""

    @property
    def identity(self) -> tuple[str, str, str, int, str, str]:
        return (
            self.main_url,
            self.backup_url,
            self.fallback_url,
            self.url_expire,
            self.spade_a,
            self.source_file,
        )

    @property
    def expired(self) -> bool:
        return bool(self.url_expire and self.url_expire <= int(time.time()))

    @property
    def decryptable(self) -> bool:
        method = self.encryption_method.lower()
        return bool(
            self.spade_a
            and (not method or "aes" in method or "cenc" in method)
            and _has_valid_spade(self.spade_a)
        )

    def public_metadata(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "series_title": self.series_title,
            "episode": self.episode,
            "vid": self.vid,
            "definition": self.definition,
            "codec": self.codec,
            "encrypted": self.encrypted,
            "source_file": Path(self.source_file).name,
        }


@dataclass(frozen=True)
class CapturedEpisode:
    episode: int
    video_id: str
    title: str
    is_paid: bool = False
    paid_reason: str = ""
    capture_status: str = "waiting_capture"

    def public_dict(self) -> dict[str, Any]:
        return {
            "episode": self.episode,
            "video_id": self.video_id,
            "title": self.title,
            "is_paid": self.is_paid,
            "paid_reason": self.paid_reason,
            "capture_status": self.capture_status,
        }


@dataclass(frozen=True)
class CapturedSeries:
    series_id: str
    title: str
    episodes: tuple[CapturedEpisode, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "book_id": self.series_id,
            "title": self.title,
            "cover": "",
            "summary": "",
            "episode_count": len(self.episodes),
            "source_platform": "hongguo_capture",
        }


class HongguoCaptureCatalog:
    """Incremental reader for capture_video_urls.py raw JSON output."""

    def __init__(self, capture_root: str | Path) -> None:
        self.capture_root = Path(capture_root).expanduser()
        self.raw_dir = self.capture_root / "raw"
        self._files: dict[Path, tuple[tuple[int, int], Any]] = {}
        self._metadata_files: dict[Path, tuple[tuple[int, int], Any]] = {}
        self._metadata_loaded = False
        self._series: dict[str, CapturedSeries] = {}
        self._variants: dict[tuple[str, int], list[CapturedVideoVariant]] = {}
        self._lock = RLock()

    def validate_root(self) -> None:
        if not self.capture_root.is_absolute():
            raise ValueError("抓包根目录必须是绝对路径")
        if not self.capture_root.exists() or not self.capture_root.is_dir():
            raise FileNotFoundError(f"抓包根目录不存在：{self.capture_root}")
        if not self.raw_dir.exists() or not self.raw_dir.is_dir():
            raise FileNotFoundError(f"抓包原始数据目录不存在：{self.raw_dir}")
        if not os.access(self.raw_dir, os.R_OK):
            raise PermissionError(f"抓包原始数据目录不可读：{self.raw_dir}")
        try:
            next(self.raw_dir.iterdir(), None)
        except OSError as exc:
            raise PermissionError(f"抓包原始数据目录不可读：{self.raw_dir}") from exc

    def refresh(self) -> None:
        self.validate_root()
        with self._lock:
            paths = set(self.raw_dir.glob("*.json")) if self.raw_dir.is_dir() else set()
            if self._refresh_file_cache(self._files, paths):
                self._rebuild()

    def refresh_metadata(self) -> None:
        """Discover rank/detail captures stored beside video_urls."""

        self.validate_root()
        capture_base = self.capture_root.parent
        paths: set[Path] = set()
        if capture_base.is_dir():
            for directory in capture_base.glob("mitm_responses_*"):
                if directory.is_dir():
                    paths.update(directory.glob("*.json"))
        with self._lock:
            changed = self._refresh_file_cache(self._metadata_files, paths)
            self._metadata_loaded = True
            if changed:
                self._rebuild()

    @staticmethod
    def _refresh_file_cache(
        cache: dict[Path, tuple[tuple[int, int], Any]], paths: set[Path]
    ) -> bool:
        changed = False
        for removed in set(cache) - paths:
            cache.pop(removed, None)
            changed = True
        for path in sorted(paths):
            try:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
            cached = cache.get(path)
            if cached and cached[0] == signature:
                continue
            try:
                payload = json.loads(
                    path.read_text(encoding="utf-8-sig", errors="replace")
                )
            except (OSError, json.JSONDecodeError, ValueError):
                # Keep the previous complete payload, if any. Its old
                # signature guarantees this file is retried next refresh.
                continue
            cache[path] = (signature, payload)
            changed = True
        return changed

    def _rebuild(self) -> None:
        episode_rows: dict[str, tuple[str, list[CapturedEpisode]]] = {}
        vid_index: dict[str, tuple[str, str, int]] = {}
        model_rows: list[tuple[Path, str, dict[str, Any]]] = []
        series_sources = sorted(
            [*self._metadata_files.items(), *self._files.items()],
            key=lambda item: str(item[0]),
        )
        capture_paths = set(self._files)
        for path, (_signature, payload) in series_sources:
            for parent_key, obj in _walk_keyed_dicts(payload):
                series_id = str(obj.get("series_id") or "").strip()
                series_title = str(obj.get("series_title") or "").strip()
                video_list = obj.get("video_list")
                if series_id and series_title and isinstance(video_list, list):
                    episodes: list[CapturedEpisode] = []
                    for position, item in enumerate(video_list, start=1):
                        if not isinstance(item, dict):
                            continue
                        vid = str(item.get("vid") or item.get("video_id") or "").strip()
                        if not vid:
                            continue
                        try:
                            number = int(item.get("vid_index") or item.get("episode") or position)
                        except (TypeError, ValueError):
                            number = position
                        reason = paid_content_reason(item)
                        episode = CapturedEpisode(
                            episode=number,
                            video_id=vid,
                            title=str(item.get("title") or f"第 {number} 集").strip(),
                            is_paid=bool(reason),
                            paid_reason=reason,
                        )
                        episodes.append(episode)
                        vid_index[vid] = (series_id, series_title, number)
                    if episodes:
                        episode_rows[series_id] = (
                            series_title,
                            sorted(episodes, key=lambda item: item.episode),
                        )
                if path in capture_paths and isinstance(obj.get("video_model"), str):
                    model_rows.append((path, parent_key, obj))

        variants: dict[tuple[str, int], list[CapturedVideoVariant]] = {}
        for path, parent_key, wrapper in model_rows:
            try:
                model = json.loads(str(wrapper["video_model"]))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if not isinstance(model, dict):
                continue
            fallback = model.get("fallback_api")
            fallback_url = ""
            if isinstance(fallback, dict):
                value = fallback.get("fallback_api")
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    fallback_url = value
            model_expire = model.get("url_expire")
            # capture_video_urls.py stores each wrapper below data.{vid}. Some
            # fixtures instead expose a model-level video_id, so retain that as
            # a compatibility fallback without rescanning every cached file.
            captured_vid = _capture_vid_from_path(path)
            vid = (
                parent_key
                if parent_key in vid_index
                else captured_vid
                if captured_vid in vid_index
                else str(model.get("video_id") or "").strip()
            )
            relation = vid_index.get(vid)
            if relation is None:
                continue
            series_id, series_title, episode_number = relation
            for candidate in model.get("video_list") or []:
                if not isinstance(candidate, dict):
                    continue
                main_url = str(candidate.get("main_url") or "").strip()
                if not main_url.startswith(("http://", "https://")):
                    continue
                meta = candidate.get("video_meta") if isinstance(candidate.get("video_meta"), dict) else {}
                encrypt = candidate.get("encrypt_info") if isinstance(candidate.get("encrypt_info"), dict) else {}
                try:
                    expiry = int(model_expire or 0)
                except (TypeError, ValueError):
                    expiry = 0
                row = CapturedVideoVariant(
                    series_id=series_id,
                    series_title=series_title,
                    episode=episode_number,
                    vid=vid,
                    main_url=main_url,
                    backup_url=str(candidate.get("backup_url") or "").strip(),
                    fallback_url=fallback_url,
                    url_expire=expiry,
                    definition=str(meta.get("definition") or "").strip(),
                    codec=str(meta.get("codec_type") or "").strip(),
                    height=_int_value(meta.get("vheight")),
                    size=_int_value(meta.get("size")),
                    encrypted=encrypt.get("encrypt") is True
                    or encrypt.get("encrypt") == 1
                    or str(encrypt.get("encrypt") or "").strip().lower() == "true",
                    spade_a=str(encrypt.get("spade_a") or "").strip(),
                    encryption_method=str(encrypt.get("encryption_method") or "").strip(),
                    source_file=str(path),
                )
                variants.setdefault((series_id, episode_number), []).append(row)

        series: dict[str, CapturedSeries] = {}
        for series_id, (title, rows) in episode_rows.items():
            public_rows: list[CapturedEpisode] = []
            for episode in rows:
                status = self._status_for_variants(
                    variants.get((series_id, episode.episode), []), episode.is_paid
                )
                public_rows.append(
                    CapturedEpisode(
                        episode=episode.episode,
                        video_id=episode.video_id,
                        title=episode.title,
                        is_paid=episode.is_paid,
                        paid_reason=episode.paid_reason,
                        capture_status=status,
                    )
                )
            series[series_id] = CapturedSeries(series_id, title, tuple(public_rows))
        self._series = series
        self._variants = variants

    @staticmethod
    def _status_for_variants(rows: list[CapturedVideoVariant], paid: bool) -> str:
        if paid:
            return "paid"
        current = [row for row in rows if not row.expired and (not row.encrypted or row.decryptable)]
        if any(not row.encrypted for row in current):
            return "ready_clear"
        if current:
            return "ready_encrypted"
        if rows and all(row.expired for row in rows):
            return "expired"
        return "waiting_capture"

    @staticmethod
    def _variant_score(row: CapturedVideoVariant) -> tuple[int, int, int, int]:
        if row.expired or (row.encrypted and not row.decryptable):
            return (-1, 0, 0, 0)
        clear_score = 2 if not row.encrypted else 1
        codec_score = int(row.codec.lower() in {"h264", "avc", "avc1"})
        return clear_score, codec_score, row.height, row.size

    def search_exact(self, title: str) -> list[CapturedSeries]:
        self.refresh()
        wanted = normalize_title(title)
        matches = self._exact_matches(wanted)
        if not matches:
            self.refresh_metadata()
            matches = self._exact_matches(wanted)
        return self._deduplicate_titles(matches)

    def _exact_matches(self, wanted: str) -> list[CapturedSeries]:
        return [
            item
            for item in sorted(self._series.values(), key=lambda value: value.title)
            if normalize_title(item.title) == wanted
        ]

    def search_compatible(self, title: str, limit: int = 8) -> list[CapturedSeries]:
        exact = self.search_exact(title)
        if exact:
            return exact[: max(0, int(limit))]
        wanted = normalize_title(title)
        if len(wanted) < 4:
            return []
        matches = [
            item
            for item in sorted(
                self._series.values(),
                key=lambda value: len(normalize_title(value.title)),
                reverse=True,
            )
            if len(normalize_title(item.title)) >= 4
            and (
                wanted in normalize_title(item.title)
                or normalize_title(item.title) in wanted
            )
        ]
        return self._deduplicate_titles(matches)[: max(0, int(limit))]

    def suggest(self, title: str, limit: int = 8) -> list[CapturedSeries]:
        self.refresh()
        self.refresh_metadata()
        wanted = normalize_title(title)
        if not wanted:
            return []
        matches = [
            item
            for item in sorted(self._series.values(), key=lambda value: value.title)
            if wanted in normalize_title(item.title)
        ]
        return self._deduplicate_titles(matches)[: max(0, int(limit))]

    @staticmethod
    def _deduplicate_titles(rows: list[CapturedSeries]) -> list[CapturedSeries]:
        selected: dict[str, CapturedSeries] = {}
        for item in rows:
            key = normalize_title(item.title)
            current = selected.get(key)
            if current is None or len(item.episodes) > len(current.episodes):
                selected[key] = item
        return list(selected.values())

    def episodes(self, series_id: str) -> list[CapturedEpisode]:
        self.refresh()
        key = str(series_id).strip()
        if not self._metadata_loaded or key not in self._series:
            self.refresh_metadata()
        series = self._series.get(key)
        return list(series.episodes) if series else []

    def series_title(self, series_id: str) -> str:
        self.refresh()
        key = str(series_id).strip()
        if not self._metadata_loaded or key not in self._series:
            self.refresh_metadata()
        series = self._series.get(key)
        return series.title if series else ""

    def episode(self, series_id: str, episode: int) -> CapturedEpisode | None:
        number = int(episode)
        return next(
            (item for item in self.episodes(series_id) if item.episode == number),
            None,
        )

    def best_variant(
        self,
        series_id: str,
        episode: int,
        excluded: set[tuple[str, str, str, int, str, str]] | None = None,
    ) -> CapturedVideoVariant | None:
        self.refresh()
        rows = self._variants.get((str(series_id).strip(), int(episode)), [])
        excluded = excluded or set()
        usable = [
            row
            for row in rows
            if self._variant_score(row)[0] >= 0 and row.identity not in excluded
        ]
        return max(usable, key=self._variant_score) if usable else None

    def variant_identities(
        self, series_id: str, episode: int
    ) -> set[tuple[str, str, str, int, str, str]]:
        """Snapshot every variant visible before an active capture request."""

        self.refresh()
        rows = self._variants.get((str(series_id).strip(), int(episode)), [])
        return {row.identity for row in rows}

    def stats(self) -> dict[str, Any]:
        self.refresh()
        files = list(self.raw_dir.glob("*.json")) if self.raw_dir.is_dir() else []
        mtimes: list[float] = []
        for path in files:
            try:
                mtimes.append(path.stat().st_mtime)
            except OSError:
                continue
        newest = max(mtimes, default=0)
        playable = sum(
            episode.capture_status in {"ready_clear", "ready_encrypted"}
            for series in self._series.values()
            for episode in series.episodes
        )
        return {
            "raw_file_count": len(files),
            "series_count": len(self._series),
            "playable_episode_count": playable,
            "newest_capture_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(newest)) if newest else "",
        }
