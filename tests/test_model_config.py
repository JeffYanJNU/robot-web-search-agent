import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.services.model_config import ModelConfigInput, ModelConfigStore


def test_model_store_hides_secret_and_applies_active_model(tmp_path):
    path = tmp_path / "models.json"
    settings = Settings(
        deepseek_api_key="environment-secret",
        model_config_path=str(path),
    )
    store = ModelConfigStore(settings)

    initial = store.list_public()
    assert initial["active_id"] == "default-deepseek"
    assert "api_key" not in initial["models"][0]
    assert initial["models"][0]["api_key_configured"] is True

    created = store.upsert(
        ModelConfigInput(
            name="Custom model",
            provider="custom",
            base_url="https://models.example.com/v1/chat/completions",
            api_key="new-secret",
            model="example/model",
            json_mode=False,
        )
    )
    store.activate(created["id"])

    active = store.active_settings()
    assert active.deepseek_api_key == "new-secret"
    assert active.deepseek_base_url == "https://models.example.com/v1/chat/completions"
    assert active.deepseek_model == "example/model"
    assert active.llm_json_mode is False
    assert created["completion_url"] == "https://models.example.com/v1/chat/completions"
    assert "new-secret" not in json.dumps(store.list_public())


def test_model_store_requires_key_before_activation(tmp_path):
    store = ModelConfigStore(
        Settings(deepseek_api_key="key", model_config_path=str(tmp_path / "models.json"))
    )
    created = store.upsert(
        ModelConfigInput(
            name="No key",
            provider="custom",
            base_url="https://models.example.com/v1",
            model="model-a",
        )
    )

    with pytest.raises(ValueError, match="API Key"):
        store.activate(created["id"])


def test_model_store_persists_and_retains_key_on_edit(tmp_path):
    path = tmp_path / "models.json"
    settings = Settings(deepseek_api_key="key", model_config_path=str(path))
    store = ModelConfigStore(settings)
    created = store.upsert(
        ModelConfigInput(
            name="First name",
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key="saved-secret",
            model="gpt-test",
        )
    )
    store.upsert(
        ModelConfigInput(
            name="Renamed",
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key=None,
            model="gpt-test-2",
        ),
        created["id"],
    )
    store.activate(created["id"])

    reloaded = ModelConfigStore(settings)
    assert reloaded.active_settings().deepseek_api_key == "saved-secret"
    assert reloaded.active_settings().deepseek_model == "gpt-test-2"


def test_model_config_api_never_returns_api_key(tmp_path, monkeypatch):
    store = ModelConfigStore(
        Settings(deepseek_api_key="hidden-secret", model_config_path=str(tmp_path / "models.json"))
    )
    monkeypatch.setattr(main_module, "model_store", store)

    with TestClient(main_module.app) as client:
        response = client.get("/model-configs")
        assert response.status_code == 200
        assert "hidden-secret" not in response.text
        assert "api_key" not in response.json()["models"][0]

        created = client.post(
            "/model-configs",
            json={
                "name": "API model",
                "provider": "custom",
                "base_url": "https://models.example.com/v1",
                "api_key": "another-secret",
                "model": "model-b",
            },
        )
        assert created.status_code == 201
        assert "another-secret" not in created.text

        activated = client.post(f'/model-configs/{created.json()["id"]}/activate')
        assert activated.status_code == 200
        assert store.active_settings().deepseek_model == "model-b"
