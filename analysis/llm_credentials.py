# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Operating-system credential storage for LLM provider API keys."""

from __future__ import annotations

from typing import Any, Protocol

import keyring


class LLMCredentialStoreError(RuntimeError):
    """Raised when the operating-system credential store cannot be changed."""


class LLMCredentialStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...

    def set(self, provider_id: str, api_key: str) -> None: ...

    def delete(self, provider_id: str) -> None: ...


class KeyringLLMCredentialStore:
    """Store provider credentials in the operating system keyring."""

    service_name = "AiWorker.LLM"

    def __init__(self, backend: Any | None = None) -> None:
        self._backend = keyring if backend is None else backend

    def get(self, provider_id: str) -> str | None:
        try:
            return self._backend.get_password(self.service_name, self._username(provider_id))
        except Exception:
            # Reading is best-effort so deployments without a keyring can use
            # their environment-based configuration instead.
            return None

    def set(self, provider_id: str, api_key: str) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")

        try:
            self._backend.set_password(
                self.service_name,
                self._username(provider_id),
                api_key,
            )
        except Exception:
            raise LLMCredentialStoreError("系统凭据库不可用") from None

    def delete(self, provider_id: str) -> None:
        try:
            self._backend.delete_password(self.service_name, self._username(provider_id))
        except Exception:
            raise LLMCredentialStoreError("系统凭据库不可用") from None

    @staticmethod
    def _username(provider_id: str) -> str:
        if not provider_id:
            raise ValueError("provider_id must not be empty")
        return f"provider:{provider_id}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(service_name={self.service_name!r})"
