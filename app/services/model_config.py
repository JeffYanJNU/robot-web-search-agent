from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.config import Settings


PROVIDER_PRESETS = [
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
    {"id": "siliconflow", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    {"id": "custom", "name": "自定义 / Custom", "base_url": "", "model": ""},
]


class ModelConfigInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(default="custom", min_length=1, max_length=40)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    json_mode: bool = True
    supports_tools: bool = False
    supports_images: bool = False
    supports_reasoning: bool = False
    input_context: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)

    @field_validator("name", "provider", "base_url", "model", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("api_key", mode="before")
    @classmethod
    def strip_api_key(cls, value: object) -> str | None:
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped or None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("接口地址必须以 http:// 或 https:// 开头")
        return value.rstrip("/")


class ModelConfig(ModelConfigInput):
    id: str
    api_key: str | None = None

    @property
    def completion_url(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"

    def public_dict(self) -> dict:
        data = self.model_dump(exclude={"api_key"})
        data["api_key_configured"] = bool(self.api_key)
        data["completion_url"] = self.completion_url
        return data


class ModelConfigStore:
    """Thread-safe JSON-backed store for OpenAI-compatible model endpoints."""

    def __init__(self, settings: Settings, path: str | Path | None = None):
        self._settings = settings
        self._path = Path(path or settings.model_config_path)
        self._lock = RLock()
        self._models: dict[str, ModelConfig] = {}
        self._active_id = ""
        self._load()

    def _default_model(self) -> ModelConfig:
        return ModelConfig(
            id="default-deepseek",
            name="DeepSeek（环境配置）",
            provider="deepseek",
            base_url=self._settings.deepseek_base_url,
            api_key=self._settings.deepseek_api_key,
            model=self._settings.deepseek_model,
            json_mode=True,
        )

    def _load(self) -> None:
        with self._lock:
            if not self._path.exists():
                default = self._default_model()
                self._models = {default.id: default}
                self._active_id = default.id
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                models = [ModelConfig.model_validate(item) for item in raw.get("models", [])]
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"模型配置文件无法读取：{self._path}: {exc}") from exc
            if not models:
                models = [self._default_model()]
            self._models = {item.id: item for item in models}
            requested_active = str(raw.get("active_id", ""))
            self._active_id = requested_active if requested_active in self._models else models[0].id

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        payload = {
            "active_id": self._active_id,
            "models": [item.model_dump() for item in self._models.values()],
        }
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._path)

    def list_public(self) -> dict:
        with self._lock:
            return {
                "active_id": self._active_id,
                "models": [item.public_dict() for item in self._models.values()],
                "providers": PROVIDER_PRESETS,
            }

    def upsert(self, data: ModelConfigInput, model_id: str | None = None) -> dict:
        with self._lock:
            existing = self._models.get(model_id or "")
            if model_id and existing is None:
                raise KeyError("模型配置不存在")
            api_key = data.api_key if data.api_key is not None else (existing.api_key if existing else None)
            item = ModelConfig(id=model_id or uuid4().hex, **data.model_dump(exclude={"api_key"}), api_key=api_key)
            self._models[item.id] = item
            if not self._active_id:
                self._active_id = item.id
            self._save()
            return item.public_dict()

    def activate(self, model_id: str) -> dict:
        with self._lock:
            item = self._models.get(model_id)
            if item is None:
                raise KeyError("模型配置不存在")
            if not item.api_key:
                raise ValueError("该模型尚未配置 API Key")
            self._active_id = model_id
            self._save()
            return item.public_dict()

    def delete(self, model_id: str) -> None:
        with self._lock:
            if model_id not in self._models:
                raise KeyError("模型配置不存在")
            if model_id == self._active_id:
                raise ValueError("不能删除当前正在使用的模型")
            del self._models[model_id]
            self._save()

    def active_settings(self) -> Settings:
        with self._lock:
            item = self._models[self._active_id]
            return self._settings.model_copy(
                update={
                    "deepseek_api_key": item.api_key or "",
                    "deepseek_base_url": item.base_url,
                    "deepseek_model": item.model,
                    "llm_json_mode": item.json_mode,
                }
            )
