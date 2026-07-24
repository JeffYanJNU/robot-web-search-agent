from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.config import Settings


class QccConfigInput(BaseModel):
    provider: Literal["airia", "qcc_official"] = "airia"
    airia_key: SecretStr | None = Field(default=None, max_length=1000, repr=False)
    app_key: SecretStr | None = Field(default=None, max_length=1000, repr=False)
    secret_key: SecretStr | None = Field(default=None, max_length=1000, repr=False)
    max_api_calls: int = Field(default=20, ge=0, le=1000)

    @field_validator("airia_key", "app_key", "secret_key", mode="before")
    @classmethod
    def strip_secret(cls, value: object) -> str | None:
        if value is None:
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        stripped = str(raw_value).strip()
        return stripped or None


class QccConfigStore:
    """JSON-backed QCC provider selection and credentials."""

    def __init__(self, settings: Settings, path: str | Path | None = None):
        self._settings = settings
        self._path = Path(path or settings.qcc_config_path)
        self._lock = RLock()
        self._provider = (
            "airia"
            if settings.qcc_airia_key or not (settings.qcc_app_key and settings.qcc_secret_key)
            else "qcc_official"
        )
        self._airia_key = settings.qcc_airia_key
        self._app_key = settings.qcc_app_key
        self._secret_key = settings.qcc_secret_key
        self._max_api_calls = settings.qcc_max_api_calls
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"工商 API 配置文件无法读取：{self._path}: {exc}") from exc
        provider = str(raw.get("provider") or "")
        if provider in {"airia", "qcc_official"}:
            self._provider = provider
        self._airia_key = str(raw.get("airia_key") or "")
        self._app_key = str(raw.get("app_key") or "")
        self._secret_key = str(raw.get("secret_key") or "")
        self._max_api_calls = max(0, min(1000, int(raw.get("max_api_calls", 20))))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        payload = {
            "provider": self._provider,
            "airia_key": self._airia_key,
            "app_key": self._app_key,
            "secret_key": self._secret_key,
            "max_api_calls": self._max_api_calls,
        }
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._path)

    def public_dict(self) -> dict:
        with self._lock:
            return {
                "provider": self._provider,
                "airia_key_configured": bool(self._airia_key),
                "app_key_configured": bool(self._app_key),
                "secret_key_configured": bool(self._secret_key),
                "max_api_calls": self._max_api_calls,
            }

    def update(self, data: QccConfigInput) -> dict:
        with self._lock:
            airia_key = (
                data.airia_key.get_secret_value()
                if data.airia_key is not None
                else self._airia_key
            )
            app_key = (
                data.app_key.get_secret_value()
                if data.app_key is not None
                else self._app_key
            )
            secret_key = (
                data.secret_key.get_secret_value()
                if data.secret_key is not None
                else self._secret_key
            )
            if data.provider == "airia" and not airia_key:
                raise ValueError("Airia 模式必须填写企业查询访问 Key")
            if data.provider == "qcc_official" and not (app_key and secret_key):
                raise ValueError("企查查官方模式必须同时填写 App Key 和 Secret Key")
            self._provider = data.provider
            self._airia_key = airia_key
            self._app_key = app_key
            self._secret_key = secret_key
            self._max_api_calls = data.max_api_calls
            self._save()
            return self.public_dict()

    def settings(self, base: Settings) -> Settings:
        with self._lock:
            if self._provider == "airia":
                updates = {
                    "qcc_airia_key": self._airia_key,
                    "qcc_app_key": "",
                    "qcc_secret_key": "",
                    "qcc_max_api_calls": self._max_api_calls,
                }
            else:
                updates = {
                    "qcc_airia_key": "",
                    "qcc_app_key": self._app_key,
                    "qcc_secret_key": self._secret_key,
                    "qcc_max_api_calls": self._max_api_calls,
                }
            return base.model_copy(update=updates)
