import time
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

from app.config import Settings


DISCOVERY_QUERIES_ZH = [
    "中国 机器人企业 新注册 成立",
    "中国 人形机器人 创业公司 新成立",
    "中国 机器人公司 工商注册",
    "中国 机器人企业 首次亮相 首次曝光",
    "中国 企业 首次发布机器人业务",
    "中国 上市公司 进入机器人领域",
    "中国 制造企业 新增机器人业务",
    "中国 企业 成立机器人子公司",
    "中国 机器人企业 发布首款产品",
    "中国 机器人公司 新产品发布",
    "中国 人形机器人 新型号 发布",
    "中国 工业机器人 新产品 量产",
    "中国 服务机器人 新产品 交付",
    "中国 特种机器人 新产品 中标",
    "中国 机器人核心零部件 新企业 新产品",
    "中国 具身智能 企业 融资 产品发布",
]


@dataclass(frozen=True)
class SearchQuery:
    text: str
    market: str = "zh-CN"
    reason: str = "固定行业搜索"
    adaptive: bool = False


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


def build_queries(lookback_days: int, max_queries: int) -> list[SearchQuery]:
    since = date.today() - timedelta(days=lookback_days)
    return [
        SearchQuery(f"{query} after:{since.isoformat()}")
        for query in DISCOVERY_QUERIES_ZH[:max_queries]
    ]


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
                "mkt": "zh-CN",
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
