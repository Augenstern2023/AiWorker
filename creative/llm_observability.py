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
序号、耗时、安全结构摘要与完整诊断信息分别保存：

- 任务日志（``emit_event``）只记录安全摘要，不含响应键值或异常原文；
- 完整返回体及异常写入 ``data/creative/jobs/<job_id>/llm_debug/<stage>-attemptN.json``。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from analysis.llm import LLMError, LLMOutputTruncatedError, LLMTimeoutError

logger = logging.getLogger(__name__)


LLM_DEBUG_BASE_DIR = Path("data") / "creative" / "jobs"

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
    """供内部修复提示使用的键形态摘要；不得写入普通日志或任务事件。"""
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


def summarize_payload_metadata(payload: Any) -> str:
    """Public diagnostic summary: never include upstream keys or values."""
    if isinstance(payload, dict):
        return f"顶层对象/{len(payload)}键"
    if isinstance(payload, list):
        return f"顶层数组/{len(payload)}项"
    if payload is None:
        return "无响应对象"
    return "顶层标量"


def _public_request_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    result = {}
    for key in (
        "protocol",
        "model",
        "reasoning_effort",
        "response_format",
        "client_request_id",
        "gateway_request_id",
        "exception_type",
        "timeout_phase",
    ):
        value = summary.get(key)
        if isinstance(value, str):
            result[key] = value
    for key in ("max_tokens", "message_count", "input_chars", "request_attempt"):
        value = summary.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    elapsed = summary.get("request_elapsed_seconds")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and elapsed >= 0:
        result["request_elapsed_seconds"] = round(float(elapsed), 3)
    return result


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
    if summary.get("client_request_id"):
        parts.append(f"request_id={summary['client_request_id']}")
    if summary.get("gateway_request_id"):
        parts.append(f"gateway_request_id={summary['gateway_request_id']}")
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
        path = _available_debug_path(target_dir / f"{stage or 'stage'}-attempt{int(attempt)}.json")
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
        path = _available_debug_path(target_dir / f"{stage or 'stage'}-attempt{int(attempt)}-truncated.txt")
        path.write_text(text, encoding="utf-8")
    except Exception:
        logger.warning(
            "写入模型截断片段失败：job_id=%s stage=%s attempt=%s",
            job_id,
            stage,
            attempt,
        )
        return None
    return str(path)


def _available_debug_path(path: Path) -> Path:
    # Batch splitting restarts attempt numbering; preserve earlier evidence.
    candidate = path
    sequence = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{sequence}{path.suffix}")
        sequence += 1
    return candidate


def public_failure_summary(exc: Exception) -> tuple[str, str]:
    """Classify failures without exposing supplier or validator error text."""
    if isinstance(exc, LLMOutputTruncatedError):
        return "llm_output_truncated", "模型输出被截断，请缩小单次生成范围或调整输出上限。"
    if isinstance(exc, LLMTimeoutError):
        return "llm_timeout", "模型调用超时，请稍后重试。"
    if isinstance(exc, LLMError):
        return "llm_error", "模型调用或响应校验失败，请查看任务诊断。"
    return "creative_generation_failed", "创作任务失败，请查看任务诊断。"


def dump_terminal_error(job_id: str, exc: Exception) -> str | None:
    """Keep the original failure in task diagnostics, never in public events."""
    try:
        target_dir = LLM_DEBUG_BASE_DIR / str(job_id) / "llm_debug"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = _available_debug_path(target_dir / "terminal-error.json")
        diagnostic = {
            "job_id": str(job_id),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "cause_type": (
                type(exc.__cause__).__name__
                if exc.__cause__ is not None
                else None
            ),
            "cause": str(exc.__cause__) if exc.__cause__ is not None else None,
        }
        request = _public_request_summary(
            getattr(exc, "request_summary", None)
        )
        if request:
            diagnostic["request"] = request
        if isinstance(exc, LLMOutputTruncatedError):
            diagnostic.update(
                finish_reason=exc.finish_reason,
                returned_chars=exc.returned_chars,
                partial_text=exc.partial_text,
            )
        path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except Exception:
        logger.warning("写入任务失败诊断文件失败")
        return None


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
        error = (
            "模型输出被输出上限截断"
            if info.get("output_truncated") or info.get("truncated_text")
            else "模型响应未通过结构校验"
        )
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
        request = _public_request_summary(info.get("request"))
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
            shape = summarize_payload_metadata(response)
            payload["error"] = error
            message = (
                f"第 {attempt}/{max_attempts} 次模型响应未通过结构校验"
                f"（{elapsed_seconds:.1f} 秒）：{error}"
            )
            if response is not None:
                message += f"；{shape}"
            if request_line:
                message += f"；{request_line}"
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
            )

    return report
