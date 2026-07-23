from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.main as main_module
from app.config import Settings
from app.database import Base
from app.run_manager import RunManager
from app.services.fetcher import Page
from app.services.model_api import test_model_api as run_model_api_test
from app.services.model_config import ModelConfigStore
from app.services.product_pipeline import ProductDiscoveryPipeline
from app.services.search import SearchResult


def _settings() -> Settings:
    return Settings(
        deepseek_api_key="secret",
        deepseek_base_url="https://models.example.com/v1",
        deepseek_model="test-model",
        llm_json_mode=True,
    )


def test_model_api_test_performs_real_chat_completion(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs["json"]
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"api_test":true}'}}]},
        )

    monkeypatch.setattr("app.services.model_api.httpx.post", fake_post)
    result = run_model_api_test(_settings())

    assert result["success"] is True
    assert result["status_code"] == 200
    assert captured["url"] == "https://models.example.com/v1/chat/completions"
    assert captured["payload"]["model"] == "test-model"
    assert captured["payload"]["response_format"] == {"type": "json_object"}


def test_model_api_test_reports_upstream_502(monkeypatch):
    def fake_post(url, **_kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(502, request=request, text="upstream unavailable")

    monkeypatch.setattr("app.services.model_api.httpx.post", fake_post)
    result = run_model_api_test(_settings())

    assert result["success"] is False
    assert result["status_code"] == 502
    assert "HTTP 502" in result["message"]


def test_run_start_is_blocked_when_model_preflight_fails(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "test_model_api",
        lambda _settings: {
            "success": False,
            "message": "模型 API 返回 HTTP 502",
        },
    )
    with TestClient(main_module.app) as client:
        response = client.post(
            "/runs/start",
            json={
                "pipeline_mode": "product",
                "lookback_days": 14,
                "max_queries": 8,
                "search_mode": "native",
                "search_providers": ["tavily"],
            },
        )

    assert response.status_code == 503
    assert "模型预检失败" in response.json()["detail"]
    assert main_module.run_manager.snapshot()["status"] == "idle"


def test_run_start_is_blocked_when_inventory_workbook_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_module,
        "test_model_api",
        lambda _settings: {
            "success": True,
            "message": "模型 API 真实调用成功",
        },
    )
    with TestClient(main_module.app) as client:
        response = client.post(
            "/runs/start",
            json={
                "pipeline_mode": "product",
                "lookback_days": 14,
                "max_queries": 8,
                "search_mode": "native",
                "search_providers": ["tavily"],
                "inventory_workbook_path": str(tmp_path / "missing.xlsx"),
            },
        )

    assert response.status_code == 400
    assert "库存文件不存在" in response.json()["detail"]
    assert main_module.run_manager.snapshot()["status"] == "idle"


def test_model_config_test_endpoint_returns_real_call_result(tmp_path, monkeypatch):
    store = ModelConfigStore(
        Settings(deepseek_api_key="secret", model_config_path=str(tmp_path / "models.json"))
    )
    monkeypatch.setattr(main_module, "model_store", store)
    monkeypatch.setattr(
        main_module,
        "test_model_api",
        lambda settings: {
            "success": True,
            "model": settings.deepseek_model,
            "endpoint": "https://models.example.com/v1/chat/completions",
            "status_code": 200,
            "latency_ms": 123,
            "json_mode": True,
            "message": "模型 API 真实调用成功",
        },
    )

    with TestClient(main_module.app) as client:
        response = client.post("/model-configs/default-deepseek/test")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["latency_ms"] == 123


def test_paused_run_is_not_resumed_when_model_is_still_unavailable(monkeypatch):
    manager = RunManager()
    manager._state["status"] = "paused"
    manager._state["auto_pause_reason"] = "模型 API 连续 3 次返回 502，任务已自动暂停"
    monkeypatch.setattr(main_module, "run_manager", manager)
    monkeypatch.setattr(
        main_module,
        "test_model_api",
        lambda _settings: {"success": False, "message": "模型 API 返回 HTTP 502"},
    )

    with TestClient(main_module.app) as client:
        response = client.post("/runs/current/resume")

    assert response.status_code == 503
    assert manager.snapshot()["status"] == "paused"


def test_three_consecutive_model_502_responses_request_auto_pause():
    manager = RunManager()
    manager._state["status"] = "running"

    assert manager.model_call_failed(502) is False
    assert manager.model_call_failed(502) is False
    assert manager.model_call_failed(502) is True

    snapshot = manager.snapshot()
    assert snapshot["status"] == "pausing"
    assert snapshot["consecutive_model_502"] == 3
    assert "自动暂停" in snapshot["auto_pause_reason"]
    assert any("连续 3 次" in item["message"] for item in snapshot["logs"])

    resumed = manager.resume()
    assert resumed["status"] == "running"
    assert resumed["consecutive_model_502"] == 0
    assert resumed["auto_pause_reason"] == ""


def test_successful_model_call_resets_502_counter():
    manager = RunManager()
    manager._state["status"] = "running"
    manager.model_call_failed(502)
    manager.model_call_failed(502)
    manager.model_call_succeeded()

    assert manager.snapshot()["consecutive_model_502"] == 0
    assert manager.model_call_failed(502) is False


def test_product_pipeline_reports_model_502_to_controller():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    class FakeSearch:
        def search(self, _query):
            return [
                SearchResult(f"产品 {index}", f"https://example.com/{index}", "", ("test",))
                for index in range(3)
            ]

    class FakeFetcher:
        def fetch(self, url):
            return Page(url, "机器人产品", "机器人产品发布内容" * 20, now, url[-1] * 64, now)

    class FailingExtractor:
        def extract(self, _page):
            request = httpx.Request("POST", "https://models.example.com/v1/chat/completions")
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError("bad gateway", request=request, response=response)

    class RecordingController:
        def __init__(self):
            self.failures = 0
            self.auto_paused = False

        def checkpoint(self):
            return True

        def update(self, *_args, **_kwargs):
            return None

        def model_call_succeeded(self):
            self.failures = 0

        def model_call_failed(self, status_code):
            self.failures = self.failures + 1 if status_code == 502 else 0
            self.auto_paused = self.failures >= 3
            return self.auto_paused

    pipeline = object.__new__(ProductDiscoveryPipeline)
    pipeline.settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    pipeline.search = FakeSearch()
    pipeline.fetcher = FakeFetcher()
    pipeline.extractor = FailingExtractor()
    controller = RecordingController()

    with Session(engine) as db:
        result = pipeline.run(db, lookback_days=14, max_queries=2, controller=controller)

    assert len(result.errors) == 3
    assert controller.auto_paused is True
