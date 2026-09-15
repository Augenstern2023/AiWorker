# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import pytest

from analysis.llm_credentials import (
    KeyringLLMCredentialStore,
    LLMCredentialStoreError,
)


class NoKeyringError(RuntimeError):
    pass


class RecordingKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.last_service: str | None = None
        self.last_username: str | None = None

    def get_password(self, service: str, username: str) -> str | None:
        self.last_service = service
        self.last_username = username
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.last_service = service
        self.last_username = username
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.last_service = service
        self.last_username = username
        self.values.pop((service, username), None)


def test_keyring_store_replaces_provider_secret_without_exposing_it() -> None:
    backend = RecordingKeyring()
    store = KeyringLLMCredentialStore(backend)

    store.set("provider-1", "first-secret")
    store.set("provider-1", "second-secret")

    assert store.get("provider-1") == "second-secret"
    assert backend.last_service == "AiWorker.LLM"
    assert backend.last_username == "provider:provider-1"
    assert "second-secret" not in repr(store)


def test_keyring_store_deletes_provider_secret() -> None:
    backend = RecordingKeyring()
    store = KeyringLLMCredentialStore(backend)
    store.set("provider-1", "secret")

    store.delete("provider-1")

    assert store.get("provider-1") is None


def test_keyring_store_rejects_empty_secret() -> None:
    store = KeyringLLMCredentialStore(RecordingKeyring())

    with pytest.raises(ValueError, match="api_key"):
        store.set("provider-1", "")


def test_keyring_store_returns_none_when_backend_is_unavailable() -> None:
    class UnavailableKeyring:
        def get_password(self, service: str, username: str) -> str:
            raise NoKeyringError()

    store = KeyringLLMCredentialStore(UnavailableKeyring())

    assert store.get("provider-1") is None


def test_keyring_store_sanitizes_write_failures() -> None:
    class FailingKeyring:
        def set_password(self, service: str, username: str, password: str) -> None:
            raise RuntimeError(f"backend rejected {password}")

    store = KeyringLLMCredentialStore(FailingKeyring())

    with pytest.raises(LLMCredentialStoreError) as error:
        store.set("provider-1", "secret-that-must-not-leak")

    assert str(error.value) == "系统凭据库不可用"
    assert "secret-that-must-not-leak" not in str(error.value)


def test_keyring_store_sanitizes_delete_failures() -> None:
    class FailingKeyring:
        def delete_password(self, service: str, username: str) -> None:
            raise RuntimeError("cannot delete")

    store = KeyringLLMCredentialStore(FailingKeyring())

    with pytest.raises(LLMCredentialStoreError, match="系统凭据库不可用"):
        store.delete("provider-1")
