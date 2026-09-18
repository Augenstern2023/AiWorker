# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""LLM 结构校验过程的可观测性：尝试事件与原始响应落盘。

结构校验失败时，任务日志过去只留下最终错误文本（例如「relationships 必须是数组」），
既看不到模型实际返回了什么，也看不出第一次修复重试是否真的发生过。这里把每次尝试的
序号、耗时、错误、返回体的顶层键形态与完整原始响应都记下来：

- 事件写入任务日志（``emit_event``），供 WebGUI「任务日志」直接阅读；
- 完整返回体写入 ``data/creative/jobs/<job_id>/llm_debug/<stage>-attemptN.json``。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


LLM_DEBUG_BASE_DIR = Path("data") / "creative" / "jobs"
MESSAGE_PREVIEW_LIMIT = 240
EVENT_PREVIEW_LIMIT = 1200

_ACTIVE_REPORTER: Callable[[dict[str, Any]], Any] | None = None


def _describe_value(value: Any) -> str:
    if isinstance(value, dict):
        return f"对象/{len(value)}键"
    if isinstance(value, list):
        return f"数组/{len(value)}项"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return type(value).__name__


def summarize_payload_shape(payload: Any) -> str:
    """用一行描述返回体的顶层形态，便于在日志里立刻看出键名漂移。"""
    if isinstance(payload, dict):
        if not payload:
            return "顶层键：（无）"
        keys = "、".join(
            f"{key}({_describe_value(value)})" for key, value in payload.items()
        )
        return f"顶层键：{keys}"
    if isinstance(payload, list):
        return f"顶层为数组（数组/{len(payload)}项）"
    return f"顶层为 {type(payload).__name__}"


def truncate_text(value: Any, limit: int) -> str:
    text = " ".join(str(value if value is not None else "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def format_request_summary(summary: Any) -> str:
    """把一次调用的实际请求参数渲染成一行，便于确认推理预算与输出上限。"""
    if not isinstance(summary, dict) or not summary:
        return ""
    parts = [
        f"model={summary.get('model') or ''}",
        f"protocol={summary.get('protocol') or ''}",
        f"max_tokens={summary.get('max_tokens') or 0}",
        f"reasoning_effort={summary.get('reasoning_effort') or '（未发送）'}",
        f"response_format={summary.get('response_format') or '（未发送）'}",
        f"输入={summary.get('input_chars') or 0} 字符",
    ]
    return "请求参数：" + "、".join(parts)


def set_attempt_reporter(
    reporter: Callable[[dict[str, Any]], Any] | None,
) -> Callable[[dict[str, Any]], Any] | None:
    """注册进程级尝试上报器，返回被替换掉的上一个上报器。

    每个创作任务由独立子进程执行，因此进程级全局变量足够安全；返回值便于调用方
    在任务结束时恢复原状态。
    """
    global _ACTIVE_REPORTER
    previous = _ACTIVE_REPORTER
    _ACTIVE_REPORTER = reporter
    return previous


def active_attempt_reporter() -> Callable[[dict[str, Any]], Any] | None:
    return _ACTIVE_REPORTER


def dump_attempt_payload(
    job_id: str,
    stage: str,
    attempt: int,
    info: dict[str, Any],
    *,
    base_dir: str | Path | None = None,
) -> str | None:
    """把一次结构校验失败的完整返回体写入调试文件，返回文件路径。"""
    payload = info.get("payload")
    if payload is None:
        return None
    base = Path(base_dir) if base_dir is not None else LLM_DEBUG_BASE_DIR
    try:
        target_dir = base / str(job_id) / "llm_debug"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{stage or 'stage'}-attempt{int(attempt)}.json"
        path.write_text(
            json.dumps(
                {
                    "job_id": str(job_id),
                    "stage": str(stage or ""),
                    "attempt": int(attempt),
                    "max_attempts": info.get("max_attempts"),
                    "ok": bool(info.get("ok")),
                    "error": str(info.get("error") or ""),
                    "elapsed_seconds": info.get("elapsed_seconds"),
                    "request": info.get("request") or {},
                    "response": payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.warning(
            "写入模型原始响应调试文件失败：job_id=%s stage=%s attempt=%s",
            job_id,
            stage,
            attempt,
            exc_info=True,
        )
        return None
    return str(path)


def dump_truncated_text(
    job_id: str,
    stage: str,
    attempt: int,
    text: str,
    *,
    base_dir: str | Path | None = None,
) -> str | None:
    """把被输出上限截断的原始片段落盘，返回文件路径。"""
    if not text:
        return None
    base = Path(base_dir) if base_dir is not None else LLM_DEBUG_BASE_DIR
    try:
        target_dir = base / str(job_id) / "llm_debug"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{stage or 'stage'}-attempt{int(attempt)}-truncated.txt"
        path.write_text(text, encoding="utf-8")
    except Exception:
        logger.warning(
            "写入模型截断片段失败：job_id=%s stage=%s attempt=%s",
            job_id,
            stage,
            attempt,
            exc_info=True,
        )
        return None
    return str(path)


def build_attempt_reporter(
    notify: Callable[[str, str, dict[str, Any]], Any],
    *,
    job_id: str,
    stage: str,
    base_dir: str | Path | None = None,
) -> Callable[[dict[str, Any]], None]:
    """构造任务日志上报器。

    ``notify(message, level, payload)`` 由调用方适配各自的 emit 入口。
    第一次尝试就通过时不产生日志（避免长任务的日志噪音），一旦发生修复重试或
    校验失败，则记录序号、耗时、错误与返回体形态。
    """

    def report(info: dict[str, Any]) -> None:
        attempt = int(info.get("attempt") or 1)
        max_attempts = int(info.get("max_attempts") or 1)
        elapsed_seconds = float(info.get("elapsed_seconds") or 0.0)
        error = str(info.get("error") or "")
        ok = bool(info.get("ok"))
        if ok and attempt == 1:
            return

        payload: dict[str, Any] = {
            "stage": str(stage or ""),
            "attempt": attempt,
            "max_attempts": max_attempts,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "ok": ok,
        }
        request = info.get("request")
        if isinstance(request, dict) and request:
            payload["request"] = request
        request_line = format_request_summary(request)
        debug_path: str | None = None
        truncated_path: str | None = None
        if not ok:
            truncated_path = dump_truncated_text(
                job_id,
                stage,
                attempt,
                str(info.get("truncated_text") or ""),
                base_dir=base_dir,
            )
            debug_path = dump_attempt_payload(
                job_id, stage, attempt, info, base_dir=base_dir
            )
        payload["debug_path"] = debug_path
        if truncated_path:
            payload["truncated_path"] = truncated_path

        if ok:
            message = (
                f"第 {attempt}/{max_attempts} 次模型响应通过结构校验"
                f"（{elapsed_seconds:.1f} 秒）"
            )
            level = "info"
        else:
            response = info.get("payload")
            shape = summarize_payload_shape(response)
            payload["error"] = error
            preview = truncate_text(
                json.dumps(response, ensure_ascii=False)
                if response is not None
                else "",
                EVENT_PREVIEW_LIMIT,
            )
            if preview:
                payload["raw_response_preview"] = preview
            message = (
                f"第 {attempt}/{max_attempts} 次模型响应未通过结构校验"
                f"（{elapsed_seconds:.1f} 秒）：{error}"
            )
            if response is not None:
                message += f"；{shape}"
            if request_line:
                message += f"；{request_line}"
            if preview:
                message += f"；原始响应开头：{truncate_text(preview, MESSAGE_PREVIEW_LIMIT)}"
            if debug_path:
                message += f"；原始响应已保存：{debug_path}"
            if truncated_path:
                message += f"；截断片段已保存：{truncated_path}"
            level = "warning"

        try:
            notify(message, level, payload)
        except Exception:
            logger.warning(
                "写入模型结构校验事件失败：job_id=%s stage=%s attempt=%s",
                job_id,
                stage,
                attempt,
                exc_info=True,
            )

    return report
