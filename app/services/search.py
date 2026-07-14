from dataclasses import dataclass
from datetime import date, timedelta
import time

import httpx

from app.config import Settings


EVENT_TERMS = ["新产品发布", "新企业成立", "融资", "量产", "交付", "合作", "中标"]
ROBOT_TERMS = ["机器人", "人形机器人", "工业机器人", "服务机器人"]


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


def build_queries(lookback_days: int, max_queries: int) -> list[str]:
    since = date.today() - timedelta(days=lookback_days)
    queries = [f'{robot} {event} after:{since.isoformat()}' for event in EVENT_TERMS for robot in ROBOT_TERMS]
    return queries[:max_queries]


class SearchClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def search(self, query: str) -> list[SearchResult]:
        provider = self.settings.search_provider.lower()
        if provider == "tavily":
            return self._tavily(query)
        if provider == "bing":
            return self._bing(query)
        raise ValueError(f"不支持的搜索提供商: {provider}")

    def _tavily(self, query: str) -> list[SearchResult]:
        if not self.settings.tavily_api_key:
            raise RuntimeError("未配置 TAVILY_API_KEY")
        response = self._request_with_retry(
            "POST",
            "https://api.tavily.com/search",
            json={
                "api_key": self.settings.tavily_api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": self.settings.search_results_per_query,
            },
        )
        response.raise_for_status()
        return [SearchResult(r.get("title", ""), r["url"], r.get("content", "")) for r in response.json().get("results", [])]

    def _bing(self, query: str) -> list[SearchResult]:
        if not self.settings.bing_api_key:
            raise RuntimeError("未配置 BING_API_KEY")
        response = self._request_with_retry(
            "GET",
            self.settings.bing_endpoint,
            params={"q": query, "count": self.settings.search_results_per_query, "mkt": "zh-CN"},
            headers={"Ocp-Apim-Subscription-Key": self.settings.bing_api_key},
        )
        response.raise_for_status()
        values = response.json().get("webPages", {}).get("value", [])
        return [SearchResult(r.get("name", ""), r["url"], r.get("snippet", "")) for r in values]

    @staticmethod
    def _request_with_retry(method: str, url: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return httpx.request(method, url, timeout=30, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error
