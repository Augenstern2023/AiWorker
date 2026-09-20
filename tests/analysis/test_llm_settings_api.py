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
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.schema import Principal
from identity.dependencies import current_principal
from analysis.llm import OpenAICompatibleLLMProvider

from tests.analysis.test_llm_settings import MemoryCredentialStore, _repository


class _LocalLLMHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(content_length)
        payload = {"choices": [{"message": {"content": "OK"}}]}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


@pytest.fixture
def local_llm_base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalLLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _configure_unreachable_system_proxy(monkeypatch) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")


def _api_module():
    spec = importlib.util.find_spec("analysis.llm_settings_api")
    assert spec is not None, "analysis.llm_settings_api must expose the management service"
    return importlib.import_module("analysis.llm_settings_api")


def _loopback_test_client(app: FastAPI) -> TestClient:
    """Give TestClient the loopback peer address its base URL represents."""

    @app.middleware("http")
    async def represent_loopback_client(request, call_next):
        request.scope["client"] = ("127.0.0.1", 50000)
        return await call_next(request)

    return TestClient(app, base_url="http://127.0.0.1")


@pytest.mark.parametrize("case", ["credential-array", "test-missing-model", "nested-extra"])
def test_secret_route_validation_never_reflects_any_request_input(case):
    """Catch secrets reflected through whole-body or unrelated-field 422 inputs."""
    module = _api_module()
    repository = _repository(credential_store=MemoryCredentialStore())
    provider = repository.create_provider(
        name="Validation", preset="custom", base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
    )
    app = FastAPI()
    app.include_router(module.build_llm_settings_router(
        module.LLMSettingsService(repository), _RecordingAudit(),
        administrator_dependency=lambda: _admin(),
    ))
    client = _loopback_test_client(app)
    secret = "validation-sentinel"
    if case == "credential-array":
        response = client.put(
            f"/api/llm/providers/{provider['id']}/credential", json=[{"api_key": secret}],
        )
    elif case == "test-missing-model":
        response = client.post("/api/llm/test", json={"api_key": secret})
    else:
        response = client.put(
            f"/api/llm/providers/{provider['id']}/credential",
            json={"api_key": secret, "extra": {"nested": {"api_key": secret}}},
        )
    assert response.status_code == 422
    assert secret not in response.text
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize(("method", "suffix", "body"), [
    ("PUT", "/credential", {"api_key": "missing-provider-sentinel"}),
    ("DELETE", "/credential", None),
    ("PUT", "", {"name": "Missing"}),
    ("DELETE", "", None),
    ("POST", "/models", {"model_id": "test-model"}),
    ("POST", "/models/sync", None),
    ("POST", "/test", {"model_id": "test-model"}),
])
def test_provider_routes_return_404_when_provider_is_missing(method, suffix, body):
    """Catch missing resources being mapped to a generic bad-request response."""
    module = _api_module()
    app = FastAPI()
    audit = _RecordingAudit()
    app.include_router(module.build_llm_settings_router(
        module.LLMSettingsService(_repository(credential_store=MemoryCredentialStore())),
        audit, administrator_dependency=lambda: _admin(),
    ))
    provider_id = "10000000-0000-0000-0000-000000000000"
    url = f"/api/llm/providers/{provider_id}{suffix}"
    if suffix == "/test":
        url = "/api/llm/test"
        body = {**body, "provider_id": provider_id}
    response = _loopback_test_client(app).request(method, url, json=body)
    assert response.status_code == 404
    assert "missing-provider-sentinel" not in response.text
    assert audit.events == []


@pytest.mark.parametrize("transient", [False, True], ids=["saved", "transient"])
@pytest.mark.parametrize("wire_api", ["chat_completions", "responses"])
def test_connection_api_discards_upstream_authorization_echo(transient, wire_api):
    """Catch arbitrary upstream content exposing a saved or temporary credential."""
    module = _api_module()
    vault = MemoryCredentialStore()
    repository = _repository(credential_store=vault)
    provider = repository.create_provider(
        name="Echo", preset="custom", base_url="https://llm.example.test/v1",
        wire_api=wire_api,
    )
    repository.replace_api_key(provider["id"], "saved-echo-sentinel")

    def handler(request):
        echoed = request.headers["Authorization"]
        return httpx.Response(200, json={
            "output_text": echoed, "choices": [{"message": {"content": echoed}}],
        })

    audit = _RecordingAudit()
    app = FastAPI()
    app.include_router(module.build_llm_settings_router(
        module.LLMSettingsService(repository, transport=httpx.MockTransport(handler)),
        audit, administrator_dependency=lambda: _admin(),
    ))
    body = {"provider_id": provider["id"], "model_id": "test-model"}
    if transient:
        body["api_key"] = "transient-echo-sentinel"
    response = _loopback_test_client(app).post("/api/llm/test", json=body)
    assert response.status_code == 200
    for secret in ("saved-echo-sentinel", "transient-echo-sentinel"):
        assert secret not in response.text
        assert secret not in repr(audit.events)
    assert set(response.json()) == {"status", "message", "latency_ms"}
    assert response.json()["status"] == "success"


@pytest.mark.parametrize("environment_present", [False, True], ids=["missing-env", "old-env"])
def test_connection_draft_environment_cannot_override_vault(monkeypatch, environment_present):
    """Catch draft env taking priority over a saved vault value or blocking it."""
    module = _api_module()
    monkeypatch.delenv("DRAFT_FALLBACK_KEY", raising=False)
    if environment_present:
        monkeypatch.setenv("DRAFT_FALLBACK_KEY", "old-env-sentinel")
    repository = _repository(credential_store=MemoryCredentialStore())
    provider = repository.create_provider(
        name="Vault Priority", preset="custom", base_url="https://llm.example.test/v1",
        wire_api="chat_completions", api_key_env_var="DRAFT_FALLBACK_KEY",
    )
    repository.replace_api_key(provider["id"], "vault-priority-sentinel")
    sent_headers = []

    def handler(request):
        sent_headers.append(request.headers["Authorization"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    app = FastAPI()
    app.include_router(module.build_llm_settings_router(
        module.LLMSettingsService(repository, transport=httpx.MockTransport(handler)),
        _RecordingAudit(), administrator_dependency=lambda: _admin(),
    ))
    response = _loopback_test_client(app).post("/api/llm/test", json={
        "provider_id": provider["id"], "model_id": "test-model",
        "api_key_env_var": "DRAFT_FALLBACK_KEY",
    })
    assert response.status_code == 200
    assert sent_headers == ["Bearer vault-priority-sentinel"]


@pytest.mark.asyncio
async def test_connection_malformed_response_uses_safe_error_without_cause():
    """Catch upstream refusal details leaking through parser exception chains."""
    module = _api_module()

    def handler(request):
        return httpx.Response(200, json={
            "status": "incomplete",
            "incomplete_details": {"reason": request.headers["Authorization"]},
        })

    service = module.LLMSettingsService(_repository(), transport=httpx.MockTransport(handler))
    with pytest.raises(module.LLMSettingsError, match="模型服务返回格式无效") as caught:
        await service.test_connection(
            base_url="https://llm.example.test/v1", api_key="malformed-sentinel",
            model_id="test-model", wire_api="responses",
        )
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
async def test_model_sync_merges_openai_compatible_model_list(monkeypatch):
    """Would catch /models responses being ignored or local models being replaced."""

    monkeypatch.setenv("SYNC_API_KEY", "sync-secret")
    repository = _repository()
    provider = repository.create_provider(
        name="Model Catalog",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="SYNC_API_KEY",
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
    assert "response_preview" not in result
    assert result["latency_ms"] >= 0
    assert "draft-secret" not in repr(result)


@pytest.mark.asyncio
async def test_connection_ignores_unreachable_system_proxy(
    monkeypatch, local_llm_base_url
):
    """Model checks must not inherit crawler or desktop proxy variables."""

    _configure_unreachable_system_proxy(monkeypatch)
    service = _api_module().LLMSettingsService(_repository(), timeout_seconds=1)

    result = await service.test_connection(
        base_url=local_llm_base_url,
        api_key="test-secret",
        model_id="test-model",
        wire_api="chat_completions",
    )

    assert result["status"] == "success"
    assert "response_preview" not in result


@pytest.mark.asyncio
async def test_generation_ignores_unreachable_system_proxy(
    monkeypatch, local_llm_base_url
):
    """Free generation must connect directly even when a system proxy is stale."""

    _configure_unreachable_system_proxy(monkeypatch)
    provider = OpenAICompatibleLLMProvider(
        base_url=local_llm_base_url,
        api_key="test-secret",
        model="test-model",
        wire_api="chat_completions",
        timeout=1,
        max_retries=0,
    )

    result = await provider.complete_text([{"role": "user", "content": "test"}])

    assert result == "OK"


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


def test_available_models_route_allows_authenticated_creator_without_secrets(
    monkeypatch,
):
    monkeypatch.setenv("CATALOG_API_KEY", "catalog-secret")
    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Creative Provider",
        preset="custom",
        base_url="https://creative.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="CATALOG_API_KEY",
    )
    model = repository.create_model(
        provider["id"], model_id="creative-model", display_name="创作模型"
    )
    repository.set_default_model(model["id"])
    app = FastAPI()
    app.dependency_overrides[current_principal] = _non_admin
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository), _RecordingAudit()
        )
    )

    response = TestClient(app).get("/api/llm/available-models")

    assert response.status_code == 200
    assert response.json()["default_model_record_id"] == model["id"]
    assert response.json()["items"][0]["label"] == (
        "Creative Provider / 创作模型"
    )
    assert "catalog-secret" not in response.text
    assert "creative.example.test" not in response.text


def test_provider_routes_accept_environment_variable_and_reject_plaintext_key(
    monkeypatch,
):
    """Would catch plaintext credentials being accepted, echoed, or audited."""

    monkeypatch.setenv("ROUTE_LLM_KEY", "route-secret")
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
            "api_key_env_var": "ROUTE_LLM_KEY",
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
    rejected = client.post(
        "/api/llm/providers",
        json={
            "name": "Legacy",
            "preset": "custom",
            "base_url": "https://llm.example.test/v1",
            "wire_api": "chat_completions",
            "api_key": "must-not-be-accepted",
        },
    )

    assert provider_response.status_code == 201
    assert model_response.status_code == 201
    assert default_response.status_code == 200
    assert rejected.status_code == 400
    assert provider["has_api_key"] is True
    assert provider["api_key_env_var"] == "ROUTE_LLM_KEY"
    assert "route-secret" not in repr(provider)
    assert "route-secret" not in repr(audit.events)
    assert "must-not-be-accepted" not in rejected.text
    assert [event["action"] for event in audit.events] == [
        "llm.provider.create",
        "llm.model.create",
        "llm.default_model.update",
    ]


def test_provider_metadata_update_rejects_plaintext_key():
    """Would catch the credential route being bypassed through provider metadata."""

    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Metadata Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
    )
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )

    response = TestClient(app).put(
        f"/api/llm/providers/{provider['id']}",
        json={"api_key": "metadata-secret"},
    )

    assert response.status_code == 400
    assert "metadata-secret" not in response.text


def test_system_administrator_rotates_credential_without_secret_exposure(
    monkeypatch,
):
    """Would catch rotation leaking a key or returning a cached credential."""

    monkeypatch.setenv("ROTATION_API_FALLBACK", "environment-secret")
    module = _api_module()
    vault = MemoryCredentialStore()
    repository = _repository(credential_store=vault)
    provider = repository.create_provider(
        name="Rotatable Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="ROTATION_API_FALLBACK",
    )
    audit = _RecordingAudit()
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            audit,
            administrator_dependency=lambda: _admin(),
        )
    )

    response = _loopback_test_client(app).put(
        f"/api/llm/providers/{provider['id']}/credential",
        json={"api_key": "rotated-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider_id": provider["id"],
        "credential_status": "configured",
        "credential_source": "credential_store",
    }
    assert repository.provider_connection(provider["id"])["api_key"] == "rotated-secret"
    assert audit.events[-1]["action"] == "llm.provider.credential.replace"
    assert audit.events[-1]["before"] == {
        "provider_id": provider["id"],
        "credential_status": "configured",
        "credential_source": "environment",
    }
    assert audit.events[-1]["after"] == response.json()
    assert "rotated-secret" not in response.text
    assert "rotated-secret" not in repr(audit.events)


def test_non_loopback_http_cannot_submit_provider_secret():
    """Would catch a remote HTTP request being allowed to carry a provider key."""

    module = _api_module()
    repository = _repository(credential_store=MemoryCredentialStore())
    provider = repository.create_provider(
        name="Transport Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
    )
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )

    response = TestClient(app, base_url="http://testserver").put(
        f"/api/llm/providers/{provider['id']}/credential",
        json={"api_key": "never-log-this"},
    )

    assert response.status_code == 400
    assert "本机或 HTTPS" in response.json()["detail"]
    assert "never-log-this" not in response.text


def test_https_can_submit_provider_secret():
    """Would catch the transport guard rejecting a secure remote request."""

    module = _api_module()
    vault = MemoryCredentialStore()
    repository = _repository(credential_store=vault)
    provider = repository.create_provider(
        name="HTTPS Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
    )
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )

    response = TestClient(app, base_url="https://admin.example.test").put(
        f"/api/llm/providers/{provider['id']}/credential",
        json={"api_key": "https-only-secret"},
    )

    assert response.status_code == 200
    assert vault.get(provider["id"]) == "https-only-secret"
    assert "https-only-secret" not in response.text


def test_non_administrator_cannot_rotate_provider_credential():
    """Would catch ordinary users gaining access to the credential route."""

    module = _api_module()
    repository = _repository(credential_store=MemoryCredentialStore())
    provider = repository.create_provider(
        name="Restricted Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = _non_admin
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository), _RecordingAudit()
        )
    )

    response = _loopback_test_client(app).put(
        f"/api/llm/providers/{provider['id']}/credential",
        json={"api_key": "forbidden-secret"},
    )

    assert response.status_code == 403
    assert "forbidden-secret" not in response.text


@pytest.mark.parametrize("api_key", ["", " "])
def test_provider_credential_requires_non_empty_secret(api_key):
    """Would catch the credential route accepting an empty key."""

    module = _api_module()
    repository = _repository(credential_store=MemoryCredentialStore())
    provider = repository.create_provider(
        name="Validated Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
    )
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )

    response = _loopback_test_client(app).put(
        f"/api/llm/providers/{provider['id']}/credential",
        json={"api_key": api_key},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "API Key 格式无效"}


def test_credential_store_failure_is_unavailable_without_secret_exposure():
    """Would catch a vault write failure becoming a client error or leaking its key."""

    module = _api_module()

    class FailingStore(MemoryCredentialStore):
        def set(self, provider_id: str, api_key: str) -> None:
            del provider_id, api_key
            raise module.LLMCredentialStoreError("系统凭据库不可用")

    repository = _repository(credential_store=FailingStore())
    provider = repository.create_provider(
        name="Unavailable Vault",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
    )
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )

    response = _loopback_test_client(app).put(
        f"/api/llm/providers/{provider['id']}/credential",
        json={"api_key": "write-failure-secret"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "系统凭据库不可用"
    assert "write-failure-secret" not in response.text


def test_clearing_credential_falls_back_to_environment_without_secret_exposure(
    monkeypatch,
):
    """Would catch clearing a vault credential preventing environment fallback."""

    monkeypatch.setenv("CLEAR_ROUTE_FALLBACK", "environment-fallback-secret")
    module = _api_module()
    vault = MemoryCredentialStore()
    repository = _repository(credential_store=vault)
    provider = repository.create_provider(
        name="Clearable Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="CLEAR_ROUTE_FALLBACK",
    )
    repository.replace_api_key(provider["id"], "stored-secret")
    audit = _RecordingAudit()
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            audit,
            administrator_dependency=lambda: _admin(),
        )
    )

    response = _loopback_test_client(app).delete(
        f"/api/llm/providers/{provider['id']}/credential"
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider_id": provider["id"],
        "credential_status": "configured",
        "credential_source": "environment",
    }
    assert repository.provider_connection(provider["id"])["api_key"] == (
        "environment-fallback-secret"
    )
    assert audit.events[-1]["action"] == "llm.provider.credential.clear"
    assert audit.events[-1]["before"] == {
        "provider_id": provider["id"],
        "credential_status": "configured",
        "credential_source": "credential_store",
    }
    assert audit.events[-1]["after"] == response.json()
    assert "stored-secret" not in response.text
    assert "stored-secret" not in repr(audit.events)


def test_connection_test_accepts_transient_secret_without_persisting_it():
    """Would catch a submitted test credential being retained by the repository."""

    module = _api_module()
    vault = MemoryCredentialStore()
    repository = _repository(credential_store=vault)
    provider = repository.create_provider(
        name="Transient Test Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer transient-secret"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}}]}
        )

    audit = _RecordingAudit()
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(
                repository, transport=httpx.MockTransport(handler)
            ),
            audit,
            administrator_dependency=lambda: _admin(),
        )
    )

    response = _loopback_test_client(app).post(
        "/api/llm/test",
        json={
            "provider_id": provider["id"],
            "api_key": "transient-secret",
            "model_id": "test-model",
        },
    )

    assert response.status_code == 200
    assert vault.get(provider["id"]) is None
    assert "transient-secret" not in response.text
    assert "transient-secret" not in repr(audit.events)


@pytest.mark.parametrize("api_key", ["", " "])
def test_connection_test_rejects_blank_transient_secret_without_reflection(api_key):
    """Would catch invalid transient keys reaching the connection service."""

    module = _api_module()
    repository = _repository(credential_store=MemoryCredentialStore())
    provider = repository.create_provider(
        name="Transient Validation Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
    )
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )

    response = _loopback_test_client(app).post(
        "/api/llm/test",
        json={
            "provider_id": provider["id"],
            "api_key": api_key,
            "model_id": "test-model",
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "API Key 格式无效"}


def test_model_delete_route_removes_non_default_and_rejects_default(monkeypatch):
    monkeypatch.setenv("DELETE_API_KEY", "delete-secret")
    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Delete API Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="DELETE_API_KEY",
    )
    default_model = repository.create_model(provider["id"], model_id="model-a")
    removable_model = repository.create_model(provider["id"], model_id="model-b")
    repository.set_default_model(default_model["id"])
    audit = _RecordingAudit()
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            audit,
            administrator_dependency=lambda: _admin(),
        )
    )
    client = TestClient(app)

    default_response = client.delete(f"/api/llm/models/{default_model['id']}")
    delete_response = client.delete(f"/api/llm/models/{removable_model['id']}")

    assert default_response.status_code == 409
    assert "默认模型" in default_response.json()["detail"]
    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "status": "success",
        "model_id": removable_model["id"],
    }
    assert audit.events[-1]["action"] == "llm.model.delete"
    assert audit.events[-1]["before"]["id"] == removable_model["id"]
    assert audit.events[-1]["after"] == delete_response.json()
    remaining_ids = {
        model["id"]
        for model in client.get("/api/llm/settings").json()["providers"][0]["models"]
    }
    assert remaining_ids == {default_model["id"]}


def test_provider_delete_route_removes_non_default_and_rejects_default(monkeypatch):
    monkeypatch.setenv("DEFAULT_API_KEY", "default-secret")
    monkeypatch.setenv("REMOVABLE_API_KEY", "removable-secret")
    module = _api_module()
    repository = _repository()
    default_provider = repository.create_provider(
        name="Default API Provider",
        preset="custom",
        base_url="https://default.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="DEFAULT_API_KEY",
    )
    default_model = repository.create_model(
        default_provider["id"], model_id="default-model"
    )
    repository.set_default_model(default_model["id"])
    removable_provider = repository.create_provider(
        name="Delete API Provider",
        preset="custom",
        base_url="https://delete.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="REMOVABLE_API_KEY",
    )
    repository.create_model(removable_provider["id"], model_id="unused-model")
    audit = _RecordingAudit()
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            audit,
            administrator_dependency=lambda: _admin(),
        )
    )
    client = TestClient(app)

    default_response = client.delete(
        f"/api/llm/providers/{default_provider['id']}"
    )
    delete_response = client.delete(
        f"/api/llm/providers/{removable_provider['id']}"
    )

    assert default_response.status_code == 409
    assert "默认模型" in default_response.json()["detail"]
    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "status": "success",
        "provider_id": removable_provider["id"],
    }
    assert audit.events[-1]["action"] == "llm.provider.delete"
    assert audit.events[-1]["before"]["id"] == removable_provider["id"]
    assert audit.events[-1]["after"] == delete_response.json()
    remaining_ids = {
        provider["id"]
        for provider in client.get("/api/llm/settings").json()["providers"]
    }
    assert remaining_ids == {default_provider["id"]}


def test_settings_route_exposes_presets_and_current_default(monkeypatch):
    """Would catch the UI loading without enough data to render its master-detail state."""

    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Settings Provider",
        preset="openai",
        base_url="https://api.openai.com/v1",
        wire_api="responses",
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


def test_model_routes_accept_update_and_clear_max_output_tokens(monkeypatch):
    monkeypatch.setenv("TOKEN_ROUTE_KEY", "route-secret")
    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Token Route Provider",
        preset="custom",
        base_url="https://llm.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="TOKEN_ROUTE_KEY",
    )
    app = FastAPI()
    app.include_router(
        module.build_llm_settings_router(
            module.LLMSettingsService(repository),
            _RecordingAudit(),
            administrator_dependency=lambda: _admin(),
        )
    )
    client = TestClient(app)

    created = client.post(
        f"/api/llm/providers/{provider['id']}/models",
        json={"model_id": "token-model", "max_output_tokens": 16384},
    )
    unchanged = client.put(
        f"/api/llm/models/{created.json()['id']}",
        json={"display_name": "Renamed model"},
    )
    updated = client.put(
        f"/api/llm/models/{created.json()['id']}",
        json={"max_output_tokens": 0},
    )
    cleared = client.put(
        f"/api/llm/models/{created.json()['id']}",
        json={"max_output_tokens": None},
    )
    rejected = client.put(
        f"/api/llm/models/{created.json()['id']}",
        json={"max_output_tokens": -1},
    )

    assert created.status_code == 201
    assert created.json()["max_output_tokens"] == 16384
    assert unchanged.status_code == 200
    assert unchanged.json()["max_output_tokens"] == 16384
    assert updated.status_code == 200
    assert updated.json()["max_output_tokens"] == 0
    assert cleared.status_code == 200
    assert cleared.json()["max_output_tokens"] is None
    assert rejected.status_code == 422


def test_route_can_test_an_environment_variable_before_saving_provider_changes(
    monkeypatch,
):
    """Would catch draft connection tests reading stored or submitted plaintext."""

    monkeypatch.delenv("MISSING_CREDENTIAL_KEY", raising=False)
    monkeypatch.setenv("DRAFT_API_KEY", "draft-key")
    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Missing Credential",
        preset="custom",
        base_url="https://saved.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="MISSING_CREDENTIAL_KEY",
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
            "api_key_env_var": "DRAFT_API_KEY",
            "model_id": "draft-model",
            "wire_api": "chat_completions",
        },
    )

    assert response.status_code == 200
    assert "response_preview" not in response.json()


def test_route_uses_saved_provider_environment_variable(monkeypatch):
    """Would catch saved-provider tests bypassing repository credential resolution."""

    monkeypatch.setenv("STORED_API_KEY", "stored-key")
    module = _api_module()
    repository = _repository()
    provider = repository.create_provider(
        name="Saved Credential",
        preset="custom",
        base_url="https://saved.example.test/v1",
        wire_api="chat_completions",
        api_key_env_var="STORED_API_KEY",
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
            "model_id": "saved-model",
        },
    )

    assert response.status_code == 200
@pytest.mark.asyncio
@pytest.mark.parametrize("wire_api", ["responses", "chat_completions"])
async def test_connection_accepts_a_short_probe_truncated_by_output_budget(wire_api):
    response = (
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output_text": "O"}
        if wire_api == "responses"
        else {"choices": [{"message": {"content": "O"}, "finish_reason": "length"}]}
    )
    service = _api_module().LLMSettingsService(
        _repository(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    )
    result = await service.test_connection(
        base_url="https://draft.example.test/v1", api_key="secret",
        model_id="model", wire_api=wire_api,
    )
    assert result["status"] == "success"
    assert "response_preview" not in result
