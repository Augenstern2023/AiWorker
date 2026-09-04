# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx


class HongguoVideoCaptureServiceError(RuntimeError):
    pass


def completed_capture_artifact(
    job: dict[str, Any], episode: int, capture_root: str | Path
) -> Path | None:
    if (
        str(job.get("status") or "") != "completed"
        or job.get("downloaded") is not True
        or job.get("decrypted") is not True
    ):
        return None
    output_value = str(job.get("output_path") or "").strip()
    if not output_value:
        return None
    trusted_root = Path(capture_root).expanduser().resolve().parent.parent
    output_dir = Path(output_value).expanduser().resolve()
    try:
        output_dir.relative_to(trusted_root)
    except ValueError:
        return None
    target = output_dir / f"E{int(episode):03d}.mp4"
    return target if target.is_file() else None


def safe_capture_service_error(
    detail: Any, *, status_code: int | None = None, job_failure: bool = False
) -> str:
    """Translate an untrusted service error without persisting its raw details."""

    lowered = str(detail or "").casefold()
    if "not connected to adb" in lowered or (
        "adb" in lowered and "not connected" in lowered
    ):
        return "模拟器未连接到 ADB"
    if "job not found" in lowered:
        return "视频抓包任务不存在"
    if job_failure:
        return "视频抓包任务执行失败"
    if status_code is not None:
        return f"视频抓包服务请求失败（HTTP {status_code}）"
    return "视频抓包服务请求失败"


class HongguoVideoCaptureClient:
    """Small client for the external video-capture service on port 8776."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8776",
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        value = str(base_url or "").strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("视频抓包服务地址必须是有效的 HTTP 地址")
        self.base_url = value
        self.timeout = timeout
        self.transport = transport

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def start_capture(
        self, title: str, series_id: str, episode: int
    ) -> dict[str, Any]:
        payload = {
            "title": str(title or "").strip(),
            "series_id": str(series_id or "").strip(),
            "episode": int(episode),
        }
        if not payload["title"] or not payload["series_id"] or payload["episode"] < 1:
            raise ValueError("视频抓包请求缺少有效的剧名、series_id 或集数")
        data = self._request("POST", "/v1/videos/capture", payload)
        if not str(data.get("job_id") or "").strip():
            raise HongguoVideoCaptureServiceError("视频抓包服务未返回 job_id")
        return data

    def get_job(self, job_id: str) -> dict[str, Any]:
        value = str(job_id or "").strip()
        if not value:
            raise ValueError("视频抓包任务缺少 job_id")
        return self._request("GET", f"/v1/videos/jobs/{quote(value, safe='')}")

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.request(
                    method, f"{self.base_url}{path}", json=payload
                )
        except httpx.HTTPError as exc:
            raise HongguoVideoCaptureServiceError(
                f"视频抓包服务不可用（{type(exc).__name__}）"
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise HongguoVideoCaptureServiceError("视频抓包服务返回了无效 JSON") from exc
        if not isinstance(data, dict):
            raise HongguoVideoCaptureServiceError("视频抓包服务返回了无效数据")
        if response.status_code >= 400 or data.get("error"):
            detail = data.get("error")
            raise HongguoVideoCaptureServiceError(
                safe_capture_service_error(
                    detail,
                    status_code=(response.status_code if response.status_code >= 400 else None),
                    job_failure=response.status_code < 400,
                )
            )
        return data
