# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Any


_PAID_BOOLEAN_FIELDS = {
    "is_paid",
    "is_paid_content",
    "need_pay",
    "needs_payment",
    "requires_payment",
    "is_charge",
    "is_chargeable",
    "is_series_paid",
}
_PAID_STATUS_FIELDS = {"paid_status", "series_paid_status", "charge_status", "payment_status"}
_PAID_CONTAINERS = {"paid_info", "series_paid_info", "charge_info", "payment_info"}
_PAID_MESSAGE_FIELDS = {"message", "msg", "status_msg", "error", "error_message"}
_PAID_MESSAGE_MARKERS = ("需要付费", "需付费", "付费解锁", "购买后", "请购买", "paywall", "purchase required")


def _truthy_paid_status(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().casefold() in {"1", "2", "paid", "locked", "need_pay", "requires_payment"}


def _positive_price(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def paid_content_reason(payload: Any) -> str:
    """Return a reason only when provider data explicitly marks paid content."""

    def walk(value: Any, path: tuple[str, ...] = ()) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                field = str(key).casefold()
                field_path = ".".join((*path, str(key)))
                if field in _PAID_BOOLEAN_FIELDS and _truthy_paid_status(item):
                    return f"接口标记 {field_path}"
                if field in _PAID_STATUS_FIELDS and _truthy_paid_status(item):
                    return f"接口标记 {field_path}={item}"
                if field == "price" and path and path[-1].casefold() in _PAID_CONTAINERS and _positive_price(item):
                    return f"接口标记 {field_path}={item}"
                if field in _PAID_MESSAGE_FIELDS and isinstance(item, str):
                    lowered = item.casefold()
                    if any(marker in lowered for marker in _PAID_MESSAGE_MARKERS):
                        return item.strip()
                reason = walk(item, (*path, str(key)))
                if reason:
                    return reason
        elif isinstance(value, list):
            for index, item in enumerate(value):
                reason = walk(item, (*path, str(index)))
                if reason:
                    return reason
        return ""

    return walk(payload)

