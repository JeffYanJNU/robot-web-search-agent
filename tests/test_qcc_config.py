import json

import pytest

from app.config import Settings
from app.services.qcc_config import QccConfigInput, QccConfigStore


def test_qcc_config_store_enforces_mutually_exclusive_provider_settings(tmp_path):
    path = tmp_path / "qcc.json"
    base = Settings(
        qcc_config_path=str(path),
        qcc_airia_key="env-airia",
        qcc_app_key="env-app",
        qcc_secret_key="env-secret",
    )
    store = QccConfigStore(base)

    official = store.update(
        QccConfigInput(
            provider="qcc_official",
            app_key="saved-app",
            secret_key="saved-secret",
            max_api_calls=12,
        )
    )
    official_settings = store.settings(base)

    assert official["provider"] == "qcc_official"
    assert official_settings.qcc_airia_key == ""
    assert official_settings.qcc_app_key == "saved-app"
    assert official_settings.qcc_secret_key == "saved-secret"
    assert official_settings.qcc_max_api_calls == 12
    assert "saved-app" not in json.dumps(official)
    assert "saved-secret" not in json.dumps(official)

    airia = store.update(
        QccConfigInput(
            provider="airia",
            airia_key="saved-airia",
            max_api_calls=8,
        )
    )
    airia_settings = store.settings(base)

    assert airia["provider"] == "airia"
    assert airia_settings.qcc_airia_key == "saved-airia"
    assert airia_settings.qcc_app_key == ""
    assert airia_settings.qcc_secret_key == ""
    assert airia_settings.qcc_max_api_calls == 8


def test_qcc_config_store_preserves_blank_edited_secrets_and_reloads(tmp_path):
    path = tmp_path / "qcc.json"
    base = Settings(qcc_config_path=str(path))
    store = QccConfigStore(base)
    store.update(
        QccConfigInput(
            provider="qcc_official",
            app_key="saved-app",
            secret_key="saved-secret",
        )
    )

    store.update(QccConfigInput(provider="qcc_official", max_api_calls=5))
    reloaded = QccConfigStore(base)
    settings = reloaded.settings(base)

    assert settings.qcc_app_key == "saved-app"
    assert settings.qcc_secret_key == "saved-secret"
    assert settings.qcc_airia_key == ""
    assert reloaded.public_dict()["max_api_calls"] == 5


def test_qcc_config_store_requires_credentials_for_selected_provider(tmp_path):
    store = QccConfigStore(
        Settings(qcc_config_path=str(tmp_path / "qcc.json"))
    )

    with pytest.raises(ValueError, match="Airia 模式必须填写"):
        store.update(QccConfigInput(provider="airia"))
    with pytest.raises(ValueError, match="必须同时填写 App Key 和 Secret Key"):
        store.update(QccConfigInput(provider="qcc_official"))
