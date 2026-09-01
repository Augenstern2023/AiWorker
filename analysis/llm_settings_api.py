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
from pydantic import BaseModel, ConfigDict, Field

from analysis.llm import parse_chat_completion_text, parse_responses_text
from analysis.llm_settings import (
    LLMSettingsConflict,
    LLMSettingsError,
    LLMSettingsRepository,
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
    model_config = ConfigDict(protected_namespaces=())


class ProviderCreateRequest(LLMSettingsRequest):
    name: str = Field(min_length=1, max_length=120)
    preset: PresetName
    base_url: str = Field(min_length=1, max_length=2048)
    wire_api: WireAPI
    api_key: str = Field(default="", max_length=4096)
    is_enabled: bool = True


class ProviderUpdateRequest(LLMSettingsRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    preset: PresetName | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    wire_api: WireAPI | None = None
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
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


class ConnectionTestRequest(LLMSettingsRequest):
    provider_id: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    model_id: str = Field(min_length=1, max_length=255)
    wire_api: WireAPI | None = None


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
        response_text = (
            parse_responses_text(payload)
            if wire_api == "responses"
            else parse_chat_completion_text(payload)
        )
        return {
            "status": "success",
            "message": "模型连接测试成功",
            "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
            "response_preview": response_text[:200],
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
                timeout=self.timeout_seconds, transport=self.transport
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
    router = APIRouter(prefix="/api/llm", tags=["llm-settings"])
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

    def route_error(exc: LLMSettingsError) -> HTTPException:
        return HTTPException(
            status_code=409 if isinstance(exc, LLMSettingsConflict) else 400,
            detail=str(exc),
        )

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
        draft_api_key = str(payload.api_key or "").strip()
        if payload.provider_id:
            try:
                stored = service.repository.provider_connection(
                    payload.provider_id,
                    require_api_key=not bool(draft_api_key),
                )
            except LLMSettingsError as exc:
                raise route_error(exc) from exc
        try:
            result = await service.test_connection(
                base_url=payload.base_url or str(stored.get("base_url") or ""),
                api_key=draft_api_key or str(stored.get("api_key") or ""),
                model_id=payload.model_id,
                wire_api=payload.wire_api or str(stored.get("wire_api") or ""),
            )
        except LLMSettingsError as exc:
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
