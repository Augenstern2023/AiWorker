# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Connection

from fastapi import Request

from identity.repository import IdentityRepository
from identity.schema import Principal

from .repository import AuditRepository


MAX_SUMMARY_BYTES = 8192
_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "cookie",
    "activation_code",
    "api_key",
)
_SENSITIVE_TEXT_VALUE = re.compile(
    r"\b(password|token|secret|cookie|activation_code|api_key)\b(\s*[:=]\s*)([^\s,;]+)",
    re.IGNORECASE,
)


class AuditService:
    def __init__(self, repository: IdentityRepository | AuditRepository) -> None:
        self.repository = (
            repository
            if isinstance(repository, AuditRepository)
            else AuditRepository(repository)
        )

    def record(
        self,
        *,
        actor: Principal | None,
        action: str,
        target_type: str,
        target_id: str | None,
        before: Any,
        after: Any,
        reason: str | None,
        result: str,
        request: Request | None = None,
        project_id: str | None = None,
        connection: Connection | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        request_id, ip_address, user_agent = self._request_metadata(request)
        self.repository.insert(
            {
                "id": event_id,
                "actor_user_id": actor.user_id if actor is not None else None,
                "actor_display_name_snapshot": (
                    actor.display_name if actor is not None else None
                ),
                "actor_role_snapshot_json": json.dumps(
                    list(actor.role_codes) if actor is not None else [],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "project_id": project_id,
                "before_summary_json": self._summary_json(before),
                "after_summary_json": self._summary_json(after),
                "request_id": request_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "reason": reason,
                "result": result,
            },
            connection=connection,
        )
        return event_id

    def get(self, event_id: str) -> dict[str, Any] | None:
        return self.repository.get(event_id)

    def query(self, **filters: Any) -> list[dict[str, Any]]:
        return self.repository.query(**filters)

    def count(self, **filters: Any) -> int:
        filters.pop("limit", None)
        filters.pop("offset", None)
        return self.repository.count(**filters)

    @staticmethod
    def _request_metadata(
        request: Request | None,
    ) -> tuple[str | None, str | None, str | None]:
        if request is None:
            return None, None, None
        request_id = getattr(request.state, "request_id", None)
        if request_id is None:
            request_id = request.headers.get("X-Request-ID")
        ip_address = request.client.host if request.client is not None else None
        return request_id, ip_address, request.headers.get("user-agent")

    @classmethod
    def _summary_json(cls, value: Any) -> str | None:
        if value is None:
            return None
        redacted = cls._redact(value)
        serialized = json.dumps(
            redacted, ensure_ascii=False, separators=(",", ":"), default=str
        )
        if len(serialized.encode("utf-8")) <= MAX_SUMMARY_BYTES:
            return serialized

        low = 0
        high = len(serialized)
        original_bytes = len(serialized.encode("utf-8"))
        while low < high:
            midpoint = (low + high + 1) // 2
            bounded = json.dumps(
                {
                    "_truncated": True,
                    "_original_bytes": original_bytes,
                    "_preview": serialized[:midpoint],
                },
                ensure_ascii=False,
            )
            if len(bounded.encode("utf-8")) <= MAX_SUMMARY_BYTES:
                low = midpoint
            else:
                high = midpoint - 1
        return json.dumps(
            {
                "_truncated": True,
                "_original_bytes": original_bytes,
                "_preview": serialized[:low],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                normalized = key_text.casefold()
                redacted[key_text] = (
                    "[REDACTED]"
                    if any(part in normalized for part in _SENSITIVE_KEY_PARTS)
                    else cls._redact(item)
                )
            return redacted
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._redact(item) for item in value]
        if isinstance(value, str):
            return _SENSITIVE_TEXT_VALUE.sub(r"\1\2[REDACTED]", value)
        return value
