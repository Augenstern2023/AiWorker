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
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool


def _settings_module():
    spec = importlib.util.find_spec("analysis.llm_settings")
    assert spec is not None, "analysis.llm_settings must provide LLM configuration storage"
    return importlib.import_module("analysis.llm_settings")


def test_provider_presets_define_environment_variable_names():
    """Would catch a built-in preset losing its deployment credential mapping."""

    module = _settings_module()

    assert module.PROVIDER_PRESETS["openai"]["env_key"] == "OPENAI_API_KEY"
    assert module.PROVIDER_PRESETS["deepseek"]["env_key"] == "DEEPSEEK_API_KEY"
    assert module.PROVIDER_PRESETS["minimax"]["env_key"] == "MINIMAX_API_KEY"
    assert module.PROVIDER_PRESETS["sub2api"]["env_key"] == "SUB2API_KEY"
    assert module.PROVIDER_PRESETS["custom"]["env_key"] == ""


@pytest.mark.parametrize("value", ["", "1BAD", "HAS-DASH", "HAS SPACE"])
def test_custom_provider_rejects_invalid_environment_variable_name(value):
    """Would catch an unusable environment lookup name being persisted."""

    repository = _repository()

    with pytest.raises(ValueError, match="环境变量"):
        repository.create_provider(
            name="Custom",
            preset="custom",
            base_url="https://llm.example.test/v1",
            wire_api="chat_completions",
            api_key_env_var=value,
        )


def test_repository_stores_only_environment_variable_name(monkeypatch):
    """Would catch plaintext or ciphertext credentials being written to the database."""

    module = _settings_module()
    repository = _repository()
    monkeypatch.setenv("CUSTOM_LLM_KEY", "runtime-secret")

    provider = repository.create_provider(
        name="Custom",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="CUSTOM_LLM_KEY",
    )
    with repository.database.connection() as connection:
        row = connection.execute(select(module.llm_providers)).mappings().one()

    assert row["api_key_env_var"] == "CUSTOM_LLM_KEY"
    assert row["api_key_encrypted"] is None
    assert provider["credential_status"] == "configured"
    assert provider["credential_source"] == "environment"
    assert "runtime-secret" not in repr(provider)


def test_provider_presets_include_editable_openai_compatible_defaults():
    """Would catch a preset using the wrong endpoint or wire protocol."""

    module = _settings_module()

    assert module.PROVIDER_PRESETS["openai"]["base_url"] == "https://api.openai.com/v1"
    assert module.PROVIDER_PRESETS["openai"]["wire_api"] == "responses"
    assert module.PROVIDER_PRESETS["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert module.PROVIDER_PRESETS["deepseek"]["wire_api"] == "chat_completions"
    assert module.PROVIDER_PRESETS["minimax"]["base_url"] == "https://api.minimax.io/v1"
    assert module.PROVIDER_PRESETS["sub2api"]["wire_api"] == "chat_completions"


def test_public_provider_never_contains_ciphertext_or_api_key(monkeypatch):
    """Would catch a settings response leaking encrypted or plaintext credentials."""

    monkeypatch.setenv("PUBLIC_PROVIDER_KEY", "public-runtime-secret")
    module = _settings_module()
    public = module.public_provider(
        {
            "id": "provider-1",
            "name": "Private provider",
            "preset": "custom",
            "base_url": "http://models.example.test/v1",
            "wire_api": "chat_completions",
            "is_enabled": True,
            "api_key_env_var": "PUBLIC_PROVIDER_KEY",
            "api_key_encrypted": "encrypted-payload",
        }
    )

    assert public["has_api_key"] is True
    assert public["credential_source"] == "environment"
    assert "public-runtime-secret" not in repr(public)
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
    return module.LLMSettingsRepository(database)


def test_repository_returns_environment_credential_status(monkeypatch):
    """Would catch provider responses leaking the environment value."""

    monkeypatch.setenv("INTERNAL_GATEWAY_KEY", "runtime-storage-secret")
    repository = _repository()

    provider = repository.create_provider(
        name="Internal Gateway",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="INTERNAL_GATEWAY_KEY",
    )
    settings = repository.settings_payload()
    effective = repository.effective_default()

    assert provider["has_api_key"] is True
    assert "api_key" not in provider
    assert "api_key_encrypted" not in provider
    assert settings["providers"][0]["has_api_key"] is True
    assert settings["providers"][0]["credential_status"] == "configured"
    assert "runtime-storage-secret" not in repr(settings)
    assert effective is None


def test_effective_default_reads_environment_and_ignores_legacy_ciphertext(monkeypatch):
    """Would catch runtime resolution falling back to the legacy encrypted column."""

    module = _settings_module()
    repository = _repository()
    monkeypatch.setenv("MODEL_GATEWAY_KEY", "environment-secret")
    provider = repository.create_provider(
        name="Gateway",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="MODEL_GATEWAY_KEY",
    )
    model = repository.create_model(provider["id"], model_id="model-a")
    with repository.database.transaction() as connection:
        connection.execute(
            module.llm_providers.update()
            .where(module.llm_providers.c.id == provider["id"])
            .values(api_key_encrypted="legacy-unreadable-value")
        )
    repository.set_default_model(model["id"])

    assert repository.effective_default()["api_key"] == "environment-secret"


def test_missing_environment_hides_models_and_blocks_default(monkeypatch):
    """Would catch Web and Worker disagreeing about whether credentials exist."""

    monkeypatch.delenv("MISSING_LLM_KEY", raising=False)
    repository = _repository()
    provider = repository.create_provider(
        name="Missing",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="MISSING_LLM_KEY",
    )
    model = repository.create_model(provider["id"], model_id="model-a")

    assert repository.settings_payload()["providers"][0]["credential_status"] == "missing"
    assert repository.available_models_payload()["items"] == []
    with pytest.raises(repository.conflict_error, match="MISSING_LLM_KEY"):
        repository.set_default_model(model["id"])


def test_repository_sets_an_enabled_model_as_effective_default(monkeypatch):
    """Would catch a default selection that does not drive runtime credentials."""

    monkeypatch.setenv("OPENAI_API_KEY", "runtime-secret")
    repository = _repository()
    provider = repository.create_provider(
        name="OpenAI",
        preset="openai",
        base_url="https://api.openai.com/v1",
        wire_api="responses",
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
        "api_key": "runtime-secret",
        "model": "gpt-5.6-sol",
        "wire_api": "responses",
        "allow_insecure_http": False,
    }


def test_repository_lists_and_resolves_enabled_models_without_exposing_secrets(
    monkeypatch,
):
    monkeypatch.setenv("CREATIVE_GATEWAY_KEY", "creative-runtime-secret")
    repository = _repository()
    provider = repository.create_provider(
        name="Creative Gateway",
        preset="custom",
        base_url="https://creative.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="CREATIVE_GATEWAY_KEY",
    )
    selected = repository.create_model(
        provider["id"], model_id="screenwriter-a", display_name="编剧模型 A"
    )
    disabled = repository.create_model(
        provider["id"], model_id="screenwriter-b", display_name="编剧模型 B"
    )
    repository.update_model(disabled["id"], is_enabled=False)
    repository.set_default_model(selected["id"])

    catalog = repository.available_models_payload()
    effective = repository.effective_model(selected["id"])

    assert catalog == {
        "default_model_record_id": selected["id"],
        "items": [
            {
                "id": selected["id"],
                "provider_id": provider["id"],
                "provider_name": "Creative Gateway",
                "model_id": "screenwriter-a",
                "display_name": "编剧模型 A",
                "label": "Creative Gateway / 编剧模型 A",
                "is_default": True,
            }
        ],
    }
    assert "creative-runtime-secret" not in repr(catalog)
    assert "base_url" not in repr(catalog)
    assert effective["api_key"] == "creative-runtime-secret"
    assert effective["model_record_id"] == selected["id"]
    assert effective["model"] == "screenwriter-a"
    assert effective["provider_display_name"] == "Creative Gateway"
    with pytest.raises(repository.conflict_error, match="模型已停用"):
        repository.effective_model(disabled["id"])


def test_repository_rejects_disabling_the_active_default(monkeypatch):
    """Would catch an update leaving the global default unusable."""

    monkeypatch.setenv("DEFAULT_PROVIDER_KEY", "default-secret")
    repository = _repository()
    provider = repository.create_provider(
        name="Default Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="DEFAULT_PROVIDER_KEY",
    )
    model = repository.create_model(provider["id"], model_id="model-a")
    repository.set_default_model(model["id"])

    with pytest.raises(repository.conflict_error, match="默认模型"):
        repository.update_model(model["id"], is_enabled=False)
    with pytest.raises(repository.conflict_error, match="默认模型"):
        repository.update_provider(provider["id"], is_enabled=False)


def test_repository_deletes_a_non_default_model_and_protects_the_default(monkeypatch):
    monkeypatch.setenv("DELETE_PROVIDER_KEY", "delete-secret")
    repository = _repository()
    provider = repository.create_provider(
        name="Delete Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="DELETE_PROVIDER_KEY",
    )
    default_model = repository.create_model(provider["id"], model_id="model-a")
    removable_model = repository.create_model(provider["id"], model_id="model-b")
    repository.set_default_model(default_model["id"])

    with pytest.raises(repository.conflict_error, match="默认模型"):
        repository.delete_model(default_model["id"])

    deleted = repository.delete_model(removable_model["id"])

    assert deleted["id"] == removable_model["id"]
    models = repository.settings_payload()["providers"][0]["models"]
    assert [model["id"] for model in models] == [default_model["id"]]
    with pytest.raises(ValueError, match="模型不存在"):
        repository.delete_model(removable_model["id"])


def test_repository_deletes_a_non_default_provider_and_all_of_its_models(monkeypatch):
    monkeypatch.setenv("DEFAULT_PROVIDER_KEY", "default-secret")
    monkeypatch.setenv("REMOVABLE_PROVIDER_KEY", "removable-secret")
    repository = _repository()
    default_provider = repository.create_provider(
        name="Default Provider",
        preset="custom",
        base_url="https://default.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="DEFAULT_PROVIDER_KEY",
    )
    default_model = repository.create_model(
        default_provider["id"], model_id="default-model"
    )
    repository.set_default_model(default_model["id"])
    removable_provider = repository.create_provider(
        name="Removable Provider",
        preset="custom",
        base_url="https://removable.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="REMOVABLE_PROVIDER_KEY",
    )
    repository.create_model(removable_provider["id"], model_id="model-a")
    repository.create_model(removable_provider["id"], model_id="model-b")

    with pytest.raises(repository.conflict_error, match="默认模型"):
        repository.delete_provider(default_provider["id"])

    deleted = repository.delete_provider(removable_provider["id"])

    assert deleted["id"] == removable_provider["id"]
    settings = repository.settings_payload()
    assert [provider["id"] for provider in settings["providers"]] == [
        default_provider["id"]
    ]
    with pytest.raises(ValueError, match="提供商不存在"):
        repository.delete_provider(removable_provider["id"])


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_repository_rejects_default_without_usable_credentials(monkeypatch, api_key):
    """Would catch saving a global default that every runtime call must reject."""

    monkeypatch.delenv("EMPTY_PROVIDER_KEY", raising=False)
    if api_key is not None:
        monkeypatch.setenv("EMPTY_PROVIDER_KEY", api_key)
    repository = _repository()
    provider = repository.create_provider(
        name="Credentialless Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="EMPTY_PROVIDER_KEY",
    )
    model = repository.create_model(provider["id"], model_id="model-a")

    with pytest.raises(repository.conflict_error, match="EMPTY_PROVIDER_KEY"):
        repository.set_default_model(model["id"])


def test_repository_rejects_changing_default_to_missing_environment(monkeypatch):
    """Would catch an update making the active default unusable."""

    monkeypatch.setenv("CURRENT_PROVIDER_KEY", "current-secret")
    monkeypatch.delenv("MISSING_PROVIDER_KEY", raising=False)
    repository = _repository()
    provider = repository.create_provider(
        name="Default Credential Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="CURRENT_PROVIDER_KEY",
    )
    model = repository.create_model(provider["id"], model_id="model-a")
    repository.set_default_model(model["id"])

    with pytest.raises(repository.conflict_error, match="MISSING_PROVIDER_KEY"):
        repository.update_provider(
            provider["id"], api_key_env_var="MISSING_PROVIDER_KEY"
        )


def test_synced_models_merge_without_removing_manual_or_missing_models(monkeypatch):
    """Would catch remote synchronization replacing the local model library."""

    monkeypatch.setenv("SYNC_PROVIDER_KEY", "sync-secret")
    repository = _repository()
    provider = repository.create_provider(
        name="Sync Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="SYNC_PROVIDER_KEY",
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


def test_legacy_import_is_idempotent_and_becomes_the_default(monkeypatch):
    """Would catch startup creating duplicate providers or failing to select the import."""

    monkeypatch.setenv("SUB2API_KEY", "legacy-environment-secret")
    repository = _repository()

    class LegacyConfig:
        LLM_PROVIDER = "sub2api"
        LLM_BASE_URL = "http://gateway.example.test/v1"
        LLM_API_KEY = "must-not-be-persisted"
        LLM_MODEL = "gpt-5.6-sol"
        LLM_WIRE_API = "responses"

    first = repository.import_legacy(LegacyConfig)
    second = repository.import_legacy(LegacyConfig)

    settings = repository.settings_payload()
    assert first["imported"] is True
    assert second["imported"] is False
    assert len(settings["providers"]) == 1
    assert settings["providers"][0]["api_key_env_var"] == "SUB2API_KEY"
    assert settings["providers"][0]["models"][0]["source"] == "imported"
    assert repository.effective_default()["api_key"] == "legacy-environment-secret"
