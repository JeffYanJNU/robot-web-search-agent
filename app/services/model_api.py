from __future__ import annotations

import json
from time import perf_counter
from typing import Any

import httpx

from app.config import Settings


MODEL_TEST_TIMEOUT_SECONDS = 20


def completion_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def http_status_code(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return int(status_code) if isinstance(status_code, int) else None


def test_model_api(
    settings: Settings,
    *,
    timeout_seconds: int = MODEL_TEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Perform a real minimal chat completion without exposing credentials."""
    endpoint = completion_url(settings.deepseek_base_url)
    if not settings.deepseek_api_key:
        return {
            "success": False,
            "model": settings.deepseek_model,
            "endpoint": endpoint,
            "status_code": None,
            "latency_ms": 0,
            "json_mode": settings.llm_json_mode,
            "message": "模型尚未配置 API Key",
        }
    started = perf_counter()
    payload: dict[str, Any] = {
        "model": settings.deepseek_model,
        "temperature": 0,
        "max_tokens": 32,
        "messages": [
            {
                "role": "user",
                "content": '只返回 JSON，不要解释：{"api_test":true}',
            }
        ],
    }
    if settings.llm_json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        response = httpx.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds,
        )
        latency_ms = round((perf_counter() - started) * 1000)
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if not str(content or "").strip():
            raise ValueError("响应中没有有效的 choices[0].message.content")
        return {
            "success": True,
            "model": settings.deepseek_model,
            "endpoint": endpoint,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "json_mode": settings.llm_json_mode,
            "message": "模型 API 真实调用成功",
        }
    except httpx.HTTPStatusError as exc:
        latency_ms = round((perf_counter() - started) * 1000)
        detail = exc.response.text.strip()[:500]
        return {
            "success": False,
            "model": settings.deepseek_model,
            "endpoint": endpoint,
            "status_code": exc.response.status_code,
            "latency_ms": latency_ms,
            "json_mode": settings.llm_json_mode,
            "message": (
                f"模型 API 返回 HTTP {exc.response.status_code}"
                + (f"：{detail}" if detail else "")
            ),
        }
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        return {
            "success": False,
            "model": settings.deepseek_model,
            "endpoint": endpoint,
            "status_code": None,
            "latency_ms": round((perf_counter() - started) * 1000),
            "json_mode": settings.llm_json_mode,
            "message": f"无法连接模型 API：{exc}",
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "success": False,
            "model": settings.deepseek_model,
            "endpoint": endpoint,
            "status_code": None,
            "latency_ms": round((perf_counter() - started) * 1000),
            "json_mode": settings.llm_json_mode,
            "message": f"模型响应格式无效：{exc}",
        }
