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

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.schema import Principal
from identity.dependencies import current_principal

from tests.analysis.test_llm_settings import _repository


def _api_module():
    spec = importlib.util.find_spec("analysis.llm_settings_api")
    assert spec is not None, "analysis.llm_settings_api must expose the management service"
    return importlib.import_module("analysis.llm_settings_api")


@pytest.mark.asyncio
async def test_model_sync_merges_openai_compatible_model_list():
    """Would catch /models responses being ignored or local models being replaced."""

    repository = _repository()
    provider = repository.create_provider(
        name="Model Catalog",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key="sync-secret",
    )
    repository.create_model(provider["id"], model_id="manual-model")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://llm.example.test/v1/models")
        assert request.headers["Authorization"] == "Bearer sync-secret"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"id": "remote-a"}, {"id": "remote-b"}],
            },
        )

    service = _api_module().LLMSettingsService(
        repository, transport=httpx.MockTransport(handler)
    )

    result = await service.sync_models(provider["id"])

    assert result == {"added": 2, "updated": 0, "total": 3}
    models = repository.settings_payload()["providers"][0]["models"]
    assert {model["model_id"] for model in models} == {
        "manual-model",
        "remote-a",
        "remote-b",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wire_api", "response_json", "expected_path"),
    [
        (
            "responses",
            {"output_text": "OK"},
            "/v1/responses",
        ),
        (
            "chat_completions",
            {"choices": [{"message": {"content": "OK"}}]},
            "/v1/chat/completions",
        ),
    ],
)
async def test_connection_uses_unsaved_draft_and_returns_latency_without_secret(
    wire_api, response_json, expected_path
):
    """Would catch the test action persisting drafts or exposing credentials."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=response_json)

    service = _api_module().LLMSettingsService(
        _repository(), transport=httpx.MockTransport(handler)
    )

    result = await service.test_connection(
        base_url="https://draft.example.test/v1",
        api_key="draft-secret",
        model_id="draft-model",
        wire_api=wire_api,
    )

    assert requests[0].url.path == expected_path
    assert result["status"] == "success"
    assert result["response_preview"] == "OK"
    assert result["latency_ms"] >= 0
    assert "draft-secret" not in repr(result)


class _RecordingAudit:
    def __init__(self) -> None:
        self.events = []

    def record(self, **event):
        self.events.append(event)


def _admin() -> Principal:
    return Principal(
        user_id="admin-1",
        username="admin",
        display_name="Admin",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="session-1",
    )


def _non_admin() -> Principal:
    return Principal(
        user_id="editor-1",
        username="editor",
        display_name="Editor",
        role_codes=("content_editor",),
        permissions=frozenset(),
        data_scope="task",
        session_id="session-2",
    )


def test_llm_routes_reject_non_admin_even_when_config_page_is_granted():
    """Would catch page grants accidentally widening secret-management access."""

    class GrantedPageAccess:
        @staticmethod
        def can_call_page_api(principal, method, path):
            del principal, method, path
            return True

    module = _api_module()
    app = FastAPI()
    app.state.page_access_service = GrantedPageAccess()
    app.dependency_overrides[current_principal] = _non_admin
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(_repository()), _RecordingAudit()
        )
    )

    response = TestClient(app).get("/api/llm/settings")

    assert response.status_code == 403


def test_provider_and_model_routes_never_return_or_audit_api_key():
    """Would catch a management route leaking the submitted credential."""

    module = _api_module()
    repository = _repository()
    service = module.LLMSettingsService(repository)
    audit = _RecordingAudit()
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            service,
            audit,
            administrator_dependency=lambda: _admin(),
        )
    )
    client = TestClient(app)

    provider_response = client.post(
        "/api/llm/providers",
        json={
            "name": "API Provider",
            "preset": "custom",
            "base_url": "https://llm.example.test/v1",
            "wire_api": "chat_completions",
            "api_key": "route-secret",
            "is_enabled": True,
        },
    )
    provider = provider_response.json()
    model_response = client.post(
        f"/api/llm/providers/{provider['id']}/models",
        json={"model_id": "route-model", "display_name": "Route Model"},
    )
    default_response = client.put(
        "/api/llm/default-model", json={"model_id": model_response.json()["id"]}
    )

    assert provider_response.status_code == 201
    assert model_response.status_code == 201
    assert default_response.status_code == 200
    assert provider["has_api_key"] is True
    assert "route-secret" not in repr(provider)
    assert "route-secret" not in repr(audit.events)
    assert [event["action"] for event in audit.events] == [
        "llm.provider.create",
        "llm.model.create",
        "llm.default_model.update",
    ]


def test_settings_route_exposes_presets_and_current_default():
    """Would catch the UI loading without enough data to render its master-detail state."""

    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Settings Provider",
        preset="openai",
        base_url="https://api.openai.com/v1",
        wire_api="responses",
        api_key="secret",
    )
    model = repository.create_model(provider["id"], model_id="gpt-test")
    repository.set_default_model(model["id"])
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )

    response = TestClient(app).get("/api/llm/settings")

    assert response.status_code == 200
    assert response.json()["default_model_id"] == model["id"]
    assert response.json()["presets"]["minimax"]["wire_api"] == "chat_completions"


def test_route_can_test_a_new_key_before_saving_it_to_an_existing_provider():
    """Would catch stored-key lookup blocking an explicitly supplied draft key."""

    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Missing Credential",
        preset="custom",
        base_url="https://saved.example.test/v1",
        wire_api="chat_completions",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(
            "https://draft.example.test/v1/chat/completions"
        )
        assert request.headers["Authorization"] == "Bearer draft-key"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}}]}
        )

    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(
                repository, transport=httpx.MockTransport(handler)
            ),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )

    response = TestClient(app).post(
        "/api/llm/test",
        json={
            "provider_id": provider["id"],
            "base_url": "https://draft.example.test/v1",
            "api_key": "draft-key",
            "model_id": "draft-model",
            "wire_api": "chat_completions",
        },
    )

    assert response.status_code == 200
    assert response.json()["response_preview"] == "OK"


def test_route_treats_a_whitespace_test_key_as_omitted():
    """Would catch direct API callers losing the saved key to whitespace input."""

    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Saved Credential",
        preset="custom",
        base_url="https://saved.example.test/v1",
        wire_api="chat_completions",
        api_key="stored-key",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer stored-key"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}}]}
        )

    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(
                repository, transport=httpx.MockTransport(handler)
            ),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )

    response = TestClient(app).post(
        "/api/llm/test",
        json={
            "provider_id": provider["id"],
            "api_key": "   ",
            "model_id": "saved-model",
        },
    )

    assert response.status_code == 200
