from dataclasses import dataclass
from datetime import date, timedelta
import time

import httpx

from app.config import Settings


DISCOVERY_QUERIES_ZH = [
    "机器人企业 新成立",
    "人形机器人 创业公司",
    "机器人公司 融资",
    "机器人企业 发布首款产品",
    "企业 进入机器人领域",
    "机器人公司 量产 交付",
    "机器人核心零部件 企业",
    "特种机器人 新公司",
]

DISCOVERY_QUERIES_EN = [
    "new robotics company",
    "humanoid robotics startup",
    "robotics company funding",
    "robotics startup launches first product",
    "company enters robotics market",
    "robot manufacturer production delivery",
    "robotics core components company",
    "new special purpose robotics company",
]


@dataclass(frozen=True)
class SearchQuery:
    text: str
    market: str


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


def build_queries(lookback_days: int, max_queries: int) -> list[SearchQuery]:
    since = date.today() - timedelta(days=lookback_days)
    zh = [SearchQuery(f"{query} after:{since.isoformat()}", "zh-CN") for query in DISCOVERY_QUERIES_ZH]
    en = [SearchQuery(f"{query} after:{since.isoformat()}", "en-US") for query in DISCOVERY_QUERIES_EN]

    queries: list[SearchQuery] = []
    for index in range(max(len(zh), len(en))):
        if index < len(zh):
            queries.append(zh[index])
        if index < len(en):
            queries.append(en[index])
    return queries[:max_queries]


class SearchClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def search(self, query: SearchQuery) -> list[SearchResult]:
        provider = self.settings.search_provider.lower()
        if provider == "tavily":
            return self._tavily(query)
        if provider == "bing":
            return self._bing(query)
        raise ValueError(f"不支持的搜索提供商: {provider}")

    def _tavily(self, query: SearchQuery) -> list[SearchResult]:
        if not self.settings.tavily_api_key:
            raise RuntimeError("未配置 TAVILY_API_KEY")
        response = self._request_with_retry(
            "POST",
            "https://api.tavily.com/search",
            json={
                "api_key": self.settings.tavily_api_key,
                "query": query.text,
                "search_depth": "advanced",
                "max_results": self.settings.search_results_per_query,
                "include_answer": False,
            },
        )
        response.raise_for_status()
        return [
            SearchResult(item.get("title", ""), item["url"], item.get("content", ""))
            for item in response.json().get("results", [])
        ]

    def _bing(self, query: SearchQuery) -> list[SearchResult]:
        if not self.settings.bing_api_key:
            raise RuntimeError("未配置 BING_API_KEY")
        response = self._request_with_retry(
            "GET",
            self.settings.bing_endpoint,
            params={
                "q": query.text,
                "count": self.settings.search_results_per_query,
                "mkt": query.market,
                "responseFilter": "Webpages",
            },
            headers={"Ocp-Apim-Subscription-Key": self.settings.bing_api_key},
        )
        response.raise_for_status()
        values = response.json().get("webPages", {}).get("value", [])
        return [SearchResult(item.get("name", ""), item["url"], item.get("snippet", "")) for item in values]

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
