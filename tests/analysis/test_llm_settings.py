# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import importlib
import importlib.util
import threading
import time
from contextlib import contextmanager

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


def _settings_module():
    spec = importlib.util.find_spec("analysis.llm_settings")
    assert spec is not None, "analysis.llm_settings must provide LLM configuration storage"
    return importlib.import_module("analysis.llm_settings")


def test_secret_cipher_encrypts_without_returning_plaintext(tmp_path, monkeypatch):
    """Would catch API keys being stored or exposed as plaintext."""

    monkeypatch.delenv("AIWORKER_LLM_CONFIG_KEY", raising=False)
    module = _settings_module()
    cipher = module.LLMSecretCipher.from_environment(tmp_path)

    encrypted = cipher.encrypt("sk-private-value")

    assert "sk-private-value" not in encrypted
    assert cipher.decrypt(encrypted) == "sk-private-value"
    assert (tmp_path / "llm_config.key").read_text(encoding="ascii").strip()


def test_secret_cipher_reuses_generated_key_across_process_instances(
    tmp_path, monkeypatch
):
    """Would catch background workers generating a different decryption key."""

    monkeypatch.delenv("AIWORKER_LLM_CONFIG_KEY", raising=False)
    module = _settings_module()
    first = module.LLMSecretCipher.from_environment(tmp_path)
    ciphertext = first.encrypt("shared-secret")

    second = module.LLMSecretCipher.from_environment(tmp_path)

    assert second.decrypt(ciphertext) == "shared-secret"


def test_secret_cipher_waits_for_a_concurrent_key_writer(tmp_path, monkeypatch):
    """Would catch a second startup reading the key file before its writer flushes."""

    monkeypatch.delenv("AIWORKER_LLM_CONFIG_KEY", raising=False)
    module = _settings_module()
    key_path = tmp_path / "llm_config.key"
    key_path.write_bytes(b"")
    generated = Fernet.generate_key()

    def finish_write() -> None:
        time.sleep(0.03)
        key_path.write_bytes(generated + b"\n")

    writer = threading.Thread(target=finish_write)
    writer.start()
    try:
        cipher = module.LLMSecretCipher.from_environment(tmp_path)
    finally:
        writer.join()

    ciphertext = cipher.encrypt("shared-secret")
    assert module.LLMSecretCipher(generated).decrypt(ciphertext) == "shared-secret"


def test_provider_presets_include_editable_openai_compatible_defaults():
    """Would catch a preset using the wrong endpoint or wire protocol."""

    module = _settings_module()

    assert module.PROVIDER_PRESETS["openai"]["base_url"] == "https://api.openai.com/v1"
    assert module.PROVIDER_PRESETS["openai"]["wire_api"] == "responses"
    assert module.PROVIDER_PRESETS["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert module.PROVIDER_PRESETS["deepseek"]["wire_api"] == "chat_completions"
    assert module.PROVIDER_PRESETS["minimax"]["base_url"] == "https://api.minimax.io/v1"
    assert module.PROVIDER_PRESETS["sub2api"]["wire_api"] == "responses"


def test_public_provider_never_contains_ciphertext_or_api_key():
    """Would catch a settings response leaking encrypted or plaintext credentials."""

    module = _settings_module()
    public = module.public_provider(
        {
            "id": "provider-1",
            "name": "Private provider",
            "preset": "custom",
            "base_url": "http://models.example.test/v1",
            "wire_api": "chat_completions",
            "is_enabled": True,
            "api_key_encrypted": "encrypted-payload",
        }
    )

    assert public["has_api_key"] is True
    assert public["security_warning"]
    assert "api_key" not in public
    assert "api_key_encrypted" not in public


class _TestDatabase:
    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    @contextmanager
    def connection(self):
        with self.engine.connect() as connection:
            yield connection

    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            yield connection


def _repository():
    module = _settings_module()
    schema = importlib.import_module("system_database.schema")
    tables = [
        getattr(schema, "llm_providers", None),
        getattr(schema, "llm_models", None),
        getattr(schema, "llm_settings", None),
    ]
    assert all(table is not None for table in tables), (
        "system database must define all LLM settings tables"
    )
    database = _TestDatabase()
    schema.SYSTEM_METADATA.create_all(database.engine, tables=tables)
    cipher = module.LLMSecretCipher(Fernet.generate_key())
    return module.LLMSettingsRepository(database, cipher)


def test_repository_persists_secret_but_only_returns_credential_status():
    """Would catch provider creation returning its encrypted or plaintext API key."""

    repository = _repository()

    provider = repository.create_provider(
        name="Internal Gateway",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key="sk-storage-secret",
    )
    settings = repository.settings_payload()
    effective = repository.effective_default()

    assert provider["has_api_key"] is True
    assert "api_key" not in provider
    assert "api_key_encrypted" not in provider
    assert settings["providers"][0]["has_api_key"] is True
    assert "sk-storage-secret" not in repr(settings)
    assert effective is None


def test_settings_marks_credentials_unreadable_after_encryption_key_loss():
    """Would catch a lost Fernet key being presented as a usable saved credential."""

    module = _settings_module()
    repository = _repository()
    repository.create_provider(
        name="Lost Key Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key="sk-encrypted-with-old-key",
    )
    restarted = module.LLMSettingsRepository(
        repository.database, module.LLMSecretCipher(Fernet.generate_key())
    )

    provider = restarted.settings_payload()["providers"][0]

    assert provider["has_api_key"] is True
    assert provider["credential_status"] == "unreadable"
    assert "sk-encrypted-with-old-key" not in repr(provider)


def test_repository_sets_an_enabled_model_as_effective_default():
    """Would catch a default selection that does not drive runtime credentials."""

    repository = _repository()
    provider = repository.create_provider(
        name="OpenAI",
        preset="openai",
        base_url="https://api.openai.com/v1",
        wire_api="responses",
        api_key="sk-runtime-secret",
    )
    model = repository.create_model(
        provider["id"], model_id="gpt-5.6-sol", display_name="GPT 5.6"
    )

    repository.set_default_model(model["id"])

    effective = repository.effective_default()
    assert effective == {
        "provider": "openai",
        "provider_id": provider["id"],
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-runtime-secret",
        "model": "gpt-5.6-sol",
        "wire_api": "responses",
        "allow_insecure_http": False,
    }


def test_repository_rejects_disabling_the_active_default():
    """Would catch an update leaving the global default unusable."""

    repository = _repository()
    provider = repository.create_provider(
        name="Default Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key="secret",
    )
    model = repository.create_model(provider["id"], model_id="model-a")
    repository.set_default_model(model["id"])

    with pytest.raises(repository.conflict_error, match="默认模型"):
        repository.update_model(model["id"], is_enabled=False)
    with pytest.raises(repository.conflict_error, match="默认模型"):
        repository.update_provider(provider["id"], is_enabled=False)


@pytest.mark.parametrize("api_key", ["", "   "])
def test_repository_rejects_default_without_usable_credentials(api_key):
    """Would catch saving a global default that every runtime call must reject."""

    repository = _repository()
    provider = repository.create_provider(
        name="Credentialless Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key=api_key,
    )
    model = repository.create_model(provider["id"], model_id="model-a")

    with pytest.raises(repository.conflict_error, match="API Key"):
        repository.set_default_model(model["id"])


def test_repository_rejects_clearing_credentials_from_the_default_provider():
    """Would catch an explicit clear leaving the active global default unusable."""

    repository = _repository()
    provider = repository.create_provider(
        name="Default Credential Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key="current-secret",
    )
    model = repository.create_model(provider["id"], model_id="model-a")
    repository.set_default_model(model["id"])

    with pytest.raises(repository.conflict_error, match="默认模型"):
        repository.update_provider(provider["id"], clear_api_key=True)


def test_synced_models_merge_without_removing_manual_or_missing_models():
    """Would catch remote synchronization replacing the local model library."""

    repository = _repository()
    provider = repository.create_provider(
        name="Sync Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key="secret",
    )
    repository.create_model(provider["id"], model_id="manual-model")
    repository.merge_synced_models(provider["id"], ["remote-a", "remote-b"])

    result = repository.merge_synced_models(provider["id"], ["remote-b", "remote-c"])
    model_ids = {
        model["model_id"]
        for model in repository.settings_payload()["providers"][0]["models"]
    }

    assert result == {"added": 1, "updated": 1, "total": 4}
    assert model_ids == {"manual-model", "remote-a", "remote-b", "remote-c"}


def test_legacy_import_is_idempotent_and_becomes_the_default():
    """Would catch startup creating duplicate providers or failing to select the import."""

    repository = _repository()

    class LegacyConfig:
        LLM_PROVIDER = "sub2api"
        LLM_BASE_URL = "http://gateway.example.test/v1"
        LLM_API_KEY = "legacy-secret"
        LLM_MODEL = "gpt-5.6-sol"
        LLM_WIRE_API = "responses"

    first = repository.import_legacy(LegacyConfig)
    second = repository.import_legacy(LegacyConfig)

    settings = repository.settings_payload()
    assert first["imported"] is True
    assert second["imported"] is False
    assert len(settings["providers"]) == 1
    assert settings["providers"][0]["models"][0]["source"] == "imported"
    assert repository.effective_default()["api_key"] == "legacy-secret"
