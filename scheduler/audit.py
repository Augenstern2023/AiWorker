# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Adapter for recording system-generated schedule events in the audit center."""

from __future__ import annotations

from typing import Any

from audit.service import AuditService


class SystemScheduleAuditWriter:
    def __init__(self, audit_service: AuditService) -> None:
        self.audit_service = audit_service

    def record(
        self, *, action: str, target_id: str, after: dict[str, Any], result: str
    ) -> None:
        self.audit_service.record(
            actor=None,
            action=action,
            target_type="schedule",
            target_id=target_id,
            before=None,
            after=after,
            reason=None,
            result=result,
        )
