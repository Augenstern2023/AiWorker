# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from system_database.schema import llm_models, llm_providers, llm_settings


PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "wire_api": "responses",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "wire_api": "chat_completions",
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimax.io/v1",
        "wire_api": "chat_completions",
    },
    "sub2api": {
        "label": "Sub2API",
        "base_url": "",
        "wire_api": "responses",
    },
    "custom": {
        "label": "自定义",
        "base_url": "",
        "wire_api": "chat_completions",
    },
}

_RUNTIME_REPOSITORY: "LLMSettingsRepository | None" = None
_RUNTIME_REPOSITORY_LOCK = threading.Lock()


class LLMSecretError(RuntimeError):
    pass


class LLMSettingsError(ValueError):
    pass


class LLMSettingsConflict(LLMSettingsError):
    pass


class LLMSecretCipher:
    def __init__(self, key: bytes) -> None:
        try:
            self._fernet = Fernet(key)
        except (TypeError, ValueError) as exc:
            raise LLMSecretError("AIWORKER_LLM_CONFIG_KEY must be a Fernet key") from exc

    @classmethod
    def from_environment(cls, runtime_dir: str | Path) -> "LLMSecretCipher":
        configured = os.getenv("AIWORKER_LLM_CONFIG_KEY", "").strip()
        if configured:
            return cls(configured.encode("ascii"))

        runtime_path = Path(runtime_dir)
        runtime_path.mkdir(parents=True, exist_ok=True)
        runtime_key_path = runtime_path / "llm_config.key"
        common_git_dir = cls._find_git_common_dir(runtime_path.parent)
        key_path = (
            common_git_dir / "aiworker" / "llm_config.key"
            if common_git_dir is not None
            else runtime_key_path
        )
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            return cls._read_key_after_concurrent_write(key_path)

        generated = (
            runtime_key_path.read_bytes().strip()
            if key_path != runtime_key_path and runtime_key_path.exists()
            else Fernet.generate_key()
        )
        try:
            descriptor = os.open(
                key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return cls._read_key_after_concurrent_write(key_path)
        with os.fdopen(descriptor, "wb") as key_file:
            key_file.write(generated + b"\n")
            key_file.flush()
            os.fsync(key_file.fileno())
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return cls(generated)

    @staticmethod
    def _find_git_common_dir(start: Path) -> Path | None:
        for repository_root in (start, *start.parents):
            marker = repository_root / ".git"
            if marker.is_dir():
                return marker.resolve()
            if not marker.is_file():
                continue
            try:
                prefix, separator, value = marker.read_text(
                    encoding="utf-8"
                ).strip().partition(":")
                if separator != ":" or prefix.strip().casefold() != "gitdir":
                    return None
                git_dir = Path(value.strip())
                if not git_dir.is_absolute():
                    git_dir = marker.parent / git_dir
                git_dir = git_dir.resolve()
                common_marker = git_dir / "commondir"
                if not common_marker.is_file():
                    return git_dir
                common_dir = Path(
                    common_marker.read_text(encoding="utf-8").strip()
                )
                if not common_dir.is_absolute():
                    common_dir = git_dir / common_dir
                return common_dir.resolve()
            except OSError:
                return None
        return None

    @classmethod
    def _read_key_after_concurrent_write(
        cls, key_path: Path
    ) -> "LLMSecretCipher":
        last_error: Exception | None = None
        for attempt in range(50):
            try:
                stored_key = key_path.read_bytes().strip()
                if stored_key:
                    return cls(stored_key)
            except (OSError, LLMSecretError) as exc:
                last_error = exc
            if attempt < 49:
                time.sleep(0.01)
        raise LLMSecretError(
            "LLM encryption key file is empty or invalid"
        ) from last_error

    def encrypt(self, plaintext: str) -> str:
        value = str(plaintext or "")
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        value = str(ciphertext or "")
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise LLMSecretError("Stored LLM API key cannot be decrypted") from exc


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
    public["has_api_key"] = bool(provider.get("api_key_encrypted"))
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
        "api_key_encrypted": str(row["api_key_encrypted"] or ""),
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

    def __init__(self, database: Any, cipher: LLMSecretCipher) -> None:
        self.database = database
        self.cipher = cipher

    def create_provider(
        self,
        *,
        name: str,
        preset: str,
        base_url: str,
        wire_api: str,
        api_key: str = "",
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
            "api_key_encrypted": self.cipher.encrypt(str(api_key or "").strip()),
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
        api_key: str | None = None,
        clear_api_key: bool = False,
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
            if clear_api_key and self._provider_has_default(connection, provider_id):
                raise LLMSettingsConflict("请先切换默认模型，再清除该提供商 API Key")
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
            if clear_api_key:
                values["api_key_encrypted"] = None
            elif api_key is not None and str(api_key).strip():
                values["api_key_encrypted"] = self.cipher.encrypt(str(api_key).strip())
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
                    llm_providers.c.api_key_encrypted,
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
                api_key = self.cipher.decrypt(str(row[2] or "")).strip()
            except LLMSecretError as exc:
                raise LLMSettingsConflict(
                    "提供商 API Key 无法解密，请重新录入后再设为默认"
                ) from exc
            if not api_key:
                raise LLMSettingsConflict("请先配置提供商 API Key，再设为默认")
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
            encrypted_key = stored_provider["api_key_encrypted"]
            if not encrypted_key:
                provider["credential_status"] = "missing"
            else:
                try:
                    decrypted_key = self.cipher.decrypt(encrypted_key).strip()
                    provider["credential_status"] = (
                        "configured" if decrypted_key else "missing"
                    )
                except LLMSecretError:
                    provider["credential_status"] = "unreadable"
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

    def effective_default(self) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                select(
                    llm_providers.c.id.label("provider_id"),
                    llm_providers.c.preset,
                    llm_providers.c.base_url,
                    llm_providers.c.wire_api,
                    llm_providers.c.api_key_encrypted,
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
            "api_key": self.cipher.decrypt(str(row["api_key_encrypted"] or "")),
            "model": str(row["model_id"]),
            "wire_api": str(row["wire_api"]),
            "allow_insecure_http": urlsplit(str(row["base_url"])).scheme.lower() == "http",
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
            api_key = self.cipher.decrypt(provider["api_key_encrypted"]).strip()
        except LLMSecretError as exc:
            if require_api_key:
                raise LLMSettingsError("已保存的 API Key 无法解密，请重新录入") from exc
            api_key = ""
        if require_api_key and not api_key:
            raise LLMSettingsError("请先配置提供商 API Key")
        return {
            "id": provider["id"],
            "preset": provider["preset"],
            "base_url": provider["base_url"],
            "wire_api": provider["wire_api"],
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
        api_key = str(getattr(config_module, "LLM_API_KEY", "") or "").strip()
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
                        api_key_encrypted=self.cipher.encrypt(api_key),
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
            "has_api_key": bool(api_key),
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
        runtime_dir = Path(__file__).resolve().parents[1] / ".runtime"
        _RUNTIME_REPOSITORY = LLMSettingsRepository(
            database, LLMSecretCipher.from_environment(runtime_dir)
        )
    return _RUNTIME_REPOSITORY
