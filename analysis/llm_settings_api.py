# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from analysis.llm import LLMError, parse_chat_completion_text, parse_responses_text
from analysis.llm_credentials import LLMCredentialStoreError
from analysis.llm_settings import (
    LLMProviderNotFound,
    LLMSecretError,
    LLMSettingsConflict,
    LLMSettingsError,
    LLMSettingsRepository,
    environment_api_key,
    validate_llm_base_url,
)
from identity.dependencies import current_principal
from identity.schema import Principal


PresetName = Literal["openai", "deepseek", "minimax", "sub2api", "custom"]
WireAPI = Literal["responses", "chat_completions"]


async def require_system_administrator(
    principal: Principal = Depends(current_principal),
) -> Principal:
    if not principal.is_system_admin:
        raise HTTPException(status_code=403, detail="仅系统管理员可管理 LLM 设置")
    return principal


class LLMSettingsRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")


class ProviderCreateRequest(LLMSettingsRequest):
    name: str = Field(min_length=1, max_length=120)
    preset: PresetName
    base_url: str = Field(min_length=1, max_length=2048)
    wire_api: WireAPI
    api_key_env_var: str = Field(default="", max_length=255)
    api_key: str | None = Field(
        default=None, max_length=4096, exclude=True, repr=False
    )
    is_enabled: bool = True


class ProviderUpdateRequest(LLMSettingsRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    preset: PresetName | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    wire_api: WireAPI | None = None
    api_key_env_var: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(
        default=None, max_length=4096, exclude=True, repr=False
    )
    clear_api_key: bool | None = Field(default=None, exclude=True, repr=False)
    is_enabled: bool | None = None


class ModelCreateRequest(LLMSettingsRequest):
    model_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(default="", max_length=255)
    is_enabled: bool = True


class ModelUpdateRequest(LLMSettingsRequest):
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    is_enabled: bool | None = None


class DefaultModelRequest(LLMSettingsRequest):
    model_id: str = Field(min_length=1, max_length=64)


class ProviderCredentialRequest(LLMSettingsRequest):
    api_key: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("api_key")
    @classmethod
    def reject_blank_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API Key 不能为空")
        return value


class ConnectionTestRequest(LLMSettingsRequest):
    provider_id: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key_env_var: str | None = Field(default=None, max_length=255)
    api_key: SecretStr | None = Field(
        default=None, min_length=1, max_length=4096, exclude=True, repr=False
    )
    model_id: str = Field(min_length=1, max_length=255)
    wire_api: WireAPI | None = None

    @field_validator("api_key")
    @classmethod
    def reject_blank_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("API Key 不能为空")
        return value


def reject_legacy_api_key_fields(payload: LLMSettingsRequest) -> None:
    forbidden = {"api_key", "clear_api_key"} & payload.model_fields_set
    if forbidden:
        raise HTTPException(
            status_code=400,
            detail="不再接受 API Key；请配置 API Key 环境变量名称",
        )


def require_secure_secret_transport(request: Request) -> None:
    host = request.client.host if request.client else ""
    if request.url.scheme != "https" and host not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise HTTPException(
            status_code=400,
            detail="API Key 只能通过本机或 HTTPS 提交",
        )


class SecretSafeRequestValidationRoute(APIRoute):
    """Avoid reflecting submitted secrets in FastAPI validation responses."""

    def get_route_handler(self) -> Callable[..., Any]:
        route_handler = super().get_route_handler()

        async def secret_safe_handler(request: Request) -> Any:
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                # Any field or whole-body error can embed the original secret.
                # Return fixed messages without inputs, nested contexts or links.
                detail = "请求格式无效"
                if any(
                    "api_key" in error.get("loc", ()) for error in exc.errors()
                ):
                    detail = "API Key 格式无效"
                raise HTTPException(status_code=422, detail=detail) from None

        return secret_safe_handler


class LLMSettingsService:
    def __init__(
        self,
        repository: LLMSettingsRepository,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.repository = repository
        self.transport = transport
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    async def sync_models(self, provider_id: str) -> dict[str, int]:
        connection = self.repository.provider_connection(provider_id)
        if not connection["is_enabled"]:
            raise LLMSettingsError("请先启用提供商再同步模型")
        endpoint = f"{connection['base_url'].rstrip('/')}/models"
        payload = await self._request_json(
            "GET",
            endpoint,
            api_key=connection["api_key"],
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise LLMSettingsError("模型列表响应缺少 data 数组，请改用手工新增")
        model_ids = [
            str(row.get("id") or "").strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        ]
        if not model_ids:
            raise LLMSettingsError("模型列表为空，请改用手工新增")
        return self.repository.merge_synced_models(provider_id, model_ids)

    async def test_connection(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        wire_api: str,
    ) -> dict[str, Any]:
        normalized_url = validate_llm_base_url(base_url)
        normalized_key = str(api_key or "").strip()
        normalized_model = str(model_id or "").strip()
        if not normalized_key:
            raise LLMSettingsError("API Key 不能为空")
        if not normalized_model:
            raise LLMSettingsError("测试模型 ID 不能为空")
        if wire_api not in {"responses", "chat_completions"}:
            raise LLMSettingsError("不支持的调用协议")

        if wire_api == "responses":
            endpoint = f"{normalized_url}/responses"
            body = {
                "model": normalized_model,
                "input": [{"role": "user", "content": "仅回复 OK"}],
                "max_output_tokens": 16,
            }
        else:
            endpoint = f"{normalized_url}/chat/completions"
            body = {
                "model": normalized_model,
                "messages": [{"role": "user", "content": "仅回复 OK"}],
                "max_tokens": 16,
            }
        started = time.perf_counter()
        payload = await self._request_json(
            "POST", endpoint, api_key=normalized_key, json=body
        )
        try:
            # Validate the protocol, but never return untrusted upstream text.
            if wire_api == "responses":
                parse_responses_text(payload)
            else:
                parse_chat_completion_text(payload)
        except (LLMError, TypeError, ValueError, AttributeError):
            raise LLMSettingsError("模型服务返回格式无效") from None
        return {
            "status": "success",
            "message": "模型连接测试成功",
            "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
        }

    async def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        api_key: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {api_key}"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = await client.request(
                    method, endpoint, headers=headers, json=json
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMSettingsError("模型服务请求超时") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMSettingsError(
                f"模型服务返回 HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMSettingsError("无法连接模型服务端点") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMSettingsError("模型服务返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise LLMSettingsError("模型服务返回格式无效")
        return payload


def build_llm_settings_router(
    service: LLMSettingsService,
    audit_service: Any,
    *,
    administrator_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/llm",
        tags=["llm-settings"],
        route_class=SecretSafeRequestValidationRoute,
    )
    administrator = administrator_dependency or require_system_administrator

    def record(
        principal: Principal,
        request: Request,
        action: str,
        target_type: str,
        target_id: str | None,
        before: Any,
        after: Any,
    ) -> None:
        audit_service.record(
            actor=principal,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            reason=None,
            result="success",
            request=request,
        )

    def settings_provider(provider_id: str) -> dict[str, Any] | None:
        return next(
            (
                provider
                for provider in service.repository.settings_payload()["providers"]
                if provider["id"] == provider_id
            ),
            None,
        )

    def settings_model(model_record_id: str) -> dict[str, Any] | None:
        for provider in service.repository.settings_payload()["providers"]:
            for model in provider["models"]:
                if model["id"] == model_record_id:
                    return model
        return None

    def route_error(
        exc: LLMSettingsError | LLMSecretError | LLMCredentialStoreError,
    ) -> HTTPException:
        if isinstance(exc, LLMCredentialStoreError):
            return HTTPException(status_code=503, detail="系统凭据库不可用")
        if isinstance(exc, LLMProviderNotFound):
            return HTTPException(status_code=404, detail="提供商不存在")
        return HTTPException(
            status_code=409 if isinstance(exc, LLMSettingsConflict) else 400,
            detail=str(exc),
        )

    def credential_summary(provider_id: str) -> dict[str, str]:
        provider = settings_provider(provider_id)
        if provider is None:
            raise LLMProviderNotFound("提供商不存在")
        return {
            "provider_id": provider_id,
            "credential_status": str(provider["credential_status"]),
            "credential_source": str(provider["credential_source"]),
        }

    @router.get("/available-models")
    def get_available_models(
        _: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        return service.repository.available_models_payload()

    @router.get("/settings")
    def get_settings(
        _: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        return service.repository.settings_payload()

    @router.post("/providers", status_code=201)
    def create_provider(
        payload: ProviderCreateRequest,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        reject_legacy_api_key_fields(payload)
        try:
            provider = service.repository.create_provider(
                **payload.model_dump(), actor_id=principal.user_id
            )
        except LLMSettingsError as exc:
            raise route_error(exc) from exc
        record(
            principal,
            request,
            "llm.provider.create",
            "llm_provider",
            provider["id"],
            None,
            provider,
        )
        return provider

    @router.put("/providers/{provider_id}")
    def update_provider(
        provider_id: str,
        payload: ProviderUpdateRequest,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        reject_legacy_api_key_fields(payload)
        before = settings_provider(provider_id)
        try:
            provider = service.repository.update_provider(
                provider_id,
                **payload.model_dump(),
                actor_id=principal.user_id,
            )
        except LLMSettingsError as exc:
            raise route_error(exc) from exc
        record(
            principal,
            request,
            "llm.provider.update",
            "llm_provider",
            provider_id,
            before,
            provider,
        )
        return provider

    @router.put("/providers/{provider_id}/credential")
    def replace_provider_credential(
        provider_id: str,
        payload: ProviderCredentialRequest,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, str]:
        require_secure_secret_transport(request)
        try:
            before = credential_summary(provider_id)
            service.repository.replace_api_key(
                provider_id, payload.api_key.get_secret_value()
            )
            result = credential_summary(provider_id)
        except (LLMSettingsError, LLMSecretError, LLMCredentialStoreError) as exc:
            raise route_error(exc) from exc
        record(
            principal,
            request,
            "llm.provider.credential.replace",
            "llm_provider",
            provider_id,
            before,
            result,
        )
        return result

    @router.delete("/providers/{provider_id}/credential")
    def clear_provider_credential(
        provider_id: str,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, str]:
        try:
            before = credential_summary(provider_id)
            service.repository.clear_api_key(provider_id)
            result = credential_summary(provider_id)
        except (LLMSettingsError, LLMSecretError, LLMCredentialStoreError) as exc:
            raise route_error(exc) from exc
        record(
            principal,
            request,
            "llm.provider.credential.clear",
            "llm_provider",
            provider_id,
            before,
            result,
        )
        return result

    @router.delete("/providers/{provider_id}")
    def delete_provider(
        provider_id: str,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, str]:
        before = settings_provider(provider_id)
        try:
            service.repository.delete_provider(provider_id)
        except LLMSettingsError as exc:
            raise route_error(exc) from exc
        result = {"status": "success", "provider_id": provider_id}
        record(
            principal,
            request,
            "llm.provider.delete",
            "llm_provider",
            provider_id,
            before,
            result,
        )
        return result

    @router.post("/providers/{provider_id}/models/sync")
    async def sync_models(
        provider_id: str,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, int]:
        try:
            result = await service.sync_models(provider_id)
        except LLMSettingsError as exc:
            raise route_error(exc) from exc
        record(
            principal,
            request,
            "llm.models.sync",
            "llm_provider",
            provider_id,
            None,
            result,
        )
        return result

    @router.post("/providers/{provider_id}/models", status_code=201)
    def create_model(
        provider_id: str,
        payload: ModelCreateRequest,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        try:
            model = service.repository.create_model(
                provider_id, **payload.model_dump()
            )
        except LLMSettingsError as exc:
            raise route_error(exc) from exc
        record(
            principal,
            request,
            "llm.model.create",
            "llm_model",
            model["id"],
            None,
            model,
        )
        return model

    @router.put("/models/{model_record_id}")
    def update_model(
        model_record_id: str,
        payload: ModelUpdateRequest,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        before = settings_model(model_record_id)
        try:
            model = service.repository.update_model(
                model_record_id, **payload.model_dump()
            )
        except LLMSettingsError as exc:
            raise route_error(exc) from exc
        record(
            principal,
            request,
            "llm.model.update",
            "llm_model",
            model_record_id,
            before,
            model,
        )
        return model

    @router.delete("/models/{model_record_id}")
    def delete_model(
        model_record_id: str,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, str]:
        before = settings_model(model_record_id)
        try:
            service.repository.delete_model(model_record_id)
        except LLMSettingsError as exc:
            raise route_error(exc) from exc
        result = {"status": "success", "model_id": model_record_id}
        record(
            principal,
            request,
            "llm.model.delete",
            "llm_model",
            model_record_id,
            before,
            result,
        )
        return result

    @router.put("/default-model")
    def set_default_model(
        payload: DefaultModelRequest,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        before = service.repository.settings_payload().get("default_model_id")
        try:
            service.repository.set_default_model(
                payload.model_id, actor_id=principal.user_id
            )
        except LLMSettingsError as exc:
            raise route_error(exc) from exc
        result = {"status": "success", "default_model_id": payload.model_id}
        record(
            principal,
            request,
            "llm.default_model.update",
            "llm_settings",
            "global",
            {"default_model_id": before},
            result,
        )
        return result

    @router.post("/test")
    async def test_connection(
        payload: ConnectionTestRequest,
        request: Request,
        principal: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        stored: dict[str, Any] = {}
        draft_env_var = str(payload.api_key_env_var or "").strip()
        has_transient_api_key = "api_key" in payload.model_fields_set
        if has_transient_api_key:
            require_secure_secret_transport(request)
        if payload.provider_id:
            try:
                stored = service.repository.provider_connection(
                    payload.provider_id, require_api_key=False,
                )
            except LLMSettingsError as exc:
                raise route_error(exc) from exc
        try:
            if has_transient_api_key:
                api_key = (
                    payload.api_key.get_secret_value() if payload.api_key else ""
                )
            elif stored:
                credential_provider = dict(stored)
                if draft_env_var:
                    credential_provider["api_key_env_var"] = draft_env_var
                api_key, _ = service.repository.resolve_provider_api_key(credential_provider)
            elif draft_env_var:
                api_key = environment_api_key(draft_env_var)
            else:
                api_key = str(stored.get("api_key") or "")
            result = await service.test_connection(
                base_url=payload.base_url or str(stored.get("base_url") or ""),
                api_key=api_key,
                model_id=payload.model_id,
                wire_api=payload.wire_api or str(stored.get("wire_api") or ""),
            )
        except (LLMSettingsError, LLMSecretError) as exc:
            raise route_error(exc) from exc
        record(
            principal,
            request,
            "llm.connection.test",
            "llm_provider",
            payload.provider_id,
            None,
            {
                "status": result["status"],
                "latency_ms": result["latency_ms"],
                "model_id": payload.model_id,
            },
        )
        return result

    return router
