# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


PLATFORM_NAMES = {"dy": "抖音", "hg": "红果"}
PART_KEYS = {"dy": "douyin-rank", "hg": "hongguo-rank"}
DEFAULT_STATE_DIR = Path(".runtime") / "zentao-rank-delivery"


@dataclass(frozen=True)
class RankDeliveryConfig:
    endpoint: str
    token: str
    gid: str
    sender_name: str
    state_dir: Path
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> "RankDeliveryConfig":
        return cls(
            endpoint=os.environ.get("ZENTAO_PUSH_ENDPOINT", "").strip(),
            token=os.environ.get("ZENTAO_PUSH_TOKEN", ""),
            gid=os.environ.get("ZENTAO_PUSH_GID", "").strip(),
            sender_name=os.environ.get("ZENTAO_RANK_SENDER_NAME", "AiWorker 榜单助手").strip(),
            state_dir=Path(os.environ.get("ZENTAO_RANK_STATE_DIR", str(DEFAULT_STATE_DIR))),
            timeout_seconds=float(os.environ.get("ZENTAO_PUSH_TIMEOUT_SECONDS", "30")),
        )

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.token and self.gid)


@dataclass(frozen=True)
class RankMessage:
    platform: str
    part_key: str
    report_date: str
    title: str
    content: str
    source_path: Path
    source_sha256: str
    record_count: int
    group_count: int


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _date_from_text(value: str) -> str:
    match = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", value)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else ""


def _read_records(platform: str, source_path: Path) -> tuple[list[dict[str, Any]], str]:
    if platform == "hg":
        with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
            records = [dict(row) for row in csv.DictReader(handle) if isinstance(row, dict)]
        return records, _date_from_text(str(source_path))

    payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"抖音榜单 JSON 缺少 records 数组：{source_path}")
    captured_at = _clean(payload.get("captured_at")) if isinstance(payload, dict) else ""
    return [dict(item) for item in records if isinstance(item, dict)], _date_from_text(captured_at)


def _group_and_title(platform: str, record: dict[str, Any]) -> tuple[str, str, int]:
    if platform == "hg":
        group = _clean(record.get("榜单类型") or record.get("ranking_type") or record.get("rank_type")) or "未分类榜单"
        title = _clean(record.get("剧名") or record.get("title") or record.get("play_name"))
        rank_value = record.get("排名") or record.get("rank") or 0
    else:
        category = _clean(record.get("rank_category") or record.get("category"))
        tab = _clean(record.get("tab"))
        group = " / ".join(item for item in (category, tab) if item) or "抖音榜单"
        title = _clean(record.get("title"))
        rank_value = record.get("rank") or 0
    try:
        rank = int(float(rank_value))
    except (TypeError, ValueError):
        rank = 0
    return group, title, rank


def build_rank_message(platform: str, source_path: str | Path, report_date: str = "") -> RankMessage:
    platform = platform.strip().lower()
    if platform not in PLATFORM_NAMES:
        raise ValueError("platform must be dy or hg")
    source = Path(source_path).resolve()
    records, source_date = _read_records(platform, source)
    date = report_date.strip() or source_date or _date_from_text(source.name)
    if not date:
        date = datetime.fromtimestamp(source.stat().st_mtime).date().isoformat()

    grouped: OrderedDict[str, list[tuple[int, str]]] = OrderedDict()
    for record in records:
        group, title, rank = _group_and_title(platform, record)
        if title:
            grouped.setdefault(group, []).append((rank, title))
    if not grouped:
        raise ValueError(f"榜单没有可推送的非空剧名：{source}")

    lines = [
        f"# {PLATFORM_NAMES[platform]}榜单更新（{date}）",
        "",
        f"> 更新完成：共 {len(records)} 条、{len(grouped)} 个榜单。",
        "",
    ]
    for group, items in grouped.items():
        ordered = sorted(enumerate(items), key=lambda item: (item[1][0] <= 0, item[1][0], item[0]))
        titles = [title for _, (_, title) in ordered]
        lines.append(f"- {group}：{'、'.join(titles[:10])}")
    lines.extend(["", f"数据源：`{source}`"])
    source_bytes = source.read_bytes()
    return RankMessage(
        platform=platform,
        part_key=PART_KEYS[platform],
        report_date=date,
        title=f"{PLATFORM_NAMES[platform]}榜单更新（{date}）",
        content="\n".join(lines),
        source_path=source,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        record_count=len(records),
        group_count=len(grouped),
    )


def post_json(endpoint: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status_code = int(response.status)
            response_body = response.read().decode("utf-8", errors="replace").strip()
    except HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"禅道接口返回 HTTP {exc.code}：{response_body[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"连接禅道接口失败：{exc.reason}") from exc

    success = response_body.lower() == "success"
    if not success:
        try:
            parsed = json.loads(response_body)
            success = _clean(parsed.get("result")).lower() == "success"
        except (json.JSONDecodeError, AttributeError):
            success = False
    if not success:
        raise RuntimeError(f"禅道接口未返回 success：{response_body[:500]}")
    return {"status_code": status_code, "body": response_body}


def _delivery_key(config: RankDeliveryConfig, message: RankMessage) -> str:
    identity = "\n".join(
        (config.endpoint, config.gid, message.part_key, message.report_date)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def deliver_rank_update(
    platform: str,
    source_path: str | Path,
    *,
    config: RankDeliveryConfig | None = None,
    report_date: str = "",
    post_json: Callable[[str, dict[str, Any], float], dict[str, Any]] = post_json,
) -> dict[str, Any]:
    platform = platform.strip().lower()
    part_key = PART_KEYS.get(platform, f"{platform}-rank")
    settings = config or RankDeliveryConfig.from_environment()
    if not settings.configured:
        return {"status": "skipped", "reason": "not-configured", "part_key": part_key}

    message = build_rank_message(platform, source_path, report_date=report_date)
    key = _delivery_key(settings, message)
    sent_path = settings.state_dir / f"{key}.sent.json"
    claim_path = settings.state_dir / f"{key}.claim"
    if sent_path.exists():
        return {"status": "skipped", "reason": "already-sent", "part_key": message.part_key}

    settings.state_dir.mkdir(parents=True, exist_ok=True)
    try:
        claim_fd = os.open(claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return {
            "status": "skipped",
            "reason": "delivery-in-progress-or-uncertain",
            "part_key": message.part_key,
        }
    with os.fdopen(claim_fd, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "key": key,
                "part_key": message.part_key,
                "report_date": message.report_date,
                "source_path": str(message.source_path),
                "claimed_at": datetime.now().isoformat(timespec="seconds"),
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )

    payload = {
        "token": settings.token,
        "gid": settings.gid,
        "title": message.title,
        "senderName": settings.sender_name,
        "content": message.content,
        "partKey": message.part_key,
    }
    response = post_json(settings.endpoint, payload, settings.timeout_seconds)
    record = {
        "status": "sent",
        "part_key": message.part_key,
        "report_date": message.report_date,
        "title": message.title,
        "source_path": str(message.source_path),
        "source_sha256": message.source_sha256,
        "record_count": message.record_count,
        "group_count": message.group_count,
        "endpoint": settings.endpoint,
        "gid": settings.gid,
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        "response": response,
    }
    _write_json_atomic(sent_path, record)
    return record
