# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from system_database.schema import llm_models, llm_providers, llm_settings


PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "wire_api": "responses",
        "env_key": "OPENAI_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "wire_api": "chat_completions",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimax.io/v1",
        "wire_api": "chat_completions",
        "env_key": "MINIMAX_API_KEY",
    },
    "sub2api": {
        "label": "Sub2API",
        "base_url": "",
        "wire_api": "chat_completions",
        "env_key": "SUB2API_KEY",
    },
    "custom": {
        "label": "自定义",
        "base_url": "",
        "wire_api": "chat_completions",
        "env_key": "",
    },
}

_RUNTIME_REPOSITORY: "LLMSettingsRepository | None" = None
_RUNTIME_REPOSITORY_LOCK = threading.Lock()
_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LLMSecretError(RuntimeError):
    pass


class LLMSettingsError(ValueError):
    pass


class LLMSettingsConflict(LLMSettingsError):
    pass


def validate_api_key_env_var(value: str, *, required: bool = True) -> str:
    normalized = str(value or "").strip()
    if not normalized and not required:
        return ""
    if not _ENV_VAR_NAME.fullmatch(normalized):
        raise LLMSettingsError("API Key 环境变量名称无效")
    return normalized


def environment_api_key(variable_name: str) -> str:
    normalized = validate_api_key_env_var(variable_name)
    api_key = os.getenv(normalized, "").strip()
    if not api_key:
        raise LLMSecretError(f"环境变量 {normalized} 未配置或为空")
    return api_key


def _provider_env_var(preset: str, value: str = "") -> str:
    normalized = validate_api_key_env_var(value, required=False)
    if normalized:
        return normalized
    default = PROVIDER_PRESETS[preset]["env_key"]
    return validate_api_key_env_var(default)


def _credential_status(provider: dict[str, Any]) -> str:
    try:
        environment_api_key(str(provider.get("api_key_env_var") or ""))
    except (LLMSettingsError, LLMSecretError):
        return "missing"
    return "configured"


def endpoint_security_warning(base_url: str) -> str:
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme.lower() != "http":
        return ""
    if (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}:
        return ""
    return "该端点使用公网或局域网 HTTP，API Key 和请求内容可能被明文传输。"


def public_provider(provider: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in provider.items()
        if key not in {"api_key", "api_key_encrypted"}
    }
    credential_status = _credential_status(provider)
    public["credential_status"] = credential_status
    public["credential_source"] = "environment"
    public["has_api_key"] = credential_status == "configured"
    public["security_warning"] = endpoint_security_warning(
        str(provider.get("base_url") or "")
    )
    return public


def _normalized_name(value: str) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def validate_llm_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise LLMSettingsError("Base URL 必须是有效的 HTTP 或 HTTPS 地址")
    return normalized


def _provider_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "preset": str(row["preset"]),
        "base_url": str(row["base_url"]),
        "wire_api": str(row["wire_api"]),
        "api_key_env_var": str(row["api_key_env_var"] or ""),
        "is_enabled": bool(row["is_enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _model_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "provider_id": str(row["provider_id"]),
        "model_id": str(row["model_id"]),
        "display_name": str(row["display_name"] or ""),
        "source": str(row["source"]),
        "is_enabled": bool(row["is_enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class LLMSettingsRepository:
    conflict_error = LLMSettingsConflict

    def __init__(self, database: Any) -> None:
        self.database = database

    def create_provider(
        self,
        *,
        name: str,
        preset: str,
        base_url: str,
        wire_api: str,
        api_key_env_var: str = "",
        is_enabled: bool = True,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        display_name = " ".join(str(name or "").strip().split())
        if not display_name:
            raise LLMSettingsError("提供商名称不能为空")
        if preset not in PROVIDER_PRESETS:
            raise LLMSettingsError("不支持的提供商预设")
        if wire_api not in {"responses", "chat_completions"}:
            raise LLMSettingsError("不支持的调用协议")
        provider_id = str(uuid.uuid4())
        values = {
            "id": provider_id,
            "name": display_name,
            "normalized_name": _normalized_name(display_name),
            "preset": preset,
            "base_url": validate_llm_base_url(base_url),
            "wire_api": wire_api,
            "api_key_env_var": _provider_env_var(preset, api_key_env_var),
            "api_key_encrypted": None,
            "is_enabled": bool(is_enabled),
            "created_by_user_id": actor_id,
            "updated_by_user_id": actor_id,
        }
        try:
            with self.database.transaction() as connection:
                connection.execute(llm_providers.insert().values(**values))
                row = connection.execute(
                    select(llm_providers).where(llm_providers.c.id == provider_id)
                ).mappings().one()
        except IntegrityError as exc:
            raise LLMSettingsConflict("提供商名称已存在") from exc
        return public_provider(_provider_dict(row))

    def update_provider(
        self,
        provider_id: str,
        *,
        name: str | None = None,
        preset: str | None = None,
        base_url: str | None = None,
        wire_api: str | None = None,
        api_key_env_var: str | None = None,
        is_enabled: bool | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            current = connection.execute(
                select(llm_providers).where(llm_providers.c.id == provider_id)
            ).mappings().first()
            if current is None:
                raise LLMSettingsError("提供商不存在")
            if is_enabled is False and self._provider_has_default(connection, provider_id):
                raise LLMSettingsConflict("请先切换默认模型，再停用该提供商")
            values: dict[str, Any] = {
                "updated_at": func.now(),
                "updated_by_user_id": actor_id,
            }
            if name is not None:
                display_name = " ".join(str(name).strip().split())
                if not display_name:
                    raise LLMSettingsError("提供商名称不能为空")
                values.update(name=display_name, normalized_name=_normalized_name(display_name))
            if preset is not None:
                if preset not in PROVIDER_PRESETS:
                    raise LLMSettingsError("不支持的提供商预设")
                values["preset"] = preset
            if base_url is not None:
                values["base_url"] = validate_llm_base_url(base_url)
            if wire_api is not None:
                if wire_api not in {"responses", "chat_completions"}:
                    raise LLMSettingsError("不支持的调用协议")
                values["wire_api"] = wire_api
            if api_key_env_var is not None:
                effective_preset = str(preset or current["preset"])
                normalized_env_var = _provider_env_var(
                    effective_preset, api_key_env_var
                )
                if self._provider_has_default(connection, provider_id):
                    try:
                        environment_api_key(normalized_env_var)
                    except LLMSecretError as exc:
                        raise LLMSettingsConflict(str(exc)) from exc
                values["api_key_env_var"] = normalized_env_var
            values["api_key_encrypted"] = None
            if is_enabled is not None:
                values["is_enabled"] = bool(is_enabled)
            try:
                connection.execute(
                    llm_providers.update()
                    .where(llm_providers.c.id == provider_id)
                    .values(**values)
                )
            except IntegrityError as exc:
                raise LLMSettingsConflict("提供商名称已存在") from exc
            row = connection.execute(
                select(llm_providers).where(llm_providers.c.id == provider_id)
            ).mappings().one()
        return public_provider(_provider_dict(row))

    def delete_provider(self, provider_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            current = connection.execute(
                select(llm_providers).where(llm_providers.c.id == provider_id)
            ).mappings().first()
            if current is None:
                raise LLMSettingsError("提供商不存在")
            if self._provider_has_default(connection, provider_id):
                raise LLMSettingsConflict("请先切换默认模型，再删除该提供商")
            connection.execute(
                llm_models.delete().where(llm_models.c.provider_id == provider_id)
            )
            connection.execute(
                llm_providers.delete().where(llm_providers.c.id == provider_id)
            )
        return public_provider(_provider_dict(current))

    def create_model(
        self,
        provider_id: str,
        *,
        model_id: str,
        display_name: str = "",
        source: str = "manual",
        is_enabled: bool = True,
    ) -> dict[str, Any]:
        normalized_id = str(model_id or "").strip()
        if not normalized_id:
            raise LLMSettingsError("模型 ID 不能为空")
        if source not in {"manual", "synced", "imported"}:
            raise LLMSettingsError("不支持的模型来源")
        record_id = str(uuid.uuid4())
        try:
            with self.database.transaction() as connection:
                if not self._provider_exists(connection, provider_id):
                    raise LLMSettingsError("提供商不存在")
                connection.execute(
                    llm_models.insert().values(
                        id=record_id,
                        provider_id=provider_id,
                        model_id=normalized_id,
                        display_name=str(display_name or "").strip(),
                        source=source,
                        is_enabled=bool(is_enabled),
                    )
                )
                row = connection.execute(
                    select(llm_models).where(llm_models.c.id == record_id)
                ).mappings().one()
        except IntegrityError as exc:
            raise LLMSettingsConflict("该提供商下的模型 ID 已存在") from exc
        return _model_dict(row)

    def update_model(
        self,
        model_record_id: str,
        *,
        model_id: str | None = None,
        display_name: str | None = None,
        is_enabled: bool | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            current = connection.execute(
                select(llm_models).where(llm_models.c.id == model_record_id)
            ).mappings().first()
            if current is None:
                raise LLMSettingsError("模型不存在")
            if is_enabled is False and self._is_default_model(connection, model_record_id):
                raise LLMSettingsConflict("请先切换默认模型，再停用当前默认模型")
            values: dict[str, Any] = {"updated_at": func.now()}
            if model_id is not None:
                normalized = str(model_id).strip()
                if not normalized:
                    raise LLMSettingsError("模型 ID 不能为空")
                values["model_id"] = normalized
            if display_name is not None:
                values["display_name"] = str(display_name).strip()
            if is_enabled is not None:
                values["is_enabled"] = bool(is_enabled)
            try:
                connection.execute(
                    llm_models.update()
                    .where(llm_models.c.id == model_record_id)
                    .values(**values)
                )
            except IntegrityError as exc:
                raise LLMSettingsConflict("该提供商下的模型 ID 已存在") from exc
            row = connection.execute(
                select(llm_models).where(llm_models.c.id == model_record_id)
            ).mappings().one()
        return _model_dict(row)

    def delete_model(self, model_record_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            current = connection.execute(
                select(llm_models).where(llm_models.c.id == model_record_id)
            ).mappings().first()
            if current is None:
                raise LLMSettingsError("模型不存在")
            if self._is_default_model(connection, model_record_id):
                raise LLMSettingsConflict("请先切换默认模型，再删除当前默认模型")
            connection.execute(
                llm_models.delete().where(llm_models.c.id == model_record_id)
            )
        return _model_dict(current)

    def merge_synced_models(
        self, provider_id: str, model_ids: list[str]
    ) -> dict[str, int]:
        normalized_ids = list(
            dict.fromkeys(value for value in (str(item).strip() for item in model_ids) if value)
        )
        added = 0
        updated = 0
        with self.database.transaction() as connection:
            if not self._provider_exists(connection, provider_id):
                raise LLMSettingsError("提供商不存在")
            existing_rows = connection.execute(
                select(llm_models).where(llm_models.c.provider_id == provider_id)
            ).mappings().all()
            existing = {str(row["model_id"]): row for row in existing_rows}
            for model_id in normalized_ids:
                current = existing.get(model_id)
                if current is not None:
                    connection.execute(
                        llm_models.update()
                        .where(llm_models.c.id == current["id"])
                        .values(updated_at=func.now())
                    )
                    updated += 1
                    continue
                connection.execute(
                    llm_models.insert().values(
                        id=str(uuid.uuid4()),
                        provider_id=provider_id,
                        model_id=model_id,
                        display_name="",
                        source="synced",
                        is_enabled=True,
                    )
                )
                added += 1
            total = connection.execute(
                select(func.count()).select_from(llm_models).where(
                    llm_models.c.provider_id == provider_id
                )
            ).scalar_one()
        return {"added": added, "updated": updated, "total": int(total)}

    def set_default_model(
        self, model_record_id: str, actor_id: str | None = None
    ) -> None:
        with self.database.transaction() as connection:
            row = connection.execute(
                select(
                    llm_models.c.is_enabled,
                    llm_providers.c.is_enabled,
                    llm_providers.c.api_key_env_var,
                )
                .select_from(
                    llm_models.join(
                        llm_providers,
                        llm_providers.c.id == llm_models.c.provider_id,
                    )
                )
                .where(llm_models.c.id == model_record_id)
            ).first()
            if row is None:
                raise LLMSettingsError("模型不存在")
            if not bool(row[0]) or not bool(row[1]):
                raise LLMSettingsConflict("只能将启用的模型设为默认")
            try:
                environment_api_key(str(row[2] or ""))
            except (LLMSettingsError, LLMSecretError) as exc:
                raise LLMSettingsConflict(str(exc)) from exc
            current = connection.execute(
                select(llm_settings.c.id).where(llm_settings.c.id == "global")
            ).first()
            values = {
                "default_model_id": model_record_id,
                "updated_by_user_id": actor_id,
                "updated_at": func.now(),
            }
            if current is None:
                connection.execute(llm_settings.insert().values(id="global", **values))
            else:
                connection.execute(
                    llm_settings.update()
                    .where(llm_settings.c.id == "global")
                    .values(**values)
                )

    def settings_payload(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            provider_rows = connection.execute(
                select(llm_providers).order_by(llm_providers.c.name, llm_providers.c.id)
            ).mappings().all()
            model_rows = connection.execute(
                select(llm_models).order_by(llm_models.c.model_id, llm_models.c.id)
            ).mappings().all()
            default_model_id = connection.execute(
                select(llm_settings.c.default_model_id).where(
                    llm_settings.c.id == "global"
                )
            ).scalar_one_or_none()
        models_by_provider: dict[str, list[dict[str, Any]]] = {}
        for row in model_rows:
            model = _model_dict(row)
            model["is_default"] = model["id"] == default_model_id
            models_by_provider.setdefault(model["provider_id"], []).append(model)
        providers: list[dict[str, Any]] = []
        for row in provider_rows:
            stored_provider = _provider_dict(row)
            provider = public_provider(stored_provider)
            provider["models"] = models_by_provider.get(provider["id"], [])
            provider["is_default"] = any(
                model["is_default"] for model in provider["models"]
            )
            providers.append(provider)
        return {
            "presets": PROVIDER_PRESETS,
            "providers": providers,
            "default_model_id": default_model_id,
        }

    def available_models_payload(self) -> dict[str, Any]:
        """Return the safe model catalog used by creative generation pages."""
        with self.database.connection() as connection:
            rows = connection.execute(
                select(
                    llm_models.c.id,
                    llm_models.c.model_id,
                    llm_models.c.display_name,
                    llm_models.c.is_enabled.label("model_is_enabled"),
                    llm_providers.c.id.label("provider_id"),
                    llm_providers.c.name.label("provider_name"),
                    llm_providers.c.api_key_env_var,
                    llm_providers.c.is_enabled.label("provider_is_enabled"),
                )
                .select_from(
                    llm_models.join(
                        llm_providers,
                        llm_providers.c.id == llm_models.c.provider_id,
                    )
                )
                .order_by(
                    llm_providers.c.name,
                    llm_models.c.display_name,
                    llm_models.c.model_id,
                )
            ).mappings().all()
            default_model_id = connection.execute(
                select(llm_settings.c.default_model_id).where(
                    llm_settings.c.id == "global"
                )
            ).scalar_one_or_none()

        items: list[dict[str, Any]] = []
        for row in rows:
            if not bool(row["model_is_enabled"]) or not bool(
                row["provider_is_enabled"]
            ):
                continue
            try:
                environment_api_key(str(row["api_key_env_var"] or ""))
            except (LLMSettingsError, LLMSecretError):
                continue
            display_name = str(row["display_name"] or row["model_id"])
            provider_name = str(row["provider_name"])
            items.append(
                {
                    "id": str(row["id"]),
                    "provider_id": str(row["provider_id"]),
                    "provider_name": provider_name,
                    "model_id": str(row["model_id"]),
                    "display_name": display_name,
                    "label": f"{provider_name} / {display_name}",
                    "is_default": str(row["id"]) == str(default_model_id or ""),
                }
            )
        available_ids = {item["id"] for item in items}
        normalized_default_id = str(default_model_id or "")
        return {
            "default_model_record_id": (
                normalized_default_id
                if normalized_default_id in available_ids
                else ""
            ),
            "items": items,
        }

    def effective_default(self) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                select(
                    llm_providers.c.id.label("provider_id"),
                    llm_providers.c.preset,
                    llm_providers.c.base_url,
                    llm_providers.c.wire_api,
                    llm_providers.c.api_key_env_var,
                    llm_models.c.model_id,
                )
                .select_from(
                    llm_settings.join(
                        llm_models,
                        llm_models.c.id == llm_settings.c.default_model_id,
                    ).join(
                        llm_providers,
                        llm_providers.c.id == llm_models.c.provider_id,
                    )
                )
                .where(
                    llm_settings.c.id == "global",
                    llm_models.c.is_enabled.is_(True),
                    llm_providers.c.is_enabled.is_(True),
                )
            ).mappings().first()
        if row is None:
            return None
        return {
            "provider": str(row["preset"]),
            "provider_id": str(row["provider_id"]),
            "base_url": str(row["base_url"]),
            "api_key": environment_api_key(str(row["api_key_env_var"] or "")),
            "model": str(row["model_id"]),
            "wire_api": str(row["wire_api"]),
            "allow_insecure_http": urlsplit(str(row["base_url"])).scheme.lower() == "http",
        }

    def effective_model(self, model_record_id: str) -> dict[str, Any]:
        """Resolve one enabled model record to its server-side credentials."""
        with self.database.connection() as connection:
            row = connection.execute(
                select(
                    llm_models.c.id.label("model_record_id"),
                    llm_models.c.model_id,
                    llm_models.c.display_name,
                    llm_models.c.is_enabled.label("model_is_enabled"),
                    llm_providers.c.id.label("provider_id"),
                    llm_providers.c.name.label("provider_name"),
                    llm_providers.c.preset,
                    llm_providers.c.base_url,
                    llm_providers.c.wire_api,
                    llm_providers.c.api_key_env_var,
                    llm_providers.c.is_enabled.label("provider_is_enabled"),
                )
                .select_from(
                    llm_models.join(
                        llm_providers,
                        llm_providers.c.id == llm_models.c.provider_id,
                    )
                )
                .where(llm_models.c.id == str(model_record_id or "").strip())
            ).mappings().first()
        if row is None:
            raise LLMSettingsError("所选模型不存在")
        if not bool(row["model_is_enabled"]):
            raise LLMSettingsConflict("所选模型已停用")
        if not bool(row["provider_is_enabled"]):
            raise LLMSettingsConflict("所选模型的提供商已停用")
        try:
            api_key = environment_api_key(str(row["api_key_env_var"] or ""))
        except (LLMSettingsError, LLMSecretError) as exc:
            raise LLMSettingsConflict(str(exc)) from exc
        return {
            "provider": str(row["preset"]),
            "provider_id": str(row["provider_id"]),
            "provider_display_name": str(row["provider_name"]),
            "base_url": str(row["base_url"]),
            "api_key": api_key,
            "model": str(row["model_id"]),
            "model_record_id": str(row["model_record_id"]),
            "model_display_name": str(
                row["display_name"] or row["model_id"]
            ),
            "wire_api": str(row["wire_api"]),
            "allow_insecure_http": (
                urlsplit(str(row["base_url"])).scheme.lower() == "http"
            ),
        }

    def provider_connection(
        self, provider_id: str, *, require_api_key: bool = True
    ) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                select(llm_providers).where(llm_providers.c.id == provider_id)
            ).mappings().first()
        if row is None:
            raise LLMSettingsError("提供商不存在")
        provider = _provider_dict(row)
        try:
            api_key = environment_api_key(provider["api_key_env_var"])
        except (LLMSettingsError, LLMSecretError) as exc:
            if require_api_key:
                raise LLMSettingsError(str(exc)) from exc
            api_key = ""
        if require_api_key and not api_key:
            raise LLMSettingsError("请先配置提供商 API Key")
        return {
            "id": provider["id"],
            "preset": provider["preset"],
            "base_url": provider["base_url"],
            "wire_api": provider["wire_api"],
            "api_key_env_var": provider["api_key_env_var"],
            "api_key": api_key,
            "is_enabled": provider["is_enabled"],
        }

    def import_legacy(self, config_module: Any) -> dict[str, Any]:
        provider_name = str(getattr(config_module, "LLM_PROVIDER", "") or "").strip()
        base_url = str(getattr(config_module, "LLM_BASE_URL", "") or "").strip()
        model_id = str(getattr(config_module, "LLM_MODEL", "") or "").strip()
        if not provider_name or not base_url or not model_id:
            return {"imported": False, "reason": "incomplete"}
        preset = provider_name if provider_name in PROVIDER_PRESETS else "custom"
        wire_api = str(
            getattr(config_module, "LLM_WIRE_API", "")
            or PROVIDER_PRESETS[preset]["wire_api"]
        ).strip()
        if wire_api not in {"responses", "chat_completions"}:
            wire_api = PROVIDER_PRESETS[preset]["wire_api"]
        configured_env_var = str(
            getattr(config_module, "LLM_ENV_KEY", "") or ""
        ).strip()
        try:
            api_key_env_var = _provider_env_var(preset, configured_env_var)
        except LLMSettingsError:
            return {"imported": False, "reason": "missing_environment_variable"}
        now = datetime.now(timezone.utc)
        provider_id = str(uuid.uuid4())
        model_record_id = str(uuid.uuid4())
        try:
            with self.database.transaction() as connection:
                if connection.execute(
                    select(func.count()).select_from(llm_providers)
                ).scalar_one():
                    return {"imported": False, "reason": "already_configured"}
                connection.execute(
                    llm_providers.insert().values(
                        id=provider_id,
                        name=PROVIDER_PRESETS[preset]["label"] if preset != "custom" else provider_name,
                        normalized_name=_normalized_name(provider_name),
                        preset=preset,
                        base_url=validate_llm_base_url(base_url),
                        wire_api=wire_api,
                        api_key_env_var=api_key_env_var,
                        api_key_encrypted=None,
                        is_enabled=True,
                    )
                )
                connection.execute(
                    llm_models.insert().values(
                        id=model_record_id,
                        provider_id=provider_id,
                        model_id=model_id,
                        display_name=model_id,
                        source="imported",
                        is_enabled=True,
                    )
                )
                connection.execute(
                    llm_settings.insert().values(
                        id="global",
                        default_model_id=model_record_id,
                        legacy_imported_at=now,
                    )
                )
        except IntegrityError:
            return {"imported": False, "reason": "already_configured"}
        return {
            "imported": True,
            "provider_id": provider_id,
            "model_id": model_record_id,
            "has_api_key": _credential_status(
                {"api_key_env_var": api_key_env_var}
            )
            == "configured",
        }

    @staticmethod
    def _provider_exists(connection: Any, provider_id: str) -> bool:
        return connection.execute(
            select(llm_providers.c.id).where(llm_providers.c.id == provider_id)
        ).first() is not None

    @staticmethod
    def _is_default_model(connection: Any, model_record_id: str) -> bool:
        return connection.execute(
            select(llm_settings.c.id).where(
                llm_settings.c.default_model_id == model_record_id
            )
        ).first() is not None

    @staticmethod
    def _provider_has_default(connection: Any, provider_id: str) -> bool:
        return connection.execute(
            select(llm_settings.c.id)
            .select_from(
                llm_settings.join(
                    llm_models,
                    llm_models.c.id == llm_settings.c.default_model_id,
                )
            )
            .where(llm_models.c.provider_id == provider_id)
        ).first() is not None


def runtime_settings_repository() -> LLMSettingsRepository | None:
    global _RUNTIME_REPOSITORY
    if os.getenv("AIWORKER_LLM_DATABASE_CONFIG_ENABLED", "").strip() != "1":
        return None
    if _RUNTIME_REPOSITORY is not None:
        return _RUNTIME_REPOSITORY
    with _RUNTIME_REPOSITORY_LOCK:
        if _RUNTIME_REPOSITORY is not None:
            return _RUNTIME_REPOSITORY
        from system_database import SystemDatabase, SystemDatabaseSettings

        database = SystemDatabase(SystemDatabaseSettings.from_environment())
        _RUNTIME_REPOSITORY = LLMSettingsRepository(database)
    return _RUNTIME_REPOSITORY
