# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Operating-system credential storage for LLM provider API keys."""

from __future__ import annotations

import sys
from typing import Any, Protocol


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
        self._backend = self._default_backend() if backend is None else backend

    def get(self, provider_id: str) -> str | None:
        username = self._username(provider_id)
        if self._backend is None:
            return None

        try:
            return self._backend.get_password(self.service_name, username)
        except Exception:
            # Reading is best-effort so deployments without a keyring can use
            # their environment-based configuration instead.
            return None

    def set(self, provider_id: str, api_key: str) -> None:
        username = self._username(provider_id)
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must not be empty")
        if self._backend is None:
            self._raise_unavailable()

        failed = False
        try:
            self._backend.set_password(
                self.service_name,
                username,
                api_key,
            )
        except Exception:
            failed = True
        if failed:
            self._raise_unavailable()

    def delete(self, provider_id: str) -> None:
        username = self._username(provider_id)
        if self._backend is None:
            self._raise_unavailable()

        failed = False
        try:
            self._backend.delete_password(self.service_name, username)
        except Exception:
            failed = True
        if failed:
            self._raise_unavailable()

    @staticmethod
    def _default_backend() -> Any | None:
        if sys.platform != "win32":
            return None
        try:
            from keyring.backends.Windows import WinVaultKeyring

            return WinVaultKeyring()
        except Exception:
            return None

    @staticmethod
    def _username(provider_id: str) -> str:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id must not be empty")
        return f"provider:{provider_id}"

    @staticmethod
    def _raise_unavailable() -> None:
        raise LLMCredentialStoreError("系统凭据库不可用")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(service_name={self.service_name!r})"


def default_llm_credential_store() -> LLMCredentialStore:
    """Create the credential store used by live Web and Worker processes."""

    return KeyringLLMCredentialStore()
